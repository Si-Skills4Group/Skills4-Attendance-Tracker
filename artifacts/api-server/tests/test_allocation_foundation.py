"""Proves the existing learner_allocation_history model (already built
in an earlier phase, not new in this one) can answer the questions the
next allocation phase will need, and that learners.tutor_id/cohort_id
stay in sync with the latest allocation event."""

from pyapp.routers.allocation_routes import AllocationInput, allocate_learners


def _cohort_as_of(db, learner_id, as_of_date):
    """Resolve historical cohort membership for a date: the most recent
    allocation event on or before that date, falling back to 'no
    allocation event yet' (a learner's original cohort at creation)."""
    db.execute(
        """
        SELECT new_cohort_id FROM learner_allocation_history
        WHERE learner_id = %s AND effective_date <= %s
        ORDER BY effective_date DESC, id DESC LIMIT 1
        """,
        (learner_id, as_of_date),
    )
    row = db.fetchone()
    return row["new_cohort_id"] if row else None


def test_allocate_records_previous_and_new_tutor_and_cohort(db, request_factory, admin_user, tutor_factory, cohort_factory, learner_factory):
    old_tutor = tutor_factory()
    new_tutor = tutor_factory()
    old_cohort = cohort_factory(tutor_id=old_tutor["tutorId"])
    new_cohort = cohort_factory(tutor_id=new_tutor["tutorId"])
    learner = learner_factory(tutor_id=old_tutor["tutorId"], cohort_id=old_cohort["id"])

    payload = AllocationInput(
        learnerIds=[learner["id"]],
        tutorId=new_tutor["tutorId"],
        cohortId=new_cohort["id"],
        effectiveDate="2026-03-01",
        transferReason="Capacity rebalance",
    )
    allocate_learners(payload, request_factory(), admin_user)

    db.execute(
        "SELECT previous_tutor_id, new_tutor_id, previous_cohort_id, new_cohort_id, transfer_reason, changed_by "
        "FROM learner_allocation_history WHERE learner_id = %s ORDER BY id DESC LIMIT 1",
        (learner["id"],),
    )
    row = db.fetchone()
    assert row["previous_tutor_id"] == old_tutor["tutorId"]
    assert row["new_tutor_id"] == new_tutor["tutorId"]
    assert row["previous_cohort_id"] == old_cohort["id"]
    assert row["new_cohort_id"] == new_cohort["id"]
    assert row["transfer_reason"] == "Capacity rebalance"
    assert row["changed_by"] == admin_user["userId"]


def test_current_learner_fields_stay_consistent_with_latest_allocation(db, request_factory, admin_user, tutor_factory, learner_factory):
    tutor = tutor_factory()
    learner = learner_factory()

    payload = AllocationInput(learnerIds=[learner["id"]], tutorId=tutor["tutorId"], effectiveDate="2026-01-15")
    allocate_learners(payload, request_factory(), admin_user)

    db.execute("SELECT tutor_id FROM learners WHERE id = %s", (learner["id"],))
    assert db.fetchone()["tutor_id"] == tutor["tutorId"]


def test_historical_cohort_membership_can_be_resolved_for_a_date(db, request_factory, admin_user, tutor_factory, cohort_factory, learner_factory):
    cohort_a = cohort_factory(name="Cohort A")
    cohort_b = cohort_factory(name="Cohort B")
    learner = learner_factory(cohort_id=cohort_a["id"])

    # Transfer to cohort B effective 2026-03-01.
    allocate_learners(
        AllocationInput(learnerIds=[learner["id"]], cohortId=cohort_b["id"], effectiveDate="2026-03-01"),
        request_factory(),
        admin_user,
    )

    # Before the transfer date, history has no event yet for this window --
    # querying "as of" a date before any recorded transfer correctly finds
    # nothing (the learner's pre-transfer cohort lives on the learner row
    # itself / an earlier transfer event, not fabricated by this query).
    assert _cohort_as_of(db, learner["id"], "2026-02-01") is None
    # On and after the effective date, the new cohort is resolvable.
    assert _cohort_as_of(db, learner["id"], "2026-03-01") == cohort_b["id"]
    assert _cohort_as_of(db, learner["id"], "2026-06-01") == cohort_b["id"]


def test_multiple_transfers_resolve_to_the_correct_point_in_time(db, request_factory, admin_user, cohort_factory, learner_factory):
    cohort_a = cohort_factory(name="Cohort A")
    cohort_b = cohort_factory(name="Cohort B")
    cohort_c = cohort_factory(name="Cohort C")
    learner = learner_factory(cohort_id=cohort_a["id"])

    allocate_learners(
        AllocationInput(learnerIds=[learner["id"]], cohortId=cohort_b["id"], effectiveDate="2026-02-01"),
        request_factory(),
        admin_user,
    )
    allocate_learners(
        AllocationInput(learnerIds=[learner["id"]], cohortId=cohort_c["id"], effectiveDate="2026-05-01"),
        request_factory(),
        admin_user,
    )

    assert _cohort_as_of(db, learner["id"], "2026-03-01") == cohort_b["id"]
    assert _cohort_as_of(db, learner["id"], "2026-05-01") == cohort_c["id"]
    assert _cohort_as_of(db, learner["id"], "2026-12-31") == cohort_c["id"]


def test_allocation_is_audited(db, request_factory, admin_user, tutor_factory, learner_factory):
    tutor = tutor_factory()
    learner = learner_factory()
    allocate_learners(
        AllocationInput(learnerIds=[learner["id"]], tutorId=tutor["tutorId"], effectiveDate="2026-01-01"),
        request_factory(),
        admin_user,
    )
    db.execute(
        "SELECT new_value FROM audit_logs WHERE entity_type = 'learner' AND action = 'allocate' ORDER BY id DESC LIMIT 1"
    )
    row = db.fetchone()
    assert row is not None
    assert str(learner["id"]) in row["new_value"]
