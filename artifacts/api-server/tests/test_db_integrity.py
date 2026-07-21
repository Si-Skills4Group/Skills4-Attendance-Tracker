"""Phase 10 DB-level integrity controls: CHECK constraints on status
columns that were previously Python-validated only, the partial unique
index closing the users<->tutor TOCTOU race, and the register_version
atomic guard closing the concurrent-save race."""
import datetime
import os

import psycopg
import pytest
from fastapi import HTTPException

from pyapp.routers.attendance import (
    AttendanceRegisterInput,
    RegisterEntryInput,
    save_attendance_register,
)
from pyapp.routers.users import UserLinkTutorInput, link_user_tutor
from pyapp.session_register_lib import bump_register_version


class TestStatusCheckConstraints:
    def test_learner_import_jobs_rejects_an_unknown_status(self, db):
        db.execute(
            "INSERT INTO learner_import_jobs (filename, uploaded_by, expires_at) "
            "VALUES ('x.csv', 1, now() + interval '1 day') RETURNING id"
        )
        job_id = db.fetchone()["id"]
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("UPDATE learner_import_jobs SET status = 'bogus_status' WHERE id = %s", (job_id,))

    def test_tutor_import_jobs_rejects_an_unknown_status(self, db):
        db.execute(
            "INSERT INTO tutor_import_jobs (filename, uploaded_by, expires_at) "
            "VALUES ('x.csv', 1, now() + interval '1 day') RETURNING id"
        )
        job_id = db.fetchone()["id"]
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("UPDATE tutor_import_jobs SET status = 'bogus_status' WHERE id = %s", (job_id,))

    def test_scheduled_allocations_rejects_an_unknown_status(self, db, learner_factory):
        learner = learner_factory()
        db.execute(
            "INSERT INTO scheduled_allocations (learner_id, new_tutor_id, new_cohort_id, effective_date, created_by) "
            "VALUES (%s, NULL, NULL, '2099-01-01', 1) RETURNING id",
            (learner["id"],),
        )
        row_id = db.fetchone()["id"]
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute("UPDATE scheduled_allocations SET status = 'bogus_status' WHERE id = %s", (row_id,))

    def test_known_status_values_are_all_still_accepted(self, db, learner_factory):
        """Guards against the CHECK constraint's allow-list drifting out
        of sync with the literal values the application code actually
        uses -- if any of these ever starts failing, either the app added
        a new status value or the constraint typo'd one."""
        for status in ("uploaded", "classifying", "ready", "importing", "completed", "cancelled"):
            db.execute(
                "INSERT INTO learner_import_jobs (filename, uploaded_by, status, expires_at) "
                "VALUES ('x.csv', 1, %s, now() + interval '1 day')",
                (status,),
            )
        learner = learner_factory()
        for status in ("pending", "applying", "applied", "cancelled"):
            db.execute(
                "INSERT INTO scheduled_allocations (learner_id, new_tutor_id, new_cohort_id, effective_date, created_by, status) "
                "VALUES (%s, NULL, NULL, '2099-01-01', 1, %s)",
                (learner["id"], status),
            )


@pytest.fixture
def user_factory(db):
    created_ids = []

    def make(active: bool = True, tutor_id: int | None = None) -> int:
        suffix = os.urandom(4).hex()
        db.execute(
            "INSERT INTO users (first_name, last_name, email, role, active, tutor_id) "
            "VALUES ('Test', 'User', %s, 'tutor', %s, %s) RETURNING id",
            (f"test-race-{suffix}@example.com", active, tutor_id),
        )
        user_id = db.fetchone()["id"]
        created_ids.append(user_id)
        return user_id

    yield make

    for user_id in created_ids:
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))


class TestUserTutorUniqueIndex:
    def test_index_itself_rejects_a_second_active_link_bypassing_the_app_check(
        self, db, tutor_factory, user_factory,
    ):
        """_ensure_tutor_not_linked_elsewhere (routers/users.py) is a
        check-then-act query, not itself atomic -- this goes straight at
        the raw UPDATE, bypassing that helper entirely, to prove the
        partial unique index is the real backstop for the race window
        between two concurrent requests that both pass the app-level
        check before either writes. tutor_factory() already creates and
        links its own active user to the new tutor -- that's the "first"
        active link; this test only needs to try adding a second."""
        tutor = tutor_factory()
        second_user_id = user_factory()

        with pytest.raises(psycopg.errors.UniqueViolation):
            db.execute("UPDATE users SET tutor_id = %s WHERE id = %s", (tutor["tutorId"], second_user_id))

    def test_the_ordinary_app_level_path_still_gives_a_clean_400(
        self, request_factory, admin_user, tutor_factory, user_factory,
    ):
        tutor = tutor_factory()
        second_user_id = user_factory()

        with pytest.raises(HTTPException) as exc:
            link_user_tutor(second_user_id, UserLinkTutorInput(tutorId=tutor["tutorId"]), request_factory(), admin_user)
        assert exc.value.status_code == 400
        assert "already linked" in str(exc.value.detail)

    def test_an_inactive_user_does_not_block_linking(self, db, request_factory, admin_user, tutor_factory, user_factory):
        """The unique index is partial (WHERE active = true) -- a tutor
        whose only existing link is now inactive must not block a new
        active user from linking to it."""
        tutor = tutor_factory()
        db.execute("UPDATE users SET active = false WHERE tutor_id = %s", (tutor["tutorId"],))
        new_user_id = user_factory()

        result = link_user_tutor(new_user_id, UserLinkTutorInput(tutorId=tutor["tutorId"]), request_factory(), admin_user)
        assert result["tutorId"] == tutor["tutorId"]


class TestRegisterVersionAtomicGuard:
    def test_bump_succeeds_when_version_matches(self, db, cohort_factory, attendance_session_factory, admin_user):
        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        new_version = bump_register_version(db, session["id"], expected_version=1)
        assert new_version == 2

    def test_bump_raises_stale_version_conflict_when_version_does_not_match(
        self, db, cohort_factory, attendance_session_factory, admin_user
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        with pytest.raises(HTTPException) as exc:
            bump_register_version(db, session["id"], expected_version=999)
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "stale_register_version"

    def test_concurrent_saves_the_second_gets_a_clean_conflict_not_a_silent_overwrite(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        """Simulates two requests that both read registerVersion=1 before
        either writes -- the second save must get a real 409, not silently
        overwrite the first save's data."""
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        payload_a = AttendanceRegisterInput(
            registerVersion=1,
            entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
        )
        payload_b = AttendanceRegisterInput(
            registerVersion=1,
            entries=[RegisterEntryInput(learnerId=learner["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)],
        )

        save_attendance_register(session["id"], payload_a, request_factory(), admin_user)
        with pytest.raises(HTTPException) as exc:
            save_attendance_register(session["id"], payload_b, request_factory(), admin_user)
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == "stale_register_version"

        db.execute("SELECT status FROM attendance_records WHERE session_id = %s AND learner_id = %s", (session["id"], learner["id"]))
        assert db.fetchone()["status"] == "present"
