"""HTTP-level validation for /api/allocation-reconciliation, covering the
Stage 1 corrective-pass checklist that the lib-level tests
(test_allocation_reconciliation_lib.py) don't exercise: the actual response
shape (explicit counting units), non-admin access control, and specific
missing/ambiguous-record scenarios end to end through the real route.

Auth override follows the same monkeypatch(auth_module.require_auth) pattern
as test_learner_imports_api.py / test_permissions.py -- this route is wired
with Depends(require_admin), which calls require_auth internally.
"""
import pytest

from pyapp import auth as auth_module


def _as_tutor(monkeypatch, tutor_id=1, user_id=1):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}

    def fake_require_auth(request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)


def _as_admin(monkeypatch, user_id=1):
    session = {"userId": user_id, "role": "admin", "tutorId": None}

    def fake_require_auth(request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)


def test_tutor_cannot_access_allocation_reconciliation(client, monkeypatch):
    _as_tutor(monkeypatch)
    response = client.get("/api/allocation-reconciliation")
    assert response.status_code == 403


def test_admin_response_has_explicit_counting_units(client, monkeypatch):
    """Guards the corrective-pass item-2 shape directly: Assigned/Unassigned
    are plain integers (distinct learners), Needs Review is a three-field
    object -- never a single collapsed integer that would misrepresent a
    population where one person can appear on more than one row."""
    _as_admin(monkeypatch)
    response = client.get("/api/allocation-reconciliation")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["counts"]["confirmedAssigned"], int)
    assert isinstance(body["counts"]["confirmedUnassigned"], int)
    needs_review = body["counts"]["needsReview"]
    assert set(needs_review.keys()) == {"recordCount", "distinctPeopleCount", "budLearningPlanRowCount"}
    assert needs_review["distinctPeopleCount"] <= needs_review["recordCount"]
    assert "ambiguousPlanBreakdown" in body
    for key in (
        "distinctLearnerReferences", "affectedBudLearningPlans", "affectedInternalLearners",
        "withMultipleInProgressPlans", "withOneInProgressPlusHistorical", "withOnlyHistoricalPlans",
    ):
        assert key in body["ambiguousPlanBreakdown"]


def test_missing_bud_status_is_distinguishable_from_a_known_non_in_progress_status(
    client, monkeypatch, learner_factory, bud_row_factory,
):
    """Two different Needs Review rows -- one where Bud never reported a
    status at all (learner_progress.status_desc is NULL) and one where Bud
    explicitly reported "Completed" -- must retain their own distinct
    budStatus value in the response rather than being collapsed into a
    single "not in progress" flag that hides which case actually applies."""
    _as_admin(monkeypatch)
    blank_learner = learner_factory(learner_ref="API-BLANK-STATUS", status="active")
    known_learner = learner_factory(learner_ref="API-KNOWN-STATUS", status="active")
    bud_row_factory(learner_reference="API-BLANK-STATUS", status_desc=None)
    bud_row_factory(learner_reference="API-KNOWN-STATUS", status_desc="Completed")

    response = client.get("/api/allocation-reconciliation", params={"view": "needs_review", "pageSize": 200})
    assert response.status_code == 200
    rows_by_learner_id = {r["internalLearnerId"]: r for r in response.json()["items"]}

    blank_row = rows_by_learner_id[blank_learner["id"]]
    known_row = rows_by_learner_id[known_learner["id"]]
    assert blank_row["budStatus"] is None
    assert known_row["budStatus"] == "Completed"
    assert blank_row["reviewReason"] == "bud_status_not_in_progress_but_internally_active"
    assert known_row["reviewReason"] == "bud_status_not_in_progress_but_internally_active"


def test_unknown_cohort_membership_type_carries_a_review_flag(client, monkeypatch, db, learner_factory, cohort_factory, bud_row_factory):
    """An invalid/unrecognised cohorts.membership_type value must never be
    silently treated as a valid home cohort (i.e. never counted as
    "assigned") -- classify_home_cohort routes it to "unassigned" (the same
    bucket as any other disqualified home cohort -- deleted, inactive,
    secondary), carrying a reviewReason so it's clearly distinguishable from
    an ordinary "no home cohort at all" case rather than silently passing."""
    _as_admin(monkeypatch)
    cohort = cohort_factory(membership_type="primary")
    db.execute("UPDATE cohorts SET membership_type = 'not-a-real-type' WHERE id = %s", (cohort["id"],))
    learner = learner_factory(learner_ref="API-BAD-MEMBERSHIP-TYPE", status="active", cohort_id=cohort["id"])
    bud_row_factory(learner_reference="API-BAD-MEMBERSHIP-TYPE", status_desc="In Progress")

    response = client.get("/api/allocation-reconciliation", params={"view": "unassigned", "pageSize": 200})
    assert response.status_code == 200
    rows_by_learner_id = {r["internalLearnerId"]: r for r in response.json()["items"]}
    row = rows_by_learner_id[learner["id"]]
    assert row["reviewReason"] == "cohort_membership_type_invalid"
    assert row["classification"] == "unassigned"


def test_no_needs_review_row_has_an_empty_reason_across_a_mixed_population(
    client, monkeypatch, learner_factory, cohort_factory, bud_row_factory,
):
    """Broad smoke check over several distinct scenarios at once (blank
    status, unmatched Bud record, internally-active-but-no-bud-match,
    inactive home cohort) -- every single Needs Review row must carry a
    non-empty reviewReason. build_reconciliation itself now asserts this
    internally (raising rather than silently emitting a blank reason), so
    this test is the end-to-end confirmation that assertion never fires
    for realistic data shapes."""
    _as_admin(monkeypatch)
    inactive_cohort = cohort_factory(active=False)
    learner_factory(learner_ref="API-NOMATCH-ACTIVE", status="active")
    learner_factory(learner_ref="API-INACTIVE-COHORT", status="active", cohort_id=inactive_cohort["id"])
    bud_row_factory(learner_reference="API-INACTIVE-COHORT", status_desc="In Progress")
    bud_row_factory(learner_reference="API-UNMATCHED-BUD-ONLY", status_desc="In Progress")

    response = client.get("/api/allocation-reconciliation", params={"view": "needs_review", "pageSize": 200})
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) > 0
    for row in items:
        assert row["reviewReason"], f"empty reviewReason on row: {row}"


def test_headline_counts_use_the_same_population_as_the_filtered_list(client, monkeypatch, learner_factory, bud_row_factory):
    """The counts returned alongside a filtered view must describe exactly
    that filter's own result set, not the unfiltered totals -- otherwise a
    tutor filter could show a headline count that its own paginated list
    can never actually reach."""
    _as_admin(monkeypatch)
    learner_a = learner_factory(learner_ref="API-COUNT-CONSISTENCY-A", status="active")
    bud_row_factory(learner_reference="API-COUNT-CONSISTENCY-A", status_desc="Completed")

    response = client.get(
        "/api/allocation-reconciliation",
        params={"view": "needs_review", "search": learner_a["first_name"], "pageSize": 200},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(body["items"])
    # view="needs_review" plus the same search filter on both the list and
    # the counts means these must be exactly equal, not merely consistent in
    # direction -- any divergence would mean counts and list disagree about
    # what the current filter set actually matches.
    assert body["counts"]["needsReview"]["recordCount"] == body["total"]
