"""Stage 3: catch-up permissions reuse the exact same session-attendance-
access rules as the rest of attendance data (item 5) -- admins manage
everything, a tutor only their own cohort's sessions or a session they are
the specific cover tutor for, and cover access never widens beyond that one
session. Exercised over real HTTP so the actual router/auth wiring is
proven, not just the underlying library functions."""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException, Request

from pyapp import auth as auth_module
from pyapp.cover_tutor_lib import assign_or_change_cover_tutor
from pyapp.routers.attendance import AttendanceRegisterInput, RegisterEntryInput, save_attendance_register

PAST_SESSION_DATE = "2026-01-05"

# The catchup router uses Depends(require_auth) directly (not wrapped in
# require_admin, which calls it as a plain function) -- FastAPI captures
# the real callable at route-registration time, so monkeypatching
# pyapp.auth.require_auth alone has no effect on it. Both the monkeypatch
# AND this dependency_overrides entry are required together (same pattern
# as test_allocation_reconciliation_lib.py). The override's own `request`
# parameter MUST be annotated `Request` -- dependency_overrides makes
# FastAPI resolve the override callable's own signature (not just call it
# opaquely), and an unannotated parameter is treated as a required query
# param, which is why this was earlier producing a baffling
# "query.request: Field required" 400 on every route.
_REAL_REQUIRE_AUTH = auth_module.require_auth


def _fake_session_dependency(session, user_id):
    def fake_require_auth(request: Request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    return fake_require_auth


def _as_tutor(client, monkeypatch, tutor_id, user_id):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}
    fake_require_auth = _fake_session_dependency(session, user_id)
    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)
    client.app.dependency_overrides[_REAL_REQUIRE_AUTH] = fake_require_auth


def _as_admin(client, monkeypatch, user_id=1):
    session = {"userId": user_id, "role": "admin", "tutorId": None}
    fake_require_auth = _fake_session_dependency(session, user_id)
    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)
    client.app.dependency_overrides[_REAL_REQUIRE_AUTH] = fake_require_auth


@pytest.fixture
def scenario(db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory):
    owner = tutor_factory()
    other = tutor_factory()
    cover = tutor_factory()
    cohort = cohort_factory(tutor_id=owner["tutorId"])
    learner = learner_factory(cohort_id=cohort["id"])
    session = attendance_session_factory(cohort_id=cohort["id"], session_date=PAST_SESSION_DATE, created_by=admin_user["userId"])
    save_attendance_register(
        session["id"],
        AttendanceRegisterInput(registerVersion=1, entries=[RegisterEntryInput(learnerId=learner["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)]),
        request_factory(),
        admin_user,
    )
    return {"owner": owner, "other": other, "cover": cover, "cohort": cohort, "learner": learner, "session": session}


class TestOrdinaryTutorAccess:
    def test_owning_tutor_can_record_catchup(self, client, monkeypatch, scenario):
        _as_tutor(client, monkeypatch, scenario["owner"]["tutorId"], scenario["owner"]["userId"])
        response = client.post(
            f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}",
            json={"completionDate": PAST_SESSION_DATE, "method": "recording_watched", "note": "Watched it"},
        )
        assert response.status_code == 200
        assert response.json()["effective"] is True

    def test_non_owning_tutor_cannot_record_catchup(self, client, monkeypatch, scenario):
        _as_tutor(client, monkeypatch, scenario["other"]["tutorId"], scenario["other"]["userId"])
        response = client.post(
            f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}",
            json={"completionDate": PAST_SESSION_DATE, "method": "recording_watched", "note": "note"},
        )
        assert response.status_code == 403

    def test_non_owning_tutor_cannot_even_read_catchup_state(self, client, monkeypatch, scenario):
        _as_tutor(client, monkeypatch, scenario["other"]["tutorId"], scenario["other"]["userId"])
        response = client.get(f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}")
        assert response.status_code == 403

    def test_admin_can_always_record_correct_and_revoke(self, client, monkeypatch, scenario):
        _as_admin(client, monkeypatch)
        record = client.post(
            f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}",
            json={"completionDate": PAST_SESSION_DATE, "method": "recording_watched", "note": "note"},
        )
        assert record.status_code == 200
        correct = client.put(
            f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}",
            json={"completionDate": PAST_SESSION_DATE, "method": "activity_completed", "note": "updated", "reason": "fixing method"},
        )
        assert correct.status_code == 200
        revoke = client.post(
            f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}/revoke",
            json={"reason": "recorded in error"},
        )
        assert revoke.status_code == 200


class TestCoverTutorAccess:
    def _assign_cover(self, db, scenario, request_factory, admin_user):
        from pyapp.auth import require_attendance_access

        attendance_session = require_attendance_access(db, scenario["session"]["id"], admin_user)
        assign_or_change_cover_tutor(db, attendance_session, scenario["cover"]["tutorId"], "tutor_sickness", None, admin_user["userId"])

    def test_cover_tutor_can_record_catchup_for_the_covered_session(self, client, monkeypatch, db, scenario, request_factory, admin_user):
        self._assign_cover(db, scenario, request_factory, admin_user)
        _as_tutor(client, monkeypatch, scenario["cover"]["tutorId"], scenario["cover"]["userId"])
        response = client.post(
            f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}",
            json={"completionDate": PAST_SESSION_DATE, "method": "recording_watched", "note": "note"},
        )
        assert response.status_code == 200

    def test_original_tutor_loses_write_access_while_cover_is_active(self, client, monkeypatch, db, scenario, request_factory, admin_user):
        """require_attendance_write_access blocks the ORIGINAL tutor from
        writing (read-only) while a cover tutor is assigned -- catch-up
        must respect this exactly like every other write on this session."""
        self._assign_cover(db, scenario, request_factory, admin_user)
        _as_tutor(client, monkeypatch, scenario["owner"]["tutorId"], scenario["owner"]["userId"])
        response = client.post(
            f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}",
            json={"completionDate": PAST_SESSION_DATE, "method": "recording_watched", "note": "note"},
        )
        assert response.status_code == 403

        # Read access is retained though.
        read = client.get(f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}")
        assert read.status_code == 200

    def test_cover_access_does_not_extend_to_other_sessions_in_the_same_cohort(
        self, client, monkeypatch, db, scenario, request_factory, admin_user, attendance_session_factory,
    ):
        """Cover is per-session, never a route into the wider cohort."""
        self._assign_cover(db, scenario, request_factory, admin_user)
        other_session = attendance_session_factory(cohort_id=scenario["cohort"]["id"], session_date=PAST_SESSION_DATE, created_by=admin_user["userId"])
        _as_tutor(client, monkeypatch, scenario["cover"]["tutorId"], scenario["cover"]["userId"])
        response = client.get(f"/api/attendance/sessions/{other_session['id']}/catchup/{scenario['learner']['id']}")
        assert response.status_code == 403


class TestFollowUpListScoping:
    def test_tutor_sees_only_their_own_sessions(self, client, monkeypatch, scenario):
        _as_tutor(client, monkeypatch, scenario["owner"]["tutorId"], scenario["owner"]["userId"])
        response = client.get(
            "/api/attendance/catchup/follow-up",
            params={"weekStart": "2026-01-01", "weekEnd": "2026-01-11"},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert all(item["learnerId"] == scenario["learner"]["id"] for item in items) or items == []

    def test_tutor_cannot_probe_another_tutors_cohort_via_filter(self, client, monkeypatch, scenario):
        """IDOR hardening -- probing another tutor's cohortId must 403, not
        silently return an empty 200 (list_attendance_sessions precedent)."""
        _as_tutor(client, monkeypatch, scenario["other"]["tutorId"], scenario["other"]["userId"])
        response = client.get(
            "/api/attendance/catchup/follow-up",
            params={"weekStart": "2026-01-01", "weekEnd": "2026-01-11", "cohortId": scenario["cohort"]["id"]},
        )
        assert response.status_code == 403

    def test_tutor_cannot_probe_another_tutors_learner_via_filter(self, client, monkeypatch, scenario):
        _as_tutor(client, monkeypatch, scenario["other"]["tutorId"], scenario["other"]["userId"])
        response = client.get(
            "/api/attendance/catchup/follow-up",
            params={"weekStart": "2026-01-01", "weekEnd": "2026-01-11", "learnerId": scenario["learner"]["id"]},
        )
        assert response.status_code == 403

    def test_search_finds_the_owning_tutors_own_learner_but_not_another_tutors(self, client, monkeypatch, scenario):
        _as_tutor(client, monkeypatch, scenario["owner"]["tutorId"], scenario["owner"]["userId"])
        found = client.get(
            "/api/attendance/catchup/follow-up",
            params={"weekStart": "2026-01-01", "weekEnd": "2026-01-11", "search": scenario["learner"]["last_name"]},
        )
        assert found.status_code == 200
        assert found.json()["total"] == 1
        assert found.json()["items"][0]["learnerId"] == scenario["learner"]["id"]

        _as_tutor(client, monkeypatch, scenario["other"]["tutorId"], scenario["other"]["userId"])
        not_found = client.get(
            "/api/attendance/catchup/follow-up",
            params={"weekStart": "2026-01-01", "weekEnd": "2026-01-11", "search": scenario["learner"]["last_name"]},
        )
        assert not_found.status_code == 200
        assert not_found.json()["total"] == 0

    def test_admin_sees_everything_without_a_cohort_filter(self, client, monkeypatch, scenario):
        _as_admin(client, monkeypatch)
        response = client.get(
            "/api/attendance/catchup/follow-up",
            params={"weekStart": "2026-01-01", "weekEnd": "2026-01-11"},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert any(item["learnerId"] == scenario["learner"]["id"] for item in items)


class TestCohortTransferDoesNotChangeHistoricalAccess:
    """Item 5/8: "Cohort transfers must not silently widen access to
    historical sessions." Session access is governed purely by the
    SESSION's own cohort_id (require_attendance_access), never by the
    learner's current cohort/tutor -- so transferring the learner elsewhere
    must neither grant the new tutor access to the old session nor revoke
    the original tutor's access to it. Catch-up eligibility must also keep
    using the frozen session_expected_learners roster, not current cohort
    membership (item 4)."""

    def test_original_tutor_keeps_access_after_the_learner_transfers_away(self, client, monkeypatch, db, scenario, admin_user):
        from pyapp.allocation_lib import apply_transfer

        learner_before = {
            "id": scenario["learner"]["id"],
            "tutorId": scenario["owner"]["tutorId"],
            "cohortId": scenario["cohort"]["id"],
        }
        apply_transfer(db, learner_before, None, None, date.today(), "test transfer", admin_user["userId"])

        _as_tutor(client, monkeypatch, scenario["owner"]["tutorId"], scenario["owner"]["userId"])
        response = client.post(
            f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}",
            json={"completionDate": PAST_SESSION_DATE, "method": "recording_watched", "note": "still owns the session"},
        )
        assert response.status_code == 200
        assert response.json()["effective"] is True

    def test_new_tutor_does_not_gain_access_to_the_old_session(self, client, monkeypatch, db, scenario, tutor_factory, cohort_factory, admin_user):
        from pyapp.allocation_lib import apply_transfer

        new_tutor = tutor_factory()
        new_cohort = cohort_factory(tutor_id=new_tutor["tutorId"])
        learner_before = {
            "id": scenario["learner"]["id"],
            "tutorId": scenario["owner"]["tutorId"],
            "cohortId": scenario["cohort"]["id"],
        }
        apply_transfer(db, learner_before, new_tutor["tutorId"], new_cohort["id"], date.today(), "test transfer", admin_user["userId"])

        _as_tutor(client, monkeypatch, new_tutor["tutorId"], new_tutor["userId"])
        response = client.get(f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}")
        assert response.status_code == 403


class TestGetEndpointsAreReadOnly:
    def test_get_catchup_performs_no_writes(self, db, client, monkeypatch, scenario):
        _as_admin(client, monkeypatch)
        db.execute("SELECT count(*) AS n FROM attendance_catchup")
        before = db.fetchone()["n"]
        db.execute("SELECT count(*) AS n FROM audit_logs")
        before_audit = db.fetchone()["n"]

        client.get(f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}")
        client.get(f"/api/attendance/sessions/{scenario['session']['id']}/catchup/{scenario['learner']['id']}/history")
        client.get("/api/attendance/catchup/follow-up", params={"weekStart": "2026-01-01", "weekEnd": "2026-01-11"})

        db.execute("SELECT count(*) AS n FROM attendance_catchup")
        assert db.fetchone()["n"] == before
        db.execute("SELECT count(*) AS n FROM audit_logs")
        assert db.fetchone()["n"] == before_audit
