"""Stage 2: tutor identity mapping diagnostics and a controlled correction
workflow. This module answers two DELIBERATELY separate questions and never
conflates them:

1. Identity mapping -- which internal tutors.id does a given Bud tutor_id
   (held in tutors.external_system_id, an existing, generic, admin-editable
   field -- there is no dedicated Bud-tutor column) correspond to?
2. Eligibility -- is that tutor currently allowed to receive learners?
   (_find_tutor_by_bud_id in bud_sync_lib.py already enforces this: an
   inactive tutor is NEVER treated as a valid match, regardless of what its
   external_system_id says -- an inactive tutor's mapping must never
   silently route an active allocation to that record.)

Nothing here infers identity from names or email addresses and acts on it
automatically. Name/email similarity is surfaced ONLY as a candidate for
human review (duplicateTutorCandidates) -- it never drives a merge, a
mapping change, or any write, by itself. The only thing that writes
anything is propose_tutor_mapping_correction's paired commit function
(commit_tutor_mapping_correction), which requires an explicit admin
confirmation + reason and changes exactly one thing: which tutor record
holds a given Bud tutor_id. It never merges tutor records, moves a
learner's tutor_id/cohort_id, or touches attendance history -- and it never
triggers a Bud learner-sync commit; a mapping correction only changes which
tutor a FUTURE sync preview would resolve to."""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, Request

from .audit import write_audit_log
from .bud_sync_lib import _fetch_bud_rows


def _parse_timestamp(value) -> datetime:
    """expected_*_updated_at arrives as a plain string over HTTP (Pydantic/
    FastAPI's own JSON datetime serialisation, e.g. with a "T" separator),
    while the freshly-fetched value from psycopg is already a real
    timezone-aware datetime (str()'d with a space separator) -- comparing
    the two as strings would spuriously mismatch on formatting alone even
    when they represent the exact same instant. Parsing both sides to real
    datetimes before comparing avoids that entirely."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _fetch_all_tutors(cur) -> list[dict]:
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", email, active, '
        'external_system_id AS "externalSystemId", employee_ref AS "employeeRef", '
        'updated_at AS "updatedAt" FROM tutors'
    )
    return cur.fetchall()


def build_tutor_identity_diagnostics(cur) -> dict:
    """Read-only. Every count here is in two explicit units, never
    conflated: distinct Bud learner_reference values (people) and distinct
    Bud learning-plan rows (enrolments) -- the same distinction
    allocation_reconciliation_lib already applies to learners, applied here
    to the tutor side."""
    bud_rows = _fetch_bud_rows(cur)
    tutors = _fetch_all_tutors(cur)

    rows_by_bud_tutor_id: dict[str, list[dict]] = {}
    for row in bud_rows:
        bud_tutor_id = row.get("budTutorId")
        if bud_tutor_id:
            rows_by_bud_tutor_id.setdefault(str(bud_tutor_id), []).append(row)

    tutors_by_external_id: dict[str, list[dict]] = {}
    for tutor in tutors:
        if tutor["externalSystemId"]:
            tutors_by_external_id.setdefault(tutor["externalSystemId"], []).append(tutor)

    def _population(rows: list[dict]) -> tuple[int, int]:
        references = {r["learnerReference"] for r in rows if r.get("learnerReference")}
        return len(references), len(rows)

    unmatched: list[dict] = []
    inactive_only: list[dict] = []
    multi_owner: list[dict] = []

    all_affected_references: set[str] = set()
    all_affected_plan_rows = 0

    for bud_tutor_id, rows in rows_by_bud_tutor_id.items():
        matches = tutors_by_external_id.get(bud_tutor_id, [])
        ref_count, row_count = _population(rows)
        bud_tutor_name = next((r.get("tutorName") for r in rows if r.get("tutorName")), None)

        if not matches:
            unmatched.append({
                "budTutorId": bud_tutor_id, "budTutorName": bud_tutor_name,
                "affectedLearnerReferences": ref_count, "affectedBudLearningPlanRows": row_count,
            })
            all_affected_references.update(r["learnerReference"] for r in rows if r.get("learnerReference"))
            all_affected_plan_rows += row_count
        elif len(matches) == 1 and not matches[0]["active"]:
            inactive_only.append({
                "budTutorId": bud_tutor_id, "budTutorName": bud_tutor_name,
                "inactiveTutor": {
                    "id": matches[0]["id"], "firstName": matches[0]["firstName"], "lastName": matches[0]["lastName"],
                    "email": matches[0]["email"], "employeeRef": matches[0]["employeeRef"],
                    "updatedAt": matches[0]["updatedAt"],
                },
                "affectedLearnerReferences": ref_count, "affectedBudLearningPlanRows": row_count,
            })
            all_affected_references.update(r["learnerReference"] for r in rows if r.get("learnerReference"))
            all_affected_plan_rows += row_count
        elif len(matches) > 1:
            multi_owner.append({
                "budTutorId": bud_tutor_id, "budTutorName": bud_tutor_name,
                "tutors": [
                    {"id": t["id"], "firstName": t["firstName"], "lastName": t["lastName"],
                     "email": t["email"], "active": t["active"], "updatedAt": t["updatedAt"]}
                    for t in matches
                ],
                "affectedLearnerReferences": ref_count, "affectedBudLearningPlanRows": row_count,
            })
            all_affected_references.update(r["learnerReference"] for r in rows if r.get("learnerReference"))
            all_affected_plan_rows += row_count
        # else: exactly one ACTIVE match -- correctly resolved, not a diagnostic finding.

    # Potential duplicate internal tutor records: grouped by an exact,
    # case-insensitive full-name match only -- a deliberately narrow,
    # conservative heuristic (never fuzzy/edit-distance matching) so this
    # never flags two genuinely different people who happen to share a
    # surname. This is a REVIEW CANDIDATE list only -- nothing here merges,
    # deactivates, or changes any tutor record.
    name_groups: dict[str, list[dict]] = {}
    for tutor in tutors:
        key = f"{tutor['firstName'].strip().lower()} {tutor['lastName'].strip().lower()}"
        name_groups.setdefault(key, []).append(tutor)

    duplicate_candidates = [
        {
            "normalizedName": name,
            "tutors": [
                {"id": t["id"], "firstName": t["firstName"], "lastName": t["lastName"], "email": t["email"],
                 "active": t["active"], "externalSystemId": t["externalSystemId"], "employeeRef": t["employeeRef"],
                 "updatedAt": t["updatedAt"]}
                for t in group
            ],
        }
        for name, group in name_groups.items()
        if len(group) > 1
    ]

    return {
        "unmatchedBudTutorIds": unmatched,
        "budIdsHeldOnlyByInactiveTutor": inactive_only,
        "budIdAttachedToMultipleTutors": multi_owner,
        "duplicateTutorCandidates": duplicate_candidates,
        "totals": {
            "distinctAffectedLearnerReferences": len(all_affected_references),
            "affectedBudLearningPlanRows": all_affected_plan_rows,
        },
    }


def _tutor_summary(tutor: dict) -> dict:
    return {
        "id": tutor["id"], "firstName": tutor["firstName"], "lastName": tutor["lastName"],
        "email": tutor["email"], "active": tutor["active"], "externalSystemId": tutor["externalSystemId"],
        "employeeRef": tutor["employeeRef"], "updatedAt": tutor["updatedAt"],
    }


def _supporting_identity_signals(cur, source: dict, target: dict) -> list[str]:
    """Signals that a human should weigh when deciding whether these two
    records are the same person -- NONE of them, individually or combined,
    constitute confirmation by themselves. This function never sets any
    "confirmed" flag; commit_tutor_mapping_correction ALWAYS requires an
    explicit identity_confirmed_by_admin=True regardless of what this
    returns (Stage 2 verification: an earlier version treated a match here
    as sufficient to skip that requirement -- a real bug, since a shared
    phone number or even a past transfer between two tutor_ids is still
    circumstantial, not proof two DIFFERENT people aren't behind them, and
    is corrected here).

    Never a check against a single record's own fields either (a record's
    employee_ref equalling the very Bud id being reassigned to it says
    nothing about the OTHER record; that was a separate, earlier bug in
    this function). Name similarity is reported separately by the caller,
    as the weakest possible signal, since two different people can share a
    name."""
    signals: list[str] = []

    if source["phone"] and target["phone"] and source["phone"] == target["phone"]:
        signals.append("Same phone number on record (supporting signal only, not proof).")

    if source["employeeRef"] and target["employeeRef"] and source["employeeRef"] == target["employeeRef"]:
        signals.append("Same employee_ref value on both records (supporting signal only, not proof).")

    # A learner ever transferred directly between these two specific
    # tutor_ids at least shows the app has treated them as related before
    # -- still a supporting signal to weigh, not confirmation that the
    # SAME PERSON sits behind both records.
    cur.execute(
        "SELECT count(*)::int AS n FROM learner_allocation_history "
        "WHERE (previous_tutor_id = %s AND new_tutor_id = %s) OR (previous_tutor_id = %s AND new_tutor_id = %s)",
        (source["id"], target["id"], target["id"], source["id"]),
    )
    if cur.fetchone()["n"] > 0:
        signals.append(
            "A learner has previously been transferred between these two tutor records "
            "(supporting signal only -- on its own this does not prove they are the same person)."
        )

    return signals


def preview_tutor_mapping_correction(cur, source_tutor_id: int, target_tutor_id: int, bud_tutor_id: str) -> dict:
    """Read-only. Surfaces a name match and any supporting signals (see
    _supporting_identity_signals) purely for a human to weigh -- NONE of
    them ever satisfy the identity-confirmation requirement automatically.
    Every correction, regardless of how much or how little supporting
    evidence exists, requires an explicit identityConfirmedByAdmin=True at
    commit time, re-checked fresh there rather than trusted from this
    preview."""
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", email, phone, active, '
        'external_system_id AS "externalSystemId", employee_ref AS "employeeRef", updated_at AS "updatedAt" '
        "FROM tutors WHERE id = ANY(%s)",
        ([source_tutor_id, target_tutor_id],),
    )
    by_id = {t["id"]: t for t in cur.fetchall()}
    source = by_id.get(source_tutor_id)
    target = by_id.get(target_tutor_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="Source or target tutor not found")
    if source_tutor_id == target_tutor_id:
        raise HTTPException(status_code=400, detail="Source and target tutor must be different records")

    name_match = (
        source["firstName"].strip().lower() == target["firstName"].strip().lower()
        and source["lastName"].strip().lower() == target["lastName"].strip().lower()
    )
    supporting_signals = _supporting_identity_signals(cur, source, target)

    conflicts = []
    if target["externalSystemId"] and target["externalSystemId"] != bud_tutor_id:
        conflicts.append(
            f"Target tutor {target_tutor_id} already holds a DIFFERENT Bud tutor identifier "
            f"({target['externalSystemId']}) -- this would be overwritten."
        )
    if source["externalSystemId"] and source["externalSystemId"] != bud_tutor_id:
        conflicts.append(
            f"Source tutor {source_tutor_id} holds a DIFFERENT Bud tutor identifier than the one specified "
            f"({source['externalSystemId']} vs {bud_tutor_id}) -- check this is the correction intended."
        )

    field_changes = [
        {"tutorId": source_tutor_id, "field": "externalSystemId", "before": source["externalSystemId"], "after": None},
        {"tutorId": target_tutor_id, "field": "externalSystemId", "before": target["externalSystemId"], "after": bud_tutor_id},
    ]

    bud_rows = [r for r in _fetch_bud_rows(cur) if str(r.get("budTutorId")) == str(bud_tutor_id)]
    affected_references = {r["learnerReference"] for r in bud_rows if r.get("learnerReference")}

    downstream_effects = [
        f"{len(bud_rows)} Bud learning-plan row(s) across {len(affected_references)} distinct learner reference(s) "
        f"currently reported under Bud tutor_id={bud_tutor_id} would resolve to tutor {target_tutor_id} "
        f"({target['firstName']} {target['lastName']}) in the NEXT Bud sync preview, instead of tutor_unmatched "
        f"or (if source_tutor_id currently holds this id) tutor {source_tutor_id}.",
        "This correction does not itself change any learner's tutor_id, cohort, or attendance history -- "
        "only a subsequent Bud sync preview/commit can propose and apply a learner-level tutor transfer, "
        "and only after an administrator reviews and approves it there.",
    ]

    unresolved = [
        "Every correction requires an authorised administrator to explicitly confirm these two records are the "
        "same person, regardless of how much supporting evidence is shown above -- a name match, a shared phone "
        "number, or a past transfer between these two tutor records is a signal to weigh, never proof, and none "
        "of them alone satisfy this requirement.",
        "Any learner whose CURRENT internal tutor_id is source_tutor_id is not moved by this correction -- "
        "if they should now belong to target_tutor_id, that is a separate, explicit transfer (Allocation "
        "screen, or a Bud sync tutor-transfer proposal after this mapping change and a fresh preview).",
        "Historical attendance records keep whatever tutor was recorded as delivering them at the time -- "
        "never rewritten by this or any other correction.",
    ]

    return {
        "sourceTutor": _tutor_summary(source),
        "targetTutor": _tutor_summary(target),
        "budTutorId": bud_tutor_id,
        "nameMatch": name_match,
        "supportingSignals": supporting_signals,
        "requiresManualIdentityConfirmation": True,
        "conflictingIdentifierOwnership": conflicts,
        "fieldChanges": field_changes,
        "downstreamEffects": downstream_effects,
        "remainingUnresolved": unresolved,
        "preview": {
            "sourceTutorUpdatedAt": source["updatedAt"],
            "targetTutorUpdatedAt": target["updatedAt"],
        },
    }


def commit_tutor_mapping_correction(
    cur, source_tutor_id: int, target_tutor_id: int, bud_tutor_id: str,
    expected_source_updated_at, expected_target_updated_at,
    reason: str, request: Request, session: dict,
    identity_confirmed_by_admin: bool = False,
) -> dict:
    """Server-side admin permission is enforced by the caller (the route is
    Depends(require_admin)) -- this function additionally requires a
    non-blank reason, matching every other approval-style write in this
    codebase (bud sync commit, register refresh with material changes,
    etc.). Atomic: both tutor rows change together in one transaction, or
    neither does. Staleness is checked against the exact updated_at values
    the admin's preview showed them -- a change to EITHER record since
    preview (by this admin or anyone else) rejects the whole commit rather
    than silently overwriting it. Never touches learners, cohorts,
    attendance, or bud_sync_job/bud_sync_item -- the admin must separately
    generate a fresh Bud sync preview afterward to see how this changes
    matching.

    Identity is re-checked fresh here, not trusted from the (possibly
    stale) preview response: if no independent evidence links these two
    specific records (see _independent_identity_evidence -- a name match
    alone never counts), the caller MUST pass
    identity_confirmed_by_admin=True, an explicit acknowledgement that a
    human -- not this function -- has verified they're the same person."""
    if not reason or not reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to confirm this correction")
    if source_tutor_id == target_tutor_id:
        raise HTTPException(status_code=400, detail="Source and target tutor must be different records")

    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", email, phone, active, '
        'external_system_id AS "externalSystemId", employee_ref AS "employeeRef", updated_at AS "updatedAt" '
        "FROM tutors WHERE id = ANY(%s)",
        ([source_tutor_id, target_tutor_id],),
    )
    by_id = {t["id"]: t for t in cur.fetchall()}
    source = by_id.get(source_tutor_id)
    target = by_id.get(target_tutor_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="Source or target tutor not found")

    # Stage 2 verification pass, item 4: ALWAYS required, never bypassed by
    # evidence strength -- a name match, a shared phone number, or a past
    # transfer between these two tutor records (see
    # _supporting_identity_signals) are signals for a human to weigh, never
    # automatic confirmation. An earlier version of this function skipped
    # this requirement whenever any such signal was present; that was
    # exactly the shortcut this verification pass was asked to close.
    if not identity_confirmed_by_admin:
        raise HTTPException(
            status_code=400,
            detail="An administrator must explicitly confirm these two tutor records are the same person "
                   "(identityConfirmedByAdmin=true) before this correction can be committed -- supporting "
                   "evidence alone is never sufficient",
        )

    if source["updatedAt"] != _parse_timestamp(expected_source_updated_at):
        raise HTTPException(status_code=409, detail="Source tutor has changed since preview -- generate a new preview")
    if target["updatedAt"] != _parse_timestamp(expected_target_updated_at):
        raise HTTPException(status_code=409, detail="Target tutor has changed since preview -- generate a new preview")

    before = {
        "sourceTutorId": source_tutor_id, "sourceExternalSystemId": source["externalSystemId"],
        "targetTutorId": target_tutor_id, "targetExternalSystemId": target["externalSystemId"],
    }

    with cur.connection.transaction():
        # Stage 2 verification pass, item 2: the updated_at check above is
        # a fast pre-check only, not the actual protection -- it cannot by
        # itself stop two corrections concurrently racing to claim the
        # SAME bud_tutor_id (both could read "no other holder" before
        # either writes). Serialized here by a real Postgres advisory
        # lock, scoped to this specific bud_tutor_id (same
        # pg_advisory_xact_lock(hashtext(...)) idiom already used by
        # rate_limit.py/login_rate_limit.py) -- a second, concurrent
        # correction for the SAME id blocks here until this transaction
        # commits or rolls back, then re-reads a guaranteed-fresh
        # ownership state; two corrections for DIFFERENT ids never
        # contend with each other at all.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"tutor_mapping_correction:{bud_tutor_id}",))

        cur.execute(
            'SELECT id, first_name AS "firstName", last_name AS "lastName", active FROM tutors '
            "WHERE external_system_id = %s AND id != ALL(%s)",
            (bud_tutor_id, [source_tutor_id, target_tutor_id]),
        )
        other_holders = cur.fetchall()
        if other_holders:
            raise HTTPException(
                status_code=409,
                detail=f"Bud tutor identifier {bud_tutor_id} is now also held by tutor(s) "
                       f"{[h['id'] for h in other_holders]} -- resolve that conflict first",
            )

        cur.execute(
            "UPDATE tutors SET external_system_id = NULL, updated_at = now() WHERE id = %s",
            (source_tutor_id,),
        )
        cur.execute(
            "UPDATE tutors SET external_system_id = %s, updated_at = now() WHERE id = %s RETURNING updated_at",
            (bud_tutor_id, target_tutor_id),
        )
        applied_at = cur.fetchone()["updated_at"]
        after = {
            "sourceTutorId": source_tutor_id, "sourceExternalSystemId": None,
            "targetTutorId": target_tutor_id, "targetExternalSystemId": bud_tutor_id,
        }
        write_audit_log(
            request, action="tutor_mapping_correction_applied", entity_type="tutor", entity_id=target_tutor_id,
            previous_value={**before, "reason": reason, "identityConfirmedByAdmin": identity_confirmed_by_admin},
            new_value=after, cur=cur,
        )

        # run_commit's own staleness re-check is keyed on Bud's synced_at
        # and learners.updated_at ONLY -- neither one changes when a tutor
        # mapping is corrected, so an already-generated 'ready' preview
        # would otherwise sail through commit unaffected, silently applying
        # whatever tutor a bud_sync_item's proposed_values captured at ITS
        # preview time (before this correction). Every unapplied 'ready'
        # job is invalidated here, in the SAME transaction as the mapping
        # change, by reusing the exact status ('failed') that
        # update_item/link_existing_learner/bulk_approve_new_learners/
        # run_commit already refuse to act on -- no new guard logic
        # required anywhere else, and every one of run_commit's own tests
        # already prove a non-'ready' job cannot be committed.
        cur.execute(
            """
            UPDATE bud_sync_job SET status = 'failed', error_summary = %s
            WHERE status = 'ready' RETURNING id
            """,
            (
                f"Invalidated by a tutor mapping correction (Bud tutor id {bud_tutor_id} reassigned from "
                f"tutor {source_tutor_id} to tutor {target_tutor_id}) -- generate a new preview.",
            ),
        )
        invalidated_job_ids = [r["id"] for r in cur.fetchall()]
        if invalidated_job_ids:
            write_audit_log(
                request, action="bud_sync_preview_invalidated_by_tutor_mapping_correction",
                entity_type="tutor", entity_id=target_tutor_id,
                new_value={"invalidatedJobIds": invalidated_job_ids, "budTutorId": bud_tutor_id}, cur=cur,
            )

        # Belt-and-braces for the case the 'ready' sweep above cannot see:
        # a preview that is still 'generating' right now. Bumping this
        # counter, in this same transaction, is what run_commit's own
        # locking generation check (SELECT ... FOR SHARE) actually
        # contends against -- see its comment in bud_sync_lib.py. A job
        # that stamped an older generation at the START of its preview
        # will fail this check at commit time even though it was never
        # 'ready' at the moment this correction landed.
        cur.execute("UPDATE app_settings SET tutor_mapping_generation = tutor_mapping_generation + 1 WHERE id = 1")

    return {
        "sourceTutorId": source_tutor_id, "targetTutorId": target_tutor_id, "budTutorId": bud_tutor_id,
        "appliedAt": applied_at, "reason": reason, "invalidatedPreviewJobIds": invalidated_job_ids,
    }
