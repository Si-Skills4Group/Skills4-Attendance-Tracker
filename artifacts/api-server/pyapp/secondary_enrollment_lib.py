"""Functional Skills secondary cohort enrollment: lets an Administrator add
a learner to a SECOND ('secondary' membership_type) cohort concurrently with
their home cohort/tutor -- e.g. an English or Maths Functional Skills cohort
run by a specialist tutor -- without ever touching learners.cohort_id/
tutor_id, apply_transfer, or learner_allocation_history. Those remain the
sole mechanism for a learner's single home cohort/tutor; this table is a
purely additive second membership, resolved alongside (not instead of) the
learner's home cohort by allocation_lib.learners_expected_in_cohort_as_of.

Mirrors allocation_lib.py's/cover_tutor_lib.py's plain-function style: no
ORM, raw psycopg SQL, lib functions raise HTTPException directly and
routers stay thin.

Two different SQL shapes are needed for "does this learner belong here":
- allocation_lib.py's resolver functions are date-parameterized (a session's
  own date, possibly historical or future) and use enrolled_date/end_date.
- The helpers below are for "right now" reads (cohort rosters/counts,
  dashboard widgets, report scoping) and use the maintained `status` column
  instead -- consistent with how the rest of this codebase treats
  learners.status as the "currently" signal. Do not mix the two: a status
  check is wrong for re-resolving a past session's roster, and a date check
  against today is wrong for "is this learner currently visible on this
  cohort's roster" (which should reflect an admin ending an enrollment
  effective today, immediately).
"""
from datetime import date
from typing import Any

from fastapi import HTTPException

ENROLLMENT_SELECT = """
    SELECT e.id, e.learner_id AS "learnerId", e.cohort_id AS "cohortId",
           e.enrolled_date AS "enrolledDate", e.end_date AS "endDate", e.status,
           e.enrolled_by AS "enrolledBy", e.enrollment_reason AS "enrollmentReason",
           e.ended_by AS "endedBy", e.end_reason AS "endReason",
           e.created_at AS "createdAt", e.updated_at AS "updatedAt",
           c.name AS "cohortName", c.tutor_id AS "cohortTutorId",
           CASE WHEN ct.id IS NULL THEN NULL ELSE concat(ct.first_name, ' ', ct.last_name) END AS "cohortTutorName"
    FROM learner_cohort_enrollments e
    JOIN cohorts c ON c.id = e.cohort_id
    LEFT JOIN tutors ct ON c.tutor_id = ct.id
"""


def learner_in_cohort_now_sql(learner_alias: str, cohort_id_column: str) -> str:
    """SQL fragment (boolean expression): true if `learner_alias` (a
    `learners` row/alias in scope) is currently expected in the cohort
    identified by `cohort_id_column`, either as their home cohort or via an
    active secondary enrollment. For rosters/counts/"currently" reads only
    -- both arguments must be trusted SQL references composed by the
    caller, never raw user input."""
    return f"""(
        {learner_alias}.cohort_id = {cohort_id_column}
        OR EXISTS (
            SELECT 1 FROM learner_cohort_enrollments sel_e
            WHERE sel_e.learner_id = {learner_alias}.id
              AND sel_e.cohort_id = {cohort_id_column}
              AND sel_e.status = 'active'
        )
    )"""


def learner_reachable_via_tutor_now_sql(learner_alias: str, tutor_id_column: str) -> str:
    """SQL fragment: true if `learner_alias`'s home tutor is
    `tutor_id_column`, or they have an active secondary enrollment into one
    of that tutor's own cohorts (e.g. a Functional Skills tutor's own
    learners for "my learners"/low-attendance dashboard widgets). Same
    trust contract as learner_in_cohort_now_sql."""
    return f"""(
        {learner_alias}.tutor_id = {tutor_id_column}
        OR EXISTS (
            SELECT 1 FROM learner_cohort_enrollments sel_e2
            JOIN cohorts sel_c2 ON sel_c2.id = sel_e2.cohort_id
            WHERE sel_e2.learner_id = {learner_alias}.id
              AND sel_e2.status = 'active'
              AND sel_c2.tutor_id = {tutor_id_column}
        )
    )"""


def functional_skills_subjects_sql(learner_id_column: str) -> str:
    """SQL fragment (scalar subquery): the distinct, sorted list of
    Functional Skills subjects ('math'/'english'/'both') this learner is
    currently actively enrolled in, as a Postgres text array (empty, never
    NULL, when they have none) -- the source for a "Functional Skills"
    flag/badge next to a learner's name. `learner_id_column` must be a
    trusted SQL column reference composed by the caller, never raw user
    input."""
    return f"""(
        SELECT COALESCE(array_agg(DISTINCT fs_c.subject ORDER BY fs_c.subject), ARRAY[]::text[])
        FROM learner_cohort_enrollments fs_e
        JOIN cohorts fs_c ON fs_c.id = fs_e.cohort_id
        WHERE fs_e.learner_id = {learner_id_column} AND fs_e.status = 'active' AND fs_c.subject IS NOT NULL
    )"""


def _get_secondary_cohort_or_400(cur, cohort_id: int) -> dict:
    cur.execute(
        'SELECT id, membership_type AS "membershipType", deleted_at AS "deletedAt" FROM cohorts WHERE id = %s',
        (cohort_id,),
    )
    cohort = cur.fetchone()
    if not cohort or cohort["deletedAt"] is not None:
        raise HTTPException(status_code=404, detail="Cohort not found")
    if cohort["membershipType"] != "secondary":
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "not_a_secondary_cohort",
                "message": "Only a Functional Skills cohort can be used for a secondary enrollment.",
            },
        )
    return cohort


def enroll_learner_in_secondary_cohort(
    cur,
    learner: dict[str, Any],
    cohort_id: int,
    enrolled_date: date,
    reason: str | None,
    enrolled_by: int,
) -> dict:
    _get_secondary_cohort_or_400(cur, cohort_id)
    if learner.get("cohortId") == cohort_id:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "already_home_cohort",
                "message": "This learner's home cohort is already this cohort.",
            },
        )
    cur.execute(
        "SELECT id FROM learner_cohort_enrollments WHERE learner_id = %s AND cohort_id = %s AND status = 'active'",
        (learner["id"], cohort_id),
    )
    if cur.fetchone():
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "already_enrolled",
                "message": "This learner already has an active enrollment in this cohort.",
            },
        )
    cur.execute(
        """
        INSERT INTO learner_cohort_enrollments
            (learner_id, cohort_id, enrolled_date, status, enrolled_by, enrollment_reason)
        VALUES (%s, %s, %s, 'active', %s, %s)
        RETURNING id
        """,
        (learner["id"], cohort_id, enrolled_date, enrolled_by, reason),
    )
    new_id = cur.fetchone()["id"]
    cur.execute(f"{ENROLLMENT_SELECT} WHERE e.id = %s", (new_id,))
    return cur.fetchone()


def end_secondary_enrollment(cur, enrollment_id: int, end_date: date, reason: str | None, ended_by: int) -> dict:
    """The single writer of status+end_date together -- never one without
    the other, mirroring routers/learners.py's status-change convention."""
    cur.execute(f"{ENROLLMENT_SELECT} WHERE e.id = %s", (enrollment_id,))
    existing = cur.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Secondary enrollment not found")
    if existing["status"] != "active":
        raise HTTPException(status_code=400, detail="This enrollment has already ended")
    cur.execute(
        """
        UPDATE learner_cohort_enrollments
        SET status = 'ended', end_date = %s, ended_by = %s, end_reason = %s, updated_at = now()
        WHERE id = %s
        """,
        (end_date, ended_by, reason, enrollment_id),
    )
    cur.execute(f"{ENROLLMENT_SELECT} WHERE e.id = %s", (enrollment_id,))
    return cur.fetchone()


def list_secondary_enrollments_for_learner(cur, learner_id: int) -> list[dict]:
    cur.execute(
        f"{ENROLLMENT_SELECT} WHERE e.learner_id = %s ORDER BY e.enrolled_date DESC, e.id DESC", (learner_id,)
    )
    return cur.fetchall()


def list_secondary_enrollments_for_cohort(cur, cohort_id: int) -> list[dict]:
    cur.execute(
        f"{ENROLLMENT_SELECT} WHERE e.cohort_id = %s ORDER BY e.enrolled_date DESC, e.id DESC", (cohort_id,)
    )
    return cur.fetchall()
