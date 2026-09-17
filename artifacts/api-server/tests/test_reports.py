"""HTTP-level tests for the Phase 9 reporting endpoints (pyapp/routers/
reports.py). Replaces the old hours-based test_reports.py entirely: that
file locked in attendance_calc.py's formula (including an explicitly
documented "breakdown ignores the top-level filter" quirk) which Phase 9's
migration onto attendance_metrics.py deliberately removes -- report totals
must now reconcile with the dashboard engine, not diverge from it.
"""
from datetime import date

from fastapi import Request

from pyapp import auth as auth_module
from pyapp.attendance_metrics import fetch_attendance_metrics
from pyapp.session_register_lib import ensure_expected_learners_snapshot


# Captured once at import time, before any test monkeypatches
# auth_module.require_auth -- this is the exact function object every
# router's Depends(require_auth) captured at its own route-registration
# time, so it's the only valid dependency_overrides key. (Reading
# auth_module.require_auth *after* monkeypatching it would just return the
# fake, defeating the override.)
_REAL_REQUIRE_AUTH = auth_module.require_auth


def _fake_session_dependency(session, user_id):
    # request MUST be annotated as Request: when this function is used as a
    # dependency_overrides target, FastAPI rebuilds a fresh Dependant from
    # *this* function's own signature (not the original's) to decide what
    # to inject -- an unannotated `request` param is treated as a required
    # query field instead of the special Request injection, breaking every
    # route that depends on it with a spurious "query.request required" 400.
    def fake_require_auth(request: Request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    return fake_require_auth


def _as_tutor(client, monkeypatch, tutor_id, user_id=1):
    """Covers both Depends(require_auth)-wired routes (via
    dependency_overrides -- FastAPI captured the require_auth callable at
    route-registration time, so monkeypatching auth_module.require_auth
    alone has no effect on those) and Depends(require_admin)-wired routes
    (require_admin calls require_auth as a plain function lookup inside
    auth.py, so it *does* see the monkeypatch) -- matching the two
    mechanisms test_attendance_summary_endpoints.py already established."""
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


PERIOD_QS = "period=custom&dateFrom=2026-01-01&dateTo=2026-01-31"


class TestLearnerReport:
    def test_reconciles_with_attendance_metrics_engine(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", planned_duration_hours=6, created_by=admin_user["userId"])
        _snapshot(db, session_row)
        _record(db, session_row["id"], learner["id"], "present", hours_attended=6)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/learner/{learner['id']}?{PERIOD_QS}")
        assert response.status_code == 200
        body = response.json()

        expected = fetch_attendance_metrics(db, scope="learner", scope_id=learner["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
        assert body["metrics"]["expectedMinutes"] == expected.expectedMinutes == 360
        assert body["metrics"]["attendedMinutes"] == expected.attendedMinutes == 360
        assert "sessionHistory" in body and body["sessionHistory"]["total"] == 1
        assert "registerCompletion" in body

    def test_tutor_cannot_access_another_tutors_learner(self, client, monkeypatch, tutor_factory, learner_factory):
        owner = tutor_factory()
        other = tutor_factory()
        learner = learner_factory(tutor_id=owner["tutorId"])
        _as_tutor(client, monkeypatch, other["tutorId"])
        response = client.get(f"/api/reports/learner/{learner['id']}")
        assert response.status_code == 403

    def test_nonexistent_learner_is_404(self, client, monkeypatch):
        _as_admin(client, monkeypatch)
        response = client.get("/api/reports/learner/999999999")
        assert response.status_code == 404

    def test_custom_period_without_dates_is_400(self, client, monkeypatch, learner_factory):
        learner = learner_factory()
        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/learner/{learner['id']}?period=custom")
        assert response.status_code == 400


class TestLearnerReportSecondaryCohortSeparation:
    """A learner enrolled in both a home cohort and a Functional Skills
    secondary cohort must get separate, never-blended metrics for each --
    both in the new cohortBreakdown and in the top-level (home-only)
    metrics field."""

    def test_home_and_secondary_metrics_never_blend(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
        secondary_enrollment_factory,
    ):
        home = cohort_factory()
        fs_cohort = cohort_factory(membership_type="secondary")
        learner = learner_factory(cohort_id=home["id"], start_date="2026-01-01")
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        home_session = attendance_session_factory(
            cohort_id=home["id"], session_date="2026-01-06", planned_duration_hours=6, created_by=admin_user["userId"]
        )
        _snapshot(db, home_session)
        _record(db, home_session["id"], learner["id"], "present", hours_attended=6)

        fs_session = attendance_session_factory(
            cohort_id=fs_cohort["id"], session_date="2026-01-07", planned_duration_hours=2, created_by=admin_user["userId"]
        )
        _snapshot(db, fs_session)
        _record(db, fs_session["id"], learner["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/learner/{learner['id']}?{PERIOD_QS}")
        assert response.status_code == 200
        body = response.json()

        # Top-level metrics stay the HOME cohort's figures only.
        assert body["metrics"]["expectedMinutes"] == 360
        assert body["metrics"]["attendedMinutes"] == 360

        breakdown = {e["relationship"]: e for e in body["cohortBreakdown"]}
        assert breakdown["home"]["cohortId"] == home["id"]
        assert breakdown["home"]["metrics"]["attendedMinutes"] == 360
        assert breakdown["functional_skills"]["cohortId"] == fs_cohort["id"]
        assert breakdown["functional_skills"]["metrics"]["expectedMinutes"] == 120
        assert breakdown["functional_skills"]["metrics"]["attendedMinutes"] == 0

    def test_functional_skills_tutor_with_no_home_relationship_can_access_the_report(
        self, client, monkeypatch, db, admin_user, tutor_factory, cohort_factory, learner_factory,
        secondary_enrollment_factory,
    ):
        home_tutor = tutor_factory()
        fs_tutor = tutor_factory()
        home = cohort_factory(tutor_id=home_tutor["tutorId"])
        fs_cohort = cohort_factory(tutor_id=fs_tutor["tutorId"], membership_type="secondary")
        learner = learner_factory(tutor_id=home_tutor["tutorId"], cohort_id=home["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        _as_tutor(client, monkeypatch, fs_tutor["tutorId"])
        response = client.get(f"/api/reports/learner/{learner['id']}?{PERIOD_QS}")
        assert response.status_code == 200

    def test_unrelated_tutor_is_still_denied(
        self, client, monkeypatch, tutor_factory, cohort_factory, learner_factory,
    ):
        home_tutor = tutor_factory()
        unrelated_tutor = tutor_factory()
        home = cohort_factory(tutor_id=home_tutor["tutorId"])
        learner = learner_factory(tutor_id=home_tutor["tutorId"], cohort_id=home["id"])

        _as_tutor(client, monkeypatch, unrelated_tutor["tutorId"])
        response = client.get(f"/api/reports/learner/{learner['id']}?{PERIOD_QS}")
        assert response.status_code == 403


class TestCohortReport:
    def test_learner_breakdown_reconciles_with_cohort_total(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_a = learner_factory(cohort_id=cohort["id"])
        learner_b = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", planned_duration_hours=6, created_by=admin_user["userId"])
        _snapshot(db, session_row)
        _record(db, session_row["id"], learner_a["id"], "present", hours_attended=6)
        _record(db, session_row["id"], learner_b["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/cohort/{cohort['id']}?{PERIOD_QS}")
        assert response.status_code == 200
        body = response.json()

        items = body["learnerBreakdown"]["items"]
        summed_attended = sum(i["metrics"]["attendedMinutes"] for i in items)
        summed_expected = sum(i["metrics"]["expectedMinutes"] for i in items)
        assert summed_attended == body["metrics"]["attendedMinutes"]
        assert summed_expected == body["metrics"]["expectedMinutes"]

    def test_breakdown_excludes_a_learner_transferred_out_before_any_session_in_this_cohort(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort_old = cohort_factory()
        cohort_new = cohort_factory()
        learner = learner_factory(cohort_id=cohort_new["id"])
        # A session that only ever happened in the *new* cohort -- the
        # learner never had a session_expected_learners row for cohort_old.
        session_row = attendance_session_factory(cohort_id=cohort_new["id"], session_date="2026-01-06", planned_duration_hours=6, created_by=admin_user["userId"])
        _snapshot(db, session_row)
        _record(db, session_row["id"], learner["id"], "present", hours_attended=6)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/cohort/{cohort_old['id']}?{PERIOD_QS}")
        assert response.status_code == 200
        assert response.json()["learnerBreakdown"]["items"] == []

    def test_tutor_cannot_access_another_tutors_cohort(self, client, monkeypatch, tutor_factory, cohort_factory):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        _as_tutor(client, monkeypatch, other["tutorId"])
        response = client.get(f"/api/reports/cohort/{cohort['id']}")
        assert response.status_code == 403


class TestTutorReport:
    def test_cohort_breakdown_reconciles_with_tutor_total(
        self, client, monkeypatch, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory
    ):
        tutor = tutor_factory()
        cohort_a = cohort_factory(tutor_id=tutor["tutorId"])
        cohort_b = cohort_factory(tutor_id=tutor["tutorId"])
        learner_a = learner_factory(cohort_id=cohort_a["id"], tutor_id=tutor["tutorId"])
        learner_b = learner_factory(cohort_id=cohort_b["id"], tutor_id=tutor["tutorId"])
        session_a = attendance_session_factory(cohort_id=cohort_a["id"], session_date="2026-01-06", planned_duration_hours=5, created_by=admin_user["userId"])
        session_b = attendance_session_factory(cohort_id=cohort_b["id"], session_date="2026-01-07", planned_duration_hours=5, created_by=admin_user["userId"])
        _snapshot(db, session_a)
        _snapshot(db, session_b)
        _record(db, session_a["id"], learner_a["id"], "present", hours_attended=5)
        _record(db, session_b["id"], learner_b["id"], "late", hours_attended=4, minutes_late=15)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/tutor/{tutor['tutorId']}?{PERIOD_QS}")
        assert response.status_code == 200
        body = response.json()

        summed = sum(c["metrics"]["attendedMinutes"] for c in body["cohortBreakdown"])
        assert summed == body["metrics"]["attendedMinutes"] == 5 * 60 + 4 * 60

    def test_tutor_can_access_own_report(self, client, monkeypatch, tutor_factory):
        tutor = tutor_factory()
        _as_tutor(client, monkeypatch, tutor["tutorId"])
        response = client.get(f"/api/reports/tutor/{tutor['tutorId']}")
        assert response.status_code == 200

    def test_tutor_cannot_access_another_tutors_report_by_changing_the_tutor_id(self, client, monkeypatch, tutor_factory):
        owner = tutor_factory()
        other = tutor_factory()
        _as_tutor(client, monkeypatch, other["tutorId"])
        response = client.get(f"/api/reports/tutor/{owner['tutorId']}")
        assert response.status_code == 403


class TestOrganisationReport:
    def test_admin_only(self, client, monkeypatch, tutor_factory):
        tutor = tutor_factory()
        _as_tutor(client, monkeypatch, tutor["tutorId"])
        response = client.get("/api/reports/organisation")
        assert response.status_code == 403

    def test_tutor_and_cohort_breakdowns_reconcile_with_organisation_total(
        self, client, monkeypatch, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory
    ):
        tutor_a = tutor_factory()
        tutor_b = tutor_factory()
        cohort_a = cohort_factory(tutor_id=tutor_a["tutorId"])
        cohort_b = cohort_factory(tutor_id=tutor_b["tutorId"])
        learner_a = learner_factory(cohort_id=cohort_a["id"], tutor_id=tutor_a["tutorId"])
        learner_b = learner_factory(cohort_id=cohort_b["id"], tutor_id=tutor_b["tutorId"])
        session_a = attendance_session_factory(cohort_id=cohort_a["id"], session_date="2026-01-06", planned_duration_hours=6, created_by=admin_user["userId"])
        session_b = attendance_session_factory(cohort_id=cohort_b["id"], session_date="2026-01-07", planned_duration_hours=6, created_by=admin_user["userId"])
        _snapshot(db, session_a)
        _snapshot(db, session_b)
        _record(db, session_a["id"], learner_a["id"], "present", hours_attended=6)
        _record(db, session_b["id"], learner_b["id"], "present", hours_attended=6)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/organisation?{PERIOD_QS}")
        assert response.status_code == 200
        body = response.json()

        cohort_ids = {cohort_a["id"], cohort_b["id"]}
        relevant_cohorts = [c for c in body["cohortBreakdown"] if c["cohort"]["id"] in cohort_ids]
        assert sum(c["metrics"]["attendedMinutes"] for c in relevant_cohorts) <= body["metrics"]["attendedMinutes"]
        # Both seeded cohorts must appear in the org-wide breakdown (no
        # silent per-cohort omission) and both tutors likewise.
        assert cohort_ids <= {c["cohort"]["id"] for c in body["cohortBreakdown"]}
        tutor_ids = {tutor_a["tutorId"], tutor_b["tutorId"]}
        assert tutor_ids <= {t["tutorId"] for t in body["tutorBreakdown"]}

    def test_subject_breakdown_separates_math_from_english(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        home = cohort_factory()
        maths_cohort = cohort_factory(membership_type="secondary", subject="math")
        english_cohort = cohort_factory(membership_type="secondary", subject="english")
        learner = learner_factory(cohort_id=home["id"])

        maths_session = attendance_session_factory(cohort_id=maths_cohort["id"], session_date="2026-01-06", planned_duration_hours=2, created_by=admin_user["userId"])
        english_session = attendance_session_factory(cohort_id=english_cohort["id"], session_date="2026-01-07", planned_duration_hours=2, created_by=admin_user["userId"])
        db.execute(
            "INSERT INTO session_expected_learners (session_id, learner_id, cohort_id) VALUES (%s, %s, %s)",
            (maths_session["id"], learner["id"], maths_cohort["id"]),
        )
        db.execute(
            "INSERT INTO session_expected_learners (session_id, learner_id, cohort_id) VALUES (%s, %s, %s)",
            (english_session["id"], learner["id"], english_cohort["id"]),
        )
        _record(db, maths_session["id"], learner["id"], "present", hours_attended=2)
        _record(db, english_session["id"], learner["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/organisation?{PERIOD_QS}")
        assert response.status_code == 200
        breakdown = {row["subject"]: row["metrics"] for row in response.json()["subjectBreakdown"]}

        assert breakdown["math"]["attendedMinutes"] == 120
        assert breakdown["english"]["attendedMinutes"] == 0
        assert breakdown["english"]["expectedMinutes"] == 120
        # A standard (non-Functional Skills) cohort never contributes a row.
        assert None not in breakdown


class TestFunctionalSkillsReport:
    def test_admin_only(self, client, monkeypatch, tutor_factory):
        tutor = tutor_factory()
        _as_tutor(client, monkeypatch, tutor["tutorId"])
        response = client.get("/api/reports/functional-skills")
        assert response.status_code == 403

    def test_home_cohort_attendance_never_leaks_into_fs_figures(
        self, client, monkeypatch, db, admin_user, tutor_factory, cohort_factory, learner_factory,
        attendance_session_factory, secondary_enrollment_factory,
    ):
        fs_tutor = tutor_factory()
        home_cohort = cohort_factory()
        fs_cohort = cohort_factory(membership_type="secondary", subject="math", tutor_id=fs_tutor["tutorId"])
        learner = learner_factory(cohort_id=home_cohort["id"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        # Perfect at home, absent every time in the FS cohort.
        home_session = attendance_session_factory(
            cohort_id=home_cohort["id"], session_date="2026-01-05", planned_duration_hours=7, created_by=admin_user["userId"]
        )
        _snapshot(db, home_session)
        _record(db, home_session["id"], learner["id"], "present", hours_attended=7)

        fs_sessions = [
            attendance_session_factory(cohort_id=fs_cohort["id"], session_date=d, planned_duration_hours=2, created_by=admin_user["userId"])
            for d in ("2026-01-06", "2026-01-13", "2026-01-20")
        ]
        for s in fs_sessions:
            _snapshot(db, s)
            _record(db, s["id"], learner["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/functional-skills?{PERIOD_QS}")
        assert response.status_code == 200
        body = response.json()

        assert body["metrics"]["attendedMinutes"] == 0
        assert body["metrics"]["expectedMinutes"] == 360
        subject_breakdown = {row["subject"]: row["metrics"] for row in body["subjectBreakdown"]}
        assert subject_breakdown["math"]["attendedMinutes"] == 0
        cohort_row = next(r for r in body["cohortBreakdown"] if r["cohort"]["id"] == fs_cohort["id"])
        assert cohort_row["metrics"]["attendancePercentage"] == 0.0
        assert cohort_row["activeLearnerCount"] == 1

        at_risk_ids = {r["learnerId"] for r in body["atRiskLearners"]}
        assert learner["id"] in at_risk_ids
        at_risk_row = next(r for r in body["atRiskLearners"] if r["learnerId"] == learner["id"])
        assert at_risk_row["metrics"]["attendancePercentage"] == 0.0
        assert at_risk_row["subject"] == "math"

    def test_subject_filter_narrows_cohort_breakdown_and_at_risk_but_not_subject_breakdown(
        self, client, monkeypatch, db, admin_user, tutor_factory, cohort_factory, learner_factory,
        attendance_session_factory, secondary_enrollment_factory,
    ):
        tutor = tutor_factory()
        maths_cohort = cohort_factory(membership_type="secondary", subject="math", tutor_id=tutor["tutorId"])
        english_cohort = cohort_factory(membership_type="secondary", subject="english", tutor_id=tutor["tutorId"])
        maths_learner = learner_factory()
        english_learner = learner_factory()
        secondary_enrollment_factory(learner_id=maths_learner["id"], cohort_id=maths_cohort["id"], enrolled_date="2026-01-01")
        secondary_enrollment_factory(learner_id=english_learner["id"], cohort_id=english_cohort["id"], enrolled_date="2026-01-01")

        maths_session = attendance_session_factory(cohort_id=maths_cohort["id"], session_date="2026-01-06", planned_duration_hours=2, created_by=admin_user["userId"])
        english_session = attendance_session_factory(cohort_id=english_cohort["id"], session_date="2026-01-07", planned_duration_hours=2, created_by=admin_user["userId"])
        _snapshot(db, maths_session)
        _snapshot(db, english_session)
        _record(db, maths_session["id"], maths_learner["id"], "present", hours_attended=2)
        _record(db, english_session["id"], english_learner["id"], "present", hours_attended=2)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/functional-skills?{PERIOD_QS}&subject=math&tutorId={tutor['tutorId']}")
        assert response.status_code == 200
        body = response.json()

        assert {c["cohort"]["id"] for c in body["cohortBreakdown"]} == {maths_cohort["id"]}
        # subjectBreakdown reflects every subject for the tutor filter, not
        # collapsed down to just the subject filter.
        assert {row["subject"] for row in body["subjectBreakdown"]} == {"math", "english"}


class TestFunctionalSkillsReportExport:
    def test_cohort_breakdown_export(
        self, client, monkeypatch, db, admin_user, cohort_factory, attendance_session_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary", subject="both")
        attendance_session_factory(cohort_id=fs_cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/functional-skills/export?{PERIOD_QS}&breakdown=cohort")
        assert response.status_code == 200
        assert "cohortName" in response.text
        assert fs_cohort["name"] in response.text

    def test_subject_breakdown_export(self, client, monkeypatch, cohort_factory):
        cohort_factory(membership_type="secondary", subject="english")
        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/functional-skills/export?{PERIOD_QS}&breakdown=subject")
        assert response.status_code == 200
        assert "key" in response.text or "label" in response.text

    def test_learner_breakdown_export(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory,
        attendance_session_factory, secondary_enrollment_factory,
    ):
        fs_cohort = cohort_factory(membership_type="secondary", subject="math")
        learner = learner_factory()
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")
        session = attendance_session_factory(cohort_id=fs_cohort["id"], session_date="2026-01-06", planned_duration_hours=2, created_by=admin_user["userId"])
        _snapshot(db, session)
        _record(db, session["id"], learner["id"], "present", hours_attended=2)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/functional-skills/export?{PERIOD_QS}&breakdown=learner")
        assert response.status_code == 200
        assert "atRisk" in response.text
        assert learner["learner_ref"] in response.text


class TestAbsenceAndLatenessReports:
    def test_absence_report_separates_authorised_from_unauthorised(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner_auth = learner_factory(cohort_id=cohort["id"])
        learner_unauth = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", planned_duration_hours=6, created_by=admin_user["userId"])
        _snapshot(db, session_row)
        _record(db, session_row["id"], learner_auth["id"], "absent_authorised")
        _record(db, session_row["id"], learner_unauth["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        auth_resp = client.get(f"/api/reports/absence?absenceType=authorised&{PERIOD_QS}&cohortId={cohort['id']}")
        assert auth_resp.status_code == 200
        auth_ids = {r["learnerId"] for r in auth_resp.json()["items"]}
        assert auth_ids == {learner_auth["id"]}

        unauth_resp = client.get(f"/api/reports/absence?absenceType=unauthorised&{PERIOD_QS}&cohortId={cohort['id']}")
        unauth_ids = {r["learnerId"] for r in unauth_resp.json()["items"]}
        assert unauth_ids == {learner_unauth["id"]}

    def test_absence_report_can_be_filtered_to_a_functional_skills_subject(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory,
    ):
        maths_cohort = cohort_factory(membership_type="secondary", subject="math")
        english_cohort = cohort_factory(membership_type="secondary", subject="english")
        learner = learner_factory(cohort_id=cohort_factory()["id"])
        maths_session = attendance_session_factory(cohort_id=maths_cohort["id"], session_date="2026-01-06", planned_duration_hours=2, created_by=admin_user["userId"])
        english_session = attendance_session_factory(cohort_id=english_cohort["id"], session_date="2026-01-07", planned_duration_hours=2, created_by=admin_user["userId"])
        db.execute(
            "INSERT INTO session_expected_learners (session_id, learner_id, cohort_id) VALUES (%s, %s, %s)",
            (maths_session["id"], learner["id"], maths_cohort["id"]),
        )
        db.execute(
            "INSERT INTO session_expected_learners (session_id, learner_id, cohort_id) VALUES (%s, %s, %s)",
            (english_session["id"], learner["id"], english_cohort["id"]),
        )
        _record(db, maths_session["id"], learner["id"], "absent_unauthorised")
        _record(db, english_session["id"], learner["id"], "absent_unauthorised")

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/absence?absenceType=unauthorised&{PERIOD_QS}&subject=math")
        assert response.status_code == 200
        items = response.json()["items"]
        assert {r["sessionId"] for r in items} == {maths_session["id"]}

    def test_tutor_scope_cannot_be_widened_via_learner_id_filter(self, client, monkeypatch, tutor_factory, learner_factory):
        owner = tutor_factory()
        other = tutor_factory()
        learner = learner_factory(tutor_id=owner["tutorId"])
        _as_tutor(client, monkeypatch, other["tutorId"])
        response = client.get(f"/api/reports/absence?absenceType=unauthorised&learnerId={learner['id']}")
        assert response.status_code == 403

    def test_lateness_report_reconciles_metrics_with_engine(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", planned_duration_hours=6, created_by=admin_user["userId"])
        _snapshot(db, session_row)
        _record(db, session_row["id"], learner["id"], "late", hours_attended=5, minutes_late=20)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/lateness?{PERIOD_QS}&cohortId={cohort['id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["items"][0]["minutesLate"] == 20
        expected = fetch_attendance_metrics(db, scope="cohort", scope_id=cohort["id"], period_start=date(2026, 1, 1), period_end=date(2026, 1, 31))
        assert body["metrics"]["lateMinutes"] == expected.lateMinutes == 20


class TestRegisterCompletionReport:
    def test_classifies_not_started_completed_and_locked(
        self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        s_not_started = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", created_by=admin_user["userId"])
        s_completed = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-07", created_by=admin_user["userId"])
        _snapshot(db, s_not_started)
        _snapshot(db, s_completed)
        _record(db, s_completed["id"], learner["id"], "present", hours_attended=6)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/register-completion?{PERIOD_QS}&cohortId={cohort['id']}")
        assert response.status_code == 200
        by_id = {r["sessionId"]: r for r in response.json()["items"]}
        assert by_id[s_not_started["id"]]["registerStatus"] == "not_started"
        assert by_id[s_not_started["id"]]["missingRowCount"] == 1
        assert by_id[s_completed["id"]]["registerStatus"] == "completed"

    def test_overdue_only_filter_excludes_future_sessions(
        self, client, monkeypatch, db, admin_user, cohort_factory, attendance_session_factory
    ):
        cohort = cohort_factory()
        future_session = attendance_session_factory(cohort_id=cohort["id"], session_date="2099-01-01", created_by=admin_user["userId"])
        _snapshot(db, future_session)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/register-completion?period=custom&dateFrom=2099-01-01&dateTo=2099-01-31&cohortId={cohort['id']}&overdueOnly=true")
        assert response.status_code == 200
        assert response.json()["items"] == []


class TestAllocationHistoryReport:
    def test_admin_only(self, client, monkeypatch, tutor_factory):
        tutor = tutor_factory()
        _as_tutor(client, monkeypatch, tutor["tutorId"])
        response = client.get("/api/reports/allocation-history")
        assert response.status_code == 403

    def test_includes_a_notice_that_attendance_never_transfers(self, client, monkeypatch):
        _as_admin(client, monkeypatch)
        response = client.get("/api/reports/allocation-history")
        assert response.status_code == 200
        assert "do not transfer historical attendance" in response.json()["notice"]


class TestAttendanceHoursReport:
    def test_learner_grouping_requires_a_tutor_or_cohort_filter(self, client, monkeypatch):
        _as_admin(client, monkeypatch)
        response = client.get("/api/reports/attendance-hours?groupBy=learner")
        assert response.status_code == 400

    def test_week_grouping_returns_bucketed_metrics(self, client, monkeypatch, db, admin_user, cohort_factory, learner_factory, attendance_session_factory):
        cohort = cohort_factory()
        learner = learner_factory(cohort_id=cohort["id"])
        session_row = attendance_session_factory(cohort_id=cohort["id"], session_date="2026-01-06", planned_duration_hours=6, created_by=admin_user["userId"])
        _snapshot(db, session_row)
        _record(db, session_row["id"], learner["id"], "present", hours_attended=6)

        _as_admin(client, monkeypatch)
        response = client.get(f"/api/reports/attendance-hours?groupBy=week&{PERIOD_QS}&cohortId={cohort['id']}")
        assert response.status_code == 200
        items = response.json()["items"]
        assert sum(i["metrics"]["attendedMinutes"] for i in items) == 360

    def test_tutor_grouping_is_admin_only(self, client, monkeypatch, tutor_factory):
        tutor = tutor_factory()
        _as_tutor(client, monkeypatch, tutor["tutorId"])
        response = client.get("/api/reports/attendance-hours?groupBy=tutor")
        assert response.status_code == 403
