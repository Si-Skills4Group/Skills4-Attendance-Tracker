"""Every response carries an X-Correlation-Id header (pyapp/correlation.py):
a valid incoming one is echoed back, a malicious/oversized one is replaced
with a freshly generated one rather than reflected, and unauthenticated
requests still get one (the middleware sits outside auth entirely)."""
import re

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def test_response_always_carries_a_correlation_id(client):
    response = client.get("/api/health/live")
    assert response.headers.get("x-correlation-id")


def test_unauthenticated_request_still_gets_a_correlation_id(client):
    response = client.get("/api/users")
    assert response.status_code == 401
    assert response.headers.get("x-correlation-id")


def test_valid_incoming_correlation_id_is_echoed_back(client):
    response = client.get("/api/health/live", headers={"X-Correlation-Id": "my-request-id-123"})
    assert response.headers["x-correlation-id"] == "my-request-id-123"


def test_oversized_incoming_correlation_id_is_replaced_not_reflected(client):
    malicious = "a" * 5000
    response = client.get("/api/health/live", headers={"X-Correlation-Id": malicious})
    returned = response.headers["x-correlation-id"]
    assert returned != malicious
    assert len(returned) < 200


def test_correlation_id_with_unsafe_characters_is_replaced_not_reflected(client):
    malicious = "<script>alert(1)</script>"
    response = client.get("/api/health/live", headers={"X-Correlation-Id": malicious})
    returned = response.headers["x-correlation-id"]
    assert returned != malicious
    assert UUID_RE.match(returned)


def test_unhandled_exception_response_includes_correlation_id(monkeypatch):
    # An invalid ISO date on /audit-log's dateFrom raises a raw ValueError
    # inside the handler (datetime.fromisoformat), uncaught by any
    # deliberate validation -- a real, easy-to-trigger unhandled exception
    # that exercises pyapp.main's generic exception handler end to end.
    # Starlette's TestClient re-raises server exceptions by default (handy
    # for seeing real tracebacks in most tests) -- raise_server_exceptions
    # =False here is what actually lets the custom Exception handler's
    # response come back through, matching real (uvicorn) behavior.
    from fastapi.testclient import TestClient

    from pyapp import auth as auth_module
    from pyapp.main import app

    def fake_require_auth(request):
        request.state.session = {"userId": 1, "role": "admin", "tutorId": None}
        request.state.current_user_id = 1
        return request.state.session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/audit-log?dateFrom=not-a-real-date")
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error"
    assert "correlationId" in body
    assert body["correlationId"] == response.headers["x-correlation-id"]
    # Never leak the raw exception message, a stack trace, or a file path.
    assert "ValueError" not in str(body)
    assert ".py" not in str(body)
