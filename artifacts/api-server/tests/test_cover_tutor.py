"""Session-level cover tutor reassignment: an Administrator hands ONE
attendance session's register to a different tutor without touching the
cohort's own tutor, learner allocations, or historical attendance
authorship. Kept separate from test_attendance_register_workflow.py since
this is squarely about the cover-tutor feature built on top of it."""
import json
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from pyapp import auth as auth_module
from pyapp.attendance_metrics import fetch_attendance_metrics_grouped
from pyapp.routers.attendance import (
    AttendanceRegisterInput,
    CompleteRegisterInput,
    CoverTutorInput,
    LockRegisterInput,
    RefreshRegisterInput,
    RegisterEntryInput,
    RemoveCoverTutorInput,
    SessionCancelInput,
    SessionDeleteInput,
    assign_cover_tutor,
    cancel_attendance_session,
    complete_register,
    delete_attendance_session,
    get_attendance_session,
    list_attendance_sessions,
    lock_attendance_register,
    refresh_session_register,
    remove_cover_tutor_endpoint,
    save_attendance_register,
)
from pyapp.routers.cohorts import list_cohorts
from pyapp.routers.dashboard import get_tutor_dashboard_cohorts
from pyapp.routers.learners import get_learner


def _as_tutor(monkeypatch, tutor_id, user_id):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}

    def fake_require_auth(request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)


def _record_one_present(request_factory, session_id, learner_id, acting_session, register_version=1):
    return save_attendance_register(
        session_id,
        AttendanceRegisterInput(
            registerVersion=register_version,
            entries=[RegisterEntryInput(learnerId=learner_id, status="present", hoursAttended=7, minutesLate=0)],
        ),
        request_factory(),
        acting_session,
    )


@pytest.fixture
def scenario(request_factory, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory):
    """A cohort with its own (original) tutor, one learner, one not-yet-
    started session, and a second, separate active tutor eligible to cover."""
    original = tutor_factory()
    cover = tutor_factory()
    cohort = cohort_factory(tutor_id=original["tutorId"])
    learner = learner_factory(cohort_id=cohort["id"])
    session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
    return {
        "original": original,
        "cover": cover,
        "cohort": cohort,
        "learner": learner,
        "session": session,
    }


class TestReassignment:
    def test_admin_can_assign_cover_tutor(self, request_factory, admin_user, scenario):
        result = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(),
            admin_user,
        )
        assert result["coverTutorId"] == scenario["cover"]["tutorId"]
        assert result["effectiveTutorId"] == scenario["cover"]["tutorId"]

    def test_ordinary_tutor_cannot_assign_cover(self, client, monkeypatch, scenario):
        _as_tutor(monkeypatch, scenario["original"]["tutorId"], scenario["original"]["userId"])
        response = client.put(
            f"/api/attendance/sessions/{scenario['session']['id']}/cover",
            json={"coverTutorId": scenario["cover"]["tutorId"], "reason": "tutor_sickness", "registerVersion": 1},
        )
        assert response.status_code == 403

    def test_replacement_tutor_must_be_active(self, request_factory, admin_user, scenario, tutor_factory):
        inactive = tutor_factory(active=False)
        with pytest.raises(HTTPException) as exc:
            assign_cover_tutor(
                scenario["session"]["id"],
                CoverTutorInput(coverTutorId=inactive["tutorId"], reason="tutor_sickness", registerVersion=1),
                request_factory(),
                admin_user,
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["reason"] == "cover_tutor_inactive"

    def test_replacement_tutor_cannot_be_same_as_original(self, request_factory, admin_user, scenario):
        with pytest.raises(HTTPException) as exc:
            assign_cover_tutor(
                scenario["session"]["id"],
                CoverTutorInput(coverTutorId=scenario["original"]["tutorId"], reason="tutor_sickness", registerVersion=1),
                request_factory(),
                admin_user,
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["reason"] == "cover_tutor_same_as_original"

    def test_original_and_replacement_tutor_are_stored_separately(self, request_factory, admin_user, scenario):
        result = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(),
            admin_user,
        )
        assert result["tutorId"] == scenario["original"]["tutorId"]
        assert result["coverTutorId"] == scenario["cover"]["tutorId"]
        assert result["coverOriginalTutorId"] == scenario["original"]["tutorId"]

    def test_reason_is_required_and_controlled(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            CoverTutorInput(coverTutorId=1, reason="made_up_reason", registerVersion=1)

    def test_other_reason_requires_notes(self, request_factory, admin_user, scenario):
        with pytest.raises(HTTPException) as exc:
            assign_cover_tutor(
                scenario["session"]["id"],
                CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="other", notes=None, registerVersion=1),
                request_factory(),
                admin_user,
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["reason"] == "other_reason_requires_notes"

    def test_other_reason_with_notes_succeeds(self, request_factory, admin_user, scenario):
        result = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="other", notes="Cover swap agreed with team", registerVersion=1),
            request_factory(),
            admin_user,
        )
        assert result["coverReason"] == "other"
        assert result["coverNotes"] == "Cover swap agreed with team"

    def test_reassignment_affects_only_one_session(
        self, request_factory, admin_user, scenario, attendance_session_factory
    ):
        other_session = attendance_session_factory(
            cohort_id=scenario["cohort"]["id"], session_date="2026-01-08", created_by=admin_user["userId"]
        )
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(),
            admin_user,
        )
        other_full = get_attendance_session(other_session["id"], admin_user)
        assert other_full["session"]["coverTutorId"] is None
        assert other_full["session"]["effectiveTutorId"] == scenario["original"]["tutorId"]

    def test_cohort_tutor_remains_unchanged(self, request_factory, admin_user, scenario, db):
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(),
            admin_user,
        )
        db.execute("SELECT tutor_id FROM cohorts WHERE id = %s", (scenario["cohort"]["id"],))
        assert db.fetchone()["tutor_id"] == scenario["original"]["tutorId"]

    def test_learner_allocations_remain_unchanged(self, request_factory, admin_user, scenario, db):
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(),
            admin_user,
        )
        db.execute("SELECT cohort_id, tutor_id FROM learners WHERE id = %s", (scenario["learner"]["id"],))
        row = db.fetchone()
        assert row["cohort_id"] == scenario["cohort"]["id"]
        assert row["tutor_id"] == scenario["learner"]["tutor_id"]

    def test_historical_attendance_remains_unchanged(self, request_factory, admin_user, scenario, db):
        _record_one_present(request_factory, scenario["session"]["id"], scenario["learner"]["id"], admin_user)
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=2),
            request_factory(),
            admin_user,
        )
        db.execute(
            "SELECT status, created_by FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (scenario["session"]["id"], scenario["learner"]["id"]),
        )
        row = db.fetchone()
        assert row["status"] == "present"
        assert row["created_by"] == admin_user["userId"]


class TestAccess:
    def _assign(self, request_factory, admin_user, scenario, register_version=1):
        return assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=register_version),
            request_factory(),
            admin_user,
        )

    def test_replacement_tutor_can_see_assigned_session_in_their_list(self, request_factory, admin_user, scenario):
        self._assign(request_factory, admin_user, scenario)
        cover_session = scenario["cover"]["session"]
        rows = list_attendance_sessions(session=cover_session)
        assert any(r["id"] == scenario["session"]["id"] for r in rows)

    def test_replacement_tutor_can_open_the_register(self, request_factory, admin_user, scenario):
        self._assign(request_factory, admin_user, scenario)
        full = get_attendance_session(scenario["session"]["id"], scenario["cover"]["session"])
        assert full["session"]["id"] == scenario["session"]["id"]

    def test_replacement_tutor_can_save_draft(self, request_factory, admin_user, scenario):
        assigned = self._assign(request_factory, admin_user, scenario)
        saved = _record_one_present(
            request_factory, scenario["session"]["id"], scenario["learner"]["id"], scenario["cover"]["session"],
            register_version=assigned["registerVersion"],
        )
        assert saved["session"]["recordedCount"] == 1

    def test_replacement_tutor_can_complete_register(self, request_factory, admin_user, scenario):
        assigned = self._assign(request_factory, admin_user, scenario)
        saved = _record_one_present(
            request_factory, scenario["session"]["id"], scenario["learner"]["id"], scenario["cover"]["session"],
            register_version=assigned["registerVersion"],
        )
        result = complete_register(
            scenario["session"]["id"],
            CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(),
            scenario["cover"]["session"],
        )
        assert result["session"]["registerStatus"] == "completed"

    def test_replacement_tutor_cannot_see_other_sessions_from_the_cohort(
        self, request_factory, admin_user, scenario, attendance_session_factory
    ):
        other_session = attendance_session_factory(
            cohort_id=scenario["cohort"]["id"], session_date="2026-01-08", created_by=admin_user["userId"]
        )
        self._assign(request_factory, admin_user, scenario)
        rows = list_attendance_sessions(session=scenario["cover"]["session"])
        assert not any(r["id"] == other_session["id"] for r in rows)

    def test_replacement_tutor_cannot_see_the_cohort_generally(self, request_factory, admin_user, scenario):
        self._assign(request_factory, admin_user, scenario)
        cohorts = list_cohorts(session=scenario["cover"]["session"])
        assert not any(c["id"] == scenario["cohort"]["id"] for c in cohorts)

    def test_replacement_tutor_cannot_see_unrelated_learners(self, request_factory, admin_user, scenario, learner_factory):
        unrelated = learner_factory(cohort_id=scenario["cohort"]["id"], tutor_id=None)
        self._assign(request_factory, admin_user, scenario)
        with pytest.raises(HTTPException) as exc:
            get_learner(unrelated["id"], scenario["cover"]["session"])
        assert exc.value.status_code == 403

    def test_replacement_tutor_cannot_access_original_tutors_reports(self, request_factory, admin_user, scenario):
        self._assign(request_factory, admin_user, scenario)
        rows = get_tutor_dashboard_cohorts(session=scenario["cover"]["session"])
        assert not any(r["cohort"]["id"] == scenario["cohort"]["id"] for r in rows)

    def test_original_tutor_loses_write_access_but_keeps_read_access(self, request_factory, admin_user, scenario):
        self._assign(request_factory, admin_user, scenario)
        # Read access is retained.
        full = get_attendance_session(scenario["session"]["id"], scenario["original"]["session"])
        assert full["session"]["id"] == scenario["session"]["id"]
        # Write access is not.
        with pytest.raises(HTTPException) as exc:
            _record_one_present(
                request_factory, scenario["session"]["id"], scenario["learner"]["id"], scenario["original"]["session"],
            )
        assert exc.value.status_code == 403

    def test_replacement_tutor_can_refresh_the_register(
        self, request_factory, admin_user, scenario, attendance_session_factory
    ):
        # Refresh is only allowed on a not-yet-historical session, unlike
        # `scenario["session"]` (a fixed past date shared by the whole
        # fixture) -- a fresh future-dated session is needed here.
        future_session = attendance_session_factory(
            cohort_id=scenario["cohort"]["id"], session_date=date.today() + timedelta(days=7),
            created_by=admin_user["userId"],
        )
        assign_cover_tutor(
            future_session["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(),
            admin_user,
        )
        result = refresh_session_register(
            future_session["id"], RefreshRegisterInput(confirm=False), request_factory(), scenario["cover"]["session"],
        )
        assert "toAdd" in result

    def test_original_tutor_cannot_refresh_while_cover_is_active(
        self, request_factory, admin_user, scenario, attendance_session_factory
    ):
        future_session = attendance_session_factory(
            cohort_id=scenario["cohort"]["id"], session_date=date.today() + timedelta(days=7),
            created_by=admin_user["userId"],
        )
        assign_cover_tutor(
            future_session["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(),
            admin_user,
        )
        with pytest.raises(HTTPException) as exc:
            refresh_session_register(
                future_session["id"], RefreshRegisterInput(confirm=False), request_factory(), scenario["original"]["session"],
            )
        assert exc.value.status_code == 403

    def test_admin_retains_full_access_throughout(self, request_factory, admin_user, scenario):
        assigned = self._assign(request_factory, admin_user, scenario)
        saved = _record_one_present(
            request_factory, scenario["session"]["id"], scenario["learner"]["id"], admin_user,
            register_version=assigned["registerVersion"],
        )
        assert saved["session"]["recordedCount"] == 1


class TestChangeAndRemoval:
    def test_admin_can_change_the_cover_tutor(self, request_factory, admin_user, scenario, tutor_factory):
        second_cover = tutor_factory()
        assigned = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        changed = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=second_cover["tutorId"], reason="annual_leave", registerVersion=assigned["registerVersion"]),
            request_factory(), admin_user,
        )
        assert changed["coverTutorId"] == second_cover["tutorId"]
        # coverOriginalTutorId is never overwritten by a change -- it still
        # points at the cohort's real original tutor, not the first cover.
        assert changed["coverOriginalTutorId"] == scenario["original"]["tutorId"]

    def test_previous_cover_tutor_loses_access_after_change(self, request_factory, admin_user, scenario, tutor_factory):
        second_cover = tutor_factory()
        assigned = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=second_cover["tutorId"], reason="annual_leave", registerVersion=assigned["registerVersion"]),
            request_factory(), admin_user,
        )
        with pytest.raises(HTTPException) as exc:
            get_attendance_session(scenario["session"]["id"], scenario["cover"]["session"])
        assert exc.value.status_code == 403

    def test_new_cover_tutor_gains_access_after_change(self, request_factory, admin_user, scenario, tutor_factory):
        second_cover = tutor_factory()
        assigned = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=second_cover["tutorId"], reason="annual_leave", registerVersion=assigned["registerVersion"]),
            request_factory(), admin_user,
        )
        full = get_attendance_session(scenario["session"]["id"], second_cover["session"])
        assert full["session"]["id"] == scenario["session"]["id"]

    def test_existing_attendance_preserved_across_a_cover_change(self, request_factory, admin_user, scenario, tutor_factory, db):
        second_cover = tutor_factory()
        assigned = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        saved = _record_one_present(
            request_factory, scenario["session"]["id"], scenario["learner"]["id"], scenario["cover"]["session"],
            register_version=assigned["registerVersion"],
        )
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=second_cover["tutorId"], reason="annual_leave", registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        db.execute(
            "SELECT status, created_by FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (scenario["session"]["id"], scenario["learner"]["id"]),
        )
        row = db.fetchone()
        assert row["status"] == "present"
        assert row["created_by"] == scenario["cover"]["userId"]

    def test_admin_can_remove_cover_before_completion(self, request_factory, admin_user, scenario):
        assigned = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        result = remove_cover_tutor_endpoint(
            scenario["session"]["id"],
            RemoveCoverTutorInput(reason="Cover no longer needed", registerVersion=assigned["registerVersion"]),
            request_factory(), admin_user,
        )
        assert result["coverTutorId"] is None
        assert result["effectiveTutorId"] == scenario["original"]["tutorId"]

    def test_removing_cover_restores_original_tutor_access(self, request_factory, admin_user, scenario):
        assigned = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        removed = remove_cover_tutor_endpoint(
            scenario["session"]["id"],
            RemoveCoverTutorInput(reason="Cover no longer needed", registerVersion=assigned["registerVersion"]),
            request_factory(), admin_user,
        )
        saved = _record_one_present(
            request_factory, scenario["session"]["id"], scenario["learner"]["id"], scenario["original"]["session"],
            register_version=removed["registerVersion"],
        )
        assert saved["session"]["recordedCount"] == 1
        with pytest.raises(HTTPException) as exc:
            get_attendance_session(scenario["session"]["id"], scenario["cover"]["session"])
        assert exc.value.status_code == 403

    def test_removal_does_not_delete_attendance(self, request_factory, admin_user, scenario, db):
        assigned = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        saved = _record_one_present(
            request_factory, scenario["session"]["id"], scenario["learner"]["id"], scenario["cover"]["session"],
            register_version=assigned["registerVersion"],
        )
        remove_cover_tutor_endpoint(
            scenario["session"]["id"],
            RemoveCoverTutorInput(reason="Done", confirmWithAttendance=True, registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        db.execute("SELECT count(*)::int AS count FROM attendance_records WHERE session_id = %s", (scenario["session"]["id"],))
        assert db.fetchone()["count"] == 1

    def test_removal_after_draft_entry_requires_confirmation_and_is_audited(self, request_factory, admin_user, scenario, db):
        assigned = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        saved = _record_one_present(
            request_factory, scenario["session"]["id"], scenario["learner"]["id"], scenario["cover"]["session"],
            register_version=assigned["registerVersion"],
        )
        with pytest.raises(HTTPException) as exc:
            remove_cover_tutor_endpoint(
                scenario["session"]["id"],
                RemoveCoverTutorInput(reason="Done", registerVersion=saved["session"]["registerVersion"]),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "attendance_already_recorded"

        remove_cover_tutor_endpoint(
            scenario["session"]["id"],
            RemoveCoverTutorInput(reason="Done", confirmWithAttendance=True, registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s AND action = 'cover_tutor_removed'",
            (scenario["session"]["id"],),
        )
        assert db.fetchone() is not None


class TestSessionState:
    def test_not_started_session_can_be_reassigned(self, request_factory, admin_user, scenario):
        result = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        assert result["coverTutorId"] == scenario["cover"]["tutorId"]

    def test_draft_in_progress_session_can_be_reassigned(self, request_factory, admin_user, scenario, learner_factory):
        learner_factory(cohort_id=scenario["cohort"]["id"])  # a second, unrecorded learner -> in_progress not completed
        saved = _record_one_present(request_factory, scenario["session"]["id"], scenario["learner"]["id"], admin_user)
        assert saved["session"]["registerStatus"] == "in_progress"
        result = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        assert result["coverTutorId"] == scenario["cover"]["tutorId"]

    def test_completed_session_reassignment_is_allowed_but_audited_as_a_correction(self, request_factory, admin_user, scenario, db):
        saved = _record_one_present(request_factory, scenario["session"]["id"], scenario["learner"]["id"], admin_user)
        completed = complete_register(
            scenario["session"]["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=completed["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s ORDER BY id DESC LIMIT 1",
            (scenario["session"]["id"],),
        )
        assert db.fetchone()["action"] == "cover_tutor_correction"

    def test_locked_session_cannot_have_cover_changed(self, request_factory, admin_user, scenario):
        saved = _record_one_present(request_factory, scenario["session"]["id"], scenario["learner"]["id"], admin_user)
        completed = complete_register(
            scenario["session"]["id"], CompleteRegisterInput(registerVersion=saved["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        lock_attendance_register(
            scenario["session"]["id"], LockRegisterInput(reason="week end", registerVersion=completed["session"]["registerVersion"]),
            request_factory(), admin_user,
        )
        with pytest.raises(HTTPException) as exc:
            assign_cover_tutor(
                scenario["session"]["id"],
                CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=completed["session"]["registerVersion"] + 1),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "register_locked"

    def test_cancelled_session_cannot_be_reassigned(self, request_factory, admin_user, scenario):
        cancel_attendance_session(scenario["session"]["id"], SessionCancelInput(reason="Weather"), request_factory(), admin_user)
        with pytest.raises(HTTPException) as exc:
            assign_cover_tutor(
                scenario["session"]["id"],
                CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "session_cancelled"

    def test_deleted_session_cannot_be_reassigned(self, request_factory, admin_user, scenario):
        delete_attendance_session(scenario["session"]["id"], SessionDeleteInput(reason="Created in error"), request_factory(), admin_user)
        with pytest.raises(HTTPException) as exc:
            assign_cover_tutor(
                scenario["session"]["id"],
                CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
                request_factory(), admin_user,
            )
        assert exc.value.status_code == 404


class TestAuditAndReporting:
    def test_assignment_is_audited_with_reason(self, request_factory, admin_user, scenario, db):
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        db.execute(
            "SELECT action, new_value FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s AND action = 'cover_tutor_assigned'",
            (scenario["session"]["id"],),
        )
        row = db.fetchone()
        assert row is not None
        new_value = json.loads(row["new_value"])
        assert new_value["reason"] == "tutor_sickness"
        assert new_value["coverTutorId"] == scenario["cover"]["tutorId"]

    def test_change_is_audited(self, request_factory, admin_user, scenario, tutor_factory, db):
        second_cover = tutor_factory()
        assigned = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=second_cover["tutorId"], reason="annual_leave", registerVersion=assigned["registerVersion"]),
            request_factory(), admin_user,
        )
        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s AND action = 'cover_tutor_changed'",
            (scenario["session"]["id"],),
        )
        assert db.fetchone() is not None

    def test_original_tutor_remains_historically_visible_while_cover_active(self, request_factory, admin_user, scenario):
        result = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        assert result["coverOriginalTutorId"] == scenario["original"]["tutorId"]
        assert result["coverOriginalTutorName"] is not None

    def test_delivery_tutor_available_on_session_reads(self, request_factory, admin_user, scenario):
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        full = get_attendance_session(scenario["session"]["id"], admin_user)
        assert full["session"]["effectiveTutorId"] == scenario["cover"]["tutorId"]
        assert full["session"]["tutorId"] == scenario["original"]["tutorId"]

    def test_no_employee_reference_field_is_introduced(self, request_factory, admin_user, scenario):
        result = assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        assert "employeeRef" not in result
        assert "coverTutorEmployeeRef" not in result


class TestRegression:
    def test_normal_uncovered_sessions_use_the_cohort_tutor_as_effective_tutor(self, admin_user, scenario):
        full = get_attendance_session(scenario["session"]["id"], admin_user)
        assert full["session"]["coverTutorId"] is None
        assert full["session"]["effectiveTutorId"] == scenario["original"]["tutorId"]

    def test_unrelated_tutor_still_cannot_access_the_session(self, scenario, tutor_factory):
        unrelated = tutor_factory()
        with pytest.raises(HTTPException) as exc:
            get_attendance_session(scenario["session"]["id"], unrelated["session"])
        assert exc.value.status_code == 403

    def test_attendance_metrics_still_group_by_cohort_tutor_not_cover_tutor(self, request_factory, admin_user, scenario, db):
        assign_cover_tutor(
            scenario["session"]["id"],
            CoverTutorInput(coverTutorId=scenario["cover"]["tutorId"], reason="tutor_sickness", registerVersion=1),
            request_factory(), admin_user,
        )
        _record_one_present(request_factory, scenario["session"]["id"], scenario["learner"]["id"], admin_user, register_version=2)
        # The session's fixed session_date (attendance_session_factory's
        # default, 2026-01-01) is what metrics filter on -- not "today" --
        # so the period must actually cover that date, not wall-clock now.
        metrics_by_tutor = fetch_attendance_metrics_grouped(
            db, group_by="tutor", group_ids=[scenario["original"]["tutorId"], scenario["cover"]["tutorId"]],
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        assert metrics_by_tutor[scenario["original"]["tutorId"]].attendedMinutes > 0
        assert metrics_by_tutor[scenario["cover"]["tutorId"]].attendedMinutes == 0
