"""Stage 1 of the operational allocation audit: a read-only reconciliation
between Bud's "In Progress" learning-plan population and this app's active
cohort allocations. Visibility only -- nothing in this module writes to
learners, cohorts, tutors, learner_cohort_enrollments, attendance data, or
any Bud-sync-trial table (bud_sync_job/bud_sync_item/bud_learner_link/
bud_sync_baseline*).

Deliberately reuses bud_sync_lib's own matching primitives
(_fetch_bud_rows, _get_ambiguous_learner_references, classify_row,
build_bulk_lookups) rather than re-deriving the Bud <-> learner matching
hierarchy -- classify_row is already a pure, side-effect-free function
designed to be called standalone (see its own docstring: "for tests/
one-off checks"), so this is the same reuse pattern the test suite already
relies on, not a new one invented for this report. build_bulk_lookups is
an opt-in parameter on classify_row and its helpers (see bud_sync_lib.py) --
run_preview/run_commit never pass it and are completely unaffected by its
existence; a dedicated equivalence test suite
(test_bud_sync_bulk_lookups_equivalence.py) proves classify_row returns
byte-for-byte identical results with or without it.

Two independent classification questions, kept separate throughout:
1. Does this Bud learning-plan row resolve, via the established hierarchy,
   to exactly one internal learner, with no unresolved ambiguity? (person
   identification -- learner_reference is Bud's per-PERSON identifier;
   learning_plan_id is per-ENROLMENT, and a person can have more than one
   concurrent/historical plan.)
2. For a learner who does resolve cleanly, does their *current* Bud status
   and this app's *current* internal status/home-cohort agree with what
   the "confirmed active allocation population" requires?

A row that fails (1) is never silently resolved by picking a "most likely"
candidate (latest start date, name, email) -- it is surfaced in
Needs Review instead. This applies even when a bud_learner_link happens to
point cleanly at ONE of a person's several concurrent Bud plans: if that
person's learner_reference is ambiguous (more than one Bud row), the link
could be anchored to a historical plan while a different plan is now the
current one, so the whole person is routed to Needs Review rather than
trusted on the strength of the link alone.

Counting units, kept explicit throughout (never conflated): Assigned/
Unassigned counts are DISTINCT INTERNAL LEARNERS (people) -- guaranteed
distinct because a non-ambiguous, cleanly-matched Bud row maps to exactly
one internal learner, and ambiguous rows (which could otherwise map more
than one row to the same person) are routed to Needs Review instead
(never counted as confirmed). Needs Review is NOT a count of people at
all -- one person can appear on more than one Needs Review row (e.g. every
one of their ambiguous plans generates its own row) -- so it is reported
as three separate numbers: review records (row count), distinct
identifiable people (distinct non-null internalLearnerId across those
rows), and Bud learning-plan rows (rows carrying a budLearningPlanId)."""
from __future__ import annotations

from datetime import datetime

from .bud_sync_lib import (
    _ELIGIBLE_STATUS_DESC,
    _fetch_bud_rows,
    _get_ambiguous_learner_references,
    build_bulk_lookups,
    classify_row,
)

# match_status values (from classify_row) that represent a clean, single-
# candidate resolution to one internal learner -- never a conflict, and
# never the "no match found at all" fallback.
_CLEAN_MATCH_STATUSES = {"unchanged", "existing_update", "status_change"}

# Primary Needs Review reason, in priority order -- a row can trip more than
# one condition (e.g. an ambiguous reference that is also tutor-unmatched);
# reviewReason is always the first of these that applies, issueFlags lists
# every applicable one so nothing is hidden by the single-reason summary.
_REASON_PRIORITY = [
    "ambiguous_multiple_plans",
    "matched_learner_deleted",
    "learner_already_linked_elsewhere",
    "unsupported_status_transition",
    "bud_unresolved_tutor_unmatched",
    "bud_unresolved_no_internal_learner",
    "bud_unresolved_missing_required_field",
    "bud_in_progress_internal_inactive",
    "bud_status_not_in_progress_but_internally_active",
    "no_reliable_bud_match",
]


def _fetch_cohort_lookup(cur) -> dict[int, dict]:
    cur.execute(
        """
        SELECT id, name, active, deleted_at AS "deletedAt", membership_type AS "membershipType"
        FROM cohorts
        """
    )
    return {row["id"]: row for row in cur.fetchall()}


def classify_home_cohort(cohort_id: int | None, cohort_lookup: dict[int, dict]) -> tuple[str, str | None, dict | None]:
    """Returns (classification, reason, cohortInfo). classification is
    "assigned" or "unassigned" -- never anything else; a secondary
    (Functional Skills) enrollment is a completely different table
    (learner_cohort_enrollments) and is never consulted here, matching the
    instruction that it must not satisfy the home-cohort requirement."""
    if cohort_id is None:
        return "unassigned", "no_home_cohort", None

    cohort = cohort_lookup.get(cohort_id)
    if cohort is None:
        return "unassigned", "cohort_not_found", None

    cohort_info = {
        "id": cohort["id"], "name": cohort["name"], "active": cohort["active"],
        "deletedAt": cohort["deletedAt"], "membershipType": cohort["membershipType"],
    }
    if cohort["deletedAt"] is not None:
        return "unassigned", "cohort_deleted", cohort_info
    if not cohort["active"]:
        return "unassigned", "cohort_inactive", cohort_info
    if cohort["membershipType"] == "secondary":
        return "unassigned", "cohort_membership_type_secondary", cohort_info
    if cohort["membershipType"] != "primary":
        return "unassigned", "cohort_membership_type_invalid", cohort_info
    return "assigned", None, cohort_info


def _tutor_name(tutor_id: int | None, tutors_by_id: dict[int, dict]) -> str | None:
    tutor = tutors_by_id.get(tutor_id) if tutor_id is not None else None
    return f"{tutor['firstName']} {tutor['lastName']}" if tutor else None


def build_reconciliation(cur) -> dict:
    """The single heavy pass this whole report is built on: classifies
    every current Bud learning-plan row via the established matching
    hierarchy, cross-references it against the confirmed-active-learner
    population, and returns one row per Bud learning-plan PLUS one row per
    internally-active learner with no reliable Bud match at all (a case
    that would otherwise never appear, since it has no Bud row to anchor
    to). Read-only throughout -- calls only SELECT-issuing helpers.

    Performance: every per-row lookup classify_row would otherwise issue
    against the database is served from one bulk pre-load
    (build_bulk_lookups, 3 queries total regardless of row count) instead
    of up to ~6 queries PER Bud row -- see bud_sync_lib.py's
    build_bulk_lookups docstring and test_bud_sync_bulk_lookups_equivalence.py
    for the equivalence proof. Measured on ~8,000 production Bud rows: this
    reduced the endpoint from ~585s to ~3-4s (see the router's own
    docstring/tests for the exact measured figures)."""
    calculated_at = datetime.now().astimezone()

    bud_rows = _fetch_bud_rows(cur)
    ambiguous_references = _get_ambiguous_learner_references(cur)
    lookups = build_bulk_lookups(cur)
    cohort_lookup = _fetch_cohort_lookup(cur)

    active_learners = {
        learner_id: learner for learner_id, learner in lookups["learnersById"].items()
        if learner["status"] == "active"
    }

    rows: list[dict] = []
    matched_active_learner_ids: set[int] = set()

    # Ambiguity is a property of the PERSON (learner_reference), not of any
    # one Bud row -- classify_row's own ambiguity check fires before it ever
    # resolves internal_learner_id (see classify_row's early-return branches),
    # so an ambiguous row's item["internal_learner_id"] is reliably None even
    # though a specific internal learner is often still identifiable. Any
    # internal learner whose OWN learner_ref is ambiguous is affected
    # regardless of which specific row classify_row happened to process --
    # computed once here from the active-learner population directly,
    # rather than trusting per-row output that may never carry the id.
    ambiguous_internal_learner_ids = {
        learner_id for learner_id, learner in active_learners.items()
        if learner["learnerRef"] and learner["learnerRef"] in ambiguous_references
    }

    # Pass 1: classify every Bud row (now entirely from the pre-loaded
    # lookups -- zero additional queries per row) and collect which internal
    # learners got a clean (non-ambiguous, non-conflict) match, so pass 2
    # can find the active learners nothing here ever pointed at.
    classified: list[tuple[dict, dict, bool]] = []
    for bud_row in bud_rows:
        item = classify_row(cur, bud_row, None, ambiguous_references, lookups)
        learner_reference = bud_row.get("learnerReference")
        is_ambiguous = bool(learner_reference) and learner_reference in ambiguous_references
        classified.append((bud_row, item, is_ambiguous))
        if not is_ambiguous and item["match_status"] in _CLEAN_MATCH_STATUSES and item["internal_learner_id"] is not None:
            matched_active_learner_ids.add(item["internal_learner_id"])

    for bud_row, item, is_ambiguous in classified:
        bud_status = bud_row.get("statusDesc")
        is_in_progress = bud_status == _ELIGIBLE_STATUS_DESC
        match_status = item["match_status"]
        reason = item["reason"]
        internal_learner_id = item["internal_learner_id"]
        if internal_learner_id is None and is_ambiguous:
            # Display only (who this ambiguous row is plausibly about) --
            # never used to decide classification/eligibility. O(1) dict
            # read against the same bulk lookup, not a query.
            learner_reference = bud_row.get("learnerReference")
            candidates = lookups["learnersByReference"].get(learner_reference, [])
            if len(candidates) == 1:
                internal_learner_id = candidates[0]["id"]
        learner = lookups["learnersById"].get(internal_learner_id) if internal_learner_id else None

        # existing_before_trial (unmatched, non-actionable status) is Bud's
        # ordinary historical churn -- classify_row's own docstring already
        # treats this as "never shown in an operational queue"; reproducing
        # that here keeps this report focused on what's actually actionable
        # instead of dumping every non-In-Progress unmatched row.
        if match_status == "existing_before_trial":
            continue

        issue_flags: list[str] = []
        primary_reason: str | None = None
        classification = "needs_review"

        if is_ambiguous:
            issue_flags.append("ambiguous_multiple_plans")
            if reason:
                issue_flags.append(f"detail:{reason}")
        elif match_status == "conflict":
            if reason == "matched_learner_no_longer_exists":
                issue_flags.append("matched_learner_deleted")
            elif reason == "learner_already_linked_to_a_different_bud_record":
                issue_flags.append("learner_already_linked_elsewhere")
            elif reason == "unsupported_status_transition":
                issue_flags.append("unsupported_status_transition")
            elif reason == "tutor_unmatched":
                issue_flags.append("bud_unresolved_tutor_unmatched")
                issue_flags.append("tutor_matching_conflict")
            elif reason in ("missing_start_date", "missing_programme", "missing_learner_reference"):
                issue_flags.append("bud_unresolved_missing_required_field")
                issue_flags.append(f"detail:{reason}")
            else:
                issue_flags.append("bud_unresolved_no_internal_learner")
                if reason:
                    issue_flags.append(f"detail:{reason}")
        elif match_status == "new":
            issue_flags.append("bud_unresolved_no_internal_learner")
        elif match_status in _CLEAN_MATCH_STATUSES and learner is not None:
            internal_status = learner["status"]
            if is_in_progress and internal_status != "active":
                issue_flags.append("bud_in_progress_internal_inactive")
                issue_flags.append(f"internal_status:{internal_status}")
            elif not is_in_progress and internal_status == "active":
                issue_flags.append("bud_status_not_in_progress_but_internally_active")
                issue_flags.append(f"bud_status:{bud_status or 'blank'}")
            elif is_in_progress and internal_status == "active":
                classification = "confirmed"
            else:
                # Neither side considers this learner current (Bud isn't
                # In Progress, and internal status isn't active) -- both
                # systems agree, so there is nothing to reconcile or review
                # here. Matches the existing_before_trial exclusion above:
                # this report surfaces discrepancies, not routine agreement.
                continue

        if classification != "confirmed":
            for candidate in _REASON_PRIORITY:
                if candidate in issue_flags:
                    primary_reason = candidate
                    break
            if primary_reason is None and issue_flags:
                primary_reason = issue_flags[0]
            # Every Needs Review row must carry a reason -- if every branch
            # above genuinely left issue_flags empty, that is a
            # classification bug, not a valid "no reason" state (a past
            # version of this function had exactly that bug for the
            # "both sides agree not current" case, now excluded via the
            # `continue` above instead of falling through to here).
            assert primary_reason is not None, f"Needs Review row with no reason: bud_row={bud_row!r} item={item!r}"

        home_classification, home_reason, cohort_info = (
            classify_home_cohort(learner["cohortId"], cohort_lookup) if learner else ("unassigned", None, None)
        )
        if classification == "confirmed":
            classification = home_classification
            primary_reason = home_reason

        rows.append({
            "learnerName": f"{learner['firstName']} {learner['lastName']}" if learner else (
                f"{bud_row.get('learnerForename') or ''} {bud_row.get('learnerSurname') or ''}".strip() or None
            ),
            "learnerRef": (learner["learnerRef"] if learner else bud_row.get("learnerReference")),
            "internalLearnerId": internal_learner_id,
            "budLearningPlanId": bud_row.get("learningPlanId"),
            "internalStatus": learner["status"] if learner else None,
            "budStatus": bud_status,
            "internalTutorId": learner["tutorId"] if learner else None,
            "internalTutorName": _tutor_name(learner["tutorId"], lookups["tutorsById"]) if learner else None,
            "budTutorName": bud_row.get("tutorName"),
            "programme": (learner["programme"] if learner else None) or bud_row.get("programmeName"),
            "programmeSource": "internal" if learner else ("bud" if bud_row.get("programmeName") else None),
            "homeCohortId": learner["cohortId"] if learner else None,
            "homeCohortName": cohort_info["name"] if cohort_info else None,
            "homeCohortActive": cohort_info["active"] if cohort_info else None,
            "homeCohortDeleted": cohort_info["deletedAt"] is not None if cohort_info else None,
            "homeCohortMembershipType": cohort_info["membershipType"] if cohort_info else None,
            "classification": classification,
            "reviewReason": primary_reason,
            "issueFlags": issue_flags,
        })

    # Pass 2: internally-active learners with no reliable Bud match at all --
    # never touched by pass 1 above (no bud row resolved to them cleanly),
    # and not merely ambiguous (that's its own, more specific reason).
    for learner_id, learner in active_learners.items():
        if learner_id in matched_active_learner_ids or learner_id in ambiguous_internal_learner_ids:
            continue
        home_classification, home_reason, cohort_info = classify_home_cohort(learner["cohortId"], cohort_lookup)
        rows.append({
            "learnerName": f"{learner['firstName']} {learner['lastName']}",
            "learnerRef": learner["learnerRef"],
            "internalLearnerId": learner_id,
            "budLearningPlanId": None,
            "internalStatus": learner["status"],
            "budStatus": None,
            "internalTutorId": learner["tutorId"],
            "internalTutorName": _tutor_name(learner["tutorId"], lookups["tutorsById"]),
            "budTutorName": None,
            "programme": learner.get("programme"),
            "programmeSource": "internal",
            "homeCohortId": learner["cohortId"],
            "homeCohortName": cohort_info["name"] if cohort_info else None,
            "homeCohortActive": cohort_info["active"] if cohort_info else None,
            "homeCohortDeleted": cohort_info["deletedAt"] is not None if cohort_info else None,
            "homeCohortMembershipType": cohort_info["membershipType"] if cohort_info else None,
            "classification": "needs_review",
            "reviewReason": "no_reliable_bud_match",
            "issueFlags": ["no_reliable_bud_match"],
        })

    counts = _build_counts(rows)

    return {"rows": rows, "counts": counts, "calculatedAt": calculated_at}


def _build_counts(rows: list[dict]) -> dict:
    """Counting units, explicit and never conflated (see module docstring):
    Assigned/Unassigned are DISTINCT internal learner counts (asserted, not
    just assumed, since a bug here would silently double-count a person
    across two rows). Needs Review is reported as three separate numbers,
    never collapsed into one "count"."""
    assigned_ids = {r["internalLearnerId"] for r in rows if r["classification"] == "assigned"}
    unassigned_ids = {r["internalLearnerId"] for r in rows if r["classification"] == "unassigned"}
    assigned_rows = [r for r in rows if r["classification"] == "assigned"]
    unassigned_rows = [r for r in rows if r["classification"] == "unassigned"]
    assert len(assigned_ids) == len(assigned_rows), "a learner appears on more than one Assigned row -- join fan-out bug"
    assert len(unassigned_ids) == len(unassigned_rows), "a learner appears on more than one Unassigned row -- join fan-out bug"

    needs_review_rows = [r for r in rows if r["classification"] == "needs_review"]
    needs_review_people = {r["internalLearnerId"] for r in needs_review_rows if r["internalLearnerId"] is not None}
    needs_review_bud_rows = [r for r in needs_review_rows if r["budLearningPlanId"] is not None]

    return {
        "confirmedAssigned": len(assigned_ids),
        "confirmedUnassigned": len(unassigned_ids),
        "needsReview": {
            "recordCount": len(needs_review_rows),
            "distinctPeopleCount": len(needs_review_people),
            "budLearningPlanRowCount": len(needs_review_bud_rows),
        },
    }


def compute_ambiguous_plan_breakdown(cur) -> dict:
    """Answers exactly what the 'ambiguous_multiple_plans' Needs Review
    reason represents, in the terms requested: how many distinct people are
    affected, how many Bud learning-plan rows are implicated, and -- the
    part that actually matters for triage -- how many of those people have
    a genuinely concurrent conflict (2+ plans BOTH currently In Progress)
    versus a merely historical one (their only In Progress plan sits
    alongside older, already-finished plans, or they have no In Progress
    plan at all yet remain internally active). Read-only; does not change
    the conservative "never auto-resolve" policy -- this is visibility
    into the same Needs Review population, not a new resolution path."""
    ambiguous_references = _get_ambiguous_learner_references(cur)
    if not ambiguous_references:
        return {
            "distinctLearnerReferences": 0, "affectedBudLearningPlans": 0, "affectedInternalLearners": 0,
            "withMultipleInProgressPlans": 0, "withOneInProgressPlusHistorical": 0, "withOnlyHistoricalPlans": 0,
        }

    bud_rows = _fetch_bud_rows(cur)
    lookups = build_bulk_lookups(cur)

    rows_by_reference: dict[str, list[dict]] = {}
    for bud_row in bud_rows:
        ref = bud_row.get("learnerReference")
        if ref in ambiguous_references:
            rows_by_reference.setdefault(ref, []).append(bud_row)

    affected_internal_learners = 0
    with_multiple_in_progress = 0
    with_one_in_progress_plus_historical = 0
    with_only_historical = 0
    total_plans = 0

    for reference, plans in rows_by_reference.items():
        total_plans += len(plans)
        # By _get_ambiguous_learner_references' own definition this
        # reference matches exactly one internal learner (learner_ref is
        # unique) -- confirmed here rather than assumed.
        candidates = lookups["learnersByReference"].get(reference, [])
        if len(candidates) == 1:
            affected_internal_learners += 1
        in_progress_count = sum(1 for p in plans if p.get("statusDesc") == _ELIGIBLE_STATUS_DESC)
        if in_progress_count >= 2:
            with_multiple_in_progress += 1
        elif in_progress_count == 1:
            with_one_in_progress_plus_historical += 1
        else:
            with_only_historical += 1

    return {
        "distinctLearnerReferences": len(rows_by_reference),
        "affectedBudLearningPlans": total_plans,
        "affectedInternalLearners": affected_internal_learners,
        "withMultipleInProgressPlans": with_multiple_in_progress,
        "withOneInProgressPlusHistorical": with_one_in_progress_plus_historical,
        "withOnlyHistoricalPlans": with_only_historical,
    }


def build_multiple_plan_link_breakdown(cur) -> dict:
    """Stage 2, item 7: a READ-ONLY breakdown of the "one In Progress plan
    plus historical plans" ambiguous population (see
    compute_ambiguous_plan_breakdown's withOneInProgressPlusHistorical) --
    for each such person, does an EXISTING bud_learner_link already point
    at their current plan, at one of their historical plans, or not exist
    at all? This never relinks, reclassifies, or picks a plan by latest
    start date -- it only reports what's already true, to inform a future,
    separate matching-policy change. The conservative "never auto-resolve
    ambiguity" policy is completely unchanged by this function existing."""
    ambiguous_references = _get_ambiguous_learner_references(cur)
    if not ambiguous_references:
        return {"linkPointsToCurrentPlan": 0, "linkPointsToHistoricalPlan": 0,
                "noExistingLink": 0, "conflictingOrUnresolved": 0, "details": []}

    bud_rows = _fetch_bud_rows(cur)
    lookups = build_bulk_lookups(cur)

    rows_by_reference: dict[str, list[dict]] = {}
    for bud_row in bud_rows:
        ref = bud_row.get("learnerReference")
        if ref in ambiguous_references:
            rows_by_reference.setdefault(ref, []).append(bud_row)

    link_points_to_current = 0
    link_points_to_historical = 0
    no_existing_link = 0
    conflicting_or_unresolved = 0
    details: list[dict] = []

    for reference, plans in rows_by_reference.items():
        in_progress_plans = [p for p in plans if p.get("statusDesc") == _ELIGIBLE_STATUS_DESC]
        if len(in_progress_plans) != 1:
            # Out of scope for this specific breakdown -- genuinely
            # concurrent (2+) In Progress plans and "only historical, no
            # current plan at all" are already their own distinct buckets
            # in compute_ambiguous_plan_breakdown; this function only
            # answers the question for the "exactly one current plan"
            # shape, where "does the link point at the right one" is even
            # a meaningful question to ask.
            continue

        candidates = lookups["learnersByReference"].get(reference, [])
        current_plan_id = in_progress_plans[0]["learningPlanId"]
        plan_ids_for_reference = {p["learningPlanId"] for p in plans}

        if len(candidates) != 1:
            # By _get_ambiguous_learner_references' own definition this
            # reference matches exactly one internal learner -- a
            # different count here would itself be a data-integrity
            # surprise, not a normal case, so it's its own bucket rather
            # than silently ignored.
            conflicting_or_unresolved += 1
            details.append({
                "learnerReference": reference, "internalLearnerId": None, "learnerName": None,
                "category": "conflicting_or_unresolved", "currentPlanId": current_plan_id,
                "linkedPlanId": None, "totalPlansForReference": len(plans),
            })
            continue

        learner = candidates[0]
        link = lookups["linksByLearnerId"].get(learner["id"])
        if link is None:
            category = "no_existing_link"
            no_existing_link += 1
        elif link["budLearningPlanId"] == current_plan_id:
            category = "link_points_to_current"
            link_points_to_current += 1
        elif link["budLearningPlanId"] in plan_ids_for_reference:
            category = "link_points_to_historical"
            link_points_to_historical += 1
        else:
            # The link points at a plan_id that isn't even among this
            # reference's own current plans any more (e.g. Bud stopped
            # reporting it) -- a genuinely unresolved/conflicting state,
            # not simply "historical".
            category = "conflicting_or_unresolved"
            conflicting_or_unresolved += 1

        details.append({
            "learnerReference": reference, "internalLearnerId": learner["id"],
            "learnerName": f"{learner['firstName']} {learner['lastName']}",
            "category": category, "currentPlanId": current_plan_id,
            "linkedPlanId": link["budLearningPlanId"] if link else None,
            "totalPlansForReference": len(plans),
        })

    return {
        "linkPointsToCurrentPlan": link_points_to_current,
        "linkPointsToHistoricalPlan": link_points_to_historical,
        "noExistingLink": no_existing_link,
        "conflictingOrUnresolved": conflicting_or_unresolved,
        "details": details,
    }


def build_corrected_population_summary(cur) -> dict:
    """Stage 2, item 1: a corrected, same-snapshot Stage 1 population
    summary. The Stage 1 corrective-pass completion report mislabelled
    three historical comparison figures -- this function replaces those
    labels entirely with populations that describe exactly what they are,
    computed fresh from ONE snapshot (reusing build_reconciliation's own
    row-level output, never a separately re-derived query that could drift
    from the headline counts):

    - confirmedAssigned / confirmedUnassigned / needsReview: identical
      definitions to build_reconciliation's own counts.
    - broaderActiveWithoutActiveHomeCohort: EVERY internally active learner
      lacking a valid home cohort (classify_home_cohort's own definition --
      active, non-deleted, membership_type='primary'), regardless of what
      Bud says about them at all, or whether Bud has a record of them at
      all. A strict superset of confirmedUnassigned by construction.
    - budCorroboratedActiveWithoutActiveHomeCohort: the same population,
      narrowed to learners Bud references AT ALL -- their learner_ref or
      uln appears somewhere in learner_progress, at ANY status_desc,
      whether or not that reference resolves cleanly or is ambiguous. This
      is a WEAKER requirement than confirmedUnassigned's "Bud confirms
      In Progress via a clean, non-conflicted match", so it sits strictly
      between the other two populations.
    - exclusionBreakdown: a mutually-exclusive partition of
      broaderActiveWithoutActiveHomeCohort by each learner's actual
      reconciliation-row classification/reason. Every learner in that
      population has exactly one row in build_reconciliation's output (its
      own Pass 1/Pass 2 design guarantees this), so grouping by
      (classification, reviewReason) is exhaustive and non-overlapping by
      construction -- summing the breakdown always reproduces
      broaderActiveWithoutActiveHomeCohort exactly, and the
      "confirmed_unassigned" entry always equals confirmedUnassigned
      exactly.
    """
    reconciliation = build_reconciliation(cur)
    rows = reconciliation["rows"]
    bud_rows = _fetch_bud_rows(cur)
    lookups = build_bulk_lookups(cur)

    corroborated_references = {r["learnerReference"] for r in bud_rows if r.get("learnerReference")}
    corroborated_ulns = {r["uln"] for r in bud_rows if r.get("uln")}

    def has_valid_home_cohort(row: dict) -> bool:
        return bool(
            row["homeCohortId"] and row["homeCohortActive"] and not row["homeCohortDeleted"]
            and row["homeCohortMembershipType"] == "primary"
        )

    broader_rows = [r for r in rows if r["internalStatus"] == "active" and not has_valid_home_cohort(r)]

    # A single active learner can legitimately appear on MORE THAN ONE row
    # here (see build_reconciliation's own module docstring: an ambiguous
    # person gets one row per plan, and a person can independently pick up
    # an unrelated "learner_already_linked_elsewhere" conflict row from a
    # completely different Bud row's ULN/reference collision, alongside
    # their own genuinely-matched row). This population must be counted in
    # DISTINCT LEARNERS -- exactly the counting-units mistake the rest of
    # this Stage 2 item exists to correct, so rows are grouped by
    # internalLearnerId FIRST, and every count below is derived from that
    # grouping, never from a raw row count.
    rows_by_learner: dict[int, list[dict]] = {}
    for row in broader_rows:
        rows_by_learner.setdefault(row["internalLearnerId"], []).append(row)

    exclusion_counter: dict[str, int] = {}
    corroborated_count = 0
    for learner_id, learner_rows in rows_by_learner.items():
        # One row landing in "unassigned" is the authoritative outcome for
        # that person (their own cleanly-matched, Bud-confirmed plan lacks a
        # valid home cohort) -- it wins over any other, unrelated
        # needs_review row a different Bud record incidentally attached to
        # them. Otherwise, pick the single most-severe applicable reason via
        # the same _REASON_PRIORITY this module already uses for a row's own
        # reviewReason, so the label shown here is never an arbitrary "first
        # row found".
        reasons_present = {r["reviewReason"] for r in learner_rows if r["reviewReason"]}
        if any(r["classification"] == "unassigned" for r in learner_rows):
            key = "confirmed_unassigned"
        else:
            key = next((r for r in _REASON_PRIORITY if r in reasons_present), None) or next(iter(reasons_present), "unknown")
        exclusion_counter[key] = exclusion_counter.get(key, 0) + 1

        learner = lookups["learnersById"].get(learner_id)
        is_corroborated = bool(
            learner and (
                (learner["learnerRef"] and learner["learnerRef"] in corroborated_references)
                or (learner["uln"] and learner["uln"] in corroborated_ulns)
            )
        )
        if is_corroborated:
            corroborated_count += 1

    exclusion_breakdown = [
        {"reason": reason, "count": count}
        for reason, count in sorted(exclusion_counter.items(), key=lambda kv: -kv[1])
    ]

    return {
        "calculatedAt": reconciliation["calculatedAt"],
        "confirmedAssigned": reconciliation["counts"]["confirmedAssigned"],
        "confirmedUnassigned": reconciliation["counts"]["confirmedUnassigned"],
        "needsReview": reconciliation["counts"]["needsReview"],
        "broaderActiveWithoutActiveHomeCohort": len(rows_by_learner),
        "budCorroboratedActiveWithoutActiveHomeCohort": corroborated_count,
        "exclusionBreakdown": exclusion_breakdown,
    }


def fetch_source_info(cur) -> dict:
    """Freshness signals only -- never used to gate or filter the
    reconciliation itself. Two genuinely different timestamps, kept
    explicitly separate: the external Bud sync service's own last write to
    learner_progress (which this app cannot independently verify is
    complete -- it only reads that table), versus this app's own most
    recent Bud-sync-trial job, which is a wholly different, app-side
    process that itself only reads learner_progress."""
    cur.execute(
        'SELECT max(synced_at) AS "maxSyncedAt", count(*)::int AS "rowCount" '
        "FROM public.learner_progress WHERE learning_plan_id IS NOT NULL"
    )
    source = cur.fetchone()

    cur.execute(
        """
        SELECT id, status, started_at AS "startedAt", completed_at AS "completedAt"
        FROM bud_sync_job ORDER BY started_at DESC LIMIT 1
        """
    )
    latest_job = cur.fetchone()

    return {
        "sourceMaxSyncedAt": source["maxSyncedAt"],
        "sourceRowCount": source["rowCount"],
        "latestAppSyncJob": latest_job,
        "sourceCompletenessNote": (
            "learner_progress is populated by a separate external Bud sync service; this app only reads it. "
            "A recent synced_at timestamp or a successful app-side sync job confirms this app has processed "
            "what was in the table at that time -- it is not proof the external extract itself was complete "
            "or up to date with Bud's live system."
        ),
    }
