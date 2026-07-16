from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from ..auth import require_admin, require_auth, require_learner_access
from ..audit import write_audit_log
from ..db import get_cursor
from ..learners_query import LEARNERS_WITH_NAMES_SELECT
from ..scheduled_allocations_lib import apply_due_scheduled_allocations

router = APIRouter(tags=["learners"])

LearnerStatus = Literal["active", "paused", "withdrawn", "completed"]


def _validate_learner_dates(
    start_date: date | None,
    planned_end_date: date | None,
    actual_end_date: date | None,
    withdrawal_date: date | None,
) -> None:
    if start_date and planned_end_date and planned_end_date < start_date:
        raise HTTPException(status_code=400, detail="plannedEndDate cannot be before startDate")
    if start_date and withdrawal_date and withdrawal_date < start_date:
        raise HTTPException(status_code=400, detail="withdrawalDate cannot be before startDate")
    if start_date and actual_end_date and actual_end_date < start_date:
        raise HTTPException(status_code=400, detail="actualEndDate cannot be before startDate")


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
    actualEndDate: date | None = None
    withdrawalDate: date | None = None
    status: LearnerStatus | None = None
    tutorId: int | None = None
    cohortId: int | None = None
    externalSystemId: str | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> "LearnerInput":
        _validate_learner_dates(self.startDate, self.plannedEndDate, self.actualEndDate, self.withdrawalDate)
        return self


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
    actualEndDate: date | None = None
    withdrawalDate: date | None = None
    status: LearnerStatus | None = None
    tutorId: int | None = None
    cohortId: int | None = None
    externalSystemId: str | None = None


class LearnerStatusChangeInput(BaseModel):
    status: LearnerStatus
    actualEndDate: date | None = None
    withdrawalDate: date | None = None
    reason: str | None = None


@router.get("/learners")
def list_learners(
    search: str | None = None,
    status: str | None = None,
    programme: str | None = None,
    level: str | None = None,
    employer: str | None = None,
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
    if level:
        clauses.append("l.level = %s")
        params.append(level)
    if employer:
        clauses.append("l.employer ILIKE %s")
        params.append(f"%{employer}%")
    if tutorId is not None:
        clauses.append("l.tutor_id = %s")
        params.append(tutorId)
    if cohortId is not None:
        clauses.append("l.cohort_id = %s")
        params.append(cohortId)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as cur:
        apply_due_scheduled_allocations(cur)
        cur.execute(
            f"{LEARNERS_WITH_NAMES_SELECT} {where} LIMIT %s OFFSET %s",
            [*params, pageSize, (page - 1) * pageSize],
        )
        items = cur.fetchall()
        cur.execute(f"SELECT count(*)::int AS count FROM learners l {where}", params)
        total = cur.fetchone()["count"]

    return {"items": items, "total": total, "page": page, "pageSize": pageSize}


def _create_learner(cur, payload: LearnerInput, request: Request, session: dict) -> dict:
    """Cursor-accepting internal shared with the CSV import confirm step
    (learner_imports.py), which needs this to run inside its own
    transaction rather than opening a separate pooled connection -- see
    routers/learner_imports.py for why that matters for atomicity."""
    cur.execute("SELECT id FROM learners WHERE learner_ref = %s", (payload.learnerRef,))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="A learner with this reference already exists")

    if payload.uln:
        cur.execute("SELECT id FROM learners WHERE uln = %s", (payload.uln,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="A learner with this ULN already exists")

    cur.execute(
        """
        INSERT INTO learners (learner_ref, uln, first_name, last_name, email, employer, programme, level,
                               start_date, planned_end_date, actual_end_date, withdrawal_date, status,
                               tutor_id, cohort_id, external_system_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, *
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
            payload.actualEndDate,
            payload.withdrawalDate,
            payload.status or "active",
            payload.tutorId,
            payload.cohortId,
            payload.externalSystemId,
        ),
    )
    created = cur.fetchone()

    write_audit_log(
        request, action="create", entity_type="learner", entity_id=created["id"], new_value=created, cur=cur
    )

    cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s", (created["id"],))
    return cur.fetchone()


@router.post("/learners", status_code=201)
def create_learner(payload: LearnerInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return _create_learner(cur, payload, request, session)


@router.get("/learners/{learner_id}")
def get_learner(learner_id: int, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        apply_due_scheduled_allocations(cur)
        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s", (learner_id,))
        learner = cur.fetchone()
    if not learner:
        raise HTTPException(status_code=404, detail="Learner not found")
    if session.get("role") == "tutor" and learner["tutorId"] != session.get("tutorId"):
        raise HTTPException(status_code=403, detail="Not allowed to view this learner")
    return learner


def _update_learner(cur, learner_id: int, payload: LearnerUpdate, request: Request, session: dict) -> dict:
    """Cursor-accepting internal shared with the CSV import confirm step --
    see _create_learner's docstring. Note: the import path never puts
    tutorId/cohortId into the LearnerUpdate it builds for an existing
    learner, so this function's own generic column handling doesn't need
    any import-specific carve-out to keep allocation changes out of CSV
    updates -- that guarantee lives entirely in the caller."""
    cur.execute("SELECT * FROM learners WHERE id = %s", (learner_id,))
    existing = cur.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Learner not found")

    updates = payload.model_dump(exclude_unset=True)

    _validate_learner_dates(
        updates.get("startDate", existing["start_date"]),
        updates.get("plannedEndDate", existing["planned_end_date"]),
        updates.get("actualEndDate", existing["actual_end_date"]),
        updates.get("withdrawalDate", existing["withdrawal_date"]),
    )

    if updates.get("uln") and updates["uln"] != existing["uln"]:
        cur.execute("SELECT id FROM learners WHERE uln = %s AND id != %s", (updates["uln"], learner_id))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="A learner with this ULN already exists")
    if updates.get("learnerRef") and updates["learnerRef"] != existing["learner_ref"]:
        cur.execute("SELECT id FROM learners WHERE learner_ref = %s AND id != %s", (updates["learnerRef"], learner_id))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="A learner with this reference already exists")

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
        "actualEndDate": "actual_end_date",
        "withdrawalDate": "withdrawal_date",
        "status": "status",
        "tutorId": "tutor_id",
        "cohortId": "cohort_id",
        "externalSystemId": "external_system_id",
    }
    set_clauses = [f"{column_map[k]} = %s" for k in updates]
    params = list(updates.values())
    if set_clauses:
        cur.execute(
            f"UPDATE learners SET {', '.join(set_clauses)}, updated_at = now() WHERE id = %s RETURNING id",
            [*params, learner_id],
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Learner not found")

    cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s", (learner_id,))
    full = cur.fetchone()

    write_audit_log(
        request,
        action="update",
        entity_type="learner",
        entity_id=learner_id,
        previous_value=existing,
        new_value=full,
        cur=cur,
    )
    return full


@router.patch("/learners/{learner_id}")
def update_learner(learner_id: int, payload: LearnerUpdate, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return _update_learner(cur, learner_id, payload, request, session)


@router.post("/learners/{learner_id}/change-status")
def change_learner_status(
    learner_id: int, payload: LearnerStatusChangeInput, request: Request, _session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM learners WHERE id = %s", (learner_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Learner not found")

        actual_end_date = payload.actualEndDate or existing["actual_end_date"]
        withdrawal_date = payload.withdrawalDate or existing["withdrawal_date"]

        if payload.status == "withdrawn" and not withdrawal_date:
            raise HTTPException(status_code=400, detail="withdrawalDate is required when withdrawing a learner")
        if payload.status == "completed" and not actual_end_date:
            raise HTTPException(status_code=400, detail="actualEndDate is required when completing a learner")

        _validate_learner_dates(existing["start_date"], existing["planned_end_date"], actual_end_date, withdrawal_date)

        cur.execute(
            """
            UPDATE learners
            SET status = %s, actual_end_date = %s, withdrawal_date = %s, updated_at = now()
            WHERE id = %s
            """,
            (payload.status, actual_end_date, withdrawal_date, learner_id),
        )
        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s", (learner_id,))
        full = cur.fetchone()

    write_audit_log(
        request,
        action="change_status",
        entity_type="learner",
        entity_id=learner_id,
        previous_value={"status": existing["status"], "reason": None},
        new_value={"status": payload.status, "reason": payload.reason},
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
