"""Phase 8/9 attendance-calculation engine.

This is the minutes-based, non-rounding formula that both the dashboards
(Phase 8) and the reporting module (Phase 9, routers/reports.py) are built
on -- attendance_calc.py/attendance_data.py (the old hours-based formula)
are no longer used by any endpoint after Phase 9's migration; this module is
the single source of truth for every dashboard/report/attendance-summary
endpoint, so dashboard and report totals reconcile under identical filters
by construction, not by coincidence.

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
    COALESCE(SUM(CASE WHEN ar.status IS NULL OR ar.status NOT IN ('not_expected', 'withdrawn', 'bil')
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
    COALESCE(COUNT(*) FILTER (WHERE ar.status IS NOT NULL AND ar.status NOT IN ('not_expected', 'withdrawn', 'bil')), 0)
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
        "s.deleted_at IS NULL",
        "c.deleted_at IS NULL",
        "l.deleted_at IS NULL",
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
        JOIN learners l ON l.id = sel.learner_id
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
    fixed_cohort_id: int | None = None,
) -> dict[int, AttendanceMetrics]:
    """One aggregate query for many learners/cohorts/tutors at once, each
    bucketed via GROUP BY -- the batched twin of fetch_attendance_metrics,
    used wherever a dashboard/report needs a metric per-entity across a
    list (e.g. the low-attendance-learners list, or a cohort report's
    per-learner breakdown) instead of the N+1 pattern the old
    dashboard.py._low_attendance_learners used.

    fixed_cohort_id (group_by="learner" only) scopes each learner's totals
    to sessions belonging to that one cohort -- without it, a learner's
    metrics would be their lifetime total across every cohort they've ever
    been expected in, which is the wrong question for "how did this
    learner do *in this cohort*"."""
    if not group_ids:
        return {}
    column = _GROUP_BY_COLUMN[group_by]
    extra_clause = ""
    extra_params: list = []
    if fixed_cohort_id is not None:
        if group_by != "learner":
            raise ValueError("fixed_cohort_id is only meaningful when group_by='learner'")
        extra_clause = " AND s.cohort_id = %s"
        extra_params = [fixed_cohort_id]
    cur.execute(
        f"""
        SELECT {column} AS "groupId", {_METRICS_SELECT_COLUMNS}
        FROM attendance_sessions s
        JOIN cohorts c ON s.cohort_id = c.id
        JOIN session_expected_learners sel ON sel.session_id = s.id
        JOIN learners l ON l.id = sel.learner_id
        LEFT JOIN attendance_records ar ON ar.session_id = sel.session_id AND ar.learner_id = sel.learner_id
        WHERE s.status != 'cancelled' AND s.deleted_at IS NULL AND c.deleted_at IS NULL AND l.deleted_at IS NULL
          AND s.session_date >= %s AND s.session_date <= %s
          AND {column} = ANY(%s){extra_clause}
        GROUP BY {column}
        """,
        [period_start, period_end, group_ids, *extra_params],
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


def _fetch_metrics_by_string_key(
    cur, *, key_sql: str, extra_join: str, period_start: date, period_end: date
) -> dict[str, AttendanceMetrics]:
    """Shared implementation behind the by-programme/by-level/by-employer
    organisation-report breakdowns -- same formula/columns as every other
    aggregate here, just GROUP BY a cohort/learner attribute string instead
    of an entity id, and returning every distinct value seen (not a
    pre-known id list, unlike fetch_attendance_metrics_grouped). Always
    joins learners (even for the programme/level breakdowns, which don't
    need it for the group key itself) so a deleted learner's minutes never
    contribute to any breakdown."""
    cur.execute(
        f"""
        SELECT {key_sql} AS "groupKey", {_METRICS_SELECT_COLUMNS}
        FROM attendance_sessions s
        JOIN cohorts c ON s.cohort_id = c.id
        JOIN session_expected_learners sel ON sel.session_id = s.id
        JOIN learners l ON l.id = sel.learner_id
        {extra_join}
        LEFT JOIN attendance_records ar ON ar.session_id = sel.session_id AND ar.learner_id = sel.learner_id
        WHERE s.status != 'cancelled' AND s.deleted_at IS NULL AND c.deleted_at IS NULL AND l.deleted_at IS NULL
          AND s.session_date >= %s AND s.session_date <= %s
        GROUP BY {key_sql}
        """,
        [period_start, period_end],
    )
    return {row["groupKey"]: _row_to_metrics(row, period_start, period_end) for row in cur.fetchall()}


def fetch_attendance_metrics_by_programme(cur, *, period_start: date, period_end: date) -> dict[str, AttendanceMetrics]:
    return _fetch_metrics_by_string_key(cur, key_sql="c.programme", extra_join="", period_start=period_start, period_end=period_end)


def fetch_attendance_metrics_by_level(cur, *, period_start: date, period_end: date) -> dict[str, AttendanceMetrics]:
    return _fetch_metrics_by_string_key(cur, key_sql="c.level", extra_join="", period_start=period_start, period_end=period_end)


def fetch_attendance_metrics_by_employer(cur, *, period_start: date, period_end: date) -> dict[str, AttendanceMetrics]:
    return _fetch_metrics_by_string_key(
        cur,
        key_sql="COALESCE(l.employer, 'Unspecified')",
        extra_join="",
        period_start=period_start,
        period_end=period_end,
    )


class AttendanceMetricsBucket(BaseModel):
    bucketStart: date
    bucketEnd: date
    metrics: AttendanceMetrics


def _bucket_end(bucket: Literal["week", "month"], bucket_start: date) -> date:
    if bucket == "week":
        return bucket_start.fromordinal(bucket_start.toordinal() + 6)
    next_month = (
        bucket_start.replace(year=bucket_start.year + 1, month=1)
        if bucket_start.month == 12
        else bucket_start.replace(month=bucket_start.month + 1)
    )
    return next_month.fromordinal(next_month.toordinal() - 1)


def fetch_attendance_metrics_by_period_bucket(
    cur,
    *,
    bucket: Literal["week", "month"],
    scope: Scope,
    scope_id: int | None,
    period_start: date,
    period_end: date,
) -> list[AttendanceMetricsBucket]:
    """The attendance-hours report's week/month grouping -- same formula,
    same SELECT columns as fetch_attendance_metrics, just GROUP BY a
    date_trunc'd bucket instead of an entity id. Buckets with zero expected
    minutes in range are omitted (there is nothing to report for a week/
    month that didn't happen), rather than padded with empty rows."""
    scope_sql, scope_params = _scope_clause(scope, scope_id)
    clauses = [
        "s.status != 'cancelled'",
        "s.deleted_at IS NULL",
        "c.deleted_at IS NULL",
        "l.deleted_at IS NULL",
        "s.session_date >= %s",
        "s.session_date <= %s",
        scope_sql,
    ]
    params: list = [period_start, period_end, *scope_params]

    cur.execute(
        f"""
        SELECT date_trunc(%s, s.session_date)::date AS "bucketStart", {_METRICS_SELECT_COLUMNS}
        FROM attendance_sessions s
        JOIN cohorts c ON s.cohort_id = c.id
        JOIN session_expected_learners sel ON sel.session_id = s.id
        JOIN learners l ON l.id = sel.learner_id
        LEFT JOIN attendance_records ar ON ar.session_id = sel.session_id AND ar.learner_id = sel.learner_id
        WHERE {' AND '.join(clauses)}
        GROUP BY 1
        ORDER BY 1
        """,
        [bucket, *params],
    )
    buckets = []
    for row in cur.fetchall():
        bucket_start = row["bucketStart"]
        metrics = _row_to_metrics(row, bucket_start, _bucket_end(bucket, bucket_start))
        buckets.append(AttendanceMetricsBucket(bucketStart=bucket_start, bucketEnd=_bucket_end(bucket, bucket_start), metrics=metrics))
    return buckets


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
            WHERE s.status != 'cancelled' AND s.deleted_at IS NULL AND c.deleted_at IS NULL
              AND s.session_date >= %s AND s.session_date <= %s AND {scope_sql}
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
