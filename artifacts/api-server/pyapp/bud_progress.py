"""Read-only access to public.learner_progress -- a table populated by a
*separate*, already-deployed sync service (the "Bud" LMS integration), not
by anything in this codebase. This module must never write to it: no
INSERT/UPDATE/DELETE, ever, and no new cohorts or allocation changes are
derived from it in this phase.

There is no foreign key linking learner_progress to learners. The join used
here is a best-effort heuristic on the Unique Learner Number
(learners.uln <-> learner_progress.unique_learner_number, both meant to be
the same UK apprenticeship identifier; learners.uln already has a unique
index in this app, making it the more reliable of the two plausible keys
versus the free-text learner_ref/learner_reference pair). Production
`learners` currently holds only placeholder rows, so this join is
unverifiable against real data yet -- treat it as provisional, revisit once
real learner data exists. Every caller must tolerate a missing/no-match
result; Bud data must never break a dashboard.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class BudProgress(BaseModel):
    activityProgress: float | None
    activitiesOverdue: int | None
    lastSubmissionDate: datetime | None
    lastCompletedActivity: date | None
    statusDesc: str | None
    learningPlanUrl: str | None
    syncedAt: datetime | None


def get_bud_progress_by_uln(cur, ulns: list[str]) -> dict[str, BudProgress]:
    """One batched SELECT for many learners at once -- avoids an N+1 when
    zipping Bud context onto a list of learners on a dashboard."""
    clean = [u for u in ulns if u]
    if not clean:
        return {}
    cur.execute(
        """
        SELECT unique_learner_number AS "uln",
               activity_progress AS "activityProgress",
               activities_overdue AS "activitiesOverdue",
               last_submission_date AS "lastSubmissionDate",
               last_completed_activity AS "lastCompletedActivity",
               status_desc AS "statusDesc",
               learning_plan_url AS "learningPlanUrl",
               synced_at AS "syncedAt"
        FROM public.learner_progress
        WHERE unique_learner_number = ANY(%s)
        """,
        (clean,),
    )
    result: dict[str, BudProgress] = {}
    for row in cur.fetchall():
        uln = row.pop("uln")
        if uln is None:
            continue
        # Bud can have more than one learning-plan row per learner (e.g.
        # across programmes); keep the most recently synced one.
        existing = result.get(uln)
        candidate = BudProgress(**row)
        if existing is None or (candidate.syncedAt and (not existing.syncedAt or candidate.syncedAt > existing.syncedAt)):
            result[uln] = candidate
    return result


def get_bud_sync_health(cur) -> dict:
    """Aggregate-only, no per-learner join -- backs an admin "Bud sync
    status" card independent of whether any local learner data matches."""
    cur.execute('SELECT count(*)::int AS "totalSynced", max(synced_at) AS "lastSyncedAt" FROM public.learner_progress')
    return cur.fetchone()
