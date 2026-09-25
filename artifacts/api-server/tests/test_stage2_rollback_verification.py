"""Stage 2 verification pass, item 3: a successful atomic-correction test
alone does not demonstrate rollback -- these tests inject a real failure at
two different points inside commit_tutor_mapping_correction's transaction
(right after the first tutor UPDATE, and during the audit write) and prove
none of the following survive:
- a partially-moved Bud identifier (source cleared but target not set, or
  vice versa),
- a missing audit record alongside a partially-committed correction,
- a partially-invalidated set of outstanding sync preview jobs.
"""
import pytest
from fastapi import HTTPException

from pyapp import audit as audit_module
from pyapp.bud_sync_lib import run_preview
from pyapp.tutor_identity_lib import commit_tutor_mapping_correction, preview_tutor_mapping_correction


class _FailOnQuery:
    """Wraps a real cursor's execute, raising once a query matching `marker`
    is about to run -- the query itself never executes, simulating a crash
    at exactly that point without needing to touch the real implementation."""

    def __init__(self, cur, marker: str, message: str):
        self._cur = cur
        self._marker = marker
        self._message = message

    def execute(self, query, params=None, **kwargs):
        query_text = query if isinstance(query, str) else str(query)
        if self._marker in query_text:
            raise RuntimeError(self._message)
        return self._cur.execute(query, params, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cur, name)


def _setup_correction(db, tutor_factory, bud_tutor_id):
    source = tutor_factory(active=False)
    target = tutor_factory(active=True)
    db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", (bud_tutor_id, source["tutorId"]))
    preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], bud_tutor_id)
    return source, target, preview


class TestRollbackOnFailureAfterFirstTutorUpdate:
    def test_failure_between_the_two_tutor_updates_leaves_neither_changed(
        self, db, tutor_factory, request_factory, admin_user,
    ):
        source, target, preview = _setup_correction(db, tutor_factory, "BUD-FAIL-AFTER-FIRST-UPDATE")

        # The SECOND tutor UPDATE (the only one with "RETURNING
        # updated_at") never executes -- simulating a crash in the gap
        # right after the first one (source -> NULL) already ran.
        wrapped = _FailOnQuery(db, "RETURNING updated_at", "simulated failure after the first tutor update")

        with pytest.raises(RuntimeError, match="simulated failure after the first tutor update"):
            commit_tutor_mapping_correction(
                wrapped, source["tutorId"], target["tutorId"], "BUD-FAIL-AFTER-FIRST-UPDATE",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "will fail between the two tutor updates", request_factory(admin_user), admin_user,
                identity_confirmed_by_admin=True,
            )

        # Neither side of the correction may have landed -- source must
        # still hold the identifier it started with, target must still be
        # unset. A partial move (source cleared, target never set) would
        # be a genuine identifier LOSS, not just an inconsistency.
        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (source["tutorId"],))
        assert db.fetchone()["external_system_id"] == "BUD-FAIL-AFTER-FIRST-UPDATE", \
            "source tutor's identifier must be intact after rollback -- not left cleared"
        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (target["tutorId"],))
        assert db.fetchone()["external_system_id"] is None, \
            "target tutor must not have been partially updated"

    def test_failure_between_the_two_tutor_updates_leaves_no_audit_record(
        self, db, tutor_factory, request_factory, admin_user,
    ):
        source, target, preview = _setup_correction(db, tutor_factory, "BUD-FAIL-AFTER-FIRST-AUDIT-CHECK")
        wrapped = _FailOnQuery(db, "RETURNING updated_at", "simulated failure after the first tutor update")

        with pytest.raises(RuntimeError):
            commit_tutor_mapping_correction(
                wrapped, source["tutorId"], target["tutorId"], "BUD-FAIL-AFTER-FIRST-AUDIT-CHECK",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "will fail before any audit write happens", request_factory(admin_user), admin_user,
                identity_confirmed_by_admin=True,
            )

        db.execute(
            "SELECT count(*) AS n FROM audit_logs WHERE action = 'tutor_mapping_correction_applied' AND entity_id = %s",
            (target["tutorId"],),
        )
        assert db.fetchone()["n"] == 0, "no audit record must exist for a correction that never actually committed"

    def test_failure_between_the_two_tutor_updates_leaves_no_sync_jobs_invalidated(
        self, db, tutor_factory, bud_row_factory, baseline_factory, request_factory, admin_user,
    ):
        baseline_factory()
        bud_row_factory(learner_reference="REF-ROLLBACK-JOB-CHECK", status_desc="Withdrawn")
        outstanding_job = run_preview(db, request_factory(admin_user), admin_user)
        assert outstanding_job["status"] == "ready"

        source, target, preview = _setup_correction(db, tutor_factory, "BUD-FAIL-AFTER-FIRST-JOB-CHECK")
        wrapped = _FailOnQuery(db, "RETURNING updated_at", "simulated failure after the first tutor update")

        with pytest.raises(RuntimeError):
            commit_tutor_mapping_correction(
                wrapped, source["tutorId"], target["tutorId"], "BUD-FAIL-AFTER-FIRST-JOB-CHECK",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "will fail before the ready-job sweep is ever reached", request_factory(admin_user), admin_user,
                identity_confirmed_by_admin=True,
            )

        # The sweep (which runs AFTER the tutor updates and audit write)
        # never ran at all -- the outstanding job must be untouched, not
        # left in some half-invalidated state.
        db.execute("SELECT status FROM bud_sync_job WHERE id = %s", (outstanding_job["id"],))
        assert db.fetchone()["status"] == "ready"


class TestRollbackOnFailureDuringAuditWrite:
    def test_failure_during_the_audit_write_leaves_neither_tutor_changed(
        self, db, tutor_factory, request_factory, admin_user, monkeypatch,
    ):
        """By this point BOTH tutor UPDATEs have already executed inside
        the same still-open transaction -- proving the audit write failing
        rolls those back too, not just itself."""
        source, target, preview = _setup_correction(db, tutor_factory, "BUD-FAIL-DURING-AUDIT")

        real_write_audit_log = audit_module.write_audit_log

        def _boom(*args, **kwargs):
            if kwargs.get("action") == "tutor_mapping_correction_applied":
                raise RuntimeError("simulated failure during the audit write")
            return real_write_audit_log(*args, **kwargs)

        monkeypatch.setattr("pyapp.tutor_identity_lib.write_audit_log", _boom)

        with pytest.raises(RuntimeError, match="simulated failure during the audit write"):
            commit_tutor_mapping_correction(
                db, source["tutorId"], target["tutorId"], "BUD-FAIL-DURING-AUDIT",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "will fail exactly at the audit write", request_factory(admin_user), admin_user,
                identity_confirmed_by_admin=True,
            )

        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (source["tutorId"],))
        assert db.fetchone()["external_system_id"] == "BUD-FAIL-DURING-AUDIT", \
            "both tutor UPDATEs must roll back even though they had already executed"
        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (target["tutorId"],))
        assert db.fetchone()["external_system_id"] is None

    def test_failure_during_the_audit_write_leaves_no_partial_audit_record(
        self, db, tutor_factory, request_factory, admin_user, monkeypatch,
    ):
        source, target, preview = _setup_correction(db, tutor_factory, "BUD-FAIL-DURING-AUDIT-2")

        def _boom(*args, **kwargs):
            if kwargs.get("action") == "tutor_mapping_correction_applied":
                raise RuntimeError("simulated failure during the audit write")
            return audit_module.write_audit_log(*args, **kwargs)

        monkeypatch.setattr("pyapp.tutor_identity_lib.write_audit_log", _boom)

        with pytest.raises(RuntimeError):
            commit_tutor_mapping_correction(
                db, source["tutorId"], target["tutorId"], "BUD-FAIL-DURING-AUDIT-2",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "will fail exactly at the audit write", request_factory(admin_user), admin_user,
                identity_confirmed_by_admin=True,
            )

        db.execute(
            "SELECT count(*) AS n FROM audit_logs WHERE action = 'tutor_mapping_correction_applied' AND entity_id = %s",
            (target["tutorId"],),
        )
        assert db.fetchone()["n"] == 0, \
            "a failed audit write must leave NO audit record for a committed correction -- never a partial one"

    def test_failure_during_the_audit_write_leaves_no_sync_jobs_invalidated(
        self, db, tutor_factory, bud_row_factory, baseline_factory, request_factory, admin_user, monkeypatch,
    ):
        baseline_factory()
        bud_row_factory(learner_reference="REF-ROLLBACK-AUDIT-JOB-CHECK", status_desc="Withdrawn")
        outstanding_job = run_preview(db, request_factory(admin_user), admin_user)
        assert outstanding_job["status"] == "ready"

        source, target, preview = _setup_correction(db, tutor_factory, "BUD-FAIL-DURING-AUDIT-3")

        def _boom(*args, **kwargs):
            if kwargs.get("action") == "tutor_mapping_correction_applied":
                raise RuntimeError("simulated failure during the audit write")
            return audit_module.write_audit_log(*args, **kwargs)

        monkeypatch.setattr("pyapp.tutor_identity_lib.write_audit_log", _boom)

        with pytest.raises(RuntimeError):
            commit_tutor_mapping_correction(
                db, source["tutorId"], target["tutorId"], "BUD-FAIL-DURING-AUDIT-3",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "will fail exactly at the audit write, before the ready-job sweep", request_factory(admin_user), admin_user,
                identity_confirmed_by_admin=True,
            )

        # The ready-job sweep runs AFTER the audit write that just failed
        # -- it must never have run, so the outstanding job stays 'ready'.
        db.execute("SELECT status FROM bud_sync_job WHERE id = %s", (outstanding_job["id"],))
        assert db.fetchone()["status"] == "ready"
