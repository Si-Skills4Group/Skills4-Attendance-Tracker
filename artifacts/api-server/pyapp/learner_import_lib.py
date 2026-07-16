"""Core services for the controlled learner CSV import (Phase 5): duplicate
classification against the existing learners table, cohort-name resolution
for optional allocation, and lazy job-expiry/crash-recovery sweeping.

No background job/cron infrastructure exists in this app (see
scheduled_allocations_lib for the precedent) -- expiry and crash recovery
for import jobs are handled the same lazy way: swept once at app boot and
again at the top of every import-job read endpoint.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import HTTPException, Request

from .allocation_lib import apply_transfer
from .csv_utils import LEARNER_IMPORT_COLUMN_TO_FIELD, LEARNER_IMPORT_REQUIRED_COLUMNS

IMPORT_JOB_STALE_IMPORTING_MINUTES = 15
IMPORT_JOB_RETENTION_HOURS = 72
_POSSIBLE_DUPLICATE_START_DATE_WINDOW_DAYS = 7

JOB_SELECT = """
    SELECT id, filename, uploaded_by AS "uploadedBy", status,
           total_rows AS "totalRows", new_count AS "newCount",
           exact_existing_count AS "exactExistingCount", probable_duplicate_count AS "probableDuplicateCount",
           possible_duplicate_count AS "possibleDuplicateCount", identifier_conflict_count AS "identifierConflictCount",
           invalid_count AS "invalidCount", result_summary AS "resultSummary", last_error AS "lastError",
           started_importing_at AS "startedImportingAt", created_at AS "createdAt", updated_at AS "updatedAt",
           expires_at AS "expiresAt"
    FROM learner_import_jobs
"""

ROW_SELECT = """
    SELECT id, job_id AS "jobId", row_number AS "rowNumber", raw_data AS "rawData",
           classification, proposed_action AS "proposedAction", resolution,
           resolved_by AS "resolvedBy", resolved_at AS "resolvedAt", match_details AS "matchDetails",
           matched_learner_id AS "matchedLearnerId", cohort_match_status AS "cohortMatchStatus",
           matched_cohort_id AS "matchedCohortId", errors, warnings,
           import_result AS "importResult", import_error AS "importError", created_at AS "createdAt"
    FROM learner_import_rows
"""


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_row_fields(row: dict[str, str]) -> list[str]:
    """Field-level validation mirroring LearnerInput's own rules, applied to
    the raw snake_case CSV row before any DB lookup -- a row that fails
    this is always classified 'invalid' regardless of duplicate status."""
    errors = []
    for col in LEARNER_IMPORT_REQUIRED_COLUMNS:
        if not row.get(col):
            errors.append(f"{col} is required")

    start_date = _parse_date(row.get("start_date"))
    if row.get("start_date") and start_date is None:
        errors.append("start_date must be in YYYY-MM-DD format")

    planned_end_date = None
    if row.get("planned_end_date"):
        planned_end_date = _parse_date(row["planned_end_date"])
        if planned_end_date is None:
            errors.append("planned_end_date must be in YYYY-MM-DD format")

    if start_date and planned_end_date and planned_end_date < start_date:
        errors.append("planned_end_date cannot be before start_date")

    return errors


def _normalise_name(first: str | None, last: str | None) -> str:
    return f"{(first or '').strip().lower()} {(last or '').strip().lower()}"


def load_existing_learners(cur) -> list[dict]:
    """One unfiltered load of the whole learners table per job -- matches
    this codebase's existing precedent (e.g. allocation_lib's cohort
    resolution, the legacy csv-preview endpoint) of loading a whole small
    table rather than querying per row."""
    cur.execute(
        """
        SELECT id, learner_ref AS "learnerRef", uln, email,
               first_name AS "firstName", last_name AS "lastName", start_date AS "startDate"
        FROM learners
        """
    )
    return cur.fetchall()


class ExistingLearnerIndex:
    """In-memory lookup structures built once per job from load_existing_learners."""

    def __init__(self, learners: list[dict]):
        self.by_id = {row["id"]: row for row in learners}
        self.by_ref: dict[str, dict] = {}
        self.by_uln: dict[str, dict] = {}
        self.by_email: dict[str, list[dict]] = {}
        self.by_name: dict[str, list[dict]] = {}
        for row in learners:
            self.by_ref[row["learnerRef"]] = row
            if row["uln"]:
                self.by_uln[row["uln"]] = row
            if row["email"]:
                self.by_email.setdefault(row["email"].strip().lower(), []).append(row)
            self.by_name.setdefault(_normalise_name(row["firstName"], row["lastName"]), []).append(row)


def _find_weak_match(index: ExistingLearnerIndex, row: dict[str, str]) -> dict | None:
    """'Possible duplicate' signal: no strong identifier matched, but
    exactly one existing learner shares the email, or shares a normalised
    name with a start_date within 7 days. Ambiguous weak signals (more than
    one candidate) are deliberately discarded rather than guessed -- every
    row is still shown to the admin, so silently under-flagging is a
    smaller cost than a wrong "possible duplicate" pairing would be."""
    candidates: dict[int, dict] = {}

    email = (row.get("email") or "").strip().lower()
    if email:
        matches = index.by_email.get(email, [])
        if len(matches) == 1:
            candidates[matches[0]["id"]] = matches[0]

    name_key = _normalise_name(row.get("first_name"), row.get("last_name"))
    row_start = _parse_date(row.get("start_date"))
    if name_key.strip() and row_start:
        for candidate in index.by_name.get(name_key, []):
            if candidate["startDate"] and abs((candidate["startDate"] - row_start).days) <= _POSSIBLE_DUPLICATE_START_DATE_WINDOW_DAYS:
                candidates[candidate["id"]] = candidate

    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return None


def classify_row(index: ExistingLearnerIndex, row: dict[str, str]) -> dict:
    """Classifies a single parsed CSV row against the existing learners
    table. Order matters: invalid short-circuits everything else; an
    identifier conflict (learner_reference points to learner A but uln
    points to a *different* learner B) takes priority over a plain
    exact-match so it is never silently treated as a safe update."""
    field_errors = validate_row_fields(row)
    if field_errors:
        return {
            "classification": "invalid",
            "proposedAction": "blocked",
            "matchedLearnerId": None,
            "matchDetails": {},
            "errors": field_errors,
            "warnings": [],
        }

    ref = row.get("learner_reference", "")
    uln = row.get("uln", "")

    ref_match = index.by_ref.get(ref)
    uln_match = index.by_uln.get(uln) if uln else None

    if ref_match and uln_match and uln_match["id"] != ref_match["id"]:
        return {
            "classification": "identifier_conflict",
            "proposedAction": "blocked",
            "matchedLearnerId": ref_match["id"],
            "matchDetails": {"learnerRefMatchId": ref_match["id"], "ulnMatchId": uln_match["id"]},
            "errors": ["learner_reference and uln point to different existing learners"],
            "warnings": [],
        }

    if ref_match:
        return {
            "classification": "exact_existing",
            "proposedAction": "skip",
            "matchedLearnerId": ref_match["id"],
            "matchDetails": {"matchedOn": "learner_reference"},
            "errors": [],
            "warnings": [],
        }

    if uln_match:
        return {
            "classification": "probable_duplicate",
            "proposedAction": "skip",
            "matchedLearnerId": uln_match["id"],
            "matchDetails": {"matchedOn": "uln"},
            "errors": [],
            "warnings": [],
        }

    weak = _find_weak_match(index, row)
    if weak:
        return {
            "classification": "possible_duplicate",
            "proposedAction": "skip",
            "matchedLearnerId": weak["id"],
            "matchDetails": {"matchedOn": "email_or_name_and_start_date"},
            "errors": [],
            "warnings": [],
        }

    return {
        "classification": "new",
        "proposedAction": "create",
        "matchedLearnerId": None,
        "matchDetails": {},
        "errors": [],
        "warnings": [],
    }


def classify_rows(cur, parsed_rows: list[dict[str, str]]) -> list[dict]:
    """Classifies every row in one pass against the DB, plus a
    within-file check: a second row reusing a learner_reference that a
    prior row in the *same file* already claimed as 'new' is reclassified
    as an identifier_conflict, since only the first occurrence could ever
    import cleanly (the second would collide with the first at INSERT
    time if both were allowed through as 'create')."""
    index = ExistingLearnerIndex(load_existing_learners(cur))

    results = []
    seen_refs_this_file: set[str] = set()
    for row in parsed_rows:
        result = classify_row(index, row)
        ref = row.get("learner_reference", "")
        if result["classification"] == "new" and ref:
            if ref in seen_refs_this_file:
                result = {
                    "classification": "identifier_conflict",
                    "proposedAction": "blocked",
                    "matchedLearnerId": None,
                    "matchDetails": {"duplicateWithinFile": True},
                    "errors": [f"learner_reference '{ref}' appears more than once in this file"],
                    "warnings": [],
                }
            else:
                seen_refs_this_file.add(ref)
        results.append(result)
    return results


def resolve_cohort_names(cur, names: list[str]) -> dict[str, dict]:
    """Batched, case-insensitive resolution of CSV cohort_name values to
    active cohorts. Returns a dict keyed by the original (trimmed) name the
    caller passed in, one entry per distinct non-empty name, each with a
    'status' of matched | zero_matches | ambiguous | inactive and a
    'cohort' row (only present when status == 'matched')."""
    distinct = sorted({n.strip() for n in names if n and n.strip()})
    if not distinct:
        return {}

    cur.execute(
        'SELECT id, name, tutor_id AS "tutorId", active FROM cohorts WHERE lower(name) = ANY(%s)',
        ([n.lower() for n in distinct],),
    )
    by_lower: dict[str, list[dict]] = {}
    for row in cur.fetchall():
        by_lower.setdefault(row["name"].strip().lower(), []).append(row)

    resolved = {}
    for name in distinct:
        matches = by_lower.get(name.lower(), [])
        if not matches:
            resolved[name] = {"status": "zero_matches", "cohort": None}
        elif len(matches) > 1:
            resolved[name] = {"status": "ambiguous", "cohort": None}
        elif not matches[0]["active"]:
            resolved[name] = {"status": "inactive", "cohort": None}
        else:
            resolved[name] = {"status": "matched", "cohort": matches[0]}
    return resolved


def expire_due_learner_import_jobs(cur, as_of: datetime | None = None) -> None:
    """Lazy sweep (same pattern as apply_due_scheduled_allocations):
    - Jobs stuck in 'importing' for too long (the process crashed
      mid-confirm -- a normal failure always reverts its own status) are
      reverted to 'ready' with an explanatory last_error, unifying crash
      recovery with an ordinary retry.
    - Jobs past their expires_at are hard-deleted (rows first, no FK to
      violate) -- only parsed JSONB rows are ever stored, so there is no
      uploaded file left to clean up alongside them.
    """
    as_of = as_of or datetime.now()
    stale_cutoff = as_of - timedelta(minutes=IMPORT_JOB_STALE_IMPORTING_MINUTES)
    cur.execute(
        """
        UPDATE learner_import_jobs
        SET status = 'ready', last_error = 'Import was interrupted and has been reset for retry.', updated_at = now()
        WHERE status = 'importing' AND started_importing_at < %s
        """,
        (stale_cutoff,),
    )

    cur.execute("SELECT id FROM learner_import_jobs WHERE expires_at < %s", (as_of,))
    expired_ids = [row["id"] for row in cur.fetchall()]
    if expired_ids:
        cur.execute("DELETE FROM learner_import_rows WHERE job_id = ANY(%s)", (expired_ids,))
        cur.execute("DELETE FROM learner_import_jobs WHERE id = ANY(%s)", (expired_ids,))


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
    """Classifies every row and persists the job header + one
    learner_import_rows row per CSV row in a single pass. Classification
    happens synchronously (no background worker exists in this app), so
    the job goes straight from 'uploaded' to 'ready' -- the 'classifying'
    status in the schema exists for forward-compatibility/observability,
    not because there is ever an observable window where a job sits in it.
    """
    index_rows = classify_rows(cur, parsed_rows)
    cohort_names = [row.get("cohort_name", "") for row in parsed_rows]
    cohort_resolution = resolve_cohort_names(cur, cohort_names)

    counts = {
        "new": 0,
        "exact_existing": 0,
        "probable_duplicate": 0,
        "possible_duplicate": 0,
        "identifier_conflict": 0,
        "invalid": 0,
    }
    for result in index_rows:
        counts[result["classification"]] += 1

    expires_at = datetime.now() + timedelta(hours=retention_hours)
    cur.execute(
        """
        INSERT INTO learner_import_jobs
            (filename, uploaded_by, status, total_rows, new_count, exact_existing_count,
             probable_duplicate_count, possible_duplicate_count, identifier_conflict_count,
             invalid_count, expires_at)
        VALUES (%s, %s, 'ready', %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            filename,
            uploaded_by,
            len(parsed_rows),
            counts["new"],
            counts["exact_existing"],
            counts["probable_duplicate"],
            counts["possible_duplicate"],
            counts["identifier_conflict"],
            counts["invalid"],
            expires_at,
        ),
    )
    job_id = cur.fetchone()["id"]

    for row_number, (raw_row, result) in enumerate(zip(parsed_rows, index_rows), start=1):
        cohort_name = (raw_row.get("cohort_name") or "").strip()
        cohort_status = None
        matched_cohort_id = None
        warnings = list(result["warnings"])
        if cohort_name:
            outcome = cohort_resolution.get(cohort_name, {"status": "zero_matches", "cohort": None})
            cohort_status = outcome["status"]
            if outcome["cohort"]:
                matched_cohort_id = outcome["cohort"]["id"]
            if cohort_status != "matched":
                warnings.append(f"cohort_name '{cohort_name}' could not be resolved ({cohort_status.replace('_', ' ')})")

        cur.execute(
            """
            INSERT INTO learner_import_rows
                (job_id, row_number, raw_data, classification, proposed_action, match_details,
                 matched_learner_id, cohort_match_status, matched_cohort_id, errors, warnings)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job_id,
                row_number,
                json.dumps(raw_row),
                result["classification"],
                result["proposedAction"],
                json.dumps(result["matchDetails"]),
                result["matchedLearnerId"],
                cohort_status,
                matched_cohort_id,
                json.dumps(result["errors"]),
                json.dumps(warnings),
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
    cur.execute(f"SELECT count(*)::int AS count FROM learner_import_rows {where}", params)
    total = cur.fetchone()["count"]

    _enrich_rows_with_names(cur, items)
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def _enrich_rows_with_names(cur, rows: list[dict]) -> None:
    """Adds matchedLearnerName/matchedCohortName to each row in place --
    an admin resolving a possible_duplicate is choosing whether to update
    *this specific person*, and a bare database id is meaningless for that
    decision. Batched (one query per id set across the whole page) rather
    than per-row, matching this codebase's existing enrichment precedent
    (allocation_lib.enrich_allocation_history)."""
    learner_ids = {r["matchedLearnerId"] for r in rows if r["matchedLearnerId"] is not None}
    cohort_ids = {r["matchedCohortId"] for r in rows if r["matchedCohortId"] is not None}

    learner_names: dict[int, str] = {}
    if learner_ids:
        cur.execute("SELECT id, first_name, last_name FROM learners WHERE id = ANY(%s)", (list(learner_ids),))
        learner_names = {row["id"]: f"{row['first_name']} {row['last_name']}" for row in cur.fetchall()}

    cohort_names: dict[int, str] = {}
    if cohort_ids:
        cur.execute("SELECT id, name FROM cohorts WHERE id = ANY(%s)", (list(cohort_ids),))
        cohort_names = {row["id"]: row["name"] for row in cur.fetchall()}

    for r in rows:
        r["matchedLearnerName"] = learner_names.get(r["matchedLearnerId"])
        r["matchedCohortName"] = cohort_names.get(r["matchedCohortId"])


def resolve_import_row(cur, job_id: int, row_id: int, resolution: str, resolved_by: int) -> dict:
    """Records the admin's explicit skip-or-update choice for one duplicate
    row. Only meaningful for a row that actually has a choice to make: a
    'new' row always creates, and a 'blocked' (invalid/identifier_conflict)
    row can never be actioned until the source file is fixed and
    re-uploaded -- resolving either is rejected rather than silently
    ignored, since 'silently ignored' is indistinguishable from a bug that
    dropped the admin's choice on the floor."""
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
        raise HTTPException(status_code=400, detail="New learners are always created -- there is nothing to resolve")

    cur.execute(
        """
        UPDATE learner_import_rows
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
        "UPDATE learner_import_jobs SET status = 'cancelled', updated_at = now() WHERE id = %s",
        (job_id,),
    )
    return get_import_job(cur, job_id)


# ---------------------------------------------------------------------------
# Confirm -- atomic, idempotent import.
# ---------------------------------------------------------------------------


def _row_to_field_kwargs(raw_data: dict[str, str], *, include_blanks: bool) -> dict:
    """Maps a raw snake_case CSV row to camelCase LearnerInput/LearnerUpdate
    kwargs, excluding cohort_name (handled separately via allocation, never
    passed to create/update) and, for updates, excluding blank values
    entirely so exclude_unset in _update_learner only touches fields the
    file actually provided -- a blank cell must never blank out existing
    data the file simply didn't mention."""
    kwargs = {}
    for csv_col, field in LEARNER_IMPORT_COLUMN_TO_FIELD.items():
        if field == "cohortName":
            continue
        value = (raw_data.get(csv_col) or "").strip()
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


def _maybe_allocate_new_learner(cur, learner: dict, row: dict, session: dict) -> None:
    """A freshly created learner is always safe to allocate -- tutor_id and
    cohort_id are NULL, so there is nothing to conflict with. Re-checks the
    cohort's current active state rather than trusting the classification
    snapshot, since time may have passed between preview and confirm; if
    the cohort is no longer valid the learner is simply left unallocated,
    not treated as an import failure."""
    if row["cohortMatchStatus"] != "matched" or not row["matchedCohortId"]:
        return
    cur.execute('SELECT id, tutor_id AS "tutorId", active FROM cohorts WHERE id = %s', (row["matchedCohortId"],))
    cohort = cur.fetchone()
    if not cohort or not cohort["active"]:
        return
    apply_transfer(cur, learner, cohort["tutorId"], cohort["id"], date.today(), "CSV import allocation", session["userId"])


def _import_one_row(cur, row: dict, request: Request, session: dict) -> dict:
    from .routers.learners import LearnerInput, LearnerUpdate, _create_learner, _update_learner

    action = _effective_action(row)

    if action == "skip":
        cur.execute("UPDATE learner_import_rows SET import_result = 'skipped' WHERE id = %s", (row["id"],))
        return {"rowId": row["id"], "result": "skipped"}

    if action == "create":
        payload = LearnerInput(**_row_to_field_kwargs(row["rawData"], include_blanks=True))
        created = _create_learner(cur, payload, request, session)
        _maybe_allocate_new_learner(cur, created, row, session)
        cur.execute(
            "UPDATE learner_import_rows SET import_result = 'created', matched_learner_id = %s WHERE id = %s",
            (created["id"], row["id"]),
        )
        return {"rowId": row["id"], "result": "created", "learnerId": created["id"]}

    # action == "update" -- never includes tutorId/cohortId (not in
    # LEARNER_IMPORT_COLUMN_TO_FIELD), so this can never transfer an
    # already-allocated learner; that guarantee lives entirely here.
    payload = LearnerUpdate(**_row_to_field_kwargs(row["rawData"], include_blanks=False))
    updated = _update_learner(cur, row["matchedLearnerId"], payload, request, session)
    cur.execute("UPDATE learner_import_rows SET import_result = 'updated' WHERE id = %s", (row["id"],))
    return {"rowId": row["id"], "result": "updated", "learnerId": updated["id"]}


def _build_result_summary(result_rows: list[dict]) -> dict:
    counts = {"created": 0, "updated": 0, "skipped": 0}
    for r in result_rows:
        counts[r["result"]] += 1
    return {"totalRows": len(result_rows), **counts}


def confirm_import_job(cur, job_id: int, request: Request, session: dict) -> dict:
    """Atomically claims and imports a job. Idempotent: confirming an
    already-completed job just returns the stored result again rather than
    re-running anything, so a client that retries after a dropped response
    (or a double-click) cannot double-import. Any failure during the
    per-row loop rolls back every write made during *this* confirm attempt
    and reverts the job to 'ready' with last_error set -- the job is never
    left partially imported; it is always either 'ready' (nothing applied
    yet, or a previous attempt fully rolled back) or 'completed' (fully
    applied)."""
    cur.execute(
        """
        UPDATE learner_import_jobs
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
            "UPDATE learner_import_jobs SET status = 'ready', last_error = %s, updated_at = now() WHERE id = %s",
            (str(exc.detail) if isinstance(exc, HTTPException) else str(exc), job_id),
        )
        raise

    summary = _build_result_summary(result_rows)
    cur.execute(
        """
        UPDATE learner_import_jobs
        SET status = 'completed', result_summary = %s, last_error = NULL, updated_at = now()
        WHERE id = %s
        """,
        (json.dumps(summary), job_id),
    )
    return summary
