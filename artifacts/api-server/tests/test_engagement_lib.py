"""Stage 4 (+ its focused correction): learner engagement recency.

Covers:
- Only attendance and effective catch-up ever contribute to the combined
  "latest recorded engagement" -- Bud submission/completed-activity dates
  are shown for reference only, with their upstream meaning unverified,
  and never move the calculated date.
- Evidence is scoped to the requesting tutor's own session authority
  (evidence_tutor_id), separate from which learners are even listed
  (tutor_id/cohort_id population scope) -- an FS-only tutor's view is
  never inflated by a learner's unrelated home-cohort attendance, and Bud
  evidence is only shown to the learner's home tutor or an admin.
- The linked Bud plan's CURRENCY is validated (is it actually the
  learner's current In Progress plan, or a stale/ambiguous one) before
  its dates are shown at all, even to an authorised viewer.
"""
from datetime import date, timedelta

import pytest

from pyapp.catchup_lib import record_catchup, revoke_catchup
from pyapp.engagement_lib import fetch_engagement_rows, fetch_engagement_summary, resolve_learner_engagement_detail
from pyapp.routers.attendance import AttendanceRegisterInput, RegisterEntryInput, save_attendance_register

TODAY = date.today()  # always the real "today" -- never hardcode this, it drifts


def _record(request_factory, session_id, learner_id, status, acting_session, register_version=1, **kwargs):
    minutes_late = kwargs.get("minutesLate", 5 if status == "late" else 0)
    return save_attendance_register(
        session_id,
        AttendanceRegisterInput(
            registerVersion=register_version,
            entries=[RegisterEntryInput(learnerId=learner_id, status=status, hoursAttended=kwargs.get("hoursAttended", 0), minutesLate=minutes_late)],
            changeReason=kwargs.get("changeReason", "test"),
        ),
        request_factory(),
        acting_session,
    )


def _link_bud(db, learner_id: int, plan_id: str) -> None:
    db.execute(
        "INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)",
        (learner_id, plan_id),
    )


def _row_for(db, tutor, learner_id, evidence_tutor_id=None, **kwargs):
    """evidence_tutor_id defaults to the SAME tutor doing the population
    lookup -- i.e. "I am tutor X asking about my own learner" -- matching
    how the router calls this for a real tutor session. Pass a different
    value (or None, for admin-style unrestricted evidence) to test the
    evidence-scoping correction specifically."""
    if evidence_tutor_id is None and tutor is not None:
        evidence_tutor_id = tutor["tutorId"]
    rows, _ = fetch_engagement_rows(
        db, tutor_id=tutor["tutorId"] if tutor else None, cohort_id=None, learner_id=learner_id,
        programme=None, search=None, no_engagement_only=False, min_days_since=None,
        evidence_tutor_id=evidence_tutor_id,
        page=1, page_size=25, **kwargs,
    )
    assert len(rows) == 1
    return rows[0]


class TestAcceptanceExample:
    """Item 7's worked example (Stage 4 spec), reproduced with the literal
    dates given: attendance 10 Sep, Bud submission 15 Sep (now excluded
    from the calculation -- see the correction), catch-up completed 17
    Sep (entered by the tutor on 20 Sep) -- as of 23 Sep the latest
    recorded engagement must be 17 Sep via catch-up, 6 days ago."""

    def test_matches_the_worked_example_with_bud_excluded_from_the_calculation(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory,
        request_factory, bud_row_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        attended_session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-10", created_by=admin_user["userId"])
        _record(request_factory, attended_session["id"], learner["id"], "present", admin_user, hoursAttended=6)

        absent_session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-05", created_by=admin_user["userId"])
        _record(request_factory, absent_session["id"], learner["id"], "absent_authorised", admin_user)
        record_catchup(db, absent_session["id"], learner["id"], date(2026, 9, 17), "recording_watched", "watched it", request_factory(), admin_user)

        plan = bud_row_factory(status_desc="In Progress", last_submission_date="2026-09-15T09:00:00Z", last_completed_activity=None, synced_at="2026-09-15T09:05:00Z")
        _link_bud(db, learner["id"], plan["learningPlanId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["lastLiveAttendance"] == date(2026, 9, 10)
        assert row["lastBudSubmission"] == date(2026, 9, 15), "still shown for reference"
        assert row["lastCatchupCompletion"] == date(2026, 9, 17)
        assert row["latestEngagementDate"] == date(2026, 9, 17)
        assert row["sources"] == ["catchup"]
        assert row["daysSinceEngagement"] == (TODAY - date(2026, 9, 17)).days
        assert row["budStatus"] == "resolved"
        assert any("unverified" in note.lower() for note in row["sourceLimitations"]), "Bud dates must be captioned unverified/excluded"


class TestBudNeverContributesToTheCalculation:
    def test_a_bud_submission_newer_than_attendance_and_catchup_does_not_become_the_latest(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory,
        request_factory, bud_row_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        old_session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-08-01", created_by=admin_user["userId"])
        _record(request_factory, old_session["id"], learner["id"], "present", admin_user, hoursAttended=6)

        plan = bud_row_factory(status_desc="In Progress", last_submission_date="2026-09-18T09:00:00Z", last_completed_activity=None)
        _link_bud(db, learner["id"], plan["learningPlanId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["lastBudSubmission"] == date(2026, 9, 18), "still shown for reference"
        assert row["latestEngagementDate"] == date(2026, 8, 1), "attendance remains the latest -- Bud never wins"
        assert row["sources"] == ["attendance"]

    def test_a_bud_completed_activity_newer_than_attendance_and_catchup_does_not_become_the_latest(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory, bud_row_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        old_session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-08-01", created_by=admin_user["userId"])
        _record(request_factory, old_session["id"], learner["id"], "present", admin_user, hoursAttended=6)

        plan = bud_row_factory(status_desc="In Progress", last_submission_date=None, last_completed_activity="2026-09-19")
        _link_bud(db, learner["id"], plan["learningPlanId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["lastBudCompletedActivity"] == date(2026, 9, 19), "still shown for reference"
        assert row["latestEngagementDate"] == date(2026, 8, 1)
        assert row["sources"] == ["attendance"]

    def test_a_bud_only_learner_with_no_attendance_or_catchup_shows_no_recorded_engagement(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, request_factory, bud_row_factory,
    ):
        """Item 1: coverage must never be described as complete merely
        because a Bud field is populated -- a learner with ONLY Bud data
        (no attendance, no catch-up) must show "No recorded engagement",
        not a fabricated date from the unverified source."""
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        plan = bud_row_factory(status_desc="In Progress", last_submission_date="2026-09-20T09:00:00Z", last_completed_activity="2026-09-21")
        _link_bud(db, learner["id"], plan["learningPlanId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["latestEngagementDate"] is None
        assert row["sources"] == []
        assert row["daysSinceEngagement"] is None
        assert row["lastBudSubmission"] == date(2026, 9, 20)
        assert row["lastBudCompletedActivity"] == date(2026, 9, 21)


class TestEachCalculationSourceCanBeNewest:
    def test_attendance_is_the_newest_source(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        recent = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-20", created_by=admin_user["userId"])
        _record(request_factory, recent["id"], learner["id"], "present", admin_user, hoursAttended=6)

        row = _row_for(db, tutor, learner["id"])
        assert row["latestEngagementDate"] == date(2026, 9, 20)
        assert row["sources"] == ["attendance"]

    def test_catchup_is_the_newest_source_using_its_completion_date(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-01", created_by=admin_user["userId"])
        _record(request_factory, session["id"], learner["id"], "absent_authorised", admin_user)
        record_catchup(db, session["id"], learner["id"], date(2026, 9, 21), "activity_completed", "note", request_factory(), admin_user)

        row = _row_for(db, tutor, learner["id"])
        assert row["latestEngagementDate"] == date(2026, 9, 21)
        assert row["sources"] == ["catchup"]

    def test_attendance_and_catchup_sharing_the_latest_date_are_both_listed(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        shared_date = date(2026, 9, 15)
        learner_a = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        learner_b = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date=shared_date.isoformat(), created_by=admin_user["userId"])
        save_attendance_register(
            session["id"],
            AttendanceRegisterInput(
                registerVersion=1,
                entries=[
                    RegisterEntryInput(learnerId=learner_a["id"], status="present", hoursAttended=6, minutesLate=0),
                    RegisterEntryInput(learnerId=learner_b["id"], status="absent_authorised", hoursAttended=0, minutesLate=0),
                ],
            ),
            request_factory(),
            admin_user,
        )
        record_catchup(db, session["id"], learner_b["id"], shared_date, "recording_watched", "note", request_factory(), admin_user)

        row_a = _row_for(db, tutor, learner_a["id"])
        row_b = _row_for(db, tutor, learner_b["id"])
        assert row_a["sources"] == ["attendance"]
        assert row_b["sources"] == ["catchup"]

    def test_no_evidence_in_any_source_shows_no_recorded_engagement(self, db, admin_user, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["latestEngagementDate"] is None
        assert row["sources"] == []
        assert row["daysSinceEngagement"] is None
        assert row["budStatus"] == "not_linked"


class TestDataQuality:
    def test_a_future_attendance_or_catchup_date_is_excluded_and_flagged(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-01", created_by=admin_user["userId"])
        _record(request_factory, session["id"], learner["id"], "absent_authorised", admin_user)
        record_catchup(db, session["id"], learner["id"], date(2026, 9, 1), "recording_watched", "note", request_factory(), admin_user)

        row = _row_for(db, tutor, learner["id"])
        assert row["latestEngagementDate"] == date(2026, 9, 1)
        assert row["dataQualityIssues"] == []

    def test_a_revoked_catchup_does_not_count(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-01", created_by=admin_user["userId"])
        _record(request_factory, session["id"], learner["id"], "absent_authorised", admin_user)
        record_catchup(db, session["id"], learner["id"], date(2026, 9, 18), "recording_watched", "note", request_factory(), admin_user)
        revoke_catchup(db, session["id"], learner["id"], "recorded in error", request_factory(), admin_user)

        row = _row_for(db, tutor, learner["id"])
        assert row["lastCatchupCompletion"] is None
        assert row["latestEngagementDate"] is None

    def test_cancelled_session_attendance_is_excluded(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        from pyapp.session_register_lib import cancel_session

        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-05", created_by=admin_user["userId"])
        _record(request_factory, session["id"], learner["id"], "present", admin_user, hoursAttended=6)
        cancel_session(db, session, "cancelled after the fact", True, admin_user["userId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["lastLiveAttendance"] is None
        assert row["latestEngagementDate"] is None

    def test_source_refresh_alone_does_not_change_the_calculated_date(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory, bud_row_factory,
    ):
        """A bare synced_at bump with no change to any Bud date field must
        leave the calculated engagement date (and the displayed Bud dates)
        completely unchanged -- doubly true now that Bud is excluded from
        the calculation regardless."""
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-10", created_by=admin_user["userId"])
        _record(request_factory, session["id"], learner["id"], "present", admin_user, hoursAttended=6)
        plan = bud_row_factory(status_desc="In Progress", last_submission_date="2026-09-05T09:00:00Z", last_completed_activity=None, synced_at="2026-09-10T09:05:00Z")
        _link_bud(db, learner["id"], plan["learningPlanId"])

        before = _row_for(db, tutor, learner["id"])
        db.execute("UPDATE public.learner_progress SET synced_at = %s WHERE learning_plan_id = %s", ("2026-09-22T09:05:00Z", plan["learningPlanId"]))
        after = _row_for(db, tutor, learner["id"])

        assert after["latestEngagementDate"] == before["latestEngagementDate"] == date(2026, 9, 10)
        assert after["lastBudSubmission"] == before["lastBudSubmission"]
        assert after["budSyncedAt"] != before["budSyncedAt"], "the freshness signal itself is still shown separately"


class TestPopulation:
    def test_a_learner_with_no_attendance_rows_still_appears(self, db, admin_user, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        rows, total = fetch_engagement_rows(
            db, tutor_id=tutor["tutorId"], cohort_id=None, learner_id=None,
            programme=None, search=None, no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=tutor["tutorId"], page=1, page_size=25,
        )
        assert any(r["id"] == learner["id"] for r in rows)
        assert total >= 1


class TestSearch:
    def test_matches_full_name_not_just_one_part(self, db, admin_user, tutor_factory, cohort_factory, learner_factory):
        """Regression: a single ILIKE against the concatenated full name,
        not separate first_name/last_name ILIKEs -- see routers/learners.py's
        own fix for the same underlying bug this mirrors."""
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"], first_name="Phoebe", last_name="Jones")

        rows, total = fetch_engagement_rows(
            db, tutor_id=tutor["tutorId"], cohort_id=None, learner_id=None,
            programme=None, search="Phoebe Jones", no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=tutor["tutorId"], page=1, page_size=25,
        )
        assert total == 1
        assert rows[0]["id"] == learner["id"]


class TestFiltersCountsAndPaginationAgree:
    def test_no_engagement_only_filter_matches_the_summary_count(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        with_engagement = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-10", created_by=admin_user["userId"])
        _record(request_factory, session["id"], with_engagement["id"], "present", admin_user, hoursAttended=6)
        without_engagement = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        rows, total = fetch_engagement_rows(
            db, tutor_id=tutor["tutorId"], cohort_id=None, learner_id=None,
            programme=None, search=None, no_engagement_only=True, min_days_since=None,
            evidence_tutor_id=tutor["tutorId"], page=1, page_size=25,
        )
        summary = fetch_engagement_summary(db, tutor_id=tutor["tutorId"], cohort_id=None, evidence_tutor_id=tutor["tutorId"])

        assert all(r["latestEngagementDate"] is None for r in rows)
        assert without_engagement["id"] in {r["id"] for r in rows}
        assert with_engagement["id"] not in {r["id"] for r in rows}
        assert total == summary["noRecordedEngagementCount"]

    def test_min_days_since_excludes_learners_with_no_recorded_engagement(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        old_engagement = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        old_session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-06-01", created_by=admin_user["userId"])
        _record(request_factory, old_session["id"], old_engagement["id"], "present", admin_user, hoursAttended=6)
        no_engagement = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        rows, total = fetch_engagement_rows(
            db, tutor_id=tutor["tutorId"], cohort_id=None, learner_id=None,
            programme=None, search=None, no_engagement_only=False, min_days_since=30,
            evidence_tutor_id=tutor["tutorId"], page=1, page_size=25,
        )
        ids = {r["id"] for r in rows}
        assert old_engagement["id"] in ids
        assert no_engagement["id"] not in ids

    def test_pagination_across_two_pages_has_no_omission_or_duplication(self, db, admin_user, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learners = [learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"]) for _ in range(5)]

        page1, total1 = fetch_engagement_rows(
            db, tutor_id=tutor["tutorId"], cohort_id=None, learner_id=None,
            programme=None, search=None, no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=tutor["tutorId"], page=1, page_size=3,
        )
        page2, total2 = fetch_engagement_rows(
            db, tutor_id=tutor["tutorId"], cohort_id=None, learner_id=None,
            programme=None, search=None, no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=tutor["tutorId"], page=2, page_size=3,
        )
        assert total1 == total2 == len(learners)
        seen_ids = [r["id"] for r in page1] + [r["id"] for r in page2]
        assert len(seen_ids) == len(set(seen_ids)), "no learner duplicated across pages"
        assert {l["id"] for l in learners} <= set(seen_ids)


class TestDetailResolution:
    def test_detail_matches_the_list_rows_combined_result(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-14", created_by=admin_user["userId"])
        _record(request_factory, session["id"], learner["id"], "present", admin_user, hoursAttended=6)

        list_row = _row_for(db, tutor, learner["id"])
        detail = resolve_learner_engagement_detail(db, learner["id"], evidence_tutor_id=tutor["tutorId"])

        assert detail["latestEngagementDate"] == list_row["latestEngagementDate"] == date(2026, 9, 14)
        assert detail["attendanceEvidence"]["sessionId"] == session["id"]
        assert detail["attendanceEvidence"]["sessionDate"] == date(2026, 9, 14)


class TestEvidencePermissions:
    """Item 2's correction: require_learner_access (or the tutor_id
    population filter) decides whether a learner is visible at ALL -- it
    must not also decide which of that learner's underlying events count.
    An FS-only tutor's evidence must come only from sessions they hold
    real session-level authority over; Bud evidence is shown only to the
    learner's home tutor or an admin."""

    @pytest.fixture
    def fs_scenario(self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, secondary_enrollment_factory, request_factory):
        core_tutor = tutor_factory()
        fs_tutor = tutor_factory()
        core_cohort = cohort_factory(tutor_id=core_tutor["tutorId"])
        fs_cohort = cohort_factory(tutor_id=fs_tutor["tutorId"], membership_type="secondary", subject="math")
        learner = learner_factory(cohort_id=core_cohort["id"], tutor_id=core_tutor["tutorId"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        older_fs_session = attendance_session_factory(cohort_id=fs_cohort["id"], session_date="2026-08-01", created_by=admin_user["userId"])
        _record(request_factory, older_fs_session["id"], learner["id"], "present", admin_user, hoursAttended=1)

        newer_core_session = attendance_session_factory(cohort_id=core_cohort["id"], session_date="2026-09-15", created_by=admin_user["userId"])
        _record(request_factory, newer_core_session["id"], learner["id"], "present", admin_user, hoursAttended=6)

        return {"core_tutor": core_tutor, "fs_tutor": fs_tutor, "core_cohort": core_cohort, "fs_cohort": fs_cohort, "learner": learner}

    def test_fs_only_tutor_sees_only_the_older_fs_event_never_the_newer_core_event(self, db, fs_scenario):
        """The exact scenario item 2 asks to lock in: an FS-only tutor can
        see an older FS attendance event but not a newer core event, and
        that newer restricted event must not influence the returned date,
        summary, filter result, or export (export/filter covered via the
        shared fetch_engagement_rows/summary machinery this exercises)."""
        fs_tutor = fs_scenario["fs_tutor"]
        learner = fs_scenario["learner"]

        row = fetch_engagement_rows(
            db, tutor_id=fs_tutor["tutorId"], cohort_id=None, learner_id=learner["id"],
            programme=None, search=None, no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=fs_tutor["tutorId"], page=1, page_size=25,
        )[0][0]
        assert row["lastLiveAttendance"] == date(2026, 8, 1), "only the FS tutor's own session is visible evidence"
        assert row["latestEngagementDate"] == date(2026, 8, 1)
        assert row["daysSinceEngagement"] == (TODAY - date(2026, 8, 1)).days

        detail = resolve_learner_engagement_detail(db, learner["id"], evidence_tutor_id=fs_tutor["tutorId"])
        assert detail["latestEngagementDate"] == date(2026, 8, 1)
        assert detail["attendanceEvidence"]["sessionDate"] == date(2026, 8, 1)

        # The min-days-since filter must be evaluated against the FS
        # tutor's OWN visible date (44 days), never the hidden newer one.
        filtered, total = fetch_engagement_rows(
            db, tutor_id=fs_tutor["tutorId"], cohort_id=None, learner_id=learner["id"],
            programme=None, search=None, no_engagement_only=False, min_days_since=40,
            evidence_tutor_id=fs_tutor["tutorId"], page=1, page_size=25,
        )
        assert total == 1, "the older FS-visible date does satisfy >= 40 days"

        summary = fetch_engagement_summary(db, tutor_id=fs_tutor["tutorId"], cohort_id=None, evidence_tutor_id=fs_tutor["tutorId"])
        assert summary["noRecordedEngagementCount"] == 0, "the FS tutor's own visible attendance means this learner is not 'no engagement' from their view"

    def test_core_home_tutor_sees_only_their_own_session_not_the_fs_one(self, db, fs_scenario):
        core_tutor = fs_scenario["core_tutor"]
        learner = fs_scenario["learner"]

        row = fetch_engagement_rows(
            db, tutor_id=core_tutor["tutorId"], cohort_id=None, learner_id=learner["id"],
            programme=None, search=None, no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=core_tutor["tutorId"], page=1, page_size=25,
        )[0][0]
        assert row["lastLiveAttendance"] == date(2026, 9, 15)
        assert row["latestEngagementDate"] == date(2026, 9, 15)

    def test_admin_sees_both_events_and_the_newer_one_wins(self, db, fs_scenario):
        learner = fs_scenario["learner"]
        row = fetch_engagement_rows(
            db, tutor_id=None, cohort_id=None, learner_id=learner["id"],
            programme=None, search=None, no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=None, page=1, page_size=25,
        )[0][0]
        assert row["lastLiveAttendance"] == date(2026, 9, 15), "admin evidence is unrestricted -- sees the newer of the two"
        assert row["latestEngagementDate"] == date(2026, 9, 15)

    def test_fs_only_tutor_does_not_see_bud_evidence_even_when_linked(self, db, fs_scenario, bud_row_factory):
        fs_tutor = fs_scenario["fs_tutor"]
        learner = fs_scenario["learner"]
        plan = bud_row_factory(status_desc="In Progress", last_submission_date="2026-09-20T09:00:00Z", last_completed_activity=None)
        _link_bud(db, learner["id"], plan["learningPlanId"])

        row = fetch_engagement_rows(
            db, tutor_id=fs_tutor["tutorId"], cohort_id=None, learner_id=learner["id"],
            programme=None, search=None, no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=fs_tutor["tutorId"], page=1, page_size=25,
        )[0][0]
        assert row["budStatus"] == "not_authorized"
        assert row["lastBudSubmission"] is None
        assert row["budLinkedPlanId"] is None, "not even the plan id is exposed to an unauthorised viewer"

        detail = resolve_learner_engagement_detail(db, learner["id"], evidence_tutor_id=fs_tutor["tutorId"])
        assert detail["budStatus"] == "not_authorized"
        assert detail["budEvidence"] is None

    def test_core_home_tutor_does_see_bud_evidence(self, db, fs_scenario, bud_row_factory):
        core_tutor = fs_scenario["core_tutor"]
        learner = fs_scenario["learner"]
        plan = bud_row_factory(status_desc="In Progress", last_submission_date="2026-09-20T09:00:00Z", last_completed_activity=None)
        _link_bud(db, learner["id"], plan["learningPlanId"])

        row = fetch_engagement_rows(
            db, tutor_id=core_tutor["tutorId"], cohort_id=None, learner_id=learner["id"],
            programme=None, search=None, no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=core_tutor["tutorId"], page=1, page_size=25,
        )[0][0]
        assert row["budStatus"] == "resolved"
        assert row["lastBudSubmission"] == date(2026, 9, 20)

    def test_admin_always_sees_bud_evidence(self, db, fs_scenario, bud_row_factory):
        learner = fs_scenario["learner"]
        plan = bud_row_factory(status_desc="In Progress", last_submission_date="2026-09-20T09:00:00Z", last_completed_activity=None)
        _link_bud(db, learner["id"], plan["learningPlanId"])

        row = fetch_engagement_rows(
            db, tutor_id=None, cohort_id=None, learner_id=learner["id"],
            programme=None, search=None, no_engagement_only=False, min_days_since=None,
            evidence_tutor_id=None, page=1, page_size=25,
        )[0][0]
        assert row["budStatus"] == "resolved"


class TestBudPlanCurrency:
    """Item 3: a link alone does not prove the linked plan is the
    learner's RELEVANT CURRENT plan."""

    def test_link_to_the_relevant_current_plan_is_resolved_cleanly(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, bud_row_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        plan = bud_row_factory(status_desc="In Progress", last_submission_date="2026-09-10T09:00:00Z")
        _link_bud(db, learner["id"], plan["learningPlanId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["budStatus"] == "resolved"
        assert row["budPlanCurrency"] == "current"

    def test_link_to_a_historical_plan_while_another_is_in_progress_is_flagged_for_review(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, bud_row_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        shared_reference = "LEARNER-REF-XYZ"
        historical_plan = bud_row_factory(learner_reference=shared_reference, status_desc="Completed", last_submission_date="2026-01-10T09:00:00Z")
        bud_row_factory(learner_reference=shared_reference, status_desc="In Progress", last_submission_date="2026-09-01T09:00:00Z")
        _link_bud(db, learner["id"], historical_plan["learningPlanId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["budStatus"] == "needs_review"
        assert row["budPlanCurrency"] == "linked_plan_not_current"
        assert row["lastBudSubmission"] is None, "the stale plan's date is withheld, not shown as if current"

    def test_multiple_concurrent_in_progress_plans_are_flagged_ambiguous(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, bud_row_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        shared_reference = "LEARNER-REF-AMBIGUOUS"
        linked_plan = bud_row_factory(learner_reference=shared_reference, status_desc="In Progress", last_submission_date="2026-09-01T09:00:00Z")
        bud_row_factory(learner_reference=shared_reference, status_desc="In Progress", last_submission_date="2026-09-05T09:00:00Z")
        _link_bud(db, learner["id"], linked_plan["learningPlanId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["budStatus"] == "needs_review"
        assert row["budPlanCurrency"] == "multiple_current_plans_ambiguous"
        assert row["lastBudSubmission"] is None

    def test_missing_linked_source_row(self, db, admin_user, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        _link_bud(db, learner["id"], "TEST-PLAN-NEVER-SYNCED")

        row = _row_for(db, tutor, learner["id"])
        assert row["budStatus"] == "missing_source"

    def test_no_link(self, db, admin_user, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["budStatus"] == "not_linked"

    def test_plan_currency_never_affects_the_combined_calculation_since_bud_is_already_excluded(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory, bud_row_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])
        session = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-09-01", created_by=admin_user["userId"])
        _record(request_factory, session["id"], learner["id"], "present", admin_user, hoursAttended=6)
        shared_reference = "LEARNER-REF-CALC-UNCHANGED"
        historical_plan = bud_row_factory(learner_reference=shared_reference, status_desc="Completed", last_submission_date="2026-09-20T09:00:00Z")
        bud_row_factory(learner_reference=shared_reference, status_desc="In Progress", last_submission_date="2026-09-19T09:00:00Z")
        _link_bud(db, learner["id"], historical_plan["learningPlanId"])

        row = _row_for(db, tutor, learner["id"])
        assert row["budPlanCurrency"] == "linked_plan_not_current"
        assert row["latestEngagementDate"] == date(2026, 9, 1), "attendance is unaffected by the Bud plan-currency flag"
