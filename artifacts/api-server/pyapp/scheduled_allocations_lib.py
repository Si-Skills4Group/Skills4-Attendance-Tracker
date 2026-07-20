"""Applies scheduled learner transfers once their effective date arrives.

No background job/cron infrastructure exists in this app -- a scheduled
transfer is instead applied lazily: once at app boot (catching up on
anything that became due while the app was down) and again at the top of
the read paths that resolve a learner's *current* tutor/cohort, so a
transfer takes effect no later than the first read on or after its
effective date. Until then, learners.tutor_id/cohort_id are untouched --
apply_transfer is the only thing that ever writes them.
"""
from datetime import date

from .allocation_lib import apply_transfer


def apply_due_scheduled_allocations(cur, as_of: date | None = None) -> list[int]:
    as_of = as_of or date.today()
    cur.execute(
        """
        SELECT id, learner_id AS "learnerId", new_tutor_id AS "newTutorId",
               new_cohort_id AS "newCohortId", effective_date AS "effectiveDate",
               transfer_reason AS "transferReason", created_by AS "createdBy"
        FROM scheduled_allocations
        WHERE status = 'pending' AND effective_date <= %s
        ORDER BY effective_date, id
        """,
        (as_of,),
    )
    due = cur.fetchall()

    applied_ids = []
    for row in due:
        # Atomically claim this row before applying it -- a single UPDATE is
        # safe even without an explicit transaction (the connection pool
        # runs in autocommit mode), and prevents a concurrent caller from
        # double-applying the same transfer.
        cur.execute(
            "UPDATE scheduled_allocations SET status = 'applying' WHERE id = %s AND status = 'pending'",
            (row["id"],),
        )
        if cur.rowcount == 0:
            continue

        # Defensive: a pending transfer is cancelled when its learner is
        # deleted (see routers/learners.py::delete_learner), so this should
        # never actually match a deleted learner -- but treat one the same
        # as "not found" rather than resurrecting them into an allocation.
        cur.execute(
            'SELECT id, tutor_id AS "tutorId", cohort_id AS "cohortId" FROM learners WHERE id = %s AND deleted_at IS NULL',
            (row["learnerId"],),
        )
        learner = cur.fetchone()
        if learner:
            apply_transfer(
                cur,
                learner,
                row["newTutorId"],
                row["newCohortId"],
                row["effectiveDate"],
                row["transferReason"],
                row["createdBy"],
            )

        cur.execute(
            "UPDATE scheduled_allocations SET status = 'applied', applied_at = now() WHERE id = %s",
            (row["id"],),
        )
        applied_ids.append(row["id"])

    return applied_ids
