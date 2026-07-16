import os

import pytest
from fastapi import HTTPException

from pyapp.tutor_import_lib import (
    cancel_import_job,
    confirm_import_job,
    create_import_job,
    get_import_job,
    list_import_job_rows,
    resolve_import_row,
)


def _row(**overrides) -> dict:
    row = {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": f"jane-{os.urandom(4).hex()}@example.com",
        "employee_ref": "",
        "phone": "",
        "active": "",
        "external_system_id": "",
    }
    row.update(overrides)
    return row


@pytest.fixture
def import_job_cleanup(db):
    created_ids = []
    yield created_ids
    for job_id in created_ids:
        db.execute("DELETE FROM tutor_import_rows WHERE job_id = %s", (job_id,))
        db.execute("DELETE FROM tutor_import_jobs WHERE id = %s", (job_id,))


@pytest.fixture
def user_ref_cleanup(db):
    """Deletes any user+tutor row created by email during a test, in
    fixture teardown -- always runs, even on assertion failure (see this
    session's learner-import test suite for why that matters: a failed
    test's manual post-assert cleanup can leave stray rows that
    contaminate a later run's duplicate-detection heuristics)."""
    emails = []
    yield emails
    for email in emails:
        db.execute("SELECT id, user_id FROM tutors WHERE email = %s", (email,))
        row = db.fetchone()
        if not row:
            continue
        db.execute("DELETE FROM tutors WHERE id = %s", (row["id"],))
        db.execute("DELETE FROM users WHERE id = %s", (row["user_id"],))


# ---------------------------------------------------------------------------
# Job creation / listing / resolution / cancel
# ---------------------------------------------------------------------------

def test_create_import_job_persists_header_counts_and_rows(db, admin_user, import_job_cleanup):
    rows = [_row(), _row(first_name="")]  # one valid "new", one invalid
    job = create_import_job(db, "tutors.csv", admin_user["userId"], rows)
    import_job_cleanup.append(job["id"])

    assert job["status"] == "ready"
    assert job["totalRows"] == 2
    assert job["newCount"] == 1
    assert job["invalidCount"] == 1

    listing = list_import_job_rows(db, job["id"])
    assert listing["total"] == 2
    assert {r["classification"] for r in listing["items"]} == {"new", "invalid"}


def test_resolve_import_row_rejects_new_classification(db, admin_user, import_job_cleanup):
    job = create_import_job(db, "tutors.csv", admin_user["userId"], [_row()])
    import_job_cleanup.append(job["id"])
    row = list_import_job_rows(db, job["id"])["items"][0]

    with pytest.raises(HTTPException) as exc:
        resolve_import_row(db, job["id"], row["id"], "update", admin_user["userId"])
    assert exc.value.status_code == 400


def test_cancel_import_job_marks_cancelled(db, admin_user, import_job_cleanup):
    job = create_import_job(db, "tutors.csv", admin_user["userId"], [_row()])
    import_job_cleanup.append(job["id"])

    cancelled = cancel_import_job(db, job["id"])
    assert cancelled["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Confirm: create / update / idempotency / rollback-on-failure
# ---------------------------------------------------------------------------

def test_confirm_creates_new_tutor_and_is_audited(db, admin_user, request_factory, import_job_cleanup, user_ref_cleanup):
    row = _row()
    user_ref_cleanup.append(row["email"])
    job = create_import_job(db, "tutors.csv", admin_user["userId"], [row])
    import_job_cleanup.append(job["id"])

    summary = confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert summary["created"] == 1

    db.execute("SELECT id FROM tutors WHERE email = %s", (row["email"],))
    tutor = db.fetchone()
    assert tutor is not None

    db.execute(
        "SELECT id FROM audit_logs WHERE entity_type = 'tutor' AND entity_id = %s AND action = 'create'",
        (tutor["id"],),
    )
    assert db.fetchone() is not None

    refreshed = get_import_job(db, job["id"])
    assert refreshed["status"] == "completed"
    assert refreshed["resultSummary"]["created"] == 1


def test_confirm_updates_existing_tutor_without_touching_role_or_entra_identity(
    db, admin_user, request_factory, tutor_factory, import_job_cleanup
):
    tutor = tutor_factory()
    db.execute("SELECT t.email FROM tutors t WHERE t.id = %s", (tutor["tutorId"],))
    existing_email = db.fetchone()["email"]

    row = _row(email=existing_email, phone="07700-900000")
    job = create_import_job(db, "tutors.csv", admin_user["userId"], [row])
    import_job_cleanup.append(job["id"])
    row_data = list_import_job_rows(db, job["id"])["items"][0]
    resolve_import_row(db, job["id"], row_data["id"], "update", admin_user["userId"])

    summary = confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert summary["updated"] == 1

    db.execute("SELECT phone FROM tutors WHERE id = %s", (tutor["tutorId"],))
    assert db.fetchone()["phone"] == "07700-900000"

    db.execute(
        "SELECT role, entra_object_id, entra_tenant_id FROM users WHERE id = %s", (tutor["userId"],)
    )
    user_row = db.fetchone()
    assert user_row["role"] == "tutor"
    assert user_row["entra_object_id"] is None  # untouched -- CSV import never carries identity fields


def test_confirm_default_resolution_skips_duplicate_row(db, admin_user, request_factory, tutor_factory, import_job_cleanup):
    tutor = tutor_factory()
    db.execute("SELECT email FROM tutors WHERE id = %s", (tutor["tutorId"],))
    existing_email = db.fetchone()["email"]

    job = create_import_job(
        db, "tutors.csv", admin_user["userId"], [_row(email=existing_email, first_name="Changed")]
    )
    import_job_cleanup.append(job["id"])

    summary = confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert summary["skipped"] == 1

    db.execute("SELECT first_name FROM tutors WHERE id = %s", (tutor["tutorId"],))
    assert db.fetchone()["first_name"] == "Test"  # unchanged from tutor_factory's default


def test_confirm_is_idempotent_on_double_confirm(db, admin_user, request_factory, import_job_cleanup, user_ref_cleanup):
    row = _row()
    user_ref_cleanup.append(row["email"])
    job = create_import_job(db, "tutors.csv", admin_user["userId"], [row])
    import_job_cleanup.append(job["id"])

    first = confirm_import_job(db, job["id"], request_factory(), admin_user)
    second = confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert first == second

    db.execute("SELECT count(*)::int AS count FROM tutors WHERE email = %s", (row["email"],))
    assert db.fetchone()["count"] == 1


def test_confirm_rejects_job_not_in_ready_state(db, admin_user, request_factory, import_job_cleanup):
    job = create_import_job(db, "tutors.csv", admin_user["userId"], [_row()])
    import_job_cleanup.append(job["id"])
    db.execute("UPDATE tutor_import_jobs SET status = 'uploaded' WHERE id = %s", (job["id"],))

    with pytest.raises(HTTPException) as exc:
        confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert exc.value.status_code == 409


def test_confirm_rolls_back_entire_job_when_one_row_fails(db, admin_user, request_factory, import_job_cleanup, user_ref_cleanup):
    ok_row = _row()
    race_row = _row()
    user_ref_cleanup.extend([ok_row["email"], race_row["email"]])
    job = create_import_job(db, "tutors.csv", admin_user["userId"], [ok_row, race_row])
    import_job_cleanup.append(job["id"])

    # Simulate a race: another process creates the second row's tutor
    # (by email) after classification but before confirm.
    db.execute(
        "INSERT INTO users (first_name, last_name, email, role, active) VALUES ('Race', 'Winner', %s, 'tutor', true) RETURNING id",
        (race_row["email"],),
    )
    race_user_id = db.fetchone()["id"]
    db.execute(
        "INSERT INTO tutors (user_id, first_name, last_name, email, active) VALUES (%s, 'Race', 'Winner', %s, true)",
        (race_user_id, race_row["email"]),
    )

    with pytest.raises(HTTPException):
        confirm_import_job(db, job["id"], request_factory(), admin_user)

    db.execute("SELECT count(*)::int AS count FROM tutors WHERE email = %s", (ok_row["email"],))
    assert db.fetchone()["count"] == 0  # rolled back, not left half-imported

    refreshed = get_import_job(db, job["id"])
    assert refreshed["status"] == "ready"
    assert refreshed["lastError"]


def test_confirm_never_bypasses_the_active_cohorts_deactivation_guard(
    db, admin_user, request_factory, tutor_factory, cohort_factory, import_job_cleanup
):
    """A CSV-driven update that would deactivate a tutor who still has an
    active cohort assigned must fail closed (like any other row error,
    rolling back the whole job) rather than silently bypassing the guard
    the dedicated /deactivate endpoint enforces."""
    tutor = tutor_factory()
    cohort_factory(tutor_id=tutor["tutorId"], active=True)
    db.execute("SELECT email FROM tutors WHERE id = %s", (tutor["tutorId"],))
    existing_email = db.fetchone()["email"]

    job = create_import_job(
        db, "tutors.csv", admin_user["userId"], [_row(email=existing_email, active="false")]
    )
    import_job_cleanup.append(job["id"])
    row = list_import_job_rows(db, job["id"])["items"][0]
    resolve_import_row(db, job["id"], row["id"], "update", admin_user["userId"])

    with pytest.raises(HTTPException) as exc:
        confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert exc.value.status_code == 409

    db.execute("SELECT active FROM tutors WHERE id = %s", (tutor["tutorId"],))
    assert db.fetchone()["active"] is True  # untouched

    refreshed = get_import_job(db, job["id"])
    assert refreshed["status"] == "ready"
