"""HTTP-level permission enforcement for the tutor-identity endpoints --
mirrors test_allocation_reconciliation_api.py's pattern exactly."""
from pyapp import auth as auth_module


def _as_tutor(monkeypatch, tutor_id=1, user_id=1):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}

    def fake_require_auth(request):
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


def test_tutor_cannot_access_tutor_identity_diagnostics(client, monkeypatch):
    _as_tutor(monkeypatch)
    response = client.get("/api/tutor-identity/diagnostics")
    assert response.status_code == 403


def test_admin_can_access_tutor_identity_diagnostics(client, monkeypatch):
    _as_admin(monkeypatch)
    response = client.get("/api/tutor-identity/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert "unmatchedBudTutorIds" in body
    assert "duplicateTutorCandidates" in body


def test_tutor_cannot_preview_a_mapping_correction(client, monkeypatch, tutor_factory):
    _as_tutor(monkeypatch)
    tutor = tutor_factory()
    response = client.post(
        "/api/tutor-identity/mapping-corrections/preview",
        json={"sourceTutorId": tutor["tutorId"], "targetTutorId": tutor["tutorId"] + 1, "budTutorId": "X"},
    )
    assert response.status_code == 403


def test_tutor_cannot_commit_a_mapping_correction(client, monkeypatch, tutor_factory):
    _as_tutor(monkeypatch)
    tutor = tutor_factory()
    response = client.post(
        "/api/tutor-identity/mapping-corrections/commit",
        json={
            "sourceTutorId": tutor["tutorId"], "targetTutorId": tutor["tutorId"] + 1, "budTutorId": "X",
            "expectedSourceTutorUpdatedAt": "2026-01-01T00:00:00Z", "expectedTargetTutorUpdatedAt": "2026-01-01T00:00:00Z",
            "reason": "should be blocked",
        },
    )
    assert response.status_code == 403


def test_admin_preview_then_commit_over_http(client, monkeypatch, db, tutor_factory):
    _as_admin(monkeypatch)
    source = tutor_factory(active=False)
    target = tutor_factory(active=True)
    db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-HTTP-FLOW", source["tutorId"]))

    preview_response = client.post(
        "/api/tutor-identity/mapping-corrections/preview",
        json={"sourceTutorId": source["tutorId"], "targetTutorId": target["tutorId"], "budTutorId": "BUD-HTTP-FLOW"},
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    # tutor_factory's default fixtures share a name ("Test Tutor") but no
    # independently-linking identifier -- commit must require explicit
    # confirmation.
    assert preview["requiresManualIdentityConfirmation"] is True

    commit_without_confirmation = client.post(
        "/api/tutor-identity/mapping-corrections/commit",
        json={
            "sourceTutorId": source["tutorId"], "targetTutorId": target["tutorId"], "budTutorId": "BUD-HTTP-FLOW",
            "expectedSourceTutorUpdatedAt": preview["preview"]["sourceTutorUpdatedAt"],
            "expectedTargetTutorUpdatedAt": preview["preview"]["targetTutorUpdatedAt"],
            "reason": "verified via HTTP round-trip test",
        },
    )
    assert commit_without_confirmation.status_code == 400

    commit_response = client.post(
        "/api/tutor-identity/mapping-corrections/commit",
        json={
            "sourceTutorId": source["tutorId"], "targetTutorId": target["tutorId"], "budTutorId": "BUD-HTTP-FLOW",
            "expectedSourceTutorUpdatedAt": preview["preview"]["sourceTutorUpdatedAt"],
            "expectedTargetTutorUpdatedAt": preview["preview"]["targetTutorUpdatedAt"],
            "reason": "verified via HTTP round-trip test",
            "identityConfirmedByAdmin": True,
        },
    )
    assert commit_response.status_code == 200
    db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (target["tutorId"],))
    assert db.fetchone()["external_system_id"] == "BUD-HTTP-FLOW"


def test_tutor_cannot_access_missing_source_exceptions(client, monkeypatch):
    _as_tutor(monkeypatch)
    response = client.get("/api/bud-missing-source/exceptions")
    assert response.status_code == 403


def test_admin_can_access_missing_source_exceptions(client, monkeypatch):
    _as_admin(monkeypatch)
    response = client.get("/api/bud-missing-source/exceptions")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    # Source freshness/completeness must be visible in the interface, not
    # just asserted in a docstring -- this was missing before verification.
    assert "sourceInfo" in body
    assert "sourceCompletenessNote" in body["sourceInfo"]
    # Stage 2 verification pass, item 1: the refresh status (when detection
    # last ran/succeeded) must also be visible on the read-only GET.
    assert "refreshStatus" in body
    assert "lastSucceededAt" in body["refreshStatus"]


def test_tutor_cannot_trigger_missing_source_refresh(client, monkeypatch):
    _as_tutor(monkeypatch)
    response = client.post("/api/bud-missing-source/refresh")
    assert response.status_code == 403


def test_admin_can_trigger_missing_source_refresh_over_http(client, monkeypatch):
    _as_admin(monkeypatch)
    response = client.post("/api/bud-missing-source/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["lastSucceededAt"] is not None
    assert body["lastError"] is None
