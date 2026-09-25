"""Stage 3: session-based participation including catch-up
(fetch_session_participation_metrics). The acceptance example from the
feature spec is reproduced exactly (item 7): 10 eligible expected
learner-sessions -- 6 present, 1 late, 2 absent (one later caught up), 1
never recorded -- must yield live=7, recordedAbsences=2, caughtUp=1,
absencesWithoutCatchup=1, attendanceNotRecorded=1, totalParticipation=8,
participationRate=80%, with the existing minutes-based attendancePercentage
completely unaffected."""
from datetime import date, timedelta

import pytest
from psycopg.rows import dict_row

from pyapp.attendance_metrics import fetch_attendance_metrics, fetch_session_participation_metrics
from pyapp.catchup_lib import record_catchup
from pyapp.db import pool
from pyapp.routers.attendance import AttendanceRegisterInput, RegisterEntryInput, save_attendance_register

SESSION_DATE = "2026-01-05"


def _record(request_factory, session_id, learner_id, status, acting_session, register_version=None, **kwargs):
    if register_version is None:
        # save_attendance_register bumps register_version on every call --
        # several learners are recorded against the SAME session in
        # sequence in this file's fixture, so each call must use the
        # CURRENT version, not an assumed 1.
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT register_version AS v FROM attendance_sessions WHERE id = %s", (session_id,))
                register_version = cur.fetchone()["v"]
    minutes_late = kwargs.get("minutesLate", 5 if status == "late" else 0)
    return save_attendance_register(
        session_id,
        AttendanceRegisterInput(
            registerVersion=register_version,
            entries=[RegisterEntryInput(learnerId=learner_id, status=status, hoursAttended=kwargs.get("hoursAttended", 0), minutesLate=minutes_late)],
        ),
        request_factory(),
        acting_session,
    )


@pytest.fixture
def ten_learner_session(admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory):
    """10 expected learner-sessions, ALL against one single session --
    matching the acceptance example's population exactly: 6 present, 1
    late, 2 absent, 1 never recorded. Deliberately one session, not several:
    every learner in a cohort is "expected" at every one of that cohort's
    own sessions, so splitting this same set of 10 learners across two
    sessions in the SAME cohort would make each of them expected at BOTH
    (20 learner-sessions, not 10) -- not what the spec's acceptance example
    describes."""
    tutor = tutor_factory()
    cohort = cohort_factory(tutor_id=tutor["tutorId"])
    session = attendance_session_factory(cohort_id=cohort["id"], session_date=SESSION_DATE, created_by=admin_user["userId"])

    present_learners = [learner_factory(cohort_id=cohort["id"]) for _ in range(6)]
    late_learner = learner_factory(cohort_id=cohort["id"])
    absent_learners = [learner_factory(cohort_id=cohort["id"]) for _ in range(2)]
    unrecorded_learner = learner_factory(cohort_id=cohort["id"])

    for learner in present_learners:
        _record(request_factory, session["id"], learner["id"], "present", admin_user, hoursAttended=7)
    _record(request_factory, session["id"], late_learner["id"], "late", admin_user, hoursAttended=6)
    _record(request_factory, session["id"], absent_learners[0]["id"], "absent_authorised", admin_user)
    _record(request_factory, session["id"], absent_learners[1]["id"], "absent_unauthorised", admin_user)
    # unrecorded_learner: deliberately left untouched -- their own presence
    # in the cohort at snapshot time is what makes them "expected", the
    # same session_expected_learners row every other learner here has, just
    # with no matching attendance_records row (ar.status IS NULL).

    return {
        "cohort": cohort, "session": session,
        "absent_authorised_learner_id": absent_learners[0]["id"],
        "unrecorded_learner_id": unrecorded_learner["id"],
    }


class TestAcceptanceExample:
    def test_matches_the_spec_acceptance_example_exactly(self, db, ten_learner_session):
        metrics = fetch_session_participation_metrics(
            db, scope="cohort", scope_id=ten_learner_session["cohort"]["id"],
            period_start=date.fromisoformat(SESSION_DATE), period_end=date.fromisoformat(SESSION_DATE),
        )
        assert metrics.expectedLearnerSessions == 10
        assert metrics.liveAttendedLearnerSessions == 7
        assert metrics.recordedAbsences == 2
        assert metrics.absencesWithoutCatchup == 2  # nobody has caught up yet
        assert metrics.caughtUp == 0
        assert metrics.attendanceNotRecorded == 1
        assert metrics.totalParticipation == 7
        assert metrics.participationRate == 70.0

    def test_after_one_absence_catches_up(self, db, ten_learner_session, admin_user, request_factory):
        cohort_id = ten_learner_session["cohort"]["id"]
        record_catchup(
            db, ten_learner_session["session"]["id"], ten_learner_session["absent_authorised_learner_id"],
            date.fromisoformat(SESSION_DATE), "recording_watched", "Watched the recorded session", request_factory(), admin_user,
        )

        metrics = fetch_session_participation_metrics(
            db, scope="cohort", scope_id=cohort_id,
            period_start=date.fromisoformat(SESSION_DATE), period_end=date.fromisoformat(SESSION_DATE),
        )
        assert metrics.expectedLearnerSessions == 10
        assert metrics.liveAttendedLearnerSessions == 7
        assert metrics.recordedAbsences == 2
        assert metrics.caughtUp == 1
        assert metrics.absencesWithoutCatchup == 1
        assert metrics.attendanceNotRecorded == 1
        assert metrics.totalParticipation == 8
        assert metrics.participationRate == 80.0

    def test_existing_minutes_based_percentage_is_unaffected_by_catchup(self, db, ten_learner_session, admin_user, request_factory):
        """The pre-existing attendancePercentage formula must not move by
        even a fraction of a point because of a catch-up confirmation --
        same query, same column, completely untouched by this feature."""
        cohort_id = ten_learner_session["cohort"]["id"]
        before = fetch_attendance_metrics(
            db, scope="cohort", scope_id=cohort_id,
            period_start=date.fromisoformat(SESSION_DATE), period_end=date.fromisoformat(SESSION_DATE),
        )

        record_catchup(
            db, ten_learner_session["session"]["id"], ten_learner_session["absent_authorised_learner_id"],
            date.fromisoformat(SESSION_DATE), "recording_watched", "note", request_factory(), admin_user,
        )

        after = fetch_attendance_metrics(
            db, scope="cohort", scope_id=cohort_id,
            period_start=date.fromisoformat(SESSION_DATE), period_end=date.fromisoformat(SESSION_DATE),
        )
        assert after.attendancePercentage == before.attendancePercentage
        assert after.attendedMinutes == before.attendedMinutes
        assert after.expectedMinutes == before.expectedMinutes


class TestParticipationFormulaEdgeCases:
    def test_zero_expected_returns_no_percentage(self, db, cohort_factory):
        cohort = cohort_factory()
        metrics = fetch_session_participation_metrics(
            db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        assert metrics.expectedLearnerSessions == 0
        assert metrics.participationRate is None

    def test_future_sessions_are_excluded(self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner_factory(cohort_id=cohort["id"])
        future_date = (date.today() + timedelta(days=10)).isoformat()
        attendance_session_factory(cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"])

        metrics = fetch_session_participation_metrics(
            db, scope="cohort", scope_id=cohort["id"], period_start=date.today(), period_end=date(2027, 1, 1),
        )
        assert metrics.expectedLearnerSessions == 0

    def test_a_revoked_catchup_does_not_count(self, db, ten_learner_session, admin_user, request_factory):
        from pyapp.catchup_lib import revoke_catchup

        cohort_id = ten_learner_session["cohort"]["id"]
        session_id = ten_learner_session["session"]["id"]
        learner_id = ten_learner_session["absent_authorised_learner_id"]
        record_catchup(db, session_id, learner_id, date.fromisoformat(SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)
        revoke_catchup(db, session_id, learner_id, "was recorded in error", request_factory(), admin_user)

        metrics = fetch_session_participation_metrics(
            db, scope="cohort", scope_id=cohort_id,
            period_start=date.fromisoformat(SESSION_DATE), period_end=date.fromisoformat(SESSION_DATE),
        )
        assert metrics.caughtUp == 0
        assert metrics.totalParticipation == 7

    def test_not_expected_withdrawn_and_bil_are_excluded_from_expected_learner_sessions(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        """Same exclusions as the existing minutes-based fetch_attendance_
        metrics -- a learner recorded not_expected/withdrawn/bil at a
        session is still in session_expected_learners (the frozen roster),
        but must NOT count toward expectedLearnerSessions here, exactly as
        it doesn't count toward expectedMinutes there."""
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=SESSION_DATE, created_by=admin_user["userId"])

        present_learner = learner_factory(cohort_id=cohort["id"])
        not_expected_learner = learner_factory(cohort_id=cohort["id"])
        withdrawn_learner = learner_factory(cohort_id=cohort["id"])
        bil_learner = learner_factory(cohort_id=cohort["id"])

        _record(request_factory, session["id"], present_learner["id"], "present", admin_user, hoursAttended=6)
        _record(request_factory, session["id"], not_expected_learner["id"], "not_expected", admin_user)
        _record(request_factory, session["id"], withdrawn_learner["id"], "withdrawn", admin_user)
        _record(request_factory, session["id"], bil_learner["id"], "bil", admin_user)

        metrics = fetch_session_participation_metrics(
            db, scope="cohort", scope_id=cohort["id"],
            period_start=date.fromisoformat(SESSION_DATE), period_end=date.fromisoformat(SESSION_DATE),
        )
        assert metrics.expectedLearnerSessions == 1
        assert metrics.liveAttendedLearnerSessions == 1
        assert metrics.totalParticipation == 1

    def test_cancelled_sessions_are_excluded_from_expected_learner_sessions(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        from pyapp.session_register_lib import cancel_session

        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=SESSION_DATE, created_by=admin_user["userId"])
        _record(request_factory, session["id"], learner["id"], "absent_authorised", admin_user)

        cancel_session(db, session, "cancelled after the fact", True, admin_user["userId"])

        metrics = fetch_session_participation_metrics(
            db, scope="cohort", scope_id=cohort["id"],
            period_start=date.fromisoformat(SESSION_DATE), period_end=date.fromisoformat(SESSION_DATE),
        )
        assert metrics.expectedLearnerSessions == 0
        assert metrics.recordedAbsences == 0


class TestFunctionalSkillsCoreSeparation:
    """Core (home-cohort) and Functional Skills (secondary-cohort) results
    are two entirely separate attendance_sessions rows, even for the same
    learner on the same date -- a catch-up is tied to one specific
    session_id, so a core-session catch-up can structurally never appear
    when a query is scoped to an FS cohort's own sessions. This locks that
    in with a concrete cross-cohort scenario, not just by inspection."""

    def test_core_cohort_catchup_never_appears_in_functional_skills_scoped_metrics(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory,
        secondary_enrollment_factory, request_factory,
    ):
        tutor = tutor_factory()
        core_cohort = cohort_factory(tutor_id=tutor["tutorId"])
        fs_cohort = cohort_factory(membership_type="secondary", subject="math")
        learner = learner_factory(cohort_id=core_cohort["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        core_session = attendance_session_factory(cohort_id=core_cohort["id"], session_date=SESSION_DATE, created_by=admin_user["userId"])
        fs_session = attendance_session_factory(cohort_id=fs_cohort["id"], session_date=SESSION_DATE, created_by=admin_user["userId"])

        _record(request_factory, core_session["id"], learner["id"], "absent_authorised", admin_user)
        _record(request_factory, fs_session["id"], learner["id"], "absent_authorised", admin_user)

        # Catch up only the CORE absence.
        record_catchup(db, core_session["id"], learner["id"], date.fromisoformat(SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)

        core_metrics = fetch_session_participation_metrics(
            db, scope="cohort", scope_id=core_cohort["id"],
            period_start=date.fromisoformat(SESSION_DATE), period_end=date.fromisoformat(SESSION_DATE),
        )
        fs_metrics = fetch_session_participation_metrics(
            db, scope="cohort", scope_id=fs_cohort["id"],
            period_start=date.fromisoformat(SESSION_DATE), period_end=date.fromisoformat(SESSION_DATE),
        )

        assert core_metrics.caughtUp == 1
        assert core_metrics.totalParticipation == 1
        # The FS cohort has its OWN absence for the same learner, still with
        # no catch-up of its own -- the core catch-up must never propagate.
        assert fs_metrics.expectedLearnerSessions == 1
        assert fs_metrics.recordedAbsences == 1
        assert fs_metrics.caughtUp == 0
        assert fs_metrics.totalParticipation == 0
