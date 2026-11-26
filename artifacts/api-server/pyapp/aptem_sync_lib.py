"""Aptem sync -- Phase 1, core ingestion. A second external LMS, parallel to
pyapp/bud_sync_lib.py (deliberately a separate pipeline, not a generalisation
of it -- see the Aptem sync plan). Administrator-only, preview-then-commit
workflow:

1. Detect an Aptem learner who does not yet exist in Attendance (matched via
   uln/email) and let an Administrator review + approve creating them.
2. Detect a simple field change (name/email/programme/start date) for a
   learner who already exists in Attendance and is already linked.

aptem_learner_progress is owned entirely by an external process (mirroring
public.learner_progress's own ownership for Bud) -- every query here is a
plain SELECT, and this module must never write to it.

Matching hierarchy:
1. Already Aptem-linked (aptem_learner_link.aptem_user_id) -- an established
   link always wins.
2. learners.uln = aptem row.uln, when both are non-null.
3. Fallback: case-insensitive learners.email = aptem row.email, when uln
   didn't resolve a match.
4. If uln-match and email-match resolve to two *different* internal
   learners, that's a conflict.
5. No reliable identifier -> unmatched.
Never matched by name alone.

Shape difference from Bud: a single Aptem learner can have several
concurrent programmes/episodes (confirmed in the Aptem sandbox data -- main
apprenticeship standard, onboarding, functional skills, additional learning
support all co-occurring for the same person). _select_primary_programme_row
collapses these down to one row per learner before classification -- see
its own docstring for why this heuristic is explicitly unvalidated.

No tutor mapping this phase -- aptem_v1_episodes.OwnerId -> internal tutor
linkage is unconfirmed against real data. A new learner is created with no
tutor and no cohort; both are a deliberately separate, later, manual step
(exactly mirroring Bud's own "no cohort at creation" precedent).

Status handling is deliberately conservative: Aptem's real status_desc-
equivalent vocabulary (episode_status/UserProgram_Status) is unconfirmed
against production data (the only values seen in the Aptem sandbox were
null, "Active", and "Onboarding"). Rather than hardcode a guessed mapping
the way bud_status_mapping.py does for Bud's confirmed-real values, this
module only ever uses the episode_is_active boolean as a signal -- for
new-learner eligibility, and for flagging (never auto-applying) a possible
status change on an already-matched learner. See _ELIGIBLE_EPISODE_ACTIVE
and _classify_existing_learner_update.
"""
from __future__ import annotations

import json
from datetime import date

from fastapi import HTTPException, Request

from .audit import write_audit_log
from .correlation import get_correlation_id

# Aptem-owned learner fields this sync proposes synchronising, and their
# internal learners-table counterpart. uln is handled separately (only ever
# set when the internal value is currently null -- see _diff_simple_fields).
_SIMPLE_FIELD_MAP = {
    "firstName": "firstName",
    "lastName": "lastName",
    "email": "email",
    "programName": "programme",
}

# Fields an Administrator must supply before a create_learner proposal
# becomes approvable -- Aptem has no equivalent for either (mirrors Bud's
# identical learnerRef/level rule; see bud_sync_lib.py).
_REQUIRED_LEARNER_FIELDS = ("learnerRef", "level")

# UNVALIDATED against real production Aptem data (see module docstring) --
# episode_is_active is used instead of a specific status string because a
# boolean is far more likely to be stable than an unconfirmed status
# vocabulary. Revisit once the production Aptem tenant replaces the sandbox
# feed and real episode_status/UserProgram_Status values are known -- this
# is the one place to change it.
_ELIGIBLE_EPISODE_ACTIVE = True


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def get_active_baseline(cur) -> dict | None:
    cur.execute(
        """
        SELECT id, established_at AS "establishedAt", established_by AS "establishedBy",
               source_max_synced_at AS "sourceMaxSyncedAt", source_row_count AS "sourceRowCount",
               status, notes, correlation_id AS "correlationId"
        FROM aptem_sync_baseline WHERE status = 'active'
        """
    )
    return cur.fetchone()


def get_source_status(cur) -> dict:
    """Read-only snapshot used by the trial's status card and Sync History.
    matchedLearnerCount uses the same primary hierarchy as classify_row --
    learners.uln first, email as a fallback."""
    cur.execute(
        'SELECT count(DISTINCT aptem_user_id)::int AS "rowCount", max(synced_at) AS "maxSyncedAt" '
        "FROM aptem_learner_progress WHERE aptem_user_id IS NOT NULL"
    )
    source = cur.fetchone()
    baseline = get_active_baseline(cur)

    matched_count = 0
    unmatched_count = 0
    if source["rowCount"]:
        cur.execute(
            """
            SELECT count(DISTINCT alp.aptem_user_id)::int AS count
            FROM aptem_learner_progress alp
            WHERE alp.aptem_user_id IS NOT NULL AND (
                EXISTS (
                    SELECT 1 FROM learners l
                    WHERE l.deleted_at IS NULL AND l.uln IS NOT NULL AND l.uln <> '' AND l.uln = alp.uln
                ) OR EXISTS (
                    SELECT 1 FROM learners l
                    WHERE l.deleted_at IS NULL AND l.email IS NOT NULL AND alp.email IS NOT NULL
                          AND lower(l.email) = lower(alp.email)
                )
            )
            """
        )
        matched_count = cur.fetchone()["count"]
        unmatched_count = source["rowCount"] - matched_count

    return {
        "sourceMaxSyncedAt": source["maxSyncedAt"],
        "sourceRowCount": source["rowCount"],
        "matchedLearnerCount": matched_count,
        "unmatchedLearnerCount": unmatched_count,
        "activeBaseline": baseline,
    }


def establish_baseline(cur, request: Request, session: dict, notes: str | None = None) -> dict:
    """Zero business writes -- only ever inserts one aptem_sync_baseline row
    plus its identity snapshot (aptem_sync_baseline_snapshot), both sync
    bookkeeping, never a learner. Snapshotted by aptem_user_id (person-level,
    since a learner's several programmes are already collapsed to one
    primary row before classification), not by programme."""
    if get_active_baseline(cur) is not None:
        raise HTTPException(status_code=409, detail="An Aptem sync baseline is already active. Reset it first.")

    cur.execute(
        'SELECT count(DISTINCT aptem_user_id)::int AS "rowCount", max(synced_at) AS "maxSyncedAt" '
        "FROM aptem_learner_progress WHERE aptem_user_id IS NOT NULL"
    )
    source = cur.fetchone()

    cur.execute(
        """
        INSERT INTO aptem_sync_baseline (established_by, source_max_synced_at, source_row_count, notes, correlation_id)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, established_at AS "establishedAt", established_by AS "establishedBy",
                  source_max_synced_at AS "sourceMaxSyncedAt", source_row_count AS "sourceRowCount",
                  status, notes, correlation_id AS "correlationId"
        """,
        (session["userId"], source["maxSyncedAt"], source["rowCount"], notes, get_correlation_id() or None),
    )
    baseline = cur.fetchone()

    cur.execute(
        """
        INSERT INTO aptem_sync_baseline_snapshot (baseline_id, source_identifier)
        SELECT %s, aptem_user_id::text
        FROM (
            SELECT DISTINCT aptem_user_id FROM aptem_learner_progress WHERE aptem_user_id IS NOT NULL
        ) AS all_rows
        ON CONFLICT (baseline_id, source_identifier) DO NOTHING
        """,
        (baseline["id"],),
    )

    write_audit_log(
        request, action="aptem_sync_baseline_established", entity_type="aptem_sync_baseline",
        entity_id=baseline["id"], new_value=baseline, cur=cur,
    )
    return baseline


def reset_baseline(cur, request: Request, session: dict, reason: str) -> dict:
    current = get_active_baseline(cur)
    if current is None:
        raise HTTPException(status_code=409, detail="No active baseline to reset")

    cur.execute(
        """
        UPDATE aptem_sync_baseline
        SET status = 'superseded', superseded_at = now(), superseded_by = %s, reset_reason = %s
        WHERE id = %s
        """,
        (session["userId"], reason, current["id"]),
    )
    write_audit_log(
        request, action="aptem_sync_baseline_reset", entity_type="aptem_sync_baseline",
        entity_id=current["id"], previous_value=current, new_value={"reason": reason}, cur=cur,
    )
    return establish_baseline(cur, request, session, notes=f"Reset from baseline {current['id']}: {reason}")


def list_baseline_history(cur) -> list[dict]:
    cur.execute(
        """
        SELECT id, established_at AS "establishedAt", established_by AS "establishedBy",
               source_max_synced_at AS "sourceMaxSyncedAt", source_row_count AS "sourceRowCount",
               status, notes, superseded_at AS "supersededAt", superseded_by AS "supersededBy",
               reset_reason AS "resetReason"
        FROM aptem_sync_baseline ORDER BY established_at DESC
        """
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Source read + matching
# ---------------------------------------------------------------------------


def _fetch_aptem_rows(cur) -> list[dict]:
    """Every row in the Aptem mirror table, at any episode state --
    eligibility is applied in classify_row, not here, exactly mirroring
    _fetch_bud_rows's own reasoning in bud_sync_lib.py."""
    cur.execute(
        """
        SELECT aptem_user_id AS "aptemUserId", aptem_program_id AS "aptemProgramId",
               program_name AS "programName", program_type AS "programType",
               episode_status AS "episodeStatus", episode_is_active AS "episodeIsActive",
               episode_start_date AS "episodeStartDate", episode_end_date AS "episodeEndDate",
               email, uln, first_name AS "firstName", last_name AS "lastName",
               component_count AS "componentCount", completed_component_count AS "completedComponentCount",
               overdue_component_count AS "overdueComponentCount",
               review_count AS "reviewCount", completed_review_count AS "completedReviewCount",
               next_planned_review_date AS "nextPlannedReviewDate", as_of_date AS "asOfDate",
               synced_at AS "syncedAt"
        FROM aptem_learner_progress
        WHERE aptem_user_id IS NOT NULL
        """
    )
    rows = cur.fetchall()
    # Defensive dedup by (user, programme), keeping the latest synced_at --
    # mirrors _fetch_bud_rows's own "never trust the source already dedups"
    # stance.
    by_key: dict[tuple, dict] = {}
    for row in rows:
        key = (row["aptemUserId"], row["aptemProgramId"])
        existing = by_key.get(key)
        if existing is None or (row["syncedAt"] and (not existing["syncedAt"] or row["syncedAt"] > existing["syncedAt"])):
            by_key[key] = row
    return list(by_key.values())


def _select_primary_programme_row(rows_for_user: list[dict]) -> dict:
    """Which of this learner's several concurrent Aptem programmes/episodes
    represents "the" programme for sync purposes.

    UNVALIDATED against real production data -- this heuristic (prefer the
    row with the longest episode_start_date-to-episode_end_date span,
    treating an open/missing end date as the longest of all) is a
    placeholder best guess, not a confirmed business rule. Revisit once real
    Aptem program_type values are known (the sandbox data suggests
    onboarding/functional-skills/additional-learning-support sub-programmes
    could instead be told apart by program_type directly) -- this is the one
    place to change it."""
    if len(rows_for_user) == 1:
        return rows_for_user[0]

    def _rank(row: dict) -> tuple[int, int]:
        start = row.get("episodeStartDate")
        end = row.get("episodeEndDate")
        if start is None:
            return (0, 0)
        if end is None:
            return (2, 0)
        return (1, (end - start).days)

    return max(rows_for_user, key=_rank)


def _select_primary_rows(rows: list[dict]) -> list[dict]:
    """Collapses _fetch_aptem_rows's one-row-per-(user,programme) output
    down to one row per learner -- see _select_primary_programme_row."""
    by_user: dict[int, list[dict]] = {}
    for row in rows:
        by_user.setdefault(row["aptemUserId"], []).append(row)
    return [_select_primary_programme_row(user_rows) for user_rows in by_user.values()]


def _find_link_by_user_id(cur, aptem_user_id: int) -> dict | None:
    cur.execute(
        """
        SELECT id, internal_learner_id AS "internalLearnerId", aptem_user_id AS "aptemUserId",
               aptem_program_id AS "aptemProgramId", accepted_synced_at AS "acceptedSyncedAt",
               accepted_values AS "acceptedValues"
        FROM aptem_learner_link WHERE aptem_user_id = %s
        """,
        (aptem_user_id,),
    )
    return cur.fetchone()


def _find_link_by_learner_id(cur, learner_id: int) -> dict | None:
    cur.execute(
        """
        SELECT id, internal_learner_id AS "internalLearnerId", aptem_user_id AS "aptemUserId",
               aptem_program_id AS "aptemProgramId", accepted_synced_at AS "acceptedSyncedAt",
               accepted_values AS "acceptedValues"
        FROM aptem_learner_link WHERE internal_learner_id = %s
        """,
        (learner_id,),
    )
    return cur.fetchone()


def _find_learners_by_uln(cur, uln: str) -> list[dict]:
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", email, programme, uln, '
        'tutor_id AS "tutorId", cohort_id AS "cohortId", start_date AS "startDate", updated_at AS "updatedAt", '
        'status FROM learners WHERE uln = %s AND deleted_at IS NULL',
        (uln,),
    )
    return cur.fetchall()


def _find_learners_by_email(cur, email: str) -> list[dict]:
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", email, programme, uln, '
        'tutor_id AS "tutorId", cohort_id AS "cohortId", start_date AS "startDate", updated_at AS "updatedAt", '
        'status FROM learners WHERE email IS NOT NULL AND lower(email) = lower(%s) AND deleted_at IS NULL',
        (email,),
    )
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Classification (preview)
# ---------------------------------------------------------------------------


def _diff_simple_fields(aptem_row: dict, accepted_or_current: dict, current_uln: str | None) -> dict:
    diff: dict = {}
    for aptem_key, internal_key in _SIMPLE_FIELD_MAP.items():
        before = accepted_or_current.get(internal_key)
        after = aptem_row.get(aptem_key)
        if after and after != before:
            diff[internal_key] = {"before": before, "after": after}
    if aptem_row.get("uln") and not current_uln:
        diff["uln"] = {"before": None, "after": aptem_row["uln"]}
    return diff


def classify_row(cur, aptem_row: dict, baseline: dict) -> dict:
    """Pure classification against current DB state -- never writes
    anything. Returns a dict shaped for insertion into aptem_sync_item
    (minus id/sync_job_id, filled in by the caller).

    `baseline` is accepted for signature parity with Bud's classify_row and
    future use, but -- like Bud's own current behaviour -- is not itself
    queried here; new-learner eligibility is gated purely on
    episode_is_active (see _ELIGIBLE_EPISODE_ACTIVE)."""
    aptem_user_id = aptem_row["aptemUserId"]
    base_item = {
        "source_identifier": str(aptem_user_id),
        "internal_learner_id": None,
        "proposed_values": {},
        "previous_values": {},
        "warnings": [],
        "reason": None,
    }

    link = _find_link_by_user_id(cur, aptem_user_id)
    matched_learner: dict | None = None

    if link is None:
        uln_matches = _find_learners_by_uln(cur, aptem_row["uln"]) if aptem_row.get("uln") else []
        if len(uln_matches) > 1:
            return {**base_item, "match_status": "conflict", "action_type": "none",
                    "reason": "uln_matches_multiple_internal_learners"}

        email_matches = _find_learners_by_email(cur, aptem_row["email"]) if aptem_row.get("email") else []
        if len(email_matches) > 1:
            return {**base_item, "match_status": "conflict", "action_type": "none",
                    "reason": "email_matches_multiple_internal_learners"}

        if uln_matches and email_matches and uln_matches[0]["id"] != email_matches[0]["id"]:
            return {**base_item, "match_status": "conflict", "action_type": "none",
                    "reason": "uln_and_email_disagree"}

        matched_learner = uln_matches[0] if uln_matches else (email_matches[0] if email_matches else None)

        if matched_learner is not None:
            base_item["internal_learner_id"] = matched_learner["id"]
            existing_link_for_learner = _find_link_by_learner_id(cur, matched_learner["id"])
            if existing_link_for_learner and existing_link_for_learner["aptemUserId"] != aptem_user_id:
                return {**base_item, "match_status": "conflict", "action_type": "none",
                        "reason": "learner_already_linked_to_a_different_aptem_record"}
            if existing_link_for_learner is None:
                # First-ever observation for an already-existing Attendance
                # learner -- no prior accepted snapshot exists, so every
                # Aptem-owned field looks "changed" and is proposed for
                # review (mirrors Bud's identical first-observation rule).
                return _classify_existing_learner_update(
                    cur, aptem_row, base_item, {"acceptedValues": {}, "acceptedSyncedAt": None},
                )
            link = existing_link_for_learner

    if link is not None:
        base_item["internal_learner_id"] = link["internalLearnerId"]
        return _classify_existing_learner_update(cur, aptem_row, base_item, link)

    if not aptem_row.get("uln") and not aptem_row.get("email"):
        return {**base_item, "match_status": "conflict", "action_type": "none", "reason": "missing_identifiers"}

    if aptem_row.get("episodeIsActive") is not _ELIGIBLE_EPISODE_ACTIVE:
        return {**base_item, "match_status": "existing_before_trial", "action_type": "none",
                "reason": "unmatched_non_actionable_episode"}

    return _classify_new_learner(cur, aptem_row, base_item)


def _classify_new_learner(cur, aptem_row: dict, base_item: dict) -> dict:
    start_date_value = aptem_row.get("episodeStartDate")
    if not start_date_value:
        return {**base_item, "match_status": "conflict", "action_type": "create_learner", "reason": "missing_start_date"}
    if not aptem_row.get("programName"):
        return {**base_item, "match_status": "conflict", "action_type": "create_learner", "reason": "missing_programme"}

    proposed_learner = {
        "learnerRef": None,
        "uln": aptem_row.get("uln"),
        "firstName": aptem_row.get("firstName"),
        "lastName": aptem_row.get("lastName"),
        "email": aptem_row.get("email"),
        "programme": aptem_row.get("programName"),
        "level": None,
        "startDate": str(start_date_value),
    }

    return {
        **base_item,
        "match_status": "new",
        "action_type": "create_learner",
        "proposed_values": {
            "learner": proposed_learner,
            # No tutor/cohort here -- no tutor mapping this phase (see
            # module docstring); tutor and cohort assignment are both a
            # deliberately separate, later, manual step. Display-only.
            "aptemStatus": {"episodeStatus": aptem_row.get("episodeStatus"),
                             "episodeIsActive": aptem_row.get("episodeIsActive")},
        },
        "previous_values": {"aptemSyncedAt": str(aptem_row["syncedAt"]) if aptem_row.get("syncedAt") else None},
        "warnings": ["learnerRef and level must be supplied before this can be approved."],
        "reason": "new_aptem_record",
    }


def _classify_existing_learner_update(cur, aptem_row: dict, base_item: dict, link: dict) -> dict:
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", email, programme, uln, '
        'tutor_id AS "tutorId", cohort_id AS "cohortId", start_date AS "startDate", updated_at AS "updatedAt", '
        'status FROM learners WHERE id = %s AND deleted_at IS NULL',
        (base_item["internal_learner_id"],),
    )
    learner = cur.fetchone()
    if not learner:
        return {**base_item, "match_status": "conflict", "action_type": "none", "reason": "matched_learner_no_longer_exists"}

    accepted = link.get("acceptedValues") or {}
    fields_diff = _diff_simple_fields(aptem_row, accepted, learner["uln"])

    proposed: dict = {}
    warnings: list[str] = []
    if fields_diff:
        proposed["fields"] = fields_diff

    start_date_change = None
    new_start = aptem_row.get("episodeStartDate")
    if new_start and str(new_start) != str(learner["startDate"]) and str(new_start) != str(accepted.get("episodeStartDate")):
        start_date_change = {"oldStartDate": str(learner["startDate"]), "newStartDate": str(new_start)}
        warnings.append(
            "Start date changed -- the learner.startDate field will be updated, but automatic cohort "
            "re-grouping based on a start-date change is not implemented; move the learner's cohort "
            "manually via Allocation if needed."
        )
        proposed["startDateChange"] = start_date_change

    # Never auto-applies a status change -- Aptem's real status vocabulary is
    # unconfirmed (see module docstring), so this only ever flags the
    # episode_is_active flag flipping as something for an Administrator to
    # review and apply manually, exactly like an "informational-only" Bud
    # status blip (action_type stays "none").
    status_change = None
    episode_is_active = aptem_row.get("episodeIsActive")
    accepted_episode_is_active = accepted.get("episodeIsActive")
    if accepted and episode_is_active is not None and episode_is_active != accepted_episode_is_active:
        status_change = {
            "previousAcceptedEpisodeIsActive": accepted_episode_is_active,
            "currentEpisodeIsActive": episode_is_active,
            "currentEpisodeStatus": aptem_row.get("episodeStatus"),
            "currentLearnerStatus": learner["status"],
        }
        warnings.append(
            f"Aptem reports this learner's episode active flag changed to {episode_is_active} -- status "
            "mapping is not yet implemented for Aptem, review and change the learner's status manually "
            "via the Learner record if appropriate."
        )
        proposed["statusChange"] = status_change

    if not proposed and not warnings:
        return {**base_item, "match_status": "unchanged", "action_type": "none", "reason": "no_proposable_change"}

    match_status = "status_change" if status_change else "existing_update"
    if start_date_change:
        action_type = "change_start_date"
    elif fields_diff:
        action_type = "update_learner"
    else:
        action_type = "none"  # informational-only status blip: nothing to apply, just acknowledge + snapshot

    return {
        **base_item,
        "match_status": match_status,
        "action_type": action_type,
        "proposed_values": proposed,
        "previous_values": {
            "fields": {k: learner.get(k) for k in fields_diff} if fields_diff else {},
            "updatedAt": str(learner["updatedAt"]),
            "aptemSyncedAt": str(aptem_row["syncedAt"]) if aptem_row.get("syncedAt") else None,
        },
        "warnings": warnings,
        "reason": "post_baseline_aptem_change",
    }


# ---------------------------------------------------------------------------
# Preview orchestration
# ---------------------------------------------------------------------------

_ITEM_INSERT_SQL = """
    INSERT INTO aptem_sync_item
        (sync_job_id, source_identifier, match_status, action_type, internal_learner_id,
         proposed_values, previous_values, warnings, reason, approved,
         source_first_name, source_last_name, source_email)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def run_preview(cur, request: Request, session: dict) -> dict:
    """Mirrors bud_sync_lib.run_preview's transaction/failure discipline
    exactly -- see its own docstring for the reasoning (job row created as
    its own pre-failure-survivable statement, 'ready' only as the very last
    statement of one all-or-nothing transaction)."""
    baseline = get_active_baseline(cur)
    if baseline is None:
        raise HTTPException(status_code=409, detail="No active Aptem sync baseline. Establish one before previewing.")

    cur.execute(
        """
        INSERT INTO aptem_sync_job (baseline_id, started_by, source_max_synced_at, status, correlation_id)
        VALUES (%s, %s, %s, 'generating', %s) RETURNING id
        """,
        (baseline["id"], session["userId"], baseline["sourceMaxSyncedAt"], get_correlation_id() or None),
    )
    job_id = cur.fetchone()["id"]

    try:
        aptem_rows = _select_primary_rows(_fetch_aptem_rows(cur))

        counts = {"new": 0, "existing_update": 0, "unchanged": 0, "conflict": 0,
                  "existing_before_trial": 0, "status_change": 0}

        with cur.connection.transaction():
            for row in aptem_rows:
                item = classify_row(cur, row, baseline)
                counts[item["match_status"]] += 1

                cur.execute(
                    _ITEM_INSERT_SQL,
                    (
                        job_id, item["source_identifier"], item["match_status"], item["action_type"],
                        item["internal_learner_id"], json.dumps(item["proposed_values"], default=str),
                        json.dumps(item["previous_values"], default=str), json.dumps(item["warnings"]), item["reason"],
                        False,
                        row.get("firstName"), row.get("lastName"), row.get("email"),
                    ),
                )

            cur.execute(
                """
                UPDATE aptem_sync_job SET
                    status = 'ready', total_source_rows_examined = %s,
                    new_learners_detected = %s, learner_updates_detected = %s,
                    conflict_count = %s, skipped_count = %s, status_changes_detected = %s
                WHERE id = %s
                """,
                (len(aptem_rows), counts["new"], counts["existing_update"],
                 counts["conflict"], counts["existing_before_trial"], counts["status_change"], job_id),
            )
    except Exception as exc:
        cur.execute(
            "UPDATE aptem_sync_job SET status = 'failed', error_summary = %s WHERE id = %s",
            (str(exc.detail) if isinstance(exc, HTTPException) else str(exc), job_id),
        )
        write_audit_log(
            request, action="aptem_sync_preview_failed", entity_type="aptem_sync_job", entity_id=job_id,
            new_value={"error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc)},
        )
        raise

    write_audit_log(
        request, action="aptem_sync_preview_created", entity_type="aptem_sync_job", entity_id=job_id,
        new_value={"baselineId": baseline["id"], "counts": counts}, cur=cur,
    )
    return get_job(cur, job_id)


def get_job(cur, job_id: int) -> dict:
    cur.execute(
        """
        SELECT id, baseline_id AS "baselineId", status, started_at AS "startedAt", completed_at AS "completedAt",
               started_by AS "startedBy", source_max_synced_at AS "sourceMaxSyncedAt",
               total_source_rows_examined AS "totalSourceRowsExamined",
               new_learners_detected AS "newLearnersDetected", learner_updates_detected AS "learnerUpdatesDetected",
               approved_count AS "approvedCount", applied_count AS "appliedCount", skipped_count AS "skippedCount",
               conflict_count AS "conflictCount", error_count AS "errorCount", approval_reason AS "approvalReason",
               correlation_id AS "correlationId", error_summary AS "errorSummary",
               status_changes_detected AS "statusChangesDetected"
        FROM aptem_sync_job WHERE id = %s
        """,
        (job_id,),
    )
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return job


def list_jobs(cur, page: int, page_size: int) -> dict:
    cur.execute("SELECT count(*)::int AS count FROM aptem_sync_job")
    total = cur.fetchone()["count"]
    cur.execute(
        """
        SELECT id, baseline_id AS "baselineId", status, started_at AS "startedAt", completed_at AS "completedAt",
               new_learners_detected AS "newLearnersDetected", learner_updates_detected AS "learnerUpdatesDetected",
               conflict_count AS "conflictCount", applied_count AS "appliedCount",
               status_changes_detected AS "statusChangesDetected"
        FROM aptem_sync_job ORDER BY started_at DESC LIMIT %s OFFSET %s
        """,
        (page_size, (page - 1) * page_size),
    )
    return {"items": cur.fetchall(), "total": total, "page": page, "pageSize": page_size}


def list_items(cur, job_id: int, match_status: str | None, action_type: str | None, page: int, page_size: int) -> dict:
    clauses = ["sync_job_id = %s"]
    params: list = [job_id]
    if match_status:
        clauses.append("match_status = %s")
        params.append(match_status)
    if action_type:
        clauses.append("action_type = %s")
        params.append(action_type)
    where = " AND ".join(clauses)

    cur.execute(f"SELECT count(*)::int AS count FROM aptem_sync_item WHERE {where}", params)
    total = cur.fetchone()["count"]
    cur.execute(
        f"""
        SELECT id, sync_job_id AS "syncJobId", source_identifier AS "sourceIdentifier", match_status AS "matchStatus",
               action_type AS "actionType", internal_learner_id AS "internalLearnerId",
               proposed_values AS "proposedValues", previous_values AS "previousValues", warnings, reason,
               approved, applied, outcome, error_code AS "errorCode", processed_at AS "processedAt",
               source_first_name AS "sourceFirstName", source_last_name AS "sourceLastName",
               source_email AS "sourceEmail", created_at AS "createdAt"
        FROM aptem_sync_item WHERE {where} ORDER BY id LIMIT %s OFFSET %s
        """,
        [*params, page_size, (page - 1) * page_size],
    )
    return {"items": cur.fetchall(), "total": total, "page": page, "pageSize": page_size}


# ---------------------------------------------------------------------------
# Item edit (Administrator supplies a missing required field, or approves)
# ---------------------------------------------------------------------------


def _item_missing_fields(item: dict) -> list[str]:
    if item["actionType"] != "create_learner":
        return []
    missing = []
    learner_values = item["proposedValues"].get("learner", {})
    for field in _REQUIRED_LEARNER_FIELDS:
        if not learner_values.get(field):
            missing.append(f"learner.{field}")
    return missing


def update_item(cur, job_id: int, item_id: int, field_updates: dict | None, approved: bool | None) -> dict:
    cur.execute("SELECT status FROM aptem_sync_job WHERE id = %s", (job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Job is not in a reviewable state (status={job['status']})")

    cur.execute(
        """
        SELECT id, sync_job_id AS "syncJobId", match_status AS "matchStatus", action_type AS "actionType",
               proposed_values AS "proposedValues", approved
        FROM aptem_sync_item WHERE id = %s AND sync_job_id = %s
        """,
        (item_id, job_id),
    )
    item = cur.fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="Sync item not found")

    proposed = item["proposedValues"]
    if field_updates:
        for path, value in field_updates.items():
            section, _, key = path.partition(".")
            if section not in proposed or not isinstance(proposed[section], dict):
                raise HTTPException(status_code=400, detail=f"Unknown field path: {path}")
            proposed[section][key] = value
        cur.execute("UPDATE aptem_sync_item SET proposed_values = %s WHERE id = %s", (json.dumps(proposed, default=str), item_id))

    if approved is not None:
        if approved:
            if item["matchStatus"] == "conflict":
                raise HTTPException(status_code=400, detail="Conflicted items cannot be approved")
            missing = _item_missing_fields({**item, "proposedValues": proposed})
            if missing:
                raise HTTPException(status_code=400, detail=f"Required fields missing before approval: {', '.join(missing)}")
        cur.execute("UPDATE aptem_sync_item SET approved = %s WHERE id = %s", (approved, item_id))

    cur.execute(
        """
        SELECT id, sync_job_id AS "syncJobId", source_identifier AS "sourceIdentifier", match_status AS "matchStatus",
               action_type AS "actionType", internal_learner_id AS "internalLearnerId",
               proposed_values AS "proposedValues", previous_values AS "previousValues", warnings, reason,
               approved, applied, outcome, error_code AS "errorCode", processed_at AS "processedAt",
               source_first_name AS "sourceFirstName", source_last_name AS "sourceLastName",
               source_email AS "sourceEmail", created_at AS "createdAt"
        FROM aptem_sync_item WHERE id = %s
        """,
        (item_id,),
    )
    return cur.fetchone()


def bulk_approve_new_learners(cur, job_id: int, items: list[dict], request: Request, session: dict) -> dict:
    """Mirrors bud_sync_lib.bulk_approve_new_learners exactly -- see its own
    docstring for the reasoning (partial success per-item, not
    all-or-nothing; duplicate/collision checks run both within the batch and
    against existing learners, including soft-deleted ones)."""
    cur.execute("SELECT status FROM aptem_sync_job WHERE id = %s", (job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Job is not in a reviewable state (status={job['status']})")

    submitted_refs = {entry["learnerRef"].strip() for entry in items if entry.get("learnerRef", "").strip()}
    existing_refs: set[str] = set()
    if submitted_refs:
        cur.execute("SELECT learner_ref FROM learners WHERE learner_ref = ANY(%s)", (list(submitted_refs),))
        existing_refs = {r["learner_ref"] for r in cur.fetchall()}

    approved_ids: list[int] = []
    errors: list[dict] = []
    claimed_refs: dict[str, int] = {}
    with cur.connection.transaction():
        for entry in items:
            item_id = entry["itemId"]
            cur.execute(
                """
                SELECT id, match_status AS "matchStatus", proposed_values AS "proposedValues", applied
                FROM aptem_sync_item WHERE id = %s AND sync_job_id = %s
                """,
                (item_id, job_id),
            )
            item = cur.fetchone()
            if not item:
                errors.append({"itemId": item_id, "message": "Sync item not found"})
                continue
            if item["applied"]:
                errors.append({"itemId": item_id, "message": "Already applied"})
                continue
            if item["matchStatus"] != "new":
                errors.append({"itemId": item_id, "message": "Not a new-learner item"})
                continue
            learner_ref = entry["learnerRef"].strip()
            level = entry["level"].strip()
            if not learner_ref or not level:
                errors.append({"itemId": item_id, "message": "learnerRef and level are both required"})
                continue
            if learner_ref in existing_refs:
                errors.append({
                    "itemId": item_id,
                    "message": f"'{learner_ref}' is already used by an existing learner -- use Already represented to link this row to them instead",
                })
                continue
            if learner_ref in claimed_refs:
                errors.append({
                    "itemId": item_id,
                    "message": f"'{learner_ref}' is also used by item #{claimed_refs[learner_ref]} in this batch -- give it a different reference",
                })
                continue
            claimed_refs[learner_ref] = item_id

            proposed = item["proposedValues"]
            proposed["learner"]["learnerRef"] = learner_ref
            proposed["learner"]["level"] = level
            cur.execute(
                "UPDATE aptem_sync_item SET proposed_values = %s, approved = true WHERE id = %s",
                (json.dumps(proposed, default=str), item_id),
            )
            approved_ids.append(item_id)

        if approved_ids:
            write_audit_log(
                request, action="aptem_sync_bulk_approved", entity_type="aptem_sync_job", entity_id=job_id,
                new_value={"approvedCount": len(approved_ids), "itemIds": approved_ids}, cur=cur,
            )

    return {"approvedCount": len(approved_ids), "errors": errors}


def link_existing_learner(cur, job_id: int, item_id: int, target_learner_id: int, request: Request, session: dict) -> dict:
    cur.execute("SELECT status FROM aptem_sync_job WHERE id = %s", (job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Job is not in a reviewable state (status={job['status']})")

    cur.execute(
        'SELECT id, source_identifier AS "sourceIdentifier", match_status AS "matchStatus" '
        "FROM aptem_sync_item WHERE id = %s AND sync_job_id = %s",
        (item_id, job_id),
    )
    item = cur.fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="Sync item not found")
    if item["matchStatus"] != "new":
        raise HTTPException(status_code=400, detail="Only a 'new learner' item can be manually linked to an existing learner")

    cur.execute("SELECT id FROM learners WHERE id = %s AND deleted_at IS NULL", (target_learner_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Learner not found")

    existing_link = _find_link_by_learner_id(cur, target_learner_id)
    if existing_link and str(existing_link["aptemUserId"]) != item["sourceIdentifier"]:
        raise HTTPException(status_code=409, detail="This learner is already linked to a different Aptem record")

    fresh_rows = {str(r["aptemUserId"]): r for r in _select_primary_rows(_fetch_aptem_rows(cur))}
    fresh_row = fresh_rows.get(item["sourceIdentifier"])
    if fresh_row is None:
        raise HTTPException(status_code=409, detail="This Aptem row is no longer present in the source -- generate a new preview")

    with cur.connection.transaction():
        _upsert_learner_link(cur, target_learner_id, fresh_row, job_id)
        cur.execute(
            "UPDATE aptem_sync_item SET approved = true, applied = true, outcome = 'manually_linked', "
            "internal_learner_id = %s, processed_at = now() WHERE id = %s",
            (target_learner_id, item_id),
        )
        write_audit_log(
            request, action="aptem_sync_manual_link_established", entity_type="learner", entity_id=target_learner_id,
            new_value={"sourceIdentifier": item["sourceIdentifier"], "syncItemId": item_id}, cur=cur,
        )

    return get_job(cur, job_id)


def get_job_summary(cur, job_id: int) -> dict:
    cur.execute(
        """
        SELECT match_status, count(*)::int AS count
        FROM aptem_sync_item WHERE sync_job_id = %s AND applied = false
        GROUP BY match_status
        """,
        (job_id,),
    )
    open_counts = {row["match_status"]: row["count"] for row in cur.fetchall()}

    today = date.today()
    cur.execute(
        """
        SELECT count(*)::int AS count FROM aptem_sync_item
        WHERE sync_job_id = %s AND applied = true AND action_type = 'create_learner' AND processed_at::date = %s
        """,
        (job_id, today),
    )
    created_today = cur.fetchone()["count"]

    cur.execute("SELECT completed_at AS \"completedAt\" FROM aptem_sync_job WHERE status = 'completed' ORDER BY completed_at DESC LIMIT 1")
    last_commit = cur.fetchone()

    return {
        "statusChangesCount": open_counts.get("status_change", 0),
        "newLearnersCount": open_counts.get("new", 0),
        "conflictsCount": open_counts.get("conflict", 0),
        "learnersCreatedToday": created_today,
        "lastSuccessfulSyncAt": last_commit["completedAt"] if last_commit else None,
    }


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def _get_trial_limits(cur) -> dict:
    cur.execute(
        "SELECT aptem_sync_max_learner_creations, aptem_sync_max_learner_updates, "
        "aptem_sync_max_cohort_creations FROM app_settings WHERE id = 1"
    )
    row = cur.fetchone()
    return {
        "learnerCreations": row["aptem_sync_max_learner_creations"],
        "learnerUpdates": row["aptem_sync_max_learner_updates"],
        "cohortCreations": row["aptem_sync_max_cohort_creations"],
    }


def _count_batch_actions(items: list[dict]) -> dict:
    counts = {"learnerCreations": 0, "learnerUpdates": 0, "cohortCreations": 0}
    for item in items:
        if item["actionType"] == "create_learner":
            counts["learnerCreations"] += 1
        elif item["actionType"] in ("update_learner", "change_start_date", "change_status"):
            counts["learnerUpdates"] += 1
    return counts


def _build_accepted_values(aptem_row: dict) -> dict:
    values = {internal_key: aptem_row.get(aptem_key) for aptem_key, internal_key in _SIMPLE_FIELD_MAP.items()}
    values["episodeIsActive"] = aptem_row.get("episodeIsActive")
    values["episodeStatus"] = aptem_row.get("episodeStatus")
    values["episodeStartDate"] = str(aptem_row["episodeStartDate"]) if aptem_row.get("episodeStartDate") else None
    return values


def _upsert_learner_link(cur, learner_id: int, aptem_row: dict, job_id: int) -> None:
    cur.execute(
        """
        INSERT INTO aptem_learner_link
            (internal_learner_id, aptem_user_id, aptem_program_id, accepted_synced_at, accepted_values, last_sync_job_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (internal_learner_id) DO UPDATE SET
            aptem_user_id = EXCLUDED.aptem_user_id,
            aptem_program_id = EXCLUDED.aptem_program_id,
            accepted_synced_at = EXCLUDED.accepted_synced_at,
            accepted_values = EXCLUDED.accepted_values,
            last_sync_job_id = EXCLUDED.last_sync_job_id,
            updated_at = now()
        """,
        (
            learner_id, aptem_row["aptemUserId"], aptem_row.get("aptemProgramId"), aptem_row.get("syncedAt"),
            json.dumps(_build_accepted_values(aptem_row), default=str), job_id,
        ),
    )


def _apply_new_learner(cur, item: dict, request: Request, session: dict) -> int:
    """Creates the learner with no tutor and no cohort -- no tutor mapping
    this phase (see module docstring); both are a deliberately separate,
    later, manual step, exactly mirroring Bud's own precedent."""
    from .routers.learners import LearnerInput, _create_learner

    learner_values = item["proposedValues"]["learner"]
    learner_payload = LearnerInput(
        learnerRef=learner_values["learnerRef"],
        uln=learner_values.get("uln"),
        firstName=learner_values["firstName"],
        lastName=learner_values["lastName"],
        email=learner_values.get("email"),
        programme=learner_values["programme"],
        level=learner_values["level"],
        startDate=learner_values["startDate"],
        tutorId=None,
    )
    created = _create_learner(cur, learner_payload, request, session)
    return created["id"]


def _apply_existing_learner_update(cur, item: dict, request: Request, session: dict) -> int:
    """Never applies a status change -- see _classify_existing_learner_update's
    docstring; a 'status_change' match is always informational-only
    (action_type 'none') this phase."""
    from .routers.learners import LearnerUpdate, _update_learner

    learner_id = item["internalLearnerId"]
    proposed = item["proposedValues"]

    update_kwargs = dict(proposed.get("fields", {}))
    update_kwargs = {k: v["after"] for k, v in update_kwargs.items()}
    if "startDateChange" in proposed:
        update_kwargs["startDate"] = proposed["startDateChange"]["newStartDate"]
    if update_kwargs:
        _update_learner(cur, learner_id, LearnerUpdate(**update_kwargs), request, session)

    return learner_id


def run_commit(cur, job_id: int, item_ids: list[int], approval_reason: str, limit_override_reason: str | None,
                request: Request, session: dict) -> dict:
    job = get_job(cur, job_id)
    if job["status"] == "completed":
        return job  # idempotent replay -- no reapplication
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Job is not ready to commit (status={job['status']})")

    active_baseline = get_active_baseline(cur)
    if active_baseline is None or active_baseline["id"] != job["baselineId"]:
        raise HTTPException(
            status_code=409,
            detail="The Aptem sync baseline has changed since this preview was generated. Generate a new preview.",
        )

    cur.execute(
        """
        SELECT id, sync_job_id AS "syncJobId", source_identifier AS "sourceIdentifier", match_status AS "matchStatus",
               action_type AS "actionType", internal_learner_id AS "internalLearnerId",
               proposed_values AS "proposedValues", previous_values AS "previousValues"
        FROM aptem_sync_item WHERE id = ANY(%s) AND sync_job_id = %s
        """,
        (item_ids, job_id),
    )
    items = cur.fetchall()
    found_ids = {i["id"] for i in items}
    missing_ids = set(item_ids) - found_ids
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"Sync item(s) not found in this job: {sorted(missing_ids)}")

    conflicted = [i["id"] for i in items if i["matchStatus"] == "conflict"]
    if conflicted:
        raise HTTPException(status_code=400, detail=f"Conflicted items cannot be committed: {conflicted}")
    incomplete = [i["id"] for i in items if _item_missing_fields(i)]
    if incomplete:
        raise HTTPException(status_code=400, detail=f"Item(s) missing required fields: {incomplete}")

    ref_to_item_ids: dict[str, list[int]] = {}
    for i in items:
        if i["actionType"] != "create_learner":
            continue
        ref_to_item_ids.setdefault(i["proposedValues"]["learner"]["learnerRef"], []).append(i["id"])
    duplicated_refs = {ref: ids for ref, ids in ref_to_item_ids.items() if len(ids) > 1}
    if duplicated_refs:
        detail = "; ".join(f"'{ref}' used by items {ids}" for ref, ids in duplicated_refs.items())
        raise HTTPException(status_code=400, detail=f"Duplicate learner reference(s) within this batch: {detail}")
    if ref_to_item_ids:
        cur.execute("SELECT learner_ref FROM learners WHERE learner_ref = ANY(%s)", (list(ref_to_item_ids.keys()),))
        existing_refs = {r["learner_ref"] for r in cur.fetchall()}
        if existing_refs:
            detail = "; ".join(f"'{ref}' (item {ref_to_item_ids[ref]})" for ref in sorted(existing_refs))
            raise HTTPException(
                status_code=400,
                detail=f"Reference(s) already used by an existing learner -- use Already represented to link instead: {detail}",
            )

    surviving: list[dict] = []
    stale_source_ids: list[int] = []
    stale_internal_ids: list[int] = []
    fresh_rows_by_user_id: dict[str, dict] = {str(r["aptemUserId"]): r for r in _select_primary_rows(_fetch_aptem_rows(cur))}
    for item in items:
        previous = item["previousValues"] or {}
        fresh_row = fresh_rows_by_user_id.get(item["sourceIdentifier"])
        previous_synced_at = previous.get("aptemSyncedAt")
        fresh_synced_at = str(fresh_row["syncedAt"]) if fresh_row and fresh_row.get("syncedAt") else None
        if item["actionType"] != "none" and previous_synced_at and fresh_synced_at != previous_synced_at:
            stale_source_ids.append(item["id"])
            continue

        if item["internalLearnerId"] is not None and previous.get("updatedAt"):
            cur.execute("SELECT updated_at AS \"updatedAt\" FROM learners WHERE id = %s", (item["internalLearnerId"],))
            current = cur.fetchone()
            if current and str(current["updatedAt"]) != previous["updatedAt"]:
                stale_internal_ids.append(item["id"])
                continue

        surviving.append(item)

    for item_id in stale_source_ids + stale_internal_ids:
        cur.execute(
            "UPDATE aptem_sync_item SET outcome = %s, processed_at = now() WHERE id = %s",
            ("stale_internal_rejected" if item_id in stale_internal_ids else "stale_source_rejected", item_id),
        )

    limits = _get_trial_limits(cur)
    batch_counts = _count_batch_actions(surviving)
    over_limit = {k: v for k, v in batch_counts.items() if v > limits[k]}
    if over_limit and not limit_override_reason:
        raise HTTPException(
            status_code=409,
            detail={"reason": "trial_limit_exceeded", "overLimit": over_limit, "limits": limits},
        )

    cur.execute("UPDATE aptem_sync_job SET status = 'committing' WHERE id = %s AND status = 'ready'", (job_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail="Job is not ready to commit (status changed concurrently)")

    try:
        with cur.connection.transaction():
            if over_limit and limit_override_reason:
                write_audit_log(
                    request, action="aptem_sync_commit_limit_override", entity_type="aptem_sync_job", entity_id=job_id,
                    new_value={"overLimit": over_limit, "reason": limit_override_reason}, cur=cur,
                )

            applied_count = 0
            for item in surviving:
                fresh_row = fresh_rows_by_user_id.get(item["sourceIdentifier"])
                if item["actionType"] == "create_learner":
                    learner_id = _apply_new_learner(cur, item, request, session)
                elif item["actionType"] in ("update_learner", "change_start_date", "change_status") or (
                    item["actionType"] == "none" and item["matchStatus"] == "status_change"
                ):
                    learner_id = _apply_existing_learner_update(cur, item, request, session)
                else:
                    cur.execute(
                        "UPDATE aptem_sync_item SET approved = true, applied = true, outcome = 'skipped_no_action', processed_at = now() WHERE id = %s",
                        (item["id"],),
                    )
                    continue

                if fresh_row:
                    _upsert_learner_link(cur, learner_id, fresh_row, job_id)
                cur.execute(
                    "UPDATE aptem_sync_item SET approved = true, applied = true, outcome = 'applied', "
                    "internal_learner_id = %s, processed_at = now() WHERE id = %s",
                    (learner_id, item["id"]),
                )
                write_audit_log(
                    request, action="aptem_sync_item_applied", entity_type="aptem_sync_item", entity_id=item["id"],
                    new_value={"actionType": item["actionType"], "learnerId": learner_id}, cur=cur,
                )
                applied_count += 1

            cur.execute(
                """
                UPDATE aptem_sync_job SET status = 'completed', completed_at = now(), approval_reason = %s,
                    approved_count = %s, applied_count = %s, skipped_count = skipped_count + %s
                WHERE id = %s
                """,
                (approval_reason, len(surviving), applied_count,
                 len(stale_source_ids) + len(stale_internal_ids), job_id),
            )
    except Exception as exc:
        cur.execute(
            "UPDATE aptem_sync_job SET status = 'failed', error_summary = %s WHERE id = %s",
            (str(exc.detail) if isinstance(exc, HTTPException) else str(exc), job_id),
        )
        write_audit_log(
            request, action="aptem_sync_commit_failed", entity_type="aptem_sync_job", entity_id=job_id,
            new_value={"error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc)},
        )
        raise

    write_audit_log(
        request, action="aptem_sync_commit_completed", entity_type="aptem_sync_job", entity_id=job_id,
        new_value={"appliedCount": applied_count, "approvalReason": approval_reason}, cur=cur,
    )
    return get_job(cur, job_id)
