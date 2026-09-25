"""Stage 5: admin-uploaded Functional Skills subject requirement -- covers
the acceptance checks the feature spec calls out explicitly (item 7):
Math/English/Both, whitespace/case/BOM handling, unknown learner
reference, identical vs conflicting duplicate rows, Both->Math and other
replacements, omitted learners unchanged, blank AIM rejected, clear
retains history, inactive-learner warning without reactivation, unchanged
re-upload, stale preview, repeated commit, transaction rollback, and
subject-coverage/tutor-issue detection for the allocation view."""
from datetime import date

import pytest
from fastapi import HTTPException
from psycopg.rows import dict_row

from pyapp import audit as audit_module
from pyapp.csv_utils import CsvParseError, parse_fs_requirement_import_csv
from pyapp.db import pool
from pyapp.fs_requirement_lib import (
    cancel_import_job,
    classify_rows,
    clear_requirement,
    confirm_import_job,
    create_import_job,
    fetch_allocation_rows,
    get_import_job,
    get_requirement,
)


def _csv(rows: list[tuple[str, str]]) -> bytes:
    lines = ["learnerID,AIM"] + [f"{learner_id},{aim}" for learner_id, aim in rows]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


class TestAimNormalisationAndCsvParsing:
    def test_math_english_and_both_are_accepted_case_insensitively(self):
        rows = parse_fs_requirement_import_csv(_csv([("A1", "math"), ("A2", "ENGLISH"), ("A3", "Both")]))
        assert [r["AIM"] for r in rows] == ["math", "ENGLISH", "Both"]

    def test_utf8_bom_and_whitespace_are_handled(self):
        raw = b"\xef\xbb\xbflearnerID,AIM\r\n  A1  , Math \r\n"
        rows = parse_fs_requirement_import_csv(raw)
        assert rows == [{"learnerID": "A1", "AIM": "Math"}]

    def test_quoted_fields_are_handled(self):
        raw = b'learnerID,AIM\r\n"A,1",Math\r\n'
        rows = parse_fs_requirement_import_csv(raw)
        assert rows == [{"learnerID": "A,1", "AIM": "Math"}]

    def test_wrong_headers_are_rejected(self):
        with pytest.raises(CsvParseError):
            parse_fs_requirement_import_csv(b"learner_id,subject\r\nA1,Math\r\n")


class TestRowClassification:
    def test_math_english_both_are_classified_new_with_correct_flags(self, db, learner_factory):
        a = learner_factory()
        b = learner_factory()
        c = learner_factory()
        results = classify_rows(db, [
            {"learnerID": a["learner_ref"], "AIM": "Math"},
            {"learnerID": b["learner_ref"], "AIM": "English"},
            {"learnerID": c["learner_ref"], "AIM": "Both"},
        ])
        assert [r["outcome"] for r in results] == ["new", "new", "new"]
        assert (results[0]["proposedMaths"], results[0]["proposedEnglish"]) == (True, False)
        assert (results[1]["proposedMaths"], results[1]["proposedEnglish"]) == (False, True)
        assert (results[2]["proposedMaths"], results[2]["proposedEnglish"]) == (True, True)

    def test_unknown_learner_reference_is_an_error(self, db):
        results = classify_rows(db, [{"learnerID": "DOES-NOT-EXIST", "AIM": "Math"}])
        assert results[0]["outcome"] == "error"
        assert results[0]["matchedLearnerId"] is None
        assert any("does not match" in e for e in results[0]["errors"])

    def test_blank_aim_is_rejected_not_treated_as_a_deletion(self, db, learner_factory):
        learner = learner_factory()
        results = classify_rows(db, [{"learnerID": learner["learner_ref"], "AIM": ""}])
        assert results[0]["outcome"] == "error"
        assert any("required" in e for e in results[0]["errors"])

    def test_unrecognised_aim_value_is_rejected(self, db, learner_factory):
        learner = learner_factory()
        results = classify_rows(db, [{"learnerID": learner["learner_ref"], "AIM": "Maths"}])
        assert results[0]["outcome"] == "error"
        assert any("not recognised" in e for e in results[0]["errors"])

    def test_inactive_learner_gets_a_warning_but_still_a_normal_outcome(self, db, learner_factory):
        learner = learner_factory(status="withdrawn")
        results = classify_rows(db, [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        assert results[0]["outcome"] == "new"
        assert any("withdrawn" in w and "does not reactivate" in w for w in results[0]["warnings"])

    def test_identical_duplicate_rows_collapse_into_one_update_with_a_warning(self, db, learner_factory):
        learner = learner_factory()
        results = classify_rows(db, [
            {"learnerID": learner["learner_ref"], "AIM": "Math"},
            {"learnerID": learner["learner_ref"], "AIM": "math"},
        ])
        assert results[0]["outcome"] == "new"
        assert results[1]["outcome"] == "warning"
        assert any("Duplicate of row 1" in w for w in results[1]["warnings"])

    def test_conflicting_duplicate_rows_block_every_occurrence(self, db, learner_factory):
        learner = learner_factory()
        results = classify_rows(db, [
            {"learnerID": learner["learner_ref"], "AIM": "Math"},
            {"learnerID": learner["learner_ref"], "AIM": "English"},
        ])
        assert results[0]["outcome"] == "error"
        assert results[1]["outcome"] == "error"
        assert any("conflicting" in e for e in results[0]["errors"])

    def test_does_not_infer_both_from_separate_math_and_english_rows(self, db, learner_factory):
        """Explicit item-4 requirement: two separate rows for the same
        learner, one Math one English, must be a CONFLICT error -- never
        silently combined into an implied "Both"."""
        learner = learner_factory()
        results = classify_rows(db, [
            {"learnerID": learner["learner_ref"], "AIM": "Math"},
            {"learnerID": learner["learner_ref"], "AIM": "English"},
        ])
        assert all(r["outcome"] == "error" for r in results)


class TestOutcomeAgainstExistingRequirement:
    def _seed(self, db, admin_user, request_factory, learner_id, maths, english):
        cur = db
        cur.execute(
            "INSERT INTO learner_fs_requirements (learner_id, maths, english, status, updated_by) VALUES (%s,%s,%s,'recorded',%s)",
            (learner_id, maths, english, admin_user["userId"]),
        )

    def test_unchanged_value_is_a_no_op_outcome(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        self._seed(db, admin_user, request_factory, learner["id"], True, False)
        results = classify_rows(db, [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        assert results[0]["outcome"] == "unchanged"

    def test_both_to_math_removes_the_english_requirement(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        self._seed(db, admin_user, request_factory, learner["id"], True, True)
        results = classify_rows(db, [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        assert results[0]["outcome"] == "changed"
        assert (results[0]["proposedMaths"], results[0]["proposedEnglish"]) == (True, False)

    def test_math_to_english_replaces_rather_than_adds(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        self._seed(db, admin_user, request_factory, learner["id"], True, False)
        results = classify_rows(db, [{"learnerID": learner["learner_ref"], "AIM": "English"}])
        assert results[0]["outcome"] == "changed"
        assert (results[0]["proposedMaths"], results[0]["proposedEnglish"]) == (False, True)

    def test_re_recording_after_a_clear_is_changed_not_unchanged(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        cur = db
        cur.execute(
            "INSERT INTO learner_fs_requirements (learner_id, maths, english, status, updated_by) VALUES (%s, true, false, 'cleared', %s)",
            (learner["id"], admin_user["userId"]),
        )
        results = classify_rows(db, [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        assert results[0]["outcome"] == "changed"


class TestCommit:
    def test_new_and_changed_rows_are_applied_omitted_learners_stay_unchanged(
        self, db, admin_user, learner_factory, request_factory,
    ):
        learner_a = learner_factory()
        learner_b = learner_factory()
        untouched = learner_factory()

        job = create_import_job(db, "f.csv", admin_user["userId"], [
            {"learnerID": learner_a["learner_ref"], "AIM": "Math"},
            {"learnerID": learner_b["learner_ref"], "AIM": "Both"},
        ])
        summary = confirm_import_job(db, job["id"], request_factory(), admin_user)
        assert summary["applied"] == 2

        req_a = get_requirement(db, learner_a["id"])
        req_b = get_requirement(db, learner_b["id"])
        assert (req_a["maths"], req_a["english"], req_a["status"]) == (True, False, "recorded")
        assert (req_b["maths"], req_b["english"], req_b["status"]) == (True, True, "recorded")
        assert get_requirement(db, untouched["id"]) is None

    def test_commit_is_blocked_while_any_row_has_an_error(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        job = create_import_job(db, "f.csv", admin_user["userId"], [
            {"learnerID": learner["learner_ref"], "AIM": "Math"},
            {"learnerID": "UNKNOWN-REF", "AIM": "Math"},
        ])
        with pytest.raises(HTTPException) as exc:
            confirm_import_job(db, job["id"], request_factory(), admin_user)
        assert exc.value.status_code == 400
        assert get_requirement(db, learner["id"]) is None, "nothing should be applied while the file has errors"

    def test_unchanged_re_upload_is_a_no_op(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        job1 = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        confirm_import_job(db, job1["id"], request_factory(), admin_user)
        before = get_requirement(db, learner["id"])

        job2 = create_import_job(db, "f2.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        summary = confirm_import_job(db, job2["id"], request_factory(), admin_user)
        assert summary["applied"] == 0
        after = get_requirement(db, learner["id"])
        assert before["updatedAt"] == after["updatedAt"]

    def test_stale_preview_is_rejected(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Math"}])

        # The learner's requirement changes after preview but before confirm.
        other_job = create_import_job(db, "other.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "English"}])
        confirm_import_job(db, other_job["id"], request_factory(), admin_user)

        with pytest.raises(HTTPException) as exc:
            confirm_import_job(db, job["id"], request_factory(), admin_user)
        assert exc.value.status_code == 409

    def test_stale_preview_is_rejected_when_the_matched_learners_status_changes(
        self, db, admin_user, learner_factory, request_factory,
    ):
        """The preview page reads matched-learner status LIVE, so an admin
        re-viewing an unconfirmed job always sees the learner's current
        status -- but the row's own warning text is only ever generated
        once, at classification time. If status drifts between preview and
        confirm, that frozen warning would no longer match reality (or a
        warning that should now apply would be silently missing), so this
        must be rejected as stale exactly like a deleted learner or a
        changed requirement -- never silently applied."""
        learner = learner_factory()
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Math"}])

        db.execute("UPDATE learners SET status = 'withdrawn' WHERE id = %s", (learner["id"],))

        with pytest.raises(HTTPException) as exc:
            confirm_import_job(db, job["id"], request_factory(), admin_user)
        assert exc.value.status_code == 409
        assert get_requirement(db, learner["id"]) is None, "nothing should be applied while the preview is stale"

    def test_stale_preview_is_rejected_when_the_matched_learner_is_deleted(
        self, db, admin_user, learner_factory, request_factory,
    ):
        learner = learner_factory()
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Math"}])

        db.execute("UPDATE learners SET deleted_at = now() WHERE id = %s", (learner["id"],))

        with pytest.raises(HTTPException) as exc:
            confirm_import_job(db, job["id"], request_factory(), admin_user)
        assert exc.value.status_code == 409
        assert get_requirement(db, learner["id"]) is None

    def test_repeated_commit_does_not_duplicate_changes_or_audit_events(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        first = confirm_import_job(db, job["id"], request_factory(), admin_user)
        second = confirm_import_job(db, job["id"], request_factory(), admin_user)
        assert first == second

        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM audit_logs WHERE action = 'fs_requirement_uploaded' AND entity_type = 'learner_fs_requirement' "
                    "AND entity_id = (SELECT id FROM learner_fs_requirements WHERE learner_id = %s)",
                    (learner["id"],),
                )
                assert cur.fetchone()["n"] == 1

    def test_transaction_rolls_back_if_an_audit_write_fails_mid_batch(
        self, db, admin_user, learner_factory, request_factory, monkeypatch,
    ):
        learner_a = learner_factory()
        learner_b = learner_factory()
        job = create_import_job(db, "f.csv", admin_user["userId"], [
            {"learnerID": learner_a["learner_ref"], "AIM": "Math"},
            {"learnerID": learner_b["learner_ref"], "AIM": "English"},
        ])

        calls = {"n": 0}
        real_write = audit_module.write_audit_log

        def _boom(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure mid-batch")
            return real_write(*args, **kwargs)

        monkeypatch.setattr("pyapp.fs_requirement_lib.write_audit_log", _boom)

        with pytest.raises(RuntimeError):
            confirm_import_job(db, job["id"], request_factory(), admin_user)

        assert get_requirement(db, learner_a["id"]) is None, "the first row's insert must roll back too"
        assert get_requirement(db, learner_b["id"]) is None
        assert get_import_job(db, job["id"])["status"] == "ready"

    def test_cancel_a_ready_job(self, db, admin_user, learner_factory):
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner_factory()["learner_ref"], "AIM": "Math"}])
        cancelled = cancel_import_job(db, job["id"])
        assert cancelled["status"] == "cancelled"


class TestClearRequirement:
    def test_clear_requires_a_reason_and_retains_history(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Both"}])
        confirm_import_job(db, job["id"], request_factory(), admin_user)

        with pytest.raises(HTTPException):
            clear_requirement(db, learner["id"], "", request_factory(), admin_user)

        cleared = clear_requirement(db, learner["id"], "Learner confirmed no longer needs FS", request_factory(), admin_user)
        assert (cleared["maths"], cleared["english"], cleared["status"]) == (False, False, "cleared")

        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT action FROM audit_logs WHERE entity_type = 'learner_fs_requirement' AND entity_id = %s ORDER BY timestamp",
                    (cleared["id"],),
                )
                actions = [r["action"] for r in cur.fetchall()]
        assert actions == ["fs_requirement_uploaded", "fs_requirement_cleared"]

    def test_no_requirement_data_uploaded_vs_cleared_are_distinguishable(self, db, admin_user, learner_factory, request_factory):
        never_uploaded = learner_factory()
        assert get_requirement(db, never_uploaded["id"]) is None

        cleared_learner = learner_factory()
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": cleared_learner["learner_ref"], "AIM": "Math"}])
        confirm_import_job(db, job["id"], request_factory(), admin_user)
        clear_requirement(db, cleared_learner["id"], "no longer required", request_factory(), admin_user)

        assert get_requirement(db, cleared_learner["id"])["status"] == "cleared"


class TestAllocationView:
    def test_subject_coverage_through_a_single_both_cohort(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory, request_factory,
    ):
        tutor = tutor_factory()
        both_cohort = cohort_factory(tutor_id=tutor["tutorId"], membership_type="secondary", subject="both")
        learner = learner_factory()
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=both_cohort["id"], enrolled_date="2026-01-01")
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Both"}])
        confirm_import_job(db, job["id"], request_factory(), admin_user)

        rows, total = fetch_allocation_rows(db, search=learner["learner_ref"], subject=None, missing_only=False, status=None, page=1, page_size=25)
        assert total == 1
        assert rows[0]["missingSubjects"] == []

    def test_subject_coverage_through_two_separate_cohorts(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory, request_factory,
    ):
        maths_tutor = tutor_factory()
        english_tutor = tutor_factory()
        maths_cohort = cohort_factory(tutor_id=maths_tutor["tutorId"], membership_type="secondary", subject="math")
        english_cohort = cohort_factory(tutor_id=english_tutor["tutorId"], membership_type="secondary", subject="english")
        learner = learner_factory()
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=maths_cohort["id"], enrolled_date="2026-01-01")
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=english_cohort["id"], enrolled_date="2026-01-01")
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Both"}])
        confirm_import_job(db, job["id"], request_factory(), admin_user)

        rows, total = fetch_allocation_rows(db, search=learner["learner_ref"], subject=None, missing_only=False, status=None, page=1, page_size=25)
        assert total == 1
        assert rows[0]["missingSubjects"] == []

    def test_missing_subject_allocation_is_flagged(self, db, admin_user, learner_factory, request_factory):
        learner = learner_factory()
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Both"}])
        confirm_import_job(db, job["id"], request_factory(), admin_user)

        rows, total = fetch_allocation_rows(db, search=learner["learner_ref"], subject=None, missing_only=True, status=None, page=1, page_size=25)
        assert total == 1
        assert sorted(rows[0]["missingSubjects"]) == ["english", "math"]

    def test_inactive_tutor_is_flagged_separately_from_missing_coverage(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory, request_factory,
    ):
        tutor = tutor_factory(active=False)
        cohort = cohort_factory(tutor_id=tutor["tutorId"], membership_type="secondary", subject="math")
        learner = learner_factory()
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=cohort["id"], enrolled_date="2026-01-01")
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        confirm_import_job(db, job["id"], request_factory(), admin_user)

        rows, total = fetch_allocation_rows(db, search=learner["learner_ref"], subject=None, missing_only=False, status=None, page=1, page_size=25)
        assert total == 1
        assert "math" in rows[0]["missingSubjects"], "an inactive tutor's cohort must not count as real coverage"
        assert "math" in rows[0]["missingTutorSubjects"]

    def test_deleted_cohort_does_not_count_as_coverage(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], membership_type="secondary", subject="math")
        learner = learner_factory()
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=cohort["id"], enrolled_date="2026-01-01")
        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        confirm_import_job(db, job["id"], request_factory(), admin_user)
        db.execute("UPDATE cohorts SET deleted_at = now() WHERE id = %s", (cohort["id"],))

        rows, total = fetch_allocation_rows(db, search=learner["learner_ref"], subject=None, missing_only=False, status=None, page=1, page_size=25)
        assert total == 1
        assert "math" in rows[0]["missingSubjects"]

    def test_existing_secondary_membership_without_requirement_is_labelled_not_recorded(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], membership_type="secondary", subject="math")
        learner = learner_factory()
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=cohort["id"], enrolled_date="2026-01-01")

        rows, total = fetch_allocation_rows(db, search=learner["learner_ref"], subject=None, missing_only=False, status=None, page=1, page_size=25)
        assert total == 1
        assert rows[0]["requirementRecorded"] is False

    def test_default_population_excludes_inactive_learners_but_they_can_be_inspected_explicitly(
        self, db, admin_user, learner_factory, request_factory,
    ):
        active_learner = learner_factory()
        withdrawn_learner = learner_factory(status="withdrawn")
        job = create_import_job(db, "f.csv", admin_user["userId"], [
            {"learnerID": active_learner["learner_ref"], "AIM": "Math"},
            {"learnerID": withdrawn_learner["learner_ref"], "AIM": "Math"},
        ])
        confirm_import_job(db, job["id"], request_factory(), admin_user)

        default_rows, _ = fetch_allocation_rows(db, search=None, subject=None, missing_only=False, status=None, page=1, page_size=200)
        assert withdrawn_learner["id"] not in {r["id"] for r in default_rows}

        withdrawn_rows, total = fetch_allocation_rows(db, search=withdrawn_learner["learner_ref"], subject=None, missing_only=False, status="withdrawn", page=1, page_size=25)
        assert total == 1
        assert withdrawn_rows[0]["id"] == withdrawn_learner["id"]

    def test_counts_match_the_full_filtered_population_across_pages(
        self, db, admin_user, learner_factory, request_factory,
    ):
        learners = [learner_factory() for _ in range(5)]
        rows_payload = [{"learnerID": learner["learner_ref"], "AIM": "Math"} for learner in learners]
        job = create_import_job(db, "f.csv", admin_user["userId"], rows_payload)
        confirm_import_job(db, job["id"], request_factory(), admin_user)

        page1, total1 = fetch_allocation_rows(db, search=None, subject="math", missing_only=False, status=None, page=1, page_size=3)
        page2, total2 = fetch_allocation_rows(db, search=None, subject="math", missing_only=False, status=None, page=2, page_size=3)
        assert total1 == total2 >= 5
        seen = {r["id"] for r in page1} | {r["id"] for r in page2}
        assert {l["id"] for l in learners} <= seen


class TestUnrelatedDataUntouched:
    def test_upload_never_touches_cohort_subject_or_secondary_enrollments(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, secondary_enrollment_factory, request_factory,
    ):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"], membership_type="secondary", subject="english")
        learner = learner_factory()
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=cohort["id"], enrolled_date="2026-01-01")

        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "Math"}])
        confirm_import_job(db, job["id"], request_factory(), admin_user)

        db.execute("SELECT subject FROM cohorts WHERE id = %s", (cohort["id"],))
        assert db.fetchone()["subject"] == "english"
        db.execute("SELECT status FROM learner_cohort_enrollments WHERE learner_id = %s AND cohort_id = %s", (learner["id"], cohort["id"]))
        assert db.fetchone()["status"] == "active"

    def test_upload_never_changes_learner_status_tutor_or_cohort(self, db, admin_user, tutor_factory, cohort_factory, learner_factory, request_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(status="paused", cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        job = create_import_job(db, "f.csv", admin_user["userId"], [{"learnerID": learner["learner_ref"], "AIM": "English"}])
        confirm_import_job(db, job["id"], request_factory(), admin_user)

        db.execute("SELECT status, tutor_id, cohort_id FROM learners WHERE id = %s", (learner["id"],))
        row = db.fetchone()
        assert row["status"] == "paused"
        assert row["tutor_id"] == tutor["tutorId"]
        assert row["cohort_id"] == cohort["id"]
