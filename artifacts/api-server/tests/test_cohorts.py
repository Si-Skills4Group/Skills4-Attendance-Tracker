import pytest
from fastapi import HTTPException

from pyapp.routers.cohorts import (
    CohortDeleteInput,
    CohortInput,
    CohortUpdate,
    create_cohort,
    deactivate_cohort,
    delete_cohort,
    get_cohort,
    get_cohort_learners,
    list_cohort_summary,
    list_cohorts,
    update_cohort,
)


def _cleanup(db, cohort_id):
    db.execute("DELETE FROM cohorts WHERE id = %s", (cohort_id,))


def _base_cohort_kwargs(**overrides):
    kwargs = dict(
        name="Test Cohort",
        programme="Programme",
        level="3",
        deliveryDay="monday",
        sessionStartTime="09:00",
        sessionEndTime="16:00",
        startDate="2026-01-01",
    )
    kwargs.update(overrides)
    return kwargs


def test_session_end_time_must_be_after_start_time():
    with pytest.raises(HTTPException) as exc:
        CohortInput(**_base_cohort_kwargs(sessionStartTime="16:00", sessionEndTime="09:00"))
    assert exc.value.status_code == 400
    assert "sessionEndTime" in str(exc.value.detail)


def test_equal_start_and_end_time_is_rejected():
    with pytest.raises(HTTPException):
        CohortInput(**_base_cohort_kwargs(sessionStartTime="09:00", sessionEndTime="09:00"))


def test_end_date_cannot_precede_start_date():
    with pytest.raises(HTTPException) as exc:
        CohortInput(**_base_cohort_kwargs(startDate="2026-06-01", endDate="2026-01-01"))
    assert exc.value.status_code == 400
    assert "endDate" in str(exc.value.detail)


def test_valid_schedule_is_accepted():
    payload = CohortInput(**_base_cohort_kwargs())
    assert payload.sessionEndTime == "16:00"


def test_schedule_accepts_hh_mm_ss_matching_frontend_format():
    """Regression test: the frontend's <input type="time"> normalizes
    values to HH:MM:SS before submitting, which used to be rejected by a
    validator that only accepted strict HH:MM -- every cohort create/update
    with a schedule failed with a 400 the user never saw a useful message
    for (the frontend's onError handler was also reading the wrong field)."""
    payload = CohortInput(**_base_cohort_kwargs(sessionStartTime="09:00:00", sessionEndTime="16:00:00"))
    assert payload.sessionStartTime == "09:00:00"
    assert payload.sessionEndTime == "16:00:00"


def test_inactive_tutor_cannot_be_assigned_on_create(db, request_factory, admin_user, tutor_factory):
    inactive_tutor = tutor_factory(active=False)
    payload = CohortInput(**_base_cohort_kwargs(tutorId=inactive_tutor["tutorId"]))

    with pytest.raises(HTTPException) as exc:
        create_cohort(payload, request_factory(), admin_user)
    assert exc.value.status_code == 400
    assert "inactive tutor" in str(exc.value.detail).lower()


def test_active_tutor_can_be_assigned_on_create(db, request_factory, admin_user, tutor_factory):
    tutor = tutor_factory(active=True)
    payload = CohortInput(**_base_cohort_kwargs(tutorId=tutor["tutorId"]))

    created = create_cohort(payload, request_factory(), admin_user)
    try:
        assert created["tutorId"] == tutor["tutorId"]
    finally:
        _cleanup(db, created["id"])


def test_inactive_tutor_cannot_be_assigned_on_update(db, request_factory, admin_user, tutor_factory, cohort_factory):
    inactive_tutor = tutor_factory(active=False)
    cohort = cohort_factory()

    with pytest.raises(HTTPException) as exc:
        update_cohort(cohort["id"], CohortUpdate(tutorId=inactive_tutor["tutorId"]), request_factory(), admin_user)
    assert exc.value.status_code == 400


class TestTutorCanRenameOwnCohort:
    def test_tutor_can_rename_their_own_cohort(self, request_factory, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])

        result = update_cohort(cohort["id"], CohortUpdate(name="Renamed Cohort"), request_factory(), tutor["session"])
        assert result["name"] == "Renamed Cohort"

    def test_tutor_cannot_change_any_other_field(self, request_factory, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])

        with pytest.raises(HTTPException) as exc:
            update_cohort(cohort["id"], CohortUpdate(programme="New Programme"), request_factory(), tutor["session"])
        assert exc.value.status_code == 403

        with pytest.raises(HTTPException) as exc:
            update_cohort(cohort["id"], CohortUpdate(active=False), request_factory(), tutor["session"])
        assert exc.value.status_code == 403

    def test_tutor_cannot_smuggle_another_field_in_alongside_a_real_rename(
        self, db, request_factory, tutor_factory, cohort_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])

        with pytest.raises(HTTPException) as exc:
            update_cohort(
                cohort["id"], CohortUpdate(name="Renamed Cohort", level="5"), request_factory(), tutor["session"],
            )
        assert exc.value.status_code == 403

        db.execute("SELECT name, level FROM cohorts WHERE id = %s", (cohort["id"],))
        row = db.fetchone()
        assert row["name"] != "Renamed Cohort"
        assert row["level"] != "5"

    def test_tutor_cannot_rename_another_tutors_cohort(self, request_factory, tutor_factory, cohort_factory):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])

        with pytest.raises(HTTPException) as exc:
            update_cohort(cohort["id"], CohortUpdate(name="Hijacked"), request_factory(), other["session"])
        assert exc.value.status_code == 403

    def test_admin_can_still_change_every_field(self, request_factory, admin_user, cohort_factory):
        cohort = cohort_factory()

        result = update_cohort(
            cohort["id"], CohortUpdate(name="Admin Renamed", programme="New Programme"), request_factory(), admin_user,
        )
        assert result["name"] == "Admin Renamed"
        assert result["programme"] == "New Programme"


def test_cohort_changes_are_audited(db, request_factory, admin_user, cohort_factory):
    cohort = cohort_factory(name="Before Rename")
    update_cohort(cohort["id"], CohortUpdate(name="After Rename"), request_factory(), admin_user)

    db.execute(
        "SELECT previous_value, new_value FROM audit_logs WHERE entity_type = 'cohort' AND entity_id = %s "
        "AND action = 'update' ORDER BY id DESC LIMIT 1",
        (cohort["id"],),
    )
    row = db.fetchone()
    assert row is not None
    assert "Before Rename" in row["previous_value"]
    assert "After Rename" in row["new_value"]


def test_cohort_activation_and_deactivation_are_audited(db, request_factory, admin_user, cohort_factory):
    cohort = cohort_factory(active=True)
    deactivate_cohort(cohort["id"], request_factory(), admin_user)

    db.execute(
        "SELECT action FROM audit_logs WHERE entity_type = 'cohort' AND entity_id = %s ORDER BY id DESC LIMIT 1",
        (cohort["id"],),
    )
    assert db.fetchone()["action"] == "deactivate"


def test_deactivated_cohort_is_not_deleted(db, request_factory, admin_user, cohort_factory):
    cohort = cohort_factory(active=True)
    deactivate_cohort(cohort["id"], request_factory(), admin_user)

    db.execute("SELECT active FROM cohorts WHERE id = %s", (cohort["id"],))
    row = db.fetchone()
    assert row is not None
    assert row["active"] is False


def test_delivery_day_must_be_a_known_value():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        CohortInput(**_base_cohort_kwargs(deliveryDay="funday"))


def test_deleting_an_empty_cohort_never_removes_the_row(db, request_factory, admin_user, cohort_factory):
    cohort = cohort_factory()

    delete_cohort(cohort["id"], CohortDeleteInput(reason="Set up in error"), request_factory(), admin_user)

    db.execute("SELECT * FROM cohorts WHERE id = %s", (cohort["id"],))
    row = db.fetchone()
    assert row is not None, "the cohort row must still exist after deletion"
    assert row["deleted_at"] is not None
    assert row["deleted_by"] == admin_user["userId"]
    assert row["deletion_reason"] == "Set up in error"


def test_deleting_a_cohort_with_an_active_learner_is_blocked(db, request_factory, admin_user, cohort_factory, learner_factory):
    cohort = cohort_factory()
    learner_factory(cohort_id=cohort["id"], status="active")

    with pytest.raises(HTTPException) as exc:
        delete_cohort(cohort["id"], CohortDeleteInput(reason="Try anyway"), request_factory(), admin_user)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "cohort_not_empty"
    assert exc.value.detail["activeLearnerCount"] == 1


def test_deleting_a_cohort_with_a_session_is_blocked(db, request_factory, admin_user, cohort_factory, attendance_session_factory):
    cohort = cohort_factory()
    attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

    with pytest.raises(HTTPException) as exc:
        delete_cohort(cohort["id"], CohortDeleteInput(reason="Try anyway"), request_factory(), admin_user)
    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "cohort_not_empty"
    assert exc.value.detail["sessionCount"] == 1


def test_cohort_can_be_deleted_once_its_learners_and_sessions_are_cleared(
    db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
):
    cohort = cohort_factory()
    learner = learner_factory(cohort_id=cohort["id"], status="active")
    session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

    db.execute("UPDATE learners SET status = 'withdrawn', withdrawal_date = '2026-01-01' WHERE id = %s", (learner["id"],))
    db.execute("UPDATE attendance_sessions SET deleted_at = now() WHERE id = %s", (session["id"],))

    delete_cohort(cohort["id"], CohortDeleteInput(reason="No longer needed"), request_factory(), admin_user)

    db.execute("SELECT deleted_at FROM cohorts WHERE id = %s", (cohort["id"],))
    assert db.fetchone()["deleted_at"] is not None


def test_deleting_an_already_deleted_cohort_is_rejected(db, request_factory, admin_user, cohort_factory):
    cohort = cohort_factory()
    delete_cohort(cohort["id"], CohortDeleteInput(reason="First"), request_factory(), admin_user)

    with pytest.raises(HTTPException) as exc:
        delete_cohort(cohort["id"], CohortDeleteInput(reason="Second"), request_factory(), admin_user)
    assert exc.value.status_code == 400


def test_deleting_a_nonexistent_cohort_404s(request_factory, admin_user):
    with pytest.raises(HTTPException) as exc:
        delete_cohort(999999999, CohortDeleteInput(reason="N/A"), request_factory(), admin_user)
    assert exc.value.status_code == 404


def test_deleted_cohort_no_longer_appears_in_listings_or_lookups(db, request_factory, admin_user, cohort_factory):
    cohort = cohort_factory()
    delete_cohort(cohort["id"], CohortDeleteInput(reason="Removing"), request_factory(), admin_user)

    with pytest.raises(HTTPException) as exc:
        get_cohort(cohort["id"], session=admin_user)
    assert exc.value.status_code == 404

    listed = list_cohorts(session=admin_user)
    assert cohort["id"] not in {row["id"] for row in listed}


def test_cohort_delete_is_audited(db, request_factory, admin_user, cohort_factory):
    cohort = cohort_factory()
    delete_cohort(cohort["id"], CohortDeleteInput(reason="Duplicate cohort"), request_factory(), admin_user)

    db.execute(
        "SELECT new_value FROM audit_logs WHERE entity_type = 'cohort' AND entity_id = %s AND action = 'delete_cohort' "
        "ORDER BY id DESC LIMIT 1",
        (cohort["id"],),
    )
    row = db.fetchone()
    assert row is not None
    assert "Duplicate cohort" in row["new_value"]


class TestFunctionalSkillsSecondaryCohorts:
    """A learner secondarily enrolled in a 'secondary' membership_type
    cohort must show up wherever a cohort's roster/learner count is read,
    without ever appearing in learners.cohort_id for that cohort."""

    def test_secondarily_enrolled_learner_appears_in_cohort_roster(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        roster = get_cohort_learners(fs_cohort["id"], session=admin_user)
        assert {r["id"] for r in roster} == {learner["id"]}

    def test_secondarily_enrolled_learner_counts_toward_learner_count(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"], status="active")
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        detail = get_cohort(fs_cohort["id"], session=admin_user)
        assert detail["learnerCount"] == 1

        summary = list_cohort_summary(session=admin_user)
        fs_summary = next(row for row in summary if row["id"] == fs_cohort["id"])
        assert fs_summary["activeLearnerCount"] == 1

    def test_ended_enrollment_no_longer_counts(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"], status="active")
        secondary_enrollment_factory(
            learner_id=learner["id"], cohort_id=fs_cohort["id"], status="ended", end_date="2026-03-01",
        )

        detail = get_cohort(fs_cohort["id"], session=admin_user)
        assert detail["learnerCount"] == 0

    def test_deleting_a_secondary_cohort_with_active_enrollments_is_blocked(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        with pytest.raises(HTTPException) as exc:
            delete_cohort(fs_cohort["id"], CohortDeleteInput(reason="Try anyway"), request_factory(), admin_user)
        assert exc.value.status_code == 409
        assert exc.value.detail["activeSecondaryEnrollmentCount"] == 1

    def test_cannot_switch_to_secondary_while_it_has_home_learners(
        self, request_factory, admin_user, cohort_factory, learner_factory,
    ):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"])

        with pytest.raises(HTTPException) as exc:
            update_cohort(cohort["id"], CohortUpdate(membershipType="secondary"), request_factory(), admin_user)
        assert exc.value.status_code == 409

    def test_cannot_switch_back_to_primary_while_it_has_active_enrollments(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        with pytest.raises(HTTPException) as exc:
            update_cohort(fs_cohort["id"], CohortUpdate(membershipType="primary"), request_factory(), admin_user)
        assert exc.value.status_code == 409

    def test_new_cohorts_default_to_primary(self, db, request_factory, admin_user, cohort_factory):
        created = create_cohort(CohortInput(**_base_cohort_kwargs()), request_factory(), admin_user)
        assert created["membershipType"] == "primary"
        _cleanup(db, created["id"])

    def test_creating_a_secondary_cohort_requires_a_subject(self):
        with pytest.raises(HTTPException) as exc:
            CohortInput(**_base_cohort_kwargs(membershipType="secondary"))
        assert exc.value.status_code == 400

    def test_creating_a_primary_cohort_rejects_a_subject(self):
        with pytest.raises(HTTPException) as exc:
            CohortInput(**_base_cohort_kwargs(membershipType="primary", subject="math"))
        assert exc.value.status_code == 400

    def test_creating_a_secondary_cohort_with_a_subject_succeeds(self, db, request_factory, admin_user):
        created = create_cohort(
            CohortInput(**_base_cohort_kwargs(membershipType="secondary", subject="english")),
            request_factory(), admin_user,
        )
        assert created["membershipType"] == "secondary"
        assert created["subject"] == "english"
        _cleanup(db, created["id"])

    def test_cannot_switch_to_secondary_without_providing_a_subject(self, request_factory, admin_user, cohort_factory):
        cohort = cohort_factory()
        with pytest.raises(HTTPException) as exc:
            update_cohort(cohort["id"], CohortUpdate(membershipType="secondary"), request_factory(), admin_user)
        assert exc.value.status_code == 400

    def test_can_switch_to_secondary_when_subject_is_provided_in_the_same_request(
        self, request_factory, admin_user, cohort_factory,
    ):
        cohort = cohort_factory()
        result = update_cohort(
            cohort["id"], CohortUpdate(membershipType="secondary", subject="both"), request_factory(), admin_user,
        )
        assert result["membershipType"] == "secondary"
        assert result["subject"] == "both"

    def test_switching_back_to_primary_clears_a_previously_set_subject(
        self, request_factory, admin_user, cohort_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary", subject="math")
        result = update_cohort(
            fs_cohort["id"], CohortUpdate(membershipType="primary", subject=None), request_factory(), admin_user,
        )
        assert result["membershipType"] == "primary"
        assert result["subject"] is None

    def test_cannot_set_a_subject_on_a_standard_cohort_via_update(self, request_factory, admin_user, cohort_factory):
        cohort = cohort_factory()
        with pytest.raises(HTTPException) as exc:
            update_cohort(cohort["id"], CohortUpdate(subject="math"), request_factory(), admin_user)
        assert exc.value.status_code == 400
