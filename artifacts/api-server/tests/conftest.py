"""Shared test fixtures.

Tests run against a dedicated `attendance_test` database -- a sibling
database on the same Postgres server as production, never the real
`attendance` database. It is bootstrapped with the exact same DDL the
app itself uses (pyapp.bootstrap.bootstrap_database), so schema drift
between "what tests run against" and "what production runs" isn't
possible by construction.

Environment variables must be set before any `pyapp` module is
imported, since pyapp.db reads DATABASE_URL at import time and
pyapp.config validates required Entra settings are present (not that
they're real -- these tests never perform real Entra token
validation, they exercise application logic directly with
dependency-injected fake sessions).
"""

import os
import sys
import types

# No hardcoded credential here -- a real Azure Postgres admin password used to
# live in this file as a fallback default and ended up committed to git
# history. TEST_DATABASE_URL must point at the dedicated attendance_test
# database and is read from the developer's own environment/.env; it is
# never the same variable as DATABASE_URL, so an unset one can't silently
# fall through to pyapp.main's later load_dotenv() and point tests at the
# real production `attendance` database instead.
_test_database_url = os.environ.get("TEST_DATABASE_URL")
if not _test_database_url:
    sys.exit(
        "TEST_DATABASE_URL is not set. Point it at the attendance_test database "
        "(e.g. in artifacts/api-server/.env) before running the test suite -- "
        "see README.md for local setup."
    )
os.environ["DATABASE_URL"] = _test_database_url
os.environ.setdefault("AUTH_MODE", "entra")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("ENTRA_TENANT_ID", "test-tenant-id")
os.environ.setdefault("ENTRA_ALLOWED_TENANT_ID", "test-tenant-id")
os.environ.setdefault("ENTRA_API_CLIENT_ID", "test-api-client-id")
os.environ.setdefault("ENTRA_EXPECTED_AUDIENCE", "test-api-client-id")
os.environ.setdefault("ENTRA_AUTHORITY", "https://login.microsoftonline.com/test-tenant-id/v2.0")
os.environ.setdefault("ENTRA_REQUIRED_SCOPE", "access_as_user")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:20030")
# Force-blank (not setdefault): importing pyapp.main below loads the local
# .env file via python-dotenv, which does NOT override already-set vars --
# without this, a developer's real ADMIN_EMAIL would get picked up and
# bootstrap_database() would silently seed a real admin into the isolated
# test database, breaking tests that assume a known, controlled admin count.
os.environ["ADMIN_EMAIL"] = ""

import pytest
from fastapi.testclient import TestClient

from pyapp.db import get_cursor
from pyapp.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_dependency_overrides_after_test():
    """app is a module-level singleton, so app.dependency_overrides is a
    mutable dict shared across every test's client -- without this, an
    override set via app.dependency_overrides in one test (e.g. a fake
    require_auth) would silently leak into the next test if that test
    forgot its own try/finally cleanup."""
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _clear_rate_limit_attempts_after_test():
    """Many tests across many files reuse the same fixed fake session
    (e.g. {"userId": 1, "role": "admin"} via monkeypatched require_auth)
    against a real, shared Postgres database -- without this, pyapp.
    rate_limit's per-(action, user) budget accumulates real rows across
    the whole test run and starts returning 429s to unrelated tests that
    happen to share that fake user id, well before any single test comes
    close to its own budget."""
    yield
    with get_cursor() as cur:
        cur.execute("DELETE FROM rate_limit_attempts")


class FakeRequest:
    """Minimal stand-in for fastapi.Request, sufficient for write_audit_log
    and for calling router functions directly as plain Python functions."""

    def __init__(self, session: dict | None = None, path: str = "/api/test"):
        self.state = types.SimpleNamespace(session=session or {})
        self.headers: dict[str, str] = {}
        self.client = types.SimpleNamespace(host="127.0.0.1")
        self.url = types.SimpleNamespace(path=path)


@pytest.fixture
def db():
    with get_cursor() as cur:
        yield cur


@pytest.fixture(scope="session", autouse=True)
def _learner_progress_table_for_tests():
    """public.learner_progress does not exist in attendance_test -- it's
    populated by a separate, already-deployed sync service that only ever
    targets the production `attendance` database (confirmed by inspection;
    see pyapp/bud_progress.py's module docstring). This creates a matching
    throwaway table shape *in attendance_test only*, session-scoped so any
    test that happens to exercise a code path touching learner_progress
    (e.g. dashboard.py's low-attendance list, which looks up Bud context for
    every flagged learner) doesn't hit UndefinedTable, regardless of test
    file/order. Never touches pyapp/bootstrap.py or the real production data."""
    with get_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.learner_progress (
                learning_plan_id text NOT NULL,
                apprentice_id text NOT NULL,
                learner_name text,
                learner_forename text,
                learner_surname text,
                learner_email text,
                learner_mobile text,
                learner_reference text,
                unique_learner_number text,
                start_date date,
                tutor_name text,
                tutor_id text,
                last_submission_date timestamptz,
                last_submission_by_learner timestamptz,
                last_completed_activity date,
                activity_progress numeric,
                activities_overdue integer,
                learning_plan_url text,
                programme_name text,
                status_desc text,
                synced_at timestamptz
            )
            """
        )
    yield


@pytest.fixture
def request_factory():
    return FakeRequest


@pytest.fixture
def admin_user(db):
    db.execute(
        "INSERT INTO users (first_name, last_name, email, role, active) "
        "VALUES ('Test', 'Admin', %s, 'admin', true) RETURNING id",
        (f"test-admin-{os.urandom(4).hex()}@example.com",),
    )
    user_id = db.fetchone()["id"]
    yield {"userId": user_id, "role": "admin", "tutorId": None}
    db.execute("DELETE FROM users WHERE id = %s", (user_id,))


@pytest.fixture
def tutor_factory(db):
    created_user_ids = []
    created_tutor_ids = []

    def make(active: bool = True) -> dict:
        suffix = os.urandom(4).hex()
        db.execute(
            "INSERT INTO users (first_name, last_name, email, role, active) "
            "VALUES ('Test', 'Tutor', %s, 'tutor', %s) RETURNING id",
            (f"test-tutor-{suffix}@example.com", active),
        )
        user_id = db.fetchone()["id"]
        created_user_ids.append(user_id)

        db.execute(
            "INSERT INTO tutors (user_id, first_name, last_name, email, active) "
            "VALUES (%s, 'Test', 'Tutor', %s, %s) RETURNING id",
            (user_id, f"test-tutor-{suffix}@example.com", active),
        )
        tutor_id = db.fetchone()["id"]
        created_tutor_ids.append(tutor_id)

        db.execute("UPDATE users SET tutor_id = %s WHERE id = %s", (tutor_id, user_id))

        return {"tutorId": tutor_id, "userId": user_id, "session": {"userId": user_id, "role": "tutor", "tutorId": tutor_id}}

    yield make

    for tutor_id in created_tutor_ids:
        db.execute("DELETE FROM cohorts WHERE tutor_id = %s", (tutor_id,))
        db.execute("DELETE FROM tutors WHERE id = %s", (tutor_id,))
    for user_id in created_user_ids:
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))


@pytest.fixture
def cohort_factory(db):
    created_ids = []

    def make(**overrides) -> dict:
        defaults = dict(
            name=f"Test Cohort {os.urandom(4).hex()}",
            programme="Test Programme",
            level="3",
            tutor_id=None,
            delivery_day="monday",
            session_start_time="09:00",
            session_end_time="16:00",
            start_date="2026-01-01",
            end_date=None,
            active=True,
        )
        defaults.update(overrides)
        db.execute(
            """
            INSERT INTO cohorts (name, programme, level, tutor_id, delivery_day, session_start_time,
                                  session_end_time, start_date, end_date, active)
            VALUES (%(name)s, %(programme)s, %(level)s, %(tutor_id)s, %(delivery_day)s, %(session_start_time)s,
                    %(session_end_time)s, %(start_date)s, %(end_date)s, %(active)s)
            RETURNING id
            """,
            defaults,
        )
        cohort_id = db.fetchone()["id"]
        created_ids.append(cohort_id)
        return {"id": cohort_id, **defaults}

    yield make

    for cohort_id in created_ids:
        db.execute("DELETE FROM bud_cohort_mapping WHERE cohort_id = %s", (cohort_id,))
        db.execute("DELETE FROM cohorts WHERE id = %s", (cohort_id,))


@pytest.fixture
def learner_factory(db):
    created_ids = []

    def make(**overrides) -> dict:
        defaults = dict(
            learner_ref=f"TEST-{os.urandom(4).hex()}",
            first_name="Test",
            last_name="Learner",
            programme="Test Programme",
            level="3",
            start_date="2026-01-01",
            status="active",
            tutor_id=None,
            cohort_id=None,
            withdrawal_date=None,
            actual_end_date=None,
        )
        defaults.update(overrides)
        defaults.setdefault("uln", None)
        db.execute(
            """
            INSERT INTO learners (learner_ref, uln, first_name, last_name, programme, level, start_date, status,
                                   tutor_id, cohort_id, withdrawal_date, actual_end_date)
            VALUES (%(learner_ref)s, %(uln)s, %(first_name)s, %(last_name)s, %(programme)s, %(level)s, %(start_date)s,
                    %(status)s, %(tutor_id)s, %(cohort_id)s, %(withdrawal_date)s, %(actual_end_date)s)
            RETURNING id
            """,
            defaults,
        )
        learner_id = db.fetchone()["id"]
        created_ids.append(learner_id)
        return {"id": learner_id, **defaults}

    yield make

    for learner_id in created_ids:
        # No FK constraints exist in this schema (confirmed in bootstrap.py),
        # so these rows would otherwise silently survive the learner's
        # deletion and contaminate later test runs -- as happened once
        # before with a stray ULN row created outside a factory.
        db.execute("DELETE FROM attendance_records WHERE learner_id = %s", (learner_id,))
        db.execute("DELETE FROM scheduled_allocations WHERE learner_id = %s", (learner_id,))
        db.execute("DELETE FROM learner_allocation_history WHERE learner_id = %s", (learner_id,))
        db.execute("DELETE FROM bud_learner_link WHERE internal_learner_id = %s", (learner_id,))
        db.execute("DELETE FROM learners WHERE id = %s", (learner_id,))


@pytest.fixture
def bud_row_factory(db):
    """Seeds a row into the throwaway public.learner_progress table (see
    _learner_progress_table_for_tests) shaped like a real Bud sync row.
    Returns the row re-read in the same camelCase shape classify_row/
    run_preview actually consume (mirroring _fetch_bud_rows's own SELECT,
    but deliberately NOT calling that function directly and NOT filtering
    by status_desc) so tests can pass the return value straight into
    classify_row without a separate re-fetch step, and so seeding a
    non-'In Progress' row for status-eligibility tests still returns a
    usable row even though _fetch_bud_rows itself would exclude it."""
    created_plan_ids = []

    def make(**overrides) -> dict:
        suffix = os.urandom(4).hex()
        defaults = dict(
            learning_plan_id=f"TEST-PLAN-{suffix}",
            apprentice_id=f"TEST-APP-{suffix}",
            learner_forename="Bud",
            learner_surname="Learner",
            learner_email=f"bud-learner-{suffix}@example.com",
            learner_mobile=None,
            learner_reference=None,
            unique_learner_number=f"ULN{suffix}",
            start_date="2026-02-01",
            tutor_name="Bud Tutor",
            tutor_id=None,
            programme_name="Test Programme",
            status_desc="In Progress",
            learning_plan_url=None,
            synced_at="2026-02-01T10:00:00Z",
        )
        defaults.update(overrides)
        columns = ", ".join(defaults.keys())
        placeholders = ", ".join(f"%({k})s" for k in defaults)
        db.execute(f"INSERT INTO public.learner_progress ({columns}) VALUES ({placeholders})", defaults)
        created_plan_ids.append(defaults["learning_plan_id"])
        db.execute(
            """
            SELECT learning_plan_id AS "learningPlanId", apprentice_id AS "apprenticeId",
                   learner_forename AS "learnerForename", learner_surname AS "learnerSurname",
                   learner_email AS "learnerEmail", learner_mobile AS "learnerMobile",
                   learner_reference AS "learnerReference", unique_learner_number AS "uln",
                   start_date AS "startDate", tutor_name AS "tutorName", tutor_id AS "budTutorId",
                   programme_name AS "programmeName", status_desc AS "statusDesc",
                   learning_plan_url AS "learningPlanUrl", synced_at AS "syncedAt"
            FROM public.learner_progress WHERE learning_plan_id = %s
            """,
            (defaults["learning_plan_id"],),
        )
        return db.fetchone()

    yield make

    for plan_id in created_plan_ids:
        db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id = %s", (plan_id,))


@pytest.fixture
def baseline_factory(db, admin_user):
    """Establishes a Bud sync trial baseline directly via bud_sync_lib
    (bypassing the HTTP layer, matching the rest of this file's factory
    style). Baselines are a global singleton (at most one active row) --
    this wipes any leftover baseline state first so tests never fail due to
    a previous run's crash-before-teardown, then cleans up after itself."""
    from pyapp.bud_sync_lib import establish_baseline

    db.execute("DELETE FROM bud_sync_item")
    db.execute("DELETE FROM bud_sync_job")
    db.execute("DELETE FROM bud_sync_baseline_snapshot")
    db.execute("DELETE FROM bud_sync_baseline")
    created_ids = []

    def make(notes: str | None = None) -> dict:
        request = FakeRequest(admin_user)
        baseline = establish_baseline(db, request, admin_user, notes=notes)
        created_ids.append(baseline["id"])
        return baseline

    yield make

    db.execute("DELETE FROM bud_sync_item")
    db.execute("DELETE FROM bud_sync_job")
    db.execute("DELETE FROM bud_sync_baseline_snapshot")
    db.execute("DELETE FROM bud_sync_baseline")


@pytest.fixture
def attendance_session_factory(db):
    created_ids = []

    def make(**overrides) -> dict:
        defaults = dict(
            cohort_id=None,
            session_date="2026-01-01",
            planned_start_time="09:00",
            planned_end_time="16:00",
            planned_duration_hours=7,
            title=None,
            notes=None,
            created_by=None,
        )
        defaults.update(overrides)
        db.execute(
            """
            INSERT INTO attendance_sessions
                (cohort_id, session_date, planned_start_time, planned_end_time, planned_duration_hours,
                 title, notes, created_by)
            VALUES (%(cohort_id)s, %(session_date)s, %(planned_start_time)s, %(planned_end_time)s,
                    %(planned_duration_hours)s, %(title)s, %(notes)s, %(created_by)s)
            RETURNING id
            """,
            defaults,
        )
        session_id = db.fetchone()["id"]
        created_ids.append(session_id)
        return {"id": session_id, **defaults}

    yield make

    for session_id in created_ids:
        db.execute("DELETE FROM attendance_records WHERE session_id = %s", (session_id,))
        db.execute("DELETE FROM attendance_sessions WHERE id = %s", (session_id,))
