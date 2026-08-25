"""Existing-learner delta detection and application: comparison is always
against the last *accepted* Bud snapshot (not live internal values), a
pre-baseline change is never reconstructed, tutor transfers only ever use
bud_tutor_id (never tutor_name) and go through apply_transfer, and
historical attendance is never touched by any of this."""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from pyapp.bud_sync_lib import classify_row, run_commit, run_preview, update_item


def _tomorrow() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


class TestFieldUpdateDetection:
    def test_a_post_baseline_email_change_is_detected(self, db, bud_row_factory, baseline_factory, learner_factory):
        learner = learner_factory(email="old@example.com", uln="ULN-DELTA-1")
        baseline_factory()
        row = bud_row_factory(unique_learner_number="ULN-DELTA-1", learner_email="new@example.com", synced_at="2099-01-01T00:00:00Z")

        item = classify_row(db, row, _active_baseline(db))
        assert item["match_status"] == "existing_update"
        assert item["proposed_values"]["fields"]["email"]["after"] == "new@example.com"
        assert item["internal_learner_id"] == learner["id"]

    def test_an_unchanged_learner_produces_no_proposal(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        """The very first post-baseline observation of a ULN-matched-but-
        not-yet-linked learner always proposes establishing the link (even
        if the values happen to already agree) -- "unchanged" only applies
        to a *second* observation once a snapshot has actually been
        accepted, which this test exercises via a full preview+commit."""
        learner = learner_factory(email="same@example.com", uln="ULN-DELTA-2")
        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-DELTA-2", learner_email="same@example.com", synced_at="2099-01-01T00:00:00Z")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]),
        )
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)

        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT match_status FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s",
            (job2["id"], learner["id"]),
        )
        assert db.fetchone()["match_status"] == "unchanged"

    def test_a_matched_learner_is_processed_even_if_the_bud_row_predates_the_baseline(
        self, db, bud_row_factory, baseline_factory, learner_factory,
    ):
        """An already-matched learner must remain eligible for
        status/field-change detection regardless of when their Bud record
        first appeared -- baseline timing was never a factor for matched
        learners (only unmatched-learner new-learner detection used to gate
        on it, and that gate has since been retired too, see
        test_bud_sync_new_learners.py)."""
        learner = learner_factory(email="old@example.com", uln="ULN-DELTA-3")
        row = bud_row_factory(unique_learner_number="ULN-DELTA-3", learner_email="new@example.com", synced_at="2000-01-01T00:00:00Z")
        baseline = baseline_factory()

        item = classify_row(db, row, baseline)
        assert item["match_status"] == "existing_update"
        assert item["proposed_values"]["fields"]["email"]["after"] == "new@example.com"
        assert item["internal_learner_id"] == learner["id"]

    def test_comparison_is_against_accepted_snapshot_not_live_internal_value(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        """If an admin independently edits a learner's email through the
        normal Learner page, that is not a "Bud change" and must not be
        proposed just because it now differs from the current Bud value --
        only a change relative to the *last accepted* Bud snapshot counts."""
        learner = learner_factory(email="bud-value@example.com", uln="ULN-DELTA-4")
        baseline_factory()
        row = bud_row_factory(unique_learner_number="ULN-DELTA-4", learner_email="bud-value@example.com", synced_at="2099-01-01T00:00:00Z")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id, action_type FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s",
            (job["id"], learner["id"]),
        )
        item_row = db.fetchone()
        update_item(db, job["id"], item_row["id"], None, True)
        run_commit(db, job["id"], [item_row["id"]], "accept baseline value", None, request_factory(admin_user), admin_user)

        # Admin independently changes the internal email through the normal
        # learner-edit path -- unrelated to Bud.
        db.execute("UPDATE learners SET email = 'admin-edited@example.com', updated_at = now() WHERE id = %s", (learner["id"],))

        # A second preview against the *same*, unchanged Bud row must not
        # propose reverting the admin's edit back to the Bud value, since
        # the accepted snapshot already recorded that Bud value as seen.
        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT match_status FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s",
            (job2["id"], learner["id"]),
        )
        second_item = db.fetchone()
        assert second_item["match_status"] == "unchanged"


class TestTutorTransfer:
    def test_tutor_id_is_used_not_tutor_name(self, db, bud_row_factory, baseline_factory, learner_factory, tutor_factory):
        wrong_name_tutor = tutor_factory()
        real_tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-TT-1' WHERE id = %s", (real_tutor["tutorId"],))
        learner = learner_factory(uln="ULN-TT-1", tutor_id=wrong_name_tutor["tutorId"])
        baseline_factory()
        row = bud_row_factory(
            unique_learner_number="ULN-TT-1", tutor_id="BUD-TT-1", tutor_name="Someone Else Entirely",
            synced_at="2099-01-01T00:00:00Z",
        )

        item = classify_row(db, row, _active_baseline(db))
        assert item["action_type"] == "transfer_tutor"
        assert item["proposed_values"]["tutorTransfer"]["internalTutorId"] == real_tutor["tutorId"]

    def test_missing_tutor_mapping_is_a_conflict_not_a_transfer(self, db, bud_row_factory, baseline_factory, learner_factory):
        learner = learner_factory(uln="ULN-TT-2")
        baseline_factory()
        row = bud_row_factory(unique_learner_number="ULN-TT-2", tutor_id="NO-SUCH-TUTOR", synced_at="2099-01-01T00:00:00Z")

        item = classify_row(db, row, _active_baseline(db))
        assert "tutorTransfer" not in item["proposed_values"]
        assert any("tutor_id changed" in w for w in item["warnings"])

    def test_approved_transfer_uses_apply_transfer_and_creates_history(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory, tutor_factory,
    ):
        old_tutor = tutor_factory()
        new_tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-TT-3' WHERE id = %s", (new_tutor["tutorId"],))
        learner = learner_factory(uln="ULN-TT-3", tutor_id=old_tutor["tutorId"])
        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-TT-3", tutor_id="BUD-TT-3", synced_at="2099-01-01T00:00:00Z")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]),
        )
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "transfer approved", None, request_factory(admin_user), admin_user)

        db.execute("SELECT tutor_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["tutor_id"] == new_tutor["tutorId"]

        db.execute(
            "SELECT previous_tutor_id, new_tutor_id FROM learner_allocation_history WHERE learner_id = %s ORDER BY id DESC LIMIT 1",
            (learner["id"],),
        )
        history = db.fetchone()
        assert history["previous_tutor_id"] == old_tutor["tutorId"]
        assert history["new_tutor_id"] == new_tutor["tutorId"]

    def test_tutor_transfer_never_touches_attendance_records(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory, tutor_factory,
        cohort_factory, attendance_session_factory,
    ):
        old_tutor = tutor_factory()
        new_tutor = tutor_factory()
        db.execute("UPDATE tutors SET external_system_id = 'BUD-TT-4' WHERE id = %s", (new_tutor["tutorId"],))
        cohort = cohort_factory(tutor_id=old_tutor["tutorId"])
        learner = learner_factory(uln="ULN-TT-4", tutor_id=old_tutor["tutorId"], cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])
        db.execute(
            "INSERT INTO attendance_records (session_id, learner_id, status, hours_attended, minutes_late, created_by) "
            "VALUES (%s, %s, 'present', 7, 0, %s)",
            (session["id"], learner["id"], admin_user["userId"]),
        )

        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-TT-4", tutor_id="BUD-TT-4", synced_at="2099-01-01T00:00:00Z")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]),
        )
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "transfer approved", None, request_factory(admin_user), admin_user)

        db.execute(
            "SELECT status, session_id, learner_id FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (session["id"], learner["id"]),
        )
        record = db.fetchone()
        assert record["status"] == "present"

        db.execute("DELETE FROM attendance_records WHERE session_id = %s", (session["id"],))


class TestStartDateChange:
    def test_start_date_change_is_detected_and_shows_old_new_and_cohort(
        self, db, bud_row_factory, baseline_factory, learner_factory, cohort_factory, tutor_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(uln="ULN-SD-1", tutor_id=tutor["tutorId"], cohort_id=cohort["id"], start_date="2026-01-01")
        baseline_factory()
        row = bud_row_factory(unique_learner_number="ULN-SD-1", start_date=_tomorrow(), synced_at="2099-01-01T00:00:00Z")

        item = classify_row(db, row, _active_baseline(db))
        assert item["action_type"] == "change_start_date"
        assert item["proposed_values"]["startDateChange"]["oldStartDate"] == "2026-01-01"
        assert item["proposed_values"]["startDateChange"]["currentCohortId"] == cohort["id"]

    def test_approved_start_date_change_never_rewrites_historical_attendance(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
        cohort_factory, tutor_factory, attendance_session_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(uln="ULN-SD-2", tutor_id=tutor["tutorId"], cohort_id=cohort["id"], start_date="2026-01-01")
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"], session_date="2026-01-05")
        db.execute(
            "INSERT INTO attendance_records (session_id, learner_id, status, hours_attended, minutes_late, created_by) "
            "VALUES (%s, %s, 'present', 7, 0, %s)",
            (session["id"], learner["id"], admin_user["userId"]),
        )

        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-SD-2", start_date=_tomorrow(), synced_at="2099-01-01T00:00:00Z")

        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]),
        )
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "start date change approved", None, request_factory(admin_user), admin_user)

        db.execute("SELECT start_date FROM learners WHERE id = %s", (learner["id"],))
        assert str(db.fetchone()["start_date"]) == _tomorrow()

        db.execute(
            "SELECT status FROM attendance_records WHERE session_id = %s AND learner_id = %s", (session["id"], learner["id"]),
        )
        assert db.fetchone()["status"] == "present"

        db.execute("SELECT cohort_id FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["cohort_id"] == cohort["id"]  # no automatic cohort re-grouping this trial

        db.execute("DELETE FROM attendance_records WHERE session_id = %s", (session["id"],))


class TestStatusChangeApplication:
    """The core fix this refinement makes: a matched learner's Bud status
    change is actually detected and (where safe) applied -- not just a
    passive warning. Automatic transitions (active<->paused) apply without
    an effective date; withdrawn/completed always require the
    Administrator to supply one, never invented from synced_at."""

    def _link_learner(self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory, uln, status_desc="In Progress"):
        learner = learner_factory(uln=uln, status="active")
        baseline_factory()
        bud_row_factory(unique_learner_number=uln, synced_at="2099-01-01T00:00:00Z", status_desc=status_desc)
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)
        return learner

    def test_an_automatic_transition_is_applied_and_updates_the_snapshot(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = self._link_learner(db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory, "ULN-SC-1")

        db.execute("UPDATE public.learner_progress SET status_desc = 'On Break', synced_at = '2099-06-01T00:00:00Z' WHERE unique_learner_number = 'ULN-SC-1'")

        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            'SELECT id, match_status, approved, proposed_values AS "proposedValues" FROM bud_sync_item '
            "WHERE sync_job_id = %s AND internal_learner_id = %s", (job2["id"], learner["id"]),
        )
        item = db.fetchone()
        assert item["match_status"] == "status_change"
        assert item["proposedValues"]["statusChange"]["kind"] == "automatic"
        assert item["approved"] is True  # preview auto-preselects a fully safe transition

        run_commit(db, job2["id"], [item["id"]], "apply On Break", None, request_factory(admin_user), admin_user)

        db.execute("SELECT status FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["status"] == "paused"

        db.execute("SELECT accepted_values AS \"acceptedValues\" FROM bud_learner_link WHERE internal_learner_id = %s", (learner["id"],))
        assert db.fetchone()["acceptedValues"]["statusDesc"] == "On Break"

        db.execute("SELECT count(*)::int AS c FROM audit_logs WHERE action = 'bud_status_change_applied' AND entity_id = %s", (learner["id"],))
        assert db.fetchone()["c"] == 1

    def test_a_needs_date_transition_blocks_approval_until_the_date_is_supplied(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = self._link_learner(db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory, "ULN-SC-2")

        db.execute("UPDATE public.learner_progress SET status_desc = 'Withdrawn', synced_at = '2099-06-01T00:00:00Z' WHERE unique_learner_number = 'ULN-SC-2'")

        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            'SELECT id, approved FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s', (job2["id"], learner["id"]),
        )
        item = db.fetchone()
        assert item["approved"] is False  # never auto-preselected without a date

        with pytest.raises(HTTPException) as exc_info:
            update_item(db, job2["id"], item["id"], None, True)
        assert exc_info.value.status_code == 400

        updated = update_item(db, job2["id"], item["id"], {"statusChange.effectiveDate": "2026-08-01"}, True)
        assert updated["approved"] is True

        run_commit(db, job2["id"], [item["id"]], "apply withdrawal", None, request_factory(admin_user), admin_user)

        db.execute("SELECT status, withdrawal_date AS \"withdrawalDate\" FROM learners WHERE id = %s", (learner["id"],))
        row = db.fetchone()
        assert row["status"] == "withdrawn"
        assert str(row["withdrawalDate"]) == "2026-08-01"

    def test_a_status_change_never_touches_historical_attendance(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
        cohort_factory, tutor_factory, attendance_session_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(uln="ULN-SC-3", status="active", tutor_id=tutor["tutorId"], cohort_id=cohort["id"])
        session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"], session_date="2026-01-05")
        db.execute(
            "INSERT INTO attendance_records (session_id, learner_id, status, hours_attended, minutes_late, created_by) "
            "VALUES (%s, %s, 'present', 7, 0, %s)",
            (session["id"], learner["id"], admin_user["userId"]),
        )

        baseline_factory()
        bud_row_factory(unique_learner_number="ULN-SC-3", synced_at="2099-01-01T00:00:00Z", status_desc="In Progress")
        job = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job["id"], learner["id"]))
        item_id = db.fetchone()["id"]
        update_item(db, job["id"], item_id, None, True)
        run_commit(db, job["id"], [item_id], "establish link", None, request_factory(admin_user), admin_user)

        db.execute("UPDATE public.learner_progress SET status_desc = 'On Break', synced_at = '2099-06-01T00:00:00Z' WHERE unique_learner_number = 'ULN-SC-3'")
        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute("SELECT id FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job2["id"], learner["id"]))
        item_id2 = db.fetchone()["id"]
        run_commit(db, job2["id"], [item_id2], "apply On Break", None, request_factory(admin_user), admin_user)

        db.execute(
            "SELECT status, hours_attended AS \"hoursAttended\" FROM attendance_records WHERE session_id = %s AND learner_id = %s",
            (session["id"], learner["id"]),
        )
        row = db.fetchone()
        assert row["status"] == "present"
        assert float(row["hoursAttended"]) == 7.0

        db.execute("DELETE FROM attendance_records WHERE session_id = %s", (session["id"],))

    def test_an_unrecognised_bud_status_is_a_conflict(
        self, db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory,
    ):
        learner = self._link_learner(db, admin_user, request_factory, bud_row_factory, baseline_factory, learner_factory, "ULN-SC-4")

        db.execute("UPDATE public.learner_progress SET status_desc = 'Some Future Bud Status', synced_at = '2099-06-01T00:00:00Z' WHERE unique_learner_number = 'ULN-SC-4'")

        job2 = run_preview(db, request_factory(admin_user), admin_user)
        db.execute(
            "SELECT match_status FROM bud_sync_item WHERE sync_job_id = %s AND internal_learner_id = %s", (job2["id"], learner["id"]),
        )
        assert db.fetchone()["match_status"] == "conflict"


def _active_baseline(db):
    from pyapp.bud_sync_lib import get_active_baseline
    return get_active_baseline(db)
