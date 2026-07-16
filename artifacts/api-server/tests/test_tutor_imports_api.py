"""HTTP-level tests for the tutor CSV import API (/tutors/import-jobs/...).
Mirrors test_learner_imports_api.py -- see that file for the auth-override
rationale."""

import csv
import io
import os

import pytest

from pyapp import auth as auth_module

VALID_HEADER = "first_name,last_name,email,employee_ref,phone,active,external_system_id"


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


def _csv_bytes(*rows: str) -> bytes:
    return (VALID_HEADER + "\n" + "\n".join(rows) + "\n").encode("utf-8")


def _upload(client, email=None):
    email = email or f"api-{os.urandom(4).hex()}@example.com"
    csv_bytes = _csv_bytes(f"Jane,Doe-{os.urandom(4).hex()},{email},,,,")
    response = client.post(
        "/api/tutors/import-jobs",
        files={"file": ("tutors.csv", csv_bytes, "text/csv")},
    )
    return response, email


@pytest.fixture(autouse=True)
def _cleanup_import_jobs_and_tutors(db):
    yield
    db.execute("DELETE FROM tutor_import_rows WHERE job_id IN (SELECT id FROM tutor_import_jobs WHERE filename = 'tutors.csv')")
    db.execute("DELETE FROM tutor_import_jobs WHERE filename = 'tutors.csv'")
    db.execute("SELECT id, user_id FROM tutors WHERE email LIKE 'api-%@example.com'")
    for row in db.fetchall():
        db.execute("DELETE FROM tutors WHERE id = %s", (row["id"],))
        db.execute("DELETE FROM users WHERE id = %s", (row["user_id"],))


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def test_template_download_returns_csv_for_admin(client, monkeypatch):
    _as_admin(monkeypatch)
    response = client.get("/api/tutors/import-jobs/template")
    assert response.status_code == 200
    assert "first_name" in response.json()["csv"]


def test_tutor_cannot_access_template(client, monkeypatch):
    _as_tutor(monkeypatch)
    response = client.get("/api/tutors/import-jobs/template")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def test_admin_can_upload_valid_csv(client, monkeypatch):
    _as_admin(monkeypatch)
    response, _ = _upload(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert body["totalRows"] == 1
    assert body["newCount"] == 1


def test_upload_rejects_malformed_csv(client, monkeypatch):
    _as_admin(monkeypatch)
    response = client.post(
        "/api/tutors/import-jobs",
        files={"file": ("bad.csv", b"not,the,right,headers\n1,2,3,4\n", "text/csv")},
    )
    assert response.status_code == 400


def test_tutor_cannot_upload_via_url_manipulation(client, monkeypatch):
    _as_tutor(monkeypatch)
    response, _ = _upload(client)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Job read / row listing / resolve / confirm / cancel
# ---------------------------------------------------------------------------

def test_admin_can_read_job_and_rows(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    job_response = client.get(f"/api/tutors/import-jobs/{job_id}")
    assert job_response.status_code == 200

    rows_response = client.get(f"/api/tutors/import-jobs/{job_id}/rows")
    assert rows_response.status_code == 200
    assert rows_response.json()["total"] == 1


def test_tutor_cannot_read_job_via_url_manipulation(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    _as_tutor(monkeypatch)
    response = client.get(f"/api/tutors/import-jobs/{job_id}")
    assert response.status_code == 403


def test_resolve_rejects_invalid_resolution_value(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]
    row_id = client.get(f"/api/tutors/import-jobs/{job_id}/rows").json()["items"][0]["id"]

    response = client.patch(f"/api/tutors/import-jobs/{job_id}/rows/{row_id}", json={"resolution": "delete"})
    assert response.status_code == 400


def test_confirm_creates_tutor_and_is_idempotent_over_http(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    first = client.post(f"/api/tutors/import-jobs/{job_id}/confirm")
    assert first.status_code == 200
    assert first.json()["created"] == 1

    second = client.post(f"/api/tutors/import-jobs/{job_id}/confirm")
    assert second.status_code == 200
    assert second.json() == first.json()


def test_tutor_cannot_confirm_via_url_manipulation(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    _as_tutor(monkeypatch)
    response = client.post(f"/api/tutors/import-jobs/{job_id}/confirm")
    assert response.status_code == 403


def test_admin_can_cancel_a_ready_job(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    response = client.post(f"/api/tutors/import-jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_tutor_cannot_cancel_via_url_manipulation(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    _as_tutor(monkeypatch)
    response = client.post(f"/api/tutors/import-jobs/{job_id}/cancel")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Error report download -- CSV formula-injection protection
# ---------------------------------------------------------------------------

def test_errors_csv_report_sanitizes_formula_injection(client, monkeypatch):
    _as_admin(monkeypatch)
    # first_name blank -> invalid row; email carries a formula-injection-
    # looking value that must come back apostrophe-prefixed, not raw.
    csv_bytes_invalid = (VALID_HEADER + "\n" + ",Doe,=SUM(A1:A9),,,,\n").encode("utf-8")

    upload = client.post("/api/tutors/import-jobs", files={"file": ("tutors.csv", csv_bytes_invalid, "text/csv")})
    job_id = upload.json()["id"]

    response = client.get(f"/api/tutors/import-jobs/{job_id}/rows/errors.csv")
    assert response.status_code == 200
    csv_text = response.json()["csv"]

    reader = csv.DictReader(io.StringIO(csv_text))
    error_row = next(iter(reader))
    # Must be prefixed with a leading apostrophe, not raw -- a raw
    # substring check couldn't distinguish safe-prefixed from dangerous-raw.
    assert error_row["email"] == "'=SUM(A1:A9)"
    assert "first_name is required" in error_row["errors"]


def test_tutor_cannot_download_error_report_via_url_manipulation(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    _as_tutor(monkeypatch)
    response = client.get(f"/api/tutors/import-jobs/{job_id}/rows/errors.csv")
    assert response.status_code == 403
