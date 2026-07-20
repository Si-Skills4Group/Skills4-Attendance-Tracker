"""Phase 9 reporting module.

Every calculation here is delegated to attendance_metrics.py (Phase 8) --
this file only does query/filter construction, permission checks, response
mapping, and (for exports) CSV formatting. No formula is duplicated: the
same fetch_attendance_metrics/fetch_register_completion calls that back the
dashboards back these reports too, so totals reconcile under identical
filters by construction.

attendance_calc.py/attendance_data.py (the old hours-based formula) are no
longer used by any endpoint in this file as of Phase 9.
"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from ..attendance_metrics import (
    AttendanceMetrics,
    Period,
    Scope,
    fetch_attendance_metrics,
    fetch_attendance_metrics_by_employer,
    fetch_attendance_metrics_by_level,
    fetch_attendance_metrics_by_period_bucket,
    fetch_attendance_metrics_by_programme,
    fetch_attendance_metrics_grouped,
    fetch_register_completion,
)
from ..auth import require_admin, require_auth, require_cohort_access, require_learner_access, require_tutor_access
from ..bud_progress import get_bud_progress_by_uln
from ..db import get_cursor
from ..learners_query import LEARNERS_WITH_NAMES_SELECT
from ..report_csv import BUD_COLUMN_PREFIX_MAP, export_csv_response, stream_report_csv, with_bud_columns
from ..report_rows import (
    RegisterStatusFilter,
    fetch_absence_rows,
    fetch_allocation_history_rows,
    fetch_lateness_rows,
    fetch_learner_session_history,
    fetch_register_completion_rows,
)
from .cohorts import COHORT_SELECT
from .dashboard import _get_threshold, _low_attendance_rows, _resolve_period_or_400
from .tutors import TUTOR_SELECT

router = APIRouter(tags=["reports"])

AttendanceHoursGroupBy = Literal["learner", "cohort", "tutor", "programme", "employer", "week", "month"]


# ---------------------------------------------------------------------------
# Shared permission-scoping / helpers
# ---------------------------------------------------------------------------


def _enforce_tutor_scope(
    cur, session: dict, tutor_id: int | None, cohort_id: int | None, learner_id: int | None
) -> tuple[int | None, int | None, int | None]:
    """Admins pass through untouched. A tutor session is always pinned to
    their own tutorId (rejecting an explicit request for someone else's),
    and any cohort/learner filter they supply is independently verified as
    theirs -- a tutor cannot widen their own report by omitting a filter,
    nor narrow into someone else's data by supplying one."""
    if session.get("role") != "tutor":
        return tutor_id, cohort_id, learner_id
    own_tutor_id = session.get("tutorId")
    if tutor_id is not None and tutor_id != own_tutor_id:
        raise HTTPException(status_code=403, detail="Not allowed to view another tutor's report")
    if cohort_id is not None:
        require_cohort_access(cur, cohort_id, session)
    if learner_id is not None:
        require_learner_access(cur, learner_id, session)
    return own_tutor_id, cohort_id, learner_id


def _pick_scope(tutor_id: int | None, cohort_id: int | None, learner_id: int | None) -> tuple[Scope, int | None]:
    if learner_id is not None:
        return "learner", learner_id
    if cohort_id is not None:
        return "cohort", cohort_id
    if tutor_id is not None:
        return "tutor", tutor_id
    return "organisation", None


def _flatten(row: dict, metrics: AttendanceMetrics) -> dict:
    return {**row, **metrics.model_dump()}


def _require_admin(session: dict, message: str = "Administrator access required") -> None:
    if session.get("role") != "admin":
        raise HTTPException(status_code=403, detail=message)


LEARNER_HISTORY_COLUMNS = [
    "sessionDate", "cohortName", "tutorName", "title", "plannedDurationHours",
    "status", "hoursAttended", "minutesLate", "registerStatus",
    *BUD_COLUMN_PREFIX_MAP.values(),
]

METRICS_ROW_COLUMNS = [
    "key", "label", "periodStart", "periodEnd", "expectedMinutes", "attendedMinutes",
    "attendancePercentage", "authorisedAbsenceMinutes", "authorisedAbsenceSessions",
    "unauthorisedAbsenceMinutes", "unauthorisedAbsenceSessions", "lateMinutes", "lateSessionCount",
    "averageMinutesLate", "missingRecordCount", "completedRegisterRowCount",
    "attendanceDataCompleteness", "insufficientData",
]

ABSENCE_COLUMNS = [
    "learnerName", "learnerRef", "sessionDate", "cohortName", "tutorName", "status",
    "plannedDurationHours", "employer",
]

LATENESS_COLUMNS = [
    "learnerName", "learnerRef", "sessionDate", "cohortName", "tutorName",
    "plannedStartTime", "minutesLate", "hoursAttended",
]

REGISTER_COMPLETION_COLUMNS = [
    "sessionId", "sessionDate", "title", "cohortName", "tutorName", "registerStatus",
    "expectedCount", "recordedCount", "missingRowCount", "completedAt", "completedByName",
    "registerLockedAt", "lockedByName", "outstandingDays",
]

ALLOCATION_HISTORY_COLUMNS = [
    "learnerName", "previousTutorName", "newTutorName", "previousCohortName", "newCohortName",
    "effectiveDate", "effectiveTo", "transferReason", "changedByName", "changedDate",
]


# ---------------------------------------------------------------------------
# Learner report
# ---------------------------------------------------------------------------


@router.get("/reports/learner/{learner_id}")
def get_learner_report(
    learner_id: int,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    page: int = 1,
    pageSize: int = 25,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        require_learner_access(cur, learner_id, session)
        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s AND l.deleted_at IS NULL", (learner_id,))
        learner = cur.fetchone()
        if not learner:
            raise HTTPException(status_code=404, detail="Learner not found")

        metrics = fetch_attendance_metrics(cur, scope="learner", scope_id=learner_id, period_start=period_start, period_end=period_end)
        completion = fetch_register_completion(cur, scope="learner", scope_id=learner_id, period_start=period_start, period_end=period_end)
        history_rows, history_total = fetch_learner_session_history(
            cur, learner_id=learner_id, period_start=period_start, period_end=period_end, page=page, page_size=pageSize
        )
        bud = get_bud_progress_by_uln(cur, [learner.get("uln")]).get(learner.get("uln"))

    return {
        "learner": learner,
        "metrics": metrics,
        "registerCompletion": completion,
        "bud": bud,
        "sessionHistory": {"items": history_rows, "total": history_total, "page": page, "pageSize": pageSize},
    }


@router.get("/reports/learner/{learner_id}/export")
def export_learner_report(
    request: Request,
    learner_id: int,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        require_learner_access(cur, learner_id, session)
        cur.execute(f"{LEARNERS_WITH_NAMES_SELECT} WHERE l.id = %s AND l.deleted_at IS NULL", (learner_id,))
        learner = cur.fetchone()
        if not learner:
            raise HTTPException(status_code=404, detail="Learner not found")
        history_rows, _ = fetch_learner_session_history(
            cur, learner_id=learner_id, period_start=period_start, period_end=period_end, page=1, page_size=10_000
        )
        bud = get_bud_progress_by_uln(cur, [learner.get("uln")]).get(learner.get("uln"))

    bud_dict = bud.model_dump() if bud else None
    rows = [with_bud_columns(row, bud_dict) for row in history_rows]
    return export_csv_response(
        request, report_type="learner", rows=rows, columns=LEARNER_HISTORY_COLUMNS,
        filename=f"learner-{learner_id}-report.csv", date_from=period_start, date_to=period_end,
        filters={"learnerId": learner_id, "period": period},
    )


# ---------------------------------------------------------------------------
# Cohort report
# ---------------------------------------------------------------------------


def _cohort_learner_breakdown(cur, cohort_id: int, period_start: date, period_end: date) -> list[dict]:
    # Learners whose historical attendance belongs to this cohort in the
    # period -- via session_expected_learners (the frozen snapshot), not
    # learners.cohort_id (current), so a transferred-away learner's past
    # sessions still attribute here, and a transferred-in learner's
    # pre-transfer sessions never do.
    cur.execute(
        """
        SELECT DISTINCT sel.learner_id
        FROM session_expected_learners sel
        JOIN attendance_sessions s ON s.id = sel.session_id
        JOIN learners l ON l.id = sel.learner_id
        WHERE s.cohort_id = %s AND s.session_date >= %s AND s.session_date <= %s AND s.status != 'cancelled'
          AND s.deleted_at IS NULL AND l.deleted_at IS NULL
        """,
        (cohort_id, period_start, period_end),
    )
    learner_ids = [r["learner_id"] for r in cur.fetchall()]
    if not learner_ids:
        return []
    metrics_by_learner = fetch_attendance_metrics_grouped(
        cur, group_by="learner", group_ids=learner_ids, period_start=period_start, period_end=period_end, fixed_cohort_id=cohort_id
    )
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", learner_ref AS "learnerRef" '
        "FROM learners WHERE id = ANY(%s) AND deleted_at IS NULL",
        (learner_ids,),
    )
    names = {r["id"]: r for r in cur.fetchall()}
    return [
        {
            "learnerId": lid,
            "learnerName": f"{names[lid]['firstName']} {names[lid]['lastName']}" if lid in names else "Unknown learner",
            "learnerRef": names.get(lid, {}).get("learnerRef"),
            "metrics": metrics_by_learner[lid],
        }
        for lid in learner_ids
    ]


@router.get("/reports/cohort/{cohort_id}")
def get_cohort_report(
    cohort_id: int,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    page: int = 1,
    pageSize: int = 25,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        require_cohort_access(cur, cohort_id, session)
        cur.execute(f"{COHORT_SELECT} WHERE c.id = %s AND c.deleted_at IS NULL", (cohort_id,))
        cohort = cur.fetchone()
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found")

        metrics = fetch_attendance_metrics(cur, scope="cohort", scope_id=cohort_id, period_start=period_start, period_end=period_end)
        completion = fetch_register_completion(cur, scope="cohort", scope_id=cohort_id, period_start=period_start, period_end=period_end)
        cur.execute(
            "SELECT count(*)::int AS count FROM learners WHERE cohort_id = %s AND status = 'active' AND deleted_at IS NULL",
            (cohort_id,),
        )
        active_learner_count = cur.fetchone()["count"]

        breakdown_all = _cohort_learner_breakdown(cur, cohort_id, period_start, period_end)

    total = len(breakdown_all)
    start = (page - 1) * pageSize
    breakdown_page = breakdown_all[start:start + pageSize]

    return {
        "cohort": cohort,
        "activeLearnerCount": active_learner_count,
        "metrics": metrics,
        "registerCompletion": completion,
        "learnerBreakdown": {"items": breakdown_page, "total": total, "page": page, "pageSize": pageSize},
    }


@router.get("/reports/cohort/{cohort_id}/export")
def export_cohort_report(
    request: Request,
    cohort_id: int,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        require_cohort_access(cur, cohort_id, session)
        cur.execute("SELECT id FROM cohorts WHERE id = %s AND deleted_at IS NULL", (cohort_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Cohort not found")
        breakdown = _cohort_learner_breakdown(cur, cohort_id, period_start, period_end)

    rows = [_flatten({"learnerId": r["learnerId"], "learnerName": r["learnerName"], "learnerRef": r["learnerRef"]}, r["metrics"]) for r in breakdown]
    columns = ["learnerId", "learnerName", "learnerRef", *METRICS_ROW_COLUMNS[2:]]
    return export_csv_response(
        request, report_type="cohort", rows=rows, columns=columns,
        filename=f"cohort-{cohort_id}-report.csv", date_from=period_start, date_to=period_end,
        filters={"cohortId": cohort_id, "period": period},
    )


# ---------------------------------------------------------------------------
# Tutor report
# ---------------------------------------------------------------------------


@router.get("/reports/tutor/{tutor_id}")
def get_tutor_report(
    tutor_id: int,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        require_tutor_access(cur, tutor_id, session)
        cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
        tutor = cur.fetchone()
        if not tutor:
            raise HTTPException(status_code=404, detail="Tutor not found")

        metrics = fetch_attendance_metrics(cur, scope="tutor", scope_id=tutor_id, period_start=period_start, period_end=period_end)
        completion = fetch_register_completion(cur, scope="tutor", scope_id=tutor_id, period_start=period_start, period_end=period_end)

        cur.execute(f"{COHORT_SELECT} WHERE c.tutor_id = %s AND c.deleted_at IS NULL", (tutor_id,))
        cohorts = cur.fetchall()
        metrics_by_cohort = fetch_attendance_metrics_grouped(
            cur, group_by="cohort", group_ids=[c["id"] for c in cohorts], period_start=period_start, period_end=period_end
        )
        cohort_breakdown = [{"cohort": c, "metrics": metrics_by_cohort[c["id"]]} for c in cohorts]

        cur.execute(
            "SELECT count(*)::int AS count FROM learners WHERE tutor_id = %s AND status = 'active' AND deleted_at IS NULL",
            (tutor_id,),
        )
        active_learners = cur.fetchone()["count"]

        threshold = _get_threshold(cur)
        cur.execute(
            'SELECT l.id, l.first_name AS "firstName", l.last_name AS "lastName", l.learner_ref AS "learnerRef", '
            'l.uln, c.name AS "cohortName" FROM learners l LEFT JOIN cohorts c ON l.cohort_id = c.id '
            "WHERE l.tutor_id = %s AND l.status = 'active' AND l.deleted_at IS NULL",
            (tutor_id,),
        )
        tutor_learners = cur.fetchall()
        low_attendance = _low_attendance_rows(cur, tutor_learners, threshold, period_start, period_end)

    return {
        "tutor": tutor,
        "activeCohorts": len(cohorts),
        "activeLearners": active_learners,
        "metrics": metrics,
        "registerCompletion": completion,
        "cohortBreakdown": cohort_breakdown,
        "lowAttendanceLearners": low_attendance,
    }


@router.get("/reports/tutor/{tutor_id}/export")
def export_tutor_report(
    request: Request,
    tutor_id: int,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        require_tutor_access(cur, tutor_id, session)
        cur.execute(f"{COHORT_SELECT} WHERE c.tutor_id = %s AND c.deleted_at IS NULL", (tutor_id,))
        cohorts = cur.fetchall()
        metrics_by_cohort = fetch_attendance_metrics_grouped(
            cur, group_by="cohort", group_ids=[c["id"] for c in cohorts], period_start=period_start, period_end=period_end
        )

    rows = [_flatten({"cohortId": c["id"], "cohortName": c["name"], "programme": c["programme"], "level": c["level"]}, metrics_by_cohort[c["id"]]) for c in cohorts]
    columns = ["cohortId", "cohortName", "programme", "level", *METRICS_ROW_COLUMNS[2:]]
    return export_csv_response(
        request, report_type="tutor", rows=rows, columns=columns,
        filename=f"tutor-{tutor_id}-report.csv", date_from=period_start, date_to=period_end,
        filters={"tutorId": tutor_id, "period": period},
    )


# ---------------------------------------------------------------------------
# Organisation report
# ---------------------------------------------------------------------------


def _organisation_breakdowns(cur, period_start: date, period_end: date) -> dict:
    cur.execute(f"{TUTOR_SELECT} WHERE active = true")
    tutors = cur.fetchall()
    metrics_by_tutor = fetch_attendance_metrics_grouped(
        cur, group_by="tutor", group_ids=[t["id"] for t in tutors], period_start=period_start, period_end=period_end
    )
    tutor_breakdown = [
        {"tutorId": t["id"], "tutorName": f"{t['firstName']} {t['lastName']}", "metrics": metrics_by_tutor[t["id"]]} for t in tutors
    ]

    cur.execute(f"{COHORT_SELECT} WHERE c.active = true AND c.deleted_at IS NULL")
    cohorts = cur.fetchall()
    metrics_by_cohort = fetch_attendance_metrics_grouped(
        cur, group_by="cohort", group_ids=[c["id"] for c in cohorts], period_start=period_start, period_end=period_end
    )
    cohort_breakdown = [{"cohort": c, "metrics": metrics_by_cohort[c["id"]]} for c in cohorts]

    programme_breakdown = [
        {"programme": k, "metrics": v} for k, v in fetch_attendance_metrics_by_programme(cur, period_start=period_start, period_end=period_end).items()
    ]
    level_breakdown = [
        {"level": k, "metrics": v} for k, v in fetch_attendance_metrics_by_level(cur, period_start=period_start, period_end=period_end).items()
    ]
    employer_breakdown = [
        {"employer": k, "metrics": v} for k, v in fetch_attendance_metrics_by_employer(cur, period_start=period_start, period_end=period_end).items()
    ]
    return {
        "tutorBreakdown": tutor_breakdown, "cohortBreakdown": cohort_breakdown,
        "programmeBreakdown": programme_breakdown, "levelBreakdown": level_breakdown, "employerBreakdown": employer_breakdown,
    }


@router.get("/reports/organisation")
def get_organisation_report(
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    programme: str | None = None,
    _session: dict = Depends(require_admin),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        cur.execute("SELECT count(*)::int AS count FROM learners WHERE status = 'active' AND deleted_at IS NULL")
        active_learners = cur.fetchone()["count"]
        cur.execute("SELECT count(*)::int AS count FROM tutors WHERE active = true")
        active_tutors = cur.fetchone()["count"]
        cur.execute("SELECT count(*)::int AS count FROM cohorts WHERE active = true AND deleted_at IS NULL")
        active_cohorts = cur.fetchone()["count"]
        cur.execute(
            "SELECT count(*)::int AS count FROM attendance_sessions "
            "WHERE session_date >= %s AND session_date <= %s AND status != 'cancelled' AND deleted_at IS NULL",
            (period_start, period_end),
        )
        sessions_in_period = cur.fetchone()["count"]

        metrics = fetch_attendance_metrics(cur, scope="organisation", scope_id=None, period_start=period_start, period_end=period_end, programme=programme)
        completion = fetch_register_completion(cur, scope="organisation", scope_id=None, period_start=period_start, period_end=period_end)
        breakdowns = _organisation_breakdowns(cur, period_start, period_end)

    return {
        "activeLearners": active_learners,
        "activeTutors": active_tutors,
        "activeCohorts": active_cohorts,
        "sessionsInPeriod": sessions_in_period,
        "metrics": metrics,
        "registerCompletion": completion,
        **breakdowns,
    }


@router.get("/reports/organisation/export")
def export_organisation_report(
    request: Request,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    breakdown: Literal["tutor", "cohort", "programme", "level", "employer"] = "tutor",
    _session: dict = Depends(require_admin),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        breakdowns = _organisation_breakdowns(cur, period_start, period_end)

    key_map = {
        "tutor": ("tutorBreakdown", lambda r: {"key": r["tutorId"], "label": r["tutorName"]}),
        "cohort": ("cohortBreakdown", lambda r: {"key": r["cohort"]["id"], "label": r["cohort"]["name"]}),
        "programme": ("programmeBreakdown", lambda r: {"key": r["programme"], "label": r["programme"]}),
        "level": ("levelBreakdown", lambda r: {"key": r["level"], "label": r["level"]}),
        "employer": ("employerBreakdown", lambda r: {"key": r["employer"], "label": r["employer"]}),
    }
    field, label_fn = key_map[breakdown]
    rows = [_flatten(label_fn(r), r["metrics"]) for r in breakdowns[field]]
    return export_csv_response(
        request, report_type="organisation", rows=rows, columns=METRICS_ROW_COLUMNS,
        filename=f"organisation-{breakdown}-report.csv", date_from=period_start, date_to=period_end,
        filters={"period": period, "breakdown": breakdown},
    )


# ---------------------------------------------------------------------------
# Absence report
# ---------------------------------------------------------------------------


@router.get("/reports/absence")
def get_absence_report(
    absenceType: Literal["authorised", "unauthorised"],
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    programme: str | None = None,
    level: str | None = None,
    employer: str | None = None,
    learnerId: int | None = None,
    page: int = 1,
    pageSize: int = 25,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    status = "absent_authorised" if absenceType == "authorised" else "absent_unauthorised"
    with get_cursor() as cur:
        tutor_id, cohort_id, learner_id = _enforce_tutor_scope(cur, session, tutorId, cohortId, learnerId)
        rows, total = fetch_absence_rows(
            cur, absence_type=status, period_start=period_start, period_end=period_end,
            tutor_id=tutor_id, cohort_id=cohort_id, programme=programme, level=level,
            employer=employer, learner_id=learner_id, page=page, page_size=pageSize,
        )
        scope, scope_id = _pick_scope(tutor_id, cohort_id, learner_id)
        metrics = fetch_attendance_metrics(cur, scope=scope, scope_id=scope_id, period_start=period_start, period_end=period_end, programme=programme)
    return {"items": rows, "total": total, "page": page, "pageSize": pageSize, "metrics": metrics}


@router.get("/reports/absence/export")
def export_absence_report(
    request: Request,
    absenceType: Literal["authorised", "unauthorised"],
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    programme: str | None = None,
    level: str | None = None,
    employer: str | None = None,
    learnerId: int | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    status = "absent_authorised" if absenceType == "authorised" else "absent_unauthorised"
    with get_cursor() as cur:
        tutor_id, cohort_id, learner_id = _enforce_tutor_scope(cur, session, tutorId, cohortId, learnerId)

    def fetch_page(cur, page, page_size):
        return fetch_absence_rows(
            cur, absence_type=status, period_start=period_start, period_end=period_end,
            tutor_id=tutor_id, cohort_id=cohort_id, programme=programme, level=level,
            employer=employer, learner_id=learner_id, page=page, page_size=page_size,
        )

    return stream_report_csv(
        request, report_type="absence", columns=ABSENCE_COLUMNS, filename=f"absence-{absenceType}-report.csv",
        fetch_page=fetch_page, date_from=period_start, date_to=period_end,
        filters={"absenceType": absenceType, "tutorId": tutor_id, "cohortId": cohort_id, "learnerId": learner_id, "programme": programme, "employer": employer},
    )


# ---------------------------------------------------------------------------
# Lateness report
# ---------------------------------------------------------------------------


@router.get("/reports/lateness")
def get_lateness_report(
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    programme: str | None = None,
    level: str | None = None,
    employer: str | None = None,
    learnerId: int | None = None,
    page: int = 1,
    pageSize: int = 25,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        tutor_id, cohort_id, learner_id = _enforce_tutor_scope(cur, session, tutorId, cohortId, learnerId)
        rows, total = fetch_lateness_rows(
            cur, period_start=period_start, period_end=period_end, tutor_id=tutor_id, cohort_id=cohort_id,
            programme=programme, level=level, employer=employer, learner_id=learner_id, page=page, page_size=pageSize,
        )
        scope, scope_id = _pick_scope(tutor_id, cohort_id, learner_id)
        metrics = fetch_attendance_metrics(cur, scope=scope, scope_id=scope_id, period_start=period_start, period_end=period_end, programme=programme)
    return {"items": rows, "total": total, "page": page, "pageSize": pageSize, "metrics": metrics}


@router.get("/reports/lateness/export")
def export_lateness_report(
    request: Request,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    programme: str | None = None,
    level: str | None = None,
    employer: str | None = None,
    learnerId: int | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        tutor_id, cohort_id, learner_id = _enforce_tutor_scope(cur, session, tutorId, cohortId, learnerId)

    def fetch_page(cur, page, page_size):
        return fetch_lateness_rows(
            cur, period_start=period_start, period_end=period_end, tutor_id=tutor_id, cohort_id=cohort_id,
            programme=programme, level=level, employer=employer, learner_id=learner_id, page=page, page_size=page_size,
        )

    return stream_report_csv(
        request, report_type="lateness", columns=LATENESS_COLUMNS, filename="lateness-report.csv",
        fetch_page=fetch_page, date_from=period_start, date_to=period_end,
        filters={"tutorId": tutor_id, "cohortId": cohort_id, "learnerId": learner_id, "programme": programme, "employer": employer},
    )


# ---------------------------------------------------------------------------
# Attendance-hours report
# ---------------------------------------------------------------------------


def _attendance_hours_items(cur, session: dict, *, groupBy: AttendanceHoursGroupBy, period_start: date, period_end: date, tutor_id, cohort_id):
    if groupBy in ("week", "month"):
        scope, scope_id = _pick_scope(tutor_id, cohort_id, None)
        buckets = fetch_attendance_metrics_by_period_bucket(cur, bucket=groupBy, scope=scope, scope_id=scope_id, period_start=period_start, period_end=period_end)
        return [{"key": str(b.bucketStart), "label": f"{b.bucketStart} - {b.bucketEnd}", "periodStart": b.bucketStart, "periodEnd": b.bucketEnd, "metrics": b.metrics} for b in buckets]

    if groupBy in ("programme", "employer", "tutor"):
        _require_admin(session, "Administrator access required for this grouping")
        if groupBy == "programme":
            data = fetch_attendance_metrics_by_programme(cur, period_start=period_start, period_end=period_end)
            return [{"key": k, "label": k, "metrics": v} for k, v in data.items()]
        if groupBy == "employer":
            data = fetch_attendance_metrics_by_employer(cur, period_start=period_start, period_end=period_end)
            return [{"key": k, "label": k, "metrics": v} for k, v in data.items()]
        cur.execute(f"{TUTOR_SELECT} WHERE active = true")
        tutors = cur.fetchall()
        data = fetch_attendance_metrics_grouped(cur, group_by="tutor", group_ids=[t["id"] for t in tutors], period_start=period_start, period_end=period_end)
        return [{"key": str(t["id"]), "label": f"{t['firstName']} {t['lastName']}", "metrics": data[t["id"]]} for t in tutors]

    if groupBy == "cohort":
        if cohort_id:
            cohort_ids = [cohort_id]
        elif tutor_id:
            cur.execute("SELECT id FROM cohorts WHERE tutor_id = %s AND deleted_at IS NULL", (tutor_id,))
            cohort_ids = [r["id"] for r in cur.fetchall()]
        else:
            cur.execute("SELECT id FROM cohorts WHERE active = true AND deleted_at IS NULL")
            cohort_ids = [r["id"] for r in cur.fetchall()]
        cur.execute("SELECT id, name FROM cohorts WHERE id = ANY(%s) AND deleted_at IS NULL", (cohort_ids,))
        cohorts = cur.fetchall()
        data = fetch_attendance_metrics_grouped(cur, group_by="cohort", group_ids=[c["id"] for c in cohorts], period_start=period_start, period_end=period_end)
        return [{"key": str(c["id"]), "label": c["name"], "metrics": data[c["id"]]} for c in cohorts]

    # groupBy == "learner"
    if cohort_id:
        cur.execute(
            'SELECT id, first_name AS "firstName", last_name AS "lastName" FROM learners '
            "WHERE cohort_id = %s AND status = %s AND deleted_at IS NULL", (cohort_id, "active"),
        )
    elif tutor_id:
        cur.execute(
            'SELECT id, first_name AS "firstName", last_name AS "lastName" FROM learners '
            "WHERE tutor_id = %s AND status = %s AND deleted_at IS NULL", (tutor_id, "active"),
        )
    else:
        raise HTTPException(status_code=400, detail="groupBy=learner requires a tutorId or cohortId filter")
    learners = cur.fetchall()
    data = fetch_attendance_metrics_grouped(
        cur, group_by="learner", group_ids=[l["id"] for l in learners], period_start=period_start, period_end=period_end, fixed_cohort_id=cohort_id
    )
    return [{"key": str(l["id"]), "label": f"{l['firstName']} {l['lastName']}", "metrics": data[l["id"]]} for l in learners]


@router.get("/reports/attendance-hours")
def get_attendance_hours_report(
    groupBy: AttendanceHoursGroupBy = "cohort",
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        tutor_id, cohort_id, _ = _enforce_tutor_scope(cur, session, tutorId, cohortId, None)
        items = _attendance_hours_items(cur, session, groupBy=groupBy, period_start=period_start, period_end=period_end, tutor_id=tutor_id, cohort_id=cohort_id)
    return {"groupBy": groupBy, "items": items}


@router.get("/reports/attendance-hours/export")
def export_attendance_hours_report(
    request: Request,
    groupBy: AttendanceHoursGroupBy = "cohort",
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        tutor_id, cohort_id, _ = _enforce_tutor_scope(cur, session, tutorId, cohortId, None)
        items = _attendance_hours_items(cur, session, groupBy=groupBy, period_start=period_start, period_end=period_end, tutor_id=tutor_id, cohort_id=cohort_id)

    rows = [_flatten({"key": i["key"], "label": i["label"]}, i["metrics"]) for i in items]
    columns = ["key", "label", *METRICS_ROW_COLUMNS[2:]]
    return export_csv_response(
        request, report_type="attendance-hours", rows=rows, columns=columns,
        filename=f"attendance-hours-{groupBy}-report.csv", date_from=period_start, date_to=period_end,
        filters={"groupBy": groupBy, "tutorId": tutor_id, "cohortId": cohort_id, "period": period},
    )


# ---------------------------------------------------------------------------
# Register-completion report
# ---------------------------------------------------------------------------


@router.get("/reports/register-completion")
def get_register_completion_report(
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    registerStatus: RegisterStatusFilter | None = None,
    overdueOnly: bool = False,
    page: int = 1,
    pageSize: int = 25,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        tutor_id, cohort_id, _ = _enforce_tutor_scope(cur, session, tutorId, cohortId, None)
        rows, total = fetch_register_completion_rows(
            cur, period_start=period_start, period_end=period_end, tutor_id=tutor_id, cohort_id=cohort_id,
            register_status=registerStatus, overdue_only=overdueOnly, page=page, page_size=pageSize,
        )
        scope, scope_id = _pick_scope(tutor_id, cohort_id, None)
        completion = fetch_register_completion(cur, scope=scope, scope_id=scope_id, period_start=period_start, period_end=period_end)
    return {"items": rows, "total": total, "page": page, "pageSize": pageSize, "registerCompletion": completion}


@router.get("/reports/register-completion/export")
def export_register_completion_report(
    request: Request,
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    registerStatus: RegisterStatusFilter | None = None,
    overdueOnly: bool = False,
    session: dict = Depends(require_auth),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        tutor_id, cohort_id, _ = _enforce_tutor_scope(cur, session, tutorId, cohortId, None)

    def fetch_page(cur, page, page_size):
        return fetch_register_completion_rows(
            cur, period_start=period_start, period_end=period_end, tutor_id=tutor_id, cohort_id=cohort_id,
            register_status=registerStatus, overdue_only=overdueOnly, page=page, page_size=page_size,
        )

    return stream_report_csv(
        request, report_type="register-completion", columns=REGISTER_COMPLETION_COLUMNS, filename="register-completion-report.csv",
        fetch_page=fetch_page, date_from=period_start, date_to=period_end,
        filters={"tutorId": tutor_id, "cohortId": cohort_id, "registerStatus": registerStatus, "overdueOnly": overdueOnly},
    )


# ---------------------------------------------------------------------------
# Allocation-history report (admin-only)
# ---------------------------------------------------------------------------


@router.get("/reports/allocation-history")
def get_allocation_history_report(
    learnerId: int | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    dateFrom: date | None = None,
    dateTo: date | None = None,
    page: int = 1,
    pageSize: int = 25,
    _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        rows, total = fetch_allocation_history_rows(
            cur, learner_id=learnerId, tutor_id=tutorId, cohort_id=cohortId, date_from=dateFrom, date_to=dateTo, page=page, page_size=pageSize
        )
    return {
        "items": rows, "total": total, "page": page, "pageSize": pageSize,
        "notice": "Cohort transfers change allocation prospectively and do not transfer historical attendance.",
    }


@router.get("/reports/allocation-history/export")
def export_allocation_history_report(
    request: Request,
    learnerId: int | None = None,
    tutorId: int | None = None,
    cohortId: int | None = None,
    dateFrom: date | None = None,
    dateTo: date | None = None,
    _session: dict = Depends(require_admin),
):
    def fetch_page(cur, page, page_size):
        return fetch_allocation_history_rows(
            cur, learner_id=learnerId, tutor_id=tutorId, cohort_id=cohortId, date_from=dateFrom, date_to=dateTo, page=page, page_size=page_size
        )

    return stream_report_csv(
        request, report_type="allocation-history", columns=ALLOCATION_HISTORY_COLUMNS, filename="allocation-history-report.csv",
        fetch_page=fetch_page, date_from=dateFrom, date_to=dateTo,
        filters={"learnerId": learnerId, "tutorId": tutorId, "cohortId": cohortId},
    )
