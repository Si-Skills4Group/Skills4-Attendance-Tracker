from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_admin, require_auth
from ..audit import write_audit_log
from ..db import get_cursor

router = APIRouter(tags=["cohorts"])

COHORT_SELECT = """
    SELECT c.id, c.name, c.programme, c.level, c.tutor_id AS "tutorId",
           c.delivery_day AS "deliveryDay", c.session_start_time AS "sessionStartTime",
           c.session_end_time AS "sessionEndTime", c.start_date AS "startDate",
           c.end_date AS "endDate", c.active, c.external_system_id AS "externalSystemId",
           c.created_at AS "createdAt", c.updated_at AS "updatedAt",
           CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName"
    FROM cohorts c
    LEFT JOIN tutors t ON c.tutor_id = t.id
"""


class CohortInput(BaseModel):
    name: str = Field(min_length=1)
    programme: str = Field(min_length=1)
    level: str = Field(min_length=1)
    tutorId: int | None = None
    deliveryDay: str
    sessionStartTime: str = Field(min_length=1)
    sessionEndTime: str = Field(min_length=1)
    startDate: date
    endDate: date | None = None
    active: bool = True
    externalSystemId: str | None = None


class CohortUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    programme: str | None = Field(default=None, min_length=1)
    level: str | None = Field(default=None, min_length=1)
    tutorId: int | None = None
    deliveryDay: str | None = None
    sessionStartTime: str | None = Field(default=None, min_length=1)
    sessionEndTime: str | None = Field(default=None, min_length=1)
    startDate: date | None = None
    endDate: date | None = None
    active: bool | None = None
    externalSystemId: str | None = None


@router.get("/cohorts")
def list_cohorts(
    tutorId: int | None = None,
    active: bool | None = None,
    programme: str | None = None,
    session: dict = Depends(require_auth),
):
    clauses = []
    params: list = []
    if session.get("role") == "tutor" and session.get("tutorId"):
        clauses.append("c.tutor_id = %s")
        params.append(session["tutorId"])
    elif tutorId is not None:
        clauses.append("c.tutor_id = %s")
        params.append(tutorId)
    if active is not None:
        clauses.append("c.active = %s")
        params.append(active)
    if programme:
        clauses.append("c.programme = %s")
        params.append(programme)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT} {where}", params)
        return cur.fetchall()


@router.post("/cohorts", status_code=201)
def create_cohort(payload: CohortInput, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO cohorts (name, programme, level, tutor_id, delivery_day, session_start_time,
                                  session_end_time, start_date, end_date, active, external_system_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, *
            """,
            (
                payload.name,
                payload.programme,
                payload.level,
                payload.tutorId,
                payload.deliveryDay,
                payload.sessionStartTime,
                payload.sessionEndTime,
                payload.startDate,
                payload.endDate,
                payload.active,
                payload.externalSystemId,
            ),
        )
        created = cur.fetchone()
        cur.execute(f"{COHORT_SELECT} WHERE c.id = %s", (created["id"],))
        full = cur.fetchone()

    write_audit_log(request, action="create", entity_type="cohort", entity_id=created["id"], new_value=created)
    return full


@router.get("/cohorts/{cohort_id}")
def get_cohort(cohort_id: int, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT} WHERE c.id = %s", (cohort_id,))
        cohort = cur.fetchone()
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")
        if session.get("role") == "tutor" and cohort["tutorId"] != session.get("tutorId"):
            raise HTTPException(status_code=403, detail="Not allowed to view this cohort")

        cur.execute("SELECT count(*)::int AS count FROM learners WHERE cohort_id = %s", (cohort_id,))
        count = cur.fetchone()["count"]

    return {**cohort, "learnerCount": count}


@router.patch("/cohorts/{cohort_id}")
def update_cohort(cohort_id: int, payload: CohortUpdate, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM cohorts WHERE id = %s", (cohort_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Cohort not found")

        updates = payload.model_dump(exclude_unset=True)
        column_map = {
            "name": "name",
            "programme": "programme",
            "level": "level",
            "tutorId": "tutor_id",
            "deliveryDay": "delivery_day",
            "sessionStartTime": "session_start_time",
            "sessionEndTime": "session_end_time",
            "startDate": "start_date",
            "endDate": "end_date",
            "active": "active",
            "externalSystemId": "external_system_id",
        }
        set_clauses = [f"{column_map[k]} = %s" for k in updates]
        params = list(updates.values())
        if set_clauses:
            cur.execute(
                f"UPDATE cohorts SET {', '.join(set_clauses)} WHERE id = %s RETURNING id",
                [*params, cohort_id],
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Cohort not found")

        cur.execute(f"{COHORT_SELECT} WHERE c.id = %s", (cohort_id,))
        full = cur.fetchone()

    write_audit_log(
        request, action="update", entity_type="cohort", entity_id=cohort_id, previous_value=existing, new_value=full
    )
    return full


@router.get("/cohorts/{cohort_id}/learners")
def get_cohort_learners(cohort_id: int, session: dict = Depends(require_auth)):
    from ..learners_query import LEARNERS_WITH_NAMES_SELECT

    with get_cursor() as cur:
        cur.execute("SELECT tutor_id FROM cohorts WHERE id = %s", (cohort_id,))
        cohort = cur.fetchone()
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")
        if session.get("role") == "tutor" and cohort["tutor_id"] != session.get("tutorId"):
            raise HTTPException(status_code=403, detail="Not allowed to view this cohort")

        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.cohort_id = %s", (cohort_id,))
        return cur.fetchall()
