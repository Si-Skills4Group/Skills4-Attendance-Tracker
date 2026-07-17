from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from ..attendance_metrics import Period, fetch_attendance_metrics, fetch_register_completion, resolve_period
from ..auth import require_auth, require_cohort_access, require_learner_access, require_tutor_access
from ..db import get_cursor

router = APIRouter(tags=["attendance-summary"])


def _resolve_period_or_400(period: Period, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    try:
        return resolve_period(period, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/attendance-summary/learners/{learner_id}")
def get_learner_attendance_summary(
    learner_id: int,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        require_learner_access(cur, learner_id, session)
        metrics = fetch_attendance_metrics(
            cur, scope="learner", scope_id=learner_id, period_start=period_start, period_end=period_end
        )
        completion = fetch_register_completion(
            cur, scope="learner", scope_id=learner_id, period_start=period_start, period_end=period_end
        )
    return {"metrics": metrics, "registerCompletion": completion}


@router.get("/attendance-summary/cohorts/{cohort_id}")
def get_cohort_attendance_summary(
    cohort_id: int,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        require_cohort_access(cur, cohort_id, session)
        metrics = fetch_attendance_metrics(
            cur, scope="cohort", scope_id=cohort_id, period_start=period_start, period_end=period_end
        )
        completion = fetch_register_completion(
            cur, scope="cohort", scope_id=cohort_id, period_start=period_start, period_end=period_end
        )
    return {"metrics": metrics, "registerCompletion": completion}


@router.get("/attendance-summary/tutors/{tutor_id}")
def get_tutor_attendance_summary(
    tutor_id: int,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        require_tutor_access(cur, tutor_id, session)
        metrics = fetch_attendance_metrics(
            cur, scope="tutor", scope_id=tutor_id, period_start=period_start, period_end=period_end
        )
        completion = fetch_register_completion(
            cur, scope="tutor", scope_id=tutor_id, period_start=period_start, period_end=period_end
        )
    return {"metrics": metrics, "registerCompletion": completion}
