"""Trial limits and commit robustness: commit is blocked above the
configured app_settings limits unless explicitly overridden (and the
override is audited), a stale source or stale internal row is excluded
from a commit rather than blindly applied, and re-running commit on an
already-completed job is a no-op that reports the same result."""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from pyapp.bud_sync_lib import run_commit, run_preview, update_item


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


@pytest.fixture
def _one_learner_creation_limit(db):
    db.execute("SELECT bud_sync_max_learner_creations FROM app_settings WHERE id = 1")
    original = db.fetchone()["bud_sync_max_learner_creations"]
    db.execute("UPDATE app_settings SET bud_sync_max_learner_creations = 1 WHERE id = 1")
    yield
    db.execute("UPDATE app_settings SET bud_sync_max_learner_creations = %s WHERE id = 1", (original,))


def _prep_two_new_learners(db, bud_row_factory, tutor_factory, prefix):
    tutor = tutor_factory()
    db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", (f"BUD-LIM-{prefix}", tutor["tutorId"]))
    bud_row_factory(tutor_id=f"BUD-LIM-{prefix}", start_date=_tomorrow(), synced_at="2099-01-01T00:00:00Z")
    bud_row_factory(tutor_id=f"BUD-LIM-{prefix}", start_date=_tomorrow(), synced_at="2099-01-01T00:00:00Z")
    return tutor


class TestTrialLimits:
    def test_commit_is_blocked_above_the_configured_limit(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory, _one_learner_creation_limit,
    ):
        baseline_factory()
        _prep_two_new_learners(db, bud_row_factory, tutor_factory, "A")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_ids = [r["id"] for r in db.fetchall()]
        assert len(item_ids) == 2
        for item_id in item_ids:
            update_item(
                db, job["id"], item_id,
                {"learner.level": "3", "learner.learnerRef": f"BUD-LIM-{item_id}"},
                True,
            )

        with pytest.raises(HTTPException) as exc_info:
            run_commit(db, job["id"], item_ids, "over limit", None, request_factory(admin_user), admin_user)
        assert exc_info.value.status_code == 409

        db.execute("SELECT count(*)::int AS c FROM learners WHERE learner_ref LIKE 'BUD-LIM-%'")
        assert db.fetchone()["c"] == 0

        # Regression: this rejection is an ordinary, correctable validation
        # error -- the job must stay "ready" (not get wedged as "failed")
        # so the admin can simply resubmit with an override reason.
        db.execute("SELECT status FROM bud_sync_job WHERE id = %s", (job["id"],))
        assert db.fetchone()["status"] == "ready"

    def test_commit_can_be_retried_with_an_override_reason_after_the_limit_rejection(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory, _one_learner_creation_limit,
    ):
        baseline_factory()
        _prep_two_new_learners(db, bud_row_factory, tutor_factory, "R")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_ids = [r["id"] for r in db.fetchall()]
        for item_id in item_ids:
            update_item(
                db, job["id"], item_id,
                {"learner.level": "3", "learner.learnerRef": f"BUD-LIM-R-{item_id}"},
                True,
            )

        with pytest.raises(HTTPException) as exc_info:
            run_commit(db, job["id"], item_ids, "over limit", None, request_factory(admin_user), admin_user)
        assert exc_info.value.status_code == 409

        result = run_commit(
            db, job["id"], item_ids, "over limit but approved", "Trial expansion approved by admin",
            request_factory(admin_user), admin_user,
        )
        assert result["status"] == "completed"

        db.execute("SELECT id FROM learners WHERE learner_ref LIKE 'BUD-LIM-R-%'")
        created = db.fetchall()
        assert len(created) == 2
        for row in created:
            db.execute("DELETE FROM bud_learner_link WHERE internal_learner_id = %s", (row["id"],))
            db.execute("DELETE FROM learners WHERE id = %s", (row["id"],))

    def test_override_reason_allows_commit_and_is_audited(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory, _one_learner_creation_limit,
    ):
        baseline_factory()
        _prep_two_new_learners(db, bud_row_factory, tutor_factory, "B")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND match_status = 'new'", (job["id"],))
        item_ids = [r["id"] for r in db.fetchall()]
        for item_id in item_ids:
            update_item(
                db, job["id"], item_id,
                {"learner.level": "3", "learner.learnerRef": f"BUD-LIM-B-{item_id}"},
                True,
            )

        result = run_commit(db, job["id"], item_ids, "over limit but approved", "Trial expansion approved by admin", request_factory(admin_user), admin_user)
        assert result["status"] == "completed"

        db.execute(
            "SELECT count(*)::int AS c FROM audit_logs WHERE action = 'bud_sync_commit_limit_override' AND entity_id = %s",
            (job["id"],),
        )
        assert db.fetchone()["c"] == 1

        db.execute("SELECT id FROM learners WHERE learner_ref LIKE 'BUD-LIM-B-%'")
        created = db.fetchall()
        assert len(created) == 2
        for row in created:
            db.execute("DELETE FROM bud_learner_link WHERE internal_learner_id = %s", (row["id"],))
            db.execute("DELETE FROM learners WHERE id = %s", (row["id"],))


class TestCommitIdempotencyAndStaleness:
    def test_recommitting_a_completed_job_is_a_no_op(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = learner_factory(email="old@example.com", uln="ULN-IDEMP-1")
        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-IDEMP-1", learner_email="new@example.com", synced_at="2099-01-01T00:00:00Z")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]),
        )
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)

        first = run_commit(db, job["id"], [item_id], "reason", None, request_factory(admin_user), admin_user)
        second = run_commit(db, job["id"], [item_id], "reason", None, request_factory(admin_user), admin_user)

        assert first["status"] == second["status"] == "completed"
        assert first["appliedCount"] == second["appliedCount"]
        db.execute("SELECT count(*)::int AS c FROM audit_logs WHERE action = 'bud_sync_item_applied' AND entity_id = %s", (item_id,))
        assert db.fetchone()["c"] == 1  # applied exactly once, not twice

    def test_a_source_row_changed_since_preview_is_excluded_not_blindly_applied(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = learner_factory(email="old@example.com", uln="ULN-STALE-1")
        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-STALE-1", learner_email="preview-time-value@example.com", synced_at="2099-01-01T00:00:00Z")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]),
        )
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)

        # Bud syncs again with a newer value before the admin commits.
        db.execute(
            "UPDATE public.learner_progress SET learner_email = 'even-newer@example.com', synced_at = '2099-06-01T00:00:00Z' "
            "WHERE unique_learner_number = 'ULN-STALE-1'"
        )

        run_commit(db, job["id"], [item_id], "reason", None, request_factory(admin_user), admin_user)

        db.execute("SELECT email FROM learners WHERE id = %s", (learner["id"],))
        # The stale (preview-time) value must never be written -- either
        # the item was excluded as stale (email stays 'old@example.com'),
        # never the value that was current only at preview time.
        assert db.fetchone()["email"] != "preview-time-value@example.com"

        db.execute("SELECT outcome FROM bud_sync_item WHERE id = %s", (item_id,))
        assert db.fetchone()["outcome"] == "stale_source_rejected"

    def test_an_internal_learner_changed_since_preview_is_excluded(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = learner_factory(email="old@example.com", uln="ULN-STALE-2")
        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-STALE-2", learner_email="new@example.com", synced_at="2099-01-01T00:00:00Z")

        # Establish the link first so previous_values.updatedAt is populated.
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]),
        )
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)

        # A second, real Bud change.
        db.execute(
            "UPDATE public.learner_progress SET learner_email = 'second-change@example.com', synced_at = '2099-06-01T00:00:00Z' "
            "WHERE unique_learner_number = 'ULN-STALE-2'"
        )
        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job2["id"], learner["id"]),
        )
        item2_id = db.fetchone()["id"]
        update_item(db, job2["id"], item2_id, None, True)

        # An admin independently edits the learner between preview and commit.
        db.execute("UPDATE learners SET first_name = 'ChangedByAdmin', updated_at = now() WHERE id = %s", (learner["id"],))

        run_commit(db, job2["id"], [item2_id], "reason", None, request_factory(admin_user), admin_user)

        db.execute("SELECT outcome FROM bud_sync_item WHERE id = %s", (item2_id,))
        assert db.fetchone()["outcome"] == "stale_internal_rejected"
        db.execute("SELECT email FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["email"] != "second-change@example.com"
