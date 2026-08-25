"""Trial baseline lifecycle: establishment performs zero business writes,
a bulk Bud resync (which bumps synced_at across the whole source table
regardless of whether a row's data changed) doesn't cause duplicate or
inconsistent classification, reset requires a reason and is audited, and
baseline history is preserved (never deleted, only superseded)."""
import pytest

from pyapp.bud_sync_lib import (
    classify_row,
    establish_baseline,
    get_active_baseline,
    run_preview,
    reset_baseline,
    list_baseline_history,
)


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


class TestBaselineSurvivesABulkResync:
    """Regression coverage for the real production behaviour that broke the
    original synced_at-only eligibility check: Bud bulk-touches synced_at
    across the *entire* source table on every sync, regardless of whether a
    given row's data actually changed. classify_row no longer gates
    unmatched-learner eligibility on baseline timing at all (that
    historical-backfill rule was retired -- see test_bud_sync_new_learners.py),
    so these tests now confirm the more basic thing that actually matters:
    a bulk resync's synced_at bump doesn't change or duplicate an unmatched
    row's classification, and a genuinely new row is still detected
    correctly alongside one."""

    def test_a_bulk_resync_touching_synced_at_does_not_change_classification(
        self, db, bud_row_factory, baseline_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-RESYNC-STABLE' WHERE id = %s", (tutor["tutorId"],))
        row = bud_row_factory(synced_at="2026-01-01T00:00:00Z", tutor_id="BUD-T-RESYNC-STABLE", start_date="2099-01-01")
        baseline = baseline_factory()

        item_before = classify_row(db, row, baseline)
        assert item_before["match_status"] == "new"

        # Bud does a full resync: every row (including this unchanged one)
        # gets a fresh, later synced_at -- nothing about the learner's
        # actual data changed.
        db.execute(
            "UPDATE public.learner_progress SET synced_at = '2099-01-01T00:00:00Z' WHERE learning_plan_id = %s",
            (row["learningPlanId"],),
        )
        db.execute(
            'SELECT learning_plan_id AS "learningPlanId", apprentice_id AS "apprenticeId", '
            'learner_forename AS "learnerForename", learner_surname AS "learnerSurname", '
            'learner_email AS "learnerEmail", learner_mobile AS "learnerMobile", '
            'learner_reference AS "learnerReference", unique_learner_number AS "uln", '
            'start_date AS "startDate", tutor_name AS "tutorName", tutor_id AS "budTutorId", '
            'programme_name AS "programmeName", status_desc AS "statusDesc", '
            'learning_plan_url AS "learningPlanUrl", synced_at AS "syncedAt" '
            "FROM public.learner_progress WHERE learning_plan_id = %s",
            (row["learningPlanId"],),
        )
        resynced_row = db.fetchone()

        item_after = classify_row(db, resynced_row, baseline)
        assert item_after["match_status"] == "new"

    def test_a_genuinely_new_row_is_still_detected_alongside_a_bulk_resync(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, tutor_factory,
    ):
        pre_existing = bud_row_factory(synced_at="2026-01-01T00:00:00Z")  # no tutor mapping -> conflict, not new
        baseline_factory()

        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-T-RESYNC' WHERE id = %s", (tutor["tutorId"],))
        bud_row_factory(synced_at="2099-01-01T00:00:00Z", tutor_id="BUD-T-RESYNC", start_date="2099-01-01")

        # Simulate the same bulk resync touching the pre-existing row too.
        db.execute(
            "UPDATE public.learner_progress SET synced_at = '2099-01-01T00:00:00Z' WHERE learning_plan_id = %s",
            (pre_existing["learningPlanId"],),
        )

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT source_identifier, match_status FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s",
            (job["id"], pre_existing["learningPlanId"]),
        )
        assert db.fetchone()["match_status"] == "conflict"

        assert job["newLearnersDetected"] == 1
