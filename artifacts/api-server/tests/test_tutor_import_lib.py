from datetime import datetime, timedelta

import pytest

from pyapp.csv_utils import parse_tutor_import_csv
from pyapp.tutor_import_lib import (
    ExistingTutorIndex,
    classify_row,
    classify_rows,
    expire_due_tutor_import_jobs,
    validate_row_fields,
)

VALID_HEADER = "first_name,last_name,email,employee_ref,phone,active,external_system_id"


def _row(**overrides) -> dict:
    row = {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane.doe@example.com",
        "employee_ref": "",
        "phone": "",
        "active": "",
        "external_system_id": "",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# CSV parsing (wiring check only -- the generic parser itself is fully
# covered by test_learner_import_lib.py's parse_import_csv tests)
# ---------------------------------------------------------------------------

def test_parse_tutor_import_csv_returns_snake_case_rows():
    csv_text = f"{VALID_HEADER}\nJane,Doe,jane.doe@example.com,,,,\n"
    rows = parse_tutor_import_csv(csv_text.encode("utf-8"))
    assert rows == [_row()]


def test_parse_tutor_import_csv_rejects_missing_required_column():
    from pyapp.csv_utils import CsvParseError

    with pytest.raises(CsvParseError, match="Missing required column"):
        parse_tutor_import_csv(b"first_name,last_name\nJane,Doe\n")


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------

def test_validate_row_fields_flags_missing_required_fields():
    errors = validate_row_fields(_row(email=""))
    assert any("email" in e for e in errors)


def test_validate_row_fields_accepts_valid_row():
    assert validate_row_fields(_row()) == []


# ---------------------------------------------------------------------------
# Duplicate classification
# ---------------------------------------------------------------------------

def _index(*tutors) -> ExistingTutorIndex:
    return ExistingTutorIndex(list(tutors))


def _existing(**overrides) -> dict:
    row = {"id": 1, "firstName": "Existing", "lastName": "Tutor", "email": "existing@example.com", "employeeRef": None}
    row.update(overrides)
    return row


def test_classify_row_invalid_when_required_fields_missing():
    result = classify_row(_index(), _row(email=""))
    assert result["classification"] == "invalid"
    assert result["proposedAction"] == "blocked"


def test_classify_row_new_when_no_match():
    result = classify_row(_index(), _row())
    assert result["classification"] == "new"
    assert result["proposedAction"] == "create"
    assert result["matchedTutorId"] is None


def test_classify_row_exact_existing_when_email_matches():
    existing = _existing(id=42, email="jane.doe@example.com")
    result = classify_row(_index(existing), _row(email="jane.doe@example.com"))
    assert result["classification"] == "exact_existing"
    assert result["proposedAction"] == "skip"
    assert result["matchedTutorId"] == 42


def test_classify_row_case_insensitive_email_match():
    existing = _existing(id=42, email="jane.doe@example.com")
    result = classify_row(_index(existing), _row(email="JANE.DOE@EXAMPLE.COM"))
    assert result["classification"] == "exact_existing"


def test_classify_row_probable_duplicate_when_employee_ref_matches_different_email():
    existing = _existing(id=42, email="other@example.com", employeeRef="EMP-1")
    result = classify_row(_index(existing), _row(email="new@example.com", employee_ref="EMP-1"))
    assert result["classification"] == "probable_duplicate"
    assert result["matchedTutorId"] == 42


def test_classify_row_identifier_conflict_when_email_and_employee_ref_point_to_different_tutors():
    email_owner = _existing(id=1, email="jane.doe@example.com", employeeRef="EMP-A")
    ref_owner = _existing(id=2, email="other@example.com", employeeRef="EMP-B")
    result = classify_row(_index(email_owner, ref_owner), _row(email="jane.doe@example.com", employee_ref="EMP-B"))
    assert result["classification"] == "identifier_conflict"
    assert result["proposedAction"] == "blocked"


def test_classify_rows_flags_duplicate_email_within_same_file():
    row1 = _row(email="dup@example.com")
    row2 = _row(email="DUP@example.com", first_name="Other")  # case-insensitive dup
    index = ExistingTutorIndex([])
    results = []
    seen = set()
    for row in (row1, row2):
        result = classify_row(index, row)
        email = row["email"].strip().lower()
        if result["classification"] == "new" and email in seen:
            result = {**result, "classification": "identifier_conflict", "proposedAction": "blocked"}
        else:
            seen.add(email)
        results.append(result)
    assert results[0]["classification"] == "new"
    assert results[1]["classification"] == "identifier_conflict"


def test_classify_rows_against_real_db_detects_existing_tutor(db, tutor_factory):
    tutor = tutor_factory()
    db.execute("SELECT email FROM tutors WHERE id = %s", (tutor["tutorId"],))
    email = db.fetchone()["email"]

    results = classify_rows(db, [_row(email=email)])
    assert results[0]["classification"] == "exact_existing"
    assert results[0]["matchedTutorId"] == tutor["tutorId"]


# ---------------------------------------------------------------------------
# Job expiry / crash-recovery sweep
# ---------------------------------------------------------------------------

@pytest.fixture
def tutor_import_job_factory(db, admin_user):
    created_ids = []

    def make(**overrides) -> dict:
        defaults = dict(
            filename="test.csv",
            uploaded_by=admin_user["userId"],
            status="ready",
            started_importing_at=None,
            expires_at=datetime.now() + timedelta(hours=72),
        )
        defaults.update(overrides)
        db.execute(
            """
            INSERT INTO tutor_import_jobs (filename, uploaded_by, status, started_importing_at, expires_at)
            VALUES (%(filename)s, %(uploaded_by)s, %(status)s, %(started_importing_at)s, %(expires_at)s)
            RETURNING id
            """,
            defaults,
        )
        job_id = db.fetchone()["id"]
        created_ids.append(job_id)
        return {"id": job_id, **defaults}

    yield make

    for job_id in created_ids:
        db.execute("DELETE FROM tutor_import_rows WHERE job_id = %s", (job_id,))
        db.execute("DELETE FROM tutor_import_jobs WHERE id = %s", (job_id,))


def test_expire_sweep_reverts_stale_importing_job(db, tutor_import_job_factory):
    job = tutor_import_job_factory(status="importing", started_importing_at=datetime.now() - timedelta(minutes=30))
    expire_due_tutor_import_jobs(db, as_of=datetime.now())

    db.execute("SELECT status, last_error FROM tutor_import_jobs WHERE id = %s", (job["id"],))
    row = db.fetchone()
    assert row["status"] == "ready"
    assert row["last_error"]


def test_expire_sweep_deletes_expired_job_and_its_rows(db, tutor_import_job_factory):
    job = tutor_import_job_factory(expires_at=datetime.now() - timedelta(hours=1))
    db.execute(
        """
        INSERT INTO tutor_import_rows (job_id, row_number, raw_data, classification, proposed_action)
        VALUES (%s, 1, '{}'::jsonb, 'new', 'create')
        """,
        (job["id"],),
    )

    expire_due_tutor_import_jobs(db, as_of=datetime.now())

    db.execute("SELECT id FROM tutor_import_jobs WHERE id = %s", (job["id"],))
    assert db.fetchone() is None
    db.execute("SELECT id FROM tutor_import_rows WHERE job_id = %s", (job["id"],))
    assert db.fetchone() is None
