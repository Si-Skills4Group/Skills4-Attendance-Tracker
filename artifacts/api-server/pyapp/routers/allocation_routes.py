from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_admin
from ..audit import write_audit_log
from ..db import get_cursor
from ..learners_query import LEARNERS_WITH_NAMES_SELECT
from ..allocation_lib import enrich_allocation_history

router = APIRouter(tags=["allocation"])


class AllocationInput(BaseModel):
    learnerIds: list[int] = Field(min_length=1)
    tutorId: int | None = None
    cohortId: int | None = None
    effectiveDate: date
    transferReason: str | None = None


@router.get("/allocation/unallocated-learners")
def unallocated_learners(_session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.tutor_id IS NULL")
        return cur.fetchall()


@router.get("/allocation/by-tutor")
def allocation_by_tutor(_session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute('SELECT id, first_name AS "firstName", last_name AS "lastName" FROM tutors')
        tutors = cur.fetchall()
        cur.execute('SELECT id, name, tutor_id AS "tutorId" FROM cohorts')
        cohorts = cur.fetchall()
        cur.execute(LEARNERS_WITH_NAMES_SELECT)
        learners = cur.fetchall()

    result = []
    for tutor in tutors:
        tutor_cohorts = [c for c in cohorts if c["tutorId"] == tutor["id"]]
        cohort_groups = [
            {
                "cohortId": c["id"],
                "cohortName": c["name"],
                "learners": [l for l in learners if l["cohortId"] == c["id"]],
            }
            for c in tutor_cohorts
        ]
        direct_learners = [
            l for l in learners if l["tutorId"] == tutor["id"] and l["cohortId"] is None
        ]
        result.append(
            {
                "tutorId": tutor["id"],
                "tutorName": f"{tutor['firstName']} {tutor['lastName']}",
                "cohorts": cohort_groups,
                "unassignedCohortLearners": direct_learners,
            }
        )

    return result


@router.post("/allocation/allocate")
def allocate_learners(payload: AllocationInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute(
            'SELECT id, tutor_id AS "tutorId", cohort_id AS "cohortId" FROM learners WHERE id = ANY(%s)',
            (payload.learnerIds,),
        )
        learners = cur.fetchall()

        updated_ids = []
        for learner in learners:
            cur.execute(
                "UPDATE learners SET tutor_id = %s, cohort_id = %s WHERE id = %s",
                (payload.tutorId, payload.cohortId, learner["id"]),
            )
            cur.execute(
                """
                INSERT INTO learner_allocation_history
                    (learner_id, previous_tutor_id, new_tutor_id, previous_cohort_id, new_cohort_id,
                     effective_date, transfer_reason, changed_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    learner["id"],
                    learner["tutorId"],
                    payload.tutorId,
                    learner["cohortId"],
                    payload.cohortId,
                    payload.effectiveDate,
                    payload.transferReason,
                    session["userId"],
                ),
            )
            updated_ids.append(learner["id"])

    write_audit_log(
        request,
        action="allocate",
        entity_type="learner",
        new_value={"learnerIds": updated_ids, "tutorId": payload.tutorId, "cohortId": payload.cohortId},
    )
    return {"updated": len(updated_ids)}


@router.get("/allocation/history")
def allocation_history(
    learnerId: int | None = None, tutorId: int | None = None, _session: dict = Depends(require_admin)
):
    clauses = []
    params: list = []
    if learnerId is not None:
        clauses.append("learner_id = %s")
        params.append(learnerId)
    if tutorId is not None:
        clauses.append("new_tutor_id = %s")
        params.append(tutorId)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT id, learner_id AS "learnerId", previous_tutor_id AS "previousTutorId",
                   new_tutor_id AS "newTutorId", previous_cohort_id AS "previousCohortId",
                   new_cohort_id AS "newCohortId", effective_date AS "effectiveDate",
                   transfer_reason AS "transferReason", changed_by AS "changedBy",
                   changed_date AS "changedDate"
            FROM learner_allocation_history {where}
            """,
            params,
        )
        rows = cur.fetchall()

    return enrich_allocation_history(rows)
