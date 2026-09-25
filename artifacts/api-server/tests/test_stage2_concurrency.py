"""Stage 2 verification pass, item 2: closing the tutor-mapping/sync
concurrency gaps a plain updated_at/ownership check cannot catch by itself.

Two different techniques are used deliberately:
- The "preview generating when mapping changes" and "commit running after a
  correction" scenarios are proven with ORDINARY SEQUENTIAL steps -- the
  generation-counter mechanism (tutor_mapping_generation, stamped on every
  preview job and re-checked with a locking read in run_commit) is
  correct-by-construction based on event ORDER, not wall-clock overlap, so
  a deterministic sequential test (preview, THEN correction, THEN attempt
  commit) exercises exactly the same code path a genuinely concurrent
  interleaving would, without flaky timing.
- "Two corrections concurrently claiming the same Bud identifier" is
  proven with REAL threads and REAL separate database connections, because
  the property being tested -- that Postgres's own advisory-lock blocking
  actually serializes two overlapping transactions rather than merely
  "usually" avoiding a collision -- can only be demonstrated by genuine
  concurrent execution.
"""
import threading

import pytest
from fastapi import HTTPException
from psycopg.rows import dict_row

from pyapp.bud_sync_lib import run_commit, run_preview
from pyapp.db import pool
from pyapp.tutor_identity_lib import commit_tutor_mapping_correction, preview_tutor_mapping_correction


class TestPreviewGeneratingWhenMappingChanges:
    def test_a_correction_committed_while_a_preview_was_generating_blocks_that_previews_commit(
        self, db, tutor_factory, bud_row_factory, baseline_factory, request_factory, admin_user,
    ):
        """Simulates the exact race: a preview starts (stamping the
        generation current AT THAT MOMENT), a tutor mapping correction
        lands before the admin gets to commit, and the commit must be
        rejected -- even though the job reached 'ready' perfectly
        normally and was never itself touched by the correction's
        'ready'-job sweep (it wasn't a mapping issue with ITS OWN items,
        just stale relative to the current generation)."""
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-GEN-RACE-1", source["tutorId"]))
        baseline_factory()
        bud_row_factory(learner_reference="REF-GEN-RACE-1", status_desc="Withdrawn")

        job = run_preview(db, request_factory(admin_user), admin_user)
        assert job["status"] == "ready"

        # The correction lands AFTER the preview already stamped its
        # generation -- exactly what happens if it commits while the
        # preview was still 'generating', or any time after.
        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-GEN-RACE-1")
        commit_tutor_mapping_correction(
            db, source["tutorId"], target["tutorId"], "BUD-GEN-RACE-1",
            preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
            "unrelated correction landing mid-race", request_factory(admin_user), admin_user,
            identity_confirmed_by_admin=True,
        )

        # The correction's own 'ready'-job sweep should already have
        # marked this job failed -- but even if it hadn't (the
        # 'generating' case), the generation check below is the real
        # guarantee.
        db.execute("SELECT status FROM bud_sync_job WHERE id = %s", (job["id"],))
        assert db.fetchone()["status"] == "failed"

        with pytest.raises(HTTPException) as exc:
            run_commit(db, job["id"], [], "attempt after an unrelated mapping correction", None,
                       request_factory(admin_user), admin_user)
        assert exc.value.status_code == 409

    def test_a_job_stamped_with_the_generation_still_current_commits_normally(
        self, db, tutor_factory, bud_row_factory, baseline_factory, request_factory, admin_user,
    ):
        """Negative control: with no correction in between, the generation
        check must never falsely reject an ordinary commit."""
        baseline_factory()
        bud_row_factory(learner_reference="REF-GEN-NO-RACE", status_desc="Withdrawn")
        job = run_preview(db, request_factory(admin_user), admin_user)

        result = run_commit(db, job["id"], [], "ordinary commit, no correction happened", None,
                             request_factory(admin_user), admin_user)
        assert result["status"] == "completed"


class TestCommitRunningAfterACorrection:
    def test_commit_rejects_even_when_the_job_was_never_swept_because_it_finished_generating_after_the_correction(
        self, db, tutor_factory, bud_row_factory, baseline_factory, request_factory, admin_user, monkeypatch,
    ):
        """Directly simulates a preview that was still 'generating' at the
        moment a correction committed: the job's generation stamp is
        forced to an OLDER value than the current one (as it would
        genuinely be if the correction's UPDATE of app_settings had
        already run before this job's own read of it finished), without
        relying on real thread timing to hit that exact window."""
        baseline_factory()
        bud_row_factory(learner_reference="REF-GEN-GENERATING-RACE", status_desc="Withdrawn")
        job = run_preview(db, request_factory(admin_user), admin_user)
        assert job["status"] == "ready"

        # Force this job's stamped generation to be stale, exactly as if a
        # correction had bumped app_settings.tutor_mapping_generation
        # between this job's own generation read and its INSERT.
        db.execute("UPDATE app_settings SET tutor_mapping_generation = tutor_mapping_generation + 1 WHERE id = 1")

        with pytest.raises(HTTPException) as exc:
            run_commit(db, job["id"], [], "attempt with an artificially stale generation stamp", None,
                       request_factory(admin_user), admin_user)
        assert exc.value.status_code == 409
        assert "tutor mapping" in exc.value.detail.lower()

        # And the job is left in a clean, visible 'failed' state -- not
        # stuck in 'committing' forever.
        db.execute("SELECT status FROM bud_sync_job WHERE id = %s", (job["id"],))
        assert db.fetchone()["status"] == "failed"


def _run_correction_in_new_connection(source_id, target_id, bud_tutor_id, reason, admin_user, results, key):
    """Runs one full commit_tutor_mapping_correction call on its OWN,
    separate pooled connection -- required for a genuine concurrency test,
    since two threads cannot safely share one psycopg cursor."""
    import types

    class _FakeRequest:
        def __init__(self, session):
            self.state = types.SimpleNamespace(session=session)
            self.headers: dict = {}
            self.client = types.SimpleNamespace(host="127.0.0.1")
            self.url = types.SimpleNamespace(path="/api/test-concurrency")

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                preview = preview_tutor_mapping_correction(cur, source_id, target_id, bud_tutor_id)
                result = commit_tutor_mapping_correction(
                    cur, source_id, target_id, bud_tutor_id,
                    preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                    reason, _FakeRequest(admin_user), admin_user, identity_confirmed_by_admin=True,
                )
        results[key] = ("ok", result)
    except HTTPException as exc:
        results[key] = ("http_error", exc.status_code, exc.detail)
    except Exception as exc:  # pragma: no cover -- would indicate a real bug, not an expected rejection
        results[key] = ("unexpected_error", exc)


class TestConcurrentIdentifierClaims:
    def test_two_concurrent_corrections_claiming_the_same_bud_id_are_serialized_not_interleaved(
        self, db, tutor_factory, admin_user,
    ):
        """Real concurrency: two threads, two separate database
        connections, both racing commit_tutor_mapping_correction for the
        SAME bud_tutor_id but different targets. Without the
        pg_advisory_xact_lock serialising them, both could read "no other
        holder" before either writes, corrupting which tutor actually
        ends up owning the identifier (or leaving it on neither/both,
        depending on write order). With it, exactly one succeeds and the
        other is cleanly rejected -- never both succeeding, never silent
        data loss."""
        source = tutor_factory(active=False)
        target_b = tutor_factory(active=True)
        target_c = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-CONCURRENT-CLAIM", source["tutorId"]))

        results: dict = {}
        barrier = threading.Barrier(2)

        def _worker(target_id, key):
            barrier.wait(timeout=5)
            _run_correction_in_new_connection(
                source["tutorId"], target_id, "BUD-CONCURRENT-CLAIM",
                f"concurrent claim attempt ({key})", admin_user, results, key,
            )

        thread_b = threading.Thread(target=_worker, args=(target_b["tutorId"], "b"))
        thread_c = threading.Thread(target=_worker, args=(target_c["tutorId"], "c"))
        thread_b.start()
        thread_c.start()
        thread_b.join(timeout=15)
        thread_c.join(timeout=15)

        assert "b" in results and "c" in results, "both threads must complete (no deadlock/hang)"
        outcomes = [results["b"], results["c"]]
        successes = [o for o in outcomes if o[0] == "ok"]
        rejections = [o for o in outcomes if o[0] == "http_error"]
        unexpected = [o for o in outcomes if o[0] == "unexpected_error"]

        assert unexpected == [], f"no unexpected errors: {unexpected}"
        assert len(successes) == 1, f"exactly one correction must succeed: {outcomes}"
        assert len(rejections) == 1, f"exactly one correction must be cleanly rejected: {outcomes}"
        assert rejections[0][1] == 409

        # The database must reflect EXACTLY one winner -- never both
        # targets holding the id, never neither.
        db.execute(
            "SELECT id FROM tutors WHERE external_system_id = %s AND id = ANY(%s)",
            ("BUD-CONCURRENT-CLAIM", [target_b["tutorId"], target_c["tutorId"]]),
        )
        holders = [r["id"] for r in db.fetchall()]
        assert len(holders) == 1, f"exactly one target tutor must hold the identifier afterward, got: {holders}"
