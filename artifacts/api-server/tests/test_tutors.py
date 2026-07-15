import pytest
from fastapi import HTTPException

from pyapp.routers.tutors import (
    TutorInput,
    TutorUpdate,
    create_tutor,
    deactivate_tutor,
    update_tutor,
)


def _cleanup_tutor(db, tutor_id):
    db.execute("SELECT user_id FROM tutors WHERE id = %s", (tutor_id,))
    row = db.fetchone()
    db.execute("DELETE FROM cohorts WHERE tutor_id = %s", (tutor_id,))
    db.execute("DELETE FROM tutors WHERE id = %s", (tutor_id,))
    if row:
        db.execute("DELETE FROM users WHERE id = %s", (row["user_id"],))


def test_tutor_can_be_created_without_employee_reference(db, request_factory, admin_user):
    payload = TutorInput(firstName="No", lastName="Reference", email="no-ref-tutor@example.com")
    assert payload.employeeRef is None

    created = create_tutor(payload, request_factory(), admin_user)
    try:
        assert created["employeeRef"] is None
        assert created["active"] is True
    finally:
        _cleanup_tutor(db, created["id"])


def test_duplicate_tutor_email_is_rejected(db, request_factory, admin_user):
    payload = TutorInput(firstName="Original", lastName="Tutor", email="dup-tutor@example.com")
    created = create_tutor(payload, request_factory(), admin_user)
    try:
        duplicate = TutorInput(firstName="Impersonator", lastName="Tutor", email="dup-tutor@example.com")
        with pytest.raises(HTTPException) as exc:
            create_tutor(duplicate, request_factory(), admin_user)
        assert exc.value.status_code == 400
    finally:
        _cleanup_tutor(db, created["id"])


def test_deactivating_tutor_with_active_cohort_requires_confirmation(db, request_factory, tutor_factory, cohort_factory, admin_user):
    tutor = tutor_factory()
    cohort_factory(tutor_id=tutor["tutorId"], active=True)

    with pytest.raises(HTTPException) as exc:
        deactivate_tutor(tutor["tutorId"], request_factory(), confirm=False, _session=admin_user)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "active_cohorts_assigned"
    assert len(exc.value.detail["cohorts"]) == 1


def test_deactivating_tutor_with_confirm_succeeds_and_preserves_the_record(db, request_factory, tutor_factory, cohort_factory, admin_user):
    tutor = tutor_factory()
    cohort = cohort_factory(tutor_id=tutor["tutorId"], active=True)

    result = deactivate_tutor(tutor["tutorId"], request_factory(), confirm=True, _session=admin_user)
    assert result["active"] is False

    # Historical record preserved -- not deleted, and the cohort keeps its
    # (now-inactive) tutor assignment rather than being silently unassigned.
    db.execute("SELECT id, active FROM tutors WHERE id = %s", (tutor["tutorId"],))
    row = db.fetchone()
    assert row is not None
    assert row["active"] is False

    db.execute("SELECT tutor_id FROM cohorts WHERE id = %s", (cohort["id"],))
    assert db.fetchone()["tutor_id"] == tutor["tutorId"]


def test_deactivating_tutor_without_cohorts_needs_no_confirmation(db, request_factory, tutor_factory, admin_user):
    tutor = tutor_factory()
    result = deactivate_tutor(tutor["tutorId"], request_factory(), confirm=False, _session=admin_user)
    assert result["active"] is False


def test_deactivating_tutor_also_deactivates_linked_user_login(db, request_factory, tutor_factory, admin_user):
    tutor = tutor_factory()
    deactivate_tutor(tutor["tutorId"], request_factory(), confirm=False, _session=admin_user)

    db.execute("SELECT active FROM users WHERE id = %s", (tutor["userId"],))
    assert db.fetchone()["active"] is False


def test_update_tutor_deactivation_goes_through_the_same_guard(db, request_factory, tutor_factory, cohort_factory, admin_user):
    """The generic PATCH endpoint must not be a bypass for the
    active-cohorts confirmation guard that the dedicated /deactivate
    endpoint enforces."""
    tutor = tutor_factory()
    cohort_factory(tutor_id=tutor["tutorId"], active=True)

    with pytest.raises(HTTPException) as exc:
        update_tutor(tutor["tutorId"], TutorUpdate(active=False), request_factory(), confirm=False, _session=admin_user)
    assert exc.value.status_code == 409
