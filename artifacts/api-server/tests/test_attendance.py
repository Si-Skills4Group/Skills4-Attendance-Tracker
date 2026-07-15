import datetime

import pytest
from fastapi import HTTPException

from pyapp.routers.allocation_routes import AllocationInput, allocate_learners
from pyapp.routers.attendance import (
    AttendanceRegisterInput,
    RegisterEntryInput,
    get_attendance_session,
    list_attendance_sessions,
    mark_all_present,
    save_attendance_register,
)


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
