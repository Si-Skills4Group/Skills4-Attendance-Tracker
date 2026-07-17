"""Attendance session lifecycle: expected-learner register snapshots,
duplicate/date-range session-creation guards, cancellation, and the
future-incomplete-register refresh workflow.

The register snapshot (session_expected_learners) exists so a session's
roster is frozen at generation time rather than recomputed live on every
read -- learners_expected_in_cohort_as_of (allocation_lib) is already
date-stable against ordinary transfers (it resolves membership as of the
session's own date), but a *backdated* allocation correction entered after
the fact would otherwise silently rewrite a historical/completed session's
roster. The snapshot is what prevents that.
"""
from datetime import date

from fastapi import HTTPException

from .allocation_lib import learners_expected_in_cohort_as_of


def ensure_expected_learners_snapshot(
    cur, session_id: int, cohort_id: int, session_date: date, generated_by: int | None = None
) -> None:
    """Idempotently generates the snapshot rows for a session, if it hasn't
    been generated before. Safe to call on every register read: the claim
    on attendance_sessions.register_generated_at (rather than "no snapshot
    rows exist") means a session whose register legitimately has zero
    eligible learners is never mistaken for one that hasn't been generated
    yet, and silently regenerated -- and calling this repeatedly never
    duplicates rows (ON CONFLICT DO NOTHING) or disturbs an existing
    snapshot."""
    cur.execute(
        """
        UPDATE attendance_sessions SET register_generated_at = now()
        WHERE id = %s AND register_generated_at IS NULL
        RETURNING id
        """,
        (session_id,),
    )
    if not cur.fetchone():
        return

    learner_ids = learners_expected_in_cohort_as_of(cur, cohort_id, session_date)
    for learner_id in learner_ids:
        cur.execute(
            """
            INSERT INTO session_expected_learners (session_id, learner_id, cohort_id, generated_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (session_id, learner_id) DO NOTHING
            """,
            (session_id, learner_id, cohort_id, generated_by),
        )


def ensure_expected_learners_snapshots_bulk(cur, session_ids: list[int]) -> None:
    """Batched version of ensure_expected_learners_snapshot, for endpoints
    that touch many sessions at once (list_attendance_sessions,
    list_cohort_summary). Claims only the sessions that have never been
    generated (a single UPDATE), then generates just those -- in steady
    state (every session opened/listed at least once since this feature
    shipped) this is one no-op query, not N."""
    if not session_ids:
        return
    cur.execute(
        """
        UPDATE attendance_sessions SET register_generated_at = now()
        WHERE id = ANY(%s) AND register_generated_at IS NULL
        RETURNING id, cohort_id AS "cohortId", session_date AS "sessionDate"
        """,
        (session_ids,),
    )
    newly_claimed = cur.fetchall()
    for row in newly_claimed:
        learner_ids = learners_expected_in_cohort_as_of(cur, row["cohortId"], row["sessionDate"])
        for learner_id in learner_ids:
            cur.execute(
                """
                INSERT INTO session_expected_learners (session_id, learner_id, cohort_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (session_id, learner_id) DO NOTHING
                """,
                (row["id"], learner_id, row["cohortId"]),
            )


def find_duplicate_session(cur, cohort_id: int, session_date: date, planned_start_time: str) -> dict | None:
    """Likely-duplicate check: same cohort, same date, same planned start
    time. Deliberately narrower than a same-cohort-same-date-only check --
    two legitimate sessions on the same day at different times (e.g. an AM
    and a PM session) are not duplicates."""
    cur.execute(
        """
        SELECT id, session_date AS "sessionDate", planned_start_time AS "plannedStartTime",
               planned_end_time AS "plannedEndTime", title
        FROM attendance_sessions
        WHERE cohort_id = %s AND session_date = %s AND planned_start_time = %s
        """,
        (cohort_id, session_date, planned_start_time),
    )
    return cur.fetchone()


def session_date_outside_cohort_range(cohort: dict, session_date: date) -> bool:
    """cohort must have startDate/endDate keys (endDate may be None)."""
    if session_date < cohort["startDate"]:
        return True
    if cohort["endDate"] is not None and session_date > cohort["endDate"]:
        return True
    return False


def compute_register_refresh(cur, session_row: dict) -> dict:
    """Dry-run diff between the current snapshot and what eligibility
    resolves to today. Only sessions that are session_date >= today, not
    cancelled, and not completed should ever call this (enforced by the
    caller) -- this function itself is a pure read, safe to call anytime.

    blocked = learners no longer eligible but who already have a recorded
    attendance_records row for this session -- never removed by a refresh,
    per the "do not remove a learner row that already contains recorded
    attendance" rule."""
    session_id = session_row["id"]

    cur.execute("SELECT learner_id FROM session_expected_learners WHERE session_id = %s", (session_id,))
    current_ids = {row["learner_id"] for row in cur.fetchall()}

    eligible_ids = set(
        learners_expected_in_cohort_as_of(cur, session_row["cohortId"], session_row["sessionDate"])
    )

    to_add_ids = eligible_ids - current_ids
    candidate_remove_ids = current_ids - eligible_ids

    blocked_ids: set[int] = set()
    if candidate_remove_ids:
        cur.execute(
            "SELECT learner_id FROM attendance_records WHERE session_id = %s AND learner_id = ANY(%s)",
            (session_id, list(candidate_remove_ids)),
        )
        blocked_ids = {row["learner_id"] for row in cur.fetchall()}
    to_remove_ids = candidate_remove_ids - blocked_ids

    def _enrich(ids: set[int]) -> list[dict]:
        if not ids:
            return []
        cur.execute(
            """
            SELECT id AS "learnerId", concat(first_name, ' ', last_name) AS "learnerName"
            FROM learners WHERE id = ANY(%s) ORDER BY last_name, first_name
            """,
            (list(ids),),
        )
        return cur.fetchall()

    return {
        "toAdd": _enrich(to_add_ids),
        "toRemove": _enrich(to_remove_ids),
        "blocked": _enrich(blocked_ids),
    }


def apply_register_refresh(cur, session_row: dict, diff: dict, user_id: int | None) -> dict:
    """Applies a previously-computed diff transactionally. Never touches
    `blocked` entries. Idempotent: applying the same diff twice in a row
    (e.g. a retried request) is a no-op the second time since the rows it
    adds/removes will already be in their target state."""
    session_id = session_row["id"]

    for learner in diff["toAdd"]:
        cur.execute(
            """
            INSERT INTO session_expected_learners (session_id, learner_id, cohort_id, generated_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (session_id, learner_id) DO NOTHING
            """,
            (session_id, learner["learnerId"], session_row["cohortId"], user_id),
        )
    for learner in diff["toRemove"]:
        cur.execute(
            "DELETE FROM session_expected_learners WHERE session_id = %s AND learner_id = %s",
            (session_id, learner["learnerId"]),
        )

    return {
        "added": diff["toAdd"],
        "removed": diff["toRemove"],
        "blocked": diff["blocked"],
    }


def cancel_session(cur, session_row: dict, reason: str, confirm_with_attendance: bool, user_id: int | None) -> None:
    """Marks a session cancelled. Never deletes the session or any
    attendance_records rows -- if attendance already exists, the caller
    must pass confirm_with_attendance=True (an explicit admin decision, not
    a silent default) or this raises 409."""
    cur.execute(
        "SELECT count(*)::int AS count FROM attendance_records WHERE session_id = %s", (session_row["id"],)
    )
    recorded_count = cur.fetchone()["count"]
    if recorded_count > 0 and not confirm_with_attendance:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "attendance_already_recorded",
                "message": "This session already has recorded attendance. Confirm to cancel anyway -- "
                "the recorded attendance will be preserved, not deleted.",
                "recordedCount": recorded_count,
            },
        )

    cur.execute(
        """
        UPDATE attendance_sessions
        SET status = 'cancelled', cancelled_at = now(), cancellation_reason = %s, cancelled_by = %s
        WHERE id = %s
        """,
        (reason, user_id, session_row["id"]),
    )
