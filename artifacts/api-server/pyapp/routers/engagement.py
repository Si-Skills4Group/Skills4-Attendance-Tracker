"""Stage 4: learner engagement recency report -- see engagement_lib.py for
the full source-selection/permission design. Every endpoint here reuses
the SAME permission helpers as the rest of routers/reports.py
(_enforce_tutor_scope for the list/summary/export views, require_learner_
access for the single-learner detail/drill-down view) -- never
require_attendance_access, which is a session/cover-tutor-scoped concern
that must not be conflated with learner-level report access.

_evidence_tutor_id() is the item-2 correction: _enforce_tutor_scope only
decides which LEARNERS are visible (or, for an admin, which population a
tutorId/cohortId filter narrows to) -- it says nothing about which of a
visible learner's own attendance/catch-up/Bud EVENTS the caller may see.
evidence_tutor_id is None for an admin (full, unrestricted evidence) and
is always the caller's OWN tutorId for a tutor caller, regardless of any
tutorId/cohortId filter they passed -- an admin filtering by tutorId is
narrowing the population they're an admin over, not re-scoping their own
evidence access down to that tutor's."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..auth import require_auth, require_learner_access
from ..engagement_lib import fetch_engagement_rows, fetch_engagement_summary, resolve_learner_engagement_detail
from ..db import get_cursor
from ..report_csv import stream_report_csv
from .reports import _enforce_tutor_scope

router = APIRouter(tags=["engagement"])

ENGAGEMENT_COLUMNS = [
    "learnerRef", "learnerName", "tutorName", "programme",
    "lastLiveAttendance", "lastBudSubmission", "lastBudCompletedActivity", "lastCatchupCompletion",
    "latestEngagementDate", "sourcesText", "daysSinceEngagement",
    "budStatus", "budPlanCurrency", "hasIncompleteSourceCoverage",
    "dataQualityIssuesText", "sourceLimitationsText", "budSyncedAt",
]


def _evidence_tutor_id(session: dict) -> int | None:
    return session.get("tutorId") if session.get("role") == "tutor" else None


def _scope_label(session: dict, tutor_id: int | None) -> str:
    if session.get("role") == "admin" and tutor_id is None:
        return "Organisation-wide -- all authorised active learners"
    if session.get("role") == "admin":
        return "Organisation-wide -- filtered population, full authorised evidence"
    return "Based on engagement visible to you -- your own cohorts' sessions and, where applicable, your own Bud link"


@router.get("/reports/engagement-recency/summary")
def get_engagement_recency_summary(
    tutorId: int | None = None,
    cohortId: int | None = None,
    session: dict = Depends(require_auth),
):
    with get_cursor() as cur:
        tutor_id, cohort_id, _ = _enforce_tutor_scope(cur, session, tutorId, cohortId, None)
        summary = fetch_engagement_summary(cur, tutor_id=tutor_id, cohort_id=cohort_id, evidence_tutor_id=_evidence_tutor_id(session))
    return {**summary, "scopeLabel": _scope_label(session, tutor_id)}


@router.get("/reports/engagement-recency/export")
def export_engagement_recency(
    request: Request,
    tutorId: int | None = None,
    cohortId: int | None = None,
    programme: str | None = None,
    search: str | None = None,
    noEngagementOnly: bool = False,
    minDaysSince: int | None = None,
    session: dict = Depends(require_auth),
):
    with get_cursor() as cur:
        tutor_id, cohort_id, _ = _enforce_tutor_scope(cur, session, tutorId, cohortId, None)
    evidence_tutor_id = _evidence_tutor_id(session)

    def fetch_page(cur, page, page_size):
        return fetch_engagement_rows(
            cur, tutor_id=tutor_id, cohort_id=cohort_id, learner_id=None,
            programme=programme, search=search,
            no_engagement_only=noEngagementOnly, min_days_since=minDaysSince,
            evidence_tutor_id=evidence_tutor_id,
            page=page, page_size=page_size,
        )

    return stream_report_csv(
        request, report_type="engagement_recency", columns=ENGAGEMENT_COLUMNS,
        filename="engagement-recency-report.csv", fetch_page=fetch_page,
        date_from=None, date_to=None,
        filters={
            "tutorId": tutor_id, "cohortId": cohort_id, "programme": programme, "search": search,
            "noEngagementOnly": noEngagementOnly, "minDaysSince": minDaysSince,
        },
    )


@router.get("/reports/engagement-recency")
def list_engagement_recency(
    tutorId: int | None = None,
    cohortId: int | None = None,
    programme: str | None = None,
    search: str | None = None,
    noEngagementOnly: bool = False,
    minDaysSince: int | None = None,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    session: dict = Depends(require_auth),
):
    """Item 5's full list report. Item 4: never restricted to learners who
    already have attendance rows or a confirmed Bud link -- a learner with
    no evidence in any source still appears, with "No recorded
    engagement" rather than a fabricated zero. Filters/counts/pagination
    all share the exact same population+evidence query (engagement_lib.py)
    the /summary and CSV export endpoints use under the same scope."""
    with get_cursor() as cur:
        tutor_id, cohort_id, _ = _enforce_tutor_scope(cur, session, tutorId, cohortId, None)
        rows, total = fetch_engagement_rows(
            cur, tutor_id=tutor_id, cohort_id=cohort_id, learner_id=None,
            programme=programme, search=search,
            no_engagement_only=noEngagementOnly, min_days_since=minDaysSince,
            evidence_tutor_id=_evidence_tutor_id(session),
            page=page, page_size=pageSize,
        )
    return {
        "items": rows, "total": total, "page": page, "pageSize": pageSize,
        "calculatedAt": datetime.now(timezone.utc),
        "scopeLabel": _scope_label(session, tutor_id),
    }


@router.get("/reports/engagement-recency/{learnerId}")
def get_engagement_recency_detail(learnerId: int, session: dict = Depends(require_auth)):
    """Item 6's traceability drill-down -- gated by the same
    require_learner_access every other per-learner report uses (a cover
    tutor's session-only access, via require_attendance_access, has no
    path here at all -- a structurally separate check, never reused for
    this). require_learner_access alone would grant the FULL learner --
    evidence_tutor_id (item 2) additionally restricts which of the
    learner's own events actually come back, exactly like the list."""
    with get_cursor() as cur:
        require_learner_access(cur, learnerId, session)
        detail = resolve_learner_engagement_detail(cur, learnerId, evidence_tutor_id=_evidence_tutor_id(session))
    scope_label = "Organisation-wide -- full authorised evidence" if session.get("role") == "admin" else _scope_label(session, session.get("tutorId"))
    return {**detail, "calculatedAt": datetime.now(timezone.utc), "scopeLabel": scope_label}
