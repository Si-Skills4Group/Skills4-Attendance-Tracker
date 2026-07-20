"""Tests for pyapp.attendance_metrics -- the Phase 8 minutes-based
calculation engine. Each test builds a small, real register scenario
(session_expected_learners + attendance_records rows) and asserts on the
aggregate SQL result, since the bucketing happens in SQL, not in Python."""
from datetime import date, timedelta

import pytest

from pyapp.attendance_metrics import (
    MIN_COMPLETED_ROWS_FOR_ATTENDANCE_FLAG,
    fetch_attendance_metrics,
    fetch_attendance_metrics_grouped,
    fetch_register_completion,
    is_low_attendance,
    resolve_period,
)
from pyapp.session_register_lib import ensure_expected_learners_snapshot


def _record(db, session_id, learner_id, status, hours_attended=0, minutes_late=0):
    db.execute(
        """
        INSERT INTO attendance_records (session_id, learner_id, status, hours_attended, minutes_late)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (session_id, learner_id) DO UPDATE SET status = EXCLUDED.status,
            hours_attended = EXCLUDED.hours_attended, minutes_late = EXCLUDED.minutes_late
        """,
        (session_id, learner_id, status, hours_attended, minutes_late),
    )


def _snapshot(db, session: dict):
    ensure_expected_learners_snapshot(db, session["id"], session["cohort_id"], date.fromisoformat(session["session_date"]))


# ---------------------------------------------------------------------------
# resolve_period -- pure, no DB required
# ---------------------------------------------------------------------------


class TestResolvePeriod:
    def test_current_week_is_monday_to_sunday(self):
        # 2026-07-15 is a Wednesday
        start, end = resolve_period("current_week", today=date(2026, 7, 15))
        assert start == date(2026, 7, 13)  # Monday
        assert end == date(2026, 7, 19)  # Sunday

    def test_current_month_is_calendar_month(self):
        start, end = resolve_period("current_month", today=date(2026, 7, 15))
        assert start == date(2026, 7, 1)
        assert end == date(2026, 7, 31)

    def test_current_month_handles_december_rollover(self):
        start, end = resolve_period("current_month", today=date(2026, 12, 15))
        assert start == date(2026, 12, 1)
        assert end == date(2026, 12, 31)

    def test_previous_month(self):
        start, end = resolve_period("previous_month", today=date(2026, 7, 15))
        assert start == date(2026, 6, 1)
        assert end == date(2026, 6, 30)

    def test_previous_month_handles_january_rollover(self):
        start, end = resolve_period("previous_month", today=date(2026, 1, 15))
        assert start == date(2025, 12, 1)
        assert end == date(2025, 12, 31)

    def test_last_30_days_is_inclusive_of_today(self):
        start, end = resolve_period("last_30_days", today=date(2026, 7, 30))
        assert end == date(2026, 7, 30)
        assert (end - start).days == 29

    def test_custom_passes_through(self):
        start, end = resolve_period("custom", date_from=date(2026, 1, 1), date_to=date(2026, 1, 5))
        assert (start, end) == (date(2026, 1, 1), date(2026, 1, 5))

    def test_custom_requires_both_dates(self):
        with pytest.raises(ValueError):
            resolve_period("custom", date_from=date(2026, 1, 1))

    def test_custom_rejects_end_before_start(self):
        with pytest.raises(ValueError):
            resolve_period("custom", date_from=date(2026, 1, 5), date_to=date(2026, 1, 1))


# ---------------------------------------------------------------------------
# fetch_attendance_metrics -- the calculation rules from the brief
# ---------------------------------------------------------------------------


class TestFetchAttendanceMetrics:
    def test_present_counts_as_attended(self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "present", hours_attended=7)

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 420
        assert metrics.attendedMinutes == 420
        assert metrics.attendancePercentage == 100.0

    def test_late_attended_minutes_count_as_attended_using_recorded_duration(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "late", hours_attended=6, minutes_late=15)

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 420
        assert metrics.attendedMinutes == 360  # 6h, not the full planned 7h
        assert metrics.lateMinutes == 15
        assert metrics.lateSessionCount == 1
        assert metrics.averageMinutesLate == 15.0

    def test_authorised_absence_is_expected_but_not_attended(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "absent_authorised")

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 420
        assert metrics.attendedMinutes == 0
        assert metrics.authorisedAbsenceMinutes == 420
        assert metrics.authorisedAbsenceSessions == 1
        assert metrics.attendancePercentage == 0.0

    def test_unauthorised_absence_is_expected_but_not_attended(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "absent_unauthorised")

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 420
        assert metrics.attendedMinutes == 0
        assert metrics.unauthorisedAbsenceMinutes == 420
        assert metrics.unauthorisedAbsenceSessions == 1

    def test_not_expected_is_excluded_from_expected_minutes(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "not_expected")

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 0
        assert metrics.attendedMinutes == 0
        assert metrics.attendancePercentage is None

    def test_withdrawn_status_is_excluded_from_expected_minutes(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "withdrawn")

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 0

    def test_cancelled_sessions_are_excluded(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "absent_unauthorised")
        db.execute("UPDATE attendance_sessions SET status = 'cancelled' WHERE id = %s", (session["id"],))

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 0
        assert metrics.unauthorisedAbsenceMinutes == 0

    def test_deleted_learner_minutes_are_excluded_from_cohort_scope(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        """A deleted learner's recorded minutes must disappear from the
        cohort's aggregate total too, not just their own -- deleting a
        learner must not leave a ghost contribution behind in any other
        scope's rollup."""
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "present", hours_attended=7)

        before = fetch_attendance_metrics(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert before.expectedMinutes == 420

        db.execute("UPDATE learners SET deleted_at = now() WHERE id = %s", (learner["id"],))

        after = fetch_attendance_metrics(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert after.expectedMinutes == 0
        assert after.attendedMinutes == 0

    def test_deleted_sessions_are_excluded(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "absent_unauthorised")
        db.execute("UPDATE attendance_sessions SET deleted_at = now() WHERE id = %s", (session["id"],))

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 0
        assert metrics.unauthorisedAbsenceMinutes == 0

    def test_deleted_cohort_is_excluded_from_organisation_scope(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "present", hours_attended=7)
        db.execute("UPDATE cohorts SET deleted_at = now() WHERE id = %s", (cohort["id"],))

        metrics = fetch_attendance_metrics(
            db, scope="organisation", scope_id=None, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 0

    def test_sessions_before_learner_start_are_excluded(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], start_date="2026-06-01")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-01-01", planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        # The learner isn't in session_expected_learners at all (start_date
        # is after the session), so no row can even be recorded against it
        # via the normal save path -- expected_minutes must be 0 regardless.
        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 0

    def test_sessions_after_withdrawal_are_excluded(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], status="withdrawn", withdrawal_date="2026-01-10")
        session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-01-20", planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 0

    def test_allocation_effective_from_is_inclusive(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort_a = cohort_factory()
        cohort_b = cohort_factory()
        learner = learner_factory(cohort_id=cohort_a["id"], start_date="2026-01-01")
        # Transfer effective exactly on the session date.
        db.execute(
            """
            INSERT INTO learner_allocation_history
                (learner_id, previous_cohort_id, new_cohort_id, effective_date, changed_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (learner["id"], cohort_a["id"], cohort_b["id"], "2026-02-01", admin_user["userId"]),
        )
        db.execute("UPDATE learners SET cohort_id = %s WHERE id = %s", (cohort_b["id"], learner["id"]))

        session_on_transfer_day = attendance_session_factory(
            cohort_id=cohort_b["id"], session_date="2026-02-01", planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session_on_transfer_day)
        metrics = fetch_attendance_metrics(
            db, scope="cohort", scope_id=cohort_b["id"], period_start=date(2026, 2, 1), period_end=date(2026, 2, 1)
        )
        # The new cohort's session on the effective date itself must already
        # expect the learner (effective_date is an inclusive start).
        assert metrics.expectedMinutes == 420

    def test_allocation_effective_to_is_exclusive(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort_a = cohort_factory()
        cohort_b = cohort_factory()
        learner = learner_factory(cohort_id=cohort_a["id"], start_date="2026-01-01")
        db.execute(
            """
            INSERT INTO learner_allocation_history
                (learner_id, previous_cohort_id, new_cohort_id, effective_date, changed_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (learner["id"], cohort_a["id"], cohort_b["id"], "2026-02-01", admin_user["userId"]),
        )
        db.execute("UPDATE learners SET cohort_id = %s WHERE id = %s", (cohort_b["id"], learner["id"]))

        # A session in the OLD cohort on the transfer date itself must NOT
        # expect the learner any more -- effective_to (the transfer date,
        # from the old cohort's perspective) is exclusive.
        session_old_cohort = attendance_session_factory(
            cohort_id=cohort_a["id"], session_date="2026-02-01", planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session_old_cohort)
        metrics = fetch_attendance_metrics(
            db, scope="cohort", scope_id=cohort_a["id"], period_start=date(2026, 2, 1), period_end=date(2026, 2, 1)
        )
        assert metrics.expectedMinutes == 0

    def test_zero_expected_minutes_is_handled_safely(self, db, cohort_factory, learner_factory):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 0
        assert metrics.attendancePercentage is None
        assert metrics.insufficientData is True

    def test_missing_records_are_not_converted_into_absence(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        # No attendance_records row saved at all -- register is incomplete.

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.expectedMinutes == 420  # still expected
        assert metrics.attendedMinutes == 0
        assert metrics.authorisedAbsenceMinutes == 0
        assert metrics.unauthorisedAbsenceMinutes == 0
        assert metrics.missingRecordCount == 1
        assert metrics.completedRegisterRowCount == 0

    def test_intermediate_totals_are_not_rounded_early(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        # 3 sessions of 1/3 hour each => 60 minutes total, attended 1 of them
        # fully. If percentages were rounded per-session before summing,
        # 1/3 * 100 rounds to 33.3 and would drift from the true 33.33...%.
        third_hour = round(1 / 3, 10)
        sessions = [
            attendance_session_factory(
                cohort_id=cohort["id"], planned_duration_hours=third_hour, session_date=f"2026-01-0{i + 1}",
                created_by=admin_user["userId"],
            )
            for i in range(3)
        ]
        for i, session in enumerate(sessions):
            _snapshot(db, session)
            _record(
                db, session["id"], learner["id"],
                "present" if i == 0 else "absent_unauthorised",
                hours_attended=third_hour if i == 0 else 0,
            )

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.attendancePercentage == pytest.approx(100 / 3, rel=1e-6)


class TestFetchAttendanceMetricsGrouped:
    def test_batches_multiple_learners_in_one_query(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_a = learner_factory(cohort_id=cohort["id"])
        learner_b = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner_a["id"], "present", hours_attended=7)
        _record(db, session["id"], learner_b["id"], "absent_unauthorised")

        results = fetch_attendance_metrics_grouped(
            db, group_by="learner", group_ids=[learner_a["id"], learner_b["id"]],
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        assert results[learner_a["id"]].attendancePercentage == 100.0
        assert results[learner_b["id"]].attendancePercentage == 0.0

    def test_entity_with_no_rows_gets_an_explicit_empty_entry(self, db, cohort_factory, learner_factory):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        results = fetch_attendance_metrics_grouped(
            db, group_by="learner", group_ids=[learner["id"]], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert learner["id"] in results
        assert results[learner["id"]].expectedMinutes == 0
        assert results[learner["id"]].insufficientData is True

    def test_empty_group_ids_returns_empty_dict(self, db):
        assert fetch_attendance_metrics_grouped(
            db, group_by="learner", group_ids=[], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        ) == {}


class TestIsLowAttendance:
    def test_below_threshold_with_enough_data_is_flagged(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        sessions = [
            attendance_session_factory(
                cohort_id=cohort["id"], planned_duration_hours=1, session_date=f"2026-01-0{i + 1}",
                created_by=admin_user["userId"],
            )
            for i in range(MIN_COMPLETED_ROWS_FOR_ATTENDANCE_FLAG)
        ]
        for session in sessions:
            _snapshot(db, session)
            _record(db, session["id"], learner["id"], "absent_unauthorised")

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert is_low_attendance(metrics, threshold=85) is True

    def test_only_incomplete_registers_are_never_flagged(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        # No records saved -- register is entirely incomplete for this learner.

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert is_low_attendance(metrics, threshold=85) is False

    def test_zero_expected_minutes_is_never_flagged(self, db, cohort_factory, learner_factory):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert is_low_attendance(metrics, threshold=85) is False

    def test_below_minimum_completed_rows_is_never_flagged(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        # Only 2 recorded rows, below MIN_COMPLETED_ROWS_FOR_ATTENDANCE_FLAG (3).
        sessions = [
            attendance_session_factory(
                cohort_id=cohort["id"], planned_duration_hours=1, session_date=f"2026-01-0{i + 1}",
                created_by=admin_user["userId"],
            )
            for i in range(2)
        ]
        for session in sessions:
            _snapshot(db, session)
            _record(db, session["id"], learner["id"], "absent_unauthorised")

        metrics = fetch_attendance_metrics(
            db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert is_low_attendance(metrics, threshold=85) is False


# ---------------------------------------------------------------------------
# Register completeness -- deliberately separate from attendance
# ---------------------------------------------------------------------------


class TestFetchRegisterCompletion:
    def test_empty_register_is_not_started(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        _snapshot(db, session)

        summary = fetch_register_completion(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert summary.notStarted == 1
        assert summary.inProgress == 0
        assert summary.completed == 0

    def test_partial_register_is_in_progress(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_a = learner_factory(cohort_id=cohort["id"])
        learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        _snapshot(db, session)
        _record(db, session["id"], learner_a["id"], "present", hours_attended=7)

        summary = fetch_register_completion(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert summary.inProgress == 1
        assert summary.notStarted == 0

    def test_fully_valid_register_is_completed(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_a = learner_factory(cohort_id=cohort["id"])
        learner_b = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        _snapshot(db, session)
        _record(db, session["id"], learner_a["id"], "present", hours_attended=7)
        _record(db, session["id"], learner_b["id"], "absent_authorised")

        summary = fetch_register_completion(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert summary.completed == 1

    def test_locked_register_counts_as_complete(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "present", hours_attended=7)
        db.execute(
            "UPDATE attendance_sessions SET register_locked_at = now(), register_locked_by = %s WHERE id = %s",
            (admin_user["userId"], session["id"]),
        )

        summary = fetch_register_completion(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert summary.locked == 1
        assert summary.completed == 0
        assert summary.completionPercentage == 100.0

    def test_cancelled_sessions_are_excluded_from_completion(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        _snapshot(db, session)
        db.execute("UPDATE attendance_sessions SET status = 'cancelled' WHERE id = %s", (session["id"],))

        summary = fetch_register_completion(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert summary.notStarted == 0
        assert summary.completionPercentage is None

    def test_attendance_and_completion_remain_separate(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        """A fully-completed register with 0% attendance is a real, valid
        state -- completion tracks whether every row has a status, not
        whether learners showed up."""
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(
            cohort_id=cohort["id"], planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "absent_unauthorised")

        metrics = fetch_attendance_metrics(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        completion = fetch_register_completion(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)
        )
        assert metrics.attendancePercentage == 0.0
        assert completion.completed == 1
        assert completion.completionPercentage == 100.0

    def test_outstanding_only_counts_overdue_sessions(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_factory(cohort_id=cohort["id"])
        future_date = (date.today() + timedelta(days=30)).isoformat()
        past_session = attendance_session_factory(
            cohort_id=cohort["id"], session_date="2026-01-01", created_by=admin_user["userId"]
        )
        future_session = attendance_session_factory(
            cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"]
        )
        _snapshot(db, past_session)
        _snapshot(db, future_session)

        summary = fetch_register_completion(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2020, 1, 1), period_end=date(2099, 1, 1)
        )
        assert summary.notStarted == 2  # both are technically "not started"
        assert summary.outstanding == 1  # only the past one is overdue
