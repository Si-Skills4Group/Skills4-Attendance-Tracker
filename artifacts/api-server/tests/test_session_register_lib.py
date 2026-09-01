import datetime

import pytest
from fastapi import HTTPException

from pyapp.session_register_lib import (
    apply_register_refresh,
    cancel_session,
    compute_register_refresh,
    ensure_expected_learners_snapshot,
    find_duplicate_session,
    session_date_outside_cohort_range,
)


class TestEnsureExpectedLearnersSnapshot:
    def test_creates_rows_for_eligible_learners(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", created_by=admin_user["userId"]
        )

        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 2, 1))

        db.execute("SELECT learner_id FROM session_expected_learners WHERE session_id = %s", (session["id"],))
        assert {r["learner_id"] for r in db.fetchall()} == {learner["id"]}

    def test_is_idempotent_and_does_not_duplicate_rows(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", created_by=admin_user["userId"]
        )

        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 2, 1))
        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 2, 1))

        db.execute("SELECT count(*)::int AS count FROM session_expected_learners WHERE session_id = %s", (session["id"],))
        assert db.fetchone()["count"] == 1

    def test_zero_eligible_learners_is_not_mistaken_for_ungenerated_on_a_later_call(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        """A session generated when the cohort had no eligible learners must
        stay frozen at zero, even if a learner who would now resolve as
        eligible is added afterwards -- only a controlled refresh should
        ever change an already-generated register."""
        cohort = cohort_factory()
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", created_by=admin_user["userId"]
        )

        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 2, 1))

        learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 2, 1))

        db.execute("SELECT count(*)::int AS count FROM session_expected_learners WHERE session_id = %s", (session["id"],))
        assert db.fetchone()["count"] == 0


class TestFindDuplicateSession:
    def test_matches_same_cohort_date_and_start_time(
        self, db, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", planned_start_time="09:00",
            created_by=admin_user["userId"],
        )

        result = find_duplicate_session(db, cohort["id"], datetime.date(2026, 2, 1), "09:00")
        assert result is not None

    def test_does_not_match_a_different_start_time_on_the_same_date(
        self, db, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-02-01", planned_start_time="09:00",
            created_by=admin_user["userId"],
        )

        result = find_duplicate_session(db, cohort["id"], datetime.date(2026, 2, 1), "13:00")
        assert result is None

    def test_does_not_match_a_different_cohort(
        self, db, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort_a = cohort_factory()
        cohort_b = cohort_factory()
        attendance_session_factory(
            cohort_id=cohort_a["id"], session_date="2026-02-01", planned_start_time="09:00",
            created_by=admin_user["userId"],
        )

        result = find_duplicate_session(db, cohort_b["id"], datetime.date(2026, 2, 1), "09:00")
        assert result is None


class TestSessionDateOutsideCohortRange:
    def test_before_start_date_is_outside(self):
        cohort = {"startDate": datetime.date(2026, 2, 1), "endDate": None}
        assert session_date_outside_cohort_range(cohort, datetime.date(2026, 1, 31)) is True

    def test_on_start_date_is_inside(self):
        cohort = {"startDate": datetime.date(2026, 2, 1), "endDate": None}
        assert session_date_outside_cohort_range(cohort, datetime.date(2026, 2, 1)) is False

    def test_after_end_date_is_outside(self):
        cohort = {"startDate": datetime.date(2026, 1, 1), "endDate": datetime.date(2026, 6, 30)}
        assert session_date_outside_cohort_range(cohort, datetime.date(2026, 7, 1)) is True

    def test_on_end_date_is_inside(self):
        cohort = {"startDate": datetime.date(2026, 1, 1), "endDate": datetime.date(2026, 6, 30)}
        assert session_date_outside_cohort_range(cohort, datetime.date(2026, 6, 30)) is False

    def test_null_end_date_never_excludes_a_future_date(self):
        cohort = {"startDate": datetime.date(2026, 1, 1), "endDate": None}
        assert session_date_outside_cohort_range(cohort, datetime.date(2099, 1, 1)) is False


class TestRegisterRefresh:
    def _session_row(self, session, cohort_id, session_date):
        return {"id": session["id"], "cohortId": cohort_id, "sessionDate": session_date}

    def test_diff_reports_learners_to_add_and_remove(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        stays = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        leaves = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))

        # Snapshot generated when both were eligible.
        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))

        # "leaves" withdraws before the session date; a fresh learner joins.
        db.execute(
            "UPDATE learners SET status = 'withdrawn', withdrawal_date = %s WHERE id = %s",
            ("2026-03-01", leaves["id"]),
        )
        joins = learner_factory(cohort_id=cohort["id"], start_date="2026-04-01")

        diff = compute_register_refresh(db, session_row)
        assert {r["learnerId"] for r in diff["toAdd"]} == {joins["id"]}
        assert {r["learnerId"] for r in diff["toRemove"]} == {leaves["id"]}
        assert diff["blocked"] == []
        assert stays["id"] not in {r["learnerId"] for r in diff["toAdd"]} | {r["learnerId"] for r in diff["toRemove"]}

    def test_diff_lists_are_sorted_by_first_name(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))
        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))

        carol = learner_factory(cohort_id=cohort["id"], first_name="Carol", last_name="Adams", start_date="2026-04-01")
        alice = learner_factory(cohort_id=cohort["id"], first_name="Alice", last_name="Zephyr", start_date="2026-04-01")

        diff = compute_register_refresh(db, session_row)
        assert [r["learnerId"] for r in diff["toAdd"]] == [alice["id"], carol["id"]]

    def test_learner_with_recorded_attendance_is_blocked_not_removed(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        marked = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))

        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))
        db.execute(
            "INSERT INTO attendance_records (session_id, learner_id, status, hours_attended) VALUES (%s, %s, 'present', 7)",
            (session["id"], marked["id"]),
        )
        db.execute(
            "UPDATE learners SET status = 'withdrawn', withdrawal_date = %s WHERE id = %s",
            ("2026-03-01", marked["id"]),
        )

        diff = compute_register_refresh(db, session_row)
        assert {r["learnerId"] for r in diff["blocked"]} == {marked["id"]}
        assert diff["toRemove"] == []

    def test_apply_register_refresh_updates_snapshot_and_is_idempotent(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        leaves = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))

        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))
        db.execute(
            "UPDATE learners SET status = 'withdrawn', withdrawal_date = %s WHERE id = %s",
            ("2026-03-01", leaves["id"]),
        )
        joins = learner_factory(cohort_id=cohort["id"], start_date="2026-04-01")

        diff = compute_register_refresh(db, session_row)
        apply_register_refresh(db, session_row, diff, user_id=None)

        db.execute("SELECT learner_id FROM session_expected_learners WHERE session_id = %s", (session["id"],))
        assert {r["learner_id"] for r in db.fetchall()} == {joins["id"]}

        # Re-applying the same (now-stale) diff a second time must not error
        # or duplicate/re-delete anything.
        apply_register_refresh(db, session_row, diff, user_id=None)
        db.execute("SELECT learner_id FROM session_expected_learners WHERE session_id = %s", (session["id"],))
        assert {r["learner_id"] for r in db.fetchall()} == {joins["id"]}


class TestCancelSession:
    def test_requires_confirmation_when_attendance_already_recorded(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        db.execute(
            "INSERT INTO attendance_records (session_id, learner_id, status, hours_attended) VALUES (%s, %s, 'present', 7)",
            (session["id"], learner["id"]),
        )

        with pytest.raises(HTTPException) as exc:
            cancel_session(db, session, reason="Weather", confirm_with_attendance=False, user_id=None)
        assert exc.value.status_code == 409

    def test_succeeds_and_preserves_attendance_when_confirmed(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        db.execute(
            "INSERT INTO attendance_records (session_id, learner_id, status, hours_attended) VALUES (%s, %s, 'present', 7)",
            (session["id"], learner["id"]),
        )

        cancel_session(db, session, reason="Weather", confirm_with_attendance=True, user_id=None)

        db.execute("SELECT status, cancellation_reason FROM attendance_sessions WHERE id = %s", (session["id"],))
        row = db.fetchone()
        assert row["status"] == "cancelled"
        assert row["cancellation_reason"] == "Weather"

        db.execute(
            "SELECT status FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (session["id"], learner["id"]),
        )
        assert db.fetchone()["status"] == "present"

    def test_no_confirmation_needed_when_no_attendance_recorded(
        self, db, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

        cancel_session(db, session, reason="Cohort disbanded", confirm_with_attendance=False, user_id=None)

        db.execute("SELECT status FROM attendance_sessions WHERE id = %s", (session["id"],))
        assert db.fetchone()["status"] == "cancelled"
