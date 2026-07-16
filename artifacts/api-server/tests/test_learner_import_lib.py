import os
from datetime import datetime, timedelta

import pytest

from pyapp.csv_utils import CsvParseError, parse_learner_import_csv, sanitize_csv_cell
from pyapp.learner_import_lib import (
    ExistingLearnerIndex,
    classify_row,
    classify_rows,
    expire_due_learner_import_jobs,
    resolve_cohort_names,
    validate_row_fields,
)

VALID_HEADER = "learner_reference,first_name,last_name,apprenticeship_programme,level,start_date,uln,email,employer,planned_end_date,cohort_name"


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def test_parse_valid_csv_returns_snake_case_rows():
    csv_text = f"{VALID_HEADER}\nREF-1,Jane,Doe,Health,3,2026-01-01,,,,,\n"
    rows = parse_learner_import_csv(csv_text.encode("utf-8"))
    assert rows == [
        {
            "learner_reference": "REF-1",
            "first_name": "Jane",
            "last_name": "Doe",
            "apprenticeship_programme": "Health",
            "level": "3",
            "start_date": "2026-01-01",
            "uln": "",
            "email": "",
            "employer": "",
            "planned_end_date": "",
            "cohort_name": "",
        }
    ]


def test_parse_strips_utf8_bom():
    csv_text = f"{VALID_HEADER}\nREF-1,Jane,Doe,Health,3,2026-01-01,,,,,\n"
    raw = b"\xef\xbb\xbf" + csv_text.encode("utf-8")
    rows = parse_learner_import_csv(raw)
    assert rows[0]["learner_reference"] == "REF-1"


def test_parse_rejects_empty_file():
    with pytest.raises(CsvParseError, match="empty"):
        parse_learner_import_csv(b"")


def test_parse_rejects_oversized_file():
    with pytest.raises(CsvParseError, match="maximum size"):
        parse_learner_import_csv(b"x" * 100, max_bytes=10)


def test_parse_rejects_missing_required_column():
    with pytest.raises(CsvParseError, match="Missing required column"):
        parse_learner_import_csv(b"first_name,last_name\nJane,Doe\n")


def test_parse_rejects_unknown_column():
    bad_header = VALID_HEADER + ",not_a_real_column"
    with pytest.raises(CsvParseError, match="Unrecognised column"):
        parse_learner_import_csv(f"{bad_header}\n".encode("utf-8"))


def test_parse_rejects_duplicate_header():
    dup_header = VALID_HEADER + ",learner_reference"
    with pytest.raises(CsvParseError, match="Duplicate column"):
        parse_learner_import_csv(f"{dup_header}\n".encode("utf-8"))


def test_parse_rejects_ragged_row():
    csv_text = f"{VALID_HEADER}\nREF-1,Jane,Doe,Health,3\n"
    with pytest.raises(CsvParseError, match="Row 2"):
        parse_learner_import_csv(csv_text.encode("utf-8"))


def test_parse_rejects_non_utf8_bytes():
    csv_text = f"{VALID_HEADER}\nREF-1,Jane,Doe,Health,3,2026-01-01,,,,,\n"
    raw = csv_text.encode("utf-8") + b"\xff\xfe"
    with pytest.raises(CsvParseError, match="UTF-8"):
        parse_learner_import_csv(raw)


def test_parse_rejects_row_count_over_limit():
    rows = "\n".join(f"REF-{i},Jane,Doe,Health,3,2026-01-01,,,,," for i in range(5))
    csv_text = f"{VALID_HEADER}\n{rows}\n"
    with pytest.raises(CsvParseError, match="maximum of"):
        parse_learner_import_csv(csv_text.encode("utf-8"), max_rows=3)


def test_parse_rejects_field_exceeding_max_length():
    csv_text = f"{VALID_HEADER}\n" + "REF-1," + ("x" * 600) + ",Doe,Health,3,2026-01-01,,,,,\n"
    with pytest.raises(CsvParseError, match="exceeds"):
        parse_learner_import_csv(csv_text.encode("utf-8"))


def test_parse_ignores_blank_trailing_rows():
    csv_text = f"{VALID_HEADER}\nREF-1,Jane,Doe,Health,3,2026-01-01,,,,,\n\n\n"
    rows = parse_learner_import_csv(csv_text.encode("utf-8"))
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# CSV export injection protection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dangerous", ["=SUM(A1:A2)", "+CMD", "-1+2", "@HYPERLINK(1)", "\ttab", "\rcr"])
def test_sanitize_csv_cell_prefixes_formula_trigger_chars(dangerous):
    assert sanitize_csv_cell(dangerous).startswith("'")


def test_sanitize_csv_cell_leaves_normal_values_untouched():
    assert sanitize_csv_cell("Jane Doe") == "Jane Doe"


def test_sanitize_csv_cell_passes_through_none():
    assert sanitize_csv_cell(None) is None


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------

def _valid_row(**overrides) -> dict:
    row = {
        "learner_reference": "REF-1",
        "first_name": "Jane",
        "last_name": "Doe",
        "apprenticeship_programme": "Health",
        "level": "3",
        "start_date": "2026-01-01",
        "uln": "",
        "email": "",
        "employer": "",
        "planned_end_date": "",
        "cohort_name": "",
    }
    row.update(overrides)
    return row


def test_validate_row_fields_flags_missing_required_fields():
    errors = validate_row_fields(_valid_row(first_name=""))
    assert any("first_name" in e for e in errors)


def test_validate_row_fields_flags_bad_date_format():
    errors = validate_row_fields(_valid_row(start_date="01/01/2026"))
    assert any("start_date" in e for e in errors)


def test_validate_row_fields_flags_planned_end_before_start():
    errors = validate_row_fields(_valid_row(start_date="2026-06-01", planned_end_date="2026-01-01"))
    assert any("planned_end_date cannot be before" in e for e in errors)


def test_validate_row_fields_accepts_valid_row():
    assert validate_row_fields(_valid_row()) == []


# ---------------------------------------------------------------------------
# Duplicate classification
# ---------------------------------------------------------------------------

def _index(*learners) -> ExistingLearnerIndex:
    return ExistingLearnerIndex(list(learners))


def _existing(**overrides) -> dict:
    row = {"id": 1, "learnerRef": "EXIST-1", "uln": None, "email": None, "firstName": "Existing", "lastName": "Learner", "startDate": None}
    row.update(overrides)
    return row


def test_classify_row_invalid_when_required_fields_missing():
    result = classify_row(_index(), _valid_row(first_name=""))
    assert result["classification"] == "invalid"
    assert result["proposedAction"] == "blocked"


def test_classify_row_new_when_no_match():
    result = classify_row(_index(), _valid_row())
    assert result["classification"] == "new"
    assert result["proposedAction"] == "create"
    assert result["matchedLearnerId"] is None


def test_classify_row_exact_existing_when_ref_matches():
    existing = _existing(id=42, learnerRef="REF-1")
    result = classify_row(_index(existing), _valid_row(learner_reference="REF-1"))
    assert result["classification"] == "exact_existing"
    assert result["proposedAction"] == "skip"
    assert result["matchedLearnerId"] == 42


def test_classify_row_probable_duplicate_when_uln_matches_different_ref():
    existing = _existing(id=42, learnerRef="OTHER-REF", uln="1111111111")
    result = classify_row(_index(existing), _valid_row(learner_reference="NEW-REF", uln="1111111111"))
    assert result["classification"] == "probable_duplicate"
    assert result["matchedLearnerId"] == 42


def test_classify_row_identifier_conflict_when_ref_and_uln_point_to_different_learners():
    ref_owner = _existing(id=1, learnerRef="REF-1", uln="1111111111")
    uln_owner = _existing(id=2, learnerRef="REF-2", uln="2222222222")
    result = classify_row(_index(ref_owner, uln_owner), _valid_row(learner_reference="REF-1", uln="2222222222"))
    assert result["classification"] == "identifier_conflict"
    assert result["proposedAction"] == "blocked"


def test_classify_row_possible_duplicate_on_single_email_match():
    existing = _existing(id=7, learnerRef="OTHER", email="jane@example.com")
    result = classify_row(_index(existing), _valid_row(email="jane@example.com"))
    assert result["classification"] == "possible_duplicate"
    assert result["matchedLearnerId"] == 7


def test_classify_row_possible_duplicate_on_name_and_close_start_date():
    from datetime import date

    existing = _existing(id=9, learnerRef="OTHER", firstName="Jane", lastName="Doe", startDate=date(2026, 1, 3))
    result = classify_row(_index(existing), _valid_row(first_name="Jane", last_name="Doe", start_date="2026-01-01"))
    assert result["classification"] == "possible_duplicate"
    assert result["matchedLearnerId"] == 9


def test_classify_row_no_possible_duplicate_when_start_dates_far_apart():
    from datetime import date

    existing = _existing(id=9, learnerRef="OTHER", firstName="Jane", lastName="Doe", startDate=date(2026, 6, 1))
    result = classify_row(_index(existing), _valid_row(first_name="Jane", last_name="Doe", start_date="2026-01-01"))
    assert result["classification"] == "new"


def test_classify_row_ambiguous_email_match_is_not_flagged():
    a = _existing(id=1, learnerRef="A", email="shared@example.com")
    b = _existing(id=2, learnerRef="B", email="shared@example.com")
    result = classify_row(_index(a, b), _valid_row(email="shared@example.com"))
    assert result["classification"] == "new"


def test_classify_rows_flags_duplicate_ref_within_same_file(db):
    row1 = _valid_row(learner_reference=f"DUP-{os.urandom(4).hex()}")
    row2 = {**row1, "first_name": "Other"}
    results = classify_rows(db, [row1, row2])
    assert results[0]["classification"] == "new"
    assert results[1]["classification"] == "identifier_conflict"


def test_classify_rows_against_real_db_detects_existing_learner(db, learner_factory):
    existing = learner_factory(learner_ref="REAL-REF")
    results = classify_rows(db, [_valid_row(learner_reference="REAL-REF")])
    assert results[0]["classification"] == "exact_existing"
    assert results[0]["matchedLearnerId"] == existing["id"]


# ---------------------------------------------------------------------------
# Cohort-name resolution
# ---------------------------------------------------------------------------

def test_resolve_cohort_names_matched(db, cohort_factory):
    cohort = cohort_factory(name="Health & Social Care 2026")
    resolved = resolve_cohort_names(db, ["Health & Social Care 2026"])
    assert resolved["Health & Social Care 2026"]["status"] == "matched"
    assert resolved["Health & Social Care 2026"]["cohort"]["id"] == cohort["id"]


def test_resolve_cohort_names_is_case_insensitive(db, cohort_factory):
    cohort_factory(name="Business Admin")
    resolved = resolve_cohort_names(db, ["business admin"])
    assert resolved["business admin"]["status"] == "matched"


def test_resolve_cohort_names_zero_matches(db):
    resolved = resolve_cohort_names(db, ["Nonexistent Cohort Name"])
    assert resolved["Nonexistent Cohort Name"]["status"] == "zero_matches"


def test_resolve_cohort_names_ambiguous_when_multiple_active_cohorts_share_a_name(db, cohort_factory):
    name = "Duplicate Cohort Name"
    cohort_factory(name=name)
    cohort_factory(name=name)
    resolved = resolve_cohort_names(db, [name])
    assert resolved[name]["status"] == "ambiguous"


def test_resolve_cohort_names_inactive(db, cohort_factory):
    name = "Inactive Cohort"
    cohort_factory(name=name, active=False)
    resolved = resolve_cohort_names(db, [name])
    assert resolved[name]["status"] == "inactive"


def test_resolve_cohort_names_ignores_blank_names(db):
    assert resolve_cohort_names(db, ["", "   ", None]) == {}


# ---------------------------------------------------------------------------
# Job expiry / crash-recovery sweep
# ---------------------------------------------------------------------------

@pytest.fixture
def import_job_factory(db, admin_user):
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
            INSERT INTO learner_import_jobs (filename, uploaded_by, status, started_importing_at, expires_at)
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
        db.execute("DELETE FROM learner_import_rows WHERE job_id = %s", (job_id,))
        db.execute("DELETE FROM learner_import_jobs WHERE id = %s", (job_id,))


def test_expire_sweep_reverts_stale_importing_job(db, import_job_factory):
    job = import_job_factory(status="importing", started_importing_at=datetime.now() - timedelta(minutes=30))
    expire_due_learner_import_jobs(db, as_of=datetime.now())

    db.execute("SELECT status, last_error FROM learner_import_jobs WHERE id = %s", (job["id"],))
    row = db.fetchone()
    assert row["status"] == "ready"
    assert row["last_error"]


def test_expire_sweep_leaves_recently_started_importing_job_alone(db, import_job_factory):
    job = import_job_factory(status="importing", started_importing_at=datetime.now() - timedelta(minutes=1))
    expire_due_learner_import_jobs(db, as_of=datetime.now())

    db.execute("SELECT status FROM learner_import_jobs WHERE id = %s", (job["id"],))
    assert db.fetchone()["status"] == "importing"


def test_expire_sweep_deletes_expired_job_and_its_rows(db, import_job_factory):
    job = import_job_factory(expires_at=datetime.now() - timedelta(hours=1))
    db.execute(
        """
        INSERT INTO learner_import_rows (job_id, row_number, raw_data, classification, proposed_action)
        VALUES (%s, 1, '{}'::jsonb, 'new', 'create')
        """,
        (job["id"],),
    )

    expire_due_learner_import_jobs(db, as_of=datetime.now())

    db.execute("SELECT id FROM learner_import_jobs WHERE id = %s", (job["id"],))
    assert db.fetchone() is None
    db.execute("SELECT id FROM learner_import_rows WHERE job_id = %s", (job["id"],))
    assert db.fetchone() is None


def test_expire_sweep_leaves_unexpired_job_alone(db, import_job_factory):
    job = import_job_factory(expires_at=datetime.now() + timedelta(hours=1))
    expire_due_learner_import_jobs(db, as_of=datetime.now())

    db.execute("SELECT id FROM learner_import_jobs WHERE id = %s", (job["id"],))
    assert db.fetchone() is not None
