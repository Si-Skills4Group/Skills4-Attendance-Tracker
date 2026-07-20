"""Permission and authorization tests.

Two layers are tested deliberately:

1. The auth dependency functions (require_admin, require_role,
   require_cohort_access, ...) are unit-tested directly by
   monkeypatching `pyapp.auth.require_auth` to report a controlled
   identity. This proves the *authorization logic itself* is correct.
   Monkeypatching the module attribute (rather than FastAPI's
   dependency_overrides) matters here: require_admin calls
   require_auth as a plain Python call, not via Depends(), so it is
   only intercepted by patching the module's global name -- which is
   also what lets the same patch work through real HTTP requests below.

2. A handful of tests go through the actual HTTP routes via
   TestClient with the same monkeypatch, to prove the real route
   wiring (Depends(require_admin) on the actual endpoint) enforces
   this, not just the helper function in isolation.
"""

import pytest
from fastapi import HTTPException

from pyapp import auth as auth_module


def _as_tutor(monkeypatch, tutor_id=1, user_id=1):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}

    def fake_require_auth(request):
        # Mirror what the real require_auth/_load_entra_user does: stash the
        # resolved session on request.state, since write_audit_log reads it
        # from there (not from this function's return value) when logging
        # authorization_denied events.
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)


def _as_admin(monkeypatch, user_id=1):
    session = {"userId": user_id, "role": "admin", "tutorId": None}

    def fake_require_auth(request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)


class TestRequireAdminLogic:
    def test_denies_tutor_role(self, monkeypatch, request_factory):
        _as_tutor(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            auth_module.require_admin(request_factory())
        assert exc.value.status_code == 403

    def test_allows_admin_role(self, monkeypatch, request_factory):
        _as_admin(monkeypatch)
        session = auth_module.require_admin(request_factory())
        assert session["role"] == "admin"

    def test_denial_is_audited(self, monkeypatch, request_factory, db):
        _as_tutor(monkeypatch, user_id=424242)
        request = request_factory()
        with pytest.raises(HTTPException):
            auth_module.require_admin(request)
        db.execute(
            "SELECT action FROM audit_logs WHERE user_id = %s AND action = 'authorization_denied' ORDER BY id DESC LIMIT 1",
            (424242,),
        )
        assert db.fetchone() is not None


class TestRequireAuthUnauthenticated:
    def test_missing_bearer_token_is_rejected(self, request_factory):
        # AUTH_MODE is "entra" for the test suite (see conftest); no
        # Authorization header at all must fail before any Entra call.
        with pytest.raises(HTTPException) as exc:
            auth_module.require_auth(request_factory())
        assert exc.value.status_code == 401

    def test_admin_endpoint_rejects_missing_token_over_http(self, client):
        response = client.get("/api/users")
        assert response.status_code == 401


class TestRowLevelAccess:
    def test_tutor_cannot_access_another_tutors_cohort(self, db, cohort_factory, tutor_factory):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])

        with pytest.raises(HTTPException) as exc:
            auth_module.require_cohort_access(db, cohort["id"], other["session"])
        assert exc.value.status_code == 403

    def test_tutor_can_access_own_cohort(self, db, cohort_factory, tutor_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        result = auth_module.require_cohort_access(db, cohort["id"], tutor["session"])
        assert result["id"] == cohort["id"]

    def test_admin_bypasses_tutor_ownership_check(self, db, cohort_factory, tutor_factory, admin_user):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        result = auth_module.require_cohort_access(db, cohort["id"], admin_user)
        assert result["id"] == cohort["id"]

    def test_nonexistent_cohort_id_is_404_not_403(self, db, tutor_factory):
        """A tutor probing an unowned resource by guessing IDs should not be
        able to distinguish 'not yours' from 'does not exist' via a
        different status code family in a way that leaks existence --
        both real access-denied paths return meaningful, non-500 codes."""
        tutor = tutor_factory()
        with pytest.raises(HTTPException) as exc:
            auth_module.require_cohort_access(db, 999_999_999, tutor["session"])
        assert exc.value.status_code == 404

    def test_tutor_cannot_access_another_tutors_learner(self, db, learner_factory, tutor_factory):
        owner = tutor_factory()
        other = tutor_factory()
        learner = learner_factory(tutor_id=owner["tutorId"])

        with pytest.raises(HTTPException) as exc:
            auth_module.require_learner_access(db, learner["id"], other["session"])
        assert exc.value.status_code == 403


class TestEndpointsRejectTutorsOverHttp:
    """These go through the real ASGI route (TestClient), so
    Depends(require_admin) on the actual endpoint is what's being
    proven here, not just the helper function."""

    def test_tutor_cannot_list_users(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        response = client.get("/api/users")
        assert response.status_code == 403

    def test_tutor_cannot_create_tutor(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        response = client.post(
            "/api/tutors",
            json={"firstName": "X", "lastName": "Y", "email": "nope@example.com"},
        )
        assert response.status_code == 403

    def test_tutor_cannot_create_learner(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        response = client.post(
            "/api/learners",
            json={
                "learnerRef": "SHOULD-NOT-EXIST",
                "firstName": "X",
                "lastName": "Y",
                "programme": "P",
                "level": "3",
                "startDate": "2026-01-01",
            },
        )
        assert response.status_code == 403

    def test_tutor_cannot_create_cohort(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        response = client.post(
            "/api/cohorts",
            json={
                "name": "SHOULD-NOT-EXIST",
                "programme": "P",
                "level": "3",
                "deliveryDay": "monday",
                "sessionStartTime": "09:00",
                "sessionEndTime": "10:00",
                "startDate": "2026-01-01",
            },
        )
        assert response.status_code == 403

    def test_admin_can_list_users(self, client, monkeypatch):
        _as_admin(monkeypatch)
        response = client.get("/api/users")
        assert response.status_code == 200

    def test_tutor_cannot_delete_a_learner(self, client, monkeypatch, learner_factory):
        learner = learner_factory()
        _as_tutor(monkeypatch)
        response = client.post(f"/api/learners/{learner['id']}/delete", json={"reason": "Should be denied"})
        assert response.status_code == 403

    def test_tutor_cannot_delete_a_cohort(self, client, monkeypatch, cohort_factory):
        cohort = cohort_factory()
        _as_tutor(monkeypatch)
        response = client.post(f"/api/cohorts/{cohort['id']}/delete", json={"reason": "Should be denied"})
        assert response.status_code == 403

    def test_tutor_cannot_delete_an_attendance_session(self, client, monkeypatch, admin_user, cohort_factory, attendance_session_factory):
        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        _as_tutor(monkeypatch)
        response = client.post(f"/api/attendance/sessions/{session['id']}/delete", json={"reason": "Should be denied"})
        assert response.status_code == 403

    def _as_tutor_via_dependency_override(self, client, tutor_id):
        """require_auth is wired directly as Depends(require_auth) on
        GET /cohorts/{id} and GET /attendance/sessions (unlike
        Depends(require_admin), which calls require_auth as a plain
        function call internally) -- FastAPI captures that dependency
        callable at route-registration time, so monkeypatching
        auth_module.require_auth afterwards has no effect on routes wired
        this way. app.dependency_overrides is the correct override
        mechanism for a dependency referenced directly like this."""
        fake_session = {"userId": 1, "role": "tutor", "tutorId": tutor_id}
        client.app.dependency_overrides[auth_module.require_auth] = lambda: fake_session

    def test_tutor_cannot_open_another_tutors_cohort_over_http(self, client, tutor_factory, cohort_factory):
        """A frontend route guard is not sufficient -- this proves the real
        ASGI route for GET /cohorts/{id} (what the corrected attendance
        cohort-sessions page calls for cohort details) rejects a tutor
        attempting to reach another tutor's cohort by URL manipulation."""
        owner = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        self._as_tutor_via_dependency_override(client, owner["tutorId"] + 999_999)
        try:
            response = client.get(f"/api/cohorts/{cohort['id']}")
        finally:
            client.app.dependency_overrides.pop(auth_module.require_auth, None)
        assert response.status_code == 403

    def test_tutor_cannot_list_another_tutors_cohort_sessions_over_http(self, client, tutor_factory, cohort_factory):
        """Same as above for GET /attendance/sessions?cohortId= -- the API
        the corrected cohort-sessions page calls for the session list."""
        owner = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        self._as_tutor_via_dependency_override(client, owner["tutorId"] + 999_999)
        try:
            response = client.get(f"/api/attendance/sessions?cohortId={cohort['id']}")
        finally:
            client.app.dependency_overrides.pop(auth_module.require_auth, None)
        assert response.status_code == 403
