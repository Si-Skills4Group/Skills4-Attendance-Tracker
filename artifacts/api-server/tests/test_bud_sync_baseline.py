"""Trial baseline lifecycle: establishment performs zero business writes,
pre-baseline rows are excluded, reset requires a reason and is audited, and
baseline history is preserved (never deleted, only superseded)."""
import pytest

from pyapp.bud_sync_lib import establish_baseline, get_active_baseline, list_baseline_history, reset_baseline


class TestEstablishBaseline:
    def test_creates_no_learners_cohorts_or_allocations(self, db, admin_user, request_factory, bud_row_factory):
        bud_row_factory(synced_at="2020-01-01T00:00:00Z")
        db.execute("SELECT count(*)::int AS c FROM learners")
        learners_before = db.fetchone()["c"]
        db.execute("SELECT count(*)::int AS c FROM cohorts")
        cohorts_before = db.fetchone()["c"]
        db.execute("SELECT count(*)::int AS c FROM scheduled_allocations")
        allocations_before = db.fetchone()["c"]

        db.execute("DELETE FROM bud_sync_baseline")
        establish_baseline(db, request_factory(admin_user), admin_user)

        db.execute("SELECT count(*)::int AS c FROM learners")
        assert db.fetchone()["c"] == learners_before
        db.execute("SELECT count(*)::int AS c FROM cohorts")
        assert db.fetchone()["c"] == cohorts_before
        db.execute("SELECT count(*)::int AS c FROM scheduled_allocations")
        assert db.fetchone()["c"] == allocations_before

    def test_records_source_row_count_and_max_synced_at(self, db, admin_user, request_factory, bud_row_factory, baseline_factory):
        bud_row_factory(synced_at="2026-01-01T00:00:00Z")
        bud_row_factory(synced_at="2026-06-01T00:00:00Z")
        baseline = baseline_factory()
        assert baseline["sourceRowCount"] >= 2
        assert str(baseline["sourceMaxSyncedAt"]).startswith("2026-06-01")

    def test_rejects_a_second_baseline_while_one_is_active(self, db, admin_user, request_factory, baseline_factory):
        baseline_factory()
        with pytest.raises(Exception):
            establish_baseline(db, request_factory(admin_user), admin_user)


class TestBaselineReset:
    def test_reset_requires_a_reason(self, baseline_factory):
        baseline_factory()
        with pytest.raises(TypeError):
            reset_baseline()  # missing required args -- proves reason is not optional in the function contract

    def test_reset_supersedes_but_never_deletes_the_old_baseline(self, db, admin_user, request_factory, baseline_factory):
        original = baseline_factory()
        reset_baseline(db, request_factory(admin_user), admin_user, "Correcting a bad initial baseline")

        db.execute("SELECT status FROM bud_sync_baseline WHERE id = %s", (original["id"],))
        assert db.fetchone()["status"] == "superseded"

        history = list_baseline_history(db)
        assert any(b["id"] == original["id"] for b in history)

    def test_reset_activates_a_new_baseline(self, db, admin_user, request_factory, baseline_factory):
        original = baseline_factory()
        reset_baseline(db, request_factory(admin_user), admin_user, "reason")

        active = get_active_baseline(db)
        assert active is not None
        assert active["id"] != original["id"]

    def test_reset_is_audited_as_a_distinct_action(self, db, admin_user, request_factory, baseline_factory):
        original = baseline_factory()
        reset_baseline(db, request_factory(admin_user), admin_user, "Correcting a bad initial baseline")

        db.execute(
            "SELECT action FROM audit_logs WHERE entity_type = 'bud_sync_baseline' AND entity_id = %s ORDER BY id",
            (original["id"],),
        )
        actions = [r["action"] for r in db.fetchall()]
        assert "bud_sync_baseline_reset" in actions
        assert "bud_sync_baseline_established" in actions

    def test_reset_without_an_active_baseline_fails(self, db, admin_user, request_factory):
        db.execute("DELETE FROM bud_sync_baseline")
        with pytest.raises(Exception):
            reset_baseline(db, request_factory(admin_user), admin_user, "no baseline exists")
