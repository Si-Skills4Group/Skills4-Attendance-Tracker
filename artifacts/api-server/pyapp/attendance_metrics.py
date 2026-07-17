"""Phase 8 attendance-calculation engine.

This is a *new*, minutes-based, non-rounding formula living alongside (not
replacing) attendance_calc.py -- which keeps backing the existing, tested
/reports/* endpoints exactly as before. This module is the single source of
truth for every new dashboard/attendance-summary endpoint.

Unlike attendance_data.py's get_records_for_*() helpers (which only ever
select rows that already exist in attendance_records, and therefore cannot
represent "learner was expected but nothing has been recorded yet"), every
query here is built FROM session_expected_learners LEFT JOIN
attendance_records, so a missing register row is visible as
ar.status IS NULL and is tracked separately from absence -- never counted as
attended, and never counted as authorised/unauthorised absence either.

All bucketing happens in one aggregate SQL query per scope (learner / cohort
/ tutor / organisation) via SUM(CASE ...)/COUNT(...) FILTER (...) -- no raw
attendance rows are pulled into Python and summed here.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

UK_TZ = ZoneInfo("Europe/London")

Period = Literal["current_week", "current_month", "previous_month", "last_30_days", "custom"]
Scope = Literal["learner", "cohort", "tutor", "organisation"]

# A learner is only flagged as "low attendance" once there is enough
# recorded data to trust the percentage -- otherwise a learner with e.g. one
# absence and nothing else recorded yet would show as 0%. The brief permits
# "a conservative documented default" when no existing business rule
# specifies one; this is that default, and it lives in exactly one place.
MIN_COMPLETED_ROWS_FOR_ATTENDANCE_FLAG = 3


def uk_today() -> date:
    return datetime.now(UK_TZ).date()


def resolve_period(
    period: Period,
    date_from: date | None = None,
    date_to: date | None = None,
    today: date | None = None,
) -> tuple[date, date]:
    """UK-calendar-aware period resolution (Mon-Sun weeks, calendar months) --
    the existing dashboard.py rolling-7/30-day windows are UTC-anchored and
    don't line up with what a UK user means by "this week"/"this month"."""
    if period == "custom":
        if date_from is None or date_to is None:
            raise ValueError("custom period requires both date_from and date_to")
        if date_to < date_from:
            raise ValueError("date_to cannot be before date_from")
        return date_from, date_to

    today = today or uk_today()

    if period == "current_week":
        start = today.fromordinal(today.toordinal() - today.weekday())
        end = start.fromordinal(start.toordinal() + 6)
        return start, end

    if period == "current_month":
        start = today.replace(day=1)
        next_month = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        end = next_month.fromordinal(next_month.toordinal() - 1)
        return start, end

    if period == "previous_month":
        first_of_this_month = today.replace(day=1)
        end = first_of_this_month.fromordinal(first_of_this_month.toordinal() - 1)
        start = end.replace(day=1)
        return start, end

    if period == "last_30_days":
        return today.fromordinal(today.toordinal() - 29), today

    raise ValueError(f"Unknown period: {period}")


class AttendanceMetrics(BaseModel):
    periodStart: date
    periodEnd: date
    expectedMinutes: float
    attendedMinutes: float
    authorisedAbsenceMinutes: float
    authorisedAbsenceSessions: int
    unauthorisedAbsenceMinutes: float
    unauthorisedAbsenceSessions: int
    lateMinutes: int
    lateSessionCount: int
    averageMinutesLate: float | None
    missingRecordCount: int
    completedRegisterRowCount: int
    attendancePercentage: float | None
    attendanceDataCompleteness: float | None
    insufficientData: bool
    calculatedAt: datetime


class RegisterCompletionSummary(BaseModel):
    periodStart: date
    periodEnd: date
    notStarted: int
    inProgress: int
    completed: int
    locked: int
    outstanding: int
    completionPercentage: float | None


_METRICS_SELECT_COLUMNS = """
    COALESCE(SUM(CASE WHEN ar.status IN ('present', 'late')
                       THEN COALESCE(ar.hours_attended, 0) * 60 ELSE 0 END), 0)::float AS "attendedMinutes",
    COALESCE(SUM(CASE WHEN ar.status IS NULL OR ar.status NOT IN ('not_expected', 'withdrawn')
                       THEN s.planned_duration_hours * 60 ELSE 0 END), 0)::float AS "expectedMinutes",
    COALESCE(SUM(CASE WHEN ar.status = 'absent_authorised'
                       THEN s.planned_duration_hours * 60 ELSE 0 END), 0)::float AS "authorisedAbsenceMinutes",
    COALESCE(COUNT(*) FILTER (WHERE ar.status = 'absent_authorised'), 0) AS "authorisedAbsenceSessions",
    COALESCE(SUM(CASE WHEN ar.status = 'absent_unauthorised'
                       THEN s.planned_duration_hours * 60 ELSE 0 END), 0)::float AS "unauthorisedAbsenceMinutes",
    COALESCE(COUNT(*) FILTER (WHERE ar.status = 'absent_unauthorised'), 0) AS "unauthorisedAbsenceSessions",
    COALESCE(SUM(CASE WHEN ar.status = 'late' THEN ar.minutes_late ELSE 0 END), 0)::int AS "lateMinutes",
    COALESCE(COUNT(*) FILTER (WHERE ar.status = 'late'), 0) AS "lateSessionCount",
    COALESCE(COUNT(*) FILTER (WHERE ar.status IS NULL), 0) AS "missingRecordCount",
    COALESCE(COUNT(*) FILTER (WHERE ar.status IS NOT NULL AND ar.status NOT IN ('not_expected', 'withdrawn')), 0)
        AS "completedRegisterRowCount"
"""


def _row_to_metrics(row: dict, period_start: date, period_end: date) -> AttendanceMetrics:
    attended = row["attendedMinutes"]
    expected = row["expectedMinutes"]
    late_minutes = row["lateMinutes"]
    late_count = row["lateSessionCount"]
    missing = row["missingRecordCount"]
    completed_rows = row["completedRegisterRowCount"]
    applicable_rows = completed_rows + missing

    percentage = (attended / expected * 100) if expected > 0 else None
    completeness = (completed_rows / applicable_rows * 100) if applicable_rows > 0 else None
    average_late = (late_minutes / late_count) if late_count > 0 else None
    insufficient = expected <= 0 or completed_rows < MIN_COMPLETED_ROWS_FOR_ATTENDANCE_FLAG

    return AttendanceMetrics(
        periodStart=period_start,
        periodEnd=period_end,
        expectedMinutes=expected,
        attendedMinutes=attended,
        authorisedAbsenceMinutes=row["authorisedAbsenceMinutes"],
        authorisedAbsenceSessions=row["authorisedAbsenceSessions"],
        unauthorisedAbsenceMinutes=row["unauthorisedAbsenceMinutes"],
        unauthorisedAbsenceSessions=row["unauthorisedAbsenceSessions"],
        lateMinutes=late_minutes,
        lateSessionCount=late_count,
        averageMinutesLate=average_late,
        missingRecordCount=missing,
        completedRegisterRowCount=completed_rows,
        attendancePercentage=percentage,
        attendanceDataCompleteness=completeness,
        insufficientData=insufficient,
        calculatedAt=datetime.now(timezone.utc),
    )


def _scope_clause(scope: Scope, scope_id: int | None) -> tuple[str, list]:
    if scope == "learner":
        return "sel.learner_id = %s", [scope_id]
    if scope == "cohort":
        return "s.cohort_id = %s", [scope_id]
    if scope == "tutor":
        return "c.tutor_id = %s", [scope_id]
    if scope == "organisation":
        return "TRUE", []
    raise ValueError(f"Unknown scope: {scope}")


def fetch_attendance_metrics(
    cur,
    *,
    scope: Scope,
    scope_id: int | None,
    period_start: date,
    period_end: date,
    programme: str | None = None,
) -> AttendanceMetrics:
    scope_sql, scope_params = _scope_clause(scope, scope_id)
    clauses = [
        "s.status != 'cancelled'",
        "s.session_date >= %s",
        "s.session_date <= %s",
        scope_sql,
    ]
    params: list = [period_start, period_end, *scope_params]
    if programme:
        clauses.append("c.programme = %s")
        params.append(programme)

    cur.execute(
        f"""
        SELECT {_METRICS_SELECT_COLUMNS}
        FROM attendance_sessions s
        JOIN cohorts c ON s.cohort_id = c.id
        JOIN session_expected_learners sel ON sel.session_id = s.id
        LEFT JOIN attendance_records ar ON ar.session_id = sel.session_id AND ar.learner_id = sel.learner_id
        WHERE {' AND '.join(clauses)}
        """,
        params,
    )
    return _row_to_metrics(cur.fetchone(), period_start, period_end)


_GROUP_BY_COLUMN = {
    "learner": "sel.learner_id",
    "cohort": "s.cohort_id",
    "tutor": "c.tutor_id",
}


def fetch_attendance_metrics_grouped(
    cur,
    *,
    group_by: Literal["learner", "cohort", "tutor"],
    group_ids: list[int],
    period_start: date,
    period_end: date,
) -> dict[int, AttendanceMetrics]:
    """One aggregate query for many learners/cohorts/tutors at once, each
    bucketed via GROUP BY -- the batched twin of fetch_attendance_metrics,
    used wherever a dashboard needs a metric per-entity across a list
    (e.g. the low-attendance-learners list) instead of the N+1 pattern the
    old dashboard.py._low_attendance_learners used."""
    if not group_ids:
        return {}
    column = _GROUP_BY_COLUMN[group_by]
    cur.execute(
        f"""
        SELECT {column} AS "groupId", {_METRICS_SELECT_COLUMNS}
        FROM attendance_sessions s
        JOIN cohorts c ON s.cohort_id = c.id
        JOIN session_expected_learners sel ON sel.session_id = s.id
        LEFT JOIN attendance_records ar ON ar.session_id = sel.session_id AND ar.learner_id = sel.learner_id
        WHERE s.status != 'cancelled' AND s.session_date >= %s AND s.session_date <= %s
          AND {column} = ANY(%s)
        GROUP BY {column}
        """,
        [period_start, period_end, group_ids],
    )
    results = {row["groupId"]: _row_to_metrics(row, period_start, period_end) for row in cur.fetchall()}
    # Entities with zero matching rows (e.g. a learner with no expected
    # sessions at all in this period) get an explicit zero/insufficient
    # entry rather than silently disappearing from the result.
    empty = _row_to_metrics(
        {
            "attendedMinutes": 0.0, "expectedMinutes": 0.0, "authorisedAbsenceMinutes": 0.0,
            "authorisedAbsenceSessions": 0, "unauthorisedAbsenceMinutes": 0.0, "unauthorisedAbsenceSessions": 0,
            "lateMinutes": 0, "lateSessionCount": 0, "missingRecordCount": 0, "completedRegisterRowCount": 0,
        },
        period_start,
        period_end,
    )
    for group_id in group_ids:
        results.setdefault(group_id, empty)
    return results


def _register_completion_scope_clause(scope: Scope, scope_id: int | None) -> tuple[str, list]:
    """Like _scope_clause, but for a query whose FROM clause is
    attendance_sessions JOIN cohorts only (no session_expected_learners) --
    "learner" scope can't reference sel.learner_id directly here without
    joining sel and multiplying each session row by its expected-learner
    count, which would corrupt the not-started/in-progress/completed
    counts below. An EXISTS subquery filters sessions without that."""
    if scope == "learner":
        return "EXISTS (SELECT 1 FROM session_expected_learners sel WHERE sel.session_id = s.id AND sel.learner_id = %s)", [scope_id]
    return _scope_clause(scope, scope_id)


def fetch_register_completion(
    cur,
    *,
    scope: Scope,
    scope_id: int | None,
    period_start: date,
    period_end: date,
) -> RegisterCompletionSummary:
    scope_sql, scope_params = _register_completion_scope_clause(scope, scope_id)
    params: list = [period_start, period_end, *scope_params]

    # Mirrors routers/attendance.py::_compute_register_status's branch order
    # exactly (cancelled sessions are excluded from this query's WHERE
    # entirely, rather than branched on, since a cancelled register has no
    # meaningful completion state at all).
    cur.execute(
        f"""
        WITH session_counts AS (
            SELECT
                s.session_date,
                s.register_locked_at,
                (SELECT count(*)::int FROM attendance_records ar WHERE ar.session_id = s.id) AS recorded_count,
                (SELECT count(*)::int FROM session_expected_learners sel WHERE sel.session_id = s.id) AS expected_count
            FROM attendance_sessions s
            JOIN cohorts c ON s.cohort_id = c.id
            WHERE s.status != 'cancelled' AND s.session_date >= %s AND s.session_date <= %s AND {scope_sql}
        )
        SELECT
            count(*) FILTER (WHERE register_locked_at IS NULL AND recorded_count = 0) AS "notStarted",
            count(*) FILTER (
                WHERE register_locked_at IS NULL AND recorded_count > 0
                  AND NOT (expected_count > 0 AND recorded_count >= expected_count)
            ) AS "inProgress",
            count(*) FILTER (
                WHERE register_locked_at IS NULL AND expected_count > 0 AND recorded_count >= expected_count
            ) AS "completed",
            count(*) FILTER (WHERE register_locked_at IS NOT NULL) AS "locked",
            count(*) FILTER (
                WHERE register_locked_at IS NULL
                  AND NOT (expected_count > 0 AND recorded_count >= expected_count)
                  AND session_date <= CURRENT_DATE
            ) AS "outstanding",
            count(*) AS "total"
        FROM session_counts
        """,
        params,
    )
    row = cur.fetchone()
    total = row["total"]
    completion_pct = ((row["completed"] + row["locked"]) / total * 100) if total > 0 else None

    return RegisterCompletionSummary(
        periodStart=period_start,
        periodEnd=period_end,
        notStarted=row["notStarted"],
        inProgress=row["inProgress"],
        completed=row["completed"],
        locked=row["locked"],
        outstanding=row["outstanding"],
        completionPercentage=completion_pct,
    )


def is_low_attendance(metrics: AttendanceMetrics, threshold: float) -> bool:
    """Centralises the minimum-data guard so every dashboard/summary
    endpoint applies the exact same rule -- never flag on insufficient data,
    a zero-expected-minutes learner, or a percentage that doesn't exist."""
    if metrics.insufficientData or metrics.attendancePercentage is None:
        return False
    return metrics.attendancePercentage < threshold
