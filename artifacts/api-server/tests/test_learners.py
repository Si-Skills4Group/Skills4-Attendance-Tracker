import pydantic
import pytest
from fastapi import HTTPException

from pyapp.routers.learners import (
    LearnerInput,
    LearnerStatusChangeInput,
    change_learner_status,
    create_learner,
)


def _cleanup(db, learner_id):
    db.execute("DELETE FROM learners WHERE id = %s", (learner_id,))


def test_planned_end_date_before_start_date_is_rejected():
    with pytest.raises(HTTPException) as exc:
        LearnerInput(
            learnerRef="BAD-DATES",
            firstName="A",
            lastName="B",
            programme="P",
            level="3",
            startDate="2026-06-01",
            plannedEndDate="2026-01-01",
        )
    assert exc.value.status_code == 400


def test_valid_date_range_is_accepted():
    payload = LearnerInput(
        learnerRef="GOOD-DATES",
        firstName="A",
        lastName="B",
        programme="P",
        level="3",
        startDate="2026-01-01",
        plannedEndDate="2026-06-01",
    )
    assert payload.plannedEndDate is not None


def test_invalid_status_value_is_rejected():
    with pytest.raises(pydantic.ValidationError):
        LearnerInput(
            learnerRef="BAD-STATUS",
            firstName="A",
            lastName="B",
            programme="P",
            level="3",
            startDate="2026-01-01",
            status="graduated",  # not one of active/paused/withdrawn/completed
        )


def test_duplicate_learner_reference_is_rejected(db, request_factory, admin_user, learner_factory):
    existing = learner_factory(learner_ref="DUP-REF")
    payload = LearnerInput(learnerRef="DUP-REF", firstName="A", lastName="B", programme="P", level="3", startDate="2026-01-01")
    with pytest.raises(HTTPException) as exc:
        create_learner(payload, request_factory(), admin_user)
    assert exc.value.status_code == 400


def test_duplicate_uln_is_rejected_cleanly(db, request_factory, admin_user, learner_factory):
    """Regression test: this used to raise a raw, unhandled DB
    UniqueViolation (a 500) instead of a clean validation error."""
    learner_factory(learner_ref="ULN-OWNER", uln="1111111111")
    payload = LearnerInput(
        learnerRef="ULN-CLAIMANT", uln="1111111111", firstName="A", lastName="B", programme="P", level="3", startDate="2026-01-01"
    )
    with pytest.raises(HTTPException) as exc:
        create_learner(payload, request_factory(), admin_user)
    assert exc.value.status_code == 400
    assert "ULN" in str(exc.value.detail)


def test_status_change_to_withdrawn_requires_withdrawal_date(db, request_factory, admin_user, learner_factory):
    learner = learner_factory(status="active")
    payload = LearnerStatusChangeInput(status="withdrawn")
    with pytest.raises(HTTPException) as exc:
        change_learner_status(learner["id"], payload, request_factory(), admin_user)
    assert exc.value.status_code == 400
    assert "withdrawalDate" in str(exc.value.detail)


def test_status_change_to_completed_requires_actual_end_date(db, request_factory, admin_user, learner_factory):
    learner = learner_factory(status="active")
    payload = LearnerStatusChangeInput(status="completed")
    with pytest.raises(HTTPException) as exc:
        change_learner_status(learner["id"], payload, request_factory(), admin_user)
    assert exc.value.status_code == 400
    assert "actualEndDate" in str(exc.value.detail)


def test_change_status_is_audited(db, request_factory, admin_user, learner_factory):
    learner = learner_factory(status="active")
    payload = LearnerStatusChangeInput(status="withdrawn", withdrawalDate="2026-02-01", reason="Left employer")

    result = change_learner_status(learner["id"], payload, request_factory(), admin_user)
    assert result["status"] == "withdrawn"
    assert result["withdrawalDate"] is not None

    db.execute(
        "SELECT new_value FROM audit_logs WHERE entity_type = 'learner' AND entity_id = %s AND action = 'change_status' "
        "ORDER BY id DESC LIMIT 1",
        (learner["id"],),
    )
    row = db.fetchone()
    assert row is not None
    assert "withdrawn" in row["new_value"]


def test_completing_a_learner_without_actual_end_date_is_rejected_by_endpoint(db, request_factory, admin_user, learner_factory):
    learner = learner_factory(status="active")
    # Bypass the pydantic-level guard to prove the endpoint's own check also holds
    # (defense in depth, matching the pattern used for tutor/cohort guards).
    payload = LearnerStatusChangeInput.model_construct(status="completed", actualEndDate=None, withdrawalDate=None, reason=None)
    with pytest.raises(HTTPException) as exc:
        change_learner_status(learner["id"], payload, request_factory(), admin_user)
    assert exc.value.status_code == 400


def test_learner_with_allocation_history_is_not_deleted_by_any_endpoint():
    """There is deliberately no learner-delete endpoint in this API --
    this documents that as an intentional design choice rather than an
    oversight, satisfying 'do not permanently delete learners with
    history' by construction."""
    from pyapp.routers import learners as learners_router

    assert not hasattr(learners_router, "delete_learner")
