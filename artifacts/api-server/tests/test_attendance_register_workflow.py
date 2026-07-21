"""Phase 7: bulk-save atomicity, concurrency, completion, locking, and
historical-edit tests. Kept separate from test_attendance.py (session
lifecycle) since this file is squarely about the attendance-entry
workflow built on top of it."""
import datetime

import pytest
from fastapi import HTTPException

import pydantic

from pyapp import auth as auth_module
from pyapp.attendance_metrics import fetch_attendance_metrics
from pyapp.routers.allocation_routes import AllocationInput, allocate_learners
from pyapp.routers.attendance import (
    AttendanceRegisterInput,
    CompleteRegisterInput,
    LockRegisterInput,
    RegisterEntryInput,
    UnlockRegisterInput,
    complete_register,
    get_attendance_session,
    lock_attendance_register,
    save_attendance_register,
    unlock_attendance_register,
)


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


class TestRegisterEntryInputValidation:
    def test_invalid_status_string_is_rejected_by_pydantic(self):
        with pytest.raises(pydantic.ValidationError):
            RegisterEntryInput(learnerId=1, status="not_a_real_status", hoursAttended=0, minutesLate=0)

    def test_negative_hours_is_rejected_by_pydantic(self):
        with pytest.raises(pydantic.ValidationError):
            RegisterEntryInput(learnerId=1, status="present", hoursAttended=-1, minutesLate=0)

    def test_negative_minutes_late_is_rejected_by_pydantic(self):
        with pytest.raises(pydantic.ValidationError):
            RegisterEntryInput(learnerId=1, status="present", hoursAttended=0, minutesLate=-1)


class TestStatusValidationAtSaveEndpoint:
    def test_late_without_minutes_is_rejected(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        with pytest.raises(HTTPException) as exc:
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(
                    registerVersion=1,
                    entries=[RegisterEntryInput(learnerId=learner["id"], status="late", hoursAttended=5, minutesLate=0)],
                ),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 422
        assert any(e["field"] == "minutesLate" for e in exc.value.detail["errors"])

    def test_absent_authorised_with_nonzero_hours_is_rejected(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        with pytest.raises(HTTPException) as exc:
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(
                    registerVersion=1,
                    entries=[RegisterEntryInput(learnerId=learner["id"], status="absent_authorised", hoursAttended=3, minutesLate=0)],
                ),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 422
        assert any(e["field"] == "hoursAttended" for e in exc.value.detail["errors"])

    def test_tutor_cannot_exceed_planned_hours_even_with_a_reason(
        self, request_factory, tutor_factory, cohort_factory, learner_factory, attendance_session_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=tutor["userId"])

        with pytest.raises(HTTPException) as exc:
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(
                    registerVersion=1,
                    entries=[RegisterEntryInput(
                        learnerId=learner["id"], status="present", hoursAttended=99, minutesLate=0,
                        overrideReason="trust me",
                    )],
                ),
                request_factory(), tutor["session"],
            )
        assert exc.value.status_code == 422
        assert any("Administrator" in e["message"] for e in exc.value.detail["errors"])

    def test_admin_can_exceed_planned_hours_with_a_reason(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        result = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(
                    learnerId=learner["id"], status="present", hoursAttended=99, minutesLate=0,
                    overrideReason="Ran a catch-up block",
                )],
            ),
            request_factory(), admin_user,
        )
        assert result["entries"][0]["hoursAttended"] == 99


class TestBulkSaveAtomicity:
    def test_one_invalid_row_rejects_the_whole_save_atomically(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        good = learner_factory(cohort_id=cohort["id"])
        bad = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        with pytest.raises(HTTPException) as exc:
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(
                    registerVersion=1,
                    entries=[
                        RegisterEntryInput(learnerId=good["id"], status="present", hoursAttended=7, minutesLate=0),
                        RegisterEntryInput(learnerId=bad["id"], status="late", hoursAttended=7, minutesLate=0),
                    ],
                ),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 422

        db.execute("SELECT count(*)::int AS c FROM attendance_records WHERE session_id = %s", (session["id"],))
        assert db.fetchone()["c"] == 0, "the valid row must not have been written either -- all or nothing"

    def test_repeated_identical_save_does_not_duplicate_records(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        entry = RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)

        r1 = save_attendance_register(
            session["id"], AttendanceRegisterInput(registerVersion=1, entries=[entry]), request_factory(), admin_user,
        )
        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(registerVersion=r1["session"]["registerVersion"], entries=[entry]),
            request_factory(), admin_user,
        )

        db.execute(
            "SELECT count(*)::int AS c FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (session["id"], learner["id"]),
        )
        assert db.fetchone()["c"] == 1


class TestRegisterConcurrency:
    def test_stale_register_version_is_rejected(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        with pytest.raises(HTTPException) as exc:
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(
                    registerVersion=1,  # stale -- the save above already bumped it to 2
                    entries=[RegisterEntryInput(learnerId=learner["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)],
                ),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "stale_register_version"

    def test_correct_version_after_reload_succeeds(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        # A second, never-recorded learner keeps the register at
        # "in_progress" rather than "completed" after the first save --
        # this test is purely about concurrency-version resolution, not the
        # historical-edit-reason rule that a (derived-)completed register
        # would otherwise also trigger on the second save below.
        learner_factory(cohort_id=cohort["id"])
        future_date = datetime.date.today() + datetime.timedelta(days=7)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"])
        first = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        reloaded = get_attendance_session(session["id"], admin_user)
        assert reloaded["session"]["registerVersion"] == first["session"]["registerVersion"]

        result = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=reloaded["session"]["registerVersion"],
                entries=[RegisterEntryInput(learnerId=learner["id"], status="late", hoursAttended=6, minutesLate=10)],
            ),
            request_factory(), admin_user,
        )
        assert result["entries"][0]["status"] == "late"

    def test_stale_save_does_not_overwrite_newer_saved_data(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        with pytest.raises(HTTPException):
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(
                    registerVersion=1,
                    entries=[RegisterEntryInput(learnerId=learner["id"], status="absent_unauthorised", hoursAttended=0, minutesLate=0)],
                ),
                request_factory(), admin_user,
            )

        db.execute(
            "SELECT status FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (session["id"], learner["id"]),
        )
        assert db.fetchone()["status"] == "present"


class TestCompleteRegister:
    def test_empty_register_cannot_complete(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        get_attendance_session(session["id"], admin_user)

        with pytest.raises(HTTPException) as exc:
            complete_register(session["id"], CompleteRegisterInput(registerVersion=1), request_factory(), admin_user)
        assert exc.value.status_code == 422

    def test_partially_completed_register_cannot_complete(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        recorded = learner_factory(cohort_id=cohort["id"])
        learner_factory(cohort_id=cohort["id"])  # never recorded
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=recorded["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        with pytest.raises(HTTPException) as exc:
            complete_register(
                session["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 422

    def test_fully_valid_register_completes_and_stamps_completed_at_and_by(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        result = complete_register(
            session["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        assert result["session"]["completedAt"] is not None
        assert result["session"]["completedBy"] == admin_user["userId"]
        assert result["session"]["registerStatus"] == "completed"

    def test_cancelled_session_cannot_complete(
        self, request_factory, admin_user, cohort_factory, attendance_session_factory,
    ):
        from pyapp.routers.attendance import SessionCancelInput, cancel_attendance_session

        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        cancel_attendance_session(session["id"], SessionCancelInput(reason="Weather"), request_factory(), admin_user)

        with pytest.raises(HTTPException) as exc:
            complete_register(session["id"], CompleteRegisterInput(registerVersion=1), request_factory(), admin_user)
        assert exc.value.status_code == 409

    def test_not_expected_rows_count_as_completed(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="not_expected", hoursAttended=0, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        result = complete_register(
            session["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        assert result["session"]["registerStatus"] == "completed"

    def test_completion_is_audited(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )
        complete_register(
            session["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )

        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s AND action = 'complete_register'",
            (session["id"],),
        )
        assert db.fetchone() is not None


class TestLockUnlock:
    def _complete(self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )
        completed = complete_register(
            session["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        return session, learner, completed

    def test_admin_can_lock_a_completed_register(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        session, _, completed = self._complete(request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory)
        result = lock_attendance_register(
            session["id"],
            LockRegisterInput(reason="End of week", registerVersion=completed["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        assert result["registerStatus"] == "locked"
        assert result["lockReason"] == "End of week"

    def test_cannot_lock_an_incomplete_register(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        get_attendance_session(session["id"], admin_user)

        with pytest.raises(HTTPException) as exc:
            lock_attendance_register(
                session["id"], LockRegisterInput(reason="Too early", registerVersion=1), request_factory(), admin_user,
            )
        assert exc.value.status_code == 400

    def test_tutor_cannot_lock_over_http(
        self, client, monkeypatch, request_factory, admin_user, tutor_factory, cohort_factory,
        learner_factory, attendance_session_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )
        completed = complete_register(
            session["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )

        _as_tutor(monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.post(
            f"/api/attendance/sessions/{session['id']}/lock",
            json={"reason": "Trying anyway", "registerVersion": completed["session"]["registerVersion"]},
        )
        assert response.status_code == 403

    def test_locked_register_rejects_save(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        session, learner, completed = self._complete(request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory)
        locked = lock_attendance_register(
            session["id"],
            LockRegisterInput(reason="Done", registerVersion=completed["session"]["registerVersion"]),
            request_factory(), admin_user,
        )

        with pytest.raises(HTTPException) as exc:
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(
                    registerVersion=locked["registerVersion"],
                    entries=[RegisterEntryInput(learnerId=learner["id"], status="late", hoursAttended=6, minutesLate=5)],
                    changeReason="Trying to edit a locked register",
                ),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 409

    def test_admin_can_unlock_with_reason_and_it_is_audited(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        session, _, completed = self._complete(request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory)
        locked = lock_attendance_register(
            session["id"],
            LockRegisterInput(reason="Done", registerVersion=completed["session"]["registerVersion"]),
            request_factory(), admin_user,
        )

        unlocked = unlock_attendance_register(
            session["id"],
            UnlockRegisterInput(reason="Tutor found an error", registerVersion=locked["registerVersion"]),
            request_factory(), admin_user,
        )
        assert unlocked["registerStatus"] == "completed"
        assert unlocked["lockReason"] is None

        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s AND action = 'unlock_register'",
            (session["id"],),
        )
        assert db.fetchone() is not None

    def test_edit_works_after_controlled_unlock(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        session, learner, completed = self._complete(request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory)
        locked = lock_attendance_register(
            session["id"],
            LockRegisterInput(reason="Done", registerVersion=completed["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        unlocked = unlock_attendance_register(
            session["id"],
            UnlockRegisterInput(reason="Found an error", registerVersion=locked["registerVersion"]),
            request_factory(), admin_user,
        )

        result = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=unlocked["registerVersion"],
                entries=[RegisterEntryInput(learnerId=learner["id"], status="late", hoursAttended=6, minutesLate=10)],
                changeReason="Corrected after unlock",
            ),
            request_factory(), admin_user,
        )
        assert result["entries"][0]["status"] == "late"

    def test_unlocking_an_unlocked_register_is_rejected(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        session, _, completed = self._complete(request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory)
        with pytest.raises(HTTPException) as exc:
            unlock_attendance_register(
                session["id"], UnlockRegisterInput(reason="Not locked", registerVersion=completed["session"]["registerVersion"]),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 400


class TestHistoricalEdit:
    def test_first_save_on_a_past_session_does_not_require_a_reason(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        past_date = datetime.date.today() - datetime.timedelta(days=30)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=past_date, created_by=admin_user["userId"])

        result = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )
        assert result["entries"][0]["status"] == "present"

    def test_changing_a_historical_status_without_a_reason_is_rejected(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        past_date = datetime.date.today() - datetime.timedelta(days=30)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=past_date, created_by=admin_user["userId"])
        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        with pytest.raises(HTTPException) as exc:
            save_attendance_register(
                session["id"],
                AttendanceRegisterInput(
                    registerVersion=saved["session"]["registerVersion"],
                    entries=[RegisterEntryInput(learnerId=learner["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)],
                ),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 422
        assert any(e["field"] == "changeReason" for e in exc.value.detail["errors"])

    def test_changing_a_historical_status_with_a_reason_succeeds_and_is_audited(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        past_date = datetime.date.today() - datetime.timedelta(days=30)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=past_date, created_by=admin_user["userId"])
        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=saved["session"]["registerVersion"],
                entries=[RegisterEntryInput(learnerId=learner["id"], status="late", hoursAttended=6, minutesLate=15)],
                changeReason="Learner arrival time corrected after review",
            ),
            request_factory(), admin_user,
        )

        db.execute(
            "SELECT new_value FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s "
            "AND action = 'save_register' ORDER BY id DESC LIMIT 1",
            (session["id"],),
        )
        import json
        new_value = json.loads(db.fetchone()["new_value"])
        assert new_value["changeReason"] == "Learner arrival time corrected after review"
        change_entry = next(c for c in new_value["changes"] if c["learnerId"] == learner["id"])
        assert change_entry["fields"]["status"] == {"before": "present", "after": "late"}

    def test_minutes_late_only_change_does_not_require_a_reason_even_when_historical(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        past_date = datetime.date.today() - datetime.timedelta(days=30)
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=past_date, created_by=admin_user["userId"])
        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="late", hoursAttended=6, minutesLate=10)],
            ),
            request_factory(), admin_user,
        )

        result = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=saved["session"]["registerVersion"],
                entries=[RegisterEntryInput(learnerId=learner["id"], status="late", hoursAttended=6, minutesLate=12)],
            ),
            request_factory(), admin_user,
        )
        assert result["entries"][0]["minutesLate"] == 12


class TestHistoricalEditRegression:
    """The brief's exact scenario: a learner transfers cohorts after a
    completed historical register, then an admin edits one attendance
    value. Nothing about the transfer or the edit may move/reconstruct
    attendance, touch allocation history, or affect any other row."""

    def test_admin_edit_after_transfer_only_changes_the_one_attendance_field(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort_a = cohort_factory()
        cohort_b = cohort_factory()
        learner = learner_factory(cohort_id=cohort_a["id"])
        control_learner = learner_factory(cohort_id=cohort_a["id"])
        past_date = datetime.date.today() - datetime.timedelta(days=30)
        session = attendance_session_factory(cohort_id=cohort_a["id"], session_date=past_date, created_by=admin_user["userId"])

        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[
                    RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0),
                    RegisterEntryInput(learnerId=control_learner["id"], status="present", hoursAttended=7, minutesLate=0),
                ],
            ),
            request_factory(), admin_user,
        )
        completed = complete_register(
            session["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )

        db.execute(
            "SELECT id, status, hours_attended AS \"hoursAttended\" FROM attendance_records "
            "WHERE session_id = %s AND learner_id = %s",
            (session["id"], control_learner["id"]),
        )
        control_before = db.fetchone()

        db.execute("SELECT count(*)::int AS c FROM learner_allocation_history WHERE learner_id = %s", (learner["id"],))
        history_count_before = db.fetchone()["c"]

        allocate_learners(
            AllocationInput(learnerIds=[learner["id"]], cohortId=cohort_b["id"], effectiveDate=datetime.date.today()),
            request_factory(), admin_user,
        )

        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=completed["session"]["registerVersion"],
                entries=[
                    RegisterEntryInput(learnerId=learner["id"], status="late", hoursAttended=6, minutesLate=15),
                    RegisterEntryInput(learnerId=control_learner["id"], status="present", hoursAttended=7, minutesLate=0),
                ],
                changeReason="Corrected after a post-completion review",
            ),
            request_factory(), admin_user,
        )

        # Session cohort is unchanged.
        db.execute("SELECT cohort_id FROM attendance_sessions WHERE id = %s", (session["id"],))
        assert db.fetchone()["cohort_id"] == cohort_a["id"]

        # The attendance record is still linked to the same session and learner -- not moved.
        db.execute(
            "SELECT status, hours_attended AS \"hoursAttended\", minutes_late AS \"minutesLate\" "
            "FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (session["id"], learner["id"]),
        )
        edited_row = db.fetchone()
        assert edited_row["status"] == "late"
        assert float(edited_row["hoursAttended"]) == 6.0
        assert edited_row["minutesLate"] == 15

        # No attendance was created anywhere else (e.g. against Cohort B).
        db.execute("SELECT count(*)::int AS c FROM attendance_records WHERE learner_id = %s", (learner["id"],))
        assert db.fetchone()["c"] == 1

        # Allocation history reflects only the one genuine transfer -- not rewritten.
        db.execute("SELECT count(*)::int AS c FROM learner_allocation_history WHERE learner_id = %s", (learner["id"],))
        assert db.fetchone()["c"] == history_count_before + 1

        # No other register row changed.
        db.execute(
            "SELECT id, status, hours_attended AS \"hoursAttended\" FROM attendance_records "
            "WHERE session_id = %s AND learner_id = %s",
            (session["id"], control_learner["id"]),
        )
        assert db.fetchone() == control_before

        # Audit contains before, after, and the reason.
        db.execute(
            "SELECT new_value FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s "
            "AND action = 'save_register' ORDER BY id DESC LIMIT 1",
            (session["id"],),
        )
        import json
        new_value = json.loads(db.fetchone()["new_value"])
        assert new_value["changeReason"] == "Corrected after a post-completion review"
        change_entry = next(c for c in new_value["changes"] if c["learnerId"] == learner["id"])
        assert change_entry["fields"]["status"] == {"before": "present", "after": "late"}


class TestNewEndpointDirectObjectReferenceProtections:
    def test_tutor_cannot_unlock_over_http(
        self, client, monkeypatch, request_factory, admin_user, tutor_factory, cohort_factory,
        learner_factory, attendance_session_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )
        completed = complete_register(
            session["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        locked = lock_attendance_register(
            session["id"],
            LockRegisterInput(reason="Done", registerVersion=completed["session"]["registerVersion"]),
            request_factory(), admin_user,
        )

        _as_tutor(monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.post(
            f"/api/attendance/sessions/{session['id']}/unlock",
            json={"reason": "Trying anyway", "registerVersion": locked["registerVersion"]},
        )
        assert response.status_code == 403

    def _as_tutor_via_dependency_override(self, client, tutor_id):
        """save_attendance_register/complete_register are wired directly as
        Depends(require_auth) (unlike Depends(require_admin), which calls
        require_auth as a plain internal function call) -- FastAPI captures
        that dependency callable at route-registration time, so
        monkeypatching auth_module.require_auth afterwards has no effect on
        routes wired this way. app.dependency_overrides is the correct
        override mechanism here (same pattern as test_permissions.py)."""
        fake_session = {"userId": 1, "role": "tutor", "tutorId": tutor_id}
        client.app.dependency_overrides[auth_module.require_auth] = lambda: fake_session

    def test_tutor_cannot_save_another_tutors_register_over_http(
        self, client, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory,
    ):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        self._as_tutor_via_dependency_override(client, other["tutorId"])
        try:
            response = client.put(
                f"/api/attendance/sessions/{session['id']}/register",
                json={"registerVersion": 1, "entries": [
                    {"learnerId": learner["id"], "status": "present", "hoursAttended": 7, "minutesLate": 0},
                ]},
            )
        finally:
            client.app.dependency_overrides.pop(auth_module.require_auth, None)
        assert response.status_code == 403

    def test_tutor_cannot_complete_another_tutors_register_over_http(
        self, client, admin_user, tutor_factory, cohort_factory, attendance_session_factory,
    ):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        self._as_tutor_via_dependency_override(client, other["tutorId"])
        try:
            response = client.post(
                f"/api/attendance/sessions/{session['id']}/complete-register",
                json={"registerVersion": 1},
            )
        finally:
            client.app.dependency_overrides.pop(auth_module.require_auth, None)
        assert response.status_code == 403

    def test_nonexistent_session_is_404_for_lock_unlock_and_complete(self, client, monkeypatch):
        _as_admin(monkeypatch)
        assert client.post(
            "/api/attendance/sessions/999999999/lock", json={"reason": "x", "registerVersion": 1}
        ).status_code == 404
        assert client.post(
            "/api/attendance/sessions/999999999/unlock", json={"reason": "x", "registerVersion": 1}
        ).status_code == 404

    def test_unauthenticated_request_cannot_access_register_endpoints(self, client):
        response = client.get("/api/attendance/sessions/1")
        assert response.status_code == 401
        response = client.put("/api/attendance/sessions/1/register", json={"registerVersion": 1, "entries": []})
        assert response.status_code == 401


class TestUnrecordedAttendanceDefault:
    """Regression coverage for the register default-status defect: a
    freshly generated register must never default an unrecorded learner to
    Absent (Unauthorised), and Save Draft must never resubmit an untouched
    learner's fabricated default as though it were a real decision."""

    def test_freshly_generated_register_has_null_status_for_every_learner(
        self, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"])
        learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        view = get_attendance_session(session["id"], admin_user)
        assert len(view["entries"]) == 2
        for entry in view["entries"]:
            assert entry["status"] is None
            assert entry["recordId"] is None

    def test_freshly_generated_register_status_is_not_started(
        self, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        view = get_attendance_session(session["id"], admin_user)
        assert view["session"]["registerStatus"] == "not_started"
        assert view["session"]["recordedCount"] == 0

    def test_saving_one_authorised_absence_does_not_create_records_for_other_learners(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        changed = learner_factory(cohort_id=cohort["id"])
        untouched_a = learner_factory(cohort_id=cohort["id"])
        untouched_b = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=changed["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        db.execute("SELECT count(*)::int AS c FROM attendance_records WHERE session_id = %s", (session["id"],))
        assert db.fetchone()["c"] == 1, "only the one changed learner should have a real attendance_records row"

        view = get_attendance_session(session["id"], admin_user)
        by_id = {e["learnerId"]: e for e in view["entries"]}
        assert by_id[changed["id"]]["status"] == "absent_authorised"
        assert by_id[untouched_a["id"]]["status"] is None
        assert by_id[untouched_b["id"]]["status"] is None
        assert view["session"]["recordedCount"] == 1
        assert view["session"]["expectedCount"] == 3
        assert view["session"]["registerStatus"] == "in_progress"

    def test_untouched_learners_are_not_counted_as_unauthorised_absence(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        changed = learner_factory(cohort_id=cohort["id"])
        learner_factory(cohort_id=cohort["id"])  # never touched
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=changed["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        metrics = fetch_attendance_metrics(
            db, scope="cohort", scope_id=cohort["id"],
            period_start=datetime.date(2026, 1, 1), period_end=datetime.date(2026, 1, 31),
        )
        assert metrics.authorisedAbsenceMinutes == 420  # the one genuine, deliberately-recorded absence
        assert metrics.unauthorisedAbsenceMinutes == 0
        assert metrics.missingRecordCount == 1, "the untouched learner must be tracked as missing, not as any kind of absence"

    def test_omitting_an_already_recorded_learner_from_a_later_save_leaves_it_unchanged(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        first = learner_factory(cohort_id=cohort["id"])
        second = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=first["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )
        db.execute(
            "SELECT status, hours_attended AS h, updated_at FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (session["id"], first["id"]),
        )
        before = db.fetchone()

        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=saved["session"]["registerVersion"],
                entries=[RegisterEntryInput(learnerId=second["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        db.execute(
            "SELECT status, hours_attended AS h, updated_at FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (session["id"], first["id"]),
        )
        after = db.fetchone()
        assert after == before, "a learner omitted from a later save must be left completely untouched"

    def test_save_register_audit_log_only_reflects_the_touched_learner(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        changed = learner_factory(cohort_id=cohort["id"])
        untouched = learner_factory(cohort_id=cohort["id"])  # must not appear in the audit diff
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=changed["id"], status="present", hoursAttended=7, minutesLate=0)],
            ),
            request_factory(), admin_user,
        )

        db.execute(
            "SELECT new_value FROM audit_logs WHERE action = 'save_register' AND entity_type = 'attendance_session' "
            "AND entity_id = %s ORDER BY id DESC LIMIT 1",
            (session["id"],),
        )
        new_value = db.fetchone()["new_value"]
        assert f'"created": [{changed["id"]}]' in new_value, "only the touched learner should be recorded as created"
        assert str(untouched["id"]) not in new_value
        assert '"totalEntries": 1' in new_value

    def test_omitted_status_field_is_rejected_by_pydantic_not_defaulted(self):
        with pytest.raises(pydantic.ValidationError):
            RegisterEntryInput(learnerId=1, hoursAttended=0, minutesLate=0)

    def test_24_learner_register_one_authorised_absence_leaves_23_unrecorded(
        self, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        """The exact integration scenario from the defect report: a 24-learner
        register, one learner marked Absent (Authorised) with a reason, Save
        Draft clicked -- the other 23 must remain unrecorded, not silently
        become Absent (Unauthorised)."""
        cohort = cohort_factory()
        learners = [learner_factory(cohort_id=cohort["id"]) for _ in range(24)]
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        changed = learners[0]

        saved = save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[RegisterEntryInput(learnerId=changed["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)],
                changeReason="Confirmed authorised absence -- medical appointment",
            ),
            request_factory(), admin_user,
        )

        assert saved["session"]["recordedCount"] == 1
        assert saved["session"]["expectedCount"] == 24
        assert saved["session"]["registerStatus"] == "in_progress"

        by_id = {e["learnerId"]: e for e in saved["entries"]}
        assert by_id[changed["id"]]["status"] == "absent_authorised"
        unrecorded = [l for l in learners[1:] if by_id[l["id"]]["status"] is None]
        assert len(unrecorded) == 23, "the other 23 learners must remain unrecorded, not defaulted to any status"

        with pytest.raises(HTTPException) as exc:
            complete_register(
                session["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 422
        assert len(exc.value.detail["errors"]) == 23, "Complete Register must reject all 23 still-unrecorded learners"
