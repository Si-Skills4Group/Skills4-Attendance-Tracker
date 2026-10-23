"""A learner can legitimately re-enrol on a new Bud standard while their
old, now-withdrawn enrolment -- already linked from an earlier sync --
lingers in the source under the same learner_reference. classify_row must
flag the stale linked row as a conflict instead of silently diffing it into
a false "withdrawn" status change, and relink_to_new_enrollment is the one
reviewed way an Administrator resolves this: switch the single Bud link
over to the new enrolment and bring the learner's own record (programme,
etc.) in line with it, leaving their attendance status untouched."""
from fastapi import HTTPException
import pytest

from pyapp.bud_sync_lib import (
    _fetch_bud_rows,
    _find_link_by_learner_id,
    classify_row,
    relink_to_new_enrollment,
    run_commit,
    run_preview,
    update_item,
)


def _link_learner_via_full_cycle(db, request_factory, admin_user, learner, row):
    """Establishes a real bud_learner_link the natural way (preview, approve,
    commit) -- mirroring test_bud_sync_new_learners.py's own pattern -- so
    `learner` ends up genuinely linked to `row`'s plan before the test's
    actual scenario (a second, sibling enrolment) is introduced."""
    job = run_preview(db, request_factory(admin_user), admin_user)
    db.execute(
        "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s",
        (job["id"], learner["id"]),
    )
    item_id = db.fetchone()["id"]
    update_item(db, job["id"], item_id, None, True)
    run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)


class TestClassifyRowDetectsASiblingEnrollment:
    def test_the_old_linked_plan_becomes_a_conflict_not_a_status_change(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = learner_factory()
        baseline = baseline_factory()
        old_row = bud_row_factory(
            learner_reference=learner["learner_ref"], programme_name="Pharmacy Services Assistant v3.1",
            status_desc="In Progress",
        )
        _link_learner_via_full_cycle(db, request_factory, admin_user, learner, old_row)

        # Learner re-enrols on a new standard; Bud keeps the old enrolment
        # around (now Withdrawn) under the same learner_reference.
        db.execute(
            "UPDATE public.learner_progress SET status_desc = 'Withdrawn' WHERE learning_plan_id = %s",
            (old_row["learningPlanId"],),
        )
        bud_row_factory(
            learner_reference=learner["learner_ref"], programme_name="Pharmacy Technician (Integrated)",
            status_desc="In Progress", synced_at="2099-01-01T00:00:00Z",
        )

        fresh_rows = {r["learningPlanId"]: r for r in _fetch_bud_rows(db)}
        item = classify_row(db, fresh_rows[old_row["learningPlanId"]], baseline)
        assert item["match_status"] == "conflict"
        assert item["reason"] == "linked_bud_record_has_sibling_enrollment"
        assert item["proposed_values"]["programmeName"] == "Pharmacy Services Assistant v3.1"
        assert item["proposed_values"]["statusDesc"] == "Withdrawn"

    def test_the_new_unlinked_plans_own_classification_is_unchanged(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = learner_factory()
        baseline = baseline_factory()
        old_row = bud_row_factory(learner_reference=learner["learner_ref"], status_desc="In Progress")
        _link_learner_via_full_cycle(db, request_factory, admin_user, learner, old_row)

        db.execute(
            "UPDATE public.learner_progress SET status_desc = 'Withdrawn' WHERE learning_plan_id = %s",
            (old_row["learningPlanId"],),
        )
        new_row = bud_row_factory(
            learner_reference=learner["learner_ref"], status_desc="In Progress", synced_at="2099-01-01T00:00:00Z",
        )

        fresh_rows = {r["learningPlanId"]: r for r in _fetch_bud_rows(db)}
        item = classify_row(db, fresh_rows[new_row["learningPlanId"]], baseline)
        assert item["match_status"] == "conflict"
        assert item["reason"] == "learner_reference_matches_multiple_bud_rows"
        # Enriched for display -- an admin reviewing this conflict needs to
        # see what this row actually is before deciding to resolve it.
        assert item["proposed_values"]["statusDesc"] == "In Progress"

    def test_no_sibling_means_the_normal_status_change_still_fires(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        """Regression guard the other way: a learner with only ONE Bud
        enrolment who genuinely withdraws must still be detected normally --
        the new conflict check must not fire just because a link exists."""
        learner = learner_factory()
        baseline = baseline_factory()
        row = bud_row_factory(learner_reference=learner["learner_ref"], status_desc="In Progress")
        _link_learner_via_full_cycle(db, request_factory, admin_user, learner, row)

        db.execute(
            "UPDATE public.learner_progress SET status_desc = 'Withdrawn', synced_at = '2099-01-01T00:00:00Z' "
            "WHERE learning_plan_id = %s",
            (row["learningPlanId"],),
        )
        fresh_rows = {r["learningPlanId"]: r for r in _fetch_bud_rows(db)}
        item = classify_row(db, fresh_rows[row["learningPlanId"]], baseline)
        assert item["match_status"] == "status_change"


class TestRelinkToNewEnrollment:
    def _setup_sibling_scenario(self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory):
        learner = learner_factory()
        baseline_factory()
        old_row = bud_row_factory(
            learner_reference=learner["learner_ref"], programme_name="Pharmacy Services Assistant v3.1",
            status_desc="In Progress",
        )
        _link_learner_via_full_cycle(db, request_factory, admin_user, learner, old_row)
        db.execute(
            "UPDATE public.learner_progress SET status_desc = 'Withdrawn' WHERE learning_plan_id = %s",
            (old_row["learningPlanId"],),
        )
        new_row = bud_row_factory(
            learner_reference=learner["learner_ref"], programme_name="Pharmacy Technician (Integrated)",
            status_desc="In Progress", synced_at="2099-01-01T00:00:00Z",
        )
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            'SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s',
            (job["id"], new_row["learningPlanId"]),
        )
        new_item_id = db.fetchone()["id"]
        db.execute(
            'SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s',
            (job["id"], old_row["learningPlanId"]),
        )
        old_item_id = db.fetchone()["id"]
        return learner, old_row, new_row, job, old_item_id, new_item_id

    def test_relinking_updates_the_learners_programme_and_leaves_status_alone(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner, old_row, new_row, job, old_item_id, new_item_id = self._setup_sibling_scenario(
            db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
        )

        relink_to_new_enrollment(db, job["id"], new_item_id, request_factory(admin_user), admin_user)

        db.execute("SELECT programme, status FROM learners WHERE id = %s", (learner["id"],))
        row = db.fetchone()
        assert row["programme"] == "Pharmacy Technician (Integrated)"
        assert row["status"] == "active"  # never touched by this action

        link = _find_link_by_learner_id(db, learner["id"])
        assert link["budLearningPlanId"] == new_row["learningPlanId"]

    def test_relinking_marks_both_items_resolved(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner, old_row, new_row, job, old_item_id, new_item_id = self._setup_sibling_scenario(
            db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
        )

        relink_to_new_enrollment(db, job["id"], new_item_id, request_factory(admin_user), admin_user)

        db.execute("SELECT applied, outcome FROM bud_sync_item WHERE id = %s", (new_item_id,))
        new_item = db.fetchone()
        assert new_item["applied"] is True
        assert new_item["outcome"] == "relinked_new_enrollment"

        db.execute("SELECT outcome FROM bud_sync_item WHERE id = %s", (old_item_id,))
        assert db.fetchone()["outcome"] == "superseded_by_relink"

    def test_a_later_preview_diffs_against_the_new_enrollment_not_the_old_one(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner, old_row, new_row, job, old_item_id, new_item_id = self._setup_sibling_scenario(
            db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
        )
        relink_to_new_enrollment(db, job["id"], new_item_id, request_factory(admin_user), admin_user)

        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT match_status FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s",
            (job2["id"], new_row["learningPlanId"]),
        )
        assert db.fetchone()["match_status"] == "unchanged"

    def test_a_third_enrollment_appearing_later_is_freshly_flagged(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        """The acknowledged-siblings snapshot taken at relink time must not
        suppress a genuinely new ambiguity that shows up afterwards -- only
        the specific sibling(s) known about at relink time are acknowledged."""
        learner, old_row, new_row, job, old_item_id, new_item_id = self._setup_sibling_scenario(
            db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
        )
        relink_to_new_enrollment(db, job["id"], new_item_id, request_factory(admin_user), admin_user)

        # A third enrolment for the same reference appears after the relink.
        bud_row_factory(
            learner_reference=learner["learner_ref"], programme_name="Yet Another Standard",
            status_desc="In Progress", synced_at="2099-02-01T00:00:00Z",
        )

        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT match_status, reason FROM bud_sync_item WHERE sync_job_id = %s AND source_identifier = %s",
            (job2["id"], new_row["learningPlanId"]),
        )
        item = db.fetchone()
        assert item["match_status"] == "conflict"
        assert item["reason"] == "linked_bud_record_has_sibling_enrollment"

    def test_rejects_an_item_that_is_not_this_specific_conflict_reason(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = learner_factory()
        baseline_factory()
        bud_row_factory(learner_reference=learner["learner_ref"], learner_email="changed@example.com")
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s",
            (job["id"], learner["id"]),
        )
        item_id = db.fetchone()["id"]

        with pytest.raises(HTTPException) as exc:
            relink_to_new_enrollment(db, job["id"], item_id, request_factory(admin_user), admin_user)
        assert exc.value.status_code == 400

    def test_rejects_an_unknown_job_or_item(self, db, admin_user, request_factory):
        with pytest.raises(HTTPException) as exc:
            relink_to_new_enrollment(db, 999999999, 1, request_factory(admin_user), admin_user)
        assert exc.value.status_code == 404

    def test_rejects_a_bud_row_that_has_disappeared_since_preview(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner, old_row, new_row, job, old_item_id, new_item_id = self._setup_sibling_scenario(
            db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
        )
        db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id = %s", (new_row["learningPlanId"],))

        with pytest.raises(HTTPException) as exc:
            relink_to_new_enrollment(db, job["id"], new_item_id, request_factory(admin_user), admin_user)
        assert exc.value.status_code == 409
