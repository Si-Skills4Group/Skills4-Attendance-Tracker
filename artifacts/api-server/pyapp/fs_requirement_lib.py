"""Stage 5: admin-uploaded Functional Skills subject requirement.

This is an interim, manually-sourced substitute for ILR learning-aim data
-- the Stage 5 discovery report confirmed no aim-level data is accessible
anywhere (no DDL, no API config, no documentation of one). This module
never investigates or builds a BUD/ILR integration; it is a plain
admin-uploaded CSV (learnerID, AIM) recording what subject(s) a learner
is EXPECTED to need Functional Skills provision for -- never described as
verified open ILR aims or funding eligibility, and never treated as proof
a learner is correctly/incorrectly allocated.

Hard rules:
- learnerID is learners.learner_ref (the BUD learner reference) -- never
  the internal numeric id, never a bud_learning_plan_id. Matching is exact
  string equality on the trimmed value, against non-deleted learners only
  (learner_ref is a genuine DB UNIQUE constraint, so "ambiguous" cannot
  structurally occur for an internal match today; a defensive check still
  treats a multi-row match as an error rather than assuming that
  invariant holds forever).
- No learner is ever created from this upload, and no Bud link, learner
  status, tutor, or home cohort is ever touched by it.
- Storage is one row per learner for the WHOLE lifecycle (recorded ->
  cleared -> re-recorded, never a fresh row), matching attendance_
  catchup's own pattern -- full history lives in audit_logs
  (entity_type='learner_fs_requirement'), this table only ever reflects
  the CURRENT state, and "cleared" is a real status, not a deleted row --
  it is how "no requirement" (row absent) is told apart from "explicitly
  removed" (row present, status='cleared', both flags false).
- cohorts.subject (what a cohort teaches) and a learner's uploaded
  requirement (what a learner needs) are two separate concepts -- this
  module never reads or writes cohorts.subject, and the allocation view
  only ever COMPARES the two, never reconciles them automatically.
- Preview and commit deliberately DIFFER from the existing learner-CSV-
  import feature's "warn and silently skip bad rows" philosophy: item 5
  explicitly requires blocking the whole commit while any row still has
  an error, so that is what this module does.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import HTTPException, Request

from .audit import write_audit_log

IMPORT_JOB_STALE_IMPORTING_MINUTES = 15
IMPORT_JOB_RETENTION_HOURS = 72

AIM_NORMALIZE = {"math": "math", "english": "english", "both": "both"}
AIM_DISPLAY_LABELS = {"math": "Maths", "english": "English", "both": "Maths and English"}
AIM_FLAGS = {"math": (True, False), "english": (False, True), "both": (True, True)}

JOB_SELECT = """
    SELECT id, filename, uploaded_by AS "uploadedBy", status,
           total_rows AS "totalRows", new_count AS "newCount", changed_count AS "changedCount",
           unchanged_count AS "unchangedCount", warning_count AS "warningCount", error_count AS "errorCount",
           result_summary AS "resultSummary", last_error AS "lastError",
           started_importing_at AS "startedImportingAt", created_at AS "createdAt", updated_at AS "updatedAt",
           expires_at AS "expiresAt"
    FROM fs_requirement_import_jobs
"""

ROW_SELECT = """
    SELECT id, job_id AS "jobId", row_number AS "rowNumber", raw_data AS "rawData",
           normalized_aim AS "normalizedAim", matched_learner_id AS "matchedLearnerId", outcome,
           existing_maths AS "existingMaths", existing_english AS "existingEnglish",
           existing_status AS "existingStatus", proposed_maths AS "proposedMaths",
           proposed_english AS "proposedEnglish", errors, warnings,
           import_result AS "importResult", created_at AS "createdAt"
    FROM fs_requirement_import_rows
"""

REQUIREMENT_SELECT = """
    SELECT id, learner_id AS "learnerId", maths, english, status, source,
           import_batch_id AS "importBatchId", updated_by AS "updatedBy", updated_at AS "updatedAt",
           created_at AS "createdAt"
    FROM learner_fs_requirements
"""


def normalize_aim(raw: str) -> str | None:
    return AIM_NORMALIZE.get((raw or "").strip().lower())


def get_requirement(cur, learner_id: int) -> dict | None:
    cur.execute(f"{REQUIREMENT_SELECT} WHERE learner_id = %s", (learner_id,))
    return cur.fetchone()


def _find_learner_by_ref(cur, learner_ref: str) -> dict | None:
    """learner_ref carries a genuine DB UNIQUE constraint, so more than one
    row is not structurally possible for an internal match -- still
    treated defensively as "not resolvable" rather than assumed."""
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", status FROM learners '
        "WHERE learner_ref = %s AND deleted_at IS NULL",
        (learner_ref,),
    )
    rows = cur.fetchall()
    if len(rows) != 1:
        return None
    return rows[0]


def _classify_single_row(cur, row: dict[str, str]) -> dict:
    """First pass: per-row validation and learner matching only -- no
    in-file duplicate detection (that needs every row's result first) and
    no outcome-vs-current-state computation (that needs a confirmed,
    non-conflicting match first). Returns errors/warnings plus whatever
    was resolvable in isolation."""
    # parse_import_csv returns dicts keyed by the CSV's own literal header
    # text ("learnerID"/"AIM"), not the column_to_field mapping (that
    # mapping is only used there to validate recognised/required headers).
    learner_ref = (row.get("learnerID") or "").strip()
    raw_aim = (row.get("AIM") or "").strip()
    errors: list[str] = []
    warnings: list[str] = []

    if not learner_ref:
        errors.append("learnerID is required")
    if not raw_aim:
        errors.append("AIM is required (a blank AIM is not a deletion instruction -- use Clear Requirement instead)")

    normalized_aim = normalize_aim(raw_aim) if raw_aim else None
    if raw_aim and normalized_aim is None:
        errors.append(f"AIM '{raw_aim}' is not recognised -- expected Math, English or Both")

    matched_learner = None
    if learner_ref and not errors:
        matched_learner = _find_learner_by_ref(cur, learner_ref)
        if matched_learner is None:
            errors.append(f"learnerID '{learner_ref}' does not match exactly one existing, non-deleted learner")
        elif matched_learner["status"] != "active":
            warnings.append(
                f"Learner status is '{matched_learner['status']}' -- the requirement will still be recorded, but "
                "this does not reactivate them or add them to the active allocation population."
            )

    return {
        "learnerRef": learner_ref,
        "normalizedAim": normalized_aim,
        "matchedLearner": matched_learner,
        "errors": errors,
        "warnings": warnings,
    }


def classify_rows(cur, parsed_rows: list[dict[str, str]]) -> list[dict]:
    """Full three-pass classification:
    1. per-row validation + learner matching (_classify_single_row).
    2. in-file duplicate handling BY learnerID (exact trimmed match,
       consistent with the matching rule itself): a later row repeating an
       earlier one's normalised AIM is collapsed into a "warning" (only
       the first occurrence is applied); a later row with a DIFFERENT AIM
       for the same learnerID blocks EVERY occurrence sharing that
       learnerID as a conflict error, since none of them can be trusted.
    3. outcome vs the learner's CURRENT requirement, for every row that
       survived passes 1-2 as an actionable, non-duplicate match.
    """
    first_pass = [_classify_single_row(cur, row) for row in parsed_rows]

    groups: dict[str, list[int]] = {}
    for i, result in enumerate(first_pass):
        if result["matchedLearner"] is not None and not result["errors"]:
            groups.setdefault(result["learnerRef"], []).append(i)

    collapsed_indices: set[int] = set()
    for learner_ref, indices in groups.items():
        if len(indices) < 2:
            continue
        distinct_aims = {first_pass[i]["normalizedAim"] for i in indices}
        if len(distinct_aims) > 1:
            conflicting = sorted(AIM_DISPLAY_LABELS[a] for a in distinct_aims)
            for i in indices:
                first_pass[i]["errors"].append(
                    f"learnerID '{learner_ref}' appears more than once in this file with conflicting AIM values "
                    f"({', '.join(conflicting)}) -- none of these rows were applied"
                )
        else:
            first_row_number = min(indices) + 1
            for i in indices[1:]:
                collapsed_indices.add(i)
                first_pass[i]["warnings"].append(
                    f"Duplicate of row {first_row_number} with the same AIM value -- only the first occurrence is applied"
                )

    matched_ids = {r["matchedLearner"]["id"] for r in first_pass if r["matchedLearner"] and not r["errors"]}
    requirements_by_learner: dict[int, dict] = {}
    if matched_ids:
        cur.execute(f"{REQUIREMENT_SELECT} WHERE learner_id = ANY(%s)", (list(matched_ids),))
        requirements_by_learner = {r["learnerId"]: r for r in cur.fetchall()}

    results = []
    for i, result in enumerate(first_pass):
        errors = result["errors"]
        warnings = result["warnings"]
        matched_learner = result["matchedLearner"]
        normalized_aim = result["normalizedAim"]

        if errors:
            outcome = "error"
            proposed_maths = proposed_english = None
            existing = None
        elif i in collapsed_indices:
            outcome = "warning"
            proposed_maths, proposed_english = AIM_FLAGS[normalized_aim]
            existing = requirements_by_learner.get(matched_learner["id"])
        else:
            proposed_maths, proposed_english = AIM_FLAGS[normalized_aim]
            existing = requirements_by_learner.get(matched_learner["id"])
            if existing is None:
                outcome = "new"
            elif existing["status"] == "recorded" and existing["maths"] == proposed_maths and existing["english"] == proposed_english:
                outcome = "unchanged"
            else:
                outcome = "changed"

        results.append({
            "learnerRef": result["learnerRef"],
            "normalizedAim": normalized_aim,
            "matchedLearnerId": matched_learner["id"] if matched_learner else None,
            "matchedLearnerName": f"{matched_learner['firstName']} {matched_learner['lastName']}" if matched_learner else None,
            "matchedLearnerStatus": matched_learner["status"] if matched_learner else None,
            "outcome": outcome,
            "existingMaths": existing["maths"] if existing else None,
            "existingEnglish": existing["english"] if existing else None,
            "existingStatus": existing["status"] if existing else None,
            "proposedMaths": proposed_maths,
            "proposedEnglish": proposed_english,
            "errors": errors,
            "warnings": warnings,
        })
    return results


def expire_due_fs_requirement_import_jobs(cur, as_of: datetime | None = None) -> None:
    """Same lazy sweep as learner_import_lib.expire_due_learner_import_jobs
    -- no background worker exists in this app, so crash-recovery and
    retention both happen opportunistically on read."""
    as_of = as_of or datetime.now()
    stale_cutoff = as_of - timedelta(minutes=IMPORT_JOB_STALE_IMPORTING_MINUTES)
    cur.execute(
        """
        UPDATE fs_requirement_import_jobs
        SET status = 'ready', last_error = 'Import was interrupted and has been reset for retry.', updated_at = now()
        WHERE status = 'importing' AND started_importing_at < %s
        """,
        (stale_cutoff,),
    )
    cur.execute("SELECT id FROM fs_requirement_import_jobs WHERE expires_at < %s", (as_of,))
    expired_ids = [row["id"] for row in cur.fetchall()]
    if expired_ids:
        cur.execute("DELETE FROM fs_requirement_import_rows WHERE job_id = ANY(%s)", (expired_ids,))
        cur.execute("DELETE FROM fs_requirement_import_jobs WHERE id = ANY(%s)", (expired_ids,))


def create_import_job(
    cur, filename: str, uploaded_by: int, parsed_rows: list[dict[str, str]],
    retention_hours: int = IMPORT_JOB_RETENTION_HOURS,
) -> dict:
    results = classify_rows(cur, parsed_rows)
    counts = {"new": 0, "changed": 0, "unchanged": 0, "warning": 0, "error": 0}
    for result in results:
        counts[result["outcome"]] += 1

    expires_at = datetime.now() + timedelta(hours=retention_hours)
    cur.execute(
        """
        INSERT INTO fs_requirement_import_jobs
            (filename, uploaded_by, status, total_rows, new_count, changed_count, unchanged_count,
             warning_count, error_count, expires_at)
        VALUES (%s, %s, 'ready', %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            filename, uploaded_by, len(parsed_rows),
            counts["new"], counts["changed"], counts["unchanged"], counts["warning"], counts["error"],
            expires_at,
        ),
    )
    job_id = cur.fetchone()["id"]

    for row_number, (raw_row, result) in enumerate(zip(parsed_rows, results), start=1):
        cur.execute(
            """
            INSERT INTO fs_requirement_import_rows
                (job_id, row_number, raw_data, normalized_aim, matched_learner_id, matched_learner_status,
                 outcome, existing_maths, existing_english, existing_status, proposed_maths, proposed_english,
                 errors, warnings)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job_id, row_number,
                json.dumps(raw_row),
                result["normalizedAim"], result["matchedLearnerId"], result["matchedLearnerStatus"], result["outcome"],
                result["existingMaths"], result["existingEnglish"], result["existingStatus"],
                result["proposedMaths"], result["proposedEnglish"],
                json.dumps(result["errors"]), json.dumps(result["warnings"]),
            ),
        )
    return get_import_job(cur, job_id)


def get_import_job(cur, job_id: int) -> dict:
    cur.execute(f"{JOB_SELECT} WHERE id = %s", (job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    return job


def list_import_job_rows(cur, job_id: int, page: int = 1, page_size: int = 25, outcome: str | None = None) -> dict:
    clauses = ["r.job_id = %s"]
    params: list = [job_id]
    if outcome:
        clauses.append("r.outcome = %s")
        params.append(outcome)
    where = " AND ".join(clauses)

    cur.execute(f"SELECT count(*)::int AS n FROM fs_requirement_import_rows r WHERE {where}", params)
    total = cur.fetchone()["n"]

    # Matched learner name/status are looked up live (not persisted on the row) so the
    # preview always reflects the learner's CURRENT name/status, not a stale snapshot.
    cur.execute(
        f"""
        SELECT r.id, r.job_id AS "jobId", r.row_number AS "rowNumber", r.raw_data AS "rawData",
               r.normalized_aim AS "normalizedAim", r.matched_learner_id AS "matchedLearnerId", r.outcome,
               r.existing_maths AS "existingMaths", r.existing_english AS "existingEnglish",
               r.existing_status AS "existingStatus", r.proposed_maths AS "proposedMaths",
               r.proposed_english AS "proposedEnglish", r.errors, r.warnings,
               r.import_result AS "importResult", r.created_at AS "createdAt",
               CASE WHEN l.id IS NOT NULL THEN concat(l.first_name, ' ', l.last_name) END AS "matchedLearnerName",
               l.status AS "matchedLearnerStatus"
        FROM fs_requirement_import_rows r
        LEFT JOIN learners l ON l.id = r.matched_learner_id
        WHERE {where}
        ORDER BY r.row_number LIMIT %s OFFSET %s
        """,
        (*params, page_size, (page - 1) * page_size),
    )
    return {"items": cur.fetchall(), "total": total, "page": page, "pageSize": page_size}


def cancel_import_job(cur, job_id: int) -> dict:
    cur.execute("UPDATE fs_requirement_import_jobs SET status = 'cancelled', updated_at = now() WHERE id = %s AND status = 'ready'", (job_id,))
    if cur.rowcount == 0:
        job = get_import_job(cur, job_id)
        raise HTTPException(status_code=409, detail=f"Import job cannot be cancelled (status={job['status']})")
    return get_import_job(cur, job_id)


def _upsert_requirement(cur, learner_id: int, maths: bool, english: bool, status: str, batch_id: int | None, updated_by: int) -> dict:
    cur.execute(
        """
        INSERT INTO learner_fs_requirements (learner_id, maths, english, status, source, import_batch_id, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, 'manual_upload', %s, %s, now())
        ON CONFLICT (learner_id) DO UPDATE SET
            maths = EXCLUDED.maths, english = EXCLUDED.english, status = EXCLUDED.status,
            source = 'manual_upload', import_batch_id = EXCLUDED.import_batch_id,
            updated_by = EXCLUDED.updated_by, updated_at = now()
        RETURNING id, learner_id AS "learnerId", maths, english, status, source,
                  import_batch_id AS "importBatchId", updated_by AS "updatedBy", updated_at AS "updatedAt",
                  created_at AS "createdAt"
        """,
        (learner_id, maths, english, status, batch_id, updated_by),
    )
    return cur.fetchone()


def confirm_import_job(cur, job_id: int, request: Request, session: dict) -> dict:
    """Deliberately blocks the WHOLE commit (item 5) while any row still
    has an error, and rejects the WHOLE commit if the live learner/
    requirement state for any actionable row has drifted since preview
    (item 5's "reject a stale preview") -- neither check exists in the
    reused learner-CSV-import feature (which warns and silently skips bad
    rows, and never re-validates staleness generally); both are new,
    stricter behaviour this feature's own spec explicitly asks for."""
    expire_due_fs_requirement_import_jobs(cur)
    job = get_import_job(cur, job_id)
    if job["status"] == "completed":
        return job["resultSummary"]
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Import job is not ready to confirm (status={job['status']})")
    if job["errorCount"] > 0:
        raise HTTPException(status_code=400, detail="This file still has row-level errors -- fix them and re-upload before committing.")

    rows_result = list_import_job_rows(cur, job_id, page=1, page_size=job["totalRows"] or 1)
    actionable_rows = [r for r in rows_result["items"] if r["outcome"] in ("new", "changed")]

    if actionable_rows:
        learner_ids = [r["matchedLearnerId"] for r in actionable_rows]
        cur.execute('SELECT id, deleted_at, status FROM learners WHERE id = ANY(%s)', (learner_ids,))
        live_learners = {r["id"]: r for r in cur.fetchall()}
        cur.execute(f"{REQUIREMENT_SELECT} WHERE learner_id = ANY(%s)", (learner_ids,))
        live_requirements = {r["learnerId"]: r for r in cur.fetchall()}
        # The preview page reads matched-learner name/status LIVE (so it never shows a
        # stale name/status while a job sits unconfirmed), but the row's own warning
        # text ("Learner status is 'withdrawn' -- ...") is only ever generated once, at
        # classification time, from the status observed then. If status drifts between
        # preview and confirm, that frozen warning text no longer matches reality -- so
        # this compares the status AS OBSERVED AT CLASSIFICATION (persisted per row)
        # against the LIVE status here, and treats a mismatch as a stale preview exactly
        # like a deleted learner or a changed requirement, rather than silently applying
        # a requirement whose accompanying warning is now wrong or missing.
        row_ids = [r["id"] for r in actionable_rows]
        cur.execute(
            "SELECT id, matched_learner_status FROM fs_requirement_import_rows WHERE id = ANY(%s)",
            (row_ids,),
        )
        preview_status_by_row_id = {r["id"]: r["matched_learner_status"] for r in cur.fetchall()}

        for row in actionable_rows:
            learner = live_learners.get(row["matchedLearnerId"])
            if learner is None or learner["deleted_at"] is not None:
                raise HTTPException(
                    status_code=409,
                    detail="A matched learner has been deleted since this file was previewed -- re-upload to refresh the preview.",
                )
            if preview_status_by_row_id.get(row["id"]) != learner["status"]:
                raise HTTPException(
                    status_code=409,
                    detail="A matched learner's status changed since this file was previewed -- re-upload to refresh the preview.",
                )
            live_req = live_requirements.get(row["matchedLearnerId"])
            preview_existing = (row["existingMaths"], row["existingEnglish"], row["existingStatus"])
            live_existing = (live_req["maths"], live_req["english"], live_req["status"]) if live_req else (None, None, None)
            if preview_existing != live_existing:
                raise HTTPException(
                    status_code=409,
                    detail="This learner's requirement changed since this file was previewed -- re-upload to refresh the preview.",
                )

    cur.execute(
        "UPDATE fs_requirement_import_jobs SET status = 'importing', started_importing_at = now(), updated_at = now() WHERE id = %s AND status = 'ready'",
        (job_id,),
    )
    if cur.rowcount == 0:
        job = get_import_job(cur, job_id)
        if job["status"] == "completed":
            return job["resultSummary"]
        raise HTTPException(status_code=409, detail=f"Import job is not ready to confirm (status={job['status']})")

    try:
        with cur.connection.transaction():
            changed_learner_ids = []
            for row in actionable_rows:
                before = {"maths": row["existingMaths"], "english": row["existingEnglish"], "status": row["existingStatus"]}
                after_row = _upsert_requirement(
                    cur, row["matchedLearnerId"], row["proposedMaths"], row["proposedEnglish"],
                    "recorded", job_id, session["userId"],
                )
                write_audit_log(
                    request, action="fs_requirement_uploaded", entity_type="learner_fs_requirement",
                    entity_id=after_row["id"],
                    previous_value=before,
                    new_value={"maths": after_row["maths"], "english": after_row["english"], "status": after_row["status"], "importBatchId": job_id},
                    cur=cur,
                )
                changed_learner_ids.append(row["matchedLearnerId"])

            summary = {
                "totalRows": job["totalRows"], "new": job["newCount"], "changed": job["changedCount"],
                "unchanged": job["unchangedCount"], "warning": job["warningCount"], "applied": len(actionable_rows),
                "changedLearnerIds": changed_learner_ids,
            }
            cur.execute(
                "UPDATE fs_requirement_import_jobs SET status = 'completed', result_summary = %s, updated_at = now() WHERE id = %s",
                (json.dumps(summary), job_id),
            )
    except Exception as exc:
        cur.execute(
            "UPDATE fs_requirement_import_jobs SET status = 'ready', last_error = %s, updated_at = now() WHERE id = %s",
            (str(exc.detail) if isinstance(exc, HTTPException) else str(exc), job_id),
        )
        raise

    write_audit_log(
        request, action="fs_requirement_import_confirmed", entity_type="fs_requirement_import_job",
        entity_id=job_id, new_value=summary,
    )
    return summary


def clear_requirement(cur, learner_id: int, reason: str, request: Request, session: dict) -> dict:
    if not reason or not reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to clear a Functional Skills requirement")

    with cur.connection.transaction():
        cur.execute(f"{REQUIREMENT_SELECT} WHERE learner_id = %s FOR UPDATE", (learner_id,))
        existing = cur.fetchone()
        if existing is None or existing["status"] != "recorded":
            raise HTTPException(status_code=404, detail="No active Functional Skills requirement to clear for this learner")

        before = {"maths": existing["maths"], "english": existing["english"], "status": existing["status"]}
        cur.execute(
            """
            UPDATE learner_fs_requirements
            SET maths = false, english = false, status = 'cleared', source = 'manual_upload',
                updated_by = %s, updated_at = now()
            WHERE id = %s
            RETURNING id, learner_id AS "learnerId", maths, english, status, source,
                      import_batch_id AS "importBatchId", updated_by AS "updatedBy", updated_at AS "updatedAt"
            """,
            (session["userId"], existing["id"]),
        )
        updated = cur.fetchone()
        write_audit_log(
            request, action="fs_requirement_cleared", entity_type="learner_fs_requirement", entity_id=existing["id"],
            previous_value=before, new_value={"maths": False, "english": False, "status": "cleared", "reason": reason.strip()},
            cur=cur,
        )
    return updated


_COVERAGE_CTE = """
    WITH base AS (
        SELECT
            l.id, l.learner_ref, l.first_name, l.last_name, l.status,
            req.maths AS req_maths, req.english AS req_english, req.status AS req_status,
            req.source, req.import_batch_id, req.updated_by, req.updated_at,
            (req.status = 'recorded' AND req.maths) AS maths_required,
            (req.status = 'recorded' AND req.english) AS english_required,
            EXISTS (
                SELECT 1 FROM learner_cohort_enrollments e JOIN cohorts c ON c.id = e.cohort_id
                WHERE e.learner_id = l.id AND e.status = 'active' AND c.subject IN ('math', 'both') AND c.deleted_at IS NULL
            ) AS maths_any_covered,
            EXISTS (
                SELECT 1 FROM learner_cohort_enrollments e JOIN cohorts c ON c.id = e.cohort_id JOIN tutors t ON t.id = c.tutor_id
                WHERE e.learner_id = l.id AND e.status = 'active' AND c.subject IN ('math', 'both') AND c.deleted_at IS NULL AND t.active
            ) AS maths_active_covered,
            EXISTS (
                SELECT 1 FROM learner_cohort_enrollments e JOIN cohorts c ON c.id = e.cohort_id
                WHERE e.learner_id = l.id AND e.status = 'active' AND c.subject IN ('english', 'both') AND c.deleted_at IS NULL
            ) AS english_any_covered,
            EXISTS (
                SELECT 1 FROM learner_cohort_enrollments e JOIN cohorts c ON c.id = e.cohort_id JOIN tutors t ON t.id = c.tutor_id
                WHERE e.learner_id = l.id AND e.status = 'active' AND c.subject IN ('english', 'both') AND c.deleted_at IS NULL AND t.active
            ) AS english_active_covered,
            EXISTS (
                SELECT 1 FROM learner_cohort_enrollments e JOIN cohorts c ON c.id = e.cohort_id
                WHERE e.learner_id = l.id AND e.status = 'active' AND c.subject IS NOT NULL AND c.deleted_at IS NULL
            ) AS has_any_active_secondary
        FROM learners l
        LEFT JOIN learner_fs_requirements req ON req.learner_id = l.id
        WHERE {where}
    ),
    final AS (
        SELECT *,
            (maths_required AND NOT maths_active_covered) AS missing_maths,
            (english_required AND NOT english_active_covered) AS missing_english
        FROM base
    )
"""


def fetch_allocation_rows(
    cur, *, search: str | None, subject: str | None, missing_only: bool, status: str | None,
    page: int, page_size: int,
) -> tuple[list[dict], int]:
    """Item 6's admin allocation view. Population defaults to active
    learners (status filter overrides); a learner appears here whenever
    they have EITHER an uploaded requirement OR an existing active
    secondary enrollment, so a pre-existing FS enrollment with no uploaded
    requirement is still visible (labelled "Requirement not recorded"),
    never silently dropped. Subject coverage is derived fresh from active,
    non-deleted cohort enrollments -- never stored, never auto-reconciled
    against cohorts.subject. missing_only/subject filters are applied in
    SQL, before COUNT and before pagination, so the total always matches
    exactly what a page can show (item 6: "counts must match the full
    filtered population")."""
    clauses = ["l.deleted_at IS NULL"]
    params: dict = {}
    if status:
        clauses.append("l.status = %(status)s")
        params["status"] = status
    else:
        clauses.append("l.status = 'active'")
    if search:
        clauses.append("(l.first_name ILIKE %(search)s OR l.last_name ILIKE %(search)s OR l.learner_ref ILIKE %(search)s)")
        params["search"] = f"%{search}%"
    where = " AND ".join(clauses)

    final_filters = ["(req_status IS NOT NULL OR has_any_active_secondary)"]
    if subject == "math":
        final_filters.append("maths_required")
    elif subject == "english":
        final_filters.append("english_required")
    elif subject == "both":
        final_filters.append("(maths_required AND english_required)")
    if missing_only:
        final_filters.append("(missing_maths OR missing_english)")
    final_where = " AND ".join(final_filters)

    cte = _COVERAGE_CTE.format(where=where)

    cur.execute(f"{cte} SELECT count(*)::int AS n FROM final WHERE {final_where}", params)
    total = cur.fetchone()["n"]

    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    cur.execute(
        f"""
        {cte}
        SELECT
            f.id, f.learner_ref AS "learnerRef", concat(f.first_name, ' ', f.last_name) AS "learnerName",
            f.status, f.maths_required AS "maths", f.english_required AS "english",
            (f.req_status IS NOT NULL) AS "requirementRecorded", f.req_status AS "requirementStatus",
            f.source, f.import_batch_id AS "importBatchId", f.updated_by AS "updatedBy", f.updated_at AS "updatedAt",
            CASE WHEN f.missing_maths AND f.missing_english THEN ARRAY['math', 'english']
                 WHEN f.missing_maths THEN ARRAY['math']
                 WHEN f.missing_english THEN ARRAY['english']
                 ELSE ARRAY[]::text[] END AS "missingSubjects",
            CASE WHEN f.maths_required AND f.maths_any_covered AND NOT f.maths_active_covered THEN ARRAY['math']
                 ELSE ARRAY[]::text[] END
            || CASE WHEN f.english_required AND f.english_any_covered AND NOT f.english_active_covered THEN ARRAY['english']
                 ELSE ARRAY[]::text[] END AS "missingTutorSubjects",
            COALESCE((
                SELECT json_agg(json_build_object(
                    'cohortId', c.id, 'cohortName', c.name, 'subject', c.subject,
                    'tutorId', t.id,
                    'tutorName', CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END,
                    'tutorActive', t.active
                ))
                FROM learner_cohort_enrollments e
                JOIN cohorts c ON c.id = e.cohort_id
                LEFT JOIN tutors t ON t.id = c.tutor_id
                WHERE e.learner_id = f.id AND e.status = 'active' AND c.subject IS NOT NULL AND c.deleted_at IS NULL
            ), '[]'::json) AS "activeSecondaryCohorts"
        FROM final f
        WHERE {final_where}
        ORDER BY f.last_name, f.first_name, f.id
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    return cur.fetchall(), total
