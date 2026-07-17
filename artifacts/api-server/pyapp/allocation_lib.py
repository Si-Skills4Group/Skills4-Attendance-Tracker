"""Faithful port of lib/allocation.ts -- joins allocation history rows with
tutor/cohort/learner/user names for display."""
from datetime import date

from .db import get_cursor


def apply_transfer(
    cur,
    learner: dict,
    new_tutor_id: int | None,
    new_cohort_id: int | None,
    effective_date: date,
    transfer_reason: str | None,
    changed_by: int,
) -> None:
    """Mutates learners.tutor_id/cohort_id to the new allocation and records
    the change in learner_allocation_history. This is the one place that
    writes an allocation change -- both the immediate-allocate path and the
    scheduled-transfer apply path call this, so there is exactly one code
    path that can move a learner between tutors/cohorts."""
    cur.execute(
        "UPDATE learners SET tutor_id = %s, cohort_id = %s, updated_at = now() WHERE id = %s",
        (new_tutor_id, new_cohort_id, learner["id"]),
    )
    cur.execute(
        """
        INSERT INTO learner_allocation_history
            (learner_id, previous_tutor_id, new_tutor_id, previous_cohort_id, new_cohort_id,
             effective_date, transfer_reason, changed_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            learner["id"],
            learner["tutorId"],
            new_tutor_id,
            learner["cohortId"],
            new_cohort_id,
            effective_date,
            transfer_reason,
            changed_by,
        ),
    )


def learners_expected_in_cohort_as_of(cur, cohort_id: int, as_of_date: date) -> list[int]:
    """Resolves which learners were allocated to a cohort as of a given
    date, using learner_allocation_history rather than the learners table's
    *current* cohort_id. This is what lets a past attendance session keep
    showing the learners who were actually in the cohort on that date, even
    after they've since transferred elsewhere -- the roster is derived, not
    a snapshot, so no attendance_records row is ever touched by a transfer.

    Three-tier resolution per learner: the most recent history row on or
    before as_of_date; else the *earliest* history row's previous_cohort_id
    (the cohort they were in before their first-ever transfer); else the
    learner's current cohort_id (never transferred).

    Also excludes learners who, as of that date, hadn't started yet, or had
    already withdrawn/completed -- cohort-history resolution alone doesn't
    know about a learner's own start/end lifecycle."""
    cur.execute(
        """
        SELECT l.id
        FROM learners l
        WHERE COALESCE(
            (SELECT h.new_cohort_id FROM learner_allocation_history h
             WHERE h.learner_id = l.id AND h.effective_date <= %(as_of)s
             ORDER BY h.effective_date DESC, h.id DESC LIMIT 1),
            (SELECT h.previous_cohort_id FROM learner_allocation_history h
             WHERE h.learner_id = l.id
             ORDER BY h.effective_date ASC, h.id ASC LIMIT 1),
            l.cohort_id
        ) = %(cohort_id)s
        AND l.start_date <= %(as_of)s
        AND NOT (l.status = 'withdrawn' AND l.withdrawal_date IS NOT NULL AND l.withdrawal_date <= %(as_of)s)
        AND NOT (l.status = 'completed' AND l.actual_end_date IS NOT NULL AND l.actual_end_date <= %(as_of)s)
        """,
        {"as_of": as_of_date, "cohort_id": cohort_id},
    )
    return [row["id"] for row in cur.fetchall()]


def expected_learners_count_sql(cohort_id_column: str, as_of_date_column: str) -> str:
    """SQL fragment (a scalar subquery) counting how many learners were
    allocated to a cohort as of a date -- the multi-row-query counterpart
    to learners_expected_in_cohort_as_of, for embedding in queries that
    process many sessions/cohorts at once (e.g. dashboard aggregates),
    where calling the Python helper per row would mean N+1 queries.
    cohort_id_column/as_of_date_column must be trusted SQL column
    references composed by the caller, never raw user input."""
    return f"""(
        SELECT count(*) FROM learners exp_l
        WHERE COALESCE(
            (SELECT h.new_cohort_id FROM learner_allocation_history h
             WHERE h.learner_id = exp_l.id AND h.effective_date <= {as_of_date_column}
             ORDER BY h.effective_date DESC, h.id DESC LIMIT 1),
            (SELECT h.previous_cohort_id FROM learner_allocation_history h
             WHERE h.learner_id = exp_l.id ORDER BY h.effective_date ASC, h.id ASC LIMIT 1),
            exp_l.cohort_id
        ) = {cohort_id_column}
        AND exp_l.start_date <= {as_of_date_column}
        AND NOT (exp_l.status = 'withdrawn' AND exp_l.withdrawal_date IS NOT NULL AND exp_l.withdrawal_date <= {as_of_date_column})
        AND NOT (exp_l.status = 'completed' AND exp_l.actual_end_date IS NOT NULL AND exp_l.actual_end_date <= {as_of_date_column})
    )"""


def enrich_allocation_history(rows: list[dict]) -> list[dict]:
    if not rows:
        return []

    tutor_ids = {r["previousTutorId"] for r in rows if r["previousTutorId"] is not None}
    tutor_ids |= {r["newTutorId"] for r in rows if r["newTutorId"] is not None}
    cohort_ids = {r["previousCohortId"] for r in rows if r["previousCohortId"] is not None}
    cohort_ids |= {r["newCohortId"] for r in rows if r["newCohortId"] is not None}
    learner_ids = {r["learnerId"] for r in rows}
    user_ids = {r["changedBy"] for r in rows}

    tutors: dict[int, str] = {}
    cohorts: dict[int, str] = {}
    learners: dict[int, str] = {}
    users: dict[int, str] = {}

    with get_cursor() as cur:
        if tutor_ids:
            cur.execute(
                "SELECT id, first_name, last_name FROM tutors WHERE id = ANY(%s)",
                (list(tutor_ids),),
            )
            tutors = {r["id"]: f"{r['first_name']} {r['last_name']}" for r in cur.fetchall()}
        if cohort_ids:
            cur.execute("SELECT id, name FROM cohorts WHERE id = ANY(%s)", (list(cohort_ids),))
            cohorts = {r["id"]: r["name"] for r in cur.fetchall()}
        if learner_ids:
            cur.execute(
                "SELECT id, first_name, last_name FROM learners WHERE id = ANY(%s)",
                (list(learner_ids),),
            )
            learners = {r["id"]: f"{r['first_name']} {r['last_name']}" for r in cur.fetchall()}
        if user_ids:
            cur.execute(
                "SELECT id, first_name, last_name FROM users WHERE id = ANY(%s)",
                (list(user_ids),),
            )
            users = {r["id"]: f"{r['first_name']} {r['last_name']}" for r in cur.fetchall()}

    enriched = []
    for r in rows:
        enriched.append(
            {
                **r,
                "learnerName": learners.get(r["learnerId"], "Unknown learner"),
                "previousTutorName": tutors.get(r["previousTutorId"]) if r["previousTutorId"] is not None else None,
                "newTutorName": tutors.get(r["newTutorId"]) if r["newTutorId"] is not None else None,
                "previousCohortName": cohorts.get(r["previousCohortId"]) if r["previousCohortId"] is not None else None,
                "newCohortName": cohorts.get(r["newCohortId"]) if r["newCohortId"] is not None else None,
                "changedByName": users.get(r["changedBy"], "Unknown user"),
            }
        )
    return enriched
