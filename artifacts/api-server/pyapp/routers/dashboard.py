from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from ..attendance_calc import compute_attendance_totals
from ..attendance_data import get_records_for_learner
from ..auth import require_admin, require_auth
from ..db import get_cursor
from .cohorts import COHORT_SELECT

router = APIRouter(tags=["dashboard"])


def _low_attendance_learners(cur, learner_ids: list[int], threshold: float) -> list[dict]:
    if not learner_ids:
        return []
    cur.execute(
        'SELECT id, first_name AS "firstName", last_name AS "lastName", learner_ref AS "learnerRef" '
        "FROM learners WHERE id = ANY(%s)",
        (learner_ids,),
    )
    learners = cur.fetchall()
    rows = []
    for learner in learners:
        totals = compute_attendance_totals(get_records_for_learner(learner["id"]))
        if totals["sessionCount"] > 0 and totals["attendancePercentage"] < threshold:
            rows.append(
                {
                    "learnerId": learner["id"],
                    "learnerName": f"{learner['firstName']} {learner['lastName']}",
                    "learnerRef": learner["learnerRef"],
                    "totals": totals,
                }
            )
    return rows


def _sessions_awaiting_completion(cur, cohort_ids: list[int] | None) -> list[dict]:
    today = date.today()
    clauses = ["s.session_date <= %s"]
    params: list = [today]
    if cohort_ids is not None:
        clauses.append("s.cohort_id = ANY(%s)")
        params.append(cohort_ids)

    where = " AND ".join(clauses)
    cur.execute(
        f"""
        SELECT s.id, s.cohort_id AS "cohortId", c.name AS "cohortName", s.session_date AS "sessionDate",
               CASE WHEN t.id IS NULL THEN 'Unassigned' ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName"
        FROM attendance_sessions s
        JOIN cohorts c ON s.cohort_id = c.id
        LEFT JOIN tutors t ON c.tutor_id = t.id
        WHERE {where}
          AND (SELECT count(*) FROM learners l WHERE l.cohort_id = s.cohort_id) > 0
          AND (
              SELECT count(*) FROM attendance_records ar WHERE ar.session_id = s.id
          ) < (
              SELECT count(*) FROM learners l WHERE l.cohort_id = s.cohort_id
          )
        ORDER BY s.session_date DESC
        """,
        params,
    )
    return cur.fetchall()


def _recently_edited(cur, cohort_ids: list[int] | None, limit: int = 10) -> list[dict]:
    clauses = []
    params: list = []
    if cohort_ids is not None:
        clauses.append("s.cohort_id = ANY(%s)")
        params.append(cohort_ids)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
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


@router.get("/dashboard/admin")
def get_admin_dashboard(_session: dict = Depends(require_admin)):
    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    with get_cursor() as cur:
        cur.execute("SELECT count(*)::int AS count FROM learners WHERE status = 'active'")
        active_learners = cur.fetchone()["count"]
        cur.execute("SELECT count(*)::int AS count FROM tutors WHERE active = true")
        active_tutors = cur.fetchone()["count"]
        cur.execute("SELECT count(*)::int AS count FROM cohorts WHERE active = true")
        active_cohorts = cur.fetchone()["count"]

        def attendance_pct_since(since: date) -> float:
            cur.execute(
                """
                SELECT ar.status, ar.hours_attended AS "hoursAttended",
                       s.planned_duration_hours AS "plannedDurationHours"
                FROM attendance_records ar
                JOIN attendance_sessions s ON ar.session_id = s.id
                WHERE s.session_date >= %s
                """,
                (since,),
            )
            return compute_attendance_totals(cur.fetchall())["attendancePercentage"]

        pct_week = attendance_pct_since(week_ago)
        pct_month = attendance_pct_since(month_ago)

        sessions_awaiting = _sessions_awaiting_completion(cur, None)
        recent_edits = _recently_edited(cur, None)

        cur.execute("SELECT organisation_name, low_attendance_threshold FROM app_settings LIMIT 1")
        settings_row = cur.fetchone()
        threshold = float(settings_row["low_attendance_threshold"]) if settings_row else 85.0

        cur.execute("SELECT id FROM learners WHERE status = 'active'")
        active_learner_ids = [r["id"] for r in cur.fetchall()]
        low_attendance = _low_attendance_learners(cur, active_learner_ids, threshold)

    return {
        "activeLearners": active_learners,
        "activeTutors": active_tutors,
        "activeCohorts": active_cohorts,
        "attendancePercentageWeek": pct_week,
        "attendancePercentageMonth": pct_month,
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
        cur.execute(f"{COHORT_SELECT} WHERE c.tutor_id = %s", (tutor_id,))
        cohorts = cur.fetchall()
        cohort_ids = [c["id"] for c in cohorts]

        cohort_summaries = []
        for cohort in cohorts:
            cur.execute("SELECT count(*)::int AS count FROM learners WHERE cohort_id = %s", (cohort["id"],))
            learner_count = cur.fetchone()["count"]
            cur.execute(
                """
                SELECT ar.status, ar.hours_attended AS "hoursAttended",
                       s.planned_duration_hours AS "plannedDurationHours"
                FROM attendance_records ar
                JOIN attendance_sessions s ON ar.session_id = s.id
                WHERE s.cohort_id = %s
                """,
                (cohort["id"],),
            )
            totals = compute_attendance_totals(cur.fetchall())
            cohort_summaries.append(
                {"cohort": cohort, "learnerCount": learner_count, "attendancePercentage": totals["attendancePercentage"]}
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
                WHERE s.cohort_id = ANY(%s) AND s.session_date >= CURRENT_DATE
                ORDER BY s.session_date ASC
                LIMIT 1
                """,
                (cohort_ids,),
            )
            next_session = cur.fetchone()

        sessions_awaiting = _sessions_awaiting_completion(cur, cohort_ids) if cohort_ids else []

        cur.execute("SELECT low_attendance_threshold FROM app_settings LIMIT 1")
        settings_row = cur.fetchone()
        threshold = float(settings_row["low_attendance_threshold"]) if settings_row else 85.0

        cur.execute("SELECT id FROM learners WHERE tutor_id = %s AND status = 'active'", (tutor_id,))
        learner_ids = [r["id"] for r in cur.fetchall()]
        low_attendance = _low_attendance_learners(cur, learner_ids, threshold)

    return {
        "cohorts": cohort_summaries,
        "nextSession": next_session,
        "sessionsAwaitingCompletion": sessions_awaiting,
        "lowAttendanceLearners": low_attendance,
    }
