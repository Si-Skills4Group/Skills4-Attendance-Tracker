"""Phase 10 audit-completeness fixes: register generation is now audited,
object-level 403s (not just role-level ones) write authorization_denied,
overrideReason changes show up in the save_register diff, and -- the most
consequential one -- a mutation and its audit row commit or roll back
together, so a failed transaction can never leave a real, unaudited
change behind."""
import pytest
from fastapi import HTTPException

from pyapp import auth as auth_module
from pyapp.routers.attendance import (
    AttendanceRegisterInput,
    RegisterEntryInput,
    generate_session_register,
    save_attendance_register,
)
from pyapp.routers.cohorts import get_cohort


def test_register_generation_is_audited(db, request_factory, admin_user, cohort_factory, attendance_session_factory):
    cohort = cohort_factory()
    session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

    generate_session_register(session["id"], request_factory(), admin_user)

    db.execute(
        "SELECT action FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s "
        "AND action = 'generate_register' ORDER BY id DESC LIMIT 1",
        (session["id"],),
    )
    assert db.fetchone() is not None


def test_override_reason_change_is_captured_in_the_save_register_diff(
    db, request_factory, admin_user, cohort_factory, learner_factory, attendance_session_factory,
):
    cohort = cohort_factory()
    learner = learner_factory(cohort_id=cohort["id"])
    session = attendance_session_factory(cohort_id=cohort["id"], created_by=admin_user["userId"])

    save_attendance_register(
        session["id"],
        AttendanceRegisterInput(registerVersion=1, entries=[
            RegisterEntryInput(learnerId=learner["id"], status="present", hoursAttended=session["planned_duration_hours"] or 7, minutesLate=0),
        ]),
        request_factory(), admin_user,
    )
    save_attendance_register(
        session["id"],
        AttendanceRegisterInput(
            registerVersion=2,
            changeReason="Backdated correction",
            entries=[
                RegisterEntryInput(
                    learnerId=learner["id"], status="present", hoursAttended=8, minutesLate=0,
                    overrideReason="Extra catch-up session approved by admin",
                ),
            ],
        ),
        request_factory(), admin_user,
    )

    db.execute(
        "SELECT new_value FROM audit_logs WHERE entity_type = 'attendance_session' AND entity_id = %s "
        "AND action = 'save_register' ORDER BY id DESC LIMIT 1",
        (session["id"],),
    )
    row = db.fetchone()
    assert "overrideReason" in row["new_value"]
    assert "Extra catch-up session approved by admin" in row["new_value"]


class TestObjectLevel403Auditing:
    def test_tutor_probing_another_tutors_cohort_is_audited(self, db, client, tutor_factory, cohort_factory):
        # deny_object_access reads the in-flight Request from a contextvar
        # populated by CorrelationIdMiddleware (pyapp/correlation.py) --
        # only a real ASGI request through TestClient sets that up, unlike
        # calling get_cohort(...) directly as a plain Python function.
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])

        fake_session = {"userId": 1, "role": "tutor", "tutorId": other["tutorId"]}
        client.app.dependency_overrides[auth_module.require_auth] = lambda: fake_session
        try:
            response = client.get(f"/api/cohorts/{cohort['id']}")
        finally:
            client.app.dependency_overrides.pop(auth_module.require_auth, None)
        assert response.status_code == 403

        db.execute(
            "SELECT new_value FROM audit_logs WHERE action = 'authorization_denied' AND entity_type = 'cohort' "
            "AND entity_id = %s ORDER BY id DESC LIMIT 1",
            (cohort["id"],),
        )
        row = db.fetchone()
        assert row is not None
        assert "not_owner" in row["new_value"]

    def test_admin_bypassing_ownership_is_not_audited_as_a_denial(self, db, admin_user, tutor_factory, cohort_factory):
        """An admin viewing any cohort is a legitimate, everyday action --
        must never be logged as a denial just because the same code path
        also serves the tutor-ownership check."""
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])

        get_cohort(cohort["id"], session=admin_user)

        db.execute(
            "SELECT count(*) AS c FROM audit_logs WHERE action = 'authorization_denied' AND entity_type = 'cohort' "
            "AND entity_id = %s",
            (cohort["id"],),
        )
        assert db.fetchone()["c"] == 0


class TestAuditNeverClaimsFalseSuccess:
    def test_a_forced_rollback_after_the_mutation_leaves_neither_change_nor_audit_row(
        self, db, request_factory, admin_user, cohort_factory, learner_factory,
    ):
        """delete_learner wraps its UPDATE + audit write in one real
        transaction (with cur.connection.transaction()). Simulate a crash
        immediately after the mutation, inside that same transaction --
        the whole thing must roll back together: the learner must NOT
        end up deleted, and no audit row must exist for it."""
        from pyapp.routers.learners import LearnerDeleteInput

        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])

        import pyapp.routers.learners as learners_module

        real_write_audit_log = learners_module.write_audit_log

        def exploding_audit_log(*args, **kwargs):
            # Simulate the process dying (or any error) after the
            # UPDATE has run but before the audit row is written --
            # both are inside the same `with cur.connection.transaction()`
            # block, so raising here must roll the UPDATE back too.
            raise RuntimeError("simulated crash between mutation and audit write")

        learners_module.write_audit_log = exploding_audit_log
        try:
            with pytest.raises(RuntimeError):
                learners_module.delete_learner(
                    learner["id"], LearnerDeleteInput(reason="Testing atomicity"), request_factory(), admin_user,
                )
        finally:
            learners_module.write_audit_log = real_write_audit_log

        db.execute("SELECT deleted_at FROM learners WHERE id = %s", (learner["id"],))
        assert db.fetchone()["deleted_at"] is None, "the delete must have rolled back, not partially applied"

        db.execute(
            "SELECT count(*) AS c FROM audit_logs WHERE action = 'delete_learner' AND entity_id = %s",
            (learner["id"],),
        )
        assert db.fetchone()["c"] == 0, "no audit row should exist for a mutation that rolled back"
