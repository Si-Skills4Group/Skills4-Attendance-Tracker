"""Stage 1 of the operational allocation audit: an admin-only, read-only
reconciliation between Bud's "In Progress" learning-plan population and
this app's active cohort allocations. Thin router -- all classification
logic lives in allocation_reconciliation_lib.py. Visibility only; no write
endpoint exists in this router at all."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from ..allocation_reconciliation_lib import (
    build_corrected_population_summary,
    build_multiple_plan_link_breakdown,
    build_reconciliation,
    compute_ambiguous_plan_breakdown,
    fetch_source_info,
)
from ..auth import require_admin
from ..db import get_cursor

router = APIRouter(tags=["allocation-reconciliation"])

View = Literal["assigned", "unassigned", "needs_review"]
FilterSource = Literal["internal", "bud"]

_UNKNOWN = "Unknown"


def _matches_tutor_filter(row: dict, source: FilterSource | None, value: str | None) -> bool:
    if source is None or not value:
        return True
    if source == "internal":
        if value == _UNKNOWN:
            return row["internalTutorId"] is None
        try:
            return row["internalTutorId"] == int(value)
        except ValueError:
            return False
    # source == "bud" -- name-based, not an id; Bud has no numeric tutor
    # column reachable from a learner, only the free-text tutor_name
    # already carried onto each row.
    if value == _UNKNOWN:
        return not row["budTutorName"]
    return (row["budTutorName"] or "").strip().lower() == value.strip().lower()


def _matches_programme_filter(row: dict, source: FilterSource | None, value: str | None) -> bool:
    """Programme is a single displayed string per row (preferring the
    internal learner's own value, falling back to Bud's programme_name for
    a source-only row -- see build_reconciliation), with programmeSource
    recording which one it actually is. Filtering by source therefore means
    "the displayed value came from this source AND matches", not a
    different underlying column -- there is only one programme string per
    row to compare against either way."""
    if source is None or not value:
        return True
    if row["programmeSource"] != source:
        return False
    if value == _UNKNOWN:
        return not row["programme"]
    return (row["programme"] or "").strip().lower() == value.strip().lower()


@router.get("/allocation-reconciliation")
def get_allocation_reconciliation(
    view: View = "assigned",
    tutorSource: FilterSource | None = None,
    tutorValue: str | None = None,
    programmeSource: FilterSource | None = None,
    programmeValue: str | None = None,
    search: str | None = None,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    # REPEATABLE READ gives every statement inside this one transaction the
    # SAME database snapshot -- required so the classification pass, the
    # ambiguous-plan breakdown and the freshness/source info can never
    # observe a mid-request write (e.g. a concurrent Bud sync commit) and
    # silently disagree with each other. autocommit is the pool default, so
    # this is an explicit opt-in block, not the connection's normal mode.
    with get_cursor() as cur:
        cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        try:
            result = build_reconciliation(cur)
            source_info = fetch_source_info(cur)
            ambiguous_plan_breakdown = compute_ambiguous_plan_breakdown(cur)
        finally:
            cur.execute("COMMIT")

    rows = result["rows"]

    def matches(row: dict) -> bool:
        if not _matches_tutor_filter(row, tutorSource, tutorValue):
            return False
        if not _matches_programme_filter(row, programmeSource, programmeValue):
            return False
        if search:
            needle = search.strip().lower()
            haystack = f"{row['learnerName'] or ''} {row['learnerRef'] or ''}".lower()
            if needle not in haystack:
                return False
        return True

    filtered = [r for r in rows if matches(r)]

    # Counts reflect the SAME filters as the lists (minus `view` itself, so
    # every tab's count is consistent with what its own list would show
    # under the current filter set) -- never the unfiltered totals. Units
    # are explicit and never conflated: confirmedAssigned/confirmedUnassigned
    # are DISTINCT internal learners; needsReview is three separate numbers
    # (see allocation_reconciliation_lib._build_counts's own docstring for
    # why a single "count" would misrepresent this population -- one person
    # can legitimately appear on more than one Needs Review row).
    assigned_filtered = [r for r in filtered if r["classification"] == "assigned"]
    unassigned_filtered = [r for r in filtered if r["classification"] == "unassigned"]
    needs_review_filtered = [r for r in filtered if r["classification"] == "needs_review"]
    counts = {
        "confirmedAssigned": len({r["internalLearnerId"] for r in assigned_filtered}),
        "confirmedUnassigned": len({r["internalLearnerId"] for r in unassigned_filtered}),
        "needsReview": {
            "recordCount": len(needs_review_filtered),
            "distinctPeopleCount": len({r["internalLearnerId"] for r in needs_review_filtered if r["internalLearnerId"] is not None}),
            "budLearningPlanRowCount": sum(1 for r in needs_review_filtered if r["budLearningPlanId"] is not None),
        },
    }

    view_rows = [r for r in filtered if r["classification"] == view]

    # Distinct filter option values, computed from the full (unfiltered)
    # dataset so switching tabs/filters never narrows what's offered --
    # Unknown is always present so a source-only or unmatched record stays
    # reachable via the filter even when it has no Bud tutor/programme name.
    bud_tutor_names = sorted({r["budTutorName"] for r in rows if r["budTutorName"]})
    bud_programmes = sorted({r["programme"] for r in rows if r["programme"] and r["programmeSource"] == "bud"})

    total = len(view_rows)
    start = (page - 1) * pageSize
    items = view_rows[start:start + pageSize]

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": pageSize,
        "counts": counts,
        "availableFilters": {
            "budTutorNames": bud_tutor_names + [_UNKNOWN],
            "budProgrammes": bud_programmes + [_UNKNOWN],
        },
        "sourceInfo": source_info,
        "ambiguousPlanBreakdown": ambiguous_plan_breakdown,
        "calculatedAt": result["calculatedAt"],
    }


@router.get("/allocation-reconciliation/population-summary")
def get_population_summary(_session: dict = Depends(require_admin)):
    """Stage 2, item 1: the corrected, same-snapshot Stage 1 population
    summary -- see build_corrected_population_summary's own docstring for
    exactly what each field means. Retires the "220"/"429"/"191" labels
    from the original completion report entirely; nothing here reuses
    those names."""
    with get_cursor() as cur:
        cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        try:
            summary = build_corrected_population_summary(cur)
            source_info = fetch_source_info(cur)
        finally:
            cur.execute("COMMIT")
    return {**summary, "sourceInfo": source_info}


@router.get("/allocation-reconciliation/multiple-plan-breakdown")
def get_multiple_plan_breakdown(_session: dict = Depends(require_admin)):
    """Stage 2, item 7: read-only visibility into whether an existing Bud
    link, for a person with one current plan plus historical plans, points
    at the current plan, a historical one, or doesn't exist. Never relinks
    or changes the conservative ambiguity policy."""
    with get_cursor() as cur:
        cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        try:
            breakdown = build_multiple_plan_link_breakdown(cur)
        finally:
            cur.execute("COMMIT")
    return breakdown
