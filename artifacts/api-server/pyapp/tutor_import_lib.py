"""Core services for the controlled tutor CSV import: duplicate
classification against the existing tutors table, and lazy job-expiry/
crash-recovery sweeping. Mirrors learner_import_lib.py's structure --
see that module for the fuller design rationale -- with the identifier
roles reversed: for learners, learner_reference is the required+unique
primary key and uln is optional+unique; for tutors, email is the
required+unique primary key (enforced on users.email, denormalized onto
tutors.email by every create/update path) and employee_ref is
optional+unique. There is no cohort-allocation concept for tutors (cohorts
point *at* a tutor, tutors aren't allocated *into* anything), so there is
no equivalent of resolve_cohort_names/_maybe_allocate_new_learner here, and
no 'possible_duplicate' weak-match tier -- learners have a start_date
window + name as a weak secondary signal; tutors have nothing comparably
reliable, so a name-only match would just be noise.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from .csv_utils import TUTOR_IMPORT_COLUMN_TO_FIELD, TUTOR_IMPORT_REQUIRED_COLUMNS

IMPORT_JOB_STALE_IMPORTING_MINUTES = 15
IMPORT_JOB_RETENTION_HOURS = 72

JOB_SELECT = """
    SELECT id, filename, uploaded_by AS "uploadedBy", status,
           total_rows AS "totalRows", new_count AS "newCount",
           exact_existing_count AS "exactExistingCount", probable_duplicate_count AS "probableDuplicateCount",
           identifier_conflict_count AS "identifierConflictCount", invalid_count AS "invalidCount",
           result_summary AS "resultSummary", last_error AS "lastError",
           started_importing_at AS "startedImportingAt", created_at AS "createdAt", updated_at AS "updatedAt",
           expires_at AS "expiresAt"
    FROM tutor_import_jobs
"""

ROW_SELECT = """
    SELECT id, job_id AS "jobId", row_number AS "rowNumber", raw_data AS "rawData",
           classification, proposed_action AS "proposedAction", resolution,
           resolved_by AS "resolvedBy", resolved_at AS "resolvedAt", match_details AS "matchDetails",
           matched_tutor_id AS "matchedTutorId", errors, warnings,
           import_result AS "importResult", import_error AS "importError", created_at AS "createdAt"
    FROM tutor_import_rows
"""


def validate_row_fields(row: dict[str, str]) -> list[str]:
    """Field-level validation mirroring TutorInput's own required fields."""
    errors = []
    for col in TUTOR_IMPORT_REQUIRED_COLUMNS:
        if not row.get(col):
            errors.append(f"{col} is required")
    return errors


def load_existing_tutors(cur) -> list[dict]:
    cur.execute(
        """
        SELECT id, first_name AS "firstName", last_name AS "lastName", email, employee_ref AS "employeeRef"
        FROM tutors
        """
    )
    return cur.fetchall()


class ExistingTutorIndex:
    def __init__(self, tutors: list[dict]):
        self.by_id = {t["id"]: t for t in tutors}
        self.by_email: dict[str, dict] = {}
        self.by_employee_ref: dict[str, dict] = {}
        for t in tutors:
            if t["email"]:
                self.by_email[t["email"].strip().lower()] = t
            if t["employeeRef"]:
                self.by_employee_ref[t["employeeRef"]] = t


def classify_row(index: ExistingTutorIndex, row: dict[str, str]) -> dict:
    """Classifies a single parsed CSV row against the existing tutors
    table. Order matters: invalid short-circuits everything else; an
    identifier conflict (email points to tutor A but employee_ref points
    to a *different* tutor B) takes priority over a plain exact-match so
    it is never silently treated as a safe update."""
    field_errors = validate_row_fields(row)
    if field_errors:
        return {
            "classification": "invalid",
            "proposedAction": "blocked",
            "matchedTutorId": None,
            "matchDetails": {},
            "errors": field_errors,
            "warnings": [],
        }

    email = (row.get("email") or "").strip().lower()
    employee_ref = row.get("employee_ref", "")

    email_match = index.by_email.get(email)
    employee_ref_match = index.by_employee_ref.get(employee_ref) if employee_ref else None

    if email_match and employee_ref_match and employee_ref_match["id"] != email_match["id"]:
        return {
            "classification": "identifier_conflict",
            "proposedAction": "blocked",
            "matchedTutorId": email_match["id"],
            "matchDetails": {"emailMatchId": email_match["id"], "employeeRefMatchId": employee_ref_match["id"]},
            "errors": ["email and employee_ref point to different existing tutors"],
            "warnings": [],
        }

    if email_match:
        return {
            "classification": "exact_existing",
            "proposedAction": "skip",
            "matchedTutorId": email_match["id"],
            "matchDetails": {"matchedOn": "email"},
            "errors": [],
            "warnings": [],
        }

    if employee_ref_match:
        return {
            "classification": "probable_duplicate",
            "proposedAction": "skip",
            "matchedTutorId": employee_ref_match["id"],
            "matchDetails": {"matchedOn": "employee_ref"},
            "errors": [],
            "warnings": [],
        }

    return {
        "classification": "new",
        "proposedAction": "create",
        "matchedTutorId": None,
        "matchDetails": {},
        "errors": [],
        "warnings": [],
    }


def classify_rows(cur, parsed_rows: list[dict[str, str]]) -> list[dict]:
    """Classifies every row in one pass, plus a within-file check: a
    second row reusing an email that a prior row in the *same file*
    already claimed as 'new' is reclassified as an identifier_conflict,
    mirroring learners' within-file duplicate-reference detection."""
    index = ExistingTutorIndex(load_existing_tutors(cur))

    results = []
    seen_emails_this_file: set[str] = set()
    for row in parsed_rows:
        result = classify_row(index, row)
        email = (row.get("email") or "").strip().lower()
        if result["classification"] == "new" and email:
            if email in seen_emails_this_file:
                result = {
                    "classification": "identifier_conflict",
                    "proposedAction": "blocked",
                    "matchedTutorId": None,
                    "matchDetails": {"duplicateWithinFile": True},
                    "errors": [f"email '{email}' appears more than once in this file"],
                    "warnings": [],
                }
            else:
                seen_emails_this_file.add(email)
        results.append(result)
    return results


def expire_due_tutor_import_jobs(cur, as_of: datetime | None = None) -> None:
    """Lazy sweep (same pattern as apply_due_scheduled_allocations /
    expire_due_learner_import_jobs): stale 'importing' jobs revert to
    'ready' with an explanatory last_error; jobs past expires_at are
    hard-deleted (rows first, no FK to violate)."""
    as_of = as_of or datetime.now()
    stale_cutoff = as_of - timedelta(minutes=IMPORT_JOB_STALE_IMPORTING_MINUTES)
    cur.execute(
        """
        UPDATE tutor_import_jobs
        SET status = 'ready', last_error = 'Import was interrupted and has been reset for retry.', updated_at = now()
        WHERE status = 'importing' AND started_importing_at < %s
        """,
        (stale_cutoff,),
    )

    cur.execute("SELECT id FROM tutor_import_jobs WHERE expires_at < %s", (as_of,))
    expired_ids = [row["id"] for row in cur.fetchall()]
    if expired_ids:
        cur.execute("DELETE FROM tutor_import_rows WHERE job_id = ANY(%s)", (expired_ids,))
        cur.execute("DELETE FROM tutor_import_jobs WHERE id = ANY(%s)", (expired_ids,))


# ---------------------------------------------------------------------------
# Job lifecycle: create (upload + classify), list/read, per-row resolution,
# cancel.
# ---------------------------------------------------------------------------


def create_import_job(
    cur,
    filename: str,
    uploaded_by: int,
    parsed_rows: list[dict[str, str]],
    retention_hours: int = IMPORT_JOB_RETENTION_HOURS,
) -> dict:
    index_rows = classify_rows(cur, parsed_rows)

    counts = {"new": 0, "exact_existing": 0, "probable_duplicate": 0, "identifier_conflict": 0, "invalid": 0}
    for result in index_rows:
        counts[result["classification"]] += 1

    expires_at = datetime.now() + timedelta(hours=retention_hours)
    cur.execute(
        """
        INSERT INTO tutor_import_jobs
            (filename, uploaded_by, status, total_rows, new_count, exact_existing_count,
             probable_duplicate_count, identifier_conflict_count, invalid_count, expires_at)
        VALUES (%s, %s, 'ready', %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            filename,
            uploaded_by,
            len(parsed_rows),
            counts["new"],
            counts["exact_existing"],
            counts["probable_duplicate"],
            counts["identifier_conflict"],
            counts["invalid"],
            expires_at,
        ),
    )
    job_id = cur.fetchone()["id"]

    for row_number, (raw_row, result) in enumerate(zip(parsed_rows, index_rows), start=1):
        cur.execute(
            """
            INSERT INTO tutor_import_rows
                (job_id, row_number, raw_data, classification, proposed_action, match_details,
                 matched_tutor_id, errors, warnings)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job_id,
                row_number,
                json.dumps(raw_row),
                result["classification"],
                result["proposedAction"],
                json.dumps(result["matchDetails"]),
                result["matchedTutorId"],
                json.dumps(result["errors"]),
                json.dumps(result["warnings"]),
            ),
        )

    cur.execute(f"{JOB_SELECT} WHERE id = %s", (job_id,))
    return cur.fetchone()


def get_import_job(cur, job_id: int) -> dict:
    cur.execute(f"{JOB_SELECT} WHERE id = %s", (job_id,))
    job = cur.fetchone()
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")
    return job


def _enrich_rows_with_names(cur, rows: list[dict]) -> None:
    """Adds matchedTutorName to each row in place -- an admin resolving a
    probable_duplicate is choosing whether to update *this specific
    person*, and a bare database id is meaningless for that decision."""
    tutor_ids = {r["matchedTutorId"] for r in rows if r["matchedTutorId"] is not None}
    tutor_names: dict[int, str] = {}
    if tutor_ids:
        cur.execute("SELECT id, first_name, last_name FROM tutors WHERE id = ANY(%s)", (list(tutor_ids),))
        tutor_names = {row["id"]: f"{row['first_name']} {row['last_name']}" for row in cur.fetchall()}
    for r in rows:
        r["matchedTutorName"] = tutor_names.get(r["matchedTutorId"])


def list_import_job_rows(cur, job_id: int, page: int = 1, page_size: int = 25, classification: str | None = None) -> dict:
    get_import_job(cur, job_id)  # 404s if the job doesn't exist
    clauses = ["job_id = %s"]
    params: list = [job_id]
    if classification:
        clauses.append("classification = %s")
        params.append(classification)
    where = f"WHERE {' AND '.join(clauses)}"

    cur.execute(
        f"{ROW_SELECT} {where} ORDER BY row_number LIMIT %s OFFSET %s",
        [*params, page_size, (page - 1) * page_size],
    )
    items = cur.fetchall()
    cur.execute(f"SELECT count(*)::int AS count FROM tutor_import_rows {where}", params)
    total = cur.fetchone()["count"]

    _enrich_rows_with_names(cur, items)
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def resolve_import_row(cur, job_id: int, row_id: int, resolution: str, resolved_by: int) -> dict:
    """Records the admin's explicit skip-or-update choice for one duplicate
    row -- see learner_import_lib.resolve_import_row's docstring for why
    'new'/'blocked' rows reject a resolution attempt rather than silently
    ignoring it."""
    if resolution not in ("skip", "update"):
        raise HTTPException(status_code=400, detail="resolution must be 'skip' or 'update'")

    job = get_import_job(cur, job_id)
    if job["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Import job is not editable (status={job['status']})")

    cur.execute(f"{ROW_SELECT} WHERE id = %s AND job_id = %s", (row_id, job_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Import row not found")
    if row["proposedAction"] == "blocked":
        raise HTTPException(status_code=400, detail="This row has errors and cannot be resolved -- fix the source file and re-upload")
    if row["classification"] == "new":
        raise HTTPException(status_code=400, detail="New tutors are always created -- there is nothing to resolve")

    cur.execute(
        """
        UPDATE tutor_import_rows
        SET resolution = %s, resolved_by = %s, resolved_at = now()
        WHERE id = %s
        RETURNING id
        """,
        (resolution, resolved_by, row_id),
    )
    cur.execute(f"{ROW_SELECT} WHERE id = %s", (row_id,))
    resolved_row = cur.fetchone()
    _enrich_rows_with_names(cur, [resolved_row])
    return resolved_row


def cancel_import_job(cur, job_id: int) -> dict:
    job = get_import_job(cur, job_id)
    if job["status"] in ("completed", "importing"):
        raise HTTPException(status_code=409, detail=f"Import job cannot be cancelled (status={job['status']})")
    cur.execute(
        "UPDATE tutor_import_jobs SET status = 'cancelled', updated_at = now() WHERE id = %s",
        (job_id,),
    )
    return get_import_job(cur, job_id)


# ---------------------------------------------------------------------------
# Confirm -- atomic, idempotent import.
# ---------------------------------------------------------------------------


def _parse_active_field(value: str) -> bool:
    return value.strip().lower() not in {"false", "0", "no"}


def _row_to_field_kwargs(raw_data: dict[str, str], *, include_blanks: bool) -> dict:
    """Maps a raw snake_case CSV row to camelCase TutorInput/TutorUpdate
    kwargs. For updates, blank values are excluded entirely so
    exclude_unset in _update_tutor only touches fields the file actually
    provided -- a blank cell must never blank out existing data the file
    simply didn't mention. `active` gets its own boolean parsing (mirrors
    the old CSV importer's _parse_csv_active): a blank cell means "not
    provided", not a literal empty-string value passed to Pydantic's bool
    coercion."""
    kwargs = {}
    for csv_col, field in TUTOR_IMPORT_COLUMN_TO_FIELD.items():
        value = (raw_data.get(csv_col) or "").strip()
        if field == "active":
            if value:
                kwargs["active"] = _parse_active_field(value)
            elif include_blanks:
                kwargs["active"] = True
            continue
        if value:
            kwargs[field] = value
        elif include_blanks:
            kwargs[field] = None
    return kwargs


def _effective_action(row: dict) -> str:
    if row["proposedAction"] == "blocked":
        return "skip"
    if row["classification"] == "new":
        return "create"
    return "update" if row.get("resolution") == "update" else "skip"


def _import_one_row(cur, row: dict, request: Request, session: dict) -> dict:
    from .routers.tutors import TutorInput, TutorUpdate, _create_tutor, _update_tutor

    action = _effective_action(row)

    if action == "skip":
        cur.execute("UPDATE tutor_import_rows SET import_result = 'skipped' WHERE id = %s", (row["id"],))
        return {"rowId": row["id"], "result": "skipped"}

    if action == "create":
        payload = TutorInput(**_row_to_field_kwargs(row["rawData"], include_blanks=True))
        created = _create_tutor(cur, payload, request, session)
        cur.execute(
            "UPDATE tutor_import_rows SET import_result = 'created', matched_tutor_id = %s WHERE id = %s",
            (created["id"], row["id"]),
        )
        return {"rowId": row["id"], "result": "created", "tutorId": created["id"]}

    # action == "update" -- confirm=False (the default deactivation-guard
    # behaviour): if this row would deactivate a tutor who still has
    # active cohorts assigned, _update_tutor raises its normal 409, which
    # rolls back the whole job like any other row failure. That's a
    # deliberate fail-closed choice -- a CSV import must never silently
    # bypass a guard that exists specifically to require human
    # confirmation before deactivating someone with active work assigned.
    payload = TutorUpdate(**_row_to_field_kwargs(row["rawData"], include_blanks=False))
    updated = _update_tutor(cur, row["matchedTutorId"], payload, request, session, confirm=False)
    cur.execute("UPDATE tutor_import_rows SET import_result = 'updated' WHERE id = %s", (row["id"],))
    return {"rowId": row["id"], "result": "updated", "tutorId": updated["id"]}


def _build_result_summary(result_rows: list[dict]) -> dict:
    counts = {"created": 0, "updated": 0, "skipped": 0}
    for r in result_rows:
        counts[r["result"]] += 1
    return {"totalRows": len(result_rows), **counts}


def confirm_import_job(cur, job_id: int, request: Request, session: dict) -> dict:
    """Atomically claims and imports a job -- see
    learner_import_lib.confirm_import_job's docstring for the full
    idempotency/rollback rationale, which applies identically here."""
    cur.execute(
        """
        UPDATE tutor_import_jobs
        SET status = 'importing', started_importing_at = now(), updated_at = now()
        WHERE id = %s AND status = 'ready'
        """,
        (job_id,),
    )
    claimed = cur.rowcount > 0

    if not claimed:
        job = get_import_job(cur, job_id)
        if job["status"] == "completed":
            return job["resultSummary"]
        raise HTTPException(status_code=409, detail=f"Import job is not ready to confirm (status={job['status']})")

    cur.execute(f"{ROW_SELECT} WHERE job_id = %s ORDER BY row_number", (job_id,))
    rows = cur.fetchall()

    try:
        with cur.connection.transaction():
            result_rows = [_import_one_row(cur, row, request, session) for row in rows]
    except Exception as exc:
        cur.execute(
            "UPDATE tutor_import_jobs SET status = 'ready', last_error = %s, updated_at = now() WHERE id = %s",
            (str(exc.detail) if isinstance(exc, HTTPException) else str(exc), job_id),
        )
        raise

    summary = _build_result_summary(result_rows)
    cur.execute(
        """
        UPDATE tutor_import_jobs
        SET status = 'completed', result_summary = %s, last_error = NULL, updated_at = now()
        WHERE id = %s
        """,
        (json.dumps(summary), job_id),
    )
    return summary
