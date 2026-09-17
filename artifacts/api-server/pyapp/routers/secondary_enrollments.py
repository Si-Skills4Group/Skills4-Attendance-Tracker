"""Admin-only endpoints for Functional Skills secondary cohort enrollment.
Thin router -- all validation/mutation logic lives in
secondary_enrollment_lib.py, mirroring cover_tutor_lib.py's/
allocation_routes.py's split."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..audit import write_audit_log
from ..auth import require_admin
from ..db import get_cursor
from ..secondary_enrollment_lib import (
    enroll_learner_in_secondary_cohort,
    end_secondary_enrollment,
    list_secondary_enrollments_for_cohort,
    list_secondary_enrollments_for_learner,
)

router = APIRouter(tags=["secondary-enrollments"])


class SecondaryEnrollmentInput(BaseModel):
    cohortId: int
    enrolledDate: date
    reason: str | None = None


class SecondaryEnrollmentEndInput(BaseModel):
    endDate: date
    reason: str | None = None


@router.get("/learners/{learner_id}/secondary-enrollments")
def get_learner_secondary_enrollments(learner_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return list_secondary_enrollments_for_learner(cur, learner_id)


@router.post("/learners/{learner_id}/secondary-enrollments", status_code=201)
def create_learner_secondary_enrollment(
    learner_id: int, payload: SecondaryEnrollmentInput, request: Request, session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        cur.execute(
            'SELECT id, cohort_id AS "cohortId" FROM learners WHERE id = %s AND deleted_at IS NULL', (learner_id,)
        )
        learner = cur.fetchone()
        if not learner:
            raise HTTPException(status_code=404, detail="Learner not found")

        with cur.connection.transaction():
            enrollment = enroll_learner_in_secondary_cohort(
                cur, learner, payload.cohortId, payload.enrolledDate, payload.reason, session["userId"]
            )
            write_audit_log(
                request,
                action="create_secondary_enrollment",
                entity_type="learner",
                entity_id=learner_id,
                new_value=enrollment,
                cur=cur,
            )
    return enrollment


@router.post("/secondary-enrollments/{enrollment_id}/end")
def end_learner_secondary_enrollment(
    enrollment_id: int, payload: SecondaryEnrollmentEndInput, request: Request, session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        with cur.connection.transaction():
            enrollment = end_secondary_enrollment(
                cur, enrollment_id, payload.endDate, payload.reason, session["userId"]
            )
            write_audit_log(
                request,
                action="end_secondary_enrollment",
                entity_type="learner",
                entity_id=enrollment["learnerId"],
                new_value=enrollment,
                cur=cur,
            )
    return enrollment


@router.get("/cohorts/{cohort_id}/secondary-enrollments")
def get_cohort_secondary_enrollments(cohort_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return list_secondary_enrollments_for_cohort(cur, cohort_id)
