"""Bulk-generating a cohort's recurring attendance sessions from a simple
recurrence pattern (day of week, weekly/bi-weekly/monthly, a time, a first
and a final date) -- reuses the exact same per-date duplicate check
(find_duplicate_session) and snapshot generation (ensure_expected_learners_
snapshots_bulk) as a single manually-created session, so a generated
session is in every way indistinguishable from one created by hand.

"Monthly" means the same WEEKDAY POSITION each month (e.g. the first
session being the 2nd Tuesday of its month means every later session is
also its month's 2nd Tuesday -- not a fixed 28-day interval, which would
slowly drift against the calendar month over a long run). A month that
doesn't have a given position for that weekday (e.g. no 5th Friday) is
simply skipped -- never silently substituted with a different week.

No background job infrastructure exists in this app (see
scheduled_allocations_lib.py's own docstring) -- this is a synchronous,
request-scoped computation and transactional bulk insert, never a queued
job."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Literal

from fastapi import HTTPException

from .session_register_lib import (
    ensure_expected_learners_snapshots_bulk,
    find_duplicate_session,
    session_date_outside_cohort_range,
)

Occurrence = Literal["weekly", "biweekly", "monthly"]

_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Defensive cap, not a real-world expectation -- catches a fat-fingered
# multi-year date range before it silently inserts hundreds of sessions.
MAX_GENERATED_SESSIONS = 200


def _nth_weekday_of_month(year: int, month: int, weekday: int, index: int) -> date | None:
    """The (index+1)-th occurrence of `weekday` (0=Monday) in this month,
    or None if that occurrence doesn't exist this month (e.g. index=4 for
    "5th" in a month with only four of that weekday)."""
    first_of_month = date(year, month, 1)
    first_match_day = 1 + ((weekday - first_of_month.weekday()) % 7)
    day = first_match_day + index * 7
    if day > calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, day)


def compute_session_dates(
    day_of_week: str, occurrence: Occurrence, first_session_date: date, final_session_date: date,
) -> list[date]:
    """The full list of session dates for this recurrence pattern,
    inclusive of both endpoints. Raises HTTPException(400) for a pattern
    that can't be resolved (first date not actually on the chosen weekday,
    an inverted range, or a resulting session count past MAX_GENERATED_
    SESSIONS) rather than silently generating something the caller didn't
    ask for."""
    if day_of_week not in _WEEKDAY_INDEX:
        raise HTTPException(status_code=400, detail=f"Unknown day of week: {day_of_week}")
    target_weekday = _WEEKDAY_INDEX[day_of_week]
    if first_session_date.weekday() != target_weekday:
        raise HTTPException(
            status_code=400,
            detail=f"The first session date ({first_session_date.isoformat()}) is not a {day_of_week.capitalize()}",
        )
    if final_session_date < first_session_date:
        raise HTTPException(status_code=400, detail="The final session date cannot be before the first session date")

    dates: list[date] = []
    if occurrence in ("weekly", "biweekly"):
        step_days = 7 if occurrence == "weekly" else 14
        current = first_session_date
        while current <= final_session_date:
            dates.append(current)
            current = current + timedelta(days=step_days)
            if len(dates) > MAX_GENERATED_SESSIONS:
                break
    elif occurrence == "monthly":
        occurrence_index = (first_session_date.day - 1) // 7
        year, month = first_session_date.year, first_session_date.month
        # Bounded iteration (40 years of months) as a hard safety net --
        # the final_session_date check below is what normally stops this.
        for _ in range(12 * 40):
            if date(year, month, 1) > final_session_date:
                break
            candidate = _nth_weekday_of_month(year, month, target_weekday, occurrence_index)
            if candidate is not None and first_session_date <= candidate <= final_session_date:
                dates.append(candidate)
            month += 1
            if month > 12:
                month = 1
                year += 1
            if len(dates) > MAX_GENERATED_SESSIONS:
                break
    else:
        raise HTTPException(status_code=400, detail=f"Unknown occurrence: {occurrence}")

    if len(dates) > MAX_GENERATED_SESSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"This pattern would generate {len(dates)} sessions, which exceeds the {MAX_GENERATED_SESSIONS}-session limit -- narrow the date range.",
        )
    return dates


def _fetch_cohort_date_range(cur, cohort_id: int) -> dict:
    cur.execute('SELECT start_date AS "startDate", end_date AS "endDate" FROM cohorts WHERE id = %s', (cohort_id,))
    return cur.fetchone()


def preview_generated_sessions(
    cur, *, cohort_id: int, day_of_week: str, occurrence: Occurrence,
    planned_start_time: str, first_session_date: date, final_session_date: date,
) -> dict:
    """Read-only. One row per candidate date, flagged with whether it would
    be skipped on confirm -- either a duplicate (the same definition a
    single manual create uses) or outside the cohort's own start/end dates
    (the same create_attendance_session check, applied here per-date
    instead of once) -- never silently dropped from the preview, so the
    admin sees exactly which dates will be skipped and why before
    committing anything."""
    dates = compute_session_dates(day_of_week, occurrence, first_session_date, final_session_date)
    cohort_dates = _fetch_cohort_date_range(cur, cohort_id)
    rows = []
    for session_date in dates:
        reason = None
        if find_duplicate_session(cur, cohort_id, session_date, planned_start_time) is not None:
            reason = "duplicate_session"
        elif session_date_outside_cohort_range(cohort_dates, session_date):
            reason = "outside_cohort_date_range"
        rows.append({"sessionDate": session_date, "conflict": reason is not None, "conflictReason": reason})
    return {
        "dates": rows,
        "newCount": sum(1 for r in rows if not r["conflict"]),
        "conflictCount": sum(1 for r in rows if r["conflict"]),
    }


def generate_sessions(
    cur, *, cohort_id: int, day_of_week: str, occurrence: Occurrence,
    planned_start_time: str, planned_end_time: str, planned_duration_hours: float,
    first_session_date: date, final_session_date: date,
    title: str, notes: str | None, created_by: int,
) -> dict:
    """Re-resolves the pattern fresh (never trusts a client-supplied date
    list) and re-checks each date for a duplicate or an out-of-range date at
    commit time, not just preview time -- a date that gained a session, or
    a cohort whose end date was shortened, between this admin's preview and
    their confirm is skipped exactly like one caught at preview time, never
    double-booked or created outside the cohort's own dates. One
    transaction for every insert, then one batched snapshot pass -- mirrors
    create_attendance_session's own per-session shape, just for many
    sessions at once instead of one (minus its force/overrideReason escape
    hatch, which doesn't make sense for a batch of many dates at once)."""
    dates = compute_session_dates(day_of_week, occurrence, first_session_date, final_session_date)
    cohort_dates = _fetch_cohort_date_range(cur, cohort_id)
    created_ids: list[int] = []
    skipped_dates: list[date] = []

    with cur.connection.transaction():
        for session_date in dates:
            if (
                find_duplicate_session(cur, cohort_id, session_date, planned_start_time)
                or session_date_outside_cohort_range(cohort_dates, session_date)
            ):
                skipped_dates.append(session_date)
                continue
            cur.execute(
                """
                INSERT INTO attendance_sessions
                    (cohort_id, session_date, planned_start_time, planned_end_time, planned_duration_hours,
                     title, notes, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (cohort_id, session_date, planned_start_time, planned_end_time, planned_duration_hours, title, notes, created_by),
            )
            created_ids.append(cur.fetchone()["id"])

        ensure_expected_learners_snapshots_bulk(cur, created_ids)

    return {"createdCount": len(created_ids), "createdIds": created_ids, "skippedDates": skipped_dates}
