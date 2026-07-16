"""Tests for reports.py's cohort/tutor/organisation report endpoints.

These previously had zero test coverage. Written alongside the N+1 fix
(single fetch + group-in-Python instead of one query per entity in the
breakdown) to prove the batched rewrite produces exactly the same shape
and numbers the old per-entity-loop version did.
"""

import os

from pyapp.routers.reports import get_cohort_report, get_organisation_report, get_tutor_report


def _insert_attendance_record(db, session_id, learner_id, status, hours_attended=6):
    db.execute(
        """
        INSERT INTO attendance_records (session_id, learner_id, status, hours_attended, minutes_late)
        VALUES (%s, %s, %s, %s, 0)
        """,
        (session_id, learner_id, status, hours_attended),
    )


def test_cohort_report_totals_and_per_learner_breakdown(
    db, admin_user, cohort_factory, learner_factory, attendance_session_factory
):
    cohort = cohort_factory()
    present_learner = learner_factory(cohort_id=cohort["id"])
    absent_learner = learner_factory(cohort_id=cohort["id"])
    untouched_learner = learner_factory(cohort_id=cohort["id"])  # no attendance record at all
    session_row = attendance_session_factory(
        cohort_id=cohort["id"], planned_duration_hours=6, created_by=admin_user["userId"]
    )

    _insert_attendance_record(db, session_row["id"], present_learner["id"], "present", hours_attended=6)
    _insert_attendance_record(db, session_row["id"], absent_learner["id"], "absent_unauthorised", hours_attended=0)

    result = get_cohort_report(cohort["id"], session=admin_user)

    assert result["totals"]["scheduledHours"] == 12  # 2 learners with actual records x 6 hours
    assert result["totals"]["attendedHours"] == 6

    breakdown_by_id = {b["learnerId"]: b for b in result["learnerBreakdown"]}
    assert breakdown_by_id[present_learner["id"]]["totals"]["attendedHours"] == 6
    assert breakdown_by_id[absent_learner["id"]]["totals"]["unauthorisedAbsenceHours"] == 6
    # A learner with zero attendance_records rows must get zero totals, not
    # a KeyError -- proving the grouped-dict .get(id, []) fallback works.
    assert breakdown_by_id[untouched_learner["id"]]["totals"]["scheduledHours"] == 0
    assert breakdown_by_id[untouched_learner["id"]]["totals"]["sessionCount"] == 0

    db.execute("DELETE FROM attendance_records WHERE session_id = %s", (session_row["id"],))


def test_tutor_report_totals_and_per_cohort_breakdown(
    db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory
):
    tutor = tutor_factory()
    cohort_a = cohort_factory(tutor_id=tutor["tutorId"])
    cohort_b = cohort_factory(tutor_id=tutor["tutorId"])
    learner_a = learner_factory(cohort_id=cohort_a["id"], tutor_id=tutor["tutorId"])
    learner_b = learner_factory(cohort_id=cohort_b["id"], tutor_id=tutor["tutorId"])
    session_a = attendance_session_factory(
        cohort_id=cohort_a["id"], planned_duration_hours=5, created_by=admin_user["userId"]
    )
    session_b = attendance_session_factory(
        cohort_id=cohort_b["id"], planned_duration_hours=5, created_by=admin_user["userId"]
    )

    _insert_attendance_record(db, session_a["id"], learner_a["id"], "present", hours_attended=5)
    _insert_attendance_record(db, session_b["id"], learner_b["id"], "late", hours_attended=4)

    result = get_tutor_report(tutor["tutorId"], session=admin_user)

    assert result["totals"]["scheduledHours"] == 10
    assert result["totals"]["attendedHours"] == 9
    assert result["totals"]["lateCount"] == 1

    breakdown_by_cohort = {b["cohortId"]: b for b in result["cohortBreakdown"]}
    assert breakdown_by_cohort[cohort_a["id"]]["totals"]["attendedHours"] == 5
    assert breakdown_by_cohort[cohort_b["id"]]["totals"]["lateCount"] == 1

    db.execute("DELETE FROM attendance_records WHERE session_id IN (%s, %s)", (session_a["id"], session_b["id"]))


def test_organisation_report_with_and_without_programme_filter(
    db, admin_user, cohort_factory, learner_factory, attendance_session_factory
):
    suffix = os.urandom(4).hex()
    programme_x = f"Programme X {suffix}"
    programme_y = f"Programme Y {suffix}"
    cohort_x = cohort_factory(programme=programme_x)
    cohort_y = cohort_factory(programme=programme_y)
    learner_x = learner_factory(cohort_id=cohort_x["id"], programme=programme_x)
    learner_y = learner_factory(cohort_id=cohort_y["id"], programme=programme_y)
    session_x = attendance_session_factory(
        cohort_id=cohort_x["id"], planned_duration_hours=6, created_by=admin_user["userId"]
    )
    session_y = attendance_session_factory(
        cohort_id=cohort_y["id"], planned_duration_hours=6, created_by=admin_user["userId"]
    )

    _insert_attendance_record(db, session_x["id"], learner_x["id"], "present", hours_attended=6)
    _insert_attendance_record(db, session_y["id"], learner_y["id"], "present", hours_attended=6)

    # Without a filter: top-level totals cover both programmes' records.
    unfiltered = get_organisation_report(_session=admin_user)
    assert unfiltered["totals"]["scheduledHours"] >= 12
    programme_names = {p["programme"] for p in unfiltered["programmeBreakdown"]}
    assert {programme_x, programme_y} <= programme_names
    cohort_ids_in_breakdown = {c["cohortId"] for c in unfiltered["cohortBreakdown"]}
    assert {cohort_x["id"], cohort_y["id"]} <= cohort_ids_in_breakdown

    # With a filter: top-level totals only cover the filtered programme...
    filtered = get_organisation_report(programme=programme_x, _session=admin_user)
    assert filtered["totals"]["scheduledHours"] == 6
    # ...but the breakdown still shows every programme/cohort, matching the
    # pre-existing (if slightly surprising) behaviour this rewrite preserves
    # rather than changes incidentally.
    filtered_programme_names = {p["programme"] for p in filtered["programmeBreakdown"]}
    assert {programme_x, programme_y} <= filtered_programme_names
    filtered_cohort_ids = {c["cohortId"] for c in filtered["cohortBreakdown"]}
    assert {cohort_x["id"], cohort_y["id"]} <= filtered_cohort_ids

    db.execute("DELETE FROM attendance_records WHERE session_id IN (%s, %s)", (session_x["id"], session_y["id"]))
