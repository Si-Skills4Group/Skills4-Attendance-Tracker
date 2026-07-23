"""Controlled Bud delta-synchronisation trial (Phase 11) -- an
Administrator-only, preview-then-commit workflow that proposes (never
silently applies) creating learners who newly appear in Bud after a trial
baseline, and updating already-matched learners based on Bud changes
observed after that baseline. This module never performs a historical
backfill: a Bud row observed at-or-before the active baseline is always
classified `existing_before_trial` and excluded, regardless of whether it
matches an internal learner.

public.learner_progress is owned entirely by a separate, already-deployed
sync service -- every query here is a plain SELECT, and this module must
never write to it.

Matching hierarchy (see the Phase 11 plan for the evidence behind this):
1. Already Bud-linked (bud_learner_link.bud_learning_plan_id) -- an
   established link always wins.
2. learners.uln = learner_progress.unique_learner_number, exact match to
   exactly one internal learner (the only cross-system identifier that is
   actually unique-indexed on the internal side today).
3. No reliable identifier -> unmatched.
Never matched by name, email, or tutor_name.
"""
from __future__ import annotations

import json
from datetime import date

from fastapi import HTTPException, Request

from .allocation_lib import apply_transfer
from .audit import write_audit_log
from .correlation import get_correlation_id

# Bud-owned learner fields this trial will propose synchronising, and their
# internal learners-table counterpart. uln is handled separately (only ever
# set when the internal value is currently null -- see _diff_simple_fields).
_SIMPLE_FIELD_MAP = {
    "learnerForename": "firstName",
    "learnerSurname": "lastName",
    "learnerEmail": "email",
    "learnerMobile": "mobile",
    "programmeName": "programme",
}

# Fields an Administrator must supply before a create_learner/create_cohort
# proposal becomes approvable -- Bud has no equivalent for any of these, so
# they are never invented (per the trial's explicit "do not guess" rule).
_REQUIRED_LEARNER_FIELDS = ("learnerRef", "level")
_REQUIRED_COHORT_FIELDS = ("deliveryDay", "sessionStartTime", "sessionEndTime")

# Confirmed against real production data: an exact, real status_desc value
# (alongside Completed/Withdrawn/Pending/On Break/In End Point Assessment/
# Break Requested/Break Return Requested/Withdrawal Requested). Only rows
# with this status are ever considered by this trial -- anything else is
# excluded entirely, not merely deprioritised.
_ELIGIBLE_STATUS_DESC = "In Progress"


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def get_active_baseline(cur) -> dict | None:
    cur.execute(
        """
        SELECT id, established_at AS "establishedAt", established_by AS "establishedBy",
               source_max_synced_at AS "sourceMaxSyncedAt", source_row_count AS "sourceRowCount",
               status, notes, correlation_id AS "correlationId"
        FROM bud_sync_baseline WHERE status = 'active'
        """
    )
    return cur.fetchone()


def get_source_status(cur) -> dict:
    """Read-only snapshot used by the trial's status card, both before and
    after a baseline exists -- never mutates anything. Scoped to the same
    status_desc = 'In Progress' rows the rest of the trial considers, so
    these counts describe what the trial will actually act on, not the
    whole Bud table (which also carries Completed/Withdrawn/Pending/etc
    rows this trial never touches)."""
    cur.execute(
        'SELECT count(*)::int AS "rowCount", max(synced_at) AS "maxSyncedAt" '
        "FROM public.learner_progress WHERE status_desc = %s",
        (_ELIGIBLE_STATUS_DESC,),
    )
    source = cur.fetchone()
    baseline = get_active_baseline(cur)

    matched_count = 0
    unmatched_count = 0
    if source["rowCount"]:
        cur.execute(
            "SELECT unique_learner_number AS uln FROM public.learner_progress "
            "WHERE unique_learner_number IS NOT NULL AND unique_learner_number <> '' AND status_desc = %s",
            (_ELIGIBLE_STATUS_DESC,),
        )
        ulns = [r["uln"] for r in cur.fetchall()]
        if ulns:
            cur.execute("SELECT count(*)::int AS count FROM learners WHERE uln = ANY(%s)", (ulns,))
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
    """Zero business writes -- only ever inserts one bud_sync_baseline row
    plus its identity snapshot (bud_sync_baseline_snapshot), both trial
    bookkeeping, never a learner/cohort/allocation. Records already present
    in public.learner_progress at this instant are the trial's "before"
    line: nothing about them is ever proposed as new merely because they
    are unmatched. Scoped to status_desc = 'In Progress' only, matching
    _fetch_bud_rows -- a Completed/Withdrawn/Pending/etc row must never
    influence the baseline this trial actually uses.

    The snapshot -- which learning_plan_ids exist right now -- is what
    actually answers "did this Bud record exist before the trial started",
    not source_max_synced_at: real Bud data bulk-touches synced_at across
    the whole table on every sync, whether or not a given row's data
    changed, so a pure timestamp cutoff would treat every pre-existing
    row as newly eligible again the very next time Bud syncs."""
    if get_active_baseline(cur) is not None:
        raise HTTPException(status_code=409, detail="A trial baseline is already active. Reset it first.")

    cur.execute(
        'SELECT count(*)::int AS "rowCount", max(synced_at) AS "maxSyncedAt" '
        "FROM public.learner_progress WHERE status_desc = %s",
        (_ELIGIBLE_STATUS_DESC,),
    )
    source = cur.fetchone()

    cur.execute(
        """
        INSERT INTO bud_sync_baseline (established_by, source_max_synced_at, source_row_count, notes, correlation_id)
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
        INSERT INTO bud_sync_baseline_snapshot (baseline_id, source_identifier)
        SELECT %s, learning_plan_id
        FROM (
            SELECT DISTINCT learning_plan_id FROM public.learner_progress
            WHERE status_desc = %s AND learning_plan_id IS NOT NULL
        ) AS eligible_rows
        ON CONFLICT (baseline_id, source_identifier) DO NOTHING
        """,
        (baseline["id"], _ELIGIBLE_STATUS_DESC),
    )

    write_audit_log(
        request, action="bud_sync_baseline_established", entity_type="bud_sync_baseline",
        entity_id=baseline["id"], new_value=baseline, cur=cur,
    )
    return baseline


def reset_baseline(cur, request: Request, session: dict, reason: str) -> dict:
    current = get_active_baseline(cur)
    if current is None:
        raise HTTPException(status_code=409, detail="No active baseline to reset")

    cur.execute(
        """
        UPDATE bud_sync_baseline
        SET status = 'superseded', superseded_at = now(), superseded_by = %s, reset_reason = %s
        WHERE id = %s
        """,
        (session["userId"], reason, current["id"]),
    )
    write_audit_log(
        request, action="bud_sync_baseline_reset", entity_type="bud_sync_baseline",
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
        FROM bud_sync_baseline ORDER BY established_at DESC
        """
    )
    return cur.fetchall()


def _get_baseline_snapshot_ids(cur, baseline_id: int) -> set[str]:
    cur.execute("SELECT source_identifier FROM bud_sync_baseline_snapshot WHERE baseline_id = %s", (baseline_id,))
    return {row["source_identifier"] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Source read + matching
# ---------------------------------------------------------------------------


def _fetch_bud_rows(cur) -> list[dict]:
    """Only 'In Progress' Bud rows are ever considered for this trial --
    confirmed against real production data that this is an exact, real
    status_desc value (alongside Completed/Withdrawn/Pending/On Break/etc).
    A learner whose Bud status_desc is anything else is excluded entirely:
    never proposed as new, never proposed as an update, and -- if it was
    previously 'In Progress' and has since moved on -- simply stops being
    considered by this trial from that point forward (no update is ever
    proposed to reflect the status change itself; see the field-ownership
    notes on status_desc for why that's a warning-only signal, not an
    actionable one, this trial)."""
    cur.execute(
        """
        SELECT learning_plan_id AS "learningPlanId", apprentice_id AS "apprenticeId",
               learner_forename AS "learnerForename", learner_surname AS "learnerSurname",
               learner_email AS "learnerEmail", learner_mobile AS "learnerMobile",
               learner_reference AS "learnerReference", unique_learner_number AS "uln",
               start_date AS "startDate", tutor_name AS "tutorName", tutor_id AS "budTutorId",
               programme_name AS "programmeName", status_desc AS "statusDesc",
               learning_plan_url AS "learningPlanUrl", synced_at AS "syncedAt"
        FROM public.learner_progress
        WHERE learning_plan_id IS NOT NULL AND status_desc = %s
        """,
        (_ELIGIBLE_STATUS_DESC,),
    )
    rows = cur.fetchall()
    # Defensive dedup, mirroring bud_progress.py's own "latest synced_at
    # wins" rule -- the source is expected to upsert by learning_plan_id,
    # but never trust that without a check.
    by_plan: dict[str, dict] = {}
    for row in rows:
        plan_id = row["learningPlanId"]
        existing = by_plan.get(plan_id)
        if existing is None or (row["syncedAt"] and (not existing["syncedAt"] or row["syncedAt"] > existing["syncedAt"])):
            by_plan[plan_id] = row
    return list(by_plan.values())


def _find_link_by_plan_id(cur, learning_plan_id: str) -> dict | None:
    cur.execute(
        """
        SELECT id, internal_learner_id AS "internalLearnerId", bud_learning_plan_id AS "budLearningPlanId",
               bud_apprentice_id AS "budApprenticeId", bud_uln AS "budUln",
               accepted_synced_at AS "acceptedSyncedAt", accepted_values AS "acceptedValues"
        FROM bud_learner_link WHERE bud_learning_plan_id = %s
        """,
        (learning_plan_id,),
    )
    return cur.fetchone()


def _find_link_by_learner_id(cur, learner_id: int) -> dict | None:
    cur.execute(
        """
        SELECT id, internal_learner_id AS "internalLearnerId", bud_learning_plan_id AS "budLearningPlanId",
               bud_apprentice_id AS "budApprenticeId", bud_uln AS "budUln",
               accepted_synced_at AS "acceptedSyncedAt", accepted_values AS "acceptedValues"
        FROM bud_learner_link WHERE internal_learner_id = %s
        """,
        (learner_id,),
    )
    return cur.fetchone()


def _find_learners_by_uln(cur, uln: str) -> list[dict]:
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", tutor_id AS "tutorId", '
        'cohort_id AS "cohortId", start_date AS "startDate", updated_at AS "updatedAt" '
        "FROM learners WHERE uln = %s AND deleted_at IS NULL",
        (uln,),
    )
    return cur.fetchall()


def _find_tutor_by_bud_id(cur, bud_tutor_id: str | None) -> dict | None:
    """Bud's tutor_id has no dedicated internal column -- tutors.external_system_id
    (an existing, generic, admin-editable reference field) is the agreed home
    for it. Ambiguous (more than one match, since external_system_id has no
    DB-level uniqueness) or missing/inactive is always treated as unmatched,
    never guessed from tutor_name."""
    if not bud_tutor_id:
        return None
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", active FROM tutors WHERE external_system_id = %s',
        (str(bud_tutor_id),),
    )
    matches = cur.fetchall()
    if len(matches) != 1 or not matches[0]["active"]:
        return None
    return matches[0]


def _cohort_sync_key(tutor_id: int, start_date_value: date) -> str:
    return f"bud:{tutor_id}:{start_date_value.isoformat()}"


def _find_cohort_mapping(cur, sync_key: str) -> dict | None:
    cur.execute(
        'SELECT id, cohort_id AS "cohortId", bud_sync_key AS "budSyncKey" FROM bud_cohort_mapping WHERE bud_sync_key = %s',
        (sync_key,),
    )
    return cur.fetchone()


def _find_possible_manual_cohort(cur, tutor_id: int, start_date_value: date) -> dict | None:
    """A plausible-but-unconfirmed match to an existing, manually created
    cohort. Per the approved trial scope, this is always surfaced as a
    conflict requiring manual investigation outside this tool -- never an
    in-tool mapping picker this pass."""
    cur.execute(
        """
        SELECT c.id, c.name FROM cohorts c
        WHERE c.tutor_id = %s AND c.start_date = %s AND c.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM bud_cohort_mapping m WHERE m.cohort_id = c.id)
        """,
        (tutor_id, start_date_value),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Classification (preview)
# ---------------------------------------------------------------------------


def _diff_simple_fields(bud_row: dict, accepted_or_current: dict, current_uln: str | None) -> dict:
    """Field-level diff for the whitelisted Bud-owned simple fields, plus
    uln (only proposable when the internal value is currently null)."""
    diff: dict = {}
    for bud_key, internal_key in _SIMPLE_FIELD_MAP.items():
        before = accepted_or_current.get(internal_key)
        after = bud_row.get(bud_key)
        if after and after != before:
            diff[internal_key] = {"before": before, "after": after}
    if bud_row.get("uln") and not current_uln:
        diff["uln"] = {"before": None, "after": bud_row["uln"]}
    return diff


def classify_row(cur, bud_row: dict, baseline: dict, baseline_snapshot_ids: set[str] | None = None) -> dict:
    """Pure classification against current DB state -- never writes
    anything. Returns a dict shaped for insertion into bud_sync_item
    (minus id/sync_job_id, filled in by the caller).

    baseline_snapshot_ids is the set of learning_plan_ids captured in
    bud_sync_baseline_snapshot at baseline-establishment time -- pass it in
    (run_preview fetches it once per run) to avoid a query per row; if
    omitted, it's fetched here for a single-row call (e.g. tests/one-off
    checks). A row's *presence in that snapshot*, not its synced_at, is
    what answers "did this Bud record exist before the trial started":
    real Bud data bulk-touches synced_at across the whole table on every
    sync regardless of whether a row's data changed, so a pure timestamp
    cutoff would treat every pre-existing row as newly eligible again the
    very next time Bud syncs (confirmed against production data)."""
    plan_id = bud_row["learningPlanId"]
    if baseline_snapshot_ids is None:
        baseline_snapshot_ids = _get_baseline_snapshot_ids(cur, baseline["id"])
    is_post_baseline = plan_id not in baseline_snapshot_ids

    base_item = {
        "source_identifier": plan_id,
        "internal_learner_id": None,
        "proposed_values": {},
        "previous_values": {},
        "warnings": [],
        "reason": None,
    }

    link = _find_link_by_plan_id(cur, plan_id)

    if link is None and bud_row.get("uln"):
        matches = _find_learners_by_uln(cur, bud_row["uln"])
        if len(matches) > 1:
            return {**base_item, "match_status": "conflict", "action_type": "none",
                    "reason": "uln_matches_multiple_internal_learners"}
        if len(matches) == 1:
            existing_link_for_learner = _find_link_by_learner_id(cur, matches[0]["id"])
            if existing_link_for_learner and existing_link_for_learner["budLearningPlanId"] != plan_id:
                return {**base_item, "match_status": "conflict", "action_type": "none", "internal_learner_id": matches[0]["id"],
                        "reason": "learner_already_linked_to_a_different_bud_record"}
            if existing_link_for_learner is None:
                if not is_post_baseline:
                    return {**base_item, "match_status": "existing_before_trial", "action_type": "none",
                            "internal_learner_id": matches[0]["id"], "reason": "matched_but_observed_at_or_before_baseline"}
                link = None  # first-ever link will be established on approval; fall through to existing_update path
                base_item["internal_learner_id"] = matches[0]["id"]
            else:
                link = existing_link_for_learner
                base_item["internal_learner_id"] = matches[0]["id"]

    if link is not None:
        base_item["internal_learner_id"] = link["internalLearnerId"]
        if not (bud_row["syncedAt"] and link["acceptedSyncedAt"] and bud_row["syncedAt"] > link["acceptedSyncedAt"]) and link["acceptedSyncedAt"] is not None:
            return {**base_item, "match_status": "unchanged", "action_type": "none", "reason": "no_change_since_last_accepted_snapshot"}
        return _classify_existing_learner_update(cur, bud_row, base_item, link)

    if base_item["internal_learner_id"]:
        # ULN-matched, not yet linked, first post-baseline observation.
        return _classify_existing_learner_update(cur, bud_row, base_item, {"acceptedValues": {}, "acceptedSyncedAt": None})

    # No match at all.
    if not is_post_baseline:
        return {**base_item, "match_status": "existing_before_trial", "action_type": "none",
                "reason": "unmatched_but_observed_at_or_before_baseline"}
    return _classify_new_learner(cur, bud_row, base_item)


def _classify_new_learner(cur, bud_row: dict, base_item: dict) -> dict:
    warnings: list[str] = []
    tutor = _find_tutor_by_bud_id(cur, bud_row.get("budTutorId"))
    if tutor is None:
        return {**base_item, "match_status": "conflict", "action_type": "create_learner",
                "reason": "tutor_unmatched", "warnings": ["No active internal Tutor is linked to this Bud tutor_id."]}

    start_date_value = bud_row.get("startDate")
    if not start_date_value:
        return {**base_item, "match_status": "conflict", "action_type": "create_learner", "reason": "missing_start_date"}
    if not bud_row.get("programmeName"):
        return {**base_item, "match_status": "conflict", "action_type": "create_learner", "reason": "missing_programme"}

    cohort_action: dict
    sync_key = _cohort_sync_key(tutor["id"], start_date_value)
    mapping = _find_cohort_mapping(cur, sync_key)
    if mapping is not None:
        cohort_action = {"action": "reuse", "cohortId": mapping["cohortId"], "syncKey": sync_key}
    else:
        possible = _find_possible_manual_cohort(cur, tutor["id"], start_date_value)
        if possible is not None:
            return {**base_item, "match_status": "conflict", "action_type": "create_learner",
                    "reason": "possible_manual_cohort_match",
                    "warnings": [f"An existing cohort '{possible['name']}' (id {possible['id']}) has the same "
                                 "tutor and start date but is not mapped to Bud. Resolve manually before approving."]}
        cohort_action = {"action": "create", "syncKey": sync_key,
                         "deliveryDay": None, "sessionStartTime": None, "sessionEndTime": None}
        warnings.append("New cohort requires deliveryDay/sessionStartTime/sessionEndTime before this can be approved.")

    proposed_learner = {
        "learnerRef": None,
        "uln": bud_row.get("uln"),
        "firstName": bud_row.get("learnerForename"),
        "lastName": bud_row.get("learnerSurname"),
        "email": bud_row.get("learnerEmail"),
        "mobile": bud_row.get("learnerMobile"),
        "programme": bud_row.get("programmeName"),
        "level": None,
        "startDate": str(start_date_value),
    }
    warnings.append("learnerRef and level must be supplied before this can be approved.")

    immediate = start_date_value <= date.today()
    return {
        **base_item,
        "match_status": "new",
        "action_type": "create_learner",
        "proposed_values": {
            "learner": proposed_learner,
            "tutor": {"budTutorId": bud_row.get("budTutorId"), "internalTutorId": tutor["id"]},
            "cohort": cohort_action,
            "allocation": {"effectiveDate": str(start_date_value), "immediate": immediate},
        },
        "previous_values": {"budSyncedAt": str(bud_row["syncedAt"]) if bud_row.get("syncedAt") else None},
        "warnings": warnings,
        "reason": "new_bud_record_after_baseline",
    }


def _classify_existing_learner_update(cur, bud_row: dict, base_item: dict, link: dict) -> dict:
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", email, mobile, programme, uln, '
        'tutor_id AS "tutorId", cohort_id AS "cohortId", start_date AS "startDate", updated_at AS "updatedAt" '
        "FROM learners WHERE id = %s AND deleted_at IS NULL",
        (base_item["internal_learner_id"],),
    )
    learner = cur.fetchone()
    if not learner:
        return {**base_item, "match_status": "conflict", "action_type": "none", "reason": "matched_learner_no_longer_exists"}

    accepted = link.get("acceptedValues") or {}
    fields_diff = _diff_simple_fields(bud_row, accepted, learner["uln"])

    proposed: dict = {}
    warnings: list[str] = []
    if fields_diff:
        proposed["fields"] = fields_diff

    tutor_transfer = None
    if bud_row.get("budTutorId"):
        tutor = _find_tutor_by_bud_id(cur, bud_row["budTutorId"])
        if tutor is not None and tutor["id"] != learner["tutorId"]:
            tutor_transfer = {"budTutorId": bud_row["budTutorId"], "internalTutorId": tutor["id"],
                              "currentTutorId": learner["tutorId"]}
        elif tutor is None and accepted.get("budTutorId") != bud_row["budTutorId"]:
            warnings.append("Bud tutor_id changed but no active internal Tutor matches it -- tutor not transferred.")
    if tutor_transfer:
        proposed["tutorTransfer"] = tutor_transfer

    start_date_change = None
    new_start = bud_row.get("startDate")
    if new_start and str(new_start) != str(learner["startDate"]) and str(new_start) != str(accepted.get("startDate")):
        start_date_change = {
            "oldStartDate": str(learner["startDate"]),
            "newStartDate": str(new_start),
            "currentCohortId": learner["cohortId"],
        }
        warnings.append(
            "Start date changed -- the learner.startDate field will be updated, but automatic cohort "
            "re-grouping based on a start-date change is not implemented this trial; move the learner's "
            "cohort manually via Allocation if needed."
        )
    if start_date_change:
        proposed["startDateChange"] = start_date_change

    if bud_row.get("statusDesc") and bud_row["statusDesc"] != accepted.get("statusDesc"):
        warnings.append(f"Bud status_desc changed ('{accepted.get('statusDesc')}' -> '{bud_row['statusDesc']}') -- "
                         "review manually; not auto-mapped to an internal status this trial.")

    if not proposed and not warnings:
        return {**base_item, "match_status": "unchanged", "action_type": "none", "reason": "no_proposable_change"}

    action_type = "update_learner"
    if tutor_transfer:
        action_type = "transfer_tutor"
    elif start_date_change:
        action_type = "change_start_date"

    return {
        **base_item,
        "match_status": "existing_update",
        "action_type": action_type,
        "proposed_values": proposed,
        "previous_values": {
            "fields": {k: learner.get(k) for k in fields_diff} if fields_diff else {},
            "updatedAt": str(learner["updatedAt"]),
            "budSyncedAt": str(bud_row["syncedAt"]) if bud_row.get("syncedAt") else None,
        },
        "warnings": warnings,
        "reason": "post_baseline_bud_change",
    }


# ---------------------------------------------------------------------------
# Preview orchestration
# ---------------------------------------------------------------------------

_ITEM_INSERT_SQL = """
    INSERT INTO bud_sync_item
        (sync_job_id, source_identifier, match_status, action_type, internal_learner_id,
         proposed_values, previous_values, warnings, reason,
         source_learner_reference, source_first_name, source_last_name)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def run_preview(cur, request: Request, session: dict) -> dict:
    baseline = get_active_baseline(cur)
    if baseline is None:
        raise HTTPException(status_code=409, detail="No active trial baseline. Establish one before previewing.")

    bud_rows = _fetch_bud_rows(cur)
    baseline_snapshot_ids = _get_baseline_snapshot_ids(cur, baseline["id"])

    cur.execute(
        """
        INSERT INTO bud_sync_job (baseline_id, started_by, source_max_synced_at, total_source_rows_examined, correlation_id)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (baseline["id"], session["userId"], baseline["sourceMaxSyncedAt"], len(bud_rows), get_correlation_id() or None),
    )
    job_id = cur.fetchone()["id"]

    counts = {"new": 0, "existing_update": 0, "unchanged": 0, "conflict": 0, "existing_before_trial": 0, "skipped": 0}
    action_counts = {"cohorts_proposed": 0, "allocations_proposed": 0, "transfers_proposed": 0}
    for bud_row in bud_rows:
        item = classify_row(cur, bud_row, baseline, baseline_snapshot_ids)
        counts[item["match_status"]] += 1
        if item["action_type"] == "create_learner":
            action_counts["cohorts_proposed"] += 1 if item["proposed_values"].get("cohort", {}).get("action") == "create" else 0
            action_counts["allocations_proposed"] += 1
        if item["action_type"] == "transfer_tutor":
            action_counts["transfers_proposed"] += 1

        cur.execute(
            _ITEM_INSERT_SQL,
            (
                job_id, item["source_identifier"], item["match_status"], item["action_type"],
                item["internal_learner_id"], json.dumps(item["proposed_values"], default=str),
                json.dumps(item["previous_values"], default=str), json.dumps(item["warnings"]), item["reason"],
                bud_row.get("learnerReference"), bud_row.get("learnerForename"), bud_row.get("learnerSurname"),
            ),
        )

    cur.execute(
        """
        UPDATE bud_sync_job SET
            new_learners_detected = %s, learner_updates_detected = %s,
            cohorts_proposed = %s, allocations_proposed = %s, transfers_proposed = %s,
            conflict_count = %s, skipped_count = %s
        WHERE id = %s
        """,
        (counts["new"], counts["existing_update"], action_counts["cohorts_proposed"],
         action_counts["allocations_proposed"], action_counts["transfers_proposed"],
         counts["conflict"], counts["existing_before_trial"], job_id),
    )

    write_audit_log(
        request, action="bud_sync_preview_created", entity_type="bud_sync_job", entity_id=job_id,
        new_value={"baselineId": baseline["id"], "counts": counts, **action_counts}, cur=cur,
    )
    return get_job(cur, job_id)


def get_job(cur, job_id: int) -> dict:
    cur.execute(
        """
        SELECT id, baseline_id AS "baselineId", status, started_at AS "startedAt", completed_at AS "completedAt",
               started_by AS "startedBy", source_max_synced_at AS "sourceMaxSyncedAt",
               total_source_rows_examined AS "totalSourceRowsExamined",
               new_learners_detected AS "newLearnersDetected", learner_updates_detected AS "learnerUpdatesDetected",
               cohorts_proposed AS "cohortsProposed", allocations_proposed AS "allocationsProposed",
               transfers_proposed AS "transfersProposed", approved_count AS "approvedCount",
               applied_count AS "appliedCount", skipped_count AS "skippedCount", conflict_count AS "conflictCount",
               error_count AS "errorCount", approval_reason AS "approvalReason", correlation_id AS "correlationId",
               error_summary AS "errorSummary"
        FROM bud_sync_job WHERE id = %s
        """,
        (job_id,),
    )
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return job


def list_jobs(cur, page: int, page_size: int) -> dict:
    cur.execute("SELECT count(*)::int AS count FROM bud_sync_job")
    total = cur.fetchone()["count"]
    cur.execute(
        """
        SELECT id, baseline_id AS "baselineId", status, started_at AS "startedAt", completed_at AS "completedAt",
               new_learners_detected AS "newLearnersDetected", learner_updates_detected AS "learnerUpdatesDetected",
               conflict_count AS "conflictCount", applied_count AS "appliedCount"
        FROM bud_sync_job ORDER BY started_at DESC LIMIT %s OFFSET %s
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

    cur.execute(f"SELECT count(*)::int AS count FROM bud_sync_item WHERE {where}", params)
    total = cur.fetchone()["count"]
    cur.execute(
        f"""
        SELECT id, sync_job_id AS "syncJobId", source_identifier AS "sourceIdentifier", match_status AS "matchStatus",
               action_type AS "actionType", internal_learner_id AS "internalLearnerId",
               proposed_values AS "proposedValues", previous_values AS "previousValues", warnings, reason,
               approved, applied, outcome, error_code AS "errorCode", processed_at AS "processedAt",
               source_learner_reference AS "sourceLearnerReference",
               source_first_name AS "sourceFirstName", source_last_name AS "sourceLastName"
        FROM bud_sync_item WHERE {where} ORDER BY id LIMIT %s OFFSET %s
        """,
        [*params, page_size, (page - 1) * page_size],
    )
    return {"items": cur.fetchall(), "total": total, "page": page, "pageSize": page_size}


def get_unmatched_pre_baseline(cur, page: int, page_size: int) -> dict:
    """Read-only Administrator-awareness list -- never processed, per the
    trial's core rule against historical backfill."""
    cur.execute(
        """
        SELECT count(*)::int AS count FROM bud_sync_item
        WHERE match_status = 'existing_before_trial' AND sync_job_id = (SELECT max(id) FROM bud_sync_job)
        """
    )
    total = cur.fetchone()["count"]
    cur.execute(
        """
        SELECT source_identifier AS "sourceIdentifier", internal_learner_id AS "internalLearnerId", reason,
               source_learner_reference AS "sourceLearnerReference",
               source_first_name AS "sourceFirstName", source_last_name AS "sourceLastName"
        FROM bud_sync_item
        WHERE match_status = 'existing_before_trial' AND sync_job_id = (SELECT max(id) FROM bud_sync_job)
        ORDER BY id LIMIT %s OFFSET %s
        """,
        (page_size, (page - 1) * page_size),
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
    cohort_values = item["proposedValues"].get("cohort", {})
    if cohort_values.get("action") == "create":
        for field in _REQUIRED_COHORT_FIELDS:
            if not cohort_values.get(field):
                missing.append(f"cohort.{field}")
    return missing


def update_item(cur, job_id: int, item_id: int, field_updates: dict | None, approved: bool | None) -> dict:
    cur.execute("SELECT status FROM bud_sync_job WHERE id = %s", (job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Job is not in a reviewable state (status={job['status']})")

    cur.execute(
        """
        SELECT id, sync_job_id AS "syncJobId", match_status AS "matchStatus", action_type AS "actionType",
               proposed_values AS "proposedValues", approved
        FROM bud_sync_item WHERE id = %s AND sync_job_id = %s
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
        cur.execute("UPDATE bud_sync_item SET proposed_values = %s WHERE id = %s", (json.dumps(proposed, default=str), item_id))

    if approved is not None:
        if approved:
            if item["matchStatus"] == "conflict":
                raise HTTPException(status_code=400, detail="Conflicted items cannot be approved")
            missing = _item_missing_fields({**item, "proposedValues": proposed})
            if missing:
                raise HTTPException(status_code=400, detail=f"Required fields missing before approval: {', '.join(missing)}")
        cur.execute("UPDATE bud_sync_item SET approved = %s WHERE id = %s", (approved, item_id))

    cur.execute(
        """
        SELECT id, sync_job_id AS "syncJobId", source_identifier AS "sourceIdentifier", match_status AS "matchStatus",
               action_type AS "actionType", internal_learner_id AS "internalLearnerId",
               proposed_values AS "proposedValues", previous_values AS "previousValues", warnings, reason,
               approved, applied, outcome, error_code AS "errorCode", processed_at AS "processedAt",
               source_learner_reference AS "sourceLearnerReference",
               source_first_name AS "sourceFirstName", source_last_name AS "sourceLastName"
        FROM bud_sync_item WHERE id = %s
        """,
        (item_id,),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

_LIMIT_COLUMNS = {
    "learnerCreations": "bud_sync_max_learner_creations",
    "learnerUpdates": "bud_sync_max_learner_updates",
    "cohortCreations": "bud_sync_max_cohort_creations",
    "tutorTransfers": "bud_sync_max_tutor_transfers",
}


def _get_trial_limits(cur) -> dict:
    cur.execute(
        "SELECT bud_sync_max_learner_creations, bud_sync_max_learner_updates, "
        "bud_sync_max_cohort_creations, bud_sync_max_tutor_transfers FROM app_settings WHERE id = 1"
    )
    row = cur.fetchone()
    return {
        "learnerCreations": row["bud_sync_max_learner_creations"],
        "learnerUpdates": row["bud_sync_max_learner_updates"],
        "cohortCreations": row["bud_sync_max_cohort_creations"],
        "tutorTransfers": row["bud_sync_max_tutor_transfers"],
    }


def _count_batch_actions(items: list[dict]) -> dict:
    counts = {"learnerCreations": 0, "learnerUpdates": 0, "cohortCreations": 0, "tutorTransfers": 0}
    for item in items:
        if item["actionType"] == "create_learner":
            counts["learnerCreations"] += 1
            if item["proposedValues"].get("cohort", {}).get("action") == "create":
                counts["cohortCreations"] += 1
        elif item["actionType"] in ("update_learner", "change_start_date"):
            counts["learnerUpdates"] += 1
        elif item["actionType"] == "transfer_tutor":
            counts["tutorTransfers"] += 1
            counts["learnerUpdates"] += 1
    return counts


def _build_accepted_values(bud_row: dict) -> dict:
    values = {internal_key: bud_row.get(bud_key) for bud_key, internal_key in _SIMPLE_FIELD_MAP.items()}
    values["budTutorId"] = bud_row.get("budTutorId")
    values["statusDesc"] = bud_row.get("statusDesc")
    values["startDate"] = str(bud_row["startDate"]) if bud_row.get("startDate") else None
    return values


def _upsert_learner_link(cur, learner_id: int, bud_row: dict, job_id: int) -> None:
    cur.execute(
        """
        INSERT INTO bud_learner_link
            (internal_learner_id, bud_learning_plan_id, bud_apprentice_id, bud_uln,
             accepted_synced_at, accepted_values, last_sync_job_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (internal_learner_id) DO UPDATE SET
            bud_learning_plan_id = EXCLUDED.bud_learning_plan_id,
            bud_apprentice_id = EXCLUDED.bud_apprentice_id,
            bud_uln = EXCLUDED.bud_uln,
            accepted_synced_at = EXCLUDED.accepted_synced_at,
            accepted_values = EXCLUDED.accepted_values,
            last_sync_job_id = EXCLUDED.last_sync_job_id,
            updated_at = now()
        """,
        (
            learner_id, bud_row["learningPlanId"], bud_row.get("apprenticeId"), bud_row.get("uln"),
            bud_row.get("syncedAt"), json.dumps(_build_accepted_values(bud_row), default=str), job_id,
        ),
    )


def _apply_new_learner(cur, item: dict, request: Request, session: dict) -> int:
    from .routers.cohorts import CohortInput, _create_cohort
    from .routers.learners import LearnerInput, _create_learner

    proposed = item["proposedValues"]
    learner_values = proposed["learner"]
    cohort_action = proposed["cohort"]
    tutor_internal_id = proposed["tutor"]["internalTutorId"]

    # Re-check for a mapping right before creating -- two items in the same
    # preview can share the same deterministic key when neither had a
    # mapping yet at preview time; whichever is applied first in this batch
    # creates it, and every later one must reuse it rather than attempt a
    # second INSERT that would collide with bud_cohort_mapping's unique key.
    existing_mapping = _find_cohort_mapping(cur, cohort_action["syncKey"])
    if existing_mapping is not None:
        cohort_id = existing_mapping["cohortId"]
    elif cohort_action["action"] == "create":
        cur.execute('SELECT first_name AS "firstName", last_name AS "lastName" FROM tutors WHERE id = %s', (tutor_internal_id,))
        tutor = cur.fetchone()
        cohort_payload = CohortInput(
            name=f"{tutor['firstName']} {tutor['lastName']} — {learner_values['startDate']}",
            programme=learner_values["programme"],
            level=learner_values["level"],
            tutorId=tutor_internal_id,
            deliveryDay=cohort_action["deliveryDay"],
            sessionStartTime=cohort_action["sessionStartTime"],
            sessionEndTime=cohort_action["sessionEndTime"],
            startDate=learner_values["startDate"],
            externalSystemId=cohort_action["syncKey"],
        )
        cohort = _create_cohort(cur, cohort_payload, request, session)
        cohort_id = cohort["id"]
        cur.execute(
            "INSERT INTO bud_cohort_mapping (cohort_id, bud_sync_key, created_by) VALUES (%s, %s, %s)",
            (cohort_id, cohort_action["syncKey"], session["userId"]),
        )
    else:
        cohort_id = cohort_action["cohortId"]

    learner_payload = LearnerInput(
        learnerRef=learner_values["learnerRef"],
        uln=learner_values.get("uln"),
        firstName=learner_values["firstName"],
        lastName=learner_values["lastName"],
        email=learner_values.get("email"),
        mobile=learner_values.get("mobile"),
        programme=learner_values["programme"],
        level=learner_values["level"],
        startDate=learner_values["startDate"],
    )
    created = _create_learner(cur, learner_payload, request, session)

    effective_date = date.fromisoformat(learner_values["startDate"])
    if proposed["allocation"]["immediate"]:
        apply_transfer(
            cur, {"id": created["id"], "tutorId": None, "cohortId": None},
            tutor_internal_id, cohort_id, effective_date, "Bud sync trial - new learner", session["userId"],
        )
    else:
        cur.execute(
            """
            INSERT INTO scheduled_allocations (learner_id, new_tutor_id, new_cohort_id, effective_date, transfer_reason, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (created["id"], tutor_internal_id, cohort_id, effective_date, "Bud sync trial - new learner", session["userId"]),
        )

    return created["id"]


def _apply_existing_learner_update(cur, item: dict, request: Request, session: dict) -> int:
    from .routers.learners import LearnerUpdate, _update_learner

    learner_id = item["internalLearnerId"]
    proposed = item["proposedValues"]

    update_kwargs = dict(proposed.get("fields", {}))
    update_kwargs = {k: v["after"] for k, v in update_kwargs.items()}
    if "startDateChange" in proposed:
        update_kwargs["startDate"] = proposed["startDateChange"]["newStartDate"]
    if update_kwargs:
        _update_learner(cur, learner_id, LearnerUpdate(**update_kwargs), request, session)

    if "tutorTransfer" in proposed:
        cur.execute(
            'SELECT id, tutor_id AS "tutorId", cohort_id AS "cohortId" FROM learners WHERE id = %s', (learner_id,)
        )
        learner = cur.fetchone()
        apply_transfer(
            cur, learner, proposed["tutorTransfer"]["internalTutorId"], learner["cohortId"],
            date.today(), "Bud sync trial - tutor transfer", session["userId"],
        )

    return learner_id


def run_commit(cur, job_id: int, item_ids: list[int], approval_reason: str, limit_override_reason: str | None,
                request: Request, session: dict) -> dict:
    job = get_job(cur, job_id)
    if job["status"] == "completed":
        return job  # idempotent replay -- no reapplication

    cur.execute("UPDATE bud_sync_job SET status = 'committing' WHERE id = %s AND status = 'ready'", (job_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail=f"Job is not ready to commit (status={job['status']})")

    try:
        active_baseline = get_active_baseline(cur)
        if active_baseline is None or active_baseline["id"] != job["baselineId"]:
            raise HTTPException(
                status_code=409,
                detail="The trial baseline has changed since this preview was generated. Generate a new preview.",
            )

        cur.execute(
            """
            SELECT id, sync_job_id AS "syncJobId", source_identifier AS "sourceIdentifier", match_status AS "matchStatus",
                   action_type AS "actionType", internal_learner_id AS "internalLearnerId",
                   proposed_values AS "proposedValues", previous_values AS "previousValues"
            FROM bud_sync_item WHERE id = ANY(%s) AND sync_job_id = %s
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

        # Staleness: re-read just the selected items' Bud source rows and
        # internal learner rows; anything that moved since preview is
        # excluded from this batch rather than blindly applied. Source
        # staleness is detected via synced_at (Bud's own "this row changed"
        # marker, captured at preview time in previous_values.budSyncedAt);
        # internal staleness via learners.updated_at, mirroring the
        # register-version optimistic-concurrency pattern.
        surviving: list[dict] = []
        stale_source_ids: list[int] = []
        stale_internal_ids: list[int] = []
        fresh_bud_rows_by_plan_id: dict[str, dict] = {r["learningPlanId"]: r for r in _fetch_bud_rows(cur)}
        for item in items:
            previous = item["previousValues"] or {}
            fresh_row = fresh_bud_rows_by_plan_id.get(item["sourceIdentifier"])
            previous_synced_at = previous.get("budSyncedAt")
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
                "UPDATE bud_sync_item SET outcome = %s, processed_at = now() WHERE id = %s",
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

        # Every business write for this batch -- the limit-override audit,
        # each item's create/update dispatch, its accepted-snapshot upsert,
        # its per-item audit row, and the job's own completion -- shares one
        # transaction. A failure partway through rolls back everything in
        # this block (autocommit=True means nothing here is atomic on its
        # own; this is what actually gives the "whole batch or nothing"
        # guarantee, not merely the try/except around it).
        with cur.connection.transaction():
            if over_limit and limit_override_reason:
                write_audit_log(
                    request, action="bud_sync_commit_limit_override", entity_type="bud_sync_job", entity_id=job_id,
                    new_value={"overLimit": over_limit, "reason": limit_override_reason}, cur=cur,
                )

            applied_count = 0
            for item in surviving:
                fresh_row = fresh_bud_rows_by_plan_id.get(item["sourceIdentifier"])
                if item["actionType"] == "create_learner":
                    learner_id = _apply_new_learner(cur, item, request, session)
                elif item["actionType"] in ("update_learner", "transfer_tutor", "change_start_date"):
                    learner_id = _apply_existing_learner_update(cur, item, request, session)
                else:
                    cur.execute(
                        "UPDATE bud_sync_item SET approved = true, applied = true, outcome = 'skipped_no_action', processed_at = now() WHERE id = %s",
                        (item["id"],),
                    )
                    continue

                if fresh_row:
                    _upsert_learner_link(cur, learner_id, fresh_row, job_id)
                cur.execute(
                    "UPDATE bud_sync_item SET approved = true, applied = true, outcome = 'applied', "
                    "internal_learner_id = %s, processed_at = now() WHERE id = %s",
                    (learner_id, item["id"]),
                )
                write_audit_log(
                    request, action="bud_sync_item_applied", entity_type="bud_sync_item", entity_id=item["id"],
                    new_value={"actionType": item["actionType"], "learnerId": learner_id}, cur=cur,
                )
                applied_count += 1

            cur.execute(
                """
                UPDATE bud_sync_job SET status = 'completed', completed_at = now(), approval_reason = %s,
                    approved_count = %s, applied_count = %s, skipped_count = skipped_count + %s
                WHERE id = %s
                """,
                (approval_reason, len(surviving), applied_count,
                 len(stale_source_ids) + len(stale_internal_ids), job_id),
            )
    except Exception as exc:
        cur.execute(
            "UPDATE bud_sync_job SET status = 'failed', error_summary = %s WHERE id = %s",
            (str(exc.detail) if isinstance(exc, HTTPException) else str(exc), job_id),
        )
        write_audit_log(
            request, action="bud_sync_commit_failed", entity_type="bud_sync_job", entity_id=job_id,
            new_value={"error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc)},
        )
        raise

    write_audit_log(
        request, action="bud_sync_commit_completed", entity_type="bud_sync_job", entity_id=job_id,
        new_value={"appliedCount": applied_count, "approvalReason": approval_reason}, cur=cur,
    )
    return get_job(cur, job_id)
