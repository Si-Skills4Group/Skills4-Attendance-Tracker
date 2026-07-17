import datetime

import pytest
from fastapi import HTTPException

import pydantic

from pyapp import auth as auth_module
from pyapp.allocation_lib import expected_learners_count_sql, learners_expected_in_cohort_as_of
from pyapp.routers.allocation_routes import AllocationInput, allocate_learners
from pyapp.routers.attendance import (
    AttendanceRegisterInput,
    AttendanceSessionInput,
    AttendanceSessionUpdate,
    RefreshRegisterInput,
    RegisterEntryInput,
    SessionCancelInput,
    cancel_attendance_session,
    create_attendance_session,
    get_attendance_session,
    get_session_expected_learners,
    list_attendance_sessions,
    mark_all_present,
    refresh_session_register,
    save_attendance_register,
    update_attendance_session,
)
from pyapp.routers.cohorts import get_cohort


class TestSessionCreationRequiresTitle:
    def test_missing_title_is_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            AttendanceSessionInput(
                cohortId=1, sessionDate="2026-02-01", plannedStartTime="09:00",
                plannedEndTime="16:00", plannedDurationHours=7,
            )

    def test_empty_title_is_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            AttendanceSessionInput(
                cohortId=1, sessionDate="2026-02-01", plannedStartTime="09:00",
                plannedEndTime="16:00", plannedDurationHours=7, title="",
            )

    def test_valid_title_is_accepted(self):
        payload = AttendanceSessionInput(
            cohortId=1, sessionDate="2026-02-01", plannedStartTime="09:00",
            plannedEndTime="16:00", plannedDurationHours=7, title="Module 1 Intro",
        )
        assert payload.title == "Module 1 Intro"


class TestLearnerEligibilityRespectsLifecycle:
    """learners_expected_in_cohort_as_of/expected_learners_count_sql resolve
    cohort *membership* from allocation history, but that alone isn't
    enough -- a learner who hasn't started yet, or has already
    withdrawn/completed as of the session date, must not be expected."""

    def test_learner_not_yet_started_is_excluded(self, db, cohort_factory, learner_factory):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"], start_date="2026-03-01")
        assert learners_expected_in_cohort_as_of(db, cohort["id"], datetime.date(2026, 2, 1)) == []

    def test_learner_is_included_on_their_exact_start_date(self, db, cohort_factory, learner_factory):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], start_date="2026-03-01")
        assert learners_expected_in_cohort_as_of(db, cohort["id"], datetime.date(2026, 3, 1)) == [learner["id"]]

    def test_learner_withdrawn_before_session_date_is_excluded(self, db, cohort_factory, learner_factory):
        cohort = cohort_factory()
        learner_factory(
            cohort_id=cohort["id"], start_date="2026-01-01",
            status="withdrawn", withdrawal_date="2026-02-01",
        )
        assert learners_expected_in_cohort_as_of(db, cohort["id"], datetime.date(2026, 2, 15)) == []

    def test_learner_withdrawn_after_session_date_is_still_expected_for_that_past_session(
        self, db, cohort_factory, learner_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(
            cohort_id=cohort["id"], start_date="2026-01-01",
            status="withdrawn", withdrawal_date="2026-02-01",
        )
        assert learners_expected_in_cohort_as_of(db, cohort["id"], datetime.date(2026, 1, 15)) == [learner["id"]]

    def test_learner_completed_before_session_date_is_excluded(self, db, cohort_factory, learner_factory):
        cohort = cohort_factory()
        learner_factory(
            cohort_id=cohort["id"], start_date="2026-01-01",
            status="completed", actual_end_date="2026-02-01",
        )
        assert learners_expected_in_cohort_as_of(db, cohort["id"], datetime.date(2026, 2, 15)) == []

    def test_learner_completed_after_session_date_is_still_expected_for_that_past_session(
        self, db, cohort_factory, learner_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(
            cohort_id=cohort["id"], start_date="2026-01-01",
            status="completed", actual_end_date="2026-02-01",
        )
        assert learners_expected_in_cohort_as_of(db, cohort["id"], datetime.date(2026, 1, 15)) == [learner["id"]]

    def test_expected_learners_count_sql_agrees_with_the_python_helper(
        self, db, cohort_factory, learner_factory,
    ):
        """The SQL-fragment version (used for batched queries) must never
        diverge from the per-cohort Python helper -- this pins them together
        across a mixed set of eligible/ineligible learners."""
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")  # eligible
        learner_factory(cohort_id=cohort["id"], start_date="2026-06-01")  # not started yet
        learner_factory(
            cohort_id=cohort["id"], start_date="2026-01-01",
            status="withdrawn", withdrawal_date="2026-01-15",
        )  # withdrawn before as-of date

        as_of = datetime.date(2026, 2, 1)
        expected_ids = learners_expected_in_cohort_as_of(db, cohort["id"], as_of)
        assert len(expected_ids) == 1

        db.execute(
            f"SELECT {expected_learners_count_sql('%(cohort_id)s', '%(as_of)s')} AS count",
            {"cohort_id": cohort["id"], "as_of": as_of},
        )
        assert db.fetchone()["count"] == len(expected_ids)


class TestHistoricalAttendanceImmutability:
    """The core regression this phase exists to prevent: a cohort transfer
    must never move, copy, delete, or recalculate existing attendance."""

    def test_transferred_learner_keeps_historical_record_and_visibility(
        self, db, request_factory, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory,
    ):
        old_tutor = tutor_factory()
        new_tutor = tutor_factory()
        old_cohort = cohort_factory(tutor_id=old_tutor["tutorId"])
        new_cohort = cohort_factory(tutor_id=new_tutor["tutorId"])
        learner = learner_factory(tutor_id=old_tutor["tutorId"], cohort_id=old_cohort["id"])

        past_date = datetime.date.today() - datetime.timedelta(days=10)
        transfer_date = datetime.date.today() - datetime.timedelta(days=5)
        future_date = datetime.date.today() + datetime.timedelta(days=5)

        past_session = attendance_session_factory(
            cohort_id=old_cohort["id"], session_date=past_date, created_by=admin_user["userId"]
        )
        save_attendance_register(
            past_session["id"],
            AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)]),
            request_factory(), admin_user,
        )
        db.execute("SELECT id, status FROM attendance_records WHERE session_id = %s AND learner_id = %s", (past_session["id"], learner["id"]))
        original_record = db.fetchone()

        allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], tutorId=new_tutor["tutorId"], cohortId=new_cohort["id"], effectiveDate=transfer_date),
            request_factory(), admin_user,
        )

        db.execute("SELECT id, status FROM attendance_records WHERE session_id = %s AND learner_id = %s", (past_session["id"], learner["id"]))
        assert db.fetchone() == original_record, "historical attendance record changed after transfer"

        old_session_view = get_attendance_session(past_session["id"], admin_user)
        entries = [e for e in old_session_view["entries"] if e["learnerId"] == learner["id"]]
        assert len(entries) == 1
        assert entries[0]["status"] == "present"
        assert old_session_view["session"]["expectedCount"] == 1

        future_old_cohort_session = attendance_session_factory(
            cohort_id=old_cohort["id"], session_date=future_date, created_by=admin_user["userId"]
        )
        assert get_attendance_session(future_old_cohort_session["id"], admin_user)["session"]["expectedCount"] == 0

        future_new_cohort_session = attendance_session_factory(
            cohort_id=new_cohort["id"], session_date=future_date, created_by=admin_user["userId"]
        )
        assert get_attendance_session(future_new_cohort_session["id"], admin_user)["session"]["expectedCount"] == 1

    def test_mark_all_present_on_past_session_still_includes_transferred_out_learner(
        self, db, request_factory, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory,
    ):
        old_cohort = cohort_factory()
        new_cohort = cohort_factory()
        learner = learner_factory(cohort_id=old_cohort["id"])
        past_date = datetime.date.today() - datetime.timedelta(days=3)
        session = attendance_session_factory(cohort_id=old_cohort["id"], session_date=past_date, created_by=admin_user["userId"])

        allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], cohortId=new_cohort["id"], effectiveDate=datetime.date.today()),
            request_factory(), admin_user,
        )

        result = mark_all_present(session["id"], request_factory(), admin_user)
        marked = [e for e in result["entries"] if e["learnerId"] == learner["id"]]
        assert len(marked) == 1 and marked[0]["status"] == "present"

    def test_register_save_rejects_learner_not_expected_as_of_session_date(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        other_cohort = cohort_factory()
        outsider = learner_factory(cohort_id=other_cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        with pytest.raises(HTTPException) as exc:
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=outsider["id"], status="present", hoursAttended=7, minutesLate=0)]),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 403


class TestAttendanceSessionAccess:
    def test_tutor_cannot_list_another_tutors_cohort_sessions_via_cohort_id(
        self, tutor_factory, cohort_factory,
    ):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])

        with pytest.raises(HTTPException) as exc:
            list_attendance_sessions(cohortId=cohort["id"], tutorId=None, dateFrom=None, dateTo=None, session=other["session"])
        assert exc.value.status_code == 403

    def test_tutor_can_list_their_own_cohort_sessions_via_cohort_id(
        self, admin_user, request_factory, tutor_factory, cohort_factory, attendance_session_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        sessions = list_attendance_sessions(cohortId=cohort["id"], tutorId=None, dateFrom=None, dateTo=None, session=tutor["session"])
        assert len(sessions) == 1

    def test_nonexistent_cohort_id_is_404_not_403(self, tutor_factory):
        tutor = tutor_factory()
        with pytest.raises(HTTPException) as exc:
            list_attendance_sessions(cohortId=999_999_999, tutorId=None, dateFrom=None, dateTo=None, session=tutor["session"])
        assert exc.value.status_code == 404

    def test_tutor_cannot_access_another_tutors_session_directly(
        self, admin_user, request_factory, tutor_factory, cohort_factory, attendance_session_factory,
    ):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        with pytest.raises(HTTPException) as exc:
            get_attendance_session(session["id"], other["session"])
        assert exc.value.status_code == 403


class TestAttendanceCohortDetailAccess:
    """The frontend's /attendance/cohorts/{id} page (the corrected cohort
    navigation destination) fetches the cohort itself via GET /cohorts/{id}
    (get_cohort) and that cohort's sessions via GET /attendance/sessions?
    cohortId={id} (list_attendance_sessions, covered above) -- there is no
    separate GET /attendance/cohorts/{id} route in this repo; both real
    endpoints the page actually calls must independently enforce access."""

    def test_tutor_cannot_view_another_tutors_cohort(self, tutor_factory, cohort_factory):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])

        with pytest.raises(HTTPException) as exc:
            get_cohort(cohort["id"], session=other["session"])
        assert exc.value.status_code == 403

    def test_tutor_can_view_their_own_cohort(self, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])

        result = get_cohort(cohort["id"], session=tutor["session"])
        assert result["id"] == cohort["id"]

    def test_admin_can_view_any_cohort(self, admin_user, tutor_factory, cohort_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])

        result = get_cohort(cohort["id"], session=admin_user)
        assert result["id"] == cohort["id"]

    def test_nonexistent_cohort_is_404(self, admin_user):
        with pytest.raises(HTTPException) as exc:
            get_cohort(999_999_999, session=admin_user)
        assert exc.value.status_code == 404


class TestCohortNavigationDataIntegrity:
    """Two cohorts, two sessions each -- proves the corrected per-cohort
    session page (GET /attendance/sessions?cohortId=X) never mixes the two,
    and that each session's completion counts are its own, not borrowed
    from a session in the other cohort."""

    def test_each_cohorts_sessions_are_fully_isolated_with_correct_completion_counts(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort_a = cohort_factory(name="Cohort A")
        cohort_b = cohort_factory(name="Cohort B")
        learner_a = learner_factory(cohort_id=cohort_a["id"])
        learner_b = learner_factory(cohort_id=cohort_b["id"])

        a1 = attendance_session_factory(cohort_id=cohort_a["id"], session_date="2026-02-01", created_by=admin_user["userId"])
        a2 = attendance_session_factory(cohort_id=cohort_a["id"], session_date="2026-02-08", created_by=admin_user["userId"])
        b1 = attendance_session_factory(cohort_id=cohort_b["id"], session_date="2026-02-01", created_by=admin_user["userId"])
        b2 = attendance_session_factory(cohort_id=cohort_b["id"], session_date="2026-02-08", created_by=admin_user["userId"])

        # Mark only a1 and b2 complete, leaving a2/b1 incomplete, so a mix-up
        # between cohorts would show up as a wrong completion count, not just
        # a missing/extra session.
        save_attendance_register(
            a1["id"],
            AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=learner_a["id"], status="present", hoursAttended=7, minutesLate=0)]),
            request_factory(), admin_user,
        )
        save_attendance_register(
            b2["id"],
            AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=learner_b["id"], status="present", hoursAttended=7, minutesLate=0)]),
            request_factory(), admin_user,
        )

        cohort_a_sessions = list_attendance_sessions(cohortId=cohort_a["id"], tutorId=None, dateFrom=None, dateTo=None, session=admin_user)
        cohort_b_sessions = list_attendance_sessions(cohortId=cohort_b["id"], tutorId=None, dateFrom=None, dateTo=None, session=admin_user)

        assert {s["id"] for s in cohort_a_sessions} == {a1["id"], a2["id"]}
        assert {s["id"] for s in cohort_b_sessions} == {b1["id"], b2["id"]}

        by_id_a = {s["id"]: s for s in cohort_a_sessions}
        by_id_b = {s["id"]: s for s in cohort_b_sessions}
        assert by_id_a[a1["id"]]["recordedCount"] == 1 and by_id_a[a1["id"]]["expectedCount"] == 1
        assert by_id_a[a2["id"]]["recordedCount"] == 0 and by_id_a[a2["id"]]["expectedCount"] == 1
        assert by_id_b[b1["id"]]["recordedCount"] == 0 and by_id_b[b1["id"]]["expectedCount"] == 1
        assert by_id_b[b2["id"]]["recordedCount"] == 1 and by_id_b[b2["id"]]["expectedCount"] == 1


def _as_tutor(monkeypatch, tutor_id, user_id):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}

    def fake_require_auth(request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)


def _as_admin(monkeypatch, user_id=999999):
    session = {"userId": user_id, "role": "admin", "tutorId": None}

    def fake_require_auth(request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)


class TestSessionCreationConflicts:
    def test_duplicate_same_cohort_date_and_start_time_is_rejected(
        self, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", planned_start_time="09:00",
            created_by=admin_user["userId"],
        )

        with pytest.raises(HTTPException) as exc:
            create_attendance_session(
                AttendanceSessionInput(
                    cohortId=cohort["id"], sessionDate="2026-02-01", plannedStartTime="09:00",
                    plannedEndTime="16:00", plannedDurationHours=7, title="Session 2",
                ),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 409
        assert "duplicate_session" in exc.value.detail["reasons"]

    def test_different_start_time_same_day_is_not_a_duplicate(
        self, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", planned_start_time="09:00",
            created_by=admin_user["userId"],
        )

        result = create_attendance_session(
            AttendanceSessionInput(
                cohortId=cohort["id"], sessionDate="2026-02-01", plannedStartTime="13:00",
                plannedEndTime="16:00", plannedDurationHours=3, title="PM session",
            ),
            request_factory(), admin_user,
        )
        assert result["id"]

    def test_session_date_outside_cohort_range_is_rejected(self, request_factory, admin_user, cohort_factory):
        cohort = cohort_factory(start_date="2026-01-01", end_date="2026-06-30")

        with pytest.raises(HTTPException) as exc:
            create_attendance_session(
                AttendanceSessionInput(
                    cohortId=cohort["id"], sessionDate="2026-07-15", plannedStartTime="09:00",
                    plannedEndTime="16:00", plannedDurationHours=7, title="Out of range",
                ),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 409
        assert "outside_cohort_date_range" in exc.value.detail["reasons"]

    def test_tutor_cannot_force_a_conflict_even_with_a_reason(
        self, request_factory, tutor_factory, cohort_factory, attendance_session_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", planned_start_time="09:00",
            created_by=tutor["userId"],
        )

        with pytest.raises(HTTPException) as exc:
            create_attendance_session(
                AttendanceSessionInput(
                    cohortId=cohort["id"], sessionDate="2026-02-01", plannedStartTime="09:00",
                    plannedEndTime="16:00", plannedDurationHours=7, title="Session 2",
                    force=True, overrideReason="Needed a second session",
                ),
                request_factory(), tutor["session"],
            )
        assert exc.value.status_code == 403

    def test_admin_force_without_a_reason_is_rejected(
        self, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", planned_start_time="09:00",
            created_by=admin_user["userId"],
        )

        with pytest.raises(HTTPException) as exc:
            create_attendance_session(
                AttendanceSessionInput(
                    cohortId=cohort["id"], sessionDate="2026-02-01", plannedStartTime="09:00",
                    plannedEndTime="16:00", plannedDurationHours=7, title="Session 2", force=True,
                ),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 400

    def test_admin_force_with_a_reason_succeeds_and_is_audited(
        self, db, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", planned_start_time="09:00",
            created_by=admin_user["userId"],
        )

        result = create_attendance_session(
            AttendanceSessionInput(
                cohortId=cohort["id"], sessionDate="2026-02-01", plannedStartTime="09:00",
                plannedEndTime="16:00", plannedDurationHours=7, title="Catch-up session",
                force=True, overrideReason="Approved catch-up session for absent group",
            ),
            request_factory(), admin_user,
        )
        assert result["overrideReason"] == "Approved catch-up session for absent group"

        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s AND action = 'duplicate_override'",
            (result["id"],),
        )
        assert db.fetchone() is not None


class TestSessionCancellation:
    def test_reason_is_required(self):
        with pytest.raises(pydantic.ValidationError):
            SessionCancelInput(reason="")

    def test_cancelling_marks_session_cancelled_and_preserves_it(
        self, db, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        result = cancel_attendance_session(
            session["id"], SessionCancelInput(reason="Tutor unavailable"), request_factory(), admin_user,
        )
        assert result["status"] == "cancelled"
        assert result["cancellationReason"] == "Tutor unavailable"
        assert result["registerStatus"] == "cancelled"

        db.execute("SELECT id FROM attendance_sessions WHERE id = %s", (session["id"],))
        assert db.fetchone() is not None

    def test_cancelling_with_recorded_attendance_requires_confirmation_and_preserves_it(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)]),
            request_factory(), admin_user,
        )

        with pytest.raises(HTTPException) as exc:
            cancel_attendance_session(session["id"], SessionCancelInput(reason="Weather"), request_factory(), admin_user)
        assert exc.value.status_code == 409

        cancel_attendance_session(
            session["id"], SessionCancelInput(reason="Weather", confirmWithAttendance=True), request_factory(), admin_user,
        )

        db.execute(
            "SELECT status FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (session["id"], learner["id"]),
        )
        assert db.fetchone()["status"] == "present"

    def test_cancelled_session_cannot_accept_attendance(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        cancel_attendance_session(session["id"], SessionCancelInput(reason="Cancelled"), request_factory(), admin_user)

        with pytest.raises(HTTPException) as exc:
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)]),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 409

    def test_cancelled_session_cannot_be_edited(
        self, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        cancel_attendance_session(session["id"], SessionCancelInput(reason="Cancelled"), request_factory(), admin_user)

        with pytest.raises(HTTPException) as exc:
            update_attendance_session(session["id"], AttendanceSessionUpdate(title="New title"), request_factory(), admin_user)
        assert exc.value.status_code == 409

    def test_cancelling_an_already_cancelled_session_is_rejected(
        self, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        cancel_attendance_session(session["id"], SessionCancelInput(reason="First"), request_factory(), admin_user)

        with pytest.raises(HTTPException) as exc:
            cancel_attendance_session(session["id"], SessionCancelInput(reason="Second"), request_factory(), admin_user)
        assert exc.value.status_code == 400


class TestSessionEditConfirmation:
    def test_changing_date_without_attendance_needs_no_confirmation(
        self, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-02-01", created_by=admin_user["userId"])

        result = update_attendance_session(
            session["id"], AttendanceSessionUpdate(sessionDate="2026-02-08"), request_factory(), admin_user,
        )
        assert str(result["sessionDate"]) == "2026-02-08"

    def test_changing_date_with_recorded_attendance_requires_confirmation(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-02-01", created_by=admin_user["userId"])
        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)]),
            request_factory(), admin_user,
        )

        with pytest.raises(HTTPException) as exc:
            update_attendance_session(session["id"], AttendanceSessionUpdate(sessionDate="2026-02-08"), request_factory(), admin_user)
        assert exc.value.status_code == 409

        result = update_attendance_session(
            session["id"], AttendanceSessionUpdate(sessionDate="2026-02-08", confirmChange=True), request_factory(), admin_user,
        )
        assert str(result["sessionDate"]) == "2026-02-08"

    def test_changing_title_only_with_attendance_needs_no_confirmation(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)]),
            request_factory(), admin_user,
        )

        result = update_attendance_session(session["id"], AttendanceSessionUpdate(title="Renamed"), request_factory(), admin_user)
        assert result["title"] == "Renamed"


class TestRegisterRefreshEndpoint:
    def test_dry_run_shows_diff_without_applying(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        stays = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        future_date = datetime.date.today() + datetime.timedelta(days=7)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"])
        get_attendance_session(session["id"], admin_user)

        joins = learner_factory(cohort_id=cohort["id"], start_date=str(datetime.date.today()))

        diff = refresh_session_register(session["id"], RefreshRegisterInput(confirm=False), request_factory(), admin_user)
        assert {learner["learnerId"] for learner in diff["toAdd"]} == {joins["id"]}

        expected = get_session_expected_learners(session["id"], admin_user)
        assert {learner["learnerId"] for learner in expected} == {stays["id"]}

    def test_confirm_applies_the_diff_and_is_audited(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        future_date = datetime.date.today() + datetime.timedelta(days=7)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"])
        get_attendance_session(session["id"], admin_user)

        joins = learner_factory(cohort_id=cohort["id"], start_date=str(datetime.date.today()))

        result = refresh_session_register(session["id"], RefreshRegisterInput(confirm=True), request_factory(), admin_user)
        assert {learner["learnerId"] for learner in result["added"]} == {joins["id"]}

        expected = get_session_expected_learners(session["id"], admin_user)
        assert joins["id"] in {learner["learnerId"] for learner in expected}

        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s AND action = 'refresh_register'",
            (session["id"],),
        )
        assert db.fetchone() is not None

    def test_historical_session_cannot_be_refreshed(
        self, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        past_date = datetime.date.today() - datetime.timedelta(days=1)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=past_date, created_by=admin_user["userId"])

        with pytest.raises(HTTPException) as exc:
            refresh_session_register(session["id"], RefreshRegisterInput(confirm=False), request_factory(), admin_user)
        assert exc.value.status_code == 400

    def test_cancelled_session_cannot_be_refreshed(
        self, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        future_date = datetime.date.today() + datetime.timedelta(days=7)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"])
        cancel_attendance_session(session["id"], SessionCancelInput(reason="Cancelled"), request_factory(), admin_user)

        with pytest.raises(HTTPException) as exc:
            refresh_session_register(session["id"], RefreshRegisterInput(confirm=False), request_factory(), admin_user)
        assert exc.value.status_code == 400

    def test_completed_register_cannot_be_refreshed(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        future_date = datetime.date.today() + datetime.timedelta(days=7)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"])
        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)]),
            request_factory(), admin_user,
        )

        with pytest.raises(HTTPException) as exc:
            refresh_session_register(session["id"], RefreshRegisterInput(confirm=False), request_factory(), admin_user)
        assert exc.value.status_code == 400

    def test_learner_with_recorded_attendance_is_blocked_not_removed(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        marked = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")  # keeps the register from reading as "completed"
        future_date = datetime.date.today() + datetime.timedelta(days=7)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"])
        get_attendance_session(session["id"], admin_user)
        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(entries=[RegisterEntryInput(learnerId=marked["id"], status="present", hoursAttended=7, minutesLate=0)]),
            request_factory(), admin_user,
        )
        db.execute(
            "UPDATE learners SET status = 'withdrawn', withdrawal_date = %s WHERE id = %s",
            (str(datetime.date.today()), marked["id"]),
        )

        diff = refresh_session_register(session["id"], RefreshRegisterInput(confirm=False), request_factory(), admin_user)
        assert {learner["learnerId"] for learner in diff["blocked"]} == {marked["id"]}
        assert diff["toRemove"] == []

        expected_after = get_session_expected_learners(session["id"], admin_user)
        assert marked["id"] in {learner["learnerId"] for learner in expected_after}


class TestNewEndpointPermissions:
    def test_tutor_cannot_cancel_a_session_over_http(
        self, client, monkeypatch, admin_user, tutor_factory, cohort_factory, attendance_session_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        _as_tutor(monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.post(f"/api/attendance/sessions/{session['id']}/cancel", json={"reason": "Test"})
        assert response.status_code == 403

    def test_tutor_cannot_refresh_a_register_over_http(
        self, client, monkeypatch, admin_user, tutor_factory, cohort_factory, attendance_session_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        future_date = datetime.date.today() + datetime.timedelta(days=7)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"])

        _as_tutor(monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.post(f"/api/attendance/sessions/{session['id']}/refresh-register", json={"confirm": False})
        assert response.status_code == 403

    def test_admin_can_cancel_a_session_over_http(
        self, client, monkeypatch, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        _as_admin(monkeypatch)
        response = client.post(f"/api/attendance/sessions/{session['id']}/cancel", json={"reason": "Test"})
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    def test_tutor_cannot_view_another_tutors_expected_learners(
        self, admin_user, tutor_factory, cohort_factory, attendance_session_factory,
    ):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        with pytest.raises(HTTPException) as exc:
            get_session_expected_learners(session["id"], other["session"])
        assert exc.value.status_code == 403

    def test_nonexistent_session_id_is_404_for_cancel(self, client, monkeypatch):
        _as_admin(monkeypatch)
        response = client.post("/api/attendance/sessions/999999999/cancel", json={"reason": "Test"})
        assert response.status_code == 404

    def test_nonexistent_session_id_is_404_for_refresh(self, client, monkeypatch):
        _as_admin(monkeypatch)
        response = client.post("/api/attendance/sessions/999999999/refresh-register", json={"confirm": False})
        assert response.status_code == 404


class TestBackdatedAllocationCorrectionRegression:
    """The one gap live-dynamic eligibility resolution doesn't cover: an
    admin backdating a correction to learner_allocation_history after a
    session's register was already generated. The frozen snapshot must not
    silently change, even though a live-dynamic lookup for the same date
    now disagrees."""

    def test_backdated_correction_does_not_change_an_already_generated_register(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort_a = cohort_factory()
        cohort_b = cohort_factory()
        learner = learner_factory(cohort_id=cohort_a["id"], start_date="2026-01-01")

        session_date = datetime.date(2026, 3, 1)
        session = attendance_session_factory(cohort_id=cohort_a["id"], session_date=session_date, created_by=admin_user["userId"])

        # Generates + freezes the snapshot: learner is in cohort_a on this date.
        view = get_attendance_session(session["id"], admin_user)
        assert view["session"]["expectedCount"] == 1
        assert [e["learnerId"] for e in view["entries"]] == [learner["id"]]

        # A genuine, forward-dated transfer -- the normal case.
        allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], cohortId=cohort_b["id"], effectiveDate=datetime.date.today()),
            request_factory(), admin_user,
        )

        # An admin later backdates a *correction*: the learner was actually
        # already in cohort_b before the frozen session's date. A live
        # dynamic lookup would now flip cohort_a's session to 0 expected --
        # the frozen snapshot must not.
        allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], cohortId=cohort_b["id"], effectiveDate=datetime.date(2026, 2, 1)),
            request_factory(), admin_user,
        )

        live_result = learners_expected_in_cohort_as_of(db, cohort_a["id"], session_date)
        assert live_result == [], "test setup didn't actually create the discrepancy this test is meant to guard against"

        frozen_view = get_attendance_session(session["id"], admin_user)
        assert frozen_view["session"]["expectedCount"] == 1
        assert [e["learnerId"] for e in frozen_view["entries"]] == [learner["id"]]


class TestLazySnapshotGenerationForPreExistingSessions:
    """Simulates a session created before this feature shipped
    (register_generated_at IS NULL, no snapshot rows) -- proves the first
    read after deploy transparently generates it, with no backfill script
    needed."""

    def test_first_read_after_deploy_generates_the_snapshot(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-02-01", created_by=admin_user["userId"])

        db.execute("SELECT register_generated_at FROM attendance_sessions WHERE id = %s", (session["id"],))
        assert db.fetchone()["register_generated_at"] is None

        view = get_attendance_session(session["id"], admin_user)
        assert view["session"]["expectedCount"] == 1
        assert [e["learnerId"] for e in view["entries"]] == [learner["id"]]

        db.execute("SELECT register_generated_at FROM attendance_sessions WHERE id = %s", (session["id"],))
        assert db.fetchone()["register_generated_at"] is not None
