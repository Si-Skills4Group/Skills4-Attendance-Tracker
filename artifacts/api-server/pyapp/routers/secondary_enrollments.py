"""Endpoints for Functional Skills secondary cohort enrollment. Thin
router -- all validation/mutation logic lives in secondary_enrollment_lib.py,
mirroring cover_tutor_lib.py's/allocation_routes.py's split.

Creating an enrollment, and the raw enrollment-list reads, stay admin-only
(allocation is an admin decision). Ending one (marking a learner as having
completed the course with this Functional Skills tutor) is the one write a
tutor also needs, scoped to their own cohort via the same require_cohort_access
rule as everywhere else -- never their uploaded Functional Skills
requirement (a separate table, untouched by this), only this specific
cohort's future roster."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..audit import write_audit_log
from ..auth import require_admin, require_auth, require_cohort_access
from ..db import get_cursor
from ..secondary_enrollment_lib import (
    ENROLLMENT_SELECT,
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
    enrollment_id: int, payload: SecondaryEnrollmentEndInput, request: Request, session: dict = Depends(require_auth)
):
    with get_cursor() as cur:
        if session.get("role") == "tutor":
            # Looked up BEFORE the actual end so an unauthorised tutor gets
            # a 403 for "not your cohort", not a 404 that would also leak
            # whether the enrollment id exists at all.
            cur.execute(f"{ENROLLMENT_SELECT} WHERE e.id = %s", (enrollment_id,))
            existing = cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Secondary enrollment not found")
            require_cohort_access(cur, existing["cohortId"], session)

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
