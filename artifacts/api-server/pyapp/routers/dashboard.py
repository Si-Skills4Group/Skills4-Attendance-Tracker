from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..allocation_lib import expected_learners_count_sql
from ..bud_progress import get_bud_progress_by_uln
from ..attendance_metrics import (
    Period,
    fetch_attendance_metrics,
    fetch_attendance_metrics_grouped,
    fetch_register_completion,
    is_low_attendance,
    resolve_period,
)
from ..auth import require_admin, require_auth
from ..db import get_cursor
from .cohorts import COHORT_SELECT
from .tutors import TUTOR_SELECT

router = APIRouter(tags=["dashboard"])


def _paginate(items: list, page: int, page_size: int) -> dict:
    start = (page - 1) * page_size
    return {"items": items[start : start + page_size], "total": len(items), "page": page, "pageSize": page_size}


def _low_attendance_rows(cur, learners: list[dict], threshold: float, period_start: date, period_end: date) -> list[dict]:
    """Batched (no N+1) replacement for the old per-learner-query
    implementation -- one aggregate query for every learner in `learners`,
    then filtered/shaped in Python from that single result set.

    Bud progress is looked up separately (one batched query keyed on ULN,
    see bud_progress.py) and merged in purely for display -- it never
    affects is_low_attendance's decision, and a learner with no ULN or no
    Bud match simply gets bud: None, never breaking this list."""
    learner_ids = [learner["id"] for learner in learners]
    metrics_by_learner = fetch_attendance_metrics_grouped(
        cur, group_by="learner", group_ids=learner_ids, period_start=period_start, period_end=period_end
    )
    flagged = [learner for learner in learners if is_low_attendance(metrics_by_learner[learner["id"]], threshold)]
    bud_by_uln = get_bud_progress_by_uln(cur, [learner.get("uln") for learner in flagged])

    rows = []
    for learner in flagged:
        rows.append(
            {
                "learnerId": learner["id"],
                "learnerName": f"{learner['firstName']} {learner['lastName']}",
                "learnerRef": learner["learnerRef"],
                "cohortName": learner.get("cohortName"),
                "metrics": metrics_by_learner[learner["id"]],
                "bud": bud_by_uln.get(learner.get("uln")),
            }
        )
    return rows


def _sessions_awaiting_completion(cur, cohort_ids: list[int] | None) -> list[dict]:
    today = date.today()
    clauses = ["s.session_date <= %s", "s.status != 'cancelled'", "s.deleted_at IS NULL", "c.deleted_at IS NULL"]
    params: list = [today]
    if cohort_ids is not None:
        clauses.append("s.cohort_id = ANY(%s)")
        params.append(cohort_ids)

    where = " AND ".join(clauses)
    # Expected learners are resolved as of each session's own date (not the
    # learner's current cohort) -- otherwise a learner who has since
    # transferred elsewhere would silently disappear from a past session's
    # outstanding-count calculation, or a past session could look
    # "complete" purely because its roster shrank after the fact.
    expected_sql = expected_learners_count_sql("s.cohort_id", "s.session_date")
    cur.execute(
        f"""
        SELECT s.id, s.cohort_id AS "cohortId", c.name AS "cohortName", s.session_date AS "sessionDate",
               CASE WHEN t.id IS NULL THEN 'Unassigned' ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName"
        FROM attendance_sessions s
        JOIN cohorts c ON s.cohort_id = c.id
        LEFT JOIN tutors t ON c.tutor_id = t.id
        WHERE {where}
          AND {expected_sql} > 0
          AND (
              SELECT count(*) FROM attendance_records ar WHERE ar.session_id = s.id
          ) < {expected_sql}
        ORDER BY s.session_date DESC
        """,
        params,
    )
    return cur.fetchall()


def _recently_edited(cur, cohort_ids: list[int] | None, limit: int = 10) -> list[dict]:
    clauses = ["s.deleted_at IS NULL", "c.deleted_at IS NULL", "l.deleted_at IS NULL"]
    params: list = []
    if cohort_ids is not None:
        clauses.append("s.cohort_id = ANY(%s)")
        params.append(cohort_ids)
    where = f"WHERE {' AND '.join(clauses)}"
    cur.execute(
        f"""
        SELECT ar.id, ar.session_id AS "sessionId", c.name AS "cohortName",
               concat(l.first_name, ' ', l.last_name) AS "learnerName", ar.status,
               CASE WHEN u.id IS NULL THEN 'Unknown' ELSE concat(u.first_name, ' ', u.last_name) END AS "editedBy",
               ar.updated_at AS "editedAt"
        FROM attendance_records ar
        JOIN attendance_sessions s ON ar.session_id = s.id
        JOIN cohorts c ON s.cohort_id = c.id
        JOIN learners l ON ar.learner_id = l.id
        LEFT JOIN users u ON ar.last_edited_by = u.id
        {where}
        ORDER BY ar.updated_at DESC
        LIMIT %s
        """,
        [*params, limit],
    )
    return cur.fetchall()


def _get_threshold(cur) -> float:
    cur.execute("SELECT low_attendance_threshold FROM app_settings LIMIT 1")
    settings_row = cur.fetchone()
    return float(settings_row["low_attendance_threshold"]) if settings_row else 85.0


def _resolve_period_or_400(period: Period, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    try:
        return resolve_period(period, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/dashboard/admin")
def get_admin_dashboard(_session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT count(*)::int AS count FROM learners WHERE status = 'active' AND deleted_at IS NULL")
        active_learners = cur.fetchone()["count"]
        cur.execute("SELECT count(*)::int AS count FROM tutors WHERE active = true")
        active_tutors = cur.fetchone()["count"]
        cur.execute("SELECT count(*)::int AS count FROM cohorts WHERE active = true AND deleted_at IS NULL")
        active_cohorts = cur.fetchone()["count"]

        # attendancePercentageWeek/Month are computed via the new minutes-
        # based, cancelled-session-excluding, UK-calendar-anchored engine
        # (current calendar week/month, not a rolling 7/30-day UTC window).
        week_start, week_end = resolve_period("current_week")
        month_start, month_end = resolve_period("current_month")
        pct_week = fetch_attendance_metrics(
            cur, scope="organisation", scope_id=None, period_start=week_start, period_end=week_end
        ).attendancePercentage
        pct_month = fetch_attendance_metrics(
            cur, scope="organisation", scope_id=None, period_start=month_start, period_end=month_end
        ).attendancePercentage

        sessions_awaiting = _sessions_awaiting_completion(cur, None)
        recent_edits = _recently_edited(cur, None)

        threshold = _get_threshold(cur)

        cur.execute(
            'SELECT l.id, l.first_name AS "firstName", l.last_name AS "lastName", '
            'l.learner_ref AS "learnerRef", l.uln, c.name AS "cohortName" '
            "FROM learners l LEFT JOIN cohorts c ON l.cohort_id = c.id "
            "WHERE l.status = 'active' AND l.deleted_at IS NULL"
        )
        active_learner_rows = cur.fetchall()
        low_attendance = _low_attendance_rows(cur, active_learner_rows, threshold, month_start, month_end)

    return {
        "activeLearners": active_learners,
        "activeTutors": active_tutors,
        "activeCohorts": active_cohorts,
        "attendancePercentageWeek": pct_week if pct_week is not None else 0.0,
        "attendancePercentageMonth": pct_month if pct_month is not None else 0.0,
        "sessionsAwaitingCompletion": sessions_awaiting,
        "recentlyEditedAttendance": recent_edits,
        "lowAttendanceLearners": low_attendance,
    }


@router.get("/dashboard/tutor")
def get_tutor_dashboard(session: dict = Depends(require_auth)):
    tutor_id = session.get("tutorId")
    if not tutor_id:
        raise HTTPException(status_code=403, detail="No tutor profile linked to this account")

    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT} WHERE c.tutor_id = %s AND c.deleted_at IS NULL", (tutor_id,))
        cohorts = cur.fetchall()
        cohort_ids = [c["id"] for c in cohorts]

        month_start, month_end = resolve_period("current_month")
        metrics_by_cohort = fetch_attendance_metrics_grouped(
            cur, group_by="cohort", group_ids=cohort_ids, period_start=month_start, period_end=month_end
        )

        cohort_summaries = []
        for cohort in cohorts:
            cur.execute(
                "SELECT count(*)::int AS count FROM learners WHERE cohort_id = %s AND deleted_at IS NULL", (cohort["id"],)
            )
            learner_count = cur.fetchone()["count"]
            metrics = metrics_by_cohort.get(cohort["id"])
            cohort_summaries.append(
                {
                    "cohort": cohort,
                    "learnerCount": learner_count,
                    "attendancePercentage": (metrics.attendancePercentage if metrics else None) or 0.0,
                }
            )

        next_session = None
        if cohort_ids:
            cur.execute(
                """
                SELECT s.id, s.cohort_id AS "cohortId", c.name AS "cohortName", s.session_date AS "sessionDate",
                       concat(t.first_name, ' ', t.last_name) AS "tutorName"
                FROM attendance_sessions s
                JOIN cohorts c ON s.cohort_id = c.id
                LEFT JOIN tutors t ON c.tutor_id = t.id
                WHERE s.cohort_id = ANY(%s) AND s.session_date >= CURRENT_DATE AND s.status != 'cancelled'
                  AND s.deleted_at IS NULL AND c.deleted_at IS NULL
                ORDER BY s.session_date ASC
                LIMIT 1
                """,
                (cohort_ids,),
            )
            next_session = cur.fetchone()

        sessions_awaiting = _sessions_awaiting_completion(cur, cohort_ids) if cohort_ids else []

        threshold = _get_threshold(cur)

        cur.execute(
            'SELECT l.id, l.first_name AS "firstName", l.last_name AS "lastName", '
            'l.learner_ref AS "learnerRef", l.uln, c.name AS "cohortName" '
            "FROM learners l LEFT JOIN cohorts c ON l.cohort_id = c.id "
            "WHERE l.tutor_id = %s AND l.status = 'active' AND l.deleted_at IS NULL",
            (tutor_id,),
        )
        active_learner_rows = cur.fetchall()
        low_attendance = _low_attendance_rows(cur, active_learner_rows, threshold, month_start, month_end)

    return {
        "cohorts": cohort_summaries,
        "nextSession": next_session,
        "sessionsAwaitingCompletion": sessions_awaiting,
        "lowAttendanceLearners": low_attendance,
    }


# ---------------------------------------------------------------------------
# Phase 8: detailed sub-endpoints, all period/date-range filterable.
# ---------------------------------------------------------------------------


@router.get("/dashboard/tutor/cohorts")
def get_tutor_dashboard_cohorts(
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    session: dict = Depends(require_auth),
):
    tutor_id = session.get("tutorId")
    if not tutor_id:
        raise HTTPException(status_code=403, detail="No tutor profile linked to this account")
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)

    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT} WHERE c.tutor_id = %s AND c.deleted_at IS NULL", (tutor_id,))
        cohorts = cur.fetchall()
        cohort_ids = [c["id"] for c in cohorts]
        threshold = _get_threshold(cur)

        metrics_by_cohort = fetch_attendance_metrics_grouped(
            cur, group_by="cohort", group_ids=cohort_ids, period_start=period_start, period_end=period_end
        )

        rows = []
        for cohort in cohorts:
            cur.execute(
                "SELECT count(*)::int AS count FROM learners WHERE cohort_id = %s AND status = 'active' AND deleted_at IS NULL",
                (cohort["id"],),
            )
            learner_count = cur.fetchone()["count"]
            cur.execute(
                """
                SELECT s.id, s.session_date AS "sessionDate"
                FROM attendance_sessions s
                WHERE s.cohort_id = %s AND s.session_date >= CURRENT_DATE AND s.status != 'cancelled'
                  AND s.deleted_at IS NULL
                ORDER BY s.session_date ASC LIMIT 1
                """,
                (cohort["id"],),
            )
            next_session = cur.fetchone()
            completion = fetch_register_completion(
                cur, scope="cohort", scope_id=cohort["id"], period_start=period_start, period_end=period_end
            )
            cur.execute(
                "SELECT id, first_name AS \"firstName\", last_name AS \"lastName\", learner_ref AS \"learnerRef\", uln "
                "FROM learners WHERE cohort_id = %s AND status = 'active' AND deleted_at IS NULL",
                (cohort["id"],),
            )
            cohort_learners = cur.fetchall()
            low_attendance_count = len(_low_attendance_rows(cur, cohort_learners, threshold, period_start, period_end))
            metrics = metrics_by_cohort.get(cohort["id"])
            rows.append(
                {
                    "cohort": cohort,
                    "activeLearnerCount": learner_count,
                    "nextSession": next_session,
                    "attendancePercentage": metrics.attendancePercentage if metrics else None,
                    "registerCompletion": completion,
                    "lowAttendanceLearnerCount": low_attendance_count,
                }
            )
    return rows


@router.get("/dashboard/tutor/outstanding-registers")
def get_tutor_outstanding_registers(page: int = 1, pageSize: Annotated[int, Query(ge=1, le=200)] = 25, session: dict = Depends(require_auth)):
    tutor_id = session.get("tutorId")
    if not tutor_id:
        raise HTTPException(status_code=403, detail="No tutor profile linked to this account")
    with get_cursor() as cur:
        cur.execute("SELECT id FROM cohorts WHERE tutor_id = %s AND deleted_at IS NULL", (tutor_id,))
        cohort_ids = [r["id"] for r in cur.fetchall()]
        rows = _sessions_awaiting_completion(cur, cohort_ids) if cohort_ids else []
    return _paginate(rows, page, pageSize)


@router.get("/dashboard/tutor/low-attendance-learners")
def get_tutor_low_attendance_learners(
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    session: dict = Depends(require_auth),
):
    tutor_id = session.get("tutorId")
    if not tutor_id:
        raise HTTPException(status_code=403, detail="No tutor profile linked to this account")
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        threshold = _get_threshold(cur)
        cur.execute(
            'SELECT l.id, l.first_name AS "firstName", l.last_name AS "lastName", '
            'l.learner_ref AS "learnerRef", l.uln, c.name AS "cohortName" '
            "FROM learners l LEFT JOIN cohorts c ON l.cohort_id = c.id "
            "WHERE l.tutor_id = %s AND l.status = 'active' AND l.deleted_at IS NULL",
            (tutor_id,),
        )
        learners = cur.fetchall()
        rows = _low_attendance_rows(cur, learners, threshold, period_start, period_end)
    return _paginate(rows, page, pageSize)


@router.get("/dashboard/admin/tutors")
def get_admin_dashboard_tutors(
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        cur.execute(f"{TUTOR_SELECT} WHERE active = true")
        tutors = cur.fetchall()
        tutor_ids = [t["id"] for t in tutors]
        threshold = _get_threshold(cur)

        metrics_by_tutor = fetch_attendance_metrics_grouped(
            cur, group_by="tutor", group_ids=tutor_ids, period_start=period_start, period_end=period_end
        )

        rows = []
        for tutor in tutors:
            cur.execute(
                "SELECT count(*)::int AS count FROM cohorts WHERE tutor_id = %s AND active = true AND deleted_at IS NULL",
                (tutor["id"],),
            )
            active_cohorts = cur.fetchone()["count"]
            cur.execute(
                "SELECT count(*)::int AS count FROM learners WHERE tutor_id = %s AND status = 'active' AND deleted_at IS NULL",
                (tutor["id"],),
            )
            active_learners = cur.fetchone()["count"]
            completion = fetch_register_completion(
                cur, scope="tutor", scope_id=tutor["id"], period_start=period_start, period_end=period_end
            )
            cur.execute(
                'SELECT id, first_name AS "firstName", last_name AS "lastName", learner_ref AS "learnerRef" '
                "FROM learners WHERE tutor_id = %s AND status = 'active' AND deleted_at IS NULL",
                (tutor["id"],),
            )
            tutor_learners = cur.fetchall()
            low_attendance_count = len(_low_attendance_rows(cur, tutor_learners, threshold, period_start, period_end))
            metrics = metrics_by_tutor.get(tutor["id"])
            rows.append(
                {
                    "tutorId": tutor["id"],
                    "tutorName": f"{tutor['firstName']} {tutor['lastName']}",
                    "activeCohorts": active_cohorts,
                    "activeLearners": active_learners,
                    "attendancePercentage": metrics.attendancePercentage if metrics else None,
                    "registerCompletion": completion,
                    "lowAttendanceLearnerCount": low_attendance_count,
                }
            )
    return _paginate(rows, page, pageSize)


@router.get("/dashboard/admin/cohorts")
def get_admin_dashboard_cohorts(
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        cur.execute(f"{COHORT_SELECT} WHERE c.active = true AND c.deleted_at IS NULL")
        cohorts = cur.fetchall()
        cohort_ids = [c["id"] for c in cohorts]

        metrics_by_cohort = fetch_attendance_metrics_grouped(
            cur, group_by="cohort", group_ids=cohort_ids, period_start=period_start, period_end=period_end
        )

        rows = []
        for cohort in cohorts:
            cur.execute(
                "SELECT count(*)::int AS count FROM learners WHERE cohort_id = %s AND status = 'active' AND deleted_at IS NULL",
                (cohort["id"],),
            )
            active_learners = cur.fetchone()["count"]
            completion = fetch_register_completion(
                cur, scope="cohort", scope_id=cohort["id"], period_start=period_start, period_end=period_end
            )
            metrics = metrics_by_cohort.get(cohort["id"])
            rows.append(
                {
                    "cohort": cohort,
                    "activeLearnerCount": active_learners,
                    "attendancePercentage": metrics.attendancePercentage if metrics else None,
                    "registerCompletion": completion,
                }
            )
    return _paginate(rows, page, pageSize)


@router.get("/dashboard/admin/outstanding-registers")
def get_admin_outstanding_registers(page: int = 1, pageSize: Annotated[int, Query(ge=1, le=200)] = 25, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        rows = _sessions_awaiting_completion(cur, None)
    return _paginate(rows, page, pageSize)


@router.get("/dashboard/admin/low-attendance-learners")
def get_admin_low_attendance_learners(
    period: Period = "current_month",
    dateFrom: date | None = None,
    dateTo: date | None = None,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    period_start, period_end = _resolve_period_or_400(period, dateFrom, dateTo)
    with get_cursor() as cur:
        threshold = _get_threshold(cur)
        cur.execute(
            'SELECT l.id, l.first_name AS "firstName", l.last_name AS "lastName", '
            'l.learner_ref AS "learnerRef", l.uln, c.name AS "cohortName" '
            "FROM learners l LEFT JOIN cohorts c ON l.cohort_id = c.id "
            "WHERE l.status = 'active' AND l.deleted_at IS NULL"
        )
        learners = cur.fetchall()
        rows = _low_attendance_rows(cur, learners, threshold, period_start, period_end)
    return _paginate(rows, page, pageSize)
