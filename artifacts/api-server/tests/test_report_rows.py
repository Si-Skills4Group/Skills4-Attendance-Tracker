"""Direct (non-HTTP) tests for pyapp/report_rows.py -- the row-level report
queries backing absence/lateness/register-completion/allocation-history/
learner-session-history. Each test builds a small, real register scenario,
mirroring test_attendance_metrics.py's convention, since correctness here
is about the SQL shape (filters, pagination, derived columns), not about
permissions (covered separately in test_reports.py/test_report_csv_export.py)."""
from datetime import date, timedelta

from pyapp.report_rows import (
    fetch_absence_rows,
    fetch_allocation_history_rows,
    fetch_learner_session_history,
    fetch_lateness_rows,
    fetch_register_completion_rows,
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


PERIOD = (date(2026, 1, 1), date(2026, 1, 31))


class TestFetchAbsenceRows:
    def test_filters_by_absence_type_and_cohort(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        other_cohort = cohort_factory()
        learner_auth = learner_factory(cohort_id=cohort["id"])
        learner_unauth = learner_factory(cohort_id=cohort["id"])
        learner_other_cohort = learner_factory(cohort_id=other_cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        other_session = attendance_session_factory(cohort_id=other_cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _record(db, session_row["id"], learner_auth["id"], "absent_authorised")
        _record(db, session_row["id"], learner_unauth["id"], "absent_unauthorised")
        _record(db, other_session["id"], learner_other_cohort["id"], "absent_authorised")

        rows, total = fetch_absence_rows(
            db, absence_type="absent_authorised", period_start=PERIOD[0], period_end=PERIOD[1], cohort_id=cohort["id"]
        )
        assert total == 1
        assert rows[0]["learnerId"] == learner_auth["id"]
        assert rows[0]["status"] == "absent_authorised"

    def test_employer_filter(self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory):
        cohort = cohort_factory()
        learner_acme = learner_factory(cohort_id=cohort["id"])
        learner_other = learner_factory(cohort_id=cohort["id"])
        db.execute("UPDATE learners SET employer = %s WHERE id = %s", ("Acme Ltd", learner_acme["id"]))
        db.execute("UPDATE learners SET employer = %s WHERE id = %s", ("Other Ltd", learner_other["id"]))
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _record(db, session_row["id"], learner_acme["id"], "absent_unauthorised")
        _record(db, session_row["id"], learner_other["id"], "absent_unauthorised")

        rows, total = fetch_absence_rows(
            db, absence_type="absent_unauthorised", period_start=PERIOD[0], period_end=PERIOD[1], employer="Acme Ltd"
        )
        assert total == 1
        assert rows[0]["learnerId"] == learner_acme["id"]
        assert rows[0]["employer"] == "Acme Ltd"

    def test_pagination(self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory):
        cohort = cohort_factory()
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        for _ in range(3):
            learner = learner_factory(cohort_id=cohort["id"])
            _record(db, session_row["id"], learner["id"], "absent_unauthorised")

        page_1, total = fetch_absence_rows(
            db, absence_type="absent_unauthorised", period_start=PERIOD[0], period_end=PERIOD[1], cohort_id=cohort["id"], page=1, page_size=2
        )
        page_2, _ = fetch_absence_rows(
            db, absence_type="absent_unauthorised", period_start=PERIOD[0], period_end=PERIOD[1], cohort_id=cohort["id"], page=2, page_size=2
        )
        assert total == 3
        assert len(page_1) == 2
        assert len(page_2) == 1
        assert {r["learnerId"] for r in page_1} & {r["learnerId"] for r in page_2} == set()

    def test_cancelled_sessions_are_excluded(self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _record(db, session_row["id"], learner["id"], "absent_unauthorised")
        db.execute("UPDATE attendance_sessions SET status = 'cancelled' WHERE id = %s", (session_row["id"],))

        rows, total = fetch_absence_rows(
            db, absence_type="absent_unauthorised", period_start=PERIOD[0], period_end=PERIOD[1], cohort_id=cohort["id"]
        )
        assert total == 0
        assert rows == []


class TestFetchLatenessRows:
    def test_returns_minutes_late_and_orders_worst_first(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_a = learner_factory(cohort_id=cohort["id"])
        learner_b = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _record(db, session_row["id"], learner_a["id"], "late", hours_attended=5, minutes_late=10)
        _record(db, session_row["id"], learner_b["id"], "late", hours_attended=5, minutes_late=45)

        rows, total = fetch_lateness_rows(db, period_start=PERIOD[0], period_end=PERIOD[1], cohort_id=cohort["id"])
        assert total == 2
        assert [r["minutesLate"] for r in rows] == [45, 10]

    def test_only_returns_late_status_not_present_or_absent(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        present_learner = learner_factory(cohort_id=cohort["id"])
        late_learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _record(db, session_row["id"], present_learner["id"], "present", hours_attended=6)
        _record(db, session_row["id"], late_learner["id"], "late", hours_attended=5, minutes_late=5)

        rows, total = fetch_lateness_rows(db, period_start=PERIOD[0], period_end=PERIOD[1], cohort_id=cohort["id"])
        assert total == 1
        assert rows[0]["learnerId"] == late_learner["id"]


class TestFetchRegisterCompletionRows:
    def test_status_transitions_not_started_in_progress_completed(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_a = learner_factory(cohort_id=cohort["id"])
        learner_b = learner_factory(cohort_id=cohort["id"])
        s_not_started = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        s_in_progress = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-07", created_by=admin_user["userId"])
        s_completed = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-08", created_by=admin_user["userId"])
        for s in (s_not_started, s_in_progress, s_completed):
            _snapshot(db, s)
        _record(db, s_in_progress["id"], learner_a["id"], "present", hours_attended=6)
        # learner_b in s_in_progress has no record -- exactly one of two expected.
        _record(db, s_completed["id"], learner_a["id"], "present", hours_attended=6)
        _record(db, s_completed["id"], learner_b["id"], "present", hours_attended=6)

        rows, total = fetch_register_completion_rows(db, period_start=PERIOD[0], period_end=PERIOD[1], cohort_id=cohort["id"])
        by_id = {r["sessionId"]: r for r in rows}
        assert total == 3
        assert by_id[s_not_started["id"]]["registerStatus"] == "not_started"
        assert by_id[s_in_progress["id"]]["registerStatus"] == "in_progress"
        assert by_id[s_in_progress["id"]]["missingRowCount"] == 1
        assert by_id[s_completed["id"]]["registerStatus"] == "completed"
        assert by_id[s_completed["id"]]["missingRowCount"] == 0

    def test_locked_register_takes_priority_over_completion_state(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _snapshot(db, session_row)
        _record(db, session_row["id"], learner["id"], "present", hours_attended=6)
        db.execute("UPDATE attendance_sessions SET register_locked_at = now() WHERE id = %s", (session_row["id"],))

        rows, _ = fetch_register_completion_rows(db, period_start=PERIOD[0], period_end=PERIOD[1], cohort_id=cohort["id"])
        assert rows[0]["registerStatus"] == "locked"

    def test_register_status_filter(self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        s_not_started = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        s_completed = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-07", created_by=admin_user["userId"])
        _snapshot(db, s_not_started)
        _snapshot(db, s_completed)
        _record(db, s_completed["id"], learner["id"], "present", hours_attended=6)

        rows, total = fetch_register_completion_rows(
            db, period_start=PERIOD[0], period_end=PERIOD[1], cohort_id=cohort["id"], register_status="completed"
        )
        assert total == 1
        assert rows[0]["sessionId"] == s_completed["id"]


class TestFetchAllocationHistoryRows:
    def test_effective_to_is_derived_from_the_next_transfer(self, db, admin_user, learner_factory, tutor_factory):
        tutor_a = tutor_factory()
        tutor_b = tutor_factory()
        tutor_c = tutor_factory()
        learner = learner_factory(tutor_id=tutor_c["tutorId"])
        db.execute(
            "INSERT INTO learner_allocation_history (learner_id, previous_tutor_id, new_tutor_id, effective_date, changed_by) "
            "VALUES (%s, %s, %s, '2026-01-01', %s)",
            (learner["id"], tutor_a["tutorId"], tutor_b["tutorId"], admin_user["userId"]),
        )
        db.execute(
            "INSERT INTO learner_allocation_history (learner_id, previous_tutor_id, new_tutor_id, effective_date, changed_by) "
            "VALUES (%s, %s, %s, '2026-02-01', %s)",
            (learner["id"], tutor_b["tutorId"], tutor_c["tutorId"], admin_user["userId"]),
        )

        rows, total = fetch_allocation_history_rows(db, learner_id=learner["id"])
        assert total == 2
        by_effective_date = {str(r["effectiveDate"]): r for r in rows}
        first_transfer = by_effective_date["2026-01-01"]
        second_transfer = by_effective_date["2026-02-01"]
        assert str(first_transfer["effectiveTo"]) == "2026-02-01"
        assert second_transfer["effectiveTo"] is None
        assert first_transfer["learnerName"]
        assert first_transfer["newTutorName"]

    def test_tutor_filter_matches_both_previous_and_new(self, db, admin_user, learner_factory, tutor_factory):
        tutor_a = tutor_factory()
        tutor_b = tutor_factory()
        learner = learner_factory(tutor_id=tutor_b["tutorId"])
        db.execute(
            "INSERT INTO learner_allocation_history (learner_id, previous_tutor_id, new_tutor_id, effective_date, changed_by) "
            "VALUES (%s, %s, %s, '2026-01-01', %s)",
            (learner["id"], tutor_a["tutorId"], tutor_b["tutorId"], admin_user["userId"]),
        )

        rows_a, total_a = fetch_allocation_history_rows(db, tutor_id=tutor_a["tutorId"])
        rows_b, total_b = fetch_allocation_history_rows(db, tutor_id=tutor_b["tutorId"])
        assert total_a == 1
        assert total_b == 1
        assert rows_a[0]["id"] == rows_b[0]["id"]


class TestFetchLearnerSessionHistory:
    def test_missing_record_shows_null_status_not_absence(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _snapshot(db, session_row)
        # No attendance_records row at all for this learner+session.

        rows, total = fetch_learner_session_history(db, learner_id=learner["id"], period_start=PERIOD[0], period_end=PERIOD[1])
        assert total == 1
        assert rows[0]["status"] is None
        assert rows[0]["registerStatus"] == "not_started"

    def test_cancelled_sessions_are_excluded_from_history(
        self, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _snapshot(db, session_row)
        db.execute("UPDATE attendance_sessions SET status = 'cancelled' WHERE id = %s", (session_row["id"],))

        rows, total = fetch_learner_session_history(db, learner_id=learner["id"], period_start=PERIOD[0], period_end=PERIOD[1])
        assert total == 0
