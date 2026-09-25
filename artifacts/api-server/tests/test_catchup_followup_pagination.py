"""Stage 3 follow-up-list pagination: the weekly Absence Follow-up screen
must never cap visibility at a fixed row count -- every filter (status/
cohort/learner search) is applied server-side, against the FULL authorised
dataset, before LIMIT/OFFSET. Uses 111 eligible learner-session absences in
one week (well over any previous row cap) to prove every page is reachable,
no row is duplicated or omitted, and a learner placed deliberately beyond
the first page is still findable via search, viewable, and updatable."""
from datetime import date

import pytest

from pyapp.catchup_lib import list_catchup_followup, record_catchup
from pyapp.routers.attendance import AttendanceRegisterInput, RegisterEntryInput, save_attendance_register

PAST_SESSION_DATE = "2026-01-05"
WEEK_START = date(2026, 1, 5)
WEEK_END = date(2026, 1, 11)
PAGE_SIZE = 25
FILLER_COUNT = 110  # + 1 target learner = 111 total, comfortably over 100


@pytest.fixture
def large_scenario(admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory):
    tutor = tutor_factory()
    cohort = cohort_factory(tutor_id=tutor["tutorId"])
    session = attendance_session_factory(cohort_id=cohort["id"], session_date=PAST_SESSION_DATE, created_by=admin_user["userId"])

    # Filler learners sort BEFORE the target alphabetically (last_name
    # "Filler" < "ZTarget"), so with the list's own
    # "ORDER BY ..., l.last_name, l.first_name, ..." the target is
    # deliberately pushed onto the last page, not the first.
    learners = [learner_factory(cohort_id=cohort["id"], first_name=f"Filler{i:03d}", last_name="Filler") for i in range(FILLER_COUNT)]
    target = learner_factory(cohort_id=cohort["id"], first_name="Zed", last_name="ZTarget")
    learners.append(target)

    save_attendance_register(
        session["id"],
        AttendanceRegisterInput(
            registerVersion=1,
            entries=[
                RegisterEntryInput(learnerId=learner["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)
                for learner in learners
            ],
        ),
        request_factory(),
        admin_user,
    )
    return {"tutor": tutor, "cohort": cohort, "session": session, "learners": learners, "target": target}


class TestFollowUpPagination:
    def test_every_page_reachable_no_omission_or_duplication_and_target_beyond_page_one_is_searchable_and_updatable(
        self, db, large_scenario, admin_user, request_factory,
    ):
        # Scoped to this test's own cohort throughout -- the shared test DB
        # can carry other tests' rows in the same week, and an unscoped
        # query would silently mix them in (a documented gotcha in this
        # suite already, see test_participation_metrics.py's history).
        cohort_id = large_scenario["cohort"]["id"]

        # 1. Walk every page with the real page size the UI uses and
        # collect every (sessionId, learnerId) pair returned.
        seen: list[tuple[int, int]] = []
        total_reported = None
        page = 1
        while True:
            result = list_catchup_followup(db, admin_user, WEEK_START, WEEK_END, cohort_id, None, None, page, PAGE_SIZE)
            if total_reported is None:
                total_reported = result["total"]
            else:
                assert result["total"] == total_reported, "the full filtered total must be stable across pages"
            if not result["items"]:
                break
            seen.extend((item["sessionId"], item["learnerId"]) for item in result["items"])
            if len(result["items"]) < PAGE_SIZE:
                break
            page += 1

        expected_total = len(large_scenario["learners"])
        assert total_reported == expected_total == 111
        assert page > 1, "the scenario must genuinely span more than one page"
        assert len(seen) == expected_total, f"expected exactly {expected_total} rows across all pages, got {len(seen)}"
        assert len(set(seen)) == expected_total, "no row may be duplicated across pages"
        expected_pairs = {(large_scenario["session"]["id"], learner["id"]) for learner in large_scenario["learners"]}
        assert set(seen) == expected_pairs, "no eligible absence may be omitted from any page"

        # 2. The target learner sorts last and therefore sits on the LAST
        # page under normal pagination -- confirm they are never reachable
        # from page 1 without search, but are immediately reachable (and
        # correctly rendered) via search alone, regardless of page.
        target = large_scenario["target"]
        session = large_scenario["session"]
        page_one = list_catchup_followup(db, admin_user, WEEK_START, WEEK_END, cohort_id, None, None, 1, PAGE_SIZE)
        assert target["id"] not in {item["learnerId"] for item in page_one["items"]}

        found = list_catchup_followup(db, admin_user, WEEK_START, WEEK_END, cohort_id, None, None, 1, PAGE_SIZE, search="ZTarget")
        assert found["total"] == 1
        assert found["items"][0]["learnerId"] == target["id"]
        assert found["items"][0]["effective"] is False
        assert found["items"][0]["originalStatus"] == "absent_authorised"

        outstanding_via_search = list_catchup_followup(db, admin_user, WEEK_START, WEEK_END, cohort_id, None, "outstanding", 1, PAGE_SIZE, search="ZTarget")
        assert outstanding_via_search["total"] == 1

        # 3. Record catch-up for that same beyond-page-one learner, then
        # confirm the list (still via search) reflects the update: moved
        # out of Outstanding, into Completed, with the recorded details.
        record_catchup(db, session["id"], target["id"], date.fromisoformat(PAST_SESSION_DATE), "recording_watched", "Watched it", request_factory(), admin_user)

        no_longer_outstanding = list_catchup_followup(db, admin_user, WEEK_START, WEEK_END, cohort_id, None, "outstanding", 1, PAGE_SIZE, search="ZTarget")
        assert no_longer_outstanding["total"] == 0

        completed = list_catchup_followup(db, admin_user, WEEK_START, WEEK_END, cohort_id, None, "completed", 1, PAGE_SIZE, search="ZTarget")
        assert completed["total"] == 1
        assert completed["items"][0]["effective"] is True
        assert completed["items"][0]["method"] == "recording_watched"

        # The overall total (no status filter) is unchanged -- recording a
        # catch-up narrows/moves a row between tabs, it never adds or
        # removes an absence from the week's dataset.
        overall = list_catchup_followup(db, admin_user, WEEK_START, WEEK_END, cohort_id, None, None, 1, PAGE_SIZE)
        assert overall["total"] == 111


class TestFollowUpSearchPermissionScoping:
    """search is a NEW filter added directly onto the existing tutor-scoped
    WHERE clause (never a separate code path) -- this locks in that it can
    only narrow a tutor's own results, never surface another tutor's
    identically-named learner."""

    def test_search_never_surfaces_another_tutors_identically_named_learner(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, request_factory,
    ):
        owner = tutor_factory()
        other = tutor_factory()
        owner_cohort = cohort_factory(tutor_id=owner["tutorId"])
        other_cohort = cohort_factory(tutor_id=other["tutorId"])
        owner_learner = learner_factory(cohort_id=owner_cohort["id"], first_name="Sam", last_name="Rivera")
        other_learner = learner_factory(cohort_id=other_cohort["id"], first_name="Sam", last_name="Rivera")
        owner_session = attendance_session_factory(cohort_id=owner_cohort["id"], session_date=PAST_SESSION_DATE, created_by=admin_user["userId"])
        other_session = attendance_session_factory(cohort_id=other_cohort["id"], session_date=PAST_SESSION_DATE, created_by=admin_user["userId"])

        for session_row, learner in ((owner_session, owner_learner), (other_session, other_learner)):
            save_attendance_register(
                session_row["id"],
                AttendanceRegisterInput(registerVersion=1, entries=[RegisterEntryInput(learnerId=learner["id"], status="absent_authorised", hoursAttended=0, minutesLate=0)]),
                request_factory(),
                admin_user,
            )

        result = list_catchup_followup(db, owner["session"], WEEK_START, WEEK_END, None, None, None, 1, PAGE_SIZE, search="Rivera")

        assert result["total"] == 1
        assert result["items"][0]["learnerId"] == owner_learner["id"]
