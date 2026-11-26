"""Bud Functional Skills requirement sync -- a second, real-data-driven
source for learner_fs_requirements, alongside the existing manual CSV
upload (fs_requirement_lib.py). Administrator-only, preview-then-commit
workflow mirroring bud_sync_lib.py/aptem_sync_lib.py's shape, but simpler:
this never creates learners/cohorts/tutors, only proposes a change to an
already-linked learner's maths/english requirement flags, so there is no
identity-matching baseline to establish -- preview runs directly against
bud_functional_skill_progress's current contents each time.

bud_functional_skill_progress is owned entirely by an external process (a
standalone, developer-run staging script, not this app) -- every query here
is a plain SELECT, and this module must never write to it.

Source shape, confirmed via direct Fabric query: one row per
(bud_learning_plan_id, functional_skill), only 4 distinct functional_skill
values exist ("English - Level 1/2", "Maths - Level 1/2"), and every plan
present has either exactly 2 rows (one subject's L1+L2 pair) or exactly 4
(both subjects) -- never an odd count. ABSENCE of a plan from this table
means "not tracked", NEVER "confirmed no requirement" -- run_preview only
ever iterates plans actually present in the mirror table, and must never be
changed to synthesise an absence-based proposal (e.g. auto-clearing a
learner whose plan simply isn't in this feed).

Required-flag derivation: a subject is still-required only if at least one
of its rows has a status that is NOT 'complete' and NOT 'exempt' -- see
derive_subject_requirement. This matches what the allocation view actually
alerts on (missing live cohort coverage), not literal presence of an aim.

Cleared is sticky: if an admin explicitly cleared a learner's requirement
(learner_fs_requirements.status = 'cleared'), this sync NEVER proposes
reinstating it -- that plan classifies as 'blocked_by_clear', a distinct,
visible outcome, not silently merged into 'unchanged'. Only a manual
admin re-record can supersede an explicit clear.
"""
from __future__ import annotations

import json

from fastapi import HTTPException, Request

from . import bud_sync_lib, fs_requirement_lib
from .audit import write_audit_log
from .correlation import get_correlation_id

# Statuses that mean "no longer needs active tuition" -- anything else
# (in-progress percentage, 0%, or unknown/null) counts as still-required.
_INACTIVE_STATUS_CATEGORIES = {"complete", "exempt"}

_JOB_SELECT = """
    SELECT id, status, started_at AS "startedAt", completed_at AS "completedAt", started_by AS "startedBy",
           source_max_synced_at AS "sourceMaxSyncedAt", total_plans_examined AS "totalPlansExamined",
           new_count AS "newCount", changed_count AS "changedCount", unchanged_count AS "unchangedCount",
           unmatched_count AS "unmatchedCount", blocked_by_clear_count AS "blockedByClearCount",
           approved_count AS "approvedCount", applied_count AS "appliedCount", skipped_count AS "skippedCount",
           approval_reason AS "approvalReason", correlation_id AS "correlationId", error_summary AS "errorSummary"
    FROM fs_bud_sync_job
"""

_ITEM_SELECT = """
    SELECT i.id, i.sync_job_id AS "syncJobId", i.bud_learning_plan_id AS "budLearningPlanId",
           i.internal_learner_id AS "internalLearnerId", i.match_status AS "matchStatus",
           i.proposed_maths AS "proposedMaths", i.proposed_english AS "proposedEnglish",
           i.existing_maths AS "existingMaths", i.existing_english AS "existingEnglish",
           i.existing_status AS "existingStatus", i.existing_source AS "existingSource",
           i.source_subject_rows AS "sourceSubjectRows", i.warnings,
           i.approved, i.applied, i.outcome, i.processed_at AS "processedAt", i.created_at AS "createdAt",
           CASE WHEN l.id IS NOT NULL THEN concat(l.first_name, ' ', l.last_name) END AS "learnerName",
           l.learner_ref AS "learnerRef", l.status AS "learnerStatus"
    FROM fs_bud_sync_item i
    LEFT JOIN learners l ON l.id = i.internal_learner_id
"""


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def get_source_status(cur) -> dict:
    """Read-only snapshot used by the trial's status card. Matched/unmatched
    are counted the same way classify_plan resolves a match: an existing
    bud_learner_link row whose internal learner is not soft-deleted."""
    cur.execute(
        'SELECT count(DISTINCT bud_learning_plan_id)::int AS "planCount", max(synced_at) AS "maxSyncedAt" '
        "FROM bud_functional_skill_progress"
    )
    source = cur.fetchone()

    matched_count = 0
    unmatched_count = 0
    if source["planCount"]:
        cur.execute(
            """
            SELECT count(DISTINCT p.bud_learning_plan_id)::int AS count
            FROM bud_functional_skill_progress p
            JOIN bud_learner_link link ON link.bud_learning_plan_id = p.bud_learning_plan_id
            JOIN learners l ON l.id = link.internal_learner_id AND l.deleted_at IS NULL
            """
        )
        matched_count = cur.fetchone()["count"]
        unmatched_count = source["planCount"] - matched_count

    cur.execute(
        "SELECT completed_at AS \"completedAt\" FROM fs_bud_sync_job WHERE status = 'completed' "
        "ORDER BY completed_at DESC LIMIT 1"
    )
    last_commit = cur.fetchone()

    return {
        "sourceMaxSyncedAt": source["maxSyncedAt"],
        "sourcePlanCount": source["planCount"],
        "matchedPlanCount": matched_count,
        "unmatchedPlanCount": unmatched_count,
        "lastSuccessfulSyncAt": last_commit["completedAt"] if last_commit else None,
    }


# ---------------------------------------------------------------------------
# Source read + classification
# ---------------------------------------------------------------------------


def _fetch_progress_rows(cur) -> list[dict]:
    cur.execute(
        """
        SELECT bud_learning_plan_id AS "budLearningPlanId", functional_skill AS "functionalSkill",
               subject, level, functional_skill_status AS "functionalSkillStatus",
               status_category AS "statusCategory", progress_ratio AS "progressRatio", synced_at AS "syncedAt"
        FROM bud_functional_skill_progress
        """
    )
    return cur.fetchall()


def derive_subject_requirement(rows_for_subject: list[dict]) -> tuple[bool, list[str]]:
    """Given the rows for one subject (normally exactly 2: Level 1 + Level
    2), returns (still_required, warnings). A subject counts as
    still-required if ANY of its rows has a status category other than
    'complete'/'exempt' -- in-progress percentage, 0%, or unknown/null all
    count. Defensive: today every subject present always has exactly 2
    rows -- never assume that holds forever."""
    warnings: list[str] = []
    if not rows_for_subject:
        return False, warnings

    if len(rows_for_subject) != 2:
        warnings.append(
            f"Unexpected: {len(rows_for_subject)} row(s) reported for this subject (expected exactly 2, one per "
            "level) -- using the most pessimistic (still-required) interpretation."
        )

    if any(r["statusCategory"] == "unknown" for r in rows_for_subject):
        warnings.append(
            "Bud reports an unrecognised/blank status for one or more levels of this subject -- treated as "
            "still-required pending clarification."
        )

    still_required = any(r["statusCategory"] not in _INACTIVE_STATUS_CATEGORIES for r in rows_for_subject)
    return still_required, warnings


def classify_plan(cur, learning_plan_id: str, rows_for_plan: list[dict]) -> dict:
    """Pure classification against current DB state -- never writes
    anything. Returns a dict shaped for insertion into fs_bud_sync_item."""
    base_item: dict = {
        "bud_learning_plan_id": learning_plan_id,
        "internal_learner_id": None,
        "proposed_maths": None,
        "proposed_english": None,
        "existing_maths": None,
        "existing_english": None,
        "existing_status": None,
        "existing_source": None,
        "source_subject_rows": [
            {
                "subject": r["subject"], "level": r["level"],
                "status": r["functionalSkillStatus"], "statusCategory": r["statusCategory"],
            }
            for r in rows_for_plan
        ],
        "warnings": [],
    }

    link = bud_sync_lib._find_link_by_plan_id(cur, learning_plan_id)
    if link is None:
        return {**base_item, "match_status": "unmatched"}

    learner_id = link["internalLearnerId"]
    cur.execute("SELECT id FROM learners WHERE id = %s AND deleted_at IS NULL", (learner_id,))
    if not cur.fetchone():
        return {**base_item, "match_status": "unmatched"}

    base_item["internal_learner_id"] = learner_id

    maths_rows = [r for r in rows_for_plan if r["subject"] == "math"]
    english_rows = [r for r in rows_for_plan if r["subject"] == "english"]
    proposed_maths, maths_warnings = derive_subject_requirement(maths_rows)
    proposed_english, english_warnings = derive_subject_requirement(english_rows)
    base_item["proposed_maths"] = proposed_maths
    base_item["proposed_english"] = proposed_english
    base_item["warnings"] = maths_warnings + english_warnings

    cur.execute(f"{fs_requirement_lib.REQUIREMENT_SELECT} WHERE learner_id = %s", (learner_id,))
    existing = cur.fetchone()

    if existing is not None:
        base_item["existing_maths"] = existing["maths"]
        base_item["existing_english"] = existing["english"]
        base_item["existing_status"] = existing["status"]
        base_item["existing_source"] = existing["source"]

        if existing["status"] == "cleared":
            return {**base_item, "match_status": "blocked_by_clear"}

        if existing["source"] == "manual_upload" and (
            existing["maths"] != proposed_maths or existing["english"] != proposed_english
        ):
            base_item["warnings"].append(
                "This will overwrite an existing manually-uploaded requirement for this learner."
            )

        if existing["status"] == "recorded" and existing["maths"] == proposed_maths and existing["english"] == proposed_english:
            match_status = "unchanged"
        else:
            match_status = "changed"
    else:
        match_status = "new"

    return {**base_item, "match_status": match_status}


# ---------------------------------------------------------------------------
# Preview orchestration
# ---------------------------------------------------------------------------

_ITEM_INSERT_SQL = """
    INSERT INTO fs_bud_sync_item
        (sync_job_id, bud_learning_plan_id, internal_learner_id, match_status,
         proposed_maths, proposed_english, existing_maths, existing_english,
         existing_status, existing_source, source_subject_rows, warnings, approved)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false)
"""


def run_preview(cur, request: Request, session: dict) -> dict:
    """Mirrors bud_sync_lib.run_preview's transaction/failure discipline:
    the job row survives a mid-preview exception (created before the try
    block), flipped to 'failed' with error_summary rather than left
    dangling in 'generating'."""
    cur.execute('SELECT max(synced_at) AS "maxSyncedAt" FROM bud_functional_skill_progress')
    source_max_synced_at = cur.fetchone()["maxSyncedAt"]

    cur.execute(
        """
        INSERT INTO fs_bud_sync_job (started_by, source_max_synced_at, status, correlation_id)
        VALUES (%s, %s, 'generating', %s) RETURNING id
        """,
        (session["userId"], source_max_synced_at, get_correlation_id() or None),
    )
    job_id = cur.fetchone()["id"]

    try:
        rows = _fetch_progress_rows(cur)
        by_plan: dict[str, list[dict]] = {}
        for row in rows:
            by_plan.setdefault(row["budLearningPlanId"], []).append(row)

        counts = {"new": 0, "changed": 0, "unchanged": 0, "unmatched": 0, "blocked_by_clear": 0}

        with cur.connection.transaction():
            for plan_id, plan_rows in by_plan.items():
                item = classify_plan(cur, plan_id, plan_rows)
                counts[item["match_status"]] += 1
                cur.execute(
                    _ITEM_INSERT_SQL,
                    (
                        job_id, plan_id, item["internal_learner_id"], item["match_status"],
                        item["proposed_maths"], item["proposed_english"],
                        item["existing_maths"], item["existing_english"],
                        item["existing_status"], item["existing_source"],
                        json.dumps(item["source_subject_rows"], default=str), json.dumps(item["warnings"]),
                    ),
                )

            cur.execute(
                """
                UPDATE fs_bud_sync_job SET
                    status = 'ready', total_plans_examined = %s,
                    new_count = %s, changed_count = %s, unchanged_count = %s,
                    unmatched_count = %s, blocked_by_clear_count = %s
                WHERE id = %s
                """,
                (
                    len(by_plan), counts["new"], counts["changed"], counts["unchanged"],
                    counts["unmatched"], counts["blocked_by_clear"], job_id,
                ),
            )
    except Exception as exc:
        cur.execute(
            "UPDATE fs_bud_sync_job SET status = 'failed', error_summary = %s WHERE id = %s",
            (str(exc.detail) if isinstance(exc, HTTPException) else str(exc), job_id),
        )
        write_audit_log(
            request, action="fs_bud_sync_preview_failed", entity_type="fs_bud_sync_job", entity_id=job_id,
            new_value={"error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc)},
        )
        raise

    write_audit_log(
        request, action="fs_bud_sync_preview_created", entity_type="fs_bud_sync_job", entity_id=job_id,
        new_value={"counts": counts}, cur=cur,
    )
    return get_job(cur, job_id)


def get_job(cur, job_id: int) -> dict:
    cur.execute(f"{_JOB_SELECT} WHERE id = %s", (job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return job


def list_jobs(cur, page: int, page_size: int) -> dict:
    cur.execute("SELECT count(*)::int AS count FROM fs_bud_sync_job")
    total = cur.fetchone()["count"]
    cur.execute(f"{_JOB_SELECT} ORDER BY started_at DESC LIMIT %s OFFSET %s", (page_size, (page - 1) * page_size))
    return {"items": cur.fetchall(), "total": total, "page": page, "pageSize": page_size}


def get_job_summary(cur, job_id: int) -> dict:
    cur.execute(
        """
        SELECT match_status, count(*)::int AS count
        FROM fs_bud_sync_item WHERE sync_job_id = %s AND applied = false
        GROUP BY match_status
        """,
        (job_id,),
    )
    open_counts = {row["match_status"]: row["count"] for row in cur.fetchall()}

    cur.execute(
        "SELECT completed_at AS \"completedAt\" FROM fs_bud_sync_job WHERE status = 'completed' "
        "ORDER BY completed_at DESC LIMIT 1"
    )
    last_commit = cur.fetchone()

    return {
        "newCount": open_counts.get("new", 0),
        "changedCount": open_counts.get("changed", 0),
        "unmatchedCount": open_counts.get("unmatched", 0),
        "blockedByClearCount": open_counts.get("blocked_by_clear", 0),
        "lastSuccessfulSyncAt": last_commit["completedAt"] if last_commit else None,
    }


def list_items(cur, job_id: int, match_status: str | None, page: int, page_size: int) -> dict:
    clauses = ["i.sync_job_id = %s"]
    params: list = [job_id]
    if match_status:
        clauses.append("i.match_status = %s")
        params.append(match_status)
    where = " AND ".join(clauses)

    cur.execute(f"SELECT count(*)::int AS count FROM fs_bud_sync_item i WHERE {where}", params)
    total = cur.fetchone()["count"]

    # Matched learner name/ref/status are looked up live (not persisted on
    # the item), so the review always reflects the learner's CURRENT state --
    # mirrors fs_requirement_lib.list_import_job_rows' own reasoning exactly.
    cur.execute(
        f"{_ITEM_SELECT} WHERE {where} ORDER BY i.id LIMIT %s OFFSET %s",
        [*params, page_size, (page - 1) * page_size],
    )
    return {"items": cur.fetchall(), "total": total, "page": page, "pageSize": page_size}


# ---------------------------------------------------------------------------
# Item approval
# ---------------------------------------------------------------------------

_NON_APPROVABLE_STATUSES = ("unmatched", "blocked_by_clear", "unchanged")


def update_item(cur, job_id: int, item_id: int, approved: bool) -> dict:
    cur.execute("SELECT status FROM fs_bud_sync_job WHERE id = %s", (job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Job is not in a reviewable state (status={job['status']})")

    cur.execute(
        'SELECT id, match_status AS "matchStatus" FROM fs_bud_sync_item WHERE id = %s AND sync_job_id = %s',
        (item_id, job_id),
    )
    item = cur.fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="Sync item not found")

    if approved and item["matchStatus"] in _NON_APPROVABLE_STATUSES:
        raise HTTPException(
            status_code=400, detail=f"A '{item['matchStatus']}' item cannot be approved -- nothing to apply",
        )

    cur.execute("UPDATE fs_bud_sync_item SET approved = %s WHERE id = %s", (approved, item_id))
    cur.execute(f"{_ITEM_SELECT} WHERE i.id = %s", (item_id,))
    return cur.fetchone()


def bulk_approve_items(cur, job_id: int, item_ids: list[int], request: Request, session: dict) -> dict:
    """Partial success per-item, not all-or-nothing -- mirrors
    bud_sync_lib.bulk_approve_new_learners' own reasoning."""
    cur.execute("SELECT status FROM fs_bud_sync_job WHERE id = %s", (job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Job is not in a reviewable state (status={job['status']})")

    approved_ids: list[int] = []
    errors: list[dict] = []
    with cur.connection.transaction():
        for item_id in item_ids:
            cur.execute(
                'SELECT id, match_status AS "matchStatus", applied FROM fs_bud_sync_item '
                "WHERE id = %s AND sync_job_id = %s",
                (item_id, job_id),
            )
            item = cur.fetchone()
            if not item:
                errors.append({"itemId": item_id, "message": "Sync item not found"})
                continue
            if item["applied"]:
                errors.append({"itemId": item_id, "message": "Already applied"})
                continue
            if item["matchStatus"] not in ("new", "changed"):
                errors.append({"itemId": item_id, "message": f"A '{item['matchStatus']}' item cannot be approved"})
                continue
            cur.execute("UPDATE fs_bud_sync_item SET approved = true WHERE id = %s", (item_id,))
            approved_ids.append(item_id)

        if approved_ids:
            write_audit_log(
                request, action="fs_bud_sync_bulk_approved", entity_type="fs_bud_sync_job", entity_id=job_id,
                new_value={"approvedCount": len(approved_ids), "itemIds": approved_ids}, cur=cur,
            )

    return {"approvedCount": len(approved_ids), "errors": errors}


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def run_commit(cur, job_id: int, item_ids: list[int], approval_reason: str, request: Request, session: dict) -> dict:
    job = get_job(cur, job_id)
    if job["status"] == "completed":
        return job  # idempotent replay -- no reapplication
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Job is not ready to commit (status={job['status']})")

    cur.execute(
        f"""
        SELECT i.id, i.bud_learning_plan_id AS "budLearningPlanId", i.internal_learner_id AS "internalLearnerId",
               i.match_status AS "matchStatus", i.proposed_maths AS "proposedMaths",
               i.proposed_english AS "proposedEnglish", i.existing_maths AS "existingMaths",
               i.existing_english AS "existingEnglish", i.existing_status AS "existingStatus", i.applied
        FROM fs_bud_sync_item i WHERE i.id = ANY(%s) AND i.sync_job_id = %s
        """,
        (item_ids, job_id),
    )
    items = cur.fetchall()
    found_ids = {i["id"] for i in items}
    missing_ids = set(item_ids) - found_ids
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"Sync item(s) not found in this job: {sorted(missing_ids)}")

    not_actionable = [i["id"] for i in items if i["matchStatus"] not in ("new", "changed")]
    if not_actionable:
        raise HTTPException(
            status_code=400,
            detail=f"Item(s) are not actionable (unmatched/unchanged/blocked_by_clear): {not_actionable}",
        )

    # Staleness: a Bud-source change since preview is detected via the
    # mirror table's own max(synced_at) against the job's pinned
    # source_max_synced_at (this feed is replaced wholesale per staging
    # run, so there is no per-plan synced_at to compare individually the
    # way Bud/Aptem sync's per-row feed allows); an internal change is
    # detected via learner_fs_requirements' own current state against the
    # item's existing_* snapshot -- same "reject a stale preview"
    # discipline as fs_requirement_lib.confirm_import_job.
    cur.execute('SELECT max(synced_at) AS "maxSyncedAt" FROM bud_functional_skill_progress')
    current_max_synced_at = cur.fetchone()["maxSyncedAt"]
    source_is_stale = (
        job["sourceMaxSyncedAt"] is not None
        and current_max_synced_at is not None
        and current_max_synced_at > job["sourceMaxSyncedAt"]
    )

    surviving: list[dict] = []
    stale_source_ids: list[int] = []
    stale_internal_ids: list[int] = []
    for item in items:
        if item["applied"]:
            continue
        if source_is_stale:
            stale_source_ids.append(item["id"])
            continue
        cur.execute(f"{fs_requirement_lib.REQUIREMENT_SELECT} WHERE learner_id = %s", (item["internalLearnerId"],))
        live = cur.fetchone()
        live_tuple = (live["maths"], live["english"], live["status"]) if live else (None, None, None)
        existing_tuple = (item["existingMaths"], item["existingEnglish"], item["existingStatus"])
        if live_tuple != existing_tuple:
            stale_internal_ids.append(item["id"])
            continue
        surviving.append(item)

    for item_id in stale_source_ids + stale_internal_ids:
        cur.execute(
            "UPDATE fs_bud_sync_item SET outcome = %s, processed_at = now() WHERE id = %s",
            ("stale_source_rejected" if item_id in stale_source_ids else "stale_internal_rejected", item_id),
        )

    cur.execute("UPDATE fs_bud_sync_job SET status = 'committing' WHERE id = %s AND status = 'ready'", (job_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail="Job is not ready to commit (status changed concurrently)")

    try:
        with cur.connection.transaction():
            applied_count = 0
            for item in surviving:
                after_row = fs_requirement_lib._upsert_requirement(
                    cur, item["internalLearnerId"], item["proposedMaths"], item["proposedEnglish"],
                    "recorded", None, session["userId"], source="bud_fs_sync", fs_bud_sync_job_id=job_id,
                )
                cur.execute(
                    "UPDATE fs_bud_sync_item SET applied = true, outcome = 'applied', processed_at = now() WHERE id = %s",
                    (item["id"],),
                )
                write_audit_log(
                    request, action="fs_requirement_bud_synced", entity_type="learner_fs_requirement",
                    entity_id=after_row["id"],
                    previous_value={
                        "maths": item["existingMaths"], "english": item["existingEnglish"],
                        "status": item["existingStatus"],
                    },
                    new_value={
                        "maths": after_row["maths"], "english": after_row["english"], "status": after_row["status"],
                        "fsBudSyncJobId": job_id,
                    },
                    cur=cur,
                )
                applied_count += 1

            cur.execute(
                """
                UPDATE fs_bud_sync_job SET status = 'completed', completed_at = now(), approval_reason = %s,
                    approved_count = approved_count + %s, applied_count = applied_count + %s,
                    skipped_count = skipped_count + %s
                WHERE id = %s
                """,
                (
                    approval_reason, len(surviving), applied_count,
                    len(stale_source_ids) + len(stale_internal_ids), job_id,
                ),
            )
    except Exception as exc:
        cur.execute(
            "UPDATE fs_bud_sync_job SET status = 'failed', error_summary = %s WHERE id = %s",
            (str(exc.detail) if isinstance(exc, HTTPException) else str(exc), job_id),
        )
        write_audit_log(
            request, action="fs_bud_sync_commit_failed", entity_type="fs_bud_sync_job", entity_id=job_id,
            new_value={"error": str(exc.detail) if isinstance(exc, HTTPException) else str(exc)},
        )
        raise

    write_audit_log(
        request, action="fs_bud_sync_commit_completed", entity_type="fs_bud_sync_job", entity_id=job_id,
        new_value={"appliedCount": applied_count, "approvalReason": approval_reason}, cur=cur,
    )
    return get_job(cur, job_id)
