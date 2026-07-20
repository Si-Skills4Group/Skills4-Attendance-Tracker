"""Phase 9 row-level report queries.

attendance_metrics.py (Phase 8) answers "what are the totals" via aggregate
SQL; this module answers "show me the individual rows" for the reports that
need a listing rather than (or in addition to) a summary -- absence,
lateness, register completion, allocation history. Every summary/total
shown alongside these listings must still come from attendance_metrics.py;
nothing here recomputes a percentage or a total.

Every function returns (rows, total_count) for server-side pagination.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from .allocation_lib import enrich_allocation_history

AbsenceType = Literal["absent_authorised", "absent_unauthorised"]
RegisterStatusFilter = Literal["not_started", "in_progress", "completed", "locked", "cancelled"]


def _session_row_filters(
    *,
    tutor_id: int | None,
    cohort_id: int | None,
    programme: str | None,
    level: str | None,
    employer: str | None,
    learner_id: int | None,
) -> tuple[list[str], dict]:
    clauses = []
    params: dict = {}
    if tutor_id is not None:
        clauses.append("c.tutor_id = %(tutorId)s")
        params["tutorId"] = tutor_id
    if cohort_id is not None:
        clauses.append("c.id = %(cohortId)s")
        params["cohortId"] = cohort_id
    if programme:
        clauses.append("c.programme = %(programme)s")
        params["programme"] = programme
    if level:
        clauses.append("c.level = %(level)s")
        params["level"] = level
    if employer:
        clauses.append("l.employer = %(employer)s")
        params["employer"] = employer
    if learner_id is not None:
        clauses.append("l.id = %(learnerId)s")
        params["learnerId"] = learner_id
    return clauses, params


def fetch_learner_session_history(
    cur,
    *,
    learner_id: int,
    period_start: date,
    period_end: date,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int]:
    """One row per session this learner was ever expected at in the period
    (via session_expected_learners, so a missing/never-saved row still
    appears with status NULL rather than silently disappearing) -- each
    row's cohort/tutor is that *session's own* cohort (attendance_sessions.
    cohort_id never changes after creation), never the learner's current
    cohort, so a transfer never rewrites history here."""
    where = (
        "sel.learner_id = %(learnerId)s AND s.session_date >= %(periodStart)s "
        "AND s.session_date <= %(periodEnd)s AND s.status != 'cancelled'"
    )
    params = {"learnerId": learner_id, "periodStart": period_start, "periodEnd": period_end}

    cur.execute(
        f"""
        SELECT count(*) AS total
        FROM session_expected_learners sel
        JOIN attendance_sessions s ON s.id = sel.session_id
        WHERE {where}
        """,
        params,
    )
    total = cur.fetchone()["total"]

    cur.execute(
        f"""
        SELECT s.id AS "sessionId", s.session_date AS "sessionDate", s.title,
               s.planned_duration_hours AS "plannedDurationHours",
               c.id AS "cohortId", c.name AS "cohortName",
               CASE WHEN t.id IS NULL THEN 'Unassigned' ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
               ar.status, ar.hours_attended AS "hoursAttended", ar.minutes_late AS "minutesLate",
               s.register_locked_at AS "registerLockedAt",
               CASE
                   WHEN s.status = 'cancelled' THEN 'cancelled'
                   WHEN s.register_locked_at IS NOT NULL THEN 'locked'
                   WHEN (SELECT count(*) FROM attendance_records ar2 WHERE ar2.session_id = s.id) = 0 THEN 'not_started'
                   WHEN (SELECT count(*) FROM session_expected_learners sel2 WHERE sel2.session_id = s.id) > 0
                        AND (SELECT count(*) FROM attendance_records ar2 WHERE ar2.session_id = s.id)
                            >= (SELECT count(*) FROM session_expected_learners sel2 WHERE sel2.session_id = s.id)
                       THEN 'completed'
                   ELSE 'in_progress'
               END AS "registerStatus"
        FROM session_expected_learners sel
        JOIN attendance_sessions s ON s.id = sel.session_id
        JOIN cohorts c ON s.cohort_id = c.id
        LEFT JOIN tutors t ON c.tutor_id = t.id
        LEFT JOIN attendance_records ar ON ar.session_id = sel.session_id AND ar.learner_id = sel.learner_id
        WHERE {where}
        ORDER BY s.session_date DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    return cur.fetchall(), total


def fetch_absence_rows(
    cur,
    *,
    absence_type: AbsenceType,
    period_start: date,
    period_end: date,
    tutor_id: int | None = None,
    cohort_id: int | None = None,
    programme: str | None = None,
    level: str | None = None,
    employer: str | None = None,
    learner_id: int | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int]:
    extra_clauses, extra_params = _session_row_filters(
        tutor_id=tutor_id, cohort_id=cohort_id, programme=programme, level=level, employer=employer, learner_id=learner_id
    )
    where = " AND ".join(
        ["s.status != 'cancelled'", "s.session_date >= %(periodStart)s", "s.session_date <= %(periodEnd)s",
         "ar.status = %(absenceType)s", *extra_clauses]
    )
    params = {"periodStart": period_start, "periodEnd": period_end, "absenceType": absence_type, **extra_params}

    cur.execute(f"SELECT count(*) AS total FROM attendance_records ar "
                f"JOIN attendance_sessions s ON ar.session_id = s.id "
                f"JOIN cohorts c ON s.cohort_id = c.id "
                f"JOIN learners l ON ar.learner_id = l.id "
                f"WHERE {where}", params)
    total = cur.fetchone()["total"]

    cur.execute(
        f"""
        SELECT l.id AS "learnerId", concat(l.first_name, ' ', l.last_name) AS "learnerName",
               l.learner_ref AS "learnerRef", l.employer,
               s.id AS "sessionId", s.session_date AS "sessionDate",
               c.id AS "cohortId", c.name AS "cohortName",
               CASE WHEN t.id IS NULL THEN 'Unassigned' ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
               ar.status, s.planned_duration_hours AS "plannedDurationHours"
        FROM attendance_records ar
        JOIN attendance_sessions s ON ar.session_id = s.id
        JOIN cohorts c ON s.cohort_id = c.id
        LEFT JOIN tutors t ON c.tutor_id = t.id
        JOIN learners l ON ar.learner_id = l.id
        WHERE {where}
        ORDER BY s.session_date DESC, l.last_name, l.first_name
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    return cur.fetchall(), total


def fetch_lateness_rows(
    cur,
    *,
    period_start: date,
    period_end: date,
    tutor_id: int | None = None,
    cohort_id: int | None = None,
    programme: str | None = None,
    level: str | None = None,
    employer: str | None = None,
    learner_id: int | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int]:
    extra_clauses, extra_params = _session_row_filters(
        tutor_id=tutor_id, cohort_id=cohort_id, programme=programme, level=level, employer=employer, learner_id=learner_id
    )
    where = " AND ".join(
        ["s.status != 'cancelled'", "s.session_date >= %(periodStart)s", "s.session_date <= %(periodEnd)s",
         "ar.status = 'late'", *extra_clauses]
    )
    params = {"periodStart": period_start, "periodEnd": period_end, **extra_params}

    cur.execute(f"SELECT count(*) AS total FROM attendance_records ar "
                f"JOIN attendance_sessions s ON ar.session_id = s.id "
                f"JOIN cohorts c ON s.cohort_id = c.id "
                f"JOIN learners l ON ar.learner_id = l.id "
                f"WHERE {where}", params)
    total = cur.fetchone()["total"]

    cur.execute(
        f"""
        SELECT l.id AS "learnerId", concat(l.first_name, ' ', l.last_name) AS "learnerName",
               l.learner_ref AS "learnerRef",
               s.id AS "sessionId", s.session_date AS "sessionDate",
               s.planned_start_time AS "plannedStartTime",
               c.id AS "cohortId", c.name AS "cohortName",
               CASE WHEN t.id IS NULL THEN 'Unassigned' ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
               ar.minutes_late AS "minutesLate", ar.hours_attended AS "hoursAttended"
        FROM attendance_records ar
        JOIN attendance_sessions s ON ar.session_id = s.id
        JOIN cohorts c ON s.cohort_id = c.id
        LEFT JOIN tutors t ON c.tutor_id = t.id
        JOIN learners l ON ar.learner_id = l.id
        WHERE {where}
        ORDER BY s.session_date DESC, ar.minutes_late DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    return cur.fetchall(), total


def fetch_register_completion_rows(
    cur,
    *,
    period_start: date,
    period_end: date,
    tutor_id: int | None = None,
    cohort_id: int | None = None,
    register_status: RegisterStatusFilter | None = None,
    overdue_only: bool = False,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int]:
    """Session-level register-completion listing -- extends
    dashboard.py's _sessions_awaiting_completion (which only ever showed
    the overdue-incomplete subset) to every register status, with full
    filtering. Register status is derived with the exact same branch order
    as routers/attendance.py::_compute_register_status -- keep in sync."""
    clauses = ["s.session_date >= %(periodStart)s", "s.session_date <= %(periodEnd)s"]
    params: dict = {"periodStart": period_start, "periodEnd": period_end}
    if tutor_id is not None:
        clauses.append("c.tutor_id = %(tutorId)s")
        params["tutorId"] = tutor_id
    if cohort_id is not None:
        clauses.append("c.id = %(cohortId)s")
        params["cohortId"] = cohort_id
    where = " AND ".join(clauses)

    cte = f"""
        WITH session_rows AS (
            SELECT
                s.id AS "sessionId", s.session_date AS "sessionDate", s.title, s.status AS "sessionStatus",
                c.id AS "cohortId", c.name AS "cohortName",
                CASE WHEN t.id IS NULL THEN 'Unassigned' ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
                s.register_locked_at AS "registerLockedAt",
                CASE WHEN lu.id IS NULL THEN NULL ELSE concat(lu.first_name, ' ', lu.last_name) END AS "lockedByName",
                s.completed_at AS "completedAt",
                CASE WHEN cu.id IS NULL THEN NULL ELSE concat(cu.first_name, ' ', cu.last_name) END AS "completedByName",
                (SELECT count(*)::int FROM attendance_records ar WHERE ar.session_id = s.id) AS "recordedCount",
                (SELECT count(*)::int FROM session_expected_learners sel WHERE sel.session_id = s.id) AS "expectedCount"
            FROM attendance_sessions s
            JOIN cohorts c ON s.cohort_id = c.id
            LEFT JOIN tutors t ON c.tutor_id = t.id
            LEFT JOIN users lu ON s.register_locked_by = lu.id
            LEFT JOIN users cu ON s.completed_by = cu.id
            WHERE {where}
        ),
        with_status AS (
            SELECT *,
                CASE
                    WHEN "sessionStatus" = 'cancelled' THEN 'cancelled'
                    WHEN "registerLockedAt" IS NOT NULL THEN 'locked'
                    WHEN "recordedCount" = 0 THEN 'not_started'
                    WHEN "expectedCount" > 0 AND "recordedCount" >= "expectedCount" THEN 'completed'
                    ELSE 'in_progress'
                END AS "registerStatus"
            FROM session_rows
        )
        SELECT * FROM with_status
        WHERE (%(registerStatus)s::text IS NULL OR "registerStatus" = %(registerStatus)s)
          AND (%(overdueOnly)s = FALSE OR ("registerStatus" IN ('not_started', 'in_progress') AND "sessionDate" <= CURRENT_DATE))
    """
    count_params = {**params, "registerStatus": register_status, "overdueOnly": overdue_only}
    cur.execute(f"SELECT count(*) AS total FROM ({cte}) counted", count_params)
    total = cur.fetchone()["total"]

    cur.execute(
        f"{cte} ORDER BY \"sessionDate\" DESC LIMIT %(limit)s OFFSET %(offset)s",
        {**count_params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    rows = cur.fetchall()
    for row in rows:
        row["missingRowCount"] = max(row["expectedCount"] - row["recordedCount"], 0)
        row["outstandingDays"] = (
            (date.today() - row["sessionDate"]).days
            if row["registerStatus"] in ("not_started", "in_progress") and row["sessionDate"] <= date.today()
            else None
        )
    return rows, total


def fetch_allocation_history_rows(
    cur,
    *,
    learner_id: int | None = None,
    tutor_id: int | None = None,
    cohort_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int]:
    """Paginated, ordered, cohort-filterable wrapper around
    learner_allocation_history -- the existing GET /allocation/history has
    none of pagination/ordering/cohort-filter, and only matches new_tutor_id
    (not previous_tutor_id); this is a report-specific superset, not a
    change to that existing endpoint."""
    clauses = []
    params: dict = {}
    if learner_id is not None:
        clauses.append("learner_id = %(learnerId)s")
        params["learnerId"] = learner_id
    if tutor_id is not None:
        clauses.append("(new_tutor_id = %(tutorId)s OR previous_tutor_id = %(tutorId)s)")
        params["tutorId"] = tutor_id
    if cohort_id is not None:
        clauses.append("(new_cohort_id = %(cohortId)s OR previous_cohort_id = %(cohortId)s)")
        params["cohortId"] = cohort_id
    if date_from is not None:
        clauses.append("effective_date >= %(dateFrom)s")
        params["dateFrom"] = date_from
    if date_to is not None:
        clauses.append("effective_date <= %(dateTo)s")
        params["dateTo"] = date_to
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    cur.execute(f"SELECT count(*) AS total FROM learner_allocation_history {where}", params)
    total = cur.fetchone()["total"]

    cur.execute(
        f"""
        SELECT id, learner_id AS "learnerId", previous_tutor_id AS "previousTutorId",
               new_tutor_id AS "newTutorId", previous_cohort_id AS "previousCohortId",
               new_cohort_id AS "newCohortId", effective_date AS "effectiveDate",
               transfer_reason AS "transferReason", changed_by AS "changedBy", changed_date AS "changedDate",
               (
                   SELECT MIN(h2.effective_date) FROM learner_allocation_history h2
                   WHERE h2.learner_id = learner_allocation_history.learner_id
                     AND h2.effective_date > learner_allocation_history.effective_date
               ) AS "effectiveTo"
        FROM learner_allocation_history
        {where}
        ORDER BY effective_date DESC, id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {**params, "limit": page_size, "offset": (page - 1) * page_size},
    )
    rows = cur.fetchall()
    return enrich_allocation_history(rows), total
