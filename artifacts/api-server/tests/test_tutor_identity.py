"""Stage 2: tutor identity diagnostics and the controlled mapping-correction
workflow. See tutor_identity_lib.py's module docstring for the identity-vs-
eligibility distinction every test here respects -- none of these ever
assert that a correction transfers a learner, merges a tutor record, or
changes anything beyond the two tutors' external_system_id."""
import pytest
from fastapi import HTTPException

from pyapp import auth as auth_module
from pyapp.tutor_identity_lib import (
    build_tutor_identity_diagnostics,
    commit_tutor_mapping_correction,
    preview_tutor_mapping_correction,
)


def _as_tutor(monkeypatch, tutor_id=1, user_id=1):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}

    def fake_require_auth(request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)


def _as_admin(monkeypatch, user_id=1):
    session = {"userId": user_id, "role": "admin", "tutorId": None}

    def fake_require_auth(request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)


class TestTutorIdentityDiagnostics:
    def test_unmatched_bud_tutor_id_is_reported(self, db, bud_row_factory):
        bud_row_factory(tutor_id="BUD-TUTOR-UNMATCHED-X", tutor_name="Nobody Internal")
        result = build_tutor_identity_diagnostics(db)
        entry = next(e for e in result["unmatchedBudTutorIds"] if e["budTutorId"] == "BUD-TUTOR-UNMATCHED-X")
        assert entry["budTutorName"] == "Nobody Internal"
        assert entry["affectedLearnerReferences"] >= 1

    def test_bud_id_held_only_by_an_inactive_tutor_is_reported(self, db, tutor_factory, bud_row_factory):
        tutor = tutor_factory(active=False)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-TUTOR-INACTIVE-ONLY", tutor["tutorId"]))
        bud_row_factory(tutor_id="BUD-TUTOR-INACTIVE-ONLY", tutor_name="Inactive Person")

        result = build_tutor_identity_diagnostics(db)
        entry = next(e for e in result["budIdsHeldOnlyByInactiveTutor"] if e["budTutorId"] == "BUD-TUTOR-INACTIVE-ONLY")
        assert entry["inactiveTutor"]["id"] == tutor["tutorId"]

    def test_bud_id_attached_to_multiple_tutors_is_reported(self, db, tutor_factory, bud_row_factory):
        tutor_a = tutor_factory()
        tutor_b = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id IN (%s, %s)",
                   ("BUD-TUTOR-MULTI-OWNER", tutor_a["tutorId"], tutor_b["tutorId"]))
        bud_row_factory(tutor_id="BUD-TUTOR-MULTI-OWNER")

        result = build_tutor_identity_diagnostics(db)
        entry = next(e for e in result["budIdAttachedToMultipleTutors"] if e["budTutorId"] == "BUD-TUTOR-MULTI-OWNER")
        assert {t["id"] for t in entry["tutors"]} == {tutor_a["tutorId"], tutor_b["tutorId"]}

    def test_cleanly_matched_single_active_tutor_is_not_flagged_anywhere(self, db, tutor_factory, bud_row_factory):
        tutor = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-TUTOR-CLEAN", tutor["tutorId"]))
        bud_row_factory(tutor_id="BUD-TUTOR-CLEAN")

        result = build_tutor_identity_diagnostics(db)
        all_flagged_ids = (
            [e["budTutorId"] for e in result["unmatchedBudTutorIds"]]
            + [e["budTutorId"] for e in result["budIdsHeldOnlyByInactiveTutor"]]
            + [e["budTutorId"] for e in result["budIdAttachedToMultipleTutors"]]
        )
        assert "BUD-TUTOR-CLEAN" not in all_flagged_ids

    def test_similar_names_belonging_to_different_people_are_not_flagged_as_duplicates(self, db, tutor_factory):
        """Two tutors who merely share a common surname (a real scenario,
        distinct from Ellie's exact-name-match case) must NOT be flagged --
        the heuristic is exact full-name match only, deliberately narrow so
        it never suggests merging two different people."""
        user_a_suffix = "SmithA"
        user_b_suffix = "SmithB"
        import os
        db.execute(
            "INSERT INTO users (first_name, last_name, email, role, active) VALUES ('Jane', 'Smith', %s, 'tutor', true) RETURNING id",
            (f"jane-smith-{os.urandom(4).hex()}@example.com",),
        )
        jane_user_id = db.fetchone()["id"]
        db.execute(
            "INSERT INTO tutors (user_id, first_name, last_name, email, active) VALUES (%s, 'Jane', 'Smith', %s, true) RETURNING id",
            (jane_user_id, f"jane-smith-{os.urandom(4).hex()}@example.com"),
        )
        jane_tutor_id = db.fetchone()["id"]
        db.execute(
            "INSERT INTO users (first_name, last_name, email, role, active) VALUES ('John', 'Smith', %s, 'tutor', true) RETURNING id",
            (f"john-smith-{os.urandom(4).hex()}@example.com",),
        )
        john_user_id = db.fetchone()["id"]
        db.execute(
            "INSERT INTO tutors (user_id, first_name, last_name, email, active) VALUES (%s, 'John', 'Smith', %s, true) RETURNING id",
            (john_user_id, f"john-smith-{os.urandom(4).hex()}@example.com"),
        )
        john_tutor_id = db.fetchone()["id"]
        try:
            result = build_tutor_identity_diagnostics(db)
            flagged_ids = {t["id"] for group in result["duplicateTutorCandidates"] for t in group["tutors"]}
            assert jane_tutor_id not in flagged_ids
            assert john_tutor_id not in flagged_ids
        finally:
            db.execute("DELETE FROM tutors WHERE id IN (%s, %s)", (jane_tutor_id, john_tutor_id))
            db.execute("DELETE FROM users WHERE id IN (%s, %s)", (jane_user_id, john_user_id))

    def test_exact_same_name_different_tutor_records_are_flagged_as_duplicate_candidates(self, db, tutor_factory):
        """Mirrors the real Ellie Frost scenario: two tutor records with an
        identical first+last name are surfaced as review candidates -- this
        must NEVER auto-merge or change either record."""
        import os
        db.execute(
            "INSERT INTO users (first_name, last_name, email, role, active) VALUES ('Sam', 'Fixture', %s, 'tutor', true) RETURNING id",
            (f"sam-fixture-a-{os.urandom(4).hex()}@example.com",),
        )
        user_a = db.fetchone()["id"]
        db.execute(
            "INSERT INTO tutors (user_id, first_name, last_name, email, active) VALUES (%s, 'Sam', 'Fixture', %s, true) RETURNING id",
            (user_a, f"sam-fixture-a-{os.urandom(4).hex()}@example.com"),
        )
        tutor_a = db.fetchone()["id"]
        db.execute(
            "INSERT INTO users (first_name, last_name, email, role, active) VALUES ('Sam', 'Fixture', %s, 'tutor', false) RETURNING id",
            (f"sam-fixture-b-{os.urandom(4).hex()}@example.com",),
        )
        user_b = db.fetchone()["id"]
        db.execute(
            "INSERT INTO tutors (user_id, first_name, last_name, email, active) VALUES (%s, 'Sam', 'Fixture', %s, false) RETURNING id",
            (user_b, f"sam-fixture-b-{os.urandom(4).hex()}@example.com"),
        )
        tutor_b = db.fetchone()["id"]
        try:
            result = build_tutor_identity_diagnostics(db)
            group = next(g for g in result["duplicateTutorCandidates"] if g["normalizedName"] == "sam fixture")
            assert {t["id"] for t in group["tutors"]} == {tutor_a, tutor_b}
            # Read-only: still exactly the same active/inactive split as seeded.
            db.execute("SELECT id, active FROM tutors WHERE id IN (%s, %s)", (tutor_a, tutor_b))
            by_id = {r["id"]: r["active"] for r in db.fetchall()}
            assert by_id[tutor_a] is True
            assert by_id[tutor_b] is False
        finally:
            db.execute("DELETE FROM tutors WHERE id IN (%s, %s)", (tutor_a, tutor_b))
            db.execute("DELETE FROM users WHERE id IN (%s, %s)", (user_a, user_b))


class TestMappingCorrectionPreview:
    def test_preview_shows_conflicting_identifier_ownership(self, db, tutor_factory):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-CONFLICT-ID", source["tutorId"]))
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-SOME-OTHER-ID", target["tutorId"]))

        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-CONFLICT-ID")
        assert any("already holds a DIFFERENT" in c for c in preview["conflictingIdentifierOwnership"])

    def test_preview_shows_exact_field_changes(self, db, tutor_factory):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-FIELD-CHANGE-ID", source["tutorId"]))

        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-FIELD-CHANGE-ID")
        changes = {(c["tutorId"], c["field"]): (c["before"], c["after"]) for c in preview["fieldChanges"]}
        assert changes[(source["tutorId"], "externalSystemId")] == ("BUD-FIELD-CHANGE-ID", None)
        assert changes[(target["tutorId"], "externalSystemId")] == (None, "BUD-FIELD-CHANGE-ID")

    def test_preview_never_writes_anything(self, db, tutor_factory):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-PREVIEW-READONLY", source["tutorId"]))

        preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-PREVIEW-READONLY")

        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (source["tutorId"],))
        assert db.fetchone()["external_system_id"] == "BUD-PREVIEW-READONLY"
        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (target["tutorId"],))
        assert db.fetchone()["external_system_id"] is None

    def test_name_match_alone_never_counts_as_identity_confirmation(self, db, tutor_factory):
        """Regression guard for a real bug found during Stage 2 verification:
        an earlier version of this function checked a record's OWN
        employee_ref against the bud_tutor_id being reassigned TO IT --
        which says nothing about whether the OTHER record is the same
        person. tutor_factory's default fixtures share a name ("Test
        Tutor") but have no phone, no shared employee_ref, and no prior
        transfer between them -- so there should be no supporting signal
        here, even though the name matches exactly, and confirmation is
        always required regardless."""
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-NAME-ONLY", source["tutorId"]))

        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-NAME-ONLY")
        assert preview["nameMatch"] is True
        assert preview["supportingSignals"] == []
        assert preview["requiresManualIdentityConfirmation"] is True
        assert any("explicitly confirm" in u for u in preview["remainingUnresolved"])

    def test_shared_phone_number_is_a_supporting_signal_only(self, db, tutor_factory):
        """Stage 2 verification pass, item 4: a shared phone number is
        surfaced as a signal for a human to weigh -- it must NOT, by
        itself, mark identity as confirmed or relax the requirement that
        an admin explicitly confirms via identityConfirmedByAdmin."""
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-PHONE-EVIDENCE", source["tutorId"]))
        db.execute("UPDATE tutors SET phone = '07700900000' WHERE id IN (%s, %s)", (source["tutorId"], target["tutorId"]))

        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-PHONE-EVIDENCE")
        assert any("phone" in e.lower() for e in preview["supportingSignals"])
        # Still always required, regardless of the signal being present.
        assert preview["requiresManualIdentityConfirmation"] is True

    def test_prior_direct_transfer_between_the_two_records_is_a_supporting_signal_only(
        self, db, tutor_factory, learner_factory,
    ):
        """Stage 2 verification pass, item 4: a past learner transfer
        between these two tutor records is surfaced as a signal only --
        never described as proof, and never enough on its own to skip the
        explicit admin confirmation requirement."""
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        learner = learner_factory(tutor_id=target["tutorId"], status="active")
        db.execute(
            "INSERT INTO learner_allocation_history (learner_id, previous_tutor_id, new_tutor_id, "
            "previous_cohort_id, new_cohort_id, effective_date, changed_by) "
            "VALUES (%s, %s, %s, NULL, NULL, '2026-01-01', 1)",
            (learner["id"], source["tutorId"], target["tutorId"]),
        )
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-TRANSFER-EVIDENCE", source["tutorId"]))

        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-TRANSFER-EVIDENCE")
        signal = next(e for e in preview["supportingSignals"] if "transferred between these two tutor records" in e)
        assert "not proof" in signal.lower() or "does not" in signal.lower()
        assert preview["requiresManualIdentityConfirmation"] is True

        # And the signal alone still cannot commit without explicit confirmation.
        with pytest.raises(HTTPException) as exc:
            commit_tutor_mapping_correction(
                db, source["tutorId"], target["tutorId"], "BUD-TRANSFER-EVIDENCE",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "relying on the transfer signal alone", None, None, identity_confirmed_by_admin=False,
            )
        assert exc.value.status_code == 400


class TestMappingCorrectionCommit:
    def test_valid_mapping_correction_applies_atomically_with_audit(self, db, tutor_factory, request_factory, admin_user):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-VALID-CORRECTION", source["tutorId"]))
        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-VALID-CORRECTION")

        result = commit_tutor_mapping_correction(
            db, source["tutorId"], target["tutorId"], "BUD-VALID-CORRECTION",
            preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
            "Confirmed same person via employee_ref match", request_factory(admin_user), admin_user,
            identity_confirmed_by_admin=True,
        )
        assert result["appliedAt"] is not None

        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (source["tutorId"],))
        assert db.fetchone()["external_system_id"] is None
        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (target["tutorId"],))
        assert db.fetchone()["external_system_id"] == "BUD-VALID-CORRECTION"

        db.execute(
            "SELECT action, entity_id, previous_value, new_value FROM audit_logs "
            "WHERE action = 'tutor_mapping_correction_applied' AND entity_id = %s ORDER BY id DESC LIMIT 1",
            (target["tutorId"],),
        )
        log = db.fetchone()
        assert log is not None
        assert "Confirmed same person" in log["previous_value"]

    def test_commit_requires_a_reason(self, db, tutor_factory, request_factory, admin_user):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-NO-REASON", source["tutorId"]))
        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-NO-REASON")

        with pytest.raises(HTTPException) as exc:
            commit_tutor_mapping_correction(
                db, source["tutorId"], target["tutorId"], "BUD-NO-REASON",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "   ", request_factory(admin_user), admin_user, identity_confirmed_by_admin=True,
            )
        assert exc.value.status_code == 400

    def test_stale_preview_is_rejected(self, db, tutor_factory, request_factory, admin_user):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-STALE-CHECK", source["tutorId"]))
        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-STALE-CHECK")

        # The target tutor changes after preview (e.g. someone edits their
        # email) -- its updated_at moves, so the commit must be rejected.
        db.execute("UPDATE tutors SET email = 'changed-after-preview@example.com', updated_at = now() WHERE id = %s", (target["tutorId"],))

        with pytest.raises(HTTPException) as exc:
            commit_tutor_mapping_correction(
                db, source["tutorId"], target["tutorId"], "BUD-STALE-CHECK",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "attempt with stale preview", request_factory(admin_user), admin_user, identity_confirmed_by_admin=True,
            )
        assert exc.value.status_code == 409

        # And nothing was actually changed by the rejected attempt.
        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (source["tutorId"],))
        assert db.fetchone()["external_system_id"] == "BUD-STALE-CHECK"

    def test_conflicting_ownership_that_appeared_since_preview_is_rejected(self, db, tutor_factory, request_factory, admin_user):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        third = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-RACE-CONDITION", source["tutorId"]))
        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-RACE-CONDITION")

        # A third tutor claims the same Bud id between preview and commit.
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-RACE-CONDITION", third["tutorId"]))

        with pytest.raises(HTTPException) as exc:
            commit_tutor_mapping_correction(
                db, source["tutorId"], target["tutorId"], "BUD-RACE-CONDITION",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "attempt despite new conflict", request_factory(admin_user), admin_user, identity_confirmed_by_admin=True,
            )
        assert exc.value.status_code == 409

    def test_correction_never_transfers_learners_or_alters_attendance_history(
        self, db, tutor_factory, learner_factory, request_factory, admin_user,
    ):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        learner = learner_factory(tutor_id=source["tutorId"], status="active")
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-NO-LEARNER-TRANSFER", source["tutorId"]))
        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-NO-LEARNER-TRANSFER")

        commit_tutor_mapping_correction(
            db, source["tutorId"], target["tutorId"], "BUD-NO-LEARNER-TRANSFER",
            preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
            "identity mapping only, not a learner transfer", request_factory(admin_user), admin_user,
            identity_confirmed_by_admin=True,
        )

        db.execute("SELECT tutor_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["tutor_id"] == source["tutorId"], "the learner's own tutor_id must be completely untouched"

    def test_correction_invalidates_an_outstanding_ready_sync_preview(
        self, db, tutor_factory, bud_row_factory, baseline_factory, request_factory, admin_user,
    ):
        """run_commit's own staleness re-check is keyed on Bud's synced_at
        and learners.updated_at ONLY -- a tutor mapping correction changes
        neither, so without this safeguard an already-generated 'ready'
        preview could still be committed afterward, applying whatever
        tutor its proposed_values captured before the correction."""
        from pyapp.bud_sync_lib import run_commit, run_preview

        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-INVALIDATE-PREVIEW", source["tutorId"]))
        baseline_factory()
        bud_row_factory(learner_reference="REF-INVALIDATE-PREVIEW", status_desc="Withdrawn")
        stale_job = run_preview(db, request_factory(admin_user), admin_user)
        assert stale_job["status"] == "ready"

        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-INVALIDATE-PREVIEW")
        result = commit_tutor_mapping_correction(
            db, source["tutorId"], target["tutorId"], "BUD-INVALIDATE-PREVIEW",
            preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
            "correcting mapping, expect the outstanding preview to be invalidated",
            request_factory(admin_user), admin_user, identity_confirmed_by_admin=True,
        )
        assert stale_job["id"] in result["invalidatedPreviewJobIds"]

        db.execute("SELECT status, error_summary FROM bud_sync_job WHERE id = %s", (stale_job["id"],))
        row = db.fetchone()
        assert row["status"] == "failed"
        assert "tutor mapping correction" in row["error_summary"].lower()

        # Reusing run_commit's own existing 'not ready' guard -- no new
        # rejection logic needed there, this proves the reuse actually works.
        with pytest.raises(HTTPException) as exc:
            run_commit(db, stale_job["id"], [], "attempt to commit the invalidated preview", None,
                       request_factory(admin_user), admin_user)
        assert exc.value.status_code == 409

    def test_correction_does_not_invalidate_an_already_completed_job(
        self, db, tutor_factory, bud_row_factory, baseline_factory, request_factory, admin_user,
    ):
        from pyapp.bud_sync_lib import run_commit, run_preview, update_item

        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-KEEP-COMPLETED", source["tutorId"]))
        baseline_factory()
        bud_row_factory(learner_reference="REF-KEEP-COMPLETED", status_desc="Withdrawn")
        job = run_preview(db, request_factory(admin_user), admin_user)
        completed = run_commit(db, job["id"], [], "commit before correction", None, request_factory(admin_user), admin_user)
        assert completed["status"] == "completed"

        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-KEEP-COMPLETED")
        commit_tutor_mapping_correction(
            db, source["tutorId"], target["tutorId"], "BUD-KEEP-COMPLETED",
            preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
            "correcting mapping, completed job must be left alone", request_factory(admin_user), admin_user,
            identity_confirmed_by_admin=True,
        )
        db.execute("SELECT status FROM bud_sync_job WHERE id = %s", (job["id"],))
        assert db.fetchone()["status"] == "completed"

    def test_commit_rejects_when_identity_not_independently_confirmed(self, db, tutor_factory, request_factory, admin_user):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-UNCONFIRMED-IDENTITY", source["tutorId"]))
        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-UNCONFIRMED-IDENTITY")
        assert preview["requiresManualIdentityConfirmation"] is True

        with pytest.raises(HTTPException) as exc:
            commit_tutor_mapping_correction(
                db, source["tutorId"], target["tutorId"], "BUD-UNCONFIRMED-IDENTITY",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "attempt without confirming identity", request_factory(admin_user), admin_user,
                identity_confirmed_by_admin=False,
            )
        assert exc.value.status_code == 400
        assert "administrator must" in exc.value.detail.lower()

        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (source["tutorId"],))
        assert db.fetchone()["external_system_id"] == "BUD-UNCONFIRMED-IDENTITY", "rejected commit must not have written anything"

    def test_commit_succeeds_when_admin_explicitly_confirms_identity(self, db, tutor_factory, request_factory, admin_user):
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-EXPLICIT-CONFIRM", source["tutorId"]))
        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-EXPLICIT-CONFIRM")

        result = commit_tutor_mapping_correction(
            db, source["tutorId"], target["tutorId"], "BUD-EXPLICIT-CONFIRM",
            preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
            "Admin manually verified via HR records outside this system", request_factory(admin_user), admin_user,
            identity_confirmed_by_admin=True,
        )
        assert result["appliedAt"] is not None
        db.execute("SELECT external_system_id FROM tutors WHERE id = %s", (target["tutorId"],))
        assert db.fetchone()["external_system_id"] == "BUD-EXPLICIT-CONFIRM"

    def test_supporting_signals_never_bypass_the_explicit_confirmation_requirement(
        self, db, tutor_factory, request_factory, admin_user,
    ):
        """Stage 2 verification pass, item 4: even with a strong supporting
        signal (a shared phone number) present, commit must still refuse
        without identityConfirmedByAdmin=true -- the requirement is
        unconditional, enforced server-side, never relaxed by evidence
        strength on either the preview or the commit side."""
        source = tutor_factory(active=False)
        target = tutor_factory(active=True)
        db.execute("UPDATE tutors SET phone = '07700900111' WHERE id IN (%s, %s)", (source["tutorId"], target["tutorId"]))
        db.execute("UPDATE tutors SET external_system_id = %s WHERE id = %s", ("BUD-EVIDENCE-REMOVED", source["tutorId"]))
        preview = preview_tutor_mapping_correction(db, source["tutorId"], target["tutorId"], "BUD-EVIDENCE-REMOVED")
        assert preview["supportingSignals"] != []

        with pytest.raises(HTTPException) as exc:
            commit_tutor_mapping_correction(
                db, source["tutorId"], target["tutorId"], "BUD-EVIDENCE-REMOVED",
                preview["preview"]["sourceTutorUpdatedAt"], preview["preview"]["targetTutorUpdatedAt"],
                "attempt relying on the phone-number signal alone", request_factory(admin_user), admin_user,
                identity_confirmed_by_admin=False,
            )
        assert exc.value.status_code == 400
