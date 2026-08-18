import pydantic
import pytest
from fastapi import HTTPException

from pyapp.routers.learners import (
    LearnerInput,
    LearnerStatusChangeInput,
    LearnerUpdate,
    change_learner_status,
    create_learner,
    update_learner,
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


def test_patch_learner_ignores_a_tutorid_or_cohortid_in_the_request_body(db, request_factory, admin_user, learner_factory, tutor_factory):
    """LearnerUpdate has no tutorId/cohortId field at all -- apply_transfer
    (via POST /allocation/allocate) is the only path allowed to move a
    learner between tutors/cohorts, since it's the only one that also
    writes learner_allocation_history and checks the new tutor is active.
    A raw request body containing tutorId/cohortId must be silently ignored
    by pydantic (the default for an unrecognised field), not applied."""
    original_tutor = tutor_factory()
    other_tutor = tutor_factory()
    learner = learner_factory(tutor_id=original_tutor["tutorId"])

    payload = LearnerUpdate.model_validate({"firstName": "Changed", "tutorId": other_tutor["tutorId"], "cohortId": 999999})
    assert not hasattr(payload, "tutorId")
    assert not hasattr(payload, "cohortId")

    result = update_learner(learner["id"], payload, request_factory(), admin_user)
    assert result["firstName"] == "Changed"
    assert result["tutorId"] == original_tutor["tutorId"]

    db.execute("SELECT tutor_id AS \"tutorId\", cohort_id AS \"cohortId\" FROM learners WHERE id = %s", (learner["id"],))
    row = db.fetchone()
    assert row["tutorId"] == original_tutor["tutorId"]
    assert row["cohortId"] is None


def test_completing_a_learner_without_actual_end_date_is_rejected_by_endpoint(db, request_factory, admin_user, learner_factory):
    learner = learner_factory(status="active")
    # Bypass the pydantic-level guard to prove the endpoint's own check also holds
    # (defense in depth, matching the pattern used for tutor/cohort guards).
    payload = LearnerStatusChangeInput.model_construct(status="completed", actualEndDate=None, withdrawalDate=None, reason=None)
    with pytest.raises(HTTPException) as exc:
        change_learner_status(learner["id"], payload, request_factory(), admin_user)
    assert exc.value.status_code == 400


def test_deleting_a_learner_with_allocation_history_never_removes_the_row_or_history(db, request_factory, admin_user, learner_factory):
    """delete_learner is a soft delete -- it must never physically remove
    the learner row or any of their learner_allocation_history rows, since
    apprenticeship attendance data is subject to funding/compliance audits.
    This documents that guarantee by construction, replacing the earlier
    (now superseded) design of having no delete endpoint at all."""
    from pyapp.routers.learners import LearnerDeleteInput, delete_learner

    learner = learner_factory(status="active")
    db.execute(
        "INSERT INTO learner_allocation_history (learner_id, previous_tutor_id, new_tutor_id, effective_date, changed_by) "
        "VALUES (%s, NULL, NULL, '2026-01-01', %s)",
        (learner["id"], admin_user["userId"]),
    )

    delete_learner(learner["id"], LearnerDeleteInput(reason="Duplicate record"), request_factory(), admin_user)

    db.execute("SELECT * FROM learners WHERE id = %s", (learner["id"],))
    row = db.fetchone()
    assert row is not None, "the learner row must still exist after deletion"
    assert row["deleted_at"] is not None
    assert row["deleted_by"] == admin_user["userId"]
    assert row["deletion_reason"] == "Duplicate record"

    db.execute("SELECT count(*) AS c FROM learner_allocation_history WHERE learner_id = %s", (learner["id"],))
    assert db.fetchone()["c"] == 1, "allocation history must never be removed by a delete"


def test_deleting_an_already_deleted_learner_is_rejected(db, request_factory, admin_user, learner_factory):
    from pyapp.routers.learners import LearnerDeleteInput, delete_learner

    learner = learner_factory(status="active")
    delete_learner(learner["id"], LearnerDeleteInput(reason="First deletion"), request_factory(), admin_user)

    with pytest.raises(HTTPException) as exc:
        delete_learner(learner["id"], LearnerDeleteInput(reason="Second attempt"), request_factory(), admin_user)
    assert exc.value.status_code == 400


def test_deleting_a_nonexistent_learner_404s(request_factory, admin_user):
    from pyapp.routers.learners import LearnerDeleteInput, delete_learner

    with pytest.raises(HTTPException) as exc:
        delete_learner(999999999, LearnerDeleteInput(reason="N/A"), request_factory(), admin_user)
    assert exc.value.status_code == 404


def test_deleted_learner_no_longer_appears_in_listings_or_lookups(db, request_factory, admin_user, learner_factory):
    from pyapp.routers.learners import LearnerDeleteInput, delete_learner, get_learner, list_learners

    learner = learner_factory(status="active")
    delete_learner(learner["id"], LearnerDeleteInput(reason="Removing"), request_factory(), admin_user)

    with pytest.raises(HTTPException) as exc:
        get_learner(learner["id"], admin_user)
    assert exc.value.status_code == 404

    listed = list_learners(session=admin_user)
    assert learner["id"] not in {row["id"] for row in listed["items"]}


def test_deleting_a_learner_cancels_their_pending_scheduled_transfer(db, request_factory, admin_user, learner_factory):
    from pyapp.routers.learners import LearnerDeleteInput, delete_learner

    learner = learner_factory(status="active")
    db.execute(
        "INSERT INTO scheduled_allocations (learner_id, new_tutor_id, new_cohort_id, effective_date, created_by, status) "
        "VALUES (%s, NULL, NULL, '2099-01-01', %s, 'pending') RETURNING id",
        (learner["id"], admin_user["userId"]),
    )
    scheduled_id = db.fetchone()["id"]

    delete_learner(learner["id"], LearnerDeleteInput(reason="Leaving programme"), request_factory(), admin_user)

    db.execute("SELECT status FROM scheduled_allocations WHERE id = %s", (scheduled_id,))
    assert db.fetchone()["status"] == "cancelled"
