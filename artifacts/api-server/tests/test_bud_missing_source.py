"""Stage 2, item 6: reverse-presence exceptions for previously-linked,
internally active learners whose linked Bud row has gone missing. Every
test here checks the learner's OWN status/record is completely untouched --
this module only ever writes to bud_missing_source_exception."""
import pytest

from pyapp.bud_missing_source_lib import (
    get_missing_source_refresh_status,
    list_missing_source_exceptions,
    refresh_missing_source_exceptions,
    sync_missing_source_exceptions,
)


def _establish_link(db, learner, bud_row_factory, baseline_factory, admin_user, request_factory, learner_ref, plan_synced_at="2099-01-01T00:00:00Z"):
    from pyapp.bud_sync_lib import run_commit, run_preview, update_item

    baseline_factory()
    plan = bud_row_factory(learner_reference=learner_ref, status_desc="In Progress", synced_at=plan_synced_at)
    job = run_preview(db, request_factory(admin_user), admin_user)
    db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]))
    item_id = db.fetchone()["id"]
    update_item(db, job["id"], item_id, None, True)
    run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)
    return plan


class TestMissingSourceDetection:
    def test_missing_linked_plan_generates_an_open_exception_without_changing_learner_status(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        learner = learner_factory(learner_ref="REF-MISSING-SOURCE-1", status="active")
        plan = _establish_link(db, learner, bud_row_factory, baseline_factory, admin_user, request_factory, "REF-MISSING-SOURCE-1")

        # The linked plan disappears from the source entirely.
        db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id = %s", (plan["learningPlanId"],))

        result = sync_missing_source_exceptions(db)
        assert result["newlyOpened"] == 1

        exceptions = list_missing_source_exceptions(db, status="open")
        entry = next(e for e in exceptions if e["internalLearnerId"] == learner["id"])
        assert entry["budLearningPlanId"] == plan["learningPlanId"]
        assert entry["personEntirelyAbsentFromSource"] is True
        assert entry["otherPlansForPersonStillPresent"] is False

        db.execute("SELECT status FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["status"] == "active", "must never infer withdrawal/completion from a missing source row"

    def test_other_plans_still_present_is_distinguished_from_entirely_absent(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        learner = learner_factory(learner_ref="REF-MISSING-SOURCE-2", status="active")
        plan = _establish_link(db, learner, bud_row_factory, baseline_factory, admin_user, request_factory, "REF-MISSING-SOURCE-2")
        # A second, still-present row for the same person's reference.
        bud_row_factory(learner_reference="REF-MISSING-SOURCE-2", status_desc="Completed")

        db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id = %s", (plan["learningPlanId"],))
        sync_missing_source_exceptions(db)

        exceptions = list_missing_source_exceptions(db, status="open")
        entry = next(e for e in exceptions if e["internalLearnerId"] == learner["id"])
        assert entry["otherPlansForPersonStillPresent"] is True
        assert entry["personEntirelyAbsentFromSource"] is False

    def test_repeated_sync_does_not_duplicate_the_open_exception(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        learner = learner_factory(learner_ref="REF-MISSING-SOURCE-DUPE", status="active")
        plan = _establish_link(db, learner, bud_row_factory, baseline_factory, admin_user, request_factory, "REF-MISSING-SOURCE-DUPE")
        db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id = %s", (plan["learningPlanId"],))

        first = sync_missing_source_exceptions(db)
        second = sync_missing_source_exceptions(db)
        third = sync_missing_source_exceptions(db)
        assert first["newlyOpened"] == 1
        assert second["newlyOpened"] == 0
        assert third["newlyOpened"] == 0

        db.execute(
            "SELECT count(*) AS n FROM bud_missing_source_exception WHERE internal_learner_id = %s AND status = 'open'",
            (learner["id"],),
        )
        assert db.fetchone()["n"] == 1

    def test_source_row_returning_resolves_the_exception_and_keeps_history(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        learner = learner_factory(learner_ref="REF-MISSING-SOURCE-RETURN", status="active")
        plan = _establish_link(db, learner, bud_row_factory, baseline_factory, admin_user, request_factory, "REF-MISSING-SOURCE-RETURN")

        db.execute(
            "DELETE FROM public.learner_progress WHERE learning_plan_id = %s RETURNING *", (plan["learningPlanId"],),
        )
        deleted_row = db.fetchone()
        sync_missing_source_exceptions(db)
        assert len(list_missing_source_exceptions(db, status="open")) >= 1
        open_before = [e for e in list_missing_source_exceptions(db, status="open") if e["internalLearnerId"] == learner["id"]]
        assert len(open_before) == 1

        # The source row comes back.
        columns = list(deleted_row.keys())
        placeholders = ", ".join(f"%({c})s" for c in columns)
        db.execute(f"INSERT INTO public.learner_progress ({', '.join(columns)}) VALUES ({placeholders})", deleted_row)

        result = sync_missing_source_exceptions(db)
        assert result["resolved"] == 1

        open_after = [e for e in list_missing_source_exceptions(db, status="open") if e["internalLearnerId"] == learner["id"]]
        assert open_after == []
        resolved_after = [e for e in list_missing_source_exceptions(db, status="resolved") if e["internalLearnerId"] == learner["id"]]
        assert len(resolved_after) == 1
        assert resolved_after[0]["resolvedAt"] is not None

    def test_inactive_learner_with_a_missing_linked_plan_is_not_flagged(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        """The reverse-presence check is scoped to internally ACTIVE
        learners -- a withdrawn/completed learner's plan disappearing from
        Bud is not a review-worthy surprise the way an active one's is."""
        learner = learner_factory(learner_ref="REF-MISSING-SOURCE-INACTIVE", status="active")
        plan = _establish_link(db, learner, bud_row_factory, baseline_factory, admin_user, request_factory, "REF-MISSING-SOURCE-INACTIVE")
        db.execute("UPDATE learners SET status = 'withdrawn', withdrawal_date = '2026-01-01' WHERE id = %s", (learner["id"],))
        db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id = %s", (plan["learningPlanId"],))

        sync_missing_source_exceptions(db)
        exceptions = list_missing_source_exceptions(db, status="open")
        assert not any(e["internalLearnerId"] == learner["id"] for e in exceptions)


class TestRefreshSeparatedFromGet:
    """Stage 2 verification pass, item 1: GET must be read-only -- detection
    only ever runs via refresh_missing_source_exceptions (the POST /refresh
    route). See routers/bud_missing_source.py for the route split."""

    def test_get_route_performs_no_writes(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        from pyapp.routers.bud_missing_source import get_missing_source_exceptions

        learner = learner_factory(learner_ref="REF-GET-NO-WRITES", status="active")
        plan = _establish_link(db, learner, bud_row_factory, baseline_factory, admin_user, request_factory, "REF-GET-NO-WRITES")
        db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id = %s", (plan["learningPlanId"],))

        db.execute("SELECT count(*) AS n FROM bud_missing_source_exception")
        before = db.fetchone()["n"]

        # The GET route itself -- called directly as the plain function
        # FastAPI would dispatch to -- must not detect anything, even
        # though a genuinely missing linked plan exists right now.
        result = get_missing_source_exceptions(status="open", _session=admin_user)
        assert not any(e["internalLearnerId"] == learner["id"] for e in result["items"])

        db.execute("SELECT count(*) AS n FROM bud_missing_source_exception")
        after = db.fetchone()["n"]
        assert after == before, "GET must never write to bud_missing_source_exception"

    def test_refresh_detects_and_updates_status(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        learner = learner_factory(learner_ref="REF-REFRESH-DETECTS", status="active")
        plan = _establish_link(db, learner, bud_row_factory, baseline_factory, admin_user, request_factory, "REF-REFRESH-DETECTS")
        db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id = %s", (plan["learningPlanId"],))

        result = refresh_missing_source_exceptions(db, request_factory(admin_user), admin_user)
        assert result["newlyOpened"] >= 1

        status = get_missing_source_refresh_status(db)
        assert status["lastSucceededAt"] is not None
        assert status["lastAttemptedAt"] is not None
        assert status["lastError"] is None
        assert status["lastTriggeredBy"] == admin_user["userId"]

        exceptions = list_missing_source_exceptions(db, status="open")
        assert any(e["internalLearnerId"] == learner["id"] for e in exceptions)

    def test_refresh_failure_preserves_last_successful_results(self, db, monkeypatch, admin_user, request_factory):
        """Stage 2 verification pass, item 1: a failed refresh attempt must
        record last_attempted_at/last_error but leave last_succeeded_at and
        the previous result counts completely untouched -- the interface
        must never mistake a failed or partial refresh for confirmed
        results, and must never lose the last known-good ones either."""
        import pyapp.bud_missing_source_lib as lib

        # A first, genuinely successful refresh establishes a known-good baseline.
        good = refresh_missing_source_exceptions(db, request_factory(admin_user), admin_user)
        status_before = get_missing_source_refresh_status(db)
        assert status_before["lastSucceededAt"] is not None

        def _boom(cur):
            raise RuntimeError("simulated detection failure")

        monkeypatch.setattr(lib, "sync_missing_source_exceptions", _boom)

        with pytest.raises(RuntimeError, match="simulated detection failure"):
            refresh_missing_source_exceptions(db, request_factory(admin_user), admin_user)

        status_after = get_missing_source_refresh_status(db)
        assert status_after["lastError"] is not None
        assert "simulated detection failure" in status_after["lastError"]
        # The last KNOWN-GOOD figures must be exactly what the successful
        # run left behind -- never overwritten, never discarded, by the
        # failed attempt.
        assert status_after["lastSucceededAt"] == status_before["lastSucceededAt"]
        assert status_after["lastNewlyOpened"] == status_before["lastNewlyOpened"]
        assert status_after["lastResolved"] == status_before["lastResolved"]
        # last_attempted_at DOES advance -- an attempt genuinely was made.
        assert status_after["lastAttemptedAt"] >= status_before["lastAttemptedAt"]
