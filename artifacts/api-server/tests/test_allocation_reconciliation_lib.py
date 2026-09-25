"""Tests for pyapp/allocation_reconciliation_lib.py and pyapp/routers/
allocation_reconciliation.py -- Stage 1 of the operational allocation audit.
Visibility only: every test here asserts on the classification these
functions PRODUCE, never on anything they write (they write nothing)."""
from fastapi import HTTPException, Request

from pyapp import auth as auth_module
from pyapp.allocation_reconciliation_lib import (
    build_corrected_population_summary,
    build_multiple_plan_link_breakdown,
    build_reconciliation,
    classify_home_cohort,
)
from pyapp.routers.allocation_reconciliation import get_allocation_reconciliation

# Same dependency-override pattern as test_reports.py: FastAPI captured the
# real require_auth callable at route-registration time, so only
# monkeypatching pyapp.auth.require_auth has no effect on Depends(require_auth)
# routes -- both the monkeypatch (for require_admin's plain function-call
# check) and the dependency_overrides entry (for the Depends-wired route)
# are needed together.
_REAL_REQUIRE_AUTH = auth_module.require_auth


def _fake_session_dependency(session, user_id):
    def fake_require_auth(request: Request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    return fake_require_auth


def _as_tutor(client, monkeypatch, tutor_id, user_id=1):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}
    fake_require_auth = _fake_session_dependency(session, user_id)
    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)
    client.app.dependency_overrides[_REAL_REQUIRE_AUTH] = fake_require_auth
    return session


def _bud_row_for(learner_ref: str, **overrides) -> dict:
    overrides.setdefault("learner_reference", learner_ref)
    overrides.setdefault("status_desc", "In Progress")
    return overrides


class TestClassifyHomeCohort:
    def test_no_cohort_id_is_unassigned(self, db):
        classification, reason, info = classify_home_cohort(None, {})
        assert classification == "unassigned"
        assert reason == "no_home_cohort"
        assert info is None

    def test_cohort_id_pointing_nowhere_is_unassigned(self, db):
        classification, reason, info = classify_home_cohort(999999, {})
        assert classification == "unassigned"
        assert reason == "cohort_not_found"

    def test_inactive_cohort_is_unassigned(self):
        lookup = {5: {"id": 5, "name": "X", "active": False, "deletedAt": None, "membershipType": "primary"}}
        classification, reason, info = classify_home_cohort(5, lookup)
        assert classification == "unassigned"
        assert reason == "cohort_inactive"

    def test_deleted_cohort_is_unassigned(self):
        lookup = {5: {"id": 5, "name": "X", "active": True, "deletedAt": "2026-01-01", "membershipType": "primary"}}
        classification, reason, info = classify_home_cohort(5, lookup)
        assert classification == "unassigned"
        assert reason == "cohort_deleted"

    def test_secondary_membership_type_is_unassigned(self):
        lookup = {5: {"id": 5, "name": "X", "active": True, "deletedAt": None, "membershipType": "secondary"}}
        classification, reason, info = classify_home_cohort(5, lookup)
        assert classification == "unassigned"
        assert reason == "cohort_membership_type_secondary"

    def test_invalid_membership_type_is_unassigned_and_flagged(self):
        lookup = {5: {"id": 5, "name": "X", "active": True, "deletedAt": None, "membershipType": "weird_legacy_value"}}
        classification, reason, info = classify_home_cohort(5, lookup)
        assert classification == "unassigned"
        assert reason == "cohort_membership_type_invalid"

    def test_active_primary_cohort_is_assigned(self):
        lookup = {5: {"id": 5, "name": "X", "active": True, "deletedAt": None, "membershipType": "primary"}}
        classification, reason, info = classify_home_cohort(5, lookup)
        assert classification == "assigned"
        assert reason is None


class TestBuildReconciliation:
    def test_confirmed_active_learner_on_active_primary_cohort_is_assigned(
        self, db, cohort_factory, learner_factory, bud_row_factory,
    ):
        cohort = cohort_factory(active=True, membership_type="primary")
        learner = learner_factory(cohort_id=cohort["id"], status="active", learner_ref="REF-ASSIGNED-1")
        bud_row_factory(**_bud_row_for(learner["learner_ref"]))

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["internalLearnerId"] == learner["id"])
        assert row["classification"] == "assigned"
        assert row["reviewReason"] is None

    def test_confirmed_active_learner_with_no_cohort_is_unassigned(
        self, db, learner_factory, bud_row_factory,
    ):
        learner = learner_factory(status="active", cohort_id=None, learner_ref="REF-UNASSIGNED-1")
        bud_row_factory(**_bud_row_for(learner["learner_ref"]))

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["internalLearnerId"] == learner["id"])
        assert row["classification"] == "unassigned"
        assert row["reviewReason"] == "no_home_cohort"

    def test_inactive_home_cohort_is_unassigned_with_reason(
        self, db, cohort_factory, learner_factory, bud_row_factory,
    ):
        cohort = cohort_factory(active=False, membership_type="primary")
        learner = learner_factory(cohort_id=cohort["id"], status="active", learner_ref="REF-INACTIVE-COHORT")
        bud_row_factory(**_bud_row_for(learner["learner_ref"]))

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["internalLearnerId"] == learner["id"])
        assert row["classification"] == "unassigned"
        assert row["reviewReason"] == "cohort_inactive"

    def test_secondary_only_enrollment_does_not_satisfy_home_allocation(
        self, db, cohort_factory, learner_factory, bud_row_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary", subject="math")
        learner = learner_factory(status="active", cohort_id=None, learner_ref="REF-SECONDARY-ONLY")
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])
        bud_row_factory(**_bud_row_for(learner["learner_ref"]))

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["internalLearnerId"] == learner["id"])
        assert row["classification"] == "unassigned"
        assert row["reviewReason"] == "no_home_cohort"

    def test_learner_whose_only_cohort_is_secondary_typed_is_unassigned(
        self, db, cohort_factory, learner_factory, bud_row_factory,
    ):
        """A learner whose learners.cohort_id itself (not a secondary
        enrollment) happens to point at a 'secondary' cohort -- a data-
        quality edge case, still must not satisfy the primary requirement."""
        fs_cohort = cohort_factory(membership_type="secondary", subject="english")
        learner = learner_factory(cohort_id=fs_cohort["id"], status="active", learner_ref="REF-HOME-IS-SECONDARY")
        bud_row_factory(**_bud_row_for(learner["learner_ref"]))

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["internalLearnerId"] == learner["id"])
        assert row["classification"] == "unassigned"
        assert row["reviewReason"] == "cohort_membership_type_secondary"

    def test_internally_active_learner_with_no_reliable_bud_match_needs_review(
        self, db, cohort_factory, learner_factory,
    ):
        cohort = cohort_factory(active=True, membership_type="primary")
        learner = learner_factory(cohort_id=cohort["id"], status="active", learner_ref="REF-NO-BUD-MATCH")
        # Deliberately no bud_row_factory call at all.

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["internalLearnerId"] == learner["id"])
        assert row["classification"] == "needs_review"
        assert row["reviewReason"] == "no_reliable_bud_match"

    def test_bud_in_progress_with_internal_completed_status_needs_review(
        self, db, cohort_factory, learner_factory, bud_row_factory,
    ):
        cohort = cohort_factory(active=True, membership_type="primary")
        learner = learner_factory(
            cohort_id=cohort["id"], status="completed", actual_end_date="2026-01-01", learner_ref="REF-BUD-INPROG-COMPLETED",
        )
        bud_row_factory(**_bud_row_for(learner["learner_ref"]))

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["internalLearnerId"] == learner["id"])
        assert row["classification"] == "needs_review"
        assert row["reviewReason"] == "bud_in_progress_internal_inactive"
        assert row["internalStatus"] == "completed"
        assert row["budStatus"] == "In Progress"

    def test_internally_active_learner_whose_bud_status_is_not_in_progress_needs_review(
        self, db, cohort_factory, learner_factory, bud_row_factory,
    ):
        cohort = cohort_factory(active=True, membership_type="primary")
        learner = learner_factory(cohort_id=cohort["id"], status="active", learner_ref="REF-BUD-NOT-INPROG")
        bud_row_factory(**_bud_row_for(learner["learner_ref"], status_desc="On Break"))

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["internalLearnerId"] == learner["id"])
        assert row["classification"] == "needs_review"
        assert row["reviewReason"] == "bud_status_not_in_progress_but_internally_active"

    def test_multiple_plans_for_the_same_person_never_silently_pick_one(
        self, db, cohort_factory, learner_factory, bud_row_factory,
    ):
        """Two Bud rows share one learner_reference -- classify_row's own
        ambiguity rule (learner_reference_matches_multiple_bud_rows) must
        route this to Needs Review, and the person must never be counted
        twice in the confirmed population."""
        cohort = cohort_factory(active=True, membership_type="primary")
        learner = learner_factory(cohort_id=cohort["id"], status="active", learner_ref="REF-MULTI-PLAN")
        bud_row_factory(**_bud_row_for(learner["learner_ref"], status_desc="In Progress"))
        bud_row_factory(**_bud_row_for(learner["learner_ref"], status_desc="Completed"))

        result = build_reconciliation(db)
        matching = [r for r in result["rows"] if r["internalLearnerId"] == learner["id"]]
        assert all(r["classification"] == "needs_review" for r in matching)
        assert all(r["reviewReason"] == "ambiguous_multiple_plans" for r in matching)
        confirmed_count = sum(
            1 for r in result["rows"]
            if r["internalLearnerId"] == learner["id"] and r["classification"] in ("assigned", "unassigned")
        )
        assert confirmed_count == 0

    def test_unresolved_bud_record_with_no_internal_learner_is_visible_in_needs_review(
        self, db, tutor_factory, bud_row_factory,
    ):
        tutor = tutor_factory()
        bud_row_factory(
            learner_reference="REF-NEVER-CREATED", status_desc="In Progress",
            tutor_id=str(_external_system_id_for(db, tutor["tutorId"])),
        )

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["learnerRef"] == "REF-NEVER-CREATED")
        assert row["internalLearnerId"] is None
        assert row["classification"] == "needs_review"
        assert row["reviewReason"] == "bud_unresolved_no_internal_learner"

    def test_tutor_unmatched_conflict_is_visible_with_source_only_fields(self, db, bud_row_factory):
        bud_row_factory(learner_reference="REF-TUTOR-UNMATCHED", status_desc="In Progress", tutor_id="NOT-A-REAL-BUD-TUTOR-GUID")

        result = build_reconciliation(db)
        row = next(r for r in result["rows"] if r["learnerRef"] == "REF-TUTOR-UNMATCHED")
        assert row["internalLearnerId"] is None
        assert row["classification"] == "needs_review"
        assert row["reviewReason"] == "bud_unresolved_tutor_unmatched"
        assert row["learnerName"] == "Bud Learner"  # from bud_row_factory's default forename/surname

    def test_missing_source_row_never_implies_a_status_change(
        self, db, cohort_factory, learner_factory,
    ):
        """No bud_row seeded at all for this learner -- they must appear
        (if active) as Needs Review, but their OWN status must not have
        been touched (this function writes nothing at all)."""
        cohort = cohort_factory(active=True, membership_type="primary")
        learner = learner_factory(cohort_id=cohort["id"], status="active", learner_ref="REF-MISSING-SOURCE")

        build_reconciliation(db)

        db.execute("SELECT status FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["status"] == "active"

    def test_non_actionable_unmatched_historical_row_is_not_surfaced_at_all(
        self, db, bud_row_factory,
    ):
        """A Bud row with no internal match and a non-'In Progress' status
        is Bud's ordinary historical churn (thousands of these exist in
        production) -- must not flood this report."""
        bud_row_factory(learner_reference="REF-ORDINARY-HISTORICAL-NOISE", status_desc="Withdrawn")

        result = build_reconciliation(db)
        assert not any(r["learnerRef"] == "REF-ORDINARY-HISTORICAL-NOISE" for r in result["rows"])

    def test_matched_learner_where_both_sides_agree_not_current_is_not_surfaced(
        self, db, cohort_factory, learner_factory, bud_row_factory,
    ):
        """Regression test: a cleanly-matched learner who is internally
        withdrawn AND whose Bud status is also not 'In Progress' (e.g.
        Withdrawn/Completed/Pending) -- both systems agree they are no
        longer current, so this is routine agreement, not a discrepancy to
        review. Found via a production run: this exact combination fell
        through every classification branch and was previously surfaced as
        Needs Review with an empty reviewReason (a real bug, not a display
        gap -- the row had classification='needs_review' but no reason and
        no issue flags at all)."""
        cohort = cohort_factory(active=True, membership_type="primary")
        learner = learner_factory(
            cohort_id=cohort["id"], status="withdrawn", withdrawal_date="2026-01-01", learner_ref="REF-BOTH-AGREE-NOT-CURRENT",
        )
        bud_row_factory(**_bud_row_for(learner["learner_ref"], status_desc="Withdrawn"))

        result = build_reconciliation(db)
        assert not any(r["learnerRef"] == "REF-BOTH-AGREE-NOT-CURRENT" for r in result["rows"])


def _external_system_id_for(db, tutor_id: int) -> str:
    guid = f"TEST-BUD-TUTOR-{tutor_id}"
    db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", (guid, tutor_id))
    return guid


class TestGetAllocationReconciliationEndpoint:
    def test_admin_can_call_it_directly(self, db, admin_user, cohort_factory, learner_factory, bud_row_factory):
        cohort = cohort_factory(active=True, membership_type="primary")
        learner = learner_factory(cohort_id=cohort["id"], status="active", learner_ref="REF-ENDPOINT-1")
        bud_row_factory(**_bud_row_for(learner["learner_ref"]))

        result = get_allocation_reconciliation(view="assigned", _session=admin_user)
        assert any(r["internalLearnerId"] == learner["id"] for r in result["items"])
        assert result["total"] == len(result["items"]) or result["total"] >= len(result["items"])

    def test_non_admin_is_rejected_over_http(self, client, monkeypatch, tutor_factory):
        tutor = tutor_factory()
        _as_tutor(client, monkeypatch, tutor["tutorId"])
        response = client.get("/api/allocation-reconciliation")
        assert response.status_code == 403

    def test_counts_match_the_same_filters_as_the_list(
        self, db, admin_user, cohort_factory, learner_factory, bud_row_factory,
    ):
        cohort_a = cohort_factory(active=True, membership_type="primary")
        cohort_b = cohort_factory(active=False, membership_type="primary")
        assigned_learner = learner_factory(cohort_id=cohort_a["id"], status="active", learner_ref="REF-COUNT-A")
        unassigned_learner = learner_factory(cohort_id=cohort_b["id"], status="active", learner_ref="REF-COUNT-B")
        bud_row_factory(**_bud_row_for(assigned_learner["learner_ref"]))
        bud_row_factory(**_bud_row_for(unassigned_learner["learner_ref"]))

        assigned_result = get_allocation_reconciliation(view="assigned", _session=admin_user)
        unassigned_result = get_allocation_reconciliation(view="unassigned", _session=admin_user)

        assert assigned_result["counts"]["confirmedAssigned"] == unassigned_result["counts"]["confirmedAssigned"]
        assert assigned_result["counts"]["confirmedUnassigned"] == unassigned_result["counts"]["confirmedUnassigned"]
        assert any(r["internalLearnerId"] == assigned_learner["id"] for r in assigned_result["items"])
        assert any(r["internalLearnerId"] == unassigned_learner["id"] for r in unassigned_result["items"])

    def test_pagination_total_matches_full_filtered_result_not_just_current_page(
        self, db, admin_user, cohort_factory, learner_factory, bud_row_factory,
    ):
        cohort = cohort_factory(active=True, membership_type="primary")
        learners = [
            learner_factory(cohort_id=cohort["id"], status="active", learner_ref=f"REF-PAGE-{i}")
            for i in range(5)
        ]
        for learner in learners:
            bud_row_factory(**_bud_row_for(learner["learner_ref"]))

        page1 = get_allocation_reconciliation(view="assigned", page=1, pageSize=2, _session=admin_user)
        assert len(page1["items"]) == 2
        assert page1["total"] >= 5
        assert page1["total"] == page1["counts"]["confirmedAssigned"]

    def test_search_filters_by_learner_name_or_reference(
        self, db, admin_user, cohort_factory, learner_factory, bud_row_factory,
    ):
        cohort = cohort_factory(active=True, membership_type="primary")
        findable = learner_factory(
            cohort_id=cohort["id"], status="active", learner_ref="REF-FINDME-UNIQUE", first_name="Zebedee",
        )
        other = learner_factory(cohort_id=cohort["id"], status="active", learner_ref="REF-OTHER-UNIQUE")
        bud_row_factory(**_bud_row_for(findable["learner_ref"]))
        bud_row_factory(**_bud_row_for(other["learner_ref"]))

        result = get_allocation_reconciliation(view="assigned", search="Zebedee", _session=admin_user)
        assert all(r["internalLearnerId"] == findable["id"] for r in result["items"])
        assert len(result["items"]) >= 1


class TestCorrectedPopulationSummary:
    """Stage 2, item 1: replaces the Stage 1 corrective pass's mislabelled
    '220'/'429'/'191' comparison figures with populations computed fresh,
    from one snapshot, that describe exactly what they are."""

    def test_broader_population_counts_distinct_learners_not_rows(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        """Regression guard for a real bug found while building this very
        function: a learner_already_linked_elsewhere conflict (triggered by
        an UNRELATED Bud row's ULN collision) can add a SECOND row for a
        learner who already has their own, differently-classified row --
        exactly the same "one person, multiple rows" shape
        build_reconciliation's own docstring already documents for
        ambiguous learners. The very first version of this function counted
        raw rows instead of distinct learners here and overcounted by
        exactly this mechanism -- caught by cross-checking against a direct
        SQL count of the same population."""
        from pyapp.bud_sync_lib import run_commit, run_preview, update_item

        learner = learner_factory(learner_ref="REF-ULN-COLLISION", uln="ULN-COLLISION-1", status="active", cohort_id=None)
        baseline_factory()
        bud_row_factory(learner_reference="REF-ULN-COLLISION", status_desc="In Progress", synced_at="2099-01-01T00:00:00Z")
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)

        # A second, UNRELATED Bud row shares this learner's ULN under a
        # different learner_reference -- classify_row resolves it (via the
        # uln fallback) to the same internal learner, then finds they're
        # already linked to a DIFFERENT plan_id and reports
        # learner_already_linked_to_a_different_bud_record. Neither
        # reference individually repeats across rows, so
        # _get_ambiguous_learner_references does NOT flag this -- it is a
        # genuinely different conflict shape from "ambiguous_multiple_plans".
        bud_row_factory(learner_reference="REF-ULN-COLLISION-OTHER-PERSON", unique_learner_number="ULN-COLLISION-1", status_desc="In Progress")

        reconciliation = build_reconciliation(db)
        matching_rows = [r for r in reconciliation["rows"] if r["internalLearnerId"] == learner["id"]]
        assert len(matching_rows) == 2, "expected exactly this learner's own row plus one unrelated conflict row"
        assert {r["classification"] for r in matching_rows} == {"unassigned", "needs_review"}

        db.execute(
            "SELECT count(*) AS n FROM learners WHERE id = %s AND status = 'active' AND "
            "(cohort_id IS NULL OR NOT EXISTS (SELECT 1 FROM cohorts c WHERE c.id = learners.cohort_id "
            "AND c.active = true AND c.deleted_at IS NULL AND c.membership_type = 'primary'))",
            (learner["id"],),
        )
        assert db.fetchone()["n"] == 1

        summary = build_corrected_population_summary(db)
        # The learner must be counted exactly ONCE in the broader
        # population, and their winning bucket must be "confirmed_unassigned"
        # (their own authoritative row), not double-counted across two
        # different exclusion reasons.
        confirmed_unassigned_entry = next(e for e in summary["exclusionBreakdown"] if e["reason"] == "confirmed_unassigned")
        assert confirmed_unassigned_entry["count"] >= 1
        total_exclusion = sum(e["count"] for e in summary["exclusionBreakdown"])
        assert total_exclusion == summary["broaderActiveWithoutActiveHomeCohort"]

    def test_confirmed_unassigned_always_equals_its_own_exclusion_bucket(
        self, db, learner_factory, bud_row_factory,
    ):
        learner = learner_factory(status="active", cohort_id=None, learner_ref="REF-CORRECTED-SUMMARY-UNASSIGNED")
        bud_row_factory(**_bud_row_for(learner["learner_ref"]))

        summary = build_corrected_population_summary(db)
        confirmed_unassigned_entry = next(
            (e for e in summary["exclusionBreakdown"] if e["reason"] == "confirmed_unassigned"), None,
        )
        assert confirmed_unassigned_entry is not None
        assert confirmed_unassigned_entry["count"] == summary["confirmedUnassigned"]

    def test_bud_corroborated_population_never_exceeds_the_broader_population(self, db):
        summary = build_corrected_population_summary(db)
        assert summary["budCorroboratedActiveWithoutActiveHomeCohort"] <= summary["broaderActiveWithoutActiveHomeCohort"]

    def test_active_learner_with_no_bud_presence_at_all_is_not_bud_corroborated(
        self, db, learner_factory,
    ):
        """An active, no-cohort learner Bud has never once referenced (no
        matching learner_reference or uln anywhere in the source) must be
        excluded from the corroborated count -- it belongs only in the
        broader, ungated population."""
        learner_factory(status="active", cohort_id=None, learner_ref="REF-NEVER-IN-BUD-AT-ALL", uln=None)

        summary = build_corrected_population_summary(db)
        row_reasons = {e["reason"] for e in summary["exclusionBreakdown"]}
        # This learner has no Bud row at all -- distinct from every other
        # reason in the breakdown, all of which imply some Bud presence.
        assert summary["budCorroboratedActiveWithoutActiveHomeCohort"] < summary["broaderActiveWithoutActiveHomeCohort"]


class TestMultiplePlanLinkBreakdown:
    """Stage 2, item 7: read-only visibility only -- these tests assert
    categorisation, never that anything gets relinked."""

    def test_no_ambiguous_references_returns_all_zero(self, db):
        result = build_multiple_plan_link_breakdown(db)
        assert result == {
            "linkPointsToCurrentPlan": 0, "linkPointsToHistoricalPlan": 0,
            "noExistingLink": 0, "conflictingOrUnresolved": 0, "details": [],
        }

    def test_current_plan_plus_historical_with_no_existing_link(
        self, db, learner_factory, bud_row_factory,
    ):
        learner = learner_factory(learner_ref="REF-MULTIPLAN-NOLINK", status="active")
        bud_row_factory(learner_reference="REF-MULTIPLAN-NOLINK", status_desc="In Progress")
        bud_row_factory(learner_reference="REF-MULTIPLAN-NOLINK", status_desc="Completed")

        result = build_multiple_plan_link_breakdown(db)
        entry = next(d for d in result["details"] if d["learnerReference"] == "REF-MULTIPLAN-NOLINK")
        assert entry["category"] == "no_existing_link"
        assert entry["internalLearnerId"] == learner["id"]
        assert result["noExistingLink"] >= 1

    def test_existing_link_pointing_at_the_current_plan(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        from pyapp.bud_sync_lib import run_commit, run_preview, update_item

        learner = learner_factory(learner_ref="REF-MULTIPLAN-LINKED-CURRENT", status="active")
        baseline_factory()
        current_plan = bud_row_factory(
            learner_reference="REF-MULTIPLAN-LINKED-CURRENT", status_desc="In Progress", synced_at="2099-01-01T00:00:00Z",
        )
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)

        # A second, historical plan under the same reference makes this
        # person ambiguous (>1 plan sharing the reference).
        bud_row_factory(learner_reference="REF-MULTIPLAN-LINKED-CURRENT", status_desc="Withdrawn")

        result = build_multiple_plan_link_breakdown(db)
        entry = next(d for d in result["details"] if d["learnerReference"] == "REF-MULTIPLAN-LINKED-CURRENT")
        assert entry["category"] == "link_points_to_current"
        assert entry["linkedPlanId"] == current_plan["learningPlanId"]
        assert result["linkPointsToCurrentPlan"] >= 1

    def test_never_relinks_or_mutates_anything(self, db, learner_factory, bud_row_factory):
        learner_factory(learner_ref="REF-MULTIPLAN-READONLY", status="active")
        bud_row_factory(learner_reference="REF-MULTIPLAN-READONLY", status_desc="In Progress")
        bud_row_factory(learner_reference="REF-MULTIPLAN-READONLY", status_desc="Completed")

        build_multiple_plan_link_breakdown(db)

        db.execute("SELECT count(*) AS n FROM bud_learner_link")
        assert db.fetchone()["n"] == 0
