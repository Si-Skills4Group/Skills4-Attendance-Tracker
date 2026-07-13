"""Faithful port of lib/allocation.ts -- joins allocation history rows with
tutor/cohort/learner/user names for display."""
from .db import get_cursor


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
