import os

import pytest
from fastapi import HTTPException

from pyapp.learner_import_lib import (
    cancel_import_job,
    confirm_import_job,
    create_import_job,
    get_import_job,
    list_import_job_rows,
    resolve_import_row,
)


def _row(**overrides) -> dict:
    # first_name/last_name deliberately include a random suffix, not just
    # the ref -- this codebase's weak-duplicate heuristic matches on
    # name+start_date, so a shared literal "Jane Doe" across many tests
    # would make unrelated rows falsely match each other as
    # possible_duplicate (and, worse, match real leftover rows if any
    # earlier test's cleanup didn't run, as happened once during this very
    # test file's development).
    suffix = os.urandom(4).hex()
    row = {
        "learner_reference": f"IMP-{suffix}",
        "uln": "",
        "first_name": "Jane",
        "last_name": f"Doe-{suffix}",
        "email": "",
        "employer": "",
        "apprenticeship_programme": "Health",
        "level": "3",
        "start_date": "2026-01-01",
        "planned_end_date": "",
        "cohort_name": "",
    }
    row.update(overrides)
    return row


@pytest.fixture
def import_job_cleanup(db):
    created_ids = []
    yield created_ids
    for job_id in created_ids:
        db.execute("DELETE FROM learner_import_rows WHERE job_id = %s", (job_id,))
        db.execute("DELETE FROM learner_import_jobs WHERE id = %s", (job_id,))


@pytest.fixture
def learner_ref_cleanup(db):
    """Deletes any learner (plus dependent rows) created by ref during a
    test, in fixture teardown -- unlike a manual delete placed after an
    assertion, this always runs even if the test fails, so a failing
    assertion can never leave a real row behind to contaminate later test
    runs (this happened for real during development of this file: a failed
    test's post-assert cleanup never ran, leaving a stray learner that
    later runs' weak-duplicate matching then falsely matched against)."""
    refs = []
    yield refs
    for ref in refs:
        db.execute("SELECT id FROM learners WHERE learner_ref = %s", (ref,))
        row = db.fetchone()
        if not row:
            continue
        learner_id = row["id"]
        db.execute("DELETE FROM attendance_records WHERE learner_id = %s", (learner_id,))
        db.execute("DELETE FROM scheduled_allocations WHERE learner_id = %s", (learner_id,))
        db.execute("DELETE FROM learner_allocation_history WHERE learner_id = %s", (learner_id,))
        db.execute("DELETE FROM learners WHERE id = %s", (learner_id,))


# ---------------------------------------------------------------------------
# Job creation / listing / resolution / cancel
# ---------------------------------------------------------------------------

def test_create_import_job_persists_header_counts_and_rows(db, admin_user, import_job_cleanup):
    rows = [_row(), _row(learner_reference="", first_name="")]  # one valid "new", one invalid
    job = create_import_job(db, "learners.csv", admin_user["userId"], rows)
    import_job_cleanup.append(job["id"])

    assert job["status"] == "ready"
    assert job["totalRows"] == 2
    assert job["newCount"] == 1
    assert job["invalidCount"] == 1

    listing = list_import_job_rows(db, job["id"])
    assert listing["total"] == 2
    assert {r["classification"] for r in listing["items"]} == {"new", "invalid"}


def test_list_import_job_rows_paginates_and_filters(db, admin_user, import_job_cleanup):
    rows = [_row() for _ in range(3)]
    job = create_import_job(db, "learners.csv", admin_user["userId"], rows)
    import_job_cleanup.append(job["id"])

    page1 = list_import_job_rows(db, job["id"], page=1, page_size=2)
    assert len(page1["items"]) == 2
    assert page1["total"] == 3

    filtered = list_import_job_rows(db, job["id"], classification="new")
    assert filtered["total"] == 3


def test_get_import_job_404_for_unknown_id(db):
    with pytest.raises(HTTPException) as exc:
        get_import_job(db, 999_999_999)
    assert exc.value.status_code == 404


def test_resolve_import_row_accepts_update_on_exact_existing_row(db, admin_user, learner_factory, import_job_cleanup):
    existing = learner_factory(learner_ref="RESOLVE-1")
    job = create_import_job(db, "learners.csv", admin_user["userId"], [_row(learner_reference="RESOLVE-1")])
    import_job_cleanup.append(job["id"])

    listing = list_import_job_rows(db, job["id"])
    row = listing["items"][0]
    assert row["classification"] == "exact_existing"
    assert row["matchedLearnerId"] == existing["id"]

    resolved = resolve_import_row(db, job["id"], row["id"], "update", admin_user["userId"])
    assert resolved["resolution"] == "update"


def test_resolve_import_row_rejects_new_classification(db, admin_user, import_job_cleanup):
    job = create_import_job(db, "learners.csv", admin_user["userId"], [_row()])
    import_job_cleanup.append(job["id"])
    row = list_import_job_rows(db, job["id"])["items"][0]

    with pytest.raises(HTTPException) as exc:
        resolve_import_row(db, job["id"], row["id"], "update", admin_user["userId"])
    assert exc.value.status_code == 400


def test_resolve_import_row_rejects_blocked_row(db, admin_user, import_job_cleanup):
    job = create_import_job(db, "learners.csv", admin_user["userId"], [_row(first_name="")])
    import_job_cleanup.append(job["id"])
    row = list_import_job_rows(db, job["id"])["items"][0]
    assert row["proposedAction"] == "blocked"

    with pytest.raises(HTTPException) as exc:
        resolve_import_row(db, job["id"], row["id"], "skip", admin_user["userId"])
    assert exc.value.status_code == 400


def test_resolve_import_row_rejects_invalid_resolution_value(db, admin_user, learner_factory, import_job_cleanup):
    learner_factory(learner_ref="RESOLVE-BAD")
    job = create_import_job(db, "learners.csv", admin_user["userId"], [_row(learner_reference="RESOLVE-BAD")])
    import_job_cleanup.append(job["id"])
    row = list_import_job_rows(db, job["id"])["items"][0]

    with pytest.raises(HTTPException) as exc:
        resolve_import_row(db, job["id"], row["id"], "delete", admin_user["userId"])
    assert exc.value.status_code == 400


def test_cancel_import_job_marks_cancelled(db, admin_user, import_job_cleanup):
    job = create_import_job(db, "learners.csv", admin_user["userId"], [_row()])
    import_job_cleanup.append(job["id"])

    cancelled = cancel_import_job(db, job["id"])
    assert cancelled["status"] == "cancelled"


def test_cancel_import_job_rejects_already_completed(db, admin_user, request_factory, import_job_cleanup, learner_ref_cleanup):
    row = _row()
    learner_ref_cleanup.append(row["learner_reference"])
    job = create_import_job(db, "learners.csv", admin_user["userId"], [row])
    import_job_cleanup.append(job["id"])
    confirm_import_job(db, job["id"], request_factory(), admin_user)

    with pytest.raises(HTTPException) as exc:
        cancel_import_job(db, job["id"])
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Confirm: create / update / idempotency / rollback-on-failure
# ---------------------------------------------------------------------------

def test_confirm_creates_new_learner_and_is_audited(db, admin_user, request_factory, import_job_cleanup, learner_ref_cleanup):
    ref = f"CONFIRM-{os.urandom(4).hex()}"
    learner_ref_cleanup.append(ref)
    job = create_import_job(db, "learners.csv", admin_user["userId"], [_row(learner_reference=ref)])
    import_job_cleanup.append(job["id"])

    summary = confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert summary["created"] == 1

    db.execute("SELECT id FROM learners WHERE learner_ref = %s", (ref,))
    learner = db.fetchone()
    assert learner is not None

    db.execute(
        "SELECT id FROM audit_logs WHERE entity_type = 'learner' AND entity_id = %s AND action = 'create'",
        (learner["id"],),
    )
    assert db.fetchone() is not None

    refreshed = get_import_job(db, job["id"])
    assert refreshed["status"] == "completed"
    assert refreshed["resultSummary"]["created"] == 1


def test_confirm_updates_existing_learner_without_touching_allocation_fields(db, admin_user, request_factory, learner_factory, import_job_cleanup):
    existing = learner_factory(learner_ref="UPD-1", email=None)
    job = create_import_job(
        db, "learners.csv", admin_user["userId"], [_row(learner_reference="UPD-1", email="new@example.com")]
    )
    import_job_cleanup.append(job["id"])
    row = list_import_job_rows(db, job["id"])["items"][0]
    resolve_import_row(db, job["id"], row["id"], "update", admin_user["userId"])

    summary = confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert summary["updated"] == 1

    db.execute("SELECT email, tutor_id, cohort_id FROM learners WHERE id = %s", (existing["id"],))
    updated = db.fetchone()
    assert updated["email"] == "new@example.com"
    assert updated["tutor_id"] is None
    assert updated["cohort_id"] is None


def test_confirm_default_resolution_skips_duplicate_row(db, admin_user, request_factory, learner_factory, import_job_cleanup):
    existing = learner_factory(learner_ref="SKIP-1", first_name="Original")
    job = create_import_job(
        db, "learners.csv", admin_user["userId"], [_row(learner_reference="SKIP-1", first_name="Changed")]
    )
    import_job_cleanup.append(job["id"])

    summary = confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert summary["skipped"] == 1

    db.execute("SELECT first_name FROM learners WHERE id = %s", (existing["id"],))
    assert db.fetchone()["first_name"] == "Original"


def test_confirm_is_idempotent_on_double_confirm(db, admin_user, request_factory, import_job_cleanup, learner_ref_cleanup):
    ref = f"IDEMPOTENT-{os.urandom(4).hex()}"
    learner_ref_cleanup.append(ref)
    job = create_import_job(db, "learners.csv", admin_user["userId"], [_row(learner_reference=ref)])
    import_job_cleanup.append(job["id"])

    first = confirm_import_job(db, job["id"], request_factory(), admin_user)
    second = confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert first == second

    db.execute("SELECT count(*)::int AS count FROM learners WHERE learner_ref = %s", (ref,))
    assert db.fetchone()["count"] == 1


def test_confirm_rejects_job_not_in_ready_state(db, admin_user, request_factory, import_job_cleanup):
    job = create_import_job(db, "learners.csv", admin_user["userId"], [_row()])
    import_job_cleanup.append(job["id"])
    db.execute("UPDATE learner_import_jobs SET status = 'uploaded' WHERE id = %s", (job["id"],))

    with pytest.raises(HTTPException) as exc:
        confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert exc.value.status_code == 409


def test_confirm_rolls_back_entire_job_when_one_row_fails(db, admin_user, request_factory, import_job_cleanup, learner_ref_cleanup):
    ok_ref = f"ROLLBACK-OK-{os.urandom(4).hex()}"
    race_ref = f"ROLLBACK-RACE-{os.urandom(4).hex()}"
    learner_ref_cleanup.extend([ok_ref, race_ref])
    job = create_import_job(
        db, "learners.csv", admin_user["userId"], [_row(learner_reference=ok_ref), _row(learner_reference=race_ref)]
    )
    import_job_cleanup.append(job["id"])

    # Simulate a race: another process creates the second row's learner
    # reference *after* classification but before confirm.
    db.execute(
        """
        INSERT INTO learners (learner_ref, first_name, last_name, programme, level, start_date, status)
        VALUES (%s, 'Race', 'Winner', 'Health', '3', '2026-01-01', 'active')
        """,
        (race_ref,),
    )

    with pytest.raises(HTTPException):
        confirm_import_job(db, job["id"], request_factory(), admin_user)

    db.execute("SELECT count(*)::int AS count FROM learners WHERE learner_ref = %s", (ok_ref,))
    assert db.fetchone()["count"] == 0  # rolled back, not left half-imported

    refreshed = get_import_job(db, job["id"])
    assert refreshed["status"] == "ready"
    assert refreshed["lastError"]


def test_confirm_can_be_retried_after_a_rolled_back_failure(db, admin_user, request_factory, import_job_cleanup, learner_ref_cleanup):
    ok_ref = f"RETRY-OK-{os.urandom(4).hex()}"
    race_ref = f"RETRY-RACE-{os.urandom(4).hex()}"
    learner_ref_cleanup.extend([ok_ref, race_ref])
    job = create_import_job(
        db, "learners.csv", admin_user["userId"], [_row(learner_reference=ok_ref), _row(learner_reference=race_ref)]
    )
    import_job_cleanup.append(job["id"])

    db.execute(
        """
        INSERT INTO learners (learner_ref, first_name, last_name, programme, level, start_date, status)
        VALUES (%s, 'Race', 'Winner', 'Health', '3', '2026-01-01', 'active')
        """,
        (race_ref,),
    )
    with pytest.raises(HTTPException):
        confirm_import_job(db, job["id"], request_factory(), admin_user)

    # Reclassify would normally happen via a fresh upload; here we simulate
    # the admin resolving the now-duplicate row to "skip" and retrying.
    listing = list_import_job_rows(db, job["id"])
    race_row = next(r for r in listing["items"] if r["rawData"]["learner_reference"] == race_ref)
    db.execute(
        "UPDATE learner_import_rows SET classification = 'exact_existing', proposed_action = 'skip' WHERE id = %s",
        (race_row["id"],),
    )

    summary = confirm_import_job(db, job["id"], request_factory(), admin_user)
    assert summary["created"] == 1
    assert summary["skipped"] == 1

    db.execute("SELECT count(*)::int AS count FROM learners WHERE learner_ref = %s", (ok_ref,))
    assert db.fetchone()["count"] == 1


# ---------------------------------------------------------------------------
# Allocation integration
# ---------------------------------------------------------------------------

def test_confirm_allocates_new_learner_to_matched_active_cohort(
    db, admin_user, request_factory, cohort_factory, import_job_cleanup, learner_ref_cleanup
):
    cohort = cohort_factory(name=f"Import Allocation Cohort {os.urandom(4).hex()}")
    ref = f"ALLOC-{os.urandom(4).hex()}"
    learner_ref_cleanup.append(ref)
    job = create_import_job(
        db, "learners.csv", admin_user["userId"], [_row(learner_reference=ref, cohort_name=cohort["name"])]
    )
    import_job_cleanup.append(job["id"])

    confirm_import_job(db, job["id"], request_factory(), admin_user)

    db.execute("SELECT id, cohort_id FROM learners WHERE learner_ref = %s", (ref,))
    learner = db.fetchone()
    assert learner["cohort_id"] == cohort["id"]

    db.execute("SELECT count(*)::int AS count FROM learner_allocation_history WHERE learner_id = %s", (learner["id"],))
    assert db.fetchone()["count"] == 1


def test_confirm_never_transfers_an_already_allocated_learner_on_update(
    db, admin_user, request_factory, learner_factory, tutor_factory, cohort_factory, import_job_cleanup
):
    tutor = tutor_factory()
    original_cohort = cohort_factory(tutor_id=tutor["tutorId"])
    other_cohort = cohort_factory(name="Should Not Be Allocated Here")
    existing = learner_factory(learner_ref="ALLOC-UPD-1", tutor_id=tutor["tutorId"], cohort_id=original_cohort["id"])

    job = create_import_job(
        db,
        "learners.csv",
        admin_user["userId"],
        [_row(learner_reference="ALLOC-UPD-1", cohort_name="Should Not Be Allocated Here")],
    )
    import_job_cleanup.append(job["id"])
    row = list_import_job_rows(db, job["id"])["items"][0]
    resolve_import_row(db, job["id"], row["id"], "update", admin_user["userId"])

    confirm_import_job(db, job["id"], request_factory(), admin_user)

    db.execute("SELECT tutor_id, cohort_id FROM learners WHERE id = %s", (existing["id"],))
    unchanged = db.fetchone()
    assert unchanged["cohort_id"] == original_cohort["id"]
    assert unchanged["tutor_id"] == tutor["tutorId"]

    db.execute("SELECT count(*)::int AS count FROM learner_allocation_history WHERE learner_id = %s", (existing["id"],))
    assert db.fetchone()["count"] == 0


# ---------------------------------------------------------------------------
# Historical attendance immutability (non-negotiable regression check)
# ---------------------------------------------------------------------------

def test_confirm_update_never_touches_attendance_or_allocation_history(
    db,
    admin_user,
    request_factory,
    learner_factory,
    tutor_factory,
    cohort_factory,
    attendance_session_factory,
    import_job_cleanup,
):
    tutor = tutor_factory()
    cohort = cohort_factory(tutor_id=tutor["tutorId"])
    learner = learner_factory(learner_ref="HIST-1", tutor_id=tutor["tutorId"], cohort_id=cohort["id"])
    session_row = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
    db.execute(
        """
        INSERT INTO attendance_records (session_id, learner_id, status, hours_attended, minutes_late)
        VALUES (%s, %s, 'present', 6, 0)
        """,
        (session_row["id"], learner["id"]),
    )

    db.execute("SELECT status, hours_attended FROM attendance_records WHERE session_id = %s AND learner_id = %s", (session_row["id"], learner["id"]))
    before_record = dict(db.fetchone())
    db.execute("SELECT cohort_id FROM attendance_sessions WHERE id = %s", (session_row["id"],))
    before_session_cohort = db.fetchone()["cohort_id"]

    job = create_import_job(
        db, "learners.csv", admin_user["userId"], [_row(learner_reference="HIST-1", email="updated@example.com")]
    )
    import_job_cleanup.append(job["id"])
    row = list_import_job_rows(db, job["id"])["items"][0]
    resolve_import_row(db, job["id"], row["id"], "update", admin_user["userId"])
    confirm_import_job(db, job["id"], request_factory(), admin_user)

    db.execute("SELECT status, hours_attended FROM attendance_records WHERE session_id = %s AND learner_id = %s", (session_row["id"], learner["id"]))
    after_record = dict(db.fetchone())
    assert after_record == before_record

    db.execute("SELECT cohort_id FROM attendance_sessions WHERE id = %s", (session_row["id"],))
    assert db.fetchone()["cohort_id"] == before_session_cohort

    db.execute("SELECT count(*)::int AS count FROM learner_allocation_history WHERE learner_id = %s", (learner["id"],))
    assert db.fetchone()["count"] == 0

    db.execute("SELECT email FROM learners WHERE id = %s", (learner["id"],))
    assert db.fetchone()["email"] == "updated@example.com"
