from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_admin
from ..audit import write_audit_log
from ..db import get_cursor
from ..allocation_lib import apply_transfer, enrich_allocation_history
from ..scheduled_allocations_lib import apply_due_scheduled_allocations

router = APIRouter(tags=["allocation"])


class AllocationInput(BaseModel):
    learnerIds: list[int] = Field(min_length=1)
    tutorId: int | None = None
    cohortId: int | None = None
    effectiveDate: date
    transferReason: str | None = None


def _ensure_tutor_active(cur, tutor_id: int | None) -> None:
    if tutor_id is None:
        return
    cur.execute("SELECT active FROM tutors WHERE id = %s", (tutor_id,))
    tutor = cur.fetchone()
    if not tutor:
        raise HTTPException(status_code=400, detail="Tutor not found")
    if not tutor["active"]:
        raise HTTPException(status_code=400, detail="Cannot allocate a learner to an inactive tutor")


def _ensure_cohort_active(cur, cohort_id: int | None) -> None:
    if cohort_id is None:
        return
    cur.execute("SELECT active FROM cohorts WHERE id = %s AND deleted_at IS NULL", (cohort_id,))
    cohort = cur.fetchone()
    if not cohort:
        raise HTTPException(status_code=400, detail="Cohort not found")
    if not cohort["active"]:
        raise HTTPException(status_code=400, detail="Cannot allocate a learner to an inactive cohort")


@router.post("/allocation/allocate")
def allocate_learners(payload: AllocationInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        apply_due_scheduled_allocations(cur)

        _ensure_tutor_active(cur, payload.tutorId)
        _ensure_cohort_active(cur, payload.cohortId)

        cur.execute(
            'SELECT id, tutor_id AS "tutorId", cohort_id AS "cohortId" FROM learners WHERE id = ANY(%s) AND deleted_at IS NULL',
            (payload.learnerIds,),
        )
        learners = cur.fetchall()
        missing = set(payload.learnerIds) - {learner["id"] for learner in learners}
        if missing:
            raise HTTPException(status_code=404, detail=f"Learner(s) not found: {sorted(missing)}")

        if payload.effectiveDate <= date.today():
            updated_ids = []
            for learner in learners:
                apply_transfer(
                    cur, learner, payload.tutorId, payload.cohortId, payload.effectiveDate,
                    payload.transferReason, session["userId"],
                )
                updated_ids.append(learner["id"])

            write_audit_log(
                request,
                action="allocate",
                entity_type="learner",
                new_value={"learnerIds": updated_ids, "tutorId": payload.tutorId, "cohortId": payload.cohortId},
            )
            return {"updated": len(updated_ids), "scheduled": 0}

        # Future-dated: don't touch learners yet -- the partial unique index
        # on scheduled_allocations enforces at most one pending transfer per
        # learner, so check for conflicts up front and reject cleanly rather
        # than let a bulk insert fail halfway through with a raw IntegrityError.
        cur.execute(
            "SELECT learner_id AS \"learnerId\" FROM scheduled_allocations WHERE learner_id = ANY(%s) AND status = 'pending'",
            (payload.learnerIds,),
        )
        conflicting = sorted(row["learnerId"] for row in cur.fetchall())
        if conflicting:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "learners_already_have_pending_transfer",
                    "learnerIds": conflicting,
                },
            )

        scheduled_ids = []
        for learner in learners:
            cur.execute(
                """
                INSERT INTO scheduled_allocations
                    (learner_id, new_tutor_id, new_cohort_id, effective_date, transfer_reason, created_by)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (
                    learner["id"],
                    payload.tutorId,
                    payload.cohortId,
                    payload.effectiveDate,
                    payload.transferReason,
                    session["userId"],
                ),
            )
            scheduled_ids.append(cur.fetchone()["id"])

    write_audit_log(
        request,
        action="schedule_transfer",
        entity_type="learner",
        new_value={
            "learnerIds": [learner["id"] for learner in learners],
            "tutorId": payload.tutorId,
            "cohortId": payload.cohortId,
            "effectiveDate": str(payload.effectiveDate),
        },
    )
    return {"updated": 0, "scheduled": len(scheduled_ids)}


@router.get("/allocation/scheduled")
def list_scheduled_allocations(learnerId: int | None = None, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        apply_due_scheduled_allocations(cur)

        clauses = ["status = 'pending'"]
        params: list = []
        if learnerId is not None:
            clauses.append("learner_id = %s")
            params.append(learnerId)

        cur.execute(
            f"""
            SELECT id, learner_id AS "learnerId", new_tutor_id AS "newTutorId",
                   new_cohort_id AS "newCohortId", effective_date AS "effectiveDate",
                   transfer_reason AS "transferReason", created_by AS "createdBy",
                   created_at AS "createdAt"
            FROM scheduled_allocations
            WHERE {' AND '.join(clauses)}
            ORDER BY effective_date, id
            """,
            params,
        )
        rows = cur.fetchall()

    # Reuse enrich_allocation_history's name-joining for display -- a pending
    # scheduled row has no "previous" side yet (nothing has changed), so those
    # fields are passed as None and only the destination/creator names resolve.
    return enrich_allocation_history(
        [
            {
                **row,
                "previousTutorId": None,
                "previousCohortId": None,
                "changedBy": row["createdBy"],
            }
            for row in rows
        ]
    )


@router.post("/allocation/scheduled/{scheduled_id}/cancel")
def cancel_scheduled_allocation(scheduled_id: int, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM scheduled_allocations WHERE id = %s", (scheduled_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Scheduled transfer not found")
        if existing["status"] != "pending":
            raise HTTPException(status_code=400, detail="Only a pending scheduled transfer can be cancelled")

        cur.execute(
            "UPDATE scheduled_allocations SET status = 'cancelled', cancelled_at = now(), cancelled_by = %s WHERE id = %s",
            (session["userId"], scheduled_id),
        )

    write_audit_log(
        request,
        action="cancel_scheduled_transfer",
        entity_type="learner",
        entity_id=existing["learner_id"],
        previous_value={"scheduledAllocationId": scheduled_id, "status": "pending"},
        new_value={"scheduledAllocationId": scheduled_id, "status": "cancelled"},
    )
    return {"cancelled": True}


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
