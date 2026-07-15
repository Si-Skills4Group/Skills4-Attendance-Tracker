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
import types

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://s4admin:x4thOI6LuzNQRzSwQxSYK.3v@s4-attendance-pg-gzn5bh.postgres.database.azure.com:5432/attendance_test?sslmode=require",
)
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
            active=True,
        )
        defaults.update(overrides)
        db.execute(
            """
            INSERT INTO cohorts (name, programme, level, tutor_id, delivery_day, session_start_time,
                                  session_end_time, start_date, active)
            VALUES (%(name)s, %(programme)s, %(level)s, %(tutor_id)s, %(delivery_day)s, %(session_start_time)s,
                    %(session_end_time)s, %(start_date)s, %(active)s)
            RETURNING id
            """,
            defaults,
        )
        cohort_id = db.fetchone()["id"]
        created_ids.append(cohort_id)
        return {"id": cohort_id, **defaults}

    yield make

    for cohort_id in created_ids:
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
        )
        defaults.update(overrides)
        defaults.setdefault("uln", None)
        db.execute(
            """
            INSERT INTO learners (learner_ref, uln, first_name, last_name, programme, level, start_date, status,
                                   tutor_id, cohort_id)
            VALUES (%(learner_ref)s, %(uln)s, %(first_name)s, %(last_name)s, %(programme)s, %(level)s, %(start_date)s,
                    %(status)s, %(tutor_id)s, %(cohort_id)s)
            RETURNING id
            """,
            defaults,
        )
        learner_id = db.fetchone()["id"]
        created_ids.append(learner_id)
        return {"id": learner_id, **defaults}

    yield make

    for learner_id in created_ids:
        db.execute("DELETE FROM learners WHERE id = %s", (learner_id,))
