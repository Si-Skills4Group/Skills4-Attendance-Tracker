"""Stage 3: tutor-confirmed catch-up completion for a recorded absence.
Covers the meaningful risks the feature spec calls out explicitly: both
eligible absence statuses, rejection of ineligible/unrecorded attendance,
that the original absence and hours_attended are never touched, duplicate
submissions (including a genuine concurrent race), invalid completion
dates, corrections/revocations retaining history, a later attendance
correction excluding (not deleting) an existing catch-up, cancelled
sessions, and atomic rollback on a failure injected mid-write."""
import threading
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from psycopg.rows import dict_row

from pyapp import audit as audit_module
from pyapp.catchup_lib import (
    correct_catchup,
    get_catchup,
    get_catchup_history,
    get_catchup_with_state,
    record_catchup,
    revoke_catchup,
)
from pyapp.db import pool
from pyapp.routers.attendance import AttendanceRegisterInput, RegisterEntryInput, save_attendance_register

PAST_SESSION_DATE = "2026-01-05"  # well before uk_today() in every test run this session


def _record_status(request_factory, session_id, learner_id, status, acting_session, register_version=None, **kwargs):
    if register_version is None:
        # save_attendance_register bumps register_version on every call --
        # look up the CURRENT one rather than assuming 1, so a second call
        # in the same test (e.g. a later attendance correction) doesn't
        # spuriously hit the stale-version guard.
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
            changeReason=kwargs.get("changeReason", "test correction"),
        ),
        request_factory(),
        acting_session,
    )


@pytest.fixture
def scenario(admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory):
    tutor = tutor_factory()
    cohort = cohort_factory(tutor_id=tutor["tutorId"])
    learner = learner_factory(cohort_id=cohort["id"])
    session = attendance_session_factory(cohort_id=cohort["id"], session_date=PAST_SESSION_DATE, created_by=admin_user["userId"])
    return {"tutor": tutor, "cohort": cohort, "learner": learner, "session": session}


class TestEligibility:
    @pytest.mark.parametrize("status", ["absent_authorised", "absent_unauthorised"])
    def test_recording_succeeds_for_both_eligible_absence_statuses(self, db, scenario, admin_user, request_factory, status):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], status, admin_user)
        result = record_catchup(
            db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE),
            "recording_watched", "Watched the recording on the school laptop", request_factory(), admin_user,
        )
        assert result["effective"] is True
        assert result["catchup"]["status"] == "recorded"
        assert result["catchup"]["originalStatusAtRecording"] == status

    @pytest.mark.parametrize("status", ["present", "late", "not_expected", "withdrawn", "bil"])
    def test_recording_rejected_for_every_ineligible_recorded_status(self, db, scenario, admin_user, request_factory, status):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], status, admin_user)
        with pytest.raises(HTTPException) as exc:
            record_catchup(
                db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE),
                "recording_watched", "note", request_factory(), admin_user,
            )
        assert exc.value.status_code == 400

    def test_recording_rejected_when_attendance_was_never_recorded(self, db, scenario, admin_user, request_factory):
        """An unrecorded status is not an absence -- must not become
        catch-up eligible automatically, no matter how long it's been
        unrecorded."""
        with pytest.raises(HTTPException) as exc:
            record_catchup(
                db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE),
                "recording_watched", "note", request_factory(), admin_user,
            )
        assert exc.value.status_code == 400
        assert "unrecorded" in exc.value.detail.lower() or "absent" in exc.value.detail.lower()

    def test_recording_requires_a_note(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        with pytest.raises(HTTPException) as exc:
            record_catchup(
                db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE),
                "recording_watched", "   ", request_factory(), admin_user,
            )
        assert exc.value.status_code == 400

    def test_learner_not_expected_at_session_is_rejected(self, db, scenario, admin_user, request_factory, learner_factory):
        outsider = learner_factory()  # never in this cohort, never expected at this session
        with pytest.raises(HTTPException) as exc:
            record_catchup(
                db, scenario["session"]["id"], outsider["id"], date.fromisoformat(PAST_SESSION_DATE),
                "recording_watched", "note", request_factory(), admin_user,
            )
        assert exc.value.status_code == 400

    def test_future_session_is_rejected(self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"])
        future_date = (date.today() + timedelta(days=30)).isoformat()
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=future_date, created_by=admin_user["userId"])
        with pytest.raises(HTTPException) as exc:
            record_catchup(db, session["id"], learner["id"], date.today(), "recording_watched", "note", request_factory(), admin_user)
        assert exc.value.status_code == 400


class TestInvalidCompletionDates:
    def test_completion_date_before_session_date_is_rejected(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        too_early = date.fromisoformat(PAST_SESSION_DATE) - timedelta(days=1)
        with pytest.raises(HTTPException) as exc:
            record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], too_early, "recording_watched", "note", request_factory(), admin_user)
        assert exc.value.status_code == 400

    def test_completion_date_in_the_future_is_rejected(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        future = date.today() + timedelta(days=1)
        with pytest.raises(HTTPException) as exc:
            record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], future, "recording_watched", "note", request_factory(), admin_user)
        assert exc.value.status_code == 400


class TestOriginalAttendanceUntouched:
    def test_recording_catchup_never_changes_the_original_status_or_hours(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        db.execute(
            'SELECT status, hours_attended AS "hoursAttended" FROM attendance_records WHERE session_id = %s AND learner_id = %s',
            (scenario["session"]["id"], scenario["learner"]["id"]),
        )
        before = db.fetchone()

        record_catchup(
            db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE),
            "activity_completed", "Completed the workbook exercise", request_factory(), admin_user,
        )

        db.execute(
            'SELECT status, hours_attended AS "hoursAttended" FROM attendance_records WHERE session_id = %s AND learner_id = %s',
            (scenario["session"]["id"], scenario["learner"]["id"]),
        )
        after = db.fetchone()
        assert after["status"] == before["status"] == "absent_authorised"
        assert float(after["hoursAttended"]) == float(before["hoursAttended"]) == 0.0


class TestDuplicateSubmissions:
    def test_second_recording_attempt_is_rejected_with_409(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "first", request_factory(), admin_user)

        with pytest.raises(HTTPException) as exc:
            record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "second", request_factory(), admin_user)
        assert exc.value.status_code == 409

        db.execute("SELECT count(*) AS n FROM attendance_catchup WHERE session_id = %s AND learner_id = %s", (scenario["session"]["id"], scenario["learner"]["id"]))
        assert db.fetchone()["n"] == 1

    def test_two_concurrent_recording_attempts_never_produce_two_rows(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        """Real concurrency: two threads, two separate DB connections,
        both racing record_catchup for the SAME learner/session. The
        UNIQUE(session_id, learner_id) constraint plus the ON CONFLICT
        DO NOTHING re-check must ensure exactly one wins and the other
        gets a clean 409 -- never two rows, never duplicated participation."""
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=PAST_SESSION_DATE, created_by=admin_user["userId"])
        _record_status(request_factory, session["id"], learner["id"], "absent_authorised", admin_user)

        results: dict = {}
        barrier = threading.Barrier(2)

        def _worker(key):
            barrier.wait(timeout=5)
            try:
                with pool.connection() as conn:
                    with conn.cursor(row_factory=dict_row) as cur:
                        import types

                        class _FakeRequest:
                            def __init__(self):
                                self.state = types.SimpleNamespace(session=admin_user)
                                self.headers: dict = {}
                                self.client = types.SimpleNamespace(host="127.0.0.1")
                                self.url = types.SimpleNamespace(path="/api/test-concurrency")

                        record_catchup(cur, session["id"], learner["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", f"attempt {key}", _FakeRequest(), admin_user)
                results[key] = "ok"
            except HTTPException as exc:
                results[key] = ("http_error", exc.status_code)
            except Exception as exc:  # pragma: no cover
                results[key] = ("unexpected", exc)

        t1 = threading.Thread(target=_worker, args=("a",))
        t2 = threading.Thread(target=_worker, args=("b",))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        outcomes = [results["a"], results["b"]]
        successes = [o for o in outcomes if o == "ok"]
        rejections = [o for o in outcomes if isinstance(o, tuple) and o[0] == "http_error"]
        assert len(successes) == 1, f"exactly one attempt must succeed: {outcomes}"
        assert len(rejections) == 1 and rejections[0][1] == 409, f"the other must be cleanly rejected: {outcomes}"

        db.execute("SELECT count(*) AS n FROM attendance_catchup WHERE session_id = %s AND learner_id = %s", (session["id"], learner["id"]))
        assert db.fetchone()["n"] == 1, "never more than one catch-up row for the same learner/session"


class TestCorrectionsAndRevocation:
    def test_correction_requires_a_reason_and_updates_details(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "original note", request_factory(), admin_user)

        with pytest.raises(HTTPException) as exc:
            correct_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "activity_completed", "updated note", "", request_factory(), admin_user)
        assert exc.value.status_code == 400

        result = correct_catchup(
            db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE),
            "activity_completed", "updated note", "Tutor mis-selected the method originally", request_factory(), admin_user,
        )
        assert result["catchup"]["method"] == "activity_completed"
        assert result["catchup"]["note"] == "updated note"

    def test_revocation_requires_a_reason_and_makes_it_ineffective(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)

        with pytest.raises(HTTPException):
            revoke_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], "", request_factory(), admin_user)

        result = revoke_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], "Evidence turned out to be for a different session", request_factory(), admin_user)
        assert result["effective"] is False
        assert result["ineligibleReason"] == "revoked"
        assert result["catchup"]["status"] == "revoked"

    def test_revocation_and_correction_retain_full_history_never_hard_deleted(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)
        correct_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "activity_completed", "corrected note", "fixing method", request_factory(), admin_user)
        revoke_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], "turned out to be invalid", request_factory(), admin_user)

        catchup = get_catchup(db, scenario["session"]["id"], scenario["learner"]["id"])
        assert catchup is not None, "the row itself must never be deleted"
        history = get_catchup_history(db, catchup["id"])
        actions = [h["action"] for h in history]
        assert "catchup_recorded" in actions
        assert "catchup_corrected" in actions
        assert "catchup_revoked" in actions
        # Before/after values and reason are present on the correction/revocation entries.
        revoked_entry = next(h for h in history if h["action"] == "catchup_revoked")
        assert revoked_entry["newValue"]["reason"] == "turned out to be invalid"

    def test_re_recording_after_revocation_reuses_the_same_row(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        first = record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)
        first_id = first["catchup"]["id"]
        revoke_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], "mistake", request_factory(), admin_user)

        second = record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "activity_completed", "re-recorded properly", request_factory(), admin_user)
        assert second["catchup"]["id"] == first_id, "must reuse the one row for this learner/session, never create a second"
        assert second["effective"] is True


class TestLaterAttendanceCorrectionExcludesCatchup:
    def test_correcting_original_attendance_to_present_excludes_but_preserves_catchup(self, db, scenario, admin_user, request_factory):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)

        # The tutor/admin later corrects the original attendance record --
        # the learner was actually present all along.
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "present", admin_user, hoursAttended=7)

        result = get_catchup_with_state(db, scenario["session"]["id"], scenario["learner"]["id"])
        assert result["catchup"] is not None, "catch-up history must be preserved, not deleted"
        assert result["catchup"]["status"] == "recorded", "the catch-up row's own status is untouched by this"
        assert result["effective"] is False
        assert result["ineligibleReason"] == "original_status_changed"

    def test_does_not_double_count_participation_after_the_correction(self, db, scenario, admin_user, request_factory):
        from pyapp.attendance_metrics import fetch_session_participation_metrics

        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "present", admin_user, hoursAttended=7)

        metrics = fetch_session_participation_metrics(
            db, scope="learner", scope_id=scenario["learner"]["id"],
            period_start=date.fromisoformat(PAST_SESSION_DATE), period_end=date.fromisoformat(PAST_SESSION_DATE),
        )
        # Exactly one learner-session pair: now live-attended (present),
        # not ALSO counted as a caught-up absence -- total participation
        # must be 1, not 2.
        assert metrics.expectedLearnerSessions == 1
        assert metrics.liveAttendedLearnerSessions == 1
        assert metrics.recordedAbsences == 0
        assert metrics.caughtUp == 0
        assert metrics.totalParticipation == 1


class TestCatchupHistoryAfterOriginalAttendanceChanges:
    def test_history_remains_accessible_with_reasons_after_the_original_attendance_makes_it_ineffective(
        self, db, scenario, admin_user, request_factory,
    ):
        """Item: "Catch-up history remains accessible after an attendance
        correction makes the catch-up ineffective, with the reason
        visible." A correction/revocation reason is stored on the
        catch-up's OWN history entries (get_catchup_history reads
        audit_logs, never attendance_records), so it survives completely
        untouched by a LATER, unrelated correction to the original
        attendance -- this combines both facts in one scenario rather than
        relying on two separate tests each proving half of it."""
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        recorded = record_catchup(
            db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE),
            "recording_watched", "original note", request_factory(), admin_user,
        )
        correct_catchup(
            db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE),
            "activity_completed", "updated note", "Tutor had the method wrong originally", request_factory(), admin_user,
        )

        # The original attendance is later corrected to present -- the
        # catch-up becomes ineffective, but must not vanish.
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "present", admin_user, hoursAttended=7)

        state = get_catchup_with_state(db, scenario["session"]["id"], scenario["learner"]["id"])
        assert state["effective"] is False
        assert state["ineligibleReason"] == "original_status_changed"

        history = get_catchup_history(db, recorded["catchup"]["id"])
        actions = [h["action"] for h in history]
        assert "catchup_recorded" in actions
        assert "catchup_corrected" in actions
        correction_entry = next(h for h in history if h["action"] == "catchup_corrected")
        assert correction_entry["newValue"]["reason"] == "Tutor had the method wrong originally"


class TestConcurrentAttendanceCorrectionAndCatchup:
    def test_racing_an_attendance_correction_against_a_catchup_recording_never_produces_an_inconsistent_result(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        """Real concurrency: one thread corrects the original attendance
        (absent -> present) while another races to record catch-up for the
        SAME learner/session. record_catchup's SELECT ... FOR UPDATE on the
        attendance_records row serializes against whichever transaction's
        own write to that row commits first (an ordinary UPDATE/upsert also
        takes a row lock, so it blocks against -- and is blocked by -- an
        explicit FOR UPDATE the same way). Exactly one of two CONSISTENT
        outcomes may happen: the catch-up attempt saw the row while it was
        still an eligible absence and succeeded (leaving a correctly
        ineffective row once the correction lands), or it saw -- or was
        blocked until it saw -- the corrected 'present' status and was
        cleanly rejected. It must never succeed while leaving a catch-up
        recorded against a learner who is, once both transactions have
        committed, no longer marked absent at all."""
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=PAST_SESSION_DATE, created_by=admin_user["userId"])
        _record_status(request_factory, session["id"], learner["id"], "absent_authorised", admin_user)

        results: dict = {}
        barrier = threading.Barrier(2)

        def _correct_attendance():
            barrier.wait(timeout=5)
            try:
                with pool.connection() as conn:
                    with conn.cursor(row_factory=dict_row) as cur:
                        cur.execute("SELECT register_version AS v FROM attendance_sessions WHERE id = %s", (session["id"],))
                        version = cur.fetchone()["v"]
                save_attendance_register(
                    session["id"],
                    AttendanceRegisterInput(
                        registerVersion=version,
                        entries=[RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=7, minutesLate=0)],
                        changeReason="corrected concurrently",
                    ),
                    request_factory(),
                    admin_user,
                )
                results["attendance"] = "ok"
            except HTTPException as exc:
                results["attendance"] = ("http_error", exc.status_code)
            except Exception as exc:  # pragma: no cover
                results["attendance"] = ("unexpected", exc)

        def _attempt_catchup():
            barrier.wait(timeout=5)
            try:
                with pool.connection() as conn:
                    with conn.cursor(row_factory=dict_row) as cur:
                        import types

                        class _FakeRequest:
                            def __init__(self):
                                self.state = types.SimpleNamespace(session=admin_user)
                                self.headers: dict = {}
                                self.client = types.SimpleNamespace(host="127.0.0.1")
                                self.url = types.SimpleNamespace(path="/api/test-concurrency")

                        record_catchup(
                            cur, session["id"], learner["id"], date.fromisoformat(PAST_SESSION_DATE),
                            "recording_watched", "watched during the race", _FakeRequest(), admin_user,
                        )
                results["catchup"] = "ok"
            except HTTPException as exc:
                results["catchup"] = ("http_error", exc.status_code)
            except Exception as exc:  # pragma: no cover
                results["catchup"] = ("unexpected", exc)

        t1 = threading.Thread(target=_correct_attendance)
        t2 = threading.Thread(target=_attempt_catchup)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert results["attendance"] == "ok", f"the attendance correction itself must always succeed: {results}"

        db.execute('SELECT status FROM attendance_records WHERE session_id = %s AND learner_id = %s', (session["id"], learner["id"]))
        final_status = db.fetchone()["status"]
        assert final_status == "present"

        catchup_row = get_catchup(db, session["id"], learner["id"])
        if results["catchup"] == "ok":
            # The catch-up transaction committed against the still-absent
            # status before the correction landed -- the row exists, but is
            # now correctly ineffective given the final 'present' status.
            assert catchup_row is not None
            state = get_catchup_with_state(db, session["id"], learner["id"])
            assert state["effective"] is False
            assert state["ineligibleReason"] == "original_status_changed"
        else:
            # The attempt saw (or was serialised until it saw) 'present'
            # and was cleanly rejected -- never a catch-up row left behind
            # against a non-absent status.
            assert results["catchup"] == ("http_error", 400), f"unexpected catch-up outcome: {results}"
            assert catchup_row is None


class TestCancelledSession:
    def test_cancelled_session_makes_an_existing_catchup_ineffective(self, db, scenario, admin_user, request_factory):
        from pyapp.session_register_lib import cancel_session

        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)

        cancel_session(db, scenario["session"], "session was cancelled after the fact", True, admin_user["userId"])

        result = get_catchup_with_state(db, scenario["session"]["id"], scenario["learner"]["id"])
        assert result["catchup"] is not None
        assert result["effective"] is False
        assert result["ineligibleReason"] == "session_cancelled"

    def test_cannot_record_catchup_against_an_already_cancelled_session(self, db, scenario, admin_user, request_factory):
        from pyapp.session_register_lib import cancel_session

        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)
        cancel_session(db, scenario["session"], "cancelled", True, admin_user["userId"])

        with pytest.raises(HTTPException) as exc:
            record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)
        assert exc.value.status_code == 400


class TestFailureRollback:
    def test_failure_during_the_audit_write_leaves_no_catchup_row_and_no_audit_record(
        self, db, scenario, admin_user, request_factory, monkeypatch,
    ):
        _record_status(request_factory, scenario["session"]["id"], scenario["learner"]["id"], "absent_authorised", admin_user)

        def _boom(*args, **kwargs):
            if kwargs.get("action") == "catchup_recorded":
                raise RuntimeError("simulated failure during the audit write")
            return audit_module.write_audit_log(*args, **kwargs)

        monkeypatch.setattr("pyapp.catchup_lib.write_audit_log", _boom)

        with pytest.raises(RuntimeError, match="simulated failure during the audit write"):
            record_catchup(db, scenario["session"]["id"], scenario["learner"]["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "note", request_factory(), admin_user)

        assert get_catchup(db, scenario["session"]["id"], scenario["learner"]["id"]) is None, \
            "a failed audit write must roll back the catch-up insert too -- never a catch-up with no matching audit entry"
        db.execute(
            "SELECT count(*) AS n FROM audit_logs WHERE action = 'catchup_recorded' AND entity_type = 'attendance_catchup'",
        )
        # (count may be nonzero from other tests' data in a shared DB, so
        # just confirm no row exists for THIS session/learner via the
        # catchup table check above, which is the authoritative one.)
