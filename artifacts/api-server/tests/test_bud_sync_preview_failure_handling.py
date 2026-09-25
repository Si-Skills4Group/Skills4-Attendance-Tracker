"""Stage 2, item 5: a failed preview must never be mistaken for a completed
one, a partial preview must never be committable, and retrying must never
produce duplicated or conflicting actionable results. These tests inject a
deterministic failure partway through run_preview's classification loop
(by monkeypatching classify_row to raise on its second call) rather than
relying on a real, unreliable failure condition -- the point is to prove
the transaction/status-flip mechanism itself, not to find a real bug that
triggers it."""
import pytest
from fastapi import HTTPException

from pyapp import bud_sync_lib
from pyapp.bud_sync_lib import get_job, run_commit, run_preview


def _fail_on_nth_call(n: int):
    calls = {"count": 0}

    def fake_classify_row(cur, bud_row, baseline, ambiguous_learner_references=None, lookups=None):
        calls["count"] += 1
        if calls["count"] == n:
            raise RuntimeError("simulated classification failure")
        return real_classify_row(cur, bud_row, baseline, ambiguous_learner_references, lookups)

    return fake_classify_row


real_classify_row = bud_sync_lib.classify_row


class TestPreviewFailureHandling:
    def test_a_failure_partway_through_marks_the_job_failed_not_ready(
        self, db, monkeypatch, admin_user, request_factory, baseline_factory, bud_row_factory,
    ):
        baseline_factory()
        bud_row_factory(learner_reference="REF-PREVIEW-FAIL-1")
        bud_row_factory(learner_reference="REF-PREVIEW-FAIL-2")
        monkeypatch.setattr(bud_sync_lib, "classify_row", _fail_on_nth_call(2))

        with pytest.raises(RuntimeError, match="simulated classification failure"):
            run_preview(db, request_factory(admin_user), admin_user)

        db.execute("SELECT id, status, error_summary FROM bud_sync_job ORDER BY id DESC LIMIT 1")
        job = db.fetchone()
        assert job["status"] == "failed"
        assert "simulated classification failure" in job["error_summary"]

    def test_a_failed_preview_leaves_zero_items_not_a_partial_set(
        self, db, monkeypatch, admin_user, request_factory, baseline_factory, bud_row_factory,
    ):
        baseline_factory()
        bud_row_factory(learner_reference="REF-PREVIEW-FAIL-PARTIAL-1")
        bud_row_factory(learner_reference="REF-PREVIEW-FAIL-PARTIAL-2")
        bud_row_factory(learner_reference="REF-PREVIEW-FAIL-PARTIAL-3")
        monkeypatch.setattr(bud_sync_lib, "classify_row", _fail_on_nth_call(2))

        with pytest.raises(RuntimeError):
            run_preview(db, request_factory(admin_user), admin_user)

        db.execute("SELECT id FROM bud_sync_job ORDER BY id DESC LIMIT 1")
        job_id = db.fetchone()["id"]
        db.execute("SELECT count(*) AS n FROM bud_sync_item WHERE sync_job_id = %s", (job_id,))
        assert db.fetchone()["n"] == 0, "the first successfully-classified item must not survive the rollback either"

    def test_commit_rejects_a_failed_preview(
        self, db, monkeypatch, admin_user, request_factory, baseline_factory, bud_row_factory,
    ):
        baseline_factory()
        bud_row_factory(learner_reference="REF-PREVIEW-FAIL-COMMIT-1")
        bud_row_factory(learner_reference="REF-PREVIEW-FAIL-COMMIT-2")
        monkeypatch.setattr(bud_sync_lib, "classify_row", _fail_on_nth_call(2))
        with pytest.raises(RuntimeError):
            run_preview(db, request_factory(admin_user), admin_user)

        db.execute("SELECT id FROM bud_sync_job ORDER BY id DESC LIMIT 1")
        job_id = db.fetchone()["id"]

        with pytest.raises(HTTPException) as exc:
            run_commit(db, job_id, [], "attempt commit on failed preview", None, request_factory(admin_user), admin_user)
        assert exc.value.status_code == 409
        assert "not ready" in exc.value.detail.lower()

    def test_retry_after_a_failure_produces_a_fresh_independent_successful_job(
        self, db, monkeypatch, admin_user, request_factory, baseline_factory, bud_row_factory,
    ):
        baseline_factory()
        bud_row_factory(learner_reference="REF-PREVIEW-RETRY-1")
        bud_row_factory(learner_reference="REF-PREVIEW-RETRY-2")
        monkeypatch.setattr(bud_sync_lib, "classify_row", _fail_on_nth_call(2))
        with pytest.raises(RuntimeError):
            run_preview(db, request_factory(admin_user), admin_user)

        monkeypatch.setattr(bud_sync_lib, "classify_row", real_classify_row)
        retried_job = run_preview(db, request_factory(admin_user), admin_user)

        assert retried_job["status"] == "ready"
        db.execute("SELECT count(*) AS n FROM bud_sync_item WHERE sync_job_id = %s", (retried_job["id"],))
        # Both seeded rows are unmatched with no eligible status_desc override
        # here, so they classify as existing_before_trial -- the exact
        # match_status doesn't matter for this test, only that every row was
        # classified exactly once, with no duplication from the failed
        # attempt bleeding into this one (a completely separate job_id).
        assert db.fetchone()["n"] == 2

    def test_failed_job_is_never_picked_up_as_the_latest_valid_preview(
        self, db, monkeypatch, admin_user, request_factory, baseline_factory, bud_row_factory,
    ):
        """get_unmatched_pre_baseline must keep showing the last GOOD
        preview's data, not silently report zero because the most recent
        job (by id) happened to fail."""
        from pyapp.bud_sync_lib import get_unmatched_pre_baseline

        baseline_factory()
        bud_row_factory(learner_reference="REF-PREVIEW-STALE-CHECK", status_desc="Withdrawn")
        good_job = run_preview(db, request_factory(admin_user), admin_user)
        assert good_job["status"] == "ready"

        before = get_unmatched_pre_baseline(db, page=1, page_size=50)
        assert before["total"] >= 1

        bud_row_factory(learner_reference="REF-PREVIEW-STALE-CHECK-2", status_desc="Withdrawn")
        monkeypatch.setattr(bud_sync_lib, "classify_row", _fail_on_nth_call(1))
        with pytest.raises(RuntimeError):
            run_preview(db, request_factory(admin_user), admin_user)

        after = get_unmatched_pre_baseline(db, page=1, page_size=50)
        assert after["total"] == before["total"], "must still reflect the last successful preview, not the failed one"
