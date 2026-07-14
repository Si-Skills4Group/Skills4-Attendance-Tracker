from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_admin, require_auth, require_learner_access
from ..audit import write_audit_log
from ..csv_utils import (
    LEARNER_CSV_COLUMNS,
    ImportCsvInput,
    PreviewCsvInput,
    parse_csv_to_rows,
    stringify_rows_to_csv,
)
from ..db import get_cursor
from ..learners_query import LEARNERS_WITH_NAMES_SELECT

router = APIRouter(tags=["learners"])


class LearnerInput(BaseModel):
    learnerRef: str = Field(min_length=1)
    uln: str | None = None
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    email: str | None = None
    employer: str | None = None
    programme: str = Field(min_length=1)
    level: str = Field(min_length=1)
    startDate: date
    plannedEndDate: date | None = None
    status: str | None = None
    tutorId: int | None = None
    cohortId: int | None = None
    externalSystemId: str | None = None


class LearnerUpdate(BaseModel):
    learnerRef: str | None = Field(default=None, min_length=1)
    uln: str | None = None
    firstName: str | None = Field(default=None, min_length=1)
    lastName: str | None = Field(default=None, min_length=1)
    email: str | None = None
    employer: str | None = None
    programme: str | None = Field(default=None, min_length=1)
    level: str | None = Field(default=None, min_length=1)
    startDate: date | None = None
    plannedEndDate: date | None = None
    status: str | None = None
    tutorId: int | None = None
    cohortId: int | None = None
    externalSystemId: str | None = None


@router.get("/learners")
def list_learners(
    search: str | None = None,
    status: str | None = None,
    programme: str | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    page: int = 1,
    pageSize: int = 25,
    session: dict = Depends(require_auth),
):
    clauses = []
    params: list = []
    if session.get("role") == "tutor" and session.get("tutorId"):
        clauses.append("l.tutor_id = %s")
        params.append(session["tutorId"])
    if search:
        clauses.append("(l.first_name ILIKE %s OR l.last_name ILIKE %s OR l.learner_ref ILIKE %s)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if status:
        clauses.append("l.status = %s")
        params.append(status)
    if programme:
        clauses.append("l.programme = %s")
        params.append(programme)
    if tutorId is not None:
        clauses.append("l.tutor_id = %s")
        params.append(tutorId)
    if cohortId is not None:
        clauses.append("l.cohort_id = %s")
        params.append(cohortId)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as cur:
        cur.execute(
            f"{LEARNERS_WITH_NAMES_SELECT} {where} LIMIT %s OFFSET %s",
            [*params, pageSize, (page - 1) * pageSize],
        )
        items = cur.fetchall()
        cur.execute(f"SELECT count(*)::int AS count FROM learners l {where}", params)
        total = cur.fetchone()["count"]

    return {"items": items, "total": total, "page": page, "pageSize": pageSize}


@router.post("/learners", status_code=201)
def create_learner(payload: LearnerInput, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT id FROM learners WHERE learner_ref = %s", (payload.learnerRef,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="A learner with this reference already exists")

        cur.execute(
            """
            INSERT INTO learners (learner_ref, uln, first_name, last_name, email, employer, programme, level,
                                   start_date, planned_end_date, status, tutor_id, cohort_id, external_system_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, *
            """,
            (
                payload.learnerRef,
                payload.uln,
                payload.firstName,
                payload.lastName,
                payload.email,
                payload.employer,
                payload.programme,
                payload.level,
                payload.startDate,
                payload.plannedEndDate,
                payload.status or "active",
                payload.tutorId,
                payload.cohortId,
                payload.externalSystemId,
            ),
        )
        created = cur.fetchone()

        write_audit_log(request, action="create", entity_type="learner", entity_id=created["id"], new_value=created)

        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s", (created["id"],))
        full = cur.fetchone()

    return full


@router.get("/learners/csv-template")
def get_csv_template(_session: dict = Depends(require_admin)):
    csv_text = stringify_rows_to_csv([], LEARNER_CSV_COLUMNS)
    return {"csv": csv_text, "filename": "learner-import-template.csv"}


@router.post("/learners/csv-preview")
def preview_csv(payload: PreviewCsvInput, _session: dict = Depends(require_admin)):
    try:
        parsed_rows = parse_csv_to_rows(payload.csv)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse CSV content")

    with get_cursor() as cur:
        cur.execute("SELECT learner_ref AS ref, uln, email FROM learners")
        existing = cur.fetchall()

    ref_set = {r["ref"] for r in existing}
    uln_set = {r["uln"] for r in existing if r["uln"]}
    email_set = {r["email"] for r in existing if r["email"]}

    seen_refs: set[str] = set()
    rows = []
    for index, data in enumerate(parsed_rows):
        row_number = index + 1
        errors = []
        if not data.get("learnerRef"):
            errors.append("learnerRef is required")
        if not data.get("firstName"):
            errors.append("firstName is required")
        if not data.get("lastName"):
            errors.append("lastName is required")
        if not data.get("programme"):
            errors.append("programme is required")
        if not data.get("level"):
            errors.append("level is required")
        if not data.get("startDate"):
            errors.append("startDate is required")

        is_duplicate = False
        duplicate_reason = None
        ref = data.get("learnerRef")
        if ref and ref in ref_set:
            is_duplicate = True
            duplicate_reason = "learnerRef already exists"
        elif ref and ref in seen_refs:
            is_duplicate = True
            duplicate_reason = "duplicate learnerRef within this file"
        elif data.get("uln") and data["uln"] in uln_set:
            is_duplicate = True
            duplicate_reason = "ULN already exists"
        elif data.get("email") and data["email"] in email_set:
            is_duplicate = True
            duplicate_reason = "email already exists"
        if ref:
            seen_refs.add(ref)

        rows.append(
            {
                "rowNumber": row_number,
                "data": data,
                "isDuplicate": is_duplicate,
                "duplicateReason": duplicate_reason,
                "errors": errors,
            }
        )

    total_rows = len(rows)
    invalid_rows = len([r for r in rows if r["errors"]])
    duplicate_rows = len([r for r in rows if r["isDuplicate"]])
    valid_rows = len([r for r in rows if not r["errors"] and not r["isDuplicate"]])

    return {
        "totalRows": total_rows,
        "validRows": valid_rows,
        "invalidRows": invalid_rows,
        "duplicateRows": duplicate_rows,
        "rows": rows,
    }


@router.post("/learners/csv-import")
def import_csv(payload: ImportCsvInput, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT learner_ref AS ref FROM learners")
        existing_refs = {r["ref"] for r in cur.fetchall()}

        imported = 0
        skipped = 0
        errors = []

        for index, row in enumerate(payload.rows):
            row_number = index + 1
            required = ["learnerRef", "firstName", "lastName", "programme", "level", "startDate"]
            if not all(row.get(f) for f in required):
                errors.append({"rowNumber": row_number, "field": None, "message": "Missing required field"})
                skipped += 1
                continue

            if row["learnerRef"] in existing_refs:
                errors.append(
                    {
                        "rowNumber": row_number,
                        "field": "learnerRef",
                        "message": "Learner reference already exists -- row skipped",
                    }
                )
                skipped += 1
                continue

            cur.execute(
                """
                INSERT INTO learners (learner_ref, uln, first_name, last_name, email, employer, programme, level,
                                       start_date, planned_end_date, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
                """,
                (
                    row["learnerRef"],
                    row.get("uln") or None,
                    row["firstName"],
                    row["lastName"],
                    row.get("email") or None,
                    row.get("employer") or None,
                    row["programme"],
                    row["level"],
                    row["startDate"],
                    row.get("plannedEndDate") or None,
                ),
            )
            existing_refs.add(row["learnerRef"])
            imported += 1

    write_audit_log(
        request, action="csv_import", entity_type="learner", new_value={"imported": imported, "skipped": skipped}
    )

    return {"imported": imported, "skipped": skipped, "errors": errors}


@router.get("/learners/{learner_id}")
def get_learner(learner_id: int, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s", (learner_id,))
        learner = cur.fetchone()
    if not learner:
        raise HTTPException(status_code=404, detail="Learner not found")
    if session.get("role") == "tutor" and learner["tutorId"] != session.get("tutorId"):
        raise HTTPException(status_code=403, detail="Not allowed to view this learner")
    return learner


@router.patch("/learners/{learner_id}")
def update_learner(learner_id: int, payload: LearnerUpdate, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM learners WHERE id = %s", (learner_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Learner not found")

        updates = payload.model_dump(exclude_unset=True)
        column_map = {
            "learnerRef": "learner_ref",
            "uln": "uln",
            "firstName": "first_name",
            "lastName": "last_name",
            "email": "email",
            "employer": "employer",
            "programme": "programme",
            "level": "level",
            "startDate": "start_date",
            "plannedEndDate": "planned_end_date",
            "status": "status",
            "tutorId": "tutor_id",
            "cohortId": "cohort_id",
            "externalSystemId": "external_system_id",
        }
        set_clauses = [f"{column_map[k]} = %s" for k in updates]
        params = list(updates.values())
        if set_clauses:
            cur.execute(
                f"UPDATE learners SET {', '.join(set_clauses)} WHERE id = %s RETURNING id",
                [*params, learner_id],
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Learner not found")

        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s", (learner_id,))
        full = cur.fetchone()

    write_audit_log(
        request, action="update", entity_type="learner", entity_id=learner_id, previous_value=existing, new_value=full
    )
    return full


@router.get("/learners/{learner_id}/allocation-history")
def get_learner_allocation_history(learner_id: int, session: dict = Depends(require_auth)):
    from ..allocation_lib import enrich_allocation_history

    with get_cursor() as cur:
        require_learner_access(cur, learner_id, session)
        cur.execute(
            """
            SELECT id, learner_id AS "learnerId", previous_tutor_id AS "previousTutorId",
                   new_tutor_id AS "newTutorId", previous_cohort_id AS "previousCohortId",
                   new_cohort_id AS "newCohortId", effective_date AS "effectiveDate",
                   transfer_reason AS "transferReason", changed_by AS "changedBy",
                   changed_date AS "changedDate"
            FROM learner_allocation_history WHERE learner_id = %s
            """,
            (learner_id,),
        )
        rows = cur.fetchall()

    return enrich_allocation_history(rows)
