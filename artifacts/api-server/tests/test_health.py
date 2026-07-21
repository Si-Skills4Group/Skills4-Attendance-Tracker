"""Liveness/readiness endpoints: liveness never touches the database,
readiness reports a real DB failure as a safe 503 (no connection details
leaked), and a Bud (public.learner_progress) failure degrades readiness
without ever failing it outright -- Bud is optional context, not a core
dependency."""
from pyapp.db import get_cursor
from pyapp.routers import health as health_module


def test_liveness_does_not_touch_the_database(client, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("liveness must never call get_cursor()")

    monkeypatch.setattr(health_module, "get_cursor", explode)
    response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_legacy_healthz_alias_still_works(client):
    response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_reports_ok_when_dependencies_are_healthy(client):
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"


def test_readiness_reports_database_failure_safely(client, monkeypatch):
    class _ExplodingCursor:
        def __enter__(self):
            raise RuntimeError("connection refused: password authentication failed for user 's4admin'")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(health_module, "get_cursor", lambda: _ExplodingCursor())
    response = client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == "unavailable"
    # No connection string, password, or raw exception text in the response.
    assert "s4admin" not in str(body)
    assert "password" not in str(body).lower()
    assert "RuntimeError" not in str(body)


def test_readiness_is_not_authenticated(client):
    # Health checks must be reachable by an unauthenticated load balancer
    # / Container Apps probe.
    response = client.get("/api/health/ready")
    assert response.status_code in (200, 503)


def test_stale_bud_data_degrades_readiness_without_failing_it(db, client):
    db.execute("SELECT count(*) AS c FROM public.learner_progress")
    # Whatever the current Bud sync state is (populated or empty), a real,
    # unmocked call to get_bud_sync_health must never turn readiness itself
    # unavailable -- only the database check can do that.
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["bud"] in ("ok", "no_data")


def test_bud_check_failure_is_reported_as_degraded_not_unavailable(client, monkeypatch):
    real_get_cursor = get_cursor
    call_count = {"n": 0}

    def flaky_get_cursor():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_get_cursor()

        class _ExplodingCursor:
            def __enter__(self):
                raise RuntimeError("public.learner_progress is unavailable")

            def __exit__(self, *args):
                return False

        return _ExplodingCursor()

    monkeypatch.setattr(health_module, "get_cursor", flaky_get_cursor)
    response = client.get("/api/health/ready")
    # The database check (first get_cursor call) succeeds; the Bud check
    # (second call) fails -- readiness overall must still be 200/ok.
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["bud"] == "degraded"
