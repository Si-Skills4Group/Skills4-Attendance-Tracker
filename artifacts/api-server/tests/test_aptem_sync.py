"""Aptem sync -- Phase 1, core ingestion. Mirrors test_bud_sync_new_learners.py's
structure and coverage for the parallel pipeline: baseline establish/reset,
preview classification across the full uln/email matching hierarchy
(matched via link, matched via uln, matched via email, conflict on
disagreement, unmatched-and-eligible = new learner, unmatched-and-ineligible
= no action), commit creates a learner with no tutor/cohort, idempotent
re-commit, and trial limit enforcement with override-reason requirement."""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from pyapp.aptem_sync_lib import (
    bulk_approve_new_learners, classify_row, establish_baseline, get_active_baseline, get_job_summary,
    link_existing_learner, list_items, reset_baseline, run_commit, run_preview, update_item,
)


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


class TestBaseline:
    def test_establish_baseline_counts_distinct_users(self, db, admin_user, request_factory, aptem_row_factory):
        aptem_row_factory()
        aptem_row_factory()
        request = request_factory(admin_user)

        baseline = establish_baseline(db, request, admin_user)
        try:
            assert baseline["sourceRowCount"] == 2
            assert baseline["status"] == "active"
        finally:
            db.execute("DELETE FROM aptem_sync_baseline_snapshot WHERE baseline_id = %s", (baseline["id"],))
            db.execute("DELETE FROM aptem_sync_baseline WHERE id = %s", (baseline["id"],))

    def test_establishing_a_second_baseline_is_rejected(self, db, admin_user, request_factory, aptem_baseline_factory):
        aptem_baseline_factory()
        with pytest.raises(HTTPException) as exc_info:
            establish_baseline(db, request_factory(admin_user), admin_user)
        assert exc_info.value.status_code == 409

    def test_reset_baseline_establishes_a_fresh_one(self, db, admin_user, request_factory, aptem_baseline_factory):
        first = aptem_baseline_factory()
        second = reset_baseline(db, request_factory(admin_user), admin_user, "testing reset")

        assert second["id"] != first["id"]
        active = get_active_baseline(db)
        assert active["id"] == second["id"]

    def test_resetting_with_no_active_baseline_is_rejected(self, db, admin_user, request_factory):
        db.execute("DELETE FROM aptem_sync_baseline")
        with pytest.raises(HTTPException) as exc_info:
            reset_baseline(db, request_factory(admin_user), admin_user, "no baseline to reset")
        assert exc_info.value.status_code == 409


class TestMatchingHierarchy:
    def test_an_unmatched_active_episode_is_proposed_as_new(self, db, aptem_row_factory, aptem_baseline_factory):
        baseline = aptem_baseline_factory()
        row = aptem_row_factory(episode_start_date=_tomorrow())

        item = classify_row(db, row, baseline)
        assert item["match_status"] == "new"
        assert item["action_type"] == "create_learner"

    def test_an_unmatched_inactive_episode_is_not_actionable(self, db, aptem_row_factory, aptem_baseline_factory):
        baseline = aptem_baseline_factory()
        row = aptem_row_factory(episode_is_active=False)

        item = classify_row(db, row, baseline)
        assert item["match_status"] == "existing_before_trial"
        assert item["reason"] == "unmatched_non_actionable_episode"

    def test_matching_by_name_alone_is_rejected(self, db, aptem_row_factory, aptem_baseline_factory, learner_factory):
        baseline = aptem_baseline_factory()
        learner_factory(first_name="Ada", last_name="Lovelace", uln=None, email=None)
        row = aptem_row_factory(first_name="Ada", last_name="Lovelace", uln=None, email=None)

        item = classify_row(db, row, baseline)
        assert item["internal_learner_id"] is None

    def test_a_learner_is_matched_by_uln(self, db, aptem_row_factory, aptem_baseline_factory, learner_factory):
        baseline = aptem_baseline_factory()
        learner = learner_factory(uln="ULN-APTEM-1")
        row = aptem_row_factory(uln="ULN-APTEM-1", email="different@example.com")

        item = classify_row(db, row, baseline)
        assert item["internal_learner_id"] == learner["id"]

    def test_a_learner_is_matched_by_email_when_uln_does_not_resolve(
        self, db, aptem_row_factory, aptem_baseline_factory, learner_factory,
    ):
        # learner_factory's INSERT has no email column (see its own
        # definition in conftest.py) -- set it directly afterwards.
        baseline = aptem_baseline_factory()
        learner = learner_factory(uln=None)
        db.execute("UPDATE learners SET email = %s WHERE id = %s", ("shared@example.com", learner["id"]))
        row = aptem_row_factory(email="shared@example.com", uln=None)

        item = classify_row(db, row, baseline)
        assert item["internal_learner_id"] == learner["id"]

    def test_uln_and_email_resolving_to_different_learners_is_a_conflict(
        self, db, aptem_row_factory, aptem_baseline_factory, learner_factory,
    ):
        baseline = aptem_baseline_factory()
        uln_owner = learner_factory(uln="ULN-DISAGREE")
        email_owner = learner_factory(uln=None)
        db.execute("UPDATE learners SET email = %s WHERE id = %s", ("email-owner@example.com", email_owner["id"]))
        row = aptem_row_factory(uln="ULN-DISAGREE", email="email-owner@example.com")

        item = classify_row(db, row, baseline)
        assert item["match_status"] == "conflict"
        assert item["reason"] == "uln_and_email_disagree"
        assert uln_owner["id"] != email_owner["id"]

    def test_missing_identifiers_is_a_conflict(self, db, aptem_row_factory, aptem_baseline_factory):
        baseline = aptem_baseline_factory()
        row = aptem_row_factory(uln=None, email=None, episode_is_active=True)

        item = classify_row(db, row, baseline)
        assert item["match_status"] == "conflict"
        assert item["reason"] == "missing_identifiers"


class TestNewLearnerLifecycle:
    def test_new_learner_proposal_requires_level_and_learner_ref_before_approval(
        self, db, admin_user, request_factory, aptem_row_factory, aptem_baseline_factory,
    ):
        aptem_baseline_factory()
        aptem_row_factory(episode_start_date=_tomorrow())

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM aptem_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_row = db.fetchone()
        assert item_row is not None

        with pytest.raises(HTTPException):
            update_item(db, job["id"], item_row["id"], None, True)

        updated = update_item(
            db, job["id"], item_row["id"], {"learner.level": "3", "learner.learnerRef": "APTEM-NEW-001"}, True,
        )
        assert updated["approved"] is True

    def test_commit_creates_learner_with_no_tutor_or_cohort(
        self, db, admin_user, request_factory, aptem_row_factory, aptem_baseline_factory,
    ):
        aptem_baseline_factory()
        aptem_row_factory(episode_start_date=_tomorrow(), first_name="Grace", last_name="Hopper")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM aptem_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, {"learner.level": "3", "learner.learnerRef": "APTEM-NEW-002"}, True)

        result = run_commit(db, job["id"], [item_id], "First Aptem batch", None, request_factory(admin_user), admin_user)
        assert result["appliedCount"] == 1

        db.execute("SELECT * FROM learners WHERE learner_ref = 'APTEM-NEW-002'")
        learner = db.fetchone()
        try:
            assert learner is not None
            assert learner["first_name"] == "Grace"
            assert learner["tutor_id"] is None
            assert learner["cohort_id"] is None

            db.execute("SELECT count(*)::int AS c FROM aptem_learner_link WHERE internal_learner_id = %s", (learner["id"],))
            assert db.fetchone()["c"] == 1
        finally:
            db.execute("DELETE FROM aptem_learner_link WHERE internal_learner_id = %s", (learner["id"],))
            db.execute("DELETE FROM learners WHERE id = %s", (learner["id"],))

    def test_repeating_preview_and_commit_creates_no_duplicates(
        self, db, admin_user, request_factory, aptem_row_factory, aptem_baseline_factory,
    ):
        aptem_baseline_factory()
        aptem_row_factory(episode_start_date=_tomorrow(), first_name="Katherine", last_name="Johnson")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM aptem_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, {"learner.level": "3", "learner.learnerRef": "APTEM-NEW-003"}, True)
        first = run_commit(db, job["id"], [item_id], "reason", None, request_factory(admin_user), admin_user)
        second = run_commit(db, job["id"], [item_id], "reason", None, request_factory(admin_user), admin_user)

        assert first["status"] == "completed"
        assert second["status"] == "completed"
        assert second["appliedCount"] == first["appliedCount"]

        db.execute("SELECT count(*)::int AS c FROM learners WHERE learner_ref = 'APTEM-NEW-003'")
        assert db.fetchone()["c"] == 1

        db.execute("SELECT id FROM learners WHERE learner_ref = 'APTEM-NEW-003'")
        learner = db.fetchone()
        db.execute("DELETE FROM aptem_learner_link WHERE internal_learner_id = %s", (learner["id"],))
        db.execute("DELETE FROM learners WHERE id = %s", (learner["id"],))


class TestManualLinkExisting:
    def test_manual_link_marks_the_item_applied_and_writes_the_link(
        self, db, admin_user, request_factory, aptem_row_factory, aptem_baseline_factory, learner_factory,
    ):
        aptem_baseline_factory()
        row = aptem_row_factory(episode_start_date=_tomorrow())
        learner = learner_factory()

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM aptem_sync_item WHERE sync_job_id = %s AND source_identifier = %s",
            (job["id"], str(row["aptemUserId"])),
        )
        item_id = db.fetchone()["id"]

        result = link_existing_learner(db, job["id"], item_id, learner["id"], request_factory(admin_user), admin_user)
        assert result["id"] == job["id"]

        db.execute("SELECT applied, outcome, internal_learner_id AS \"internalLearnerId\" FROM aptem_sync_item WHERE id = %s", (item_id,))
        item = db.fetchone()
        assert item["applied"] is True
        assert item["outcome"] == "manually_linked"
        assert item["internalLearnerId"] == learner["id"]

        db.execute("SELECT aptem_user_id AS \"aptemUserId\" FROM aptem_learner_link WHERE internal_learner_id = %s", (learner["id"],))
        assert db.fetchone()["aptemUserId"] == row["aptemUserId"]


class TestJobSummary:
    def test_summary_counts_match_the_operational_queues(
        self, db, admin_user, request_factory, aptem_row_factory, aptem_baseline_factory,
    ):
        aptem_baseline_factory()
        aptem_row_factory(episode_start_date=_tomorrow())  # new learner
        aptem_row_factory(uln=None, email=None)  # conflict: no identifiers

        job = run_preview(db, request_factory(admin_user), admin_user)
        summary = get_job_summary(db, job["id"])

        assert summary["newLearnersCount"] == 1
        assert summary["conflictsCount"] == 1
        assert summary["statusChangesCount"] == 0
        assert summary["learnersCreatedToday"] == 0


class TestBulkApproveNewLearners:
    def test_approves_multiple_items_in_one_call(
        self, db, admin_user, request_factory, aptem_row_factory, aptem_baseline_factory,
    ):
        aptem_baseline_factory()
        aptem_row_factory(episode_start_date=_tomorrow())
        aptem_row_factory(episode_start_date=_tomorrow())

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM aptem_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_ids = [row["id"] for row in db.fetchall()]
        assert len(item_ids) == 2

        result = bulk_approve_new_learners(
            db, job["id"],
            [
                {"itemId": item_ids[0], "learnerRef": "APTEM-BULK-001", "level": "3"},
                {"itemId": item_ids[1], "learnerRef": "APTEM-BULK-002", "level": "3"},
            ],
            request_factory(admin_user), admin_user,
        )
        assert result["approvedCount"] == 2
        assert result["errors"] == []


class TestTrialLimits:
    def test_exceeding_the_learner_creation_limit_requires_an_override_reason(
        self, db, admin_user, request_factory, aptem_row_factory, aptem_baseline_factory,
    ):
        db.execute("UPDATE app_settings SET aptem_sync_max_learner_creations = 1 WHERE id = 1")
        try:
            aptem_baseline_factory()
            aptem_row_factory(episode_start_date=_tomorrow())
            aptem_row_factory(episode_start_date=_tomorrow())

            job = run_preview(db, request_factory(admin_user), admin_user)
            db.execute("SELECT id FROM aptem_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
            item_ids = [row["id"] for row in db.fetchall()]
            for i, item_id in enumerate(item_ids):
                update_item(db, job["id"], item_id, {"learner.level": "3", "learner.learnerRef": f"APTEM-LIMIT-{i}"}, True)

            with pytest.raises(HTTPException) as exc_info:
                run_commit(db, job["id"], item_ids, "reason", None, request_factory(admin_user), admin_user)
            assert exc_info.value.status_code == 409
            assert exc_info.value.detail["reason"] == "trial_limit_exceeded"

            result = run_commit(
                db, job["id"], item_ids, "reason", "deliberately over limit for this batch",
                request_factory(admin_user), admin_user,
            )
            assert result["status"] == "completed"
            assert result["appliedCount"] == 2
        finally:
            db.execute("UPDATE app_settings SET aptem_sync_max_learner_creations = 10 WHERE id = 1")
            db.execute("SELECT id FROM learners WHERE learner_ref LIKE 'APTEM-LIMIT-%'")
            for row in db.fetchall():
                db.execute("DELETE FROM aptem_learner_link WHERE internal_learner_id = %s", (row["id"],))
                db.execute("DELETE FROM learners WHERE id = %s", (row["id"],))
