"""New-learner detection and creation: eligible only after the baseline,
never matched by name/email/tutor_name, blocked on missing required fields
(learnerRef/level have no Bud equivalent), and creates the learner/cohort/
allocation exactly once via the existing services."""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from pyapp.bud_sync_lib import classify_row, get_job_summary, link_existing_learner, list_items, run_commit, run_preview, update_item


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


class TestNewLearnerClassification:
    def test_a_learner_appearing_after_baseline_is_proposed_as_new(self, db, bud_row_factory, baseline_factory, tutor_factory):
        baseline = baseline_factory()
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-1' WHERE id = %s", (tutor["tutorId"],))
        row = bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-1", start_date=_tomorrow())

        item = classify_row(db, row, baseline)
        assert item["match_status"] == "new"
        assert item["action_type"] == "create_learner"

    def test_a_learner_present_before_baseline_is_not_proposed_as_new(self, db, bud_row_factory, baseline_factory):
        # The row must exist in the source *before* the baseline is
        # established -- otherwise the baseline's own "no rows yet" marker
        # would make anything added afterward look post-baseline by
        # definition, regardless of its own synced_at value.
        row = bud_row_factory(synced_at="2000-01-01T00:00:00Z")
        baseline = baseline_factory()

        item = classify_row(db, row, baseline)
        assert item["match_status"] == "existing_before_trial"
        assert item["action_type"] == "none"

    def test_matching_by_name_alone_is_rejected(self, db, bud_row_factory, baseline_factory, learner_factory):
        """A Bud row sharing a learner's name but no ULN must never match --
        only learners.uln is a reliable cross-system identifier."""
        baseline = baseline_factory()
        learner_factory(first_name="Ada", last_name="Lovelace", uln=None)
        row = bud_row_factory(
            learner_forename="Ada", learner_surname="Lovelace", unique_learner_number=None,
            synced_at="2099-01-01T00:00:00Z",
        )

        item = classify_row(db, row, baseline)
        # No ULN on either side -> cannot be matched to the existing
        # Lovelace record; must be evaluated purely as an unmatched/new row.
        assert item["internal_learner_id"] is None

    def test_missing_tutor_mapping_is_a_conflict(self, db, bud_row_factory, baseline_factory):
        baseline = baseline_factory()
        row = bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="NO-SUCH-BUD-TUTOR", start_date=_tomorrow())

        item = classify_row(db, row, baseline)
        assert item["match_status"] == "conflict"
        assert item["reason"] == "tutor_unmatched"

    def test_missing_start_date_is_a_conflict(self, db, bud_row_factory, baseline_factory, tutor_factory):
        baseline = baseline_factory()
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-NODATE' WHERE id = %s", (tutor["tutorId"],))
        row = bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-NODATE", start_date=None)
        item = classify_row(db, row, baseline)
        assert item["match_status"] == "conflict"
        assert item["reason"] == "missing_start_date"

    def test_possible_manual_cohort_match_is_a_conflict_not_auto_linked(self, db, bud_row_factory, baseline_factory, tutor_factory, cohort_factory):
        baseline = baseline_factory()
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-2' WHERE id = %s", (tutor["tutorId"],))
        manual_cohort = cohort_factory(tutor_id=tutor["tutorId"], start_date=_tomorrow())
        row = bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-2", start_date=_tomorrow())

        item = classify_row(db, row, baseline)
        assert item["match_status"] == "conflict"
        assert item["reason"] == "possible_manual_cohort_match"

    def test_new_learner_proposal_requires_level_and_learner_ref_before_approval(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-3' WHERE id = %s", (tutor["tutorId"],))
        baseline_factory()
        bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-3", start_date=_tomorrow())

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_row = db.fetchone()
        assert item_row is not None

        with pytest.raises(HTTPException):
            update_item(db, job["id"], item_row["id"], None, True)

        # Supplying only the learner-side gaps still leaves the cohort's
        # required schedule fields missing (Bud has no delivery-day/time
        # equivalent either) -- approval must keep rejecting until those
        # are supplied too.
        with pytest.raises(HTTPException):
            update_item(
                db, job["id"], item_row["id"], {"learner.level": "3", "learner.learnerRef": "BUD-NEW-001"}, True,
            )

        updated = update_item(
            db, job["id"], item_row["id"],
            {
                "learner.level": "3", "learner.learnerRef": "BUD-NEW-001",
                "cohort.deliveryDay": "monday", "cohort.sessionStartTime": "09:00", "cohort.sessionEndTime": "16:00",
            },
            True,
        )
        assert updated["approved"] is True


class TestItemDisplayIdentity:
    """The item table shows learner_reference (labelled ID) and forename/
    surname instead of the internal source_identifier (learning_plan_id) --
    those display fields must be populated on every item regardless of
    match_status."""

    def test_new_item_carries_learner_reference_and_name(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-DISPLAY' WHERE id = %s", (tutor["tutorId"],))
        baseline_factory()
        bud_row_factory(
            synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-DISPLAY", start_date=_tomorrow(),
            learner_forename="Marie", learner_surname="Curie", learner_reference="BUD-REF-DISPLAY-1",
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        result = list_items(db, job["id"], None, None, 1, 200)
        item = next(i for i in result["items"] if i["matchStatus"] == "new")

        assert item["sourceLearnerReference"] == "BUD-REF-DISPLAY-1"
        assert item["sourceFirstName"] == "Marie"
        assert item["sourceLastName"] == "Curie"

    def test_conflict_item_also_carries_learner_reference_and_name(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory,
    ):
        baseline_factory()
        bud_row_factory(
            synced_at="2099-01-01T00:00:00Z", start_date=_tomorrow(),
            learner_forename="Rosalind", learner_surname="Franklin", learner_reference="BUD-REF-DISPLAY-2",
        )  # no tutor mapping -> conflict

        job = run_preview(db, request_factory(admin_user), admin_user)
        result = list_items(db, job["id"], "conflict", None, 1, 200)
        assert len(result["items"]) == 1
        item = result["items"][0]

        assert item["sourceLearnerReference"] == "BUD-REF-DISPLAY-2"
        assert item["sourceFirstName"] == "Rosalind"
        assert item["sourceLastName"] == "Franklin"


class TestAmbiguousReferenceScoping:
    """Regression test for a real bug found while verifying against
    production: two Bud rows sharing a learner_reference that matches NO
    internal learner at all must never become a conflict -- Bud commonly
    has several historical learning plans for people this trial has never
    tracked (re-enrolments, programme changes), and that's unrelated to
    the internal-matching-ambiguity rule, which only applies once a
    reference actually corresponds to an internal learner.learner_ref."""

    def test_a_reference_ambiguous_only_within_bud_is_not_a_conflict(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-AMBIG' WHERE id = %s", (tutor["tutorId"],))
        baseline_factory()
        shared_reference = "BUD-REF-NOBODY-INTERNAL"
        # Two historical Bud rows for the same never-tracked person -- no
        # internal learner has this learner_ref, so this is not an
        # internal-matching conflict.
        bud_row_factory(learner_reference=shared_reference, status_desc="Withdrawn", start_date=_tomorrow())
        new_row = bud_row_factory(
            learner_reference=shared_reference, tutor_id="BUD-T-AMBIG", start_date=_tomorrow(),
            synced_at="2099-01-01T00:00:00Z",
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT match_status FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s",
            (job["id"], new_row["learningPlanId"]),
        )
        assert db.fetchone()["match_status"] == "new"

    def test_a_reference_ambiguous_and_matching_an_internal_learner_is_still_a_conflict(
        self, db, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = learner_factory()
        baseline = baseline_factory()
        # Two Bud rows share a reference that DOES match an internal
        # learner -- this is the genuine internal-matching ambiguity case.
        bud_row_factory(learner_reference=learner["learner_ref"], status_desc="Withdrawn")
        second_row = bud_row_factory(learner_reference=learner["learner_ref"], synced_at="2099-01-01T00:00:00Z")

        item = classify_row(db, second_row, baseline)
        assert item["match_status"] == "conflict"
        assert item["reason"] == "learner_reference_matches_multiple_bud_rows"


class TestStatusEligibility:
    """New-learner (unmatched) eligibility is still restricted to
    status_desc == 'In Progress' -- confirmed as a real, exact value in
    production data (alongside Completed/Withdrawn/Pending/On Break/etc).
    A non-actionable unmatched row still gets a classified item (for
    accurate Sync History bookkeeping), just never 'new' -- see
    TestMatchedLearnerEligibilityAtAnyStatus below for why this rule does
    NOT extend to already-matched learners."""

    @pytest.mark.parametrize("status_desc", ["Completed", "Withdrawn", "Pending", "On Break", "In End Point Assessment"])
    def test_a_non_in_progress_unmatched_row_is_never_proposed_as_new(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory, status_desc,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-STATUS' WHERE id = %s", (tutor["tutorId"],))
        baseline_factory()
        row = bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-STATUS", start_date=_tomorrow(), status_desc=status_desc)

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT match_status FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s", (job["id"], row["learningPlanId"]))
        assert db.fetchone()["match_status"] == "existing_before_trial"

    def test_an_in_progress_row_is_still_proposed(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-STATUS-2' WHERE id = %s", (tutor["tutorId"],))
        baseline_factory()
        row = bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-STATUS-2", start_date=_tomorrow(), status_desc="In Progress")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT match_status FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s", (job["id"], row["learningPlanId"]))
        assert db.fetchone()["match_status"] == "new"


class TestMatchedLearnerEligibilityAtAnyStatus:
    """Corrected rule: a MATCHED learner must remain eligible for
    status-change detection at any status_desc -- the 'In Progress only'
    filter exists solely to stop historical backfill of brand-new,
    unmatched learners. Applying it to already-matched learners too was
    the defect that made status-change detection (e.g. In Progress -> BIL)
    impossible."""

    def test_a_learner_who_moves_to_withdrawn_is_detected_as_a_status_change(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = learner_factory(email="old@example.com", uln="ULN-STATUS-1")
        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-STATUS-1", learner_email="new@example.com", synced_at="2099-01-01T00:00:00Z", status_desc="In Progress")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)

        # Bud now shows the learner as Withdrawn, with a further email change.
        db.execute(
            "UPDATE public.learner_progress SET status_desc = 'Withdrawn', learner_email = 'post-withdrawal@example.com', "
            "synced_at = '2099-06-01T00:00:00Z' WHERE unique_learner_number = 'ULN-STATUS-1'"
        )

        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            'SELECT match_status, proposed_values AS "proposedValues" FROM bud_sync_item '
            "WHERE sync_job_id = %s AND internal_learner_id = %s",
            (job2["id"], learner["id"]),
        )
        item = db.fetchone()
        # Detected, not silently dropped -- and never invented an effective
        # withdrawal date Bud never supplied.
        assert item["match_status"] == "status_change"
        assert item["proposedValues"]["statusChange"]["kind"] == "needs_date"
        assert item["proposedValues"]["statusChange"]["targetStatus"] == "withdrawn"

        db.execute("SELECT status, email FROM learners WHERE id = %s", (learner["id"],))
        row = db.fetchone()
        assert row["status"] == "active"  # not applied without the missing date
        assert row["email"] == "new@example.com"  # the field update was already committed in job 1


class TestNewLearnerCommit:
    def test_commit_creates_learner_cohort_and_allocation_exactly_once(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-4' WHERE id = %s", (tutor["tutorId"],))
        baseline_factory()
        bud_row_factory(
            synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-4", start_date=_tomorrow(),
            learner_forename="Grace", learner_surname="Hopper",
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_id = db.fetchone()["id"]
        update_item(
            db, job["id"], item_id,
            {
                "learner.level": "3", "learner.learnerRef": "BUD-NEW-002",
                "cohort.deliveryDay": "monday", "cohort.sessionStartTime": "09:00", "cohort.sessionEndTime": "16:00",
            },
            True,
        )

        result = run_commit(db, job["id"], [item_id], "First trial batch", None, request_factory(admin_user), admin_user)
        assert result["appliedCount"] == 1

        db.execute("SELECT * FROM learners WHERE learner_ref = 'BUD-NEW-002'")
        learner = db.fetchone()
        assert learner is not None
        assert learner["first_name"] == "Grace"

        db.execute("SELECT * FROM bud_cohort_mapping WHERE cohort_id = %s", (learner["cohort_id"] or -1,))
        # Future start date -> scheduled, not immediately allocated -- cohort_id
        # is expected to be null on the learner row itself until the effective date.
        db.execute("SELECT * FROM scheduled_allocations WHERE learner_id = %s", (learner["id"],))
        scheduled = db.fetchone()
        assert scheduled is not None

        db.execute("SELECT * FROM bud_cohort_mapping WHERE bud_sync_key LIKE %s", (f"bud:{tutor['tutorId']}:%",))
        mapping = db.fetchone()
        assert mapping is not None

        # Cleanup (learner_factory-style teardown isn't available since this
        # learner was created by the commit itself, not the factory).
        db.execute("DELETE FROM scheduled_allocations WHERE learner_id = %s", (learner["id"],))
        db.execute("DELETE FROM bud_learner_link WHERE internal_learner_id = %s", (learner["id"],))
        db.execute("DELETE FROM learners WHERE id = %s", (learner["id"],))
        db.execute("DELETE FROM bud_cohort_mapping WHERE cohort_id = %s", (mapping["cohort_id"],))
        db.execute("DELETE FROM cohorts WHERE id = %s", (mapping["cohort_id"],))

    def test_repeating_preview_and_commit_creates_no_duplicates(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-5' WHERE id = %s", (tutor["tutorId"],))
        baseline_factory()
        bud_row_factory(
            synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-5", start_date=_tomorrow(),
            learner_forename="Katherine", learner_surname="Johnson",
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_id = db.fetchone()["id"]
        update_item(
            db, job["id"], item_id,
            {
                "learner.level": "3", "learner.learnerRef": "BUD-NEW-003",
                "cohort.deliveryDay": "monday", "cohort.sessionStartTime": "09:00", "cohort.sessionEndTime": "16:00",
            },
            True,
        )
        first = run_commit(db, job["id"], [item_id], "reason", None, request_factory(admin_user), admin_user)
        second = run_commit(db, job["id"], [item_id], "reason", None, request_factory(admin_user), admin_user)

        assert first["status"] == "completed"
        assert second["status"] == "completed"
        assert second["appliedCount"] == first["appliedCount"]

        db.execute("SELECT count(*)::int AS c FROM learners WHERE learner_ref = 'BUD-NEW-003'")
        assert db.fetchone()["c"] == 1

        db.execute("SELECT id, cohort_id FROM learners WHERE learner_ref = 'BUD-NEW-003'")
        learner = db.fetchone()
        db.execute("SELECT new_cohort_id AS cohort_id FROM scheduled_allocations WHERE learner_id = %s", (learner["id"],))
        scheduled = db.fetchone()
        cohort_id = scheduled["cohort_id"] if scheduled else learner["cohort_id"]

        db.execute("DELETE FROM scheduled_allocations WHERE learner_id = %s", (learner["id"],))
        db.execute("DELETE FROM bud_learner_link WHERE internal_learner_id = %s", (learner["id"],))
        db.execute("DELETE FROM learners WHERE id = %s", (learner["id"],))
        if cohort_id:
            db.execute("DELETE FROM bud_cohort_mapping WHERE cohort_id = %s", (cohort_id,))
            db.execute("DELETE FROM cohorts WHERE id = %s", (cohort_id,))


class TestManualLinkExisting:
    """New Learners tab's 'Mark as already represented' action: an
    Administrator manually confirms an unmatched Bud row is actually a
    specific existing Attendance learner, instead of creating a new one."""

    def test_manual_link_marks_the_item_applied_and_writes_the_link(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-MANUAL-1' WHERE id = %s", (tutor["tutorId"],))
        baseline_factory()
        row = bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-MANUAL-1", start_date=_tomorrow())
        learner = learner_factory()

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s", (job["id"], row["learningPlanId"]))
        item_id = db.fetchone()["id"]

        result = link_existing_learner(db, job["id"], item_id, learner["id"], request_factory(admin_user), admin_user)
        assert result["id"] == job["id"]

        db.execute("SELECT applied, outcome, internal_learner_id AS \"internalLearnerId\" FROM bud_sync_item WHERE id = %s", (item_id,))
        item = db.fetchone()
        assert item["applied"] is True
        assert item["outcome"] == "manually_linked"
        assert item["internalLearnerId"] == learner["id"]

        db.execute("SELECT bud_learning_plan_id AS \"planId\" FROM bud_learner_link WHERE internal_learner_id = %s", (learner["id"],))
        assert db.fetchone()["planId"] == row["learningPlanId"]

        db.execute("SELECT count(*)::int AS c FROM audit_logs WHERE action = 'bud_sync_manual_link_established' AND entity_id = %s", (learner["id"],))
        assert db.fetchone()["c"] == 1

    def test_manual_link_is_only_offered_from_a_new_item(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner1 = learner_factory(uln="ULN-MANUAL-2")
        learner2 = learner_factory()
        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-MANUAL-2", learner_email="changed@example.com", synced_at="2099-01-01T00:00:00Z")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner1["id"]))
        item_id = db.fetchone()["id"]

        with pytest.raises(HTTPException) as exc_info:
            link_existing_learner(db, job["id"], item_id, learner2["id"], request_factory(admin_user), admin_user)
        assert exc_info.value.status_code == 400

    def test_manual_link_rejects_a_learner_already_linked_to_a_different_bud_record(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-MANUAL-3' WHERE id = %s", (tutor["tutorId"],))
        already_linked_learner = learner_factory(uln="ULN-MANUAL-3")
        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-MANUAL-3", learner_email="changed@example.com", synced_at="2099-01-01T00:00:00Z")
        new_row = bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-MANUAL-3", start_date=_tomorrow())

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], already_linked_learner["id"]))
        existing_item_id = db.fetchone()["id"]
        update_item(db, job["id"], existing_item_id, None, True)
        run_commit(db, job["id"], [existing_item_id], "establish link", None, request_factory(admin_user), admin_user)

        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s", (job2["id"], new_row["learningPlanId"]))
        new_item_id = db.fetchone()["id"]

        with pytest.raises(HTTPException) as exc_info:
            link_existing_learner(db, job2["id"], new_item_id, already_linked_learner["id"], request_factory(admin_user), admin_user)
        assert exc_info.value.status_code == 409


class TestJobSummary:
    def test_summary_counts_match_the_operational_queues(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-SUMMARY' WHERE id = %s", (tutor["tutorId"],))
        baseline_factory()
        bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-SUMMARY", start_date=_tomorrow())  # new learner
        bud_row_factory(synced_at="2099-01-01T00:00:00Z", start_date=_tomorrow())  # conflict: no tutor mapping

        job = run_preview(db, request_factory(admin_user), admin_user)
        summary = get_job_summary(db, job["id"])

        assert summary["newLearnersCount"] == 1
        assert summary["conflictsCount"] == 1
        assert summary["statusChangesCount"] == 0
        assert summary["statusChangesAppliedToday"] == 0
        assert summary["learnersCreatedToday"] == 0
