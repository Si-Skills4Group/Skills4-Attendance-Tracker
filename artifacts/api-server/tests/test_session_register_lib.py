import datetime

import pytest
from fastapi import HTTPException

from pyapp.allocation_lib import apply_transfer
from pyapp.secondary_enrollment_lib import end_secondary_enrollment
from pyapp.session_register_lib import (
    apply_register_refresh,
    cancel_session,
    compute_register_refresh,
    ensure_expected_learners_snapshot,
    find_duplicate_session,
    has_cohort_membership_changed_since,
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


def _db_now(db):
    """The DB server's own clock, not the test runner's -- avoids any clock
    skew between the two when comparing a captured cutoff against a
    changed_date/created_at/updated_at written by a later `now()` call."""
    db.execute("SELECT now() AS now")
    return db.fetchone()["now"]


class TestHasCohortMembershipChangedSince:
    def test_false_when_nothing_happened_since_the_cutoff(self, db, cohort_factory):
        cohort = cohort_factory()
        since = _db_now(db)
        assert has_cohort_membership_changed_since(db, cohort["id"], since) is False

    def test_true_when_a_transfer_moved_a_learner_into_the_cohort_after_the_cutoff(
        self, db, admin_user, cohort_factory, learner_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory()
        since = _db_now(db)

        apply_transfer(
            db, {"id": learner["id"], "tutorId": None, "cohortId": None},
            None, cohort["id"], datetime.date(2026, 1, 1), "test transfer", admin_user["userId"],
        )

        assert has_cohort_membership_changed_since(db, cohort["id"], since) is True

    def test_true_when_a_transfer_moved_a_learner_out_of_the_cohort_after_the_cutoff(
        self, db, admin_user, cohort_factory, learner_factory,
    ):
        cohort = cohort_factory()
        other_cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        since = _db_now(db)

        apply_transfer(
            db, {"id": learner["id"], "tutorId": None, "cohortId": cohort["id"]},
            None, other_cohort["id"], datetime.date(2026, 1, 1), "test transfer", admin_user["userId"],
        )

        assert has_cohort_membership_changed_since(db, cohort["id"], since) is True

    def test_false_when_the_transfer_predates_the_cutoff(self, db, admin_user, cohort_factory, learner_factory):
        cohort = cohort_factory()
        learner = learner_factory()

        apply_transfer(
            db, {"id": learner["id"], "tutorId": None, "cohortId": None},
            None, cohort["id"], datetime.date(2026, 1, 1), "test transfer", admin_user["userId"],
        )
        since = _db_now(db)

        assert has_cohort_membership_changed_since(db, cohort["id"], since) is False

    def test_true_when_a_secondary_enrollment_was_created_after_the_cutoff(
        self, db, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory()
        since = _db_now(db)

        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        assert has_cohort_membership_changed_since(db, fs_cohort["id"], since) is True

    def test_true_when_a_secondary_enrollment_was_ended_after_the_cutoff(
        self, db, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory()
        enrollment = secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])
        since = _db_now(db)

        end_secondary_enrollment(db, enrollment["id"], datetime.date(2026, 6, 1), "no longer needed", admin_user["userId"])

        assert has_cohort_membership_changed_since(db, fs_cohort["id"], since) is True

    def test_none_since_is_treated_as_changed(self, db, cohort_factory):
        cohort = cohort_factory()
        assert has_cohort_membership_changed_since(db, cohort["id"], None) is True

    def test_a_scheduled_not_yet_applied_transfer_does_not_trip_the_flag(
        self, db, admin_user, cohort_factory, learner_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory()
        since = _db_now(db)

        db.execute(
            """
            INSERT INTO scheduled_allocations (learner_id, new_tutor_id, new_cohort_id, effective_date, created_by)
            VALUES (%s, NULL, %s, %s, %s)
            """,
            (learner["id"], cohort["id"], datetime.date(2099, 1, 1), admin_user["userId"]),
        )

        assert has_cohort_membership_changed_since(db, cohort["id"], since) is False

    def test_true_when_a_home_cohort_learner_is_withdrawn_after_the_cutoff(
        self, db, cohort_factory, learner_factory,
    ):
        """Withdrawing a learner (routers/learners.py's _change_learner_status)
        never touches learner_allocation_history or learner_cohort_enrollments
        -- only learners.updated_at -- so this must be caught independently,
        via the same "currently reachable from this cohort" check used for
        "currently" reads elsewhere. Regression coverage for a real bug: the
        roster-changed banner silently never appeared for a withdrawn
        learner before this branch was added."""
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        since = _db_now(db)

        db.execute(
            "UPDATE learners SET status = 'withdrawn', withdrawal_date = %s, updated_at = now() WHERE id = %s",
            ("2026-01-01", learner["id"]),
        )

        assert has_cohort_membership_changed_since(db, cohort["id"], since) is True

    def test_true_when_a_secondary_enrolled_learner_is_withdrawn_after_the_cutoff(
        self, db, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory()
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])
        since = _db_now(db)

        db.execute(
            "UPDATE learners SET status = 'withdrawn', withdrawal_date = %s, updated_at = now() WHERE id = %s",
            ("2026-01-01", learner["id"]),
        )

        assert has_cohort_membership_changed_since(db, fs_cohort["id"], since) is True

    def test_false_when_an_unrelated_learners_status_changes(self, db, cohort_factory, learner_factory):
        cohort = cohort_factory()
        unrelated_learner = learner_factory()
        since = _db_now(db)

        db.execute(
            "UPDATE learners SET status = 'withdrawn', withdrawal_date = %s, updated_at = now() WHERE id = %s",
            ("2026-01-01", unrelated_learner["id"]),
        )

        assert has_cohort_membership_changed_since(db, cohort["id"], since) is False

    def test_true_when_a_learner_is_paused_for_a_break_in_learning(self, db, cohort_factory, learner_factory):
        """A paused (Break in Learning) learner may not return to the same
        tutor/cohort and should not be expected to attend sessions --
        learners_expected_in_cohort_as_of excludes them (allocation_lib), so
        this must trip the alert exactly like withdrawn/completed."""
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        since = _db_now(db)

        db.execute("UPDATE learners SET status = 'paused', updated_at = now() WHERE id = %s", (learner["id"],))

        assert has_cohort_membership_changed_since(db, cohort["id"], since) is True

    def test_true_when_a_withdrawn_learner_is_reactivated_after_the_cutoff(self, db, cohort_factory, learner_factory):
        """Regression test: reactivating a previously withdrawn/completed/
        paused learner back to active must trip the alert too -- they
        become eligible again. A narrower version of this check that only
        fired when the CURRENT status was withdrawn/completed/paused missed
        this direction entirely, since withdrawal_date/actual_end_date are
        never cleared by _change_learner_status and the resulting status
        alone ('active') can't be told apart from a learner who was never
        withdrawn at all."""
        cohort = cohort_factory()
        learner = learner_factory(
            cohort_id=cohort["id"], status="withdrawn", withdrawal_date="2026-01-01",
        )
        since = _db_now(db)

        db.execute("UPDATE learners SET status = 'active', updated_at = now() WHERE id = %s", (learner["id"],))

        assert has_cohort_membership_changed_since(db, cohort["id"], since) is True

    def test_true_even_for_a_learners_unrelated_profile_edit(self, db, cohort_factory, learner_factory):
        """Documents an accepted, deliberate trade-off: this check can't
        distinguish a genuine lifecycle change from an unrelated profile
        edit (email, employer, etc.) purely from learners.updated_at, since
        status/withdrawalDate/actualEndDate can be written by more than one
        code path with more than one audit action name (see the function's
        docstring). Catching every real lifecycle change -- including
        reactivation -- matters more than avoiding this rarer false
        positive; the refresh dialog's "Mark Reviewed" always remains
        available for a review that turns out to be a no-op."""
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        since = _db_now(db)

        db.execute("UPDATE learners SET employer = 'New Employer Ltd', updated_at = now() WHERE id = %s", (learner["id"],))

        assert has_cohort_membership_changed_since(db, cohort["id"], since) is True


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

    def test_diff_reports_a_paused_learner_as_a_removal(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        """End-to-end regression for the reported bug: pausing a learner
        (Break in Learning) must show up as an actual removal in the
        refresh diff -- they may not return to the same tutor/cohort and
        should not be expected to attend sessions while paused."""
        cohort = cohort_factory()
        paused = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))

        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))
        db.execute("UPDATE learners SET status = 'paused' WHERE id = %s", (paused["id"],))

        diff = compute_register_refresh(db, session_row)
        assert {r["learnerId"] for r in diff["toRemove"]} == {paused["id"]}
        assert diff["blocked"] == []

    def test_diff_reports_a_reactivated_learner_as_an_addition(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        """The flip side of the paused/withdrawn removal cases: a learner
        reactivated back to active after the snapshot was generated must
        show up as an addition -- learners_expected_in_cohort_as_of's
        exclusions are all status-gated, so simply flipping status back to
        'active' re-includes them with no extra code needed here."""
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))

        db.execute(
            "UPDATE learners SET status = 'withdrawn', withdrawal_date = '2026-01-15' WHERE id = %s",
            (learner["id"],),
        )
        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))
        db.execute("SELECT count(*)::int AS c FROM session_expected_learners WHERE session_id = %s", (session["id"],))
        assert db.fetchone()["c"] == 0

        db.execute("UPDATE learners SET status = 'active' WHERE id = %s", (learner["id"],))

        diff = compute_register_refresh(db, session_row)
        assert {r["learnerId"] for r in diff["toAdd"]} == {learner["id"]}

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

    def test_refresh_picks_up_a_new_functional_skills_secondary_enrollment(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory, secondary_enrollment_factory,
    ):
        """A Functional Skills session generated before a learner's
        secondary enrollment existed must still pick them up via the
        existing Refresh Expected Learners mechanism, with zero new code
        beyond the resolver's OR-EXISTS extension."""
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=fs_cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, fs_cohort["id"], datetime.date(2026, 6, 1))

        # Generated before the enrollment exists -- zero expected learners.
        ensure_expected_learners_snapshot(db, session["id"], fs_cohort["id"], datetime.date(2026, 6, 1))
        db.execute("SELECT count(*) AS c FROM session_expected_learners WHERE session_id = %s", (session["id"],))
        assert db.fetchone()["c"] == 0

        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-02-01")

        diff = compute_register_refresh(db, session_row)
        assert {r["learnerId"] for r in diff["toAdd"]} == {learner["id"]}
        apply_register_refresh(db, session_row, diff, user_id=admin_user["userId"])

        db.execute("SELECT learner_id FROM session_expected_learners WHERE session_id = %s", (session["id"],))
        assert {r["learner_id"] for r in db.fetchall()} == {learner["id"]}

    def test_apply_register_refresh_clears_completed_at_when_adding_reopens_the_register(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))

        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))
        db.execute(
            "INSERT INTO attendance_records (session_id, learner_id, status, hours_attended) VALUES (%s, %s, 'present', 7)",
            (session["id"], learner["id"]),
        )
        db.execute(
            "UPDATE attendance_sessions SET completed_at = now(), completed_by = %s WHERE id = %s",
            (admin_user["userId"], session["id"]),
        )

        joins = learner_factory(cohort_id=cohort["id"], start_date="2026-04-01")
        diff = compute_register_refresh(db, session_row)
        apply_register_refresh(db, session_row, diff, user_id=admin_user["userId"])

        db.execute("SELECT completed_at, completed_by FROM attendance_sessions WHERE id = %s", (session["id"],))
        row = db.fetchone()
        assert row["completed_at"] is None
        assert row["completed_by"] is None

    def test_apply_register_refresh_leaves_completed_at_when_nothing_is_added(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))

        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))
        db.execute(
            "INSERT INTO attendance_records (session_id, learner_id, status, hours_attended) VALUES (%s, %s, 'present', 7)",
            (session["id"], learner["id"]),
        )
        db.execute(
            "UPDATE attendance_sessions SET completed_at = now(), completed_by = %s WHERE id = %s",
            (admin_user["userId"], session["id"]),
        )

        apply_register_refresh(db, session_row, {"toAdd": [], "toRemove": [], "blocked": []}, user_id=admin_user["userId"])

        db.execute("SELECT completed_at FROM attendance_sessions WHERE id = %s", (session["id"],))
        assert db.fetchone()["completed_at"] is not None

    def test_apply_register_refresh_advances_roster_synced_at(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))
        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))
        db.execute("SELECT roster_synced_at FROM attendance_sessions WHERE id = %s", (session["id"],))
        initial = db.fetchone()["roster_synced_at"]

        learner_factory(cohort_id=cohort["id"], start_date="2026-04-01")
        diff = compute_register_refresh(db, session_row)
        apply_register_refresh(db, session_row, diff, user_id=admin_user["userId"])

        db.execute("SELECT roster_synced_at FROM attendance_sessions WHERE id = %s", (session["id"],))
        assert db.fetchone()["roster_synced_at"] > initial

    def test_apply_register_refresh_advances_roster_synced_at_even_with_an_empty_diff(
        self, db, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))
        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))
        db.execute("SELECT roster_synced_at FROM attendance_sessions WHERE id = %s", (session["id"],))
        initial = db.fetchone()["roster_synced_at"]

        apply_register_refresh(db, session_row, {"toAdd": [], "toRemove": [], "blocked": []}, user_id=admin_user["userId"])

        db.execute("SELECT roster_synced_at FROM attendance_sessions WHERE id = %s", (session["id"],))
        assert db.fetchone()["roster_synced_at"] > initial

    def test_a_preview_alone_does_not_advance_roster_synced_at(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        cohort = cohort_factory()
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"]
        )
        session_row = self._session_row(session, cohort["id"], datetime.date(2026, 6, 1))
        ensure_expected_learners_snapshot(db, session["id"], cohort["id"], datetime.date(2026, 6, 1))
        db.execute("SELECT roster_synced_at FROM attendance_sessions WHERE id = %s", (session["id"],))
        initial = db.fetchone()["roster_synced_at"]

        learner_factory(cohort_id=cohort["id"], start_date="2026-04-01")
        compute_register_refresh(db, session_row)

        db.execute("SELECT roster_synced_at FROM attendance_sessions WHERE id = %s", (session["id"],))
        assert db.fetchone()["roster_synced_at"] == initial


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
