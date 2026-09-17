"""functionalSkillsSubjects: a small flag/badge next to a learner's name
wherever they're listed, showing which Functional Skills subject(s)
(math/english/both) they're currently actively enrolled in. Backed by
secondary_enrollment_lib.functional_skills_subjects_sql, wired into the
Learners directory / Allocation screen (LEARNERS_WITH_NAMES_SELECT) and the
attendance register roster (routers/attendance.py)."""
import datetime

from pyapp.routers.attendance import (
    AttendanceSessionInput,
    create_attendance_session,
    get_attendance_session,
    get_session_expected_learners,
)
from pyapp.routers.learners import list_learners


class TestFunctionalSkillsSubjectsOnLearnerLists:
    def test_empty_for_a_learner_with_no_secondary_enrollment(self, request_factory, admin_user, learner_factory):
        learner = learner_factory(cohort_id=None)
        result = list_learners(search=learner["learner_ref"], session=admin_user)
        assert result["items"][0]["functionalSkillsSubjects"] == []

    def test_shows_the_subject_of_an_active_enrollment(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary", subject="math")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        result = list_learners(search=learner["learner_ref"], session=admin_user)
        assert result["items"][0]["functionalSkillsSubjects"] == ["math"]

    def test_shows_multiple_distinct_subjects_when_enrolled_in_both(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        maths_cohort = cohort_factory(membership_type="secondary", subject="math")
        english_cohort = cohort_factory(membership_type="secondary", subject="english")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=maths_cohort["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=english_cohort["id"])

        result = list_learners(search=learner["learner_ref"], session=admin_user)
        assert sorted(result["items"][0]["functionalSkillsSubjects"]) == ["english", "math"]

    def test_ended_enrollment_no_longer_shows(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary", subject="both")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        secondary_enrollment_factory(
            learner_id=learner["id"], cohort_id=fs_cohort["id"], status="ended", end_date="2026-01-01",
        )

        result = list_learners(search=learner["learner_ref"], session=admin_user)
        assert result["items"][0]["functionalSkillsSubjects"] == []


class TestFunctionalSkillsSubjectsOnRegisterRoster:
    def test_shows_on_the_register_and_expected_learners_endpoints(
        self, db, admin_user, request_factory, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        home_cohort = cohort_factory()
        fs_cohort = cohort_factory(membership_type="secondary", subject="english")
        learner = learner_factory(cohort_id=home_cohort["id"], start_date="2026-01-01")
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        session = create_attendance_session(
            AttendanceSessionInput(
                cohortId=fs_cohort["id"], sessionDate=datetime.date(2026, 2, 1),
                plannedStartTime="09:00", plannedEndTime="16:00", plannedDurationHours=7,
                title="English Functional Skills",
            ),
            request_factory(), admin_user,
        )

        register = get_attendance_session(session["id"], admin_user)
        assert register["entries"][0]["functionalSkillsSubjects"] == ["english"]

        expected = get_session_expected_learners(session["id"], admin_user)
        assert expected[0]["functionalSkillsSubjects"] == ["english"]

        db.execute("DELETE FROM session_expected_learners WHERE session_id = %s", (session["id"],))
        db.execute("DELETE FROM attendance_sessions WHERE id = %s", (session["id"],))
