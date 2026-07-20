"""Tests for the Phase 9 secure CSV export path (pyapp/report_csv.py) as
exercised through the /reports/*/export HTTP endpoints: formula-injection
sanitisation, streaming in bounded batches, the row-limit-exceeded 400 (no
silent truncation), export audit logging (including rejected-over-limit
attempts), and export-endpoint permission checks that hold independently
of the matching on-screen endpoint."""
import csv
import io
import json
from datetime import date

from fastapi import Request

import pyapp.report_csv as report_csv_module
from pyapp import auth as auth_module
from pyapp.session_register_lib import ensure_expected_learners_snapshot


# Captured once at import time, before any test monkeypatches
# auth_module.require_auth -- the exact object every router's
# Depends(require_auth) captured at its own route-registration time, and
# so the only valid dependency_overrides key (see test_reports.py for the
# full explanation of why both mechanisms are needed).
_REAL_REQUIRE_AUTH = auth_module.require_auth


def _fake_session_dependency(session, user_id):
    # request MUST be annotated as Request -- see test_reports.py's copy of
    # this helper for why an unannotated param breaks dependency_overrides.
    def fake_require_auth(request: Request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    return fake_require_auth


def _as_tutor(client, monkeypatch, tutor_id, user_id=1):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}
    fake_require_auth = _fake_session_dependency(session, user_id)
    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)
    client.app.dependency_overrides[_REAL_REQUIRE_AUTH] = fake_require_auth
    return session


def _as_admin(client, monkeypatch, user_id=1):
    session = {"userId": user_id, "role": "admin", "tutorId": None}
    fake_require_auth = _fake_session_dependency(session, user_id)
    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)
    client.app.dependency_overrides[_REAL_REQUIRE_AUTH] = fake_require_auth
    return session


def _record(db, session_id, learner_id, status, hours_attended=0, minutes_late=0):
    db.execute(
        """
        INSERT INTO attendance_records (session_id, learner_id, status, hours_attended, minutes_late)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (session_id, learner_id) DO UPDATE SET status = EXCLUDED.status,
            hours_attended = EXCLUDED.hours_attended, minutes_late = EXCLUDED.minutes_late
        """,
        (session_id, learner_id, status, hours_attended, minutes_late),
    )


def _snapshot(db, session: dict):
    ensure_expected_learners_snapshot(db, session["id"], session["cohort_id"], date.fromisoformat(session["session_date"]))


def _latest_audit_row(db, correlation_id):
    """new_value is stored as plain text (json.dumps output), not a jsonb
    column -- cast inline to filter on it, then hand back the row with
    new_value already parsed into a dict for easy assertions."""
    db.execute(
        "SELECT * FROM audit_logs WHERE (new_value::jsonb)->>'correlationId' = %s ORDER BY id DESC LIMIT 1",
        (correlation_id,),
    )
    row = db.fetchone()
    if row is not None and row["new_value"] is not None:
        row = {**row, "new_value": json.loads(row["new_value"])}
    return row


class TestFormulaInjectionProtection:
    def test_learner_name_starting_with_equals_is_prefixed(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"], first_name="=cmd|'/C calc'!A0", last_name="Attack")
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _record(db, session_row["id"], learner["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        response = client.get(
            f"/api/reports/absence/export?absenceType=unauthorised&period=custom&dateFrom=2026-01-01&dateTo=2026-01-31&cohortId={cohort['id']}"
        )
        assert response.status_code == 200
        rows = list(csv.reader(io.StringIO(response.text)))
        header, data_rows = rows[0], rows[1:]
        name_col = header.index("learnerName")
        assert any(r[name_col].startswith("'=") for r in data_rows)
        # The raw formula-trigger character must never appear as the first
        # character of any exported cell -- that's what Excel/Sheets treats
        # as a formula.
        assert all(not r[name_col].startswith(("=", "+", "-", "@")) for r in data_rows)


class TestRowLimitEnforcement:
    def test_export_exceeding_the_limit_is_rejected_not_truncated(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        monkeypatch.setattr(report_csv_module, "MAX_EXPORT_ROWS", 1)
        cohort = cohort_factory()
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        for _ in range(2):
            learner = learner_factory(cohort_id=cohort["id"])
            _record(db, session_row["id"], learner["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        response = client.get(
            f"/api/reports/absence/export?absenceType=unauthorised&period=custom&dateFrom=2026-01-01&dateTo=2026-01-31&cohortId={cohort['id']}"
        )
        assert response.status_code == 400
        assert "narrow your filters" in response.json()["error"]

    def test_rejected_over_limit_export_is_still_audited(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        monkeypatch.setattr(report_csv_module, "MAX_EXPORT_ROWS", 1)
        cohort = cohort_factory()
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        for _ in range(2):
            learner = learner_factory(cohort_id=cohort["id"])
            _record(db, session_row["id"], learner["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        response = client.get(
            f"/api/reports/absence/export?absenceType=unauthorised&period=custom&dateFrom=2026-01-01&dateTo=2026-01-31&cohortId={cohort['id']}"
        )
        assert response.status_code == 400
        correlation_id = response.headers["X-Correlation-Id"]
        audit_row = _latest_audit_row(db, correlation_id)
        assert audit_row is not None
        assert audit_row["new_value"]["outcome"] == "rejected_over_limit"
        assert audit_row["new_value"]["rowCount"] == 2


class TestExportAuditLogging:
    def test_successful_export_writes_exactly_one_audit_row_with_correlation_id(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _record(db, session_row["id"], learner["id"], "absent_unauthorised")

        session = _as_admin(client, monkeypatch)
        response = client.get(
            f"/api/reports/absence/export?absenceType=unauthorised&period=custom&dateFrom=2026-01-01&dateTo=2026-01-31&cohortId={cohort['id']}"
        )
        assert response.status_code == 200
        correlation_id = response.headers["X-Correlation-Id"]

        db.execute("SELECT count(*) AS c FROM audit_logs WHERE (new_value::jsonb)->>'correlationId' = %s", (correlation_id,))
        assert db.fetchone()["c"] == 1

        audit_row = _latest_audit_row(db, correlation_id)
        assert audit_row["action"] == "export_report"
        assert audit_row["entity_type"] == "report_export"
        assert audit_row["user_id"] == session["userId"]
        assert audit_row["new_value"]["reportType"] == "absence"
        assert audit_row["new_value"]["rowCount"] == 1
        assert audit_row["new_value"]["outcome"] == "completed"
        # The exported CSV content itself must never be persisted to the audit log.
        assert "learnerName" not in str(audit_row["new_value"])

    def test_bounded_export_via_export_csv_response_is_also_audited(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", planned_duration_hours=6, created_by=admin_user["userId"])
        _snapshot(db, session_row)
        _record(db, session_row["id"], learner["id"], "present", hours_attended=6)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/learner/{learner['id']}/export?period=custom&dateFrom=2026-01-01&dateTo=2026-01-31")
        assert response.status_code == 200
        correlation_id = response.headers["X-Correlation-Id"]
        audit_row = _latest_audit_row(db, correlation_id)
        assert audit_row["new_value"]["reportType"] == "learner"
        assert audit_row["new_value"]["outcome"] == "completed"


class TestStreamingCorrectness:
    def test_multi_batch_stream_emits_every_row_exactly_once(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        monkeypatch.setattr(report_csv_module, "EXPORT_BATCH_SIZE", 2)
        cohort = cohort_factory()
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        learner_refs = []
        for _ in range(5):
            learner = learner_factory(cohort_id=cohort["id"])
            _record(db, session_row["id"], learner["id"], "absent_unauthorised")
            learner_refs.append(learner["learner_ref"])

        _as_admin(client, monkeypatch)
        response = client.get(
            f"/api/reports/absence/export?absenceType=unauthorised&period=custom&dateFrom=2026-01-01&dateTo=2026-01-31&cohortId={cohort['id']}"
        )
        assert response.status_code == 200
        rows = list(csv.reader(io.StringIO(response.text)))
        header, data_rows = rows[0], rows[1:]
        assert len(data_rows) == 5
        exported_refs = {r[header.index("learnerRef")] for r in data_rows}
        assert exported_refs == set(learner_refs)


class TestExportPermissionsAreIndependentOfTheOnScreenEndpoint:
    def test_tutor_cannot_export_another_tutors_cohort_absence_report(
        self, client, monkeypatch, tutor_factory, cohort_factory
    ):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        _as_tutor(client, monkeypatch, other["tutorId"])
        response = client.get(f"/api/reports/absence/export?absenceType=unauthorised&cohortId={cohort['id']}")
        assert response.status_code == 403

    def test_tutor_cannot_export_another_tutors_learner_report(
        self, client, monkeypatch, tutor_factory, learner_factory
    ):
        owner = tutor_factory()
        other = tutor_factory()
        learner = learner_factory(tutor_id=owner["tutorId"])
        _as_tutor(client, monkeypatch, other["tutorId"])
        response = client.get(f"/api/reports/learner/{learner['id']}/export")
        assert response.status_code == 403

    def test_tutor_cannot_widen_scope_by_supplying_someone_elses_cohort_alongside_their_own_tutor_id(
        self, client, monkeypatch, tutor_factory, cohort_factory
    ):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        _as_tutor(client, monkeypatch, other["tutorId"])
        response = client.get(
            f"/api/reports/register-completion/export?tutorId={other['tutorId']}&cohortId={cohort['id']}"
        )
        assert response.status_code == 403

    def test_allocation_history_export_is_admin_only(self, client, monkeypatch, tutor_factory):
        tutor = tutor_factory()
        _as_tutor(client, monkeypatch, tutor["tutorId"])
        response = client.get("/api/reports/allocation-history/export")
        assert response.status_code == 403

    def test_repeated_exports_never_mutate_application_data(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _record(db, session_row["id"], learner["id"], "absent_unauthorised")

        db.execute("SELECT status, hours_attended, minutes_late FROM attendance_records WHERE session_id = %s AND learner_id = %s", (session_row["id"], learner["id"]))
        before = dict(db.fetchone())

        _as_admin(client, monkeypatch)
        for _ in range(3):
            resp = client.get(
                f"/api/reports/absence/export?absenceType=unauthorised&period=custom&dateFrom=2026-01-01&dateTo=2026-01-31&cohortId={cohort['id']}"
            )
            assert resp.status_code == 200

        db.execute("SELECT status, hours_attended, minutes_late FROM attendance_records WHERE session_id = %s AND learner_id = %s", (session_row["id"], learner["id"]))
        after = dict(db.fetchone())
        assert before == after


class TestCsvResponseShape:
    def test_content_type_and_no_hidden_columns(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        _record(db, session_row["id"], learner["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        response = client.get(
            f"/api/reports/absence/export?absenceType=unauthorised&period=custom&dateFrom=2026-01-01&dateTo=2026-01-31&cohortId={cohort['id']}"
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        header = next(csv.reader(io.StringIO(response.text)))
        # No raw SQLAlchemy/psycopg repr, no Entra identifiers, no notes column.
        assert "notes" not in header
        assert "entraId" not in header
        assert all(not col.startswith("_") for col in header)
