"""Faithful port of lib/attendance-data.ts -- shared attendance-record query
helpers with optional date-range filtering. Tutor attribution is via the
cohort's *current* tutor, not a historical snapshot (see note below)."""
from .db import get_cursor


def _date_filters(date_from: str | None, date_to: str | None, prefix: str = "") -> tuple[str, list]:
    clauses = []
    params: list = []
    if date_from:
        clauses.append(f"{prefix}session_date >= %s")
        params.append(date_from)
    if date_to:
        clauses.append(f"{prefix}session_date <= %s")
        params.append(date_to)
    return clauses, params


def get_records_for_learner(learner_id: int, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    clauses, params = _date_filters(date_from, date_to, "s.")
    where = " AND ".join(["ar.learner_id = %s", *clauses])
    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT ar.status, ar.hours_attended AS "hoursAttended",
                   s.planned_duration_hours AS "plannedDurationHours"
            FROM attendance_records ar
            JOIN attendance_sessions s ON ar.session_id = s.id
            WHERE {where}
            """,
            [learner_id, *params],
        )
        return cur.fetchall()


def get_records_for_cohort(cohort_id: int, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    clauses, params = _date_filters(date_from, date_to, "s.")
    where = " AND ".join(["s.cohort_id = %s", *clauses])
    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT ar.status, ar.hours_attended AS "hoursAttended", ar.learner_id AS "learnerId",
                   s.planned_duration_hours AS "plannedDurationHours"
            FROM attendance_records ar
            JOIN attendance_sessions s ON ar.session_id = s.id
            WHERE {where}
            """,
            [cohort_id, *params],
        )
        return cur.fetchall()


# Approximation: attributes attendance to a tutor via each session's cohort's
# *current* tutor assignment, not a historical snapshot at the time of the
# session. Acceptable for reporting purposes; allocation changes are tracked
# separately in learner_allocation_history for audit purposes.
def get_records_for_tutor(tutor_id: int, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    clauses, params = _date_filters(date_from, date_to, "s.")
    where = " AND ".join(["c.tutor_id = %s", *clauses])
    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT ar.status, ar.hours_attended AS "hoursAttended",
                   s.planned_duration_hours AS "plannedDurationHours", s.cohort_id AS "cohortId"
            FROM attendance_records ar
            JOIN attendance_sessions s ON ar.session_id = s.id
            JOIN cohorts c ON s.cohort_id = c.id
            WHERE {where}
            """,
            [tutor_id, *params],
        )
        return cur.fetchall()


def get_records_for_organisation(
    date_from: str | None = None, date_to: str | None = None, programme: str | None = None
) -> list[dict]:
    clauses, params = _date_filters(date_from, date_to, "s.")
    if programme:
        clauses.append("c.programme = %s")
        params.append(programme)
    where = " AND ".join(clauses) if clauses else "TRUE"
    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT ar.status, ar.hours_attended AS "hoursAttended",
                   s.planned_duration_hours AS "plannedDurationHours",
                   s.cohort_id AS "cohortId", c.programme
            FROM attendance_records ar
            JOIN attendance_sessions s ON ar.session_id = s.id
            JOIN cohorts c ON s.cohort_id = c.id
            WHERE {where}
            """,
            params,
        )
        return cur.fetchall()
