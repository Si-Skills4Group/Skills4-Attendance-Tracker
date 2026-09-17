"""Now that a learner can be expected in more than one cohort (a home
cohort plus a Functional Skills secondary enrollment), every tutor/cohort-
scoped dashboard widget must restrict a learner's attendance figures to
that tutor's/cohort's own sessions -- never blend in the learner's
unrelated attendance elsewhere. See routers/dashboard.py::_low_attendance_rows
and its restrict_to_cohort_ids parameter."""
from datetime import date

from pyapp.routers.dashboard import _low_attendance_rows, get_admin_dashboard_tutors, get_tutor_dashboard
from pyapp.session_register_lib import ensure_expected_learners_snapshot

PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 1, 31)


def _snapshot(db, session: dict):
    ensure_expected_learners_snapshot(db, session["id"], session["cohort_id"], date.fromisoformat(session["session_date"]))


def _record(db, session_id, learner_id, status, hours_attended=0):
    db.execute(
        "INSERT INTO attendance_records (session_id, learner_id, status, hours_attended) VALUES (%s, %s, %s, %s)",
        (session_id, learner_id, status, hours_attended),
    )


def _make_sessions(db, admin_user, cohort_id, learner_id, status, count=3, start_day=date(2026, 1, 6)):
    for offset in range(count):
        session_date = date.fromordinal(start_day.toordinal() + offset)
        db.execute(
            """
            INSERT INTO attendance_sessions (cohort_id, session_date, planned_start_time, planned_end_time,
                                              planned_duration_hours, created_by)
            VALUES (%s, %s, '09:00', '16:00', 6, %s) RETURNING id
            """,
            (cohort_id, session_date, admin_user["userId"]),
        )
        session_id = db.fetchone()["id"]
        ensure_expected_learners_snapshot(db, session_id, cohort_id, session_date)
        _record(db, session_id, learner_id, status, hours_attended=6 if status == "present" else 0)


class TestLowAttendanceRowsCohortScoping:
    def test_a_learner_perfect_at_home_but_flagged_only_in_their_functional_skills_cohort(
        self, db, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        home = cohort_factory()
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=home["id"], start_date="2026-01-01")
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        _make_sessions(db, admin_user, home["id"], learner["id"], "present")
        _make_sessions(db, admin_user, fs_cohort["id"], learner["id"], "absent_unauthorised")

        learner_row = {**learner, "firstName": "Test", "lastName": "Learner", "learnerRef": learner["learner_ref"]}

        home_scoped = _low_attendance_rows(db, [learner_row], 85.0, PERIOD_START, PERIOD_END, cohort_ids=[home["id"]])
        assert home_scoped == [], "perfect home attendance must not be flagged when scoped to the home cohort"

        fs_scoped = _low_attendance_rows(db, [learner_row], 85.0, PERIOD_START, PERIOD_END, cohort_ids=[fs_cohort["id"]])
        assert {r["learnerId"] for r in fs_scoped} == {learner["id"]}, "zero FS attendance must be flagged when scoped to the FS cohort"


class TestTutorDashboardIncludesSecondarilyEnrolledLearners:
    def test_fs_tutors_own_dashboard_reflects_only_their_own_cohort(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        # get_tutor_dashboard scopes its metrics to "current month" (today's
        # real calendar month, not a test-fixed period), so the sessions
        # here must fall within it rather than a hardcoded 2026-01 date.
        today = date.today()
        month_start = today.replace(day=1)
        enrolled_date = month_start.isoformat()

        home_tutor = tutor_factory()
        fs_tutor = tutor_factory()
        home = cohort_factory(tutor_id=home_tutor["tutorId"])
        fs_cohort = cohort_factory(tutor_id=fs_tutor["tutorId"], membership_type="secondary")
        learner = learner_factory(tutor_id=home_tutor["tutorId"], cohort_id=home["id"], start_date=enrolled_date)
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date=enrolled_date)

        _make_sessions(db, admin_user, home["id"], learner["id"], "present", start_day=month_start)
        _make_sessions(db, admin_user, fs_cohort["id"], learner["id"], "absent_unauthorised", start_day=month_start)

        result = get_tutor_dashboard(fs_tutor["session"])
        fs_cohort_summary = next(c for c in result["cohorts"] if c["cohort"]["id"] == fs_cohort["id"])
        assert fs_cohort_summary["learnerCount"] == 1

        low_attendance_ids = {r["learnerId"] for r in result["lowAttendanceLearners"]}
        assert learner["id"] in low_attendance_ids, "FS tutor's dashboard must flag this learner's FS attendance"

        home_result = get_tutor_dashboard(home_tutor["session"])
        home_low_attendance_ids = {r["learnerId"] for r in home_result["lowAttendanceLearners"]}
        assert learner["id"] not in home_low_attendance_ids, "home tutor's dashboard must not see the FS-only shortfall"

    def test_admin_tutor_breakdown_counts_secondarily_enrolled_learners(
        self, db, admin_user, request_factory, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        home_tutor = tutor_factory()
        fs_tutor = tutor_factory()
        home = cohort_factory(tutor_id=home_tutor["tutorId"])
        fs_cohort = cohort_factory(tutor_id=fs_tutor["tutorId"], membership_type="secondary")
        learner = learner_factory(tutor_id=home_tutor["tutorId"], cohort_id=home["id"], start_date="2026-01-01")
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        rows = get_admin_dashboard_tutors(period="custom", dateFrom=PERIOD_START, dateTo=PERIOD_END, _session=admin_user)
        fs_row = next(r for r in rows["items"] if r["tutorId"] == fs_tutor["tutorId"])
        assert fs_row["activeLearners"] == 1
