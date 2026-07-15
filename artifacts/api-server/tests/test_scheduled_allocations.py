import datetime

import pytest
from fastapi import HTTPException

from pyapp.routers.allocation_routes import (
    AllocationInput,
    allocate_learners,
    cancel_scheduled_allocation,
    list_scheduled_allocations,
)
from pyapp.scheduled_allocations_lib import apply_due_scheduled_allocations


def _future_date(days: int = 30) -> datetime.date:
    return datetime.date.today() + datetime.timedelta(days=days)


class TestFutureDatedTransferIsDeferred:
    def test_future_effective_date_schedules_instead_of_applying(
        self, db, request_factory, admin_user, tutor_factory, cohort_factory, learner_factory,
    ):
        old_tutor = tutor_factory()
        new_tutor = tutor_factory()
        old_cohort = cohort_factory(tutor_id=old_tutor["tutorId"])
        new_cohort = cohort_factory(tutor_id=new_tutor["tutorId"])
        learner = learner_factory(tutor_id=old_tutor["tutorId"], cohort_id=old_cohort["id"])

        result = allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], tutorId=new_tutor["tutorId"], cohortId=new_cohort["id"], effectiveDate=_future_date()),
            request_factory(), admin_user,
        )
        assert result == {"updated": 0, "scheduled": 1}

        db.execute("SELECT tutor_id, cohort_id FROM learners WHERE id = %s", (learner["id"],))
        row = db.fetchone()
        assert row["tutor_id"] == old_tutor["tutorId"]
        assert row["cohort_id"] == old_cohort["id"]

    def test_today_effective_date_applies_immediately(
        self, db, request_factory, admin_user, cohort_factory, learner_factory,
    ):
        new_cohort = cohort_factory()
        learner = learner_factory()

        result = allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], cohortId=new_cohort["id"], effectiveDate=datetime.date.today()),
            request_factory(), admin_user,
        )
        assert result == {"updated": 1, "scheduled": 0}

        db.execute("SELECT cohort_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["cohort_id"] == new_cohort["id"]


class TestSchedulingConflicts:
    def test_second_pending_transfer_for_same_learner_is_rejected(
        self, request_factory, admin_user, cohort_factory, learner_factory,
    ):
        cohort_a = cohort_factory()
        cohort_b = cohort_factory()
        learner = learner_factory()

        allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], cohortId=cohort_a["id"], effectiveDate=_future_date()),
            request_factory(), admin_user,
        )

        with pytest.raises(HTTPException) as exc:
            allocate_learners(
                AllocationInput(learnerIds=[learner["id"]], cohortId=cohort_b["id"], effectiveDate=_future_date(60)),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["error"] == "learners_already_have_pending_transfer"


class TestApplyDueScheduledAllocations:
    def test_apply_due_moves_learner_and_marks_applied(
        self, db, request_factory, admin_user, cohort_factory, learner_factory,
    ):
        destination = cohort_factory()
        learner = learner_factory()
        due_date = datetime.date.today() - datetime.timedelta(days=1)

        allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], cohortId=destination["id"], effectiveDate=due_date),
            request_factory(), admin_user,
        )
        # effectiveDate is in the past relative to "today" at schedule time,
        # but allocate_learners treats "<= today" as immediate -- so insert
        # the pending row directly to exercise the lazy-apply path in isolation.
        db.execute("UPDATE learners SET cohort_id = NULL WHERE id = %s", (learner["id"],))
        db.execute(
            "INSERT INTO scheduled_allocations (learner_id, new_cohort_id, effective_date, created_by) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (learner["id"], destination["id"], due_date, admin_user["userId"]),
        )
        scheduled_id = db.fetchone()["id"]

        applied_ids = apply_due_scheduled_allocations(db, as_of=datetime.date.today())
        assert scheduled_id in applied_ids

        db.execute("SELECT cohort_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["cohort_id"] == destination["id"]
        db.execute("SELECT status FROM scheduled_allocations WHERE id = %s", (scheduled_id,))
        assert db.fetchone()["status"] == "applied"

    def test_pending_transfer_not_yet_due_is_left_untouched(
        self, db, admin_user, cohort_factory, learner_factory,
    ):
        destination = cohort_factory()
        learner = learner_factory()
        db.execute(
            "INSERT INTO scheduled_allocations (learner_id, new_cohort_id, effective_date, created_by) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (learner["id"], destination["id"], _future_date(), admin_user["userId"]),
        )
        scheduled_id = db.fetchone()["id"]

        applied_ids = apply_due_scheduled_allocations(db, as_of=datetime.date.today())
        assert scheduled_id not in applied_ids

        db.execute("SELECT cohort_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["cohort_id"] != destination["id"]


class TestListAndCancelScheduledTransfers:
    def test_pending_transfer_appears_in_scheduled_list(
        self, request_factory, admin_user, cohort_factory, learner_factory,
    ):
        destination = cohort_factory()
        learner = learner_factory()
        allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], cohortId=destination["id"], effectiveDate=_future_date()),
            request_factory(), admin_user,
        )

        pending = list_scheduled_allocations(learnerId=learner["id"], _session=admin_user)
        assert len(pending) == 1
        assert pending[0]["newCohortId"] == destination["id"]

    def test_cancelling_a_pending_transfer_prevents_it_being_applied(
        self, db, request_factory, admin_user, cohort_factory, learner_factory,
    ):
        destination = cohort_factory()
        learner = learner_factory()
        allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], cohortId=destination["id"], effectiveDate=_future_date()),
            request_factory(), admin_user,
        )
        scheduled_id = list_scheduled_allocations(learnerId=learner["id"], _session=admin_user)[0]["id"]

        result = cancel_scheduled_allocation(scheduled_id, request_factory(), admin_user)
        assert result == {"cancelled": True}

        applied_ids = apply_due_scheduled_allocations(db, as_of=_future_date(90))
        assert scheduled_id not in applied_ids
        db.execute("SELECT cohort_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["cohort_id"] != destination["id"]

    def test_cancelling_an_already_applied_transfer_is_rejected(
        self, db, request_factory, admin_user, cohort_factory, learner_factory,
    ):
        destination = cohort_factory()
        learner = learner_factory()
        db.execute(
            "INSERT INTO scheduled_allocations (learner_id, new_cohort_id, effective_date, created_by, status, applied_at) "
            "VALUES (%s, %s, %s, %s, 'applied', now()) RETURNING id",
            (learner["id"], destination["id"], datetime.date.today() - datetime.timedelta(days=1), admin_user["userId"]),
        )
        scheduled_id = db.fetchone()["id"]

        with pytest.raises(HTTPException) as exc:
            cancel_scheduled_allocation(scheduled_id, request_factory(), admin_user)
        assert exc.value.status_code == 400

    def test_cancelling_a_nonexistent_scheduled_transfer_is_404(self, request_factory, admin_user):
        with pytest.raises(HTTPException) as exc:
            cancel_scheduled_allocation(999_999_999, request_factory(), admin_user)
        assert exc.value.status_code == 404
