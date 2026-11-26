"""Bud Functional Skills requirement sync -- covers subject-derivation
across all status categories (including the defensive odd-row-count and
unknown-status warnings), matching via bud_learner_link (unmatched,
soft-deleted learner), classification (new/changed/unchanged/
blocked_by_clear, including against an existing manual_upload row and an
existing bud_fs_sync row), preview job/item creation, bulk-approve partial
success, commit staleness rejection (both the mirror-table-changed and the
learner_fs_requirements-changed cases), a successful commit writing
source='bud_fs_sync', idempotent replay of a completed job, and job summary
counts."""
import pytest
from fastapi import HTTPException

from pyapp.fs_bud_sync_lib import (
    bulk_approve_items, classify_plan, derive_subject_requirement, get_job_summary, get_source_status, list_items,
    run_commit, run_preview, update_item,
)
from pyapp.fs_requirement_lib import REQUIREMENT_SELECT, _upsert_requirement


class TestSubjectDerivation:
    def test_no_rows_is_not_required(self):
        required, warnings = derive_subject_requirement([])
        assert required is False
        assert warnings == []

    def test_both_levels_complete_or_exempt_is_not_required(self):
        rows = [{"statusCategory": "complete"}, {"statusCategory": "exempt"}]
        required, warnings = derive_subject_requirement(rows)
        assert required is False
        assert warnings == []

    def test_an_in_progress_percentage_is_still_required(self):
        rows = [{"statusCategory": "percentage"}, {"statusCategory": "exempt"}]
        required, warnings = derive_subject_requirement(rows)
        assert required is True

    def test_zero_percent_is_still_required(self):
        rows = [{"statusCategory": "percentage"}, {"statusCategory": "percentage"}]
        required, _ = derive_subject_requirement(rows)
        assert required is True

    def test_unknown_status_is_still_required_and_warns(self):
        rows = [{"statusCategory": "unknown"}, {"statusCategory": "exempt"}]
        required, warnings = derive_subject_requirement(rows)
        assert required is True
        assert any("unrecognised" in w for w in warnings)

    def test_unexpected_row_count_always_warns_regardless_of_the_resulting_flag(self):
        # An odd count always warns (today it's always exactly 2) -- but the
        # still-required flag itself is unaffected, still derived purely
        # from the statuses actually present.
        required, warnings = derive_subject_requirement([{"statusCategory": "complete"}])
        assert required is False
        assert any("Unexpected" in w for w in warnings)

        required, warnings = derive_subject_requirement(
            [{"statusCategory": "complete"}, {"statusCategory": "exempt"}, {"statusCategory": "percentage"}],
        )
        assert required is True
        assert any("Unexpected" in w for w in warnings)


def _subject_row(subject: str, level: int, status_category: str, status: str | None = None) -> dict:
    return {
        "subject": subject, "level": level,
        "functionalSkillStatus": status if status is not None else status_category,
        "statusCategory": status_category,
    }


class TestMatching:
    def test_a_plan_with_no_link_is_unmatched(self, db):
        rows = [_subject_row("english", 1, "percentage"), _subject_row("english", 2, "exempt")]
        item = classify_plan(db, "NO-SUCH-PLAN", rows)
        assert item["match_status"] == "unmatched"
        assert item["internal_learner_id"] is None

    def test_a_plan_linked_to_a_soft_deleted_learner_is_unmatched(self, db, learner_factory):
        learner = learner_factory()
        db.execute("INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)", (learner["id"], "PLAN-DELETED"))
        db.execute("UPDATE learners SET deleted_at = now() WHERE id = %s", (learner["id"],))
        try:
            rows = [_subject_row("math", 1, "percentage"), _subject_row("math", 2, "exempt")]
            item = classify_plan(db, "PLAN-DELETED", rows)
            assert item["match_status"] == "unmatched"
        finally:
            db.execute("DELETE FROM bud_learner_link WHERE internal_learner_id = %s", (learner["id"],))
            db.execute("DELETE FROM learners WHERE id = %s", (learner["id"],))


class TestClassification:
    def test_a_matched_plan_with_no_existing_requirement_is_new(self, db, learner_factory):
        learner = learner_factory()
        db.execute("INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)", (learner["id"], "PLAN-NEW"))
        rows = [_subject_row("english", 1, "percentage"), _subject_row("english", 2, "exempt")]
        item = classify_plan(db, "PLAN-NEW", rows)
        assert item["match_status"] == "new"
        assert item["proposed_english"] is True
        assert item["proposed_maths"] is False

    def test_a_matching_proposal_against_an_existing_requirement_is_unchanged(self, db, learner_factory, admin_user):
        learner = learner_factory()
        db.execute("INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)", (learner["id"], "PLAN-SAME"))
        _upsert_requirement(db, learner["id"], False, True, "recorded", None, admin_user["userId"])
        rows = [_subject_row("english", 1, "percentage"), _subject_row("english", 2, "exempt")]
        item = classify_plan(db, "PLAN-SAME", rows)
        assert item["match_status"] == "unchanged"

    def test_a_differing_proposal_against_an_existing_requirement_is_changed(self, db, learner_factory, admin_user):
        learner = learner_factory()
        db.execute("INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)", (learner["id"], "PLAN-CHANGED"))
        _upsert_requirement(db, learner["id"], False, False, "recorded", None, admin_user["userId"])
        rows = [_subject_row("english", 1, "percentage"), _subject_row("english", 2, "exempt")]
        item = classify_plan(db, "PLAN-CHANGED", rows)
        assert item["match_status"] == "changed"

    def test_overwriting_a_manual_upload_record_is_warned(self, db, learner_factory, admin_user):
        learner = learner_factory()
        db.execute("INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)", (learner["id"], "PLAN-MANUAL"))
        _upsert_requirement(db, learner["id"], False, False, "recorded", None, admin_user["userId"], source="manual_upload")
        rows = [_subject_row("english", 1, "percentage"), _subject_row("english", 2, "exempt")]
        item = classify_plan(db, "PLAN-MANUAL", rows)
        assert item["match_status"] == "changed"
        assert any("overwrite" in w for w in item["warnings"])

    def test_a_cleared_requirement_is_blocked_and_never_reinstated(self, db, learner_factory, admin_user):
        learner = learner_factory()
        db.execute("INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)", (learner["id"], "PLAN-CLEARED"))
        _upsert_requirement(db, learner["id"], False, False, "cleared", None, admin_user["userId"])
        rows = [_subject_row("english", 1, "percentage"), _subject_row("english", 2, "exempt")]
        item = classify_plan(db, "PLAN-CLEARED", rows)
        assert item["match_status"] == "blocked_by_clear"

    def test_a_matching_proposal_against_an_existing_bud_fs_sync_row_is_unchanged(self, db, learner_factory, admin_user):
        learner = learner_factory()
        db.execute("INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)", (learner["id"], "PLAN-ALREADY-SYNCED"))
        _upsert_requirement(db, learner["id"], False, True, "recorded", None, admin_user["userId"], source="bud_fs_sync")
        rows = [_subject_row("english", 1, "percentage"), _subject_row("english", 2, "exempt")]
        item = classify_plan(db, "PLAN-ALREADY-SYNCED", rows)
        assert item["match_status"] == "unchanged"


class TestPreview:
    def test_preview_classifies_every_distinct_plan_and_updates_counts(
        self, db, admin_user, request_factory, learner_factory, bud_fs_progress_row_factory, fs_bud_sync_cleanup,
    ):
        learner = learner_factory()
        row = bud_fs_progress_row_factory(subject="english", level=1, status_category="percentage", functional_skill_status="40%")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=row["budLearningPlanId"], functional_skill="English - Level 2",
            subject="english", level=2, status_category="exempt", functional_skill_status="Exempt",
        )
        db.execute(
            "INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)",
            (learner["id"], row["budLearningPlanId"]),
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        assert job["status"] == "ready"
        assert job["totalPlansExamined"] == 1
        assert job["newCount"] == 1

        items = list_items(db, job["id"], None, 1, 25)
        assert items["total"] == 1
        assert items["items"][0]["matchStatus"] == "new"
        assert items["items"][0]["proposedEnglish"] is True


class TestBulkApprove:
    def test_bulk_approve_accepts_actionable_items_and_rejects_the_rest(
        self, db, admin_user, request_factory, learner_factory, bud_fs_progress_row_factory, fs_bud_sync_cleanup,
    ):
        learner = learner_factory()
        row = bud_fs_progress_row_factory(functional_skill="Maths - Level 1", subject="math", level=1, status_category="percentage")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=row["budLearningPlanId"], functional_skill="Maths - Level 2",
            subject="math", level=2, status_category="exempt",
        )
        db.execute(
            "INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)",
            (learner["id"], row["budLearningPlanId"]),
        )
        unmatched_row = bud_fs_progress_row_factory(subject="english", level=1, status_category="percentage")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=unmatched_row["budLearningPlanId"], functional_skill="English - Level 2",
            subject="english", level=2, status_category="exempt",
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        items = list_items(db, job["id"], None, 1, 25)["items"]
        new_item = next(i for i in items if i["matchStatus"] == "new")
        unmatched_item = next(i for i in items if i["matchStatus"] == "unmatched")

        result = bulk_approve_items(
            db, job["id"], [new_item["id"], unmatched_item["id"]], request_factory(admin_user), admin_user,
        )
        assert result["approvedCount"] == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["itemId"] == unmatched_item["id"]


class TestCommit:
    def test_commit_applies_a_new_requirement_with_bud_fs_sync_source(
        self, db, admin_user, request_factory, learner_factory, bud_fs_progress_row_factory, fs_bud_sync_cleanup,
    ):
        learner = learner_factory()
        row = bud_fs_progress_row_factory(subject="english", level=1, status_category="percentage")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=row["budLearningPlanId"], functional_skill="English - Level 2",
            subject="english", level=2, status_category="exempt",
        )
        db.execute(
            "INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)",
            (learner["id"], row["budLearningPlanId"]),
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        item = list_items(db, job["id"], None, 1, 25)["items"][0]
        update_item(db, job["id"], item["id"], True)

        result = run_commit(db, job["id"], [item["id"]], "first bud fs sync batch", request_factory(admin_user), admin_user)
        assert result["status"] == "completed"
        assert result["appliedCount"] == 1

        db.execute(f"{REQUIREMENT_SELECT} WHERE learner_id = %s", (learner["id"],))
        requirement = db.fetchone()
        assert requirement["source"] == "bud_fs_sync"
        assert requirement["english"] is True
        assert requirement["status"] == "recorded"

    def test_idempotent_replay_of_a_completed_job_reapplies_nothing(
        self, db, admin_user, request_factory, learner_factory, bud_fs_progress_row_factory, fs_bud_sync_cleanup,
    ):
        learner = learner_factory()
        row = bud_fs_progress_row_factory(functional_skill="Maths - Level 1", subject="math", level=1, status_category="percentage")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=row["budLearningPlanId"], functional_skill="Maths - Level 2",
            subject="math", level=2, status_category="exempt",
        )
        db.execute(
            "INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)",
            (learner["id"], row["budLearningPlanId"]),
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        item = list_items(db, job["id"], None, 1, 25)["items"][0]
        update_item(db, job["id"], item["id"], True)

        first = run_commit(db, job["id"], [item["id"]], "reason", request_factory(admin_user), admin_user)
        second = run_commit(db, job["id"], [item["id"]], "reason", request_factory(admin_user), admin_user)
        assert first["status"] == second["status"] == "completed"
        assert second["appliedCount"] == first["appliedCount"]

    def test_a_mirror_table_change_since_preview_is_rejected_as_stale(
        self, db, admin_user, request_factory, learner_factory, bud_fs_progress_row_factory, fs_bud_sync_cleanup,
    ):
        learner = learner_factory()
        row = bud_fs_progress_row_factory(subject="english", level=1, status_category="percentage")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=row["budLearningPlanId"], functional_skill="English - Level 2",
            subject="english", level=2, status_category="exempt",
        )
        db.execute(
            "INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)",
            (learner["id"], row["budLearningPlanId"]),
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        item = list_items(db, job["id"], None, 1, 25)["items"][0]
        update_item(db, job["id"], item["id"], True)

        # Simulate a newer Fabric snapshot landing after preview.
        bud_fs_progress_row_factory(functional_skill="Maths - Level 1", subject="math", level=1, status_category="percentage", synced_at="2099-01-01T00:00:00Z")

        result = run_commit(db, job["id"], [item["id"]], "reason", request_factory(admin_user), admin_user)
        assert result["appliedCount"] == 0

        db.execute("SELECT outcome FROM fs_bud_sync_item WHERE id = %s", (item["id"],))
        assert db.fetchone()["outcome"] == "stale_source_rejected"

    def test_a_requirement_changed_since_preview_is_rejected_as_stale(
        self, db, admin_user, request_factory, learner_factory, bud_fs_progress_row_factory, fs_bud_sync_cleanup,
    ):
        learner = learner_factory()
        row = bud_fs_progress_row_factory(subject="english", level=1, status_category="percentage")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=row["budLearningPlanId"], functional_skill="English - Level 2",
            subject="english", level=2, status_category="exempt",
        )
        db.execute(
            "INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)",
            (learner["id"], row["budLearningPlanId"]),
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        item = list_items(db, job["id"], None, 1, 25)["items"][0]
        update_item(db, job["id"], item["id"], True)

        # An admin records something manually between preview and commit.
        _upsert_requirement(db, learner["id"], True, True, "recorded", None, admin_user["userId"], source="manual_upload")

        result = run_commit(db, job["id"], [item["id"]], "reason", request_factory(admin_user), admin_user)
        assert result["appliedCount"] == 0

        db.execute("SELECT outcome FROM fs_bud_sync_item WHERE id = %s", (item["id"],))
        assert db.fetchone()["outcome"] == "stale_internal_rejected"

    def test_committing_an_unmatched_item_is_rejected(
        self, db, admin_user, request_factory, bud_fs_progress_row_factory, fs_bud_sync_cleanup,
    ):
        row = bud_fs_progress_row_factory(subject="english", level=1, status_category="percentage")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=row["budLearningPlanId"], functional_skill="English - Level 2",
            subject="english", level=2, status_category="exempt",
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        item = list_items(db, job["id"], None, 1, 25)["items"][0]
        assert item["matchStatus"] == "unmatched"

        with pytest.raises(HTTPException) as exc_info:
            run_commit(db, job["id"], [item["id"]], "reason", request_factory(admin_user), admin_user)
        assert exc_info.value.status_code == 400


class TestJobSummary:
    def test_summary_counts_open_items_by_match_status(
        self, db, admin_user, request_factory, learner_factory, bud_fs_progress_row_factory, fs_bud_sync_cleanup,
    ):
        learner = learner_factory()
        row = bud_fs_progress_row_factory(subject="english", level=1, status_category="percentage")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=row["budLearningPlanId"], functional_skill="English - Level 2",
            subject="english", level=2, status_category="exempt",
        )
        db.execute(
            "INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)",
            (learner["id"], row["budLearningPlanId"]),
        )
        unmatched_row = bud_fs_progress_row_factory(functional_skill="Maths - Level 1", subject="math", level=1, status_category="percentage")
        bud_fs_progress_row_factory(
            bud_learning_plan_id=unmatched_row["budLearningPlanId"], functional_skill="Maths - Level 2",
            subject="math", level=2, status_category="exempt",
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        summary = get_job_summary(db, job["id"])

        assert summary["newCount"] == 1
        assert summary["unmatchedCount"] == 1
        assert summary["changedCount"] == 0
        assert summary["blockedByClearCount"] == 0


class TestSourceStatus:
    def test_status_reports_plan_counts_and_matching(
        self, db, learner_factory, bud_fs_progress_row_factory,
    ):
        learner = learner_factory()
        row = bud_fs_progress_row_factory(subject="english", level=1, status_category="percentage")
        db.execute(
            "INSERT INTO bud_learner_link (internal_learner_id, bud_learning_plan_id) VALUES (%s, %s)",
            (learner["id"], row["budLearningPlanId"]),
        )
        bud_fs_progress_row_factory(functional_skill="Maths - Level 1", subject="math", level=1, status_category="exempt")

        status = get_source_status(db)
        assert status["sourcePlanCount"] >= 2
        assert status["matchedPlanCount"] >= 1
        assert status["unmatchedPlanCount"] >= 1
