"""Bulk-generating a cohort's recurring attendance sessions from a
day-of-week + weekly/bi-weekly/monthly pattern. "Monthly" is the same
weekday POSITION each month (e.g. the first session's "2nd Tuesday" repeats
as each later month's own 2nd Tuesday), confirmed against the acceptance
examples below -- never a fixed 28-day interval."""
from datetime import date

import pytest
from fastapi import HTTPException

from pyapp.session_generation_lib import MAX_GENERATED_SESSIONS, compute_session_dates, generate_sessions, preview_generated_sessions


class TestComputeSessionDates:
    def test_weekly(self):
        dates = compute_session_dates("monday", "weekly", date(2026, 1, 5), date(2026, 1, 26))
        assert dates == [date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19), date(2026, 1, 26)]

    def test_biweekly(self):
        dates = compute_session_dates("monday", "biweekly", date(2026, 1, 5), date(2026, 1, 26))
        assert dates == [date(2026, 1, 5), date(2026, 1, 19)]

    def test_monthly_repeats_the_same_weekday_position(self):
        # 2026-01-06 is the FIRST Tuesday of January 2026.
        dates = compute_session_dates("tuesday", "monthly", date(2026, 1, 6), date(2026, 4, 30))
        assert dates == [date(2026, 1, 6), date(2026, 2, 3), date(2026, 3, 3), date(2026, 4, 7)]

    def test_monthly_second_occurrence(self):
        # 2026-01-13 is the SECOND Tuesday of January 2026.
        dates = compute_session_dates("tuesday", "monthly", date(2026, 1, 13), date(2026, 4, 30))
        assert dates == [date(2026, 1, 13), date(2026, 2, 10), date(2026, 3, 10), date(2026, 4, 14)]

    def test_monthly_skips_months_with_no_matching_fifth_occurrence(self):
        # 2026-01-30 is the FIFTH Friday of January 2026. February, March,
        # and April 2026 each have only four Fridays, so all three must be
        # skipped entirely, not substituted with the fourth -- the next
        # month with a genuine fifth Friday is May 2026 (the 29th).
        dates = compute_session_dates("friday", "monthly", date(2026, 1, 30), date(2026, 5, 31))
        assert dates == [date(2026, 1, 30), date(2026, 5, 29)]

    def test_rejects_a_first_date_not_on_the_chosen_weekday(self):
        with pytest.raises(HTTPException) as exc:
            compute_session_dates("monday", "weekly", date(2026, 1, 6), date(2026, 1, 26))  # a Tuesday
        assert exc.value.status_code == 400

    def test_rejects_a_final_date_before_the_first(self):
        with pytest.raises(HTTPException) as exc:
            compute_session_dates("monday", "weekly", date(2026, 1, 12), date(2026, 1, 5))
        assert exc.value.status_code == 400

    def test_rejects_a_pattern_that_would_exceed_the_session_cap(self):
        with pytest.raises(HTTPException) as exc:
            compute_session_dates("monday", "weekly", date(2020, 1, 6), date(2030, 1, 7))
        assert exc.value.status_code == 400
        assert str(MAX_GENERATED_SESSIONS) in exc.value.detail

    def test_inclusive_of_both_endpoints(self):
        dates = compute_session_dates("monday", "weekly", date(2026, 1, 5), date(2026, 1, 5))
        assert dates == [date(2026, 1, 5)]


class TestPreviewGeneratedSessions:
    def test_flags_a_date_that_already_has_a_session(self, db, admin_user, cohort_factory, attendance_session_factory):
        cohort = cohort_factory()
        attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-12", planned_start_time="09:00", created_by=admin_user["userId"])

        result = preview_generated_sessions(
            db, cohort_id=cohort["id"], day_of_week="monday", occurrence="weekly",
            planned_start_time="09:00", first_session_date=date(2026, 1, 5), final_session_date=date(2026, 1, 19),
        )
        by_date = {r["sessionDate"]: r["conflict"] for r in result["dates"]}
        assert by_date[date(2026, 1, 5)] is False
        assert by_date[date(2026, 1, 12)] is True
        assert by_date[date(2026, 1, 19)] is False
        assert result["newCount"] == 2
        assert result["conflictCount"] == 1

        by_date_reason = {r["sessionDate"]: r["conflictReason"] for r in result["dates"]}
        assert by_date_reason[date(2026, 1, 12)] == "duplicate_session"

    def test_a_different_start_time_on_the_same_date_is_not_a_conflict(self, db, admin_user, cohort_factory, attendance_session_factory):
        """Mirrors find_duplicate_session's own definition: same cohort,
        date AND start time -- an existing AM session must not block
        generating a PM one on the same day."""
        cohort = cohort_factory()
        attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-05", planned_start_time="09:00", created_by=admin_user["userId"])

        result = preview_generated_sessions(
            db, cohort_id=cohort["id"], day_of_week="monday", occurrence="weekly",
            planned_start_time="13:00", first_session_date=date(2026, 1, 5), final_session_date=date(2026, 1, 5),
        )
        assert result["conflictCount"] == 0

    def test_flags_a_date_outside_the_cohort_date_range(self, db, cohort_factory):
        """Mirrors create_attendance_session's own outside_cohort_date_range
        check, applied per-date here -- a pattern that outlives the cohort
        must not be able to silently create sessions past its end date."""
        cohort = cohort_factory(start_date="2026-01-01", end_date="2026-01-12")

        result = preview_generated_sessions(
            db, cohort_id=cohort["id"], day_of_week="monday", occurrence="weekly",
            planned_start_time="09:00", first_session_date=date(2026, 1, 5), final_session_date=date(2026, 1, 19),
        )
        by_date = {r["sessionDate"]: (r["conflict"], r["conflictReason"]) for r in result["dates"]}
        assert by_date[date(2026, 1, 5)] == (False, None)
        assert by_date[date(2026, 1, 12)] == (False, None)
        assert by_date[date(2026, 1, 19)] == (True, "outside_cohort_date_range")
        assert result["newCount"] == 2
        assert result["conflictCount"] == 1


class TestGenerateSessions:
    def test_creates_a_session_per_date_with_an_expected_learners_snapshot(
        self, db, admin_user, cohort_factory, learner_factory,
    ):
        cohort = cohort_factory(start_date="2026-01-01")
        learner_factory(cohort_id=cohort["id"], start_date="2026-01-01")

        result = generate_sessions(
            db, cohort_id=cohort["id"], day_of_week="monday", occurrence="weekly",
            planned_start_time="09:00", planned_end_time="16:00", planned_duration_hours=7,
            first_session_date=date(2026, 1, 5), final_session_date=date(2026, 1, 19),
            title="Weekly Session", notes=None, created_by=admin_user["userId"],
        )
        assert result["createdCount"] == 3
        assert result["skippedDates"] == []

        db.execute("SELECT session_date FROM attendance_sessions WHERE id = ANY(%s) ORDER BY session_date", (result["createdIds"],))
        assert [r["session_date"] for r in db.fetchall()] == [date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19)]

        db.execute("SELECT count(*) AS n FROM session_expected_learners WHERE session_id = ANY(%s)", (result["createdIds"],))
        assert db.fetchone()["n"] == 3  # one expected-learner row per session

    def test_skips_a_date_that_already_has_a_session_without_duplicating_it(
        self, db, admin_user, cohort_factory, attendance_session_factory,
    ):
        cohort = cohort_factory(start_date="2026-01-01")
        existing = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-12", planned_start_time="09:00", created_by=admin_user["userId"])

        result = generate_sessions(
            db, cohort_id=cohort["id"], day_of_week="monday", occurrence="weekly",
            planned_start_time="09:00", planned_end_time="16:00", planned_duration_hours=7,
            first_session_date=date(2026, 1, 5), final_session_date=date(2026, 1, 19),
            title="Weekly Session", notes=None, created_by=admin_user["userId"],
        )
        assert result["createdCount"] == 2
        assert result["skippedDates"] == [date(2026, 1, 12)]

        db.execute("SELECT count(*) AS n FROM attendance_sessions WHERE cohort_id = %s AND session_date = %s", (cohort["id"], date(2026, 1, 12)))
        assert db.fetchone()["n"] == 1  # the pre-existing one, not duplicated
        assert existing["id"] not in result["createdIds"]

    def test_skips_a_date_outside_the_cohort_date_range_without_creating_it(
        self, db, admin_user, cohort_factory,
    ):
        cohort = cohort_factory(start_date="2026-01-01", end_date="2026-01-12")

        result = generate_sessions(
            db, cohort_id=cohort["id"], day_of_week="monday", occurrence="weekly",
            planned_start_time="09:00", planned_end_time="16:00", planned_duration_hours=7,
            first_session_date=date(2026, 1, 5), final_session_date=date(2026, 1, 19),
            title="Weekly Session", notes=None, created_by=admin_user["userId"],
        )
        assert result["createdCount"] == 2
        assert result["skippedDates"] == [date(2026, 1, 19)]

        db.execute("SELECT count(*) AS n FROM attendance_sessions WHERE cohort_id = %s AND session_date = %s", (cohort["id"], date(2026, 1, 19)))
        assert db.fetchone()["n"] == 0

    def test_a_functional_skills_cohorts_generated_sessions_snapshot_only_its_secondarily_enrolled_learners(
        self, db, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        """The FS-vs-home-cohort distinction lives entirely inside
        learners_expected_in_cohort_as_of (shared with a single manually-
        created session) -- this locks in that bulk generation for a
        secondary cohort gets the same treatment, not just the home-cohort
        case already covered above."""
        home = cohort_factory(start_date="2026-01-01")
        fs_cohort = cohort_factory(start_date="2026-01-01", membership_type="secondary", subject="math")
        enrolled_learner = learner_factory(cohort_id=home["id"], start_date="2026-01-01")
        other_learner = learner_factory(cohort_id=home["id"], start_date="2026-01-01")
        secondary_enrollment_factory(learner_id=enrolled_learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        result = generate_sessions(
            db, cohort_id=fs_cohort["id"], day_of_week="monday", occurrence="weekly",
            planned_start_time="09:00", planned_end_time="16:00", planned_duration_hours=7,
            first_session_date=date(2026, 1, 5), final_session_date=date(2026, 1, 5),
            title="FS Session", notes=None, created_by=admin_user["userId"],
        )
        assert result["createdCount"] == 1

        db.execute("SELECT learner_id AS id FROM session_expected_learners WHERE session_id = ANY(%s)", (result["createdIds"],))
        expected_ids = {r["id"] for r in db.fetchall()}
        assert expected_ids == {enrolled_learner["id"]}
        assert other_learner["id"] not in expected_ids

    def test_title_and_notes_are_applied_to_every_generated_session(self, db, admin_user, cohort_factory):
        cohort = cohort_factory(start_date="2026-01-01")
        result = generate_sessions(
            db, cohort_id=cohort["id"], day_of_week="monday", occurrence="weekly",
            planned_start_time="09:00", planned_end_time="16:00", planned_duration_hours=7,
            first_session_date=date(2026, 1, 5), final_session_date=date(2026, 1, 12),
            title="Generated Session", notes="Auto-created", created_by=admin_user["userId"],
        )
        db.execute("SELECT title, notes FROM attendance_sessions WHERE id = ANY(%s)", (result["createdIds"],))
        rows = db.fetchall()
        assert all(r["title"] == "Generated Session" and r["notes"] == "Auto-created" for r in rows)
