"""Functional Skills secondary cohort enrollment: a learner can be added to
a SECOND ('secondary' membership_type) cohort concurrently with their home
cohort, via a dedicated admin-only mechanism that never touches
learners.cohort_id/tutor_id, apply_transfer, or learner_allocation_history."""
import datetime

import pytest
from fastapi import HTTPException

from pyapp.routers.secondary_enrollments import (
    SecondaryEnrollmentEndInput,
    SecondaryEnrollmentInput,
    create_learner_secondary_enrollment,
    end_learner_secondary_enrollment,
    get_cohort_secondary_enrollments,
    get_learner_secondary_enrollments,
)


class TestEnrollLearnerInSecondaryCohort:
    def test_enrolls_successfully_and_does_not_touch_home_cohort(
        self, db, request_factory, admin_user, cohort_factory, learner_factory,
    ):
        home = cohort_factory()
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=home["id"])

        result = create_learner_secondary_enrollment(
            learner["id"],
            SecondaryEnrollmentInput(cohortId=fs_cohort["id"], enrolledDate=datetime.date(2026, 2, 1), reason="Needs Maths support"),
            request_factory(),
            admin_user,
        )
        assert result["cohortId"] == fs_cohort["id"]
        assert result["status"] == "active"

        db.execute("SELECT cohort_id, tutor_id FROM learners WHERE id = %s", (learner["id"],))
        row = db.fetchone()
        assert row["cohort_id"] == home["id"]

    def test_is_audited(self, db, request_factory, admin_user, cohort_factory, learner_factory):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        create_learner_secondary_enrollment(
            learner["id"],
            SecondaryEnrollmentInput(cohortId=fs_cohort["id"], enrolledDate=datetime.date(2026, 2, 1)),
            request_factory(),
            admin_user,
        )
        db.execute(
            "SELECT * FROM audit_logs WHERE action = 'create_secondary_enrollment' AND entity_id = %s",
            (learner["id"],),
        )
        assert db.fetchone() is not None

    def test_rejects_a_primary_cohort_as_the_target(self, request_factory, admin_user, cohort_factory, learner_factory):
        primary_cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        with pytest.raises(HTTPException) as exc:
            create_learner_secondary_enrollment(
                learner["id"],
                SecondaryEnrollmentInput(cohortId=primary_cohort["id"], enrolledDate=datetime.date(2026, 2, 1)),
                request_factory(),
                admin_user,
            )
        assert exc.value.status_code == 422

    def test_rejects_enrolling_into_the_learners_own_home_cohort(self, request_factory, admin_user, cohort_factory, learner_factory):
        # A cohort's membership_type could in principle be flipped, but
        # enrolling a learner into what is currently *their own* home cohort
        # is never meaningful regardless -- guarded independently of the
        # membership_type check.
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=fs_cohort["id"])
        with pytest.raises(HTTPException) as exc:
            create_learner_secondary_enrollment(
                learner["id"],
                SecondaryEnrollmentInput(cohortId=fs_cohort["id"], enrolledDate=datetime.date(2026, 2, 1)),
                request_factory(),
                admin_user,
            )
        assert exc.value.status_code == 422

    def test_rejects_a_duplicate_active_enrollment(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        with pytest.raises(HTTPException) as exc:
            create_learner_secondary_enrollment(
                learner["id"],
                SecondaryEnrollmentInput(cohortId=fs_cohort["id"], enrolledDate=datetime.date(2026, 2, 1)),
                request_factory(),
                admin_user,
            )
        assert exc.value.status_code == 409


class TestEndSecondaryEnrollment:
    def test_ends_successfully(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        enrollment = secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        result = end_learner_secondary_enrollment(
            enrollment["id"],
            SecondaryEnrollmentEndInput(endDate=datetime.date(2026, 4, 1), reason="Passed Functional Skills"),
            request_factory(),
            admin_user,
        )
        assert result["status"] == "ended"
        assert str(result["endDate"]) == "2026-04-01"

        db.execute("SELECT status, end_date FROM learner_cohort_enrollments WHERE id = %s", (enrollment["id"],))
        row = db.fetchone()
        assert row["status"] == "ended"
        assert row["end_date"] == datetime.date(2026, 4, 1)

    def test_cannot_end_an_already_ended_enrollment(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        enrollment = secondary_enrollment_factory(
            learner_id=learner["id"], cohort_id=fs_cohort["id"], status="ended", end_date="2026-03-01",
        )
        with pytest.raises(HTTPException) as exc:
            end_learner_secondary_enrollment(
                enrollment["id"],
                SecondaryEnrollmentEndInput(endDate=datetime.date(2026, 4, 1)),
                request_factory(),
                admin_user,
            )
        assert exc.value.status_code == 400

    def test_the_owning_tutor_can_mark_completion_by_ending_their_own_cohorts_enrollment(
        self, db, request_factory, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        """A Functional Skills tutor doesn't need to be an admin to mark a
        learner as having completed the course with them -- scoped to their
        own cohort via the same require_cohort_access rule as everywhere
        else, never their uploaded requirement (a different table this
        never touches)."""
        fs_tutor = tutor_factory()
        fs_cohort = cohort_factory(tutor_id=fs_tutor["tutorId"], membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        enrollment = secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])
        fs_tutor_session = {"userId": fs_tutor["userId"], "role": "tutor", "tutorId": fs_tutor["tutorId"]}

        result = end_learner_secondary_enrollment(
            enrollment["id"],
            SecondaryEnrollmentEndInput(endDate=datetime.date(2026, 4, 1), reason="Completed the course"),
            request_factory(),
            fs_tutor_session,
        )
        assert result["status"] == "ended"

    def test_a_different_tutor_cannot_end_another_tutors_cohorts_enrollment(
        self, request_factory, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_tutor = tutor_factory()
        other_tutor = tutor_factory()
        fs_cohort = cohort_factory(tutor_id=fs_tutor["tutorId"], membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        enrollment = secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])
        other_tutor_session = {"userId": other_tutor["userId"], "role": "tutor", "tutorId": other_tutor["tutorId"]}

        with pytest.raises(HTTPException) as exc:
            end_learner_secondary_enrollment(
                enrollment["id"],
                SecondaryEnrollmentEndInput(endDate=datetime.date(2026, 4, 1), reason="Not my learner"),
                request_factory(),
                other_tutor_session,
            )
        assert exc.value.status_code == 403

        db_check_result = get_learner_secondary_enrollments(learner["id"], admin_user)
        assert db_check_result[0]["status"] == "active", "the denied attempt must not have ended it"

    def test_ending_stops_future_session_expectation_but_never_touches_the_uploaded_requirement(
        self, db, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        """The two explicit acceptance points: the learner is no longer
        expected in a FUTURE session of this cohort once their enrollment
        is ended, and their uploaded Functional Skills requirement (a
        completely separate table/concept) is never touched by this."""
        from pyapp.allocation_lib import learners_expected_in_cohort_as_of

        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"], start_date="2026-01-01")
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")
        db.execute(
            "INSERT INTO learner_fs_requirements (learner_id, maths, english, status, updated_by) VALUES (%s, true, false, 'recorded', %s)",
            (learner["id"], admin_user["userId"]),
        )

        assert learner["id"] in learners_expected_in_cohort_as_of(db, fs_cohort["id"], datetime.date(2026, 4, 1))

        end_learner_secondary_enrollment(
            _get_active_enrollment_id(db, learner["id"], fs_cohort["id"]),
            SecondaryEnrollmentEndInput(endDate=datetime.date(2026, 4, 1), reason="Completed the course"),
            request_factory(),
            admin_user,
        )

        assert learner["id"] not in learners_expected_in_cohort_as_of(db, fs_cohort["id"], datetime.date(2026, 4, 2))

        db.execute("SELECT maths, english, status FROM learner_fs_requirements WHERE learner_id = %s", (learner["id"],))
        requirement = db.fetchone()
        assert (requirement["maths"], requirement["english"], requirement["status"]) == (True, False, "recorded")


def _get_active_enrollment_id(db, learner_id: int, cohort_id: int) -> int:
    db.execute(
        "SELECT id FROM learner_cohort_enrollments WHERE learner_id = %s AND cohort_id = %s AND status = 'active'",
        (learner_id, cohort_id),
    )
    return db.fetchone()["id"]


class TestListSecondaryEnrollments:
    def test_lists_for_learner_and_for_cohort(
        self, request_factory, admin_user, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"])

        by_learner = get_learner_secondary_enrollments(learner["id"], admin_user)
        assert len(by_learner) == 1
        assert by_learner[0]["cohortId"] == fs_cohort["id"]

        by_cohort = get_cohort_secondary_enrollments(fs_cohort["id"], admin_user)
        assert len(by_cohort) == 1
        assert by_cohort[0]["learnerId"] == learner["id"]
