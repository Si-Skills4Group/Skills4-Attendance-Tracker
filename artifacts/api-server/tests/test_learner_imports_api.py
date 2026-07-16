"""HTTP-level tests for the learner CSV import API (/learners/import-jobs/...).

Uses the same monkeypatch-based auth override as test_permissions.py: every
route here is wired with Depends(require_admin), which calls require_auth as
a plain function call internally, so patching pyapp.auth.require_auth is
sufficient to drive both the admin-allowed and tutor-denied paths through
the real ASGI routes (not just the underlying lib functions).
"""

import csv
import io
import os

import pytest

from pyapp import auth as auth_module

VALID_HEADER = "learner_reference,first_name,last_name,apprenticeship_programme,level,start_date"


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


def _upload(client, ref=None):
    ref = ref or f"API-{os.urandom(4).hex()}"
    csv_bytes = _csv_bytes(f"{ref},Jane,Doe-{os.urandom(4).hex()},Health,3,2026-01-01")
    response = client.post(
        "/api/learners/import-jobs",
        files={"file": ("learners.csv", csv_bytes, "text/csv")},
    )
    return response, ref


@pytest.fixture(autouse=True)
def _cleanup_import_jobs_and_learners(db):
    yield
    db.execute("DELETE FROM learner_import_rows WHERE job_id IN (SELECT id FROM learner_import_jobs WHERE filename = 'learners.csv')")
    db.execute("DELETE FROM learner_import_jobs WHERE filename = 'learners.csv'")
    db.execute("DELETE FROM learners WHERE learner_ref LIKE 'API-%'")


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def test_template_download_returns_csv_for_admin(client, monkeypatch):
    _as_admin(monkeypatch)
    response = client.get("/api/learners/import-jobs/template")
    assert response.status_code == 200
    body = response.json()
    assert "learner_reference" in body["csv"]


def test_tutor_cannot_access_template(client, monkeypatch):
    _as_tutor(monkeypatch)
    response = client.get("/api/learners/import-jobs/template")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def test_admin_can_upload_valid_csv(client, monkeypatch):
    _as_admin(monkeypatch)
    response, ref = _upload(client)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert body["totalRows"] == 1
    assert body["newCount"] == 1


def test_upload_rejects_malformed_csv(client, monkeypatch):
    _as_admin(monkeypatch)
    response = client.post(
        "/api/learners/import-jobs",
        files={"file": ("bad.csv", b"not,the,right,headers\n1,2,3,4\n", "text/csv")},
    )
    assert response.status_code == 400


def test_tutor_cannot_upload_via_url_manipulation(client, monkeypatch):
    _as_tutor(monkeypatch)
    response, _ = _upload(client)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Job read / row listing
# ---------------------------------------------------------------------------

def test_admin_can_read_job_and_rows(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    job_response = client.get(f"/api/learners/import-jobs/{job_id}")
    assert job_response.status_code == 200
    assert job_response.json()["id"] == job_id

    rows_response = client.get(f"/api/learners/import-jobs/{job_id}/rows")
    assert rows_response.status_code == 200
    assert rows_response.json()["total"] == 1


def test_unknown_job_id_is_404(client, monkeypatch):
    _as_admin(monkeypatch)
    response = client.get("/api/learners/import-jobs/999999999")
    assert response.status_code == 404


def test_tutor_cannot_read_job_via_url_manipulation(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    _as_tutor(monkeypatch)
    response = client.get(f"/api/learners/import-jobs/{job_id}")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Row resolution
# ---------------------------------------------------------------------------

def test_admin_can_resolve_a_duplicate_row(client, monkeypatch, learner_factory):
    existing = learner_factory(learner_ref="API-RESOLVE-1")
    _as_admin(monkeypatch)
    csv_bytes = _csv_bytes("API-RESOLVE-1,Jane,Doe,Health,3,2026-01-01")
    upload = client.post("/api/learners/import-jobs", files={"file": ("learners.csv", csv_bytes, "text/csv")})
    job_id = upload.json()["id"]

    rows = client.get(f"/api/learners/import-jobs/{job_id}/rows").json()["items"]
    row_id = rows[0]["id"]
    assert rows[0]["matchedLearnerId"] == existing["id"]

    response = client.patch(f"/api/learners/import-jobs/{job_id}/rows/{row_id}", json={"resolution": "update"})
    assert response.status_code == 200
    assert response.json()["resolution"] == "update"


def test_resolve_rejects_invalid_resolution_value(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]
    row_id = client.get(f"/api/learners/import-jobs/{job_id}/rows").json()["items"][0]["id"]

    response = client.patch(f"/api/learners/import-jobs/{job_id}/rows/{row_id}", json={"resolution": "delete"})
    assert response.status_code == 400  # rejected by pydantic Literal via the app's validation handler


def test_tutor_cannot_resolve_a_row_via_url_manipulation(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]
    row_id = client.get(f"/api/learners/import-jobs/{job_id}/rows").json()["items"][0]["id"]

    _as_tutor(monkeypatch)
    response = client.patch(f"/api/learners/import-jobs/{job_id}/rows/{row_id}", json={"resolution": "skip"})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Confirm / cancel
# ---------------------------------------------------------------------------

def test_confirm_creates_learner_and_is_idempotent_over_http(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, ref = _upload(client)
    job_id = upload_response.json()["id"]

    first = client.post(f"/api/learners/import-jobs/{job_id}/confirm")
    assert first.status_code == 200
    assert first.json()["created"] == 1

    second = client.post(f"/api/learners/import-jobs/{job_id}/confirm")
    assert second.status_code == 200
    assert second.json() == first.json()


def test_tutor_cannot_confirm_via_url_manipulation(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    _as_tutor(monkeypatch)
    response = client.post(f"/api/learners/import-jobs/{job_id}/confirm")
    assert response.status_code == 403


def test_admin_can_cancel_a_ready_job(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    response = client.post(f"/api/learners/import-jobs/{job_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_tutor_cannot_cancel_via_url_manipulation(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    _as_tutor(monkeypatch)
    response = client.post(f"/api/learners/import-jobs/{job_id}/cancel")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Error report download -- CSV formula-injection protection
# ---------------------------------------------------------------------------

def test_errors_csv_report_sanitizes_formula_injection(client, monkeypatch):
    _as_admin(monkeypatch)
    # A blank first_name makes the row invalid; the malicious-looking
    # learner_reference should still round-trip into the error report, but
    # sanitized so it can never execute as a formula if opened in a
    # spreadsheet app.
    csv_bytes_invalid = (VALID_HEADER + "\n" + "=SUM(A1:A9),,Doe,Health,3,2026-01-01\n").encode("utf-8")

    upload = client.post("/api/learners/import-jobs", files={"file": ("learners.csv", csv_bytes_invalid, "text/csv")})
    job_id = upload.json()["id"]

    response = client.get(f"/api/learners/import-jobs/{job_id}/rows/errors.csv")
    assert response.status_code == 200
    csv_text = response.json()["csv"]

    reader = csv.DictReader(io.StringIO(csv_text))
    error_row = next(iter(reader))
    # The dangerous value must be prefixed with a leading apostrophe --
    # checking for the substring alone would not distinguish a safely
    # prefixed "'=SUM(...)" from a raw, formula-executing "=SUM(...)" that
    # merely happens to appear elsewhere in the line.
    assert error_row["learner_reference"] == "'=SUM(A1:A9)"


def test_tutor_cannot_download_error_report_via_url_manipulation(client, monkeypatch):
    _as_admin(monkeypatch)
    upload_response, _ = _upload(client)
    job_id = upload_response.json()["id"]

    _as_tutor(monkeypatch)
    response = client.get(f"/api/learners/import-jobs/{job_id}/rows/errors.csv")
    assert response.status_code == 403
