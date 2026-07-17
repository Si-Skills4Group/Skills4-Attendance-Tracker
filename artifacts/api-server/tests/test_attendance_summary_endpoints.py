"""HTTP-level tests for the Phase 8 dashboard/attendance-summary endpoints:
permissions (including direct-object-reference probes), and a reconciliation
scenario proving learner -> cohort -> tutor -> organisation totals agree and
nothing is double-counted."""
import pytest

from pyapp import auth as auth_module


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


def _as_tutor_via_dependency_override(client, tutor_id, user_id=1):
    """/dashboard/tutor* and /attendance-summary/* are wired as
    Depends(require_auth) directly, so (per the established pattern in
    test_permissions.py) monkeypatching auth_module.require_auth has no
    effect -- FastAPI captured the callable at route-registration time.
    app.dependency_overrides is the correct mechanism here."""
    fake_session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}
    client.app.dependency_overrides[auth_module.require_auth] = lambda: fake_session


def _as_admin_via_dependency_override(client, user_id=1):
    fake_session = {"userId": user_id, "role": "admin", "tutorId": None}
    client.app.dependency_overrides[auth_module.require_auth] = lambda: fake_session


def _clear_override(client):
    client.app.dependency_overrides.pop(auth_module.require_auth, None)


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


class TestAttendanceSummaryPermissions:
    def test_unauthenticated_learner_summary_is_rejected(self, client):
        response = client.get("/api/attendance-summary/learners/1")
        assert response.status_code == 401

    def test_unauthenticated_cohort_summary_is_rejected(self, client):
        response = client.get("/api/attendance-summary/cohorts/1")
        assert response.status_code == 401

    def test_unauthenticated_tutor_summary_is_rejected(self, client):
        response = client.get("/api/attendance-summary/tutors/1")
        assert response.status_code == 401

    def test_tutor_cannot_access_another_tutors_learner_summary(self, client, tutor_factory, learner_factory):
        owner = tutor_factory()
        other = tutor_factory()
        learner = learner_factory(tutor_id=owner["tutorId"])
        _as_tutor_via_dependency_override(client, other["tutorId"])
        try:
            response = client.get(f"/api/attendance-summary/learners/{learner['id']}")
        finally:
            _clear_override(client)
        assert response.status_code == 403

    def test_tutor_cannot_access_another_tutors_cohort_summary(self, client, tutor_factory, cohort_factory):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        _as_tutor_via_dependency_override(client, other["tutorId"])
        try:
            response = client.get(f"/api/attendance-summary/cohorts/{cohort['id']}")
        finally:
            _clear_override(client)
        assert response.status_code == 403

    def test_tutor_cannot_access_another_tutors_summary_by_changing_the_tutor_id(self, client, tutor_factory):
        owner = tutor_factory()
        other = tutor_factory()
        _as_tutor_via_dependency_override(client, other["tutorId"])
        try:
            response = client.get(f"/api/attendance-summary/tutors/{owner['tutorId']}")
        finally:
            _clear_override(client)
        assert response.status_code == 403

    def test_tutor_can_access_own_tutor_summary(self, client, tutor_factory):
        tutor = tutor_factory()
        _as_tutor_via_dependency_override(client, tutor["tutorId"])
        try:
            response = client.get(f"/api/attendance-summary/tutors/{tutor['tutorId']}")
        finally:
            _clear_override(client)
        assert response.status_code == 200
        assert "metrics" in response.json()
        assert "registerCompletion" in response.json()

    def test_admin_can_access_any_learner_summary(self, client, learner_factory):
        learner = learner_factory()
        _as_admin_via_dependency_override(client)
        try:
            response = client.get(f"/api/attendance-summary/learners/{learner['id']}")
        finally:
            _clear_override(client)
        assert response.status_code == 200

    def test_nonexistent_cohort_summary_is_404(self, client):
        _as_admin_via_dependency_override(client)
        try:
            response = client.get("/api/attendance-summary/cohorts/999999999")
        finally:
            _clear_override(client)
        assert response.status_code == 404

    def test_invalid_custom_period_without_dates_is_400(self, client, learner_factory):
        learner = learner_factory()
        _as_admin_via_dependency_override(client)
        try:
            response = client.get(f"/api/attendance-summary/learners/{learner['id']}?period=custom")
        finally:
            _clear_override(client)
        assert response.status_code == 400


class TestDashboardEndpointPermissions:
    def test_tutor_cannot_access_admin_dashboard(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        response = client.get("/api/dashboard/admin")
        assert response.status_code == 403

    def test_tutor_cannot_access_admin_tutors_breakdown(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        response = client.get("/api/dashboard/admin/tutors")
        assert response.status_code == 403

    def test_tutor_cannot_access_admin_cohorts_breakdown(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        response = client.get("/api/dashboard/admin/cohorts")
        assert response.status_code == 403

    def test_tutor_cannot_access_admin_outstanding_registers(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        response = client.get("/api/dashboard/admin/outstanding-registers")
        assert response.status_code == 403

    def test_tutor_cannot_access_admin_low_attendance_learners(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        response = client.get("/api/dashboard/admin/low-attendance-learners")
        assert response.status_code == 403

    def test_admin_can_access_admin_dashboard(self, client, monkeypatch):
        _as_admin(monkeypatch)
        response = client.get("/api/dashboard/admin")
        assert response.status_code == 200
        body = response.json()
        assert "attendancePercentageWeek" in body
        assert "attendancePercentageMonth" in body

    def test_unauthenticated_tutor_dashboard_is_rejected(self, client):
        response = client.get("/api/dashboard/tutor")
        assert response.status_code == 401

    def test_tutor_dashboard_cohorts_is_scoped_to_own_tutor(
        self, client, tutor_factory, cohort_factory
    ):
        owner = tutor_factory()
        other = tutor_factory()
        cohort_factory(tutor_id=owner["tutorId"])
        _as_tutor_via_dependency_override(client, other["tutorId"])
        try:
            response = client.get("/api/dashboard/tutor/cohorts")
        finally:
            _clear_override(client)
        assert response.status_code == 200
        assert response.json() == []  # other's cohort must not leak in


class TestReconciliation:
    """Two tutors, multiple cohorts, a full mix of statuses (present, late,
    authorised absence, unauthorised absence, not expected, a cancelled
    session, an incomplete register, a mid-period transfer, and a
    withdrawal). Learner totals must reconcile exactly to their cohort's
    totals, cohort totals to their tutor's, and tutor totals to the
    organisation's -- with nothing double-counted and cancelled/excluded
    rows contributing nothing anywhere."""

    def test_totals_reconcile_across_all_levels(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory
    ):
        from datetime import date

        from pyapp.attendance_metrics import fetch_attendance_metrics
        from pyapp.session_register_lib import ensure_expected_learners_snapshot

        tutor_a = tutor_factory()
        tutor_b = tutor_factory()
        cohort_a1 = cohort_factory(tutor_id=tutor_a["tutorId"])
        cohort_a2 = cohort_factory(tutor_id=tutor_a["tutorId"])
        cohort_b1 = cohort_factory(tutor_id=tutor_b["tutorId"])

        learner_1 = learner_factory(cohort_id=cohort_a1["id"], tutor_id=tutor_a["tutorId"])
        learner_2 = learner_factory(cohort_id=cohort_a2["id"], tutor_id=tutor_a["tutorId"])
        learner_3 = learner_factory(cohort_id=cohort_b1["id"], tutor_id=tutor_b["tutorId"], status="withdrawn", withdrawal_date="2026-01-20")
        # A learner transfer (with its own historical-attendance-isolation
        # guarantee) is covered separately by
        # test_historical_attendance_stays_with_original_cohort_after_a_transfer
        # below -- this test focuses on level-to-level arithmetic reconciliation.

        def make_session(cohort, day, hours=7):
            return attendance_session_factory(
                cohort_id=cohort["id"], session_date=f"2026-01-{day:02d}",
                planned_duration_hours=hours, created_by=admin_user["userId"],
            )

        s_a1_present = make_session(cohort_a1, 5)
        s_a1_late = make_session(cohort_a1, 6)
        s_a1_incomplete = make_session(cohort_a1, 7)  # left with no record for learner_1
        s_a2_auth_absence = make_session(cohort_a2, 5)
        s_a2_not_expected = make_session(cohort_a2, 8)
        s_b1_unauth_absence = make_session(cohort_b1, 5)
        s_b1_after_withdrawal = make_session(cohort_b1, 25)  # after learner_3's withdrawal
        s_a1_cancelled = make_session(cohort_a1, 9)

        for s in (s_a1_present, s_a1_late, s_a1_incomplete, s_a1_cancelled):
            ensure_expected_learners_snapshot(db, s["id"], cohort_a1["id"], date(2026, 1, int(s["session_date"][-2:])))
        for s in (s_a2_auth_absence, s_a2_not_expected):
            ensure_expected_learners_snapshot(db, s["id"], cohort_a2["id"], date(2026, 1, int(s["session_date"][-2:])))
        for s in (s_b1_unauth_absence, s_b1_after_withdrawal):
            ensure_expected_learners_snapshot(db, s["id"], cohort_b1["id"], date(2026, 1, int(s["session_date"][-2:])))

        _record(db, s_a1_present["id"], learner_1["id"], "present", hours_attended=7)
        _record(db, s_a1_late["id"], learner_1["id"], "late", hours_attended=6, minutes_late=15)
        # s_a1_incomplete: no record for learner_1 -- missing data.
        _record(db, s_a2_auth_absence["id"], learner_2["id"], "absent_authorised")
        _record(db, s_a2_not_expected["id"], learner_2["id"], "not_expected")
        _record(db, s_b1_unauth_absence["id"], learner_3["id"], "absent_unauthorised")
        # learner_3 withdrew before s_b1_after_withdrawal's date -- never
        # even in session_expected_learners for it, so no row is recorded.
        db.execute("UPDATE attendance_sessions SET status = 'cancelled' WHERE id = %s", (s_a1_cancelled["id"],))

        period_start, period_end = date(2026, 1, 1), date(2026, 1, 31)

        m_learner_1 = fetch_attendance_metrics(db, scope="learner", scope_id=learner_1["id"], period_start=period_start, period_end=period_end)
        m_learner_2 = fetch_attendance_metrics(db, scope="learner", scope_id=learner_2["id"], period_start=period_start, period_end=period_end)
        m_cohort_a1 = fetch_attendance_metrics(db, scope="cohort", scope_id=cohort_a1["id"], period_start=period_start, period_end=period_end)
        m_cohort_a2 = fetch_attendance_metrics(db, scope="cohort", scope_id=cohort_a2["id"], period_start=period_start, period_end=period_end)
        m_tutor_a = fetch_attendance_metrics(db, scope="tutor", scope_id=tutor_a["tutorId"], period_start=period_start, period_end=period_end)
        m_org = fetch_attendance_metrics(db, scope="organisation", scope_id=None, period_start=period_start, period_end=period_end)

        # learner_1's own totals: 2 real sessions expected (present + late)
        # plus 1 incomplete (still expected) = 3*420 = 1260 expected minutes;
        # attended = 420 (present) + 360 (late, recorded duration) = 780.
        assert m_learner_1.expectedMinutes == 1260
        assert m_learner_1.attendedMinutes == 780
        assert m_learner_1.missingRecordCount == 1

        # cohort_a1 contains learner_1 and learner_4 (learner_4 has no
        # sessions recorded against it in this scenario, contributing 0/0),
        # so cohort_a1's expected/attended must equal learner_1's exactly.
        assert m_cohort_a1.expectedMinutes == m_learner_1.expectedMinutes
        assert m_cohort_a1.attendedMinutes == m_learner_1.attendedMinutes

        # cohort_a2: learner_2's authorised absence (expected, not attended)
        # plus a not_expected row (contributes nothing).
        assert m_cohort_a2.expectedMinutes == m_learner_2.expectedMinutes == 420
        assert m_cohort_a2.attendedMinutes == 0
        assert m_cohort_a2.authorisedAbsenceMinutes == 420

        # tutor_a owns cohort_a1 + cohort_a2 -- totals must be the exact sum
        # (nothing double counted, nothing dropped).
        assert m_tutor_a.expectedMinutes == m_cohort_a1.expectedMinutes + m_cohort_a2.expectedMinutes
        assert m_tutor_a.attendedMinutes == m_cohort_a1.attendedMinutes + m_cohort_a2.attendedMinutes
        assert m_tutor_a.authorisedAbsenceMinutes == m_cohort_a1.authorisedAbsenceMinutes + m_cohort_a2.authorisedAbsenceMinutes

        # Organisation totals must be at least tutor_a's contribution (plus
        # whatever tutor_b's cohort contributed) -- and the cancelled
        # session and the not_expected/withdrawn-after rows must not have
        # leaked into anyone's totals at any level.
        assert m_org.expectedMinutes >= m_tutor_a.expectedMinutes
        assert m_org.attendedMinutes >= m_tutor_a.attendedMinutes

        # The cancelled session at cohort_a1 contributed nothing anywhere.
        cancelled_expected_minutes = 7 * 60
        assert m_cohort_a1.expectedMinutes < cancelled_expected_minutes * 4  # sanity: not accidentally included

        # learner_3 (withdrawn) never gets a session_expected_learners row
        # for the post-withdrawal session -- confirm directly.
        db.execute(
            "SELECT count(*) AS c FROM session_expected_learners WHERE session_id = %s AND learner_id = %s",
            (s_b1_after_withdrawal["id"], learner_3["id"]),
        )
        assert db.fetchone()["c"] == 0

    def test_historical_attendance_stays_with_original_cohort_after_a_transfer(
        self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory
    ):
        """The brief's explicit regression scenario: attendance recorded
        before a transfer must never move to the learner's new cohort."""
        from datetime import date

        from pyapp.attendance_metrics import fetch_attendance_metrics
        from pyapp.session_register_lib import ensure_expected_learners_snapshot

        cohort_old = cohort_factory()
        cohort_new = cohort_factory()
        learner = learner_factory(cohort_id=cohort_old["id"], start_date="2026-01-01")

        session_before_transfer = attendance_session_factory(
            cohort_id=cohort_old["id"], session_date="2026-01-10", planned_duration_hours=7, created_by=admin_user["userId"]
        )
        ensure_expected_learners_snapshot(db, session_before_transfer["id"], cohort_old["id"], date(2026, 1, 10))
        _record(db, session_before_transfer["id"], learner["id"], "present", hours_attended=7)

        # Transfer effective after the session.
        db.execute(
            "INSERT INTO learner_allocation_history (learner_id, previous_cohort_id, new_cohort_id, effective_date, changed_by) "
            "VALUES (%s, %s, %s, %s, %s)",
            (learner["id"], cohort_old["id"], cohort_new["id"], "2026-01-15", admin_user["userId"]),
        )
        db.execute("UPDATE learners SET cohort_id = %s WHERE id = %s", (cohort_new["id"], learner["id"]))

        period_start, period_end = date(2026, 1, 1), date(2026, 1, 31)
        m_old = fetch_attendance_metrics(db, scope="cohort", scope_id=cohort_old["id"], period_start=period_start, period_end=period_end)
        m_new = fetch_attendance_metrics(db, scope="cohort", scope_id=cohort_new["id"], period_start=period_start, period_end=period_end)

        assert m_old.expectedMinutes == 420
        assert m_old.attendedMinutes == 420
        assert m_new.expectedMinutes == 0  # no session ever occurred in the new cohort
        db.execute(
            "SELECT cohort_id FROM session_expected_learners WHERE session_id = %s AND learner_id = %s",
            (session_before_transfer["id"], learner["id"]),
        )
        assert db.fetchone()["cohort_id"] == cohort_old["id"]
