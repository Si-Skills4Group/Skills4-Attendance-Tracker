import pytest
from fastapi import HTTPException

from pyapp.routers.cohorts import (
    CohortDeleteInput,
    CohortFlagInactiveInput,
    CohortInput,
    CohortRejectInactiveFlagInput,
    CohortUpdate,
    activate_cohort,
    confirm_cohort_inactive_flag,
    create_cohort,
    deactivate_cohort,
    delete_cohort,
    flag_cohort_inactive,
    get_cohort,
    get_cohort_learners,
    list_cohort_summary,
    list_cohorts,
    reject_cohort_inactive_flag,
    unflag_cohort_inactive,
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


def test_non_teaching_tutor_cannot_be_assigned_on_create(db, request_factory, admin_user, tutor_factory):
    non_teaching_tutor = tutor_factory(is_teaching_tutor=False)
    payload = CohortInput(**_base_cohort_kwargs(tutorId=non_teaching_tutor["tutorId"]))

    with pytest.raises(HTTPException) as exc:
        create_cohort(payload, request_factory(), admin_user)
    assert exc.value.status_code == 400
    assert "non-teaching tutor" in str(exc.value.detail).lower()


def test_inactive_tutor_cannot_be_assigned_on_update(db, request_factory, admin_user, tutor_factory, cohort_factory):
    inactive_tutor = tutor_factory(active=False)
    cohort = cohort_factory()

    with pytest.raises(HTTPException) as exc:
        update_cohort(cohort["id"], CohortUpdate(tutorId=inactive_tutor["tutorId"]), request_factory(), admin_user)
    assert exc.value.status_code == 400


class TestReassigningATutorCascadesToItsHomeLearners:
    """Regression: update_cohort used to only touch cohorts.tutor_id,
    leaving every home learner's own tutor_id pointing at the old tutor --
    undercounting the new tutor's learners (dashboards/Learners page filter
    on learners.tutor_id) and leaving the old tutor with a stale
    require_learner_access grant while the new tutor could be denied direct
    access to the same learners."""

    def test_reassigning_a_cohorts_tutor_updates_its_home_learners_tutor_id(
        self, db, request_factory, admin_user, tutor_factory, cohort_factory, learner_factory,
    ):
        old_tutor = tutor_factory()
        new_tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=old_tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=old_tutor["tutorId"])

        update_cohort(cohort["id"], CohortUpdate(tutorId=new_tutor["tutorId"]), request_factory(), admin_user)

        db.execute("SELECT tutor_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["tutor_id"] == new_tutor["tutorId"]

    def test_does_not_touch_a_secondarily_enrolled_learner_whose_home_cohort_is_elsewhere(
        self, db, request_factory, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        home_tutor = tutor_factory()
        fs_old_tutor = tutor_factory()
        fs_new_tutor = tutor_factory()
        home = cohort_factory(tutor_id=home_tutor["tutorId"])
        fs_cohort = cohort_factory(tutor_id=fs_old_tutor["tutorId"], membership_type="secondary")
        learner = learner_factory(cohort_id=home["id"], tutor_id=home_tutor["tutorId"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        update_cohort(fs_cohort["id"], CohortUpdate(tutorId=fs_new_tutor["tutorId"]), request_factory(), admin_user)

        db.execute("SELECT tutor_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["tutor_id"] == home_tutor["tutorId"]

    def test_renaming_a_cohort_does_not_touch_its_learners_tutor_id(
        self, db, request_factory, tutor_factory, cohort_factory, learner_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        update_cohort(cohort["id"], CohortUpdate(name="Renamed"), request_factory(), tutor["session"])

        db.execute("SELECT tutor_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["tutor_id"] == tutor["tutorId"]


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


class TestCohortInactiveFlag:
    def test_tutor_can_flag_their_own_cohort(self, db, request_factory, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], active=True)

        result = flag_cohort_inactive(
            cohort["id"], CohortFlagInactiveInput(reason="Course finished"), request_factory(), tutor["session"],
        )
        assert result["inactiveFlaggedAt"] is not None
        assert result["active"] is True  # never taken offline by the flag itself

        db.execute("SELECT inactive_flagged_at, inactive_flagged_by FROM cohorts WHERE id = %s", (cohort["id"],))
        row = db.fetchone()
        assert row["inactive_flagged_at"] is not None
        assert row["inactive_flagged_by"] == tutor["userId"]

    def test_tutor_cannot_flag_another_tutors_cohort(self, request_factory, tutor_factory, cohort_factory):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"], active=True)

        with pytest.raises(HTTPException) as exc:
            flag_cohort_inactive(cohort["id"], CohortFlagInactiveInput(reason="x"), request_factory(), other["session"])
        assert exc.value.status_code == 403

    def test_cannot_flag_an_already_flagged_cohort(self, request_factory, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], active=True)
        flag_cohort_inactive(cohort["id"], CohortFlagInactiveInput(reason="x"), request_factory(), tutor["session"])

        with pytest.raises(HTTPException) as exc:
            flag_cohort_inactive(cohort["id"], CohortFlagInactiveInput(reason="x"), request_factory(), tutor["session"])
        assert exc.value.status_code == 409

    def test_cannot_flag_an_already_inactive_cohort(self, request_factory, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], active=False)

        with pytest.raises(HTTPException) as exc:
            flag_cohort_inactive(cohort["id"], CohortFlagInactiveInput(reason="x"), request_factory(), tutor["session"])
        assert exc.value.status_code == 400

    def test_reason_is_required(self):
        with pytest.raises(Exception):
            CohortFlagInactiveInput(reason="")

    def test_flag_unflag_confirm_reject_are_each_audited(
        self, db, request_factory, admin_user, tutor_factory, cohort_factory,
    ):
        tutor = tutor_factory()

        flagged = cohort_factory(tutor_id=tutor["tutorId"], active=True)
        flag_cohort_inactive(flagged["id"], CohortFlagInactiveInput(reason="Finished"), request_factory(), tutor["session"])
        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'cohort' AND entity_id = %s ORDER BY id DESC LIMIT 1",
            (flagged["id"],),
        )
        assert db.fetchone()["action"] == "cohort_flagged_inactive"
        unflag_cohort_inactive(flagged["id"], request_factory(), tutor["session"])
        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'cohort' AND entity_id = %s ORDER BY id DESC LIMIT 1",
            (flagged["id"],),
        )
        assert db.fetchone()["action"] == "cohort_inactive_flag_retracted"

        confirmed = cohort_factory(tutor_id=tutor["tutorId"], active=True)
        flag_cohort_inactive(confirmed["id"], CohortFlagInactiveInput(reason="Finished"), request_factory(), tutor["session"])
        confirm_cohort_inactive_flag(confirmed["id"], request_factory(), admin_user)
        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'cohort' AND entity_id = %s ORDER BY id DESC LIMIT 1",
            (confirmed["id"],),
        )
        assert db.fetchone()["action"] == "cohort_inactive_confirmed"

        rejected = cohort_factory(tutor_id=tutor["tutorId"], active=True)
        flag_cohort_inactive(rejected["id"], CohortFlagInactiveInput(reason="Finished"), request_factory(), tutor["session"])
        reject_cohort_inactive_flag(
            rejected["id"], CohortRejectInactiveFlagInput(reason="Still running"), request_factory(), admin_user,
        )
        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'cohort' AND entity_id = %s ORDER BY id DESC LIMIT 1",
            (rejected["id"],),
        )
        assert db.fetchone()["action"] == "cohort_inactive_flag_rejected"

    def test_confirm_sets_inactive_and_clears_the_flag(self, db, request_factory, admin_user, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], active=True)
        flag_cohort_inactive(cohort["id"], CohortFlagInactiveInput(reason="Finished"), request_factory(), tutor["session"])

        result = confirm_cohort_inactive_flag(cohort["id"], request_factory(), admin_user)
        assert result["active"] is False
        assert result["inactiveFlaggedAt"] is None

    def test_reject_clears_the_flag_but_leaves_the_cohort_active(
        self, request_factory, admin_user, tutor_factory, cohort_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], active=True)
        flag_cohort_inactive(cohort["id"], CohortFlagInactiveInput(reason="Finished"), request_factory(), tutor["session"])

        result = reject_cohort_inactive_flag(
            cohort["id"], CohortRejectInactiveFlagInput(reason="Still running"), request_factory(), admin_user,
        )
        assert result["active"] is True
        assert result["inactiveFlaggedAt"] is None

    def test_confirm_and_reject_are_admin_only(self, request_factory, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], active=True)
        flag_cohort_inactive(cohort["id"], CohortFlagInactiveInput(reason="Finished"), request_factory(), tutor["session"])

        # confirm_cohort_inactive_flag/reject_cohort_inactive_flag are both
        # declared with Depends(require_admin), enforced at the FastAPI
        # dependency layer rather than inside the function body -- calling
        # them directly (as every other test here does) bypasses that
        # dependency entirely, so role enforcement is asserted the same way
        # TestTutorCanRenameOwnCohort does elsewhere in this file: by reading
        # the route's own declared dependency rather than re-invoking it as
        # if it were an unprotected function.
        import inspect

        confirm_sig = inspect.signature(confirm_cohort_inactive_flag)
        assert confirm_sig.parameters["_session"].default.dependency.__name__ == "require_admin"
        reject_sig = inspect.signature(reject_cohort_inactive_flag)
        assert reject_sig.parameters["_session"].default.dependency.__name__ == "require_admin"

    def test_confirming_or_rejecting_with_no_active_flag_is_409(self, request_factory, admin_user, cohort_factory):
        cohort = cohort_factory(active=True)

        with pytest.raises(HTTPException) as exc:
            confirm_cohort_inactive_flag(cohort["id"], request_factory(), admin_user)
        assert exc.value.status_code == 409

        with pytest.raises(HTTPException) as exc:
            reject_cohort_inactive_flag(cohort["id"], CohortRejectInactiveFlagInput(reason="x"), request_factory(), admin_user)
        assert exc.value.status_code == 409

    def test_unflagging_with_no_active_flag_is_409(self, request_factory, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], active=True)

        with pytest.raises(HTTPException) as exc:
            unflag_cohort_inactive(cohort["id"], request_factory(), tutor["session"])
        assert exc.value.status_code == 409

    def test_direct_deactivate_clears_a_stale_flag(
        self, db, request_factory, admin_user, tutor_factory, cohort_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], active=True)
        flag_cohort_inactive(cohort["id"], CohortFlagInactiveInput(reason="Finished"), request_factory(), tutor["session"])

        deactivate_cohort(cohort["id"], request_factory(), admin_user)
        db.execute("SELECT active, inactive_flagged_at FROM cohorts WHERE id = %s", (cohort["id"],))
        row = db.fetchone()
        assert row["active"] is False
        assert row["inactive_flagged_at"] is None

    def test_direct_activate_clears_a_stale_flag(
        self, db, request_factory, admin_user, tutor_factory, cohort_factory,
    ):
        # A still-active cohort can carry a pending flag (that's the whole
        # point -- flagging never takes it offline by itself), so calling
        # activate_cohort on it directly (already active, bypassing the
        # flag/confirm flow entirely) must still defensively clear that flag.
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], active=True)
        flag_cohort_inactive(cohort["id"], CohortFlagInactiveInput(reason="Finished"), request_factory(), tutor["session"])

        activate_cohort(cohort["id"], request_factory(), admin_user)
        db.execute("SELECT active, inactive_flagged_at FROM cohorts WHERE id = %s", (cohort["id"],))
        row = db.fetchone()
        assert row["active"] is True
        assert row["inactive_flagged_at"] is None


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

    def test_roster_row_carries_the_enrollment_id_only_for_a_secondary_enrollment(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        """secondaryEnrollmentId is how the frontend knows which rows can
        offer a "mark completed" action (ending a secondary enrollment) --
        null for a learner on the roster via their home cohort, set to the
        real learner_cohort_enrollments id for one there via an active
        Functional Skills enrollment. The two are mutually exclusive by
        construction (enrolling into your own home cohort is rejected), so
        this asserts both directions in one pass."""
        home_cohort = cohort_factory()
        fs_cohort = cohort_factory(membership_type="secondary")
        home_learner = learner_factory(cohort_id=home_cohort["id"])
        fs_learner = learner_factory(cohort_id=cohort_factory()["id"])
        enrollment = secondary_enrollment_factory(learner_id=fs_learner["id"], cohort_id=fs_cohort["id"])

        home_roster = get_cohort_learners(home_cohort["id"], session=admin_user)
        assert next(r for r in home_roster if r["id"] == home_learner["id"])["secondaryEnrollmentId"] is None

        fs_roster = get_cohort_learners(fs_cohort["id"], session=admin_user)
        assert next(r for r in fs_roster if r["id"] == fs_learner["id"])["secondaryEnrollmentId"] == enrollment["id"]

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
