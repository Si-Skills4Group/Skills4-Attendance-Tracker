"""Direct equivalence proof for the allocation-reconciliation performance
fix: classify_row's per-row DB queries (_find_link_by_plan_id,
_find_link_by_learner_id, _find_learners_by_uln, _find_learners_by_reference,
_find_tutor_by_bud_id, and _classify_existing_learner_update's own learner
refetch) can now be satisfied from a bulk-preloaded `lookups` dict
(build_bulk_lookups) instead of one query per row. Every test here asserts
classify_row(..., lookups=None) and classify_row(..., lookups=<built>)
return IDENTICAL results for the same scenario -- this is the direct
evidence that sync preview/commit behaviour (which always calls with
lookups=None, unchanged) and the new bulk path agree exactly, not merely
"probably equivalent by inspection"."""
from pyapp.bud_sync_lib import build_bulk_lookups, classify_row, get_active_baseline


def _classify_both_ways(db, row, ambiguous=None):
    baseline = get_active_baseline(db)
    without_lookups = classify_row(db, row, baseline, ambiguous)
    with_lookups = classify_row(db, row, baseline, ambiguous, build_bulk_lookups(db))
    return without_lookups, with_lookups


def _assert_identical(without_lookups: dict, with_lookups: dict):
    assert without_lookups == with_lookups, (
        f"lookups-based classification diverged from the per-row-query path:\n"
        f"without_lookups={without_lookups}\nwith_lookups={with_lookups}"
    )


class TestBulkLookupsEquivalence:
    def test_new_learner_with_a_matched_tutor(self, db, tutor_factory, bud_row_factory):
        tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-TUTOR-NEW-1", tutor["tutorId"]))
        row = bud_row_factory(learner_reference="REF-EQUIV-NEW-1", status_desc="In Progress", tutor_id="BUD-TUTOR-NEW-1")

        without_lookups, with_lookups = _classify_both_ways(db, row)
        _assert_identical(without_lookups, with_lookups)
        assert without_lookups["match_status"] == "new"

    def test_new_learner_with_an_unmatched_tutor(self, db, bud_row_factory):
        row = bud_row_factory(learner_reference="REF-EQUIV-NEW-2", status_desc="In Progress", tutor_id="NOT-A-REAL-TUTOR-GUID")

        without_lookups, with_lookups = _classify_both_ways(db, row)
        _assert_identical(without_lookups, with_lookups)
        assert without_lookups["reason"] == "tutor_unmatched"

    def test_first_observation_of_an_already_existing_learner_via_reference(self, db, learner_factory, bud_row_factory):
        learner = learner_factory(learner_ref="REF-EQUIV-FIRST-OBS")
        row = bud_row_factory(learner_reference="REF-EQUIV-FIRST-OBS", status_desc="In Progress")

        without_lookups, with_lookups = _classify_both_ways(db, row)
        _assert_identical(without_lookups, with_lookups)
        assert without_lookups["internal_learner_id"] == learner["id"]
        assert without_lookups["match_status"] == "existing_update"

    def test_established_link_with_a_changed_field(self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory):
        from pyapp.bud_sync_lib import run_commit, run_preview, update_item

        learner = learner_factory(learner_ref="REF-EQUIV-LINKED", email="old@example.com")
        baseline_factory()
        bud_row_factory(learner_reference="REF-EQUIV-LINKED", learner_email="old@example.com", synced_at="2099-01-01T00:00:00Z")
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)

        db.execute("UPDATE public.learner_progress SET learner_email = 'new@example.com', synced_at = '2099-06-01T00:00:00Z' WHERE learner_reference = 'REF-EQUIV-LINKED'")
        db.execute(
            'SELECT learning_plan_id AS "learningPlanId", apprentice_id AS "apprenticeId", learner_forename AS "learnerForename", '
            'learner_surname AS "learnerSurname", learner_email AS "learnerEmail", learner_mobile AS "learnerMobile", '
            'learner_reference AS "learnerReference", unique_learner_number AS "uln", start_date AS "startDate", '
            'tutor_name AS "tutorName", tutor_id AS "budTutorId", programme_name AS "programmeName", '
            'status_desc AS "statusDesc", learning_plan_url AS "learningPlanUrl", synced_at AS "syncedAt" '
            "FROM public.learner_progress WHERE learner_reference = 'REF-EQUIV-LINKED'",
        )
        row = db.fetchone()

        without_lookups, with_lookups = _classify_both_ways(db, row)
        _assert_identical(without_lookups, with_lookups)
        assert without_lookups["proposed_values"]["fields"]["email"]["after"] == "new@example.com"

    def test_ambiguous_reference_conflict(self, db, learner_factory, bud_row_factory):
        learner_factory(learner_ref="REF-EQUIV-AMBIG")
        row_a = bud_row_factory(learner_reference="REF-EQUIV-AMBIG", status_desc="In Progress")
        bud_row_factory(learner_reference="REF-EQUIV-AMBIG", status_desc="Completed")

        without_lookups, with_lookups = _classify_both_ways(db, row_a)
        _assert_identical(without_lookups, with_lookups)
        assert without_lookups["reason"] == "learner_reference_matches_multiple_bud_rows"

    # No test for "uln_matches_multiple_internal_learners": learners_uln_unique
    # (bootstrap.py) is a genuine partial unique index on (uln) WHERE uln IS
    # NOT NULL AND uln <> '' -- confirmed by hitting it directly while
    # writing this test. Two learners cannot share a real ULN under the
    # current schema, so this classify_row conflict branch, while still
    # present in the code, is not constructible to test here. Worth noting
    # as a finding: this narrows the matching hierarchy's own real-world
    # conflict surface, contrary to an earlier assumption that uln had no
    # uniqueness constraint at all.

    def test_reference_and_uln_disagree_conflict(self, db, learner_factory, bud_row_factory):
        learner_factory(learner_ref="REF-EQUIV-DISAGREE-A", uln="ULN-EQUIV-DISAGREE-A")
        learner_factory(learner_ref="REF-EQUIV-DISAGREE-B", uln="ULN-EQUIV-DISAGREE-B")
        row = bud_row_factory(learner_reference="REF-EQUIV-DISAGREE-A", unique_learner_number="ULN-EQUIV-DISAGREE-B", status_desc="In Progress")

        without_lookups, with_lookups = _classify_both_ways(db, row)
        _assert_identical(without_lookups, with_lookups)
        assert without_lookups["reason"] == "learner_reference_and_uln_disagree"

    def test_learner_already_linked_to_a_different_bud_record_conflict(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        from pyapp.bud_sync_lib import run_commit, run_preview, update_item

        learner = learner_factory(learner_ref="REF-EQUIV-ALREADY-LINKED")
        baseline_factory()
        first_plan = bud_row_factory(learner_reference="REF-EQUIV-ALREADY-LINKED", synced_at="2099-01-01T00:00:00Z")
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)

        # The original plan Bud reported is no longer present in the source
        # extract (simulating Bud dropping it) -- without this, a second
        # distinct plan sharing the same reference would make the reference
        # itself ambiguous (classify_row's earlier, higher-priority check),
        # never reaching this specific conflict branch at all.
        db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id = %s", (first_plan["learningPlanId"],))
        row = bud_row_factory(learner_reference="REF-EQUIV-ALREADY-LINKED", status_desc="In Progress")

        without_lookups, with_lookups = _classify_both_ways(db, row)
        _assert_identical(without_lookups, with_lookups)
        assert without_lookups["reason"] == "learner_already_linked_to_a_different_bud_record"

    def test_matched_learner_no_longer_exists_conflict(
        self, db, learner_factory, bud_row_factory, baseline_factory, admin_user, request_factory,
    ):
        from pyapp.bud_sync_lib import run_commit, run_preview, update_item

        learner = learner_factory(learner_ref="REF-EQUIV-DELETED")
        baseline_factory()
        bud_row_factory(learner_reference="REF-EQUIV-DELETED", synced_at="2099-01-01T00:00:00Z")
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)

        db.execute("UPDATE learners SET deleted_at = now() WHERE id = %s", (learner["id"],))
        db.execute("UPDATE public.learner_progress SET synced_at = '2099-06-01T00:00:00Z' WHERE learner_reference = 'REF-EQUIV-DELETED'")
        db.execute(
            'SELECT learning_plan_id AS "learningPlanId", apprentice_id AS "apprenticeId", learner_forename AS "learnerForename", '
            'learner_surname AS "learnerSurname", learner_email AS "learnerEmail", learner_mobile AS "learnerMobile", '
            'learner_reference AS "learnerReference", unique_learner_number AS "uln", start_date AS "startDate", '
            'tutor_name AS "tutorName", tutor_id AS "budTutorId", programme_name AS "programmeName", '
            'status_desc AS "statusDesc", learning_plan_url AS "learningPlanUrl", synced_at AS "syncedAt" '
            "FROM public.learner_progress WHERE learner_reference = 'REF-EQUIV-DELETED'",
        )
        row = db.fetchone()

        without_lookups, with_lookups = _classify_both_ways(db, row)
        _assert_identical(without_lookups, with_lookups)
        assert without_lookups["reason"] == "matched_learner_no_longer_exists"

    def test_non_actionable_unmatched_status(self, db, bud_row_factory):
        row = bud_row_factory(learner_reference="REF-EQUIV-NONACTIONABLE", status_desc="Withdrawn")

        without_lookups, with_lookups = _classify_both_ways(db, row)
        _assert_identical(without_lookups, with_lookups)
        assert without_lookups["match_status"] == "existing_before_trial"

    def test_blank_uln_on_both_sides_never_produces_a_bogus_match(self, db, learner_factory, bud_row_factory):
        """Regression guard for the bulk-lookup index specifically: many
        learners can share an empty-string uln, and _find_learners_by_uln
        is never even called for a blank Bud uln (classify_row's own
        `if bud_row.get("uln")` guard) -- build_bulk_lookups must not index
        blank ulns either, or a blank-uln Bud row could spuriously "match"
        every blank-uln learner via the bulk path only."""
        learner_factory(learner_ref="REF-EQUIV-BLANK-ULN-1", uln="")
        learner_factory(learner_ref="REF-EQUIV-BLANK-ULN-2", uln="")
        row = bud_row_factory(learner_reference="REF-EQUIV-BLANK-ULN-UNMATCHED", unique_learner_number="", status_desc="In Progress")

        without_lookups, with_lookups = _classify_both_ways(db, row)
        _assert_identical(without_lookups, with_lookups)
        # No reference match, and a blank uln is never even looked up (see
        # classify_row's own `if bud_row.get("uln")` guard) -- must not be
        # a bogus "uln_matches_multiple_internal_learners" conflict from the
        # two blank-uln learners seeded above.
        assert without_lookups["internal_learner_id"] is None
        assert without_lookups["reason"] != "uln_matches_multiple_internal_learners"
