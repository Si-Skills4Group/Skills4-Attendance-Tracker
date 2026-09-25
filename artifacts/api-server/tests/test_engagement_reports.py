"""Stage 4: HTTP-level permission checks for the engagement-recency
report -- item 7's "permissions on list, detail, summary and export",
plus the item-2 correction's HTTP-level confirmation that evidence (not
just learner visibility) is scoped consistently across every endpoint.
Every endpoint here reuses the same permission machinery as the rest of
routers/reports.py (_enforce_tutor_scope / require_learner_access), so
this exercises that wiring over real HTTP rather than re-testing the
already-covered helpers themselves."""
from datetime import date

from fastapi import Request

from pyapp import auth as auth_module
from pyapp.routers.attendance import AttendanceRegisterInput, RegisterEntryInput, save_attendance_register

_REAL_REQUIRE_AUTH = auth_module.require_auth


def _record(request_factory, session_id, learner_id, status, acting_session, **kwargs):
    return save_attendance_register(
        session_id,
        AttendanceRegisterInput(
            registerVersion=1,
            entries=[RegisterEntryInput(learnerId=learner_id, status=status, hoursAttended=kwargs.get("hoursAttended", 0), minutesLate=0)],
        ),
        request_factory(),
        acting_session,
    )


def _fake_session_dependency(session, user_id):
    def fake_require_auth(request: Request):
        request.state.session = session
        request.state.current_user_id = user_id
        return session

    return fake_require_auth


def _as_tutor(client, monkeypatch, tutor_id, user_id):
    session = {"userId": user_id, "role": "tutor", "tutorId": tutor_id}
    fake_require_auth = _fake_session_dependency(session, user_id)
    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)
    client.app.dependency_overrides[_REAL_REQUIRE_AUTH] = fake_require_auth


def _as_admin(client, monkeypatch, user_id=1):
    session = {"userId": user_id, "role": "admin", "tutorId": None}
    fake_require_auth = _fake_session_dependency(session, user_id)
    monkeypatch.setattr(auth_module, "require_auth", fake_require_auth)
    client.app.dependency_overrides[_REAL_REQUIRE_AUTH] = fake_require_auth


class TestEngagementListPermissions:
    def test_tutor_sees_their_own_learner(self, client, monkeypatch, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        _as_tutor(client, monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.get("/api/reports/engagement-recency")
        assert response.status_code == 200
        body = response.json()
        assert any(item["id"] == learner["id"] for item in body["items"])
        assert body["scopeLabel"]

    def test_tutor_cannot_probe_another_tutors_id(self, client, monkeypatch, tutor_factory):
        owner = tutor_factory()
        other = tutor_factory()
        _as_tutor(client, monkeypatch, other["tutorId"], other["userId"])
        response = client.get("/api/reports/engagement-recency", params={"tutorId": owner["tutorId"]})
        assert response.status_code == 403

    def test_tutor_cannot_probe_another_tutors_cohort(self, client, monkeypatch, tutor_factory, cohort_factory):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        _as_tutor(client, monkeypatch, other["tutorId"], other["userId"])
        response = client.get("/api/reports/engagement-recency", params={"cohortId": cohort["id"]})
        assert response.status_code == 403

    def test_admin_sees_everything_without_a_tutor_filter(self, client, monkeypatch, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        _as_admin(client, monkeypatch)
        response = client.get("/api/reports/engagement-recency", params={"pageSize": 200})
        assert response.status_code == 200
        assert any(item["id"] == learner["id"] for item in response.json()["items"])


class TestEngagementDetailPermissions:
    def test_tutor_can_view_their_own_learner(self, client, monkeypatch, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        _as_tutor(client, monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.get(f"/api/reports/engagement-recency/{learner['id']}")
        assert response.status_code == 200
        assert response.json()["latestEngagementDate"] is None

    def test_tutor_cannot_view_another_tutors_learner(self, client, monkeypatch, tutor_factory, cohort_factory, learner_factory):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=owner["tutorId"])

        _as_tutor(client, monkeypatch, other["tutorId"], other["userId"])
        response = client.get(f"/api/reports/engagement-recency/{learner['id']}")
        assert response.status_code == 403


class TestEngagementSummaryPermissions:
    def test_tutor_summary_is_scoped_to_their_own_learners(self, client, monkeypatch, tutor_factory, cohort_factory, learner_factory):
        owner = tutor_factory()
        other = tutor_factory()
        owner_cohort = cohort_factory(tutor_id=owner["tutorId"])
        learner_factory(cohort_id=owner_cohort["id"], tutor_id=owner["tutorId"])

        _as_tutor(client, monkeypatch, other["tutorId"], other["userId"])
        response = client.get("/api/reports/engagement-recency/summary")
        assert response.status_code == 200
        body = response.json()
        assert "totalLearners" in body and "noRecordedEngagementCount" in body

    def test_tutor_cannot_probe_another_tutors_summary(self, client, monkeypatch, tutor_factory):
        owner = tutor_factory()
        other = tutor_factory()
        _as_tutor(client, monkeypatch, other["tutorId"], other["userId"])
        response = client.get("/api/reports/engagement-recency/summary", params={"tutorId": owner["tutorId"]})
        assert response.status_code == 403


class TestEngagementExportPermissions:
    def test_tutor_export_is_scoped_and_returns_csv(self, client, monkeypatch, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        _as_tutor(client, monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.get("/api/reports/engagement-recency/export")
        assert response.status_code == 200
        assert "learnerRef" in response.text
        assert learner["learner_ref"] in response.text

    def test_tutor_cannot_probe_another_tutors_export(self, client, monkeypatch, tutor_factory):
        owner = tutor_factory()
        other = tutor_factory()
        _as_tutor(client, monkeypatch, other["tutorId"], other["userId"])
        response = client.get("/api/reports/engagement-recency/export", params={"tutorId": owner["tutorId"]})
        assert response.status_code == 403


class TestEvidenceScopingOverHttp:
    """Item 2's acceptance check, exercised end-to-end over real HTTP: an
    FS-only tutor can see an older FS attendance event but not a newer
    core one, and the newer restricted event must not influence any
    returned date, summary count, filter result, or export -- across
    every one of list/detail/summary/export."""

    def _scenario(self, db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, secondary_enrollment_factory, request_factory):
        core_tutor = tutor_factory()
        fs_tutor = tutor_factory()
        core_cohort = cohort_factory(tutor_id=core_tutor["tutorId"])
        fs_cohort = cohort_factory(tutor_id=fs_tutor["tutorId"], membership_type="secondary", subject="math")
        learner = learner_factory(cohort_id=core_cohort["id"], tutor_id=core_tutor["tutorId"])
        secondary_enrollment_factory(learner_id=learner["id"], cohort_id=fs_cohort["id"], enrolled_date="2026-01-01")

        older_fs_session = attendance_session_factory(cohort_id=fs_cohort["id"], session_date="2026-08-01", created_by=admin_user["userId"])
        _record(request_factory, older_fs_session["id"], learner["id"], "present", admin_user, hoursAttended=1)
        newer_core_session = attendance_session_factory(cohort_id=core_cohort["id"], session_date="2026-09-15", created_by=admin_user["userId"])
        _record(request_factory, newer_core_session["id"], learner["id"], "present", admin_user, hoursAttended=6)

        return {"fs_tutor": fs_tutor, "core_tutor": core_tutor, "learner": learner}

    def test_list_summary_detail_and_export_all_reflect_only_the_fs_tutors_visible_event(
        self, client, monkeypatch, db, admin_user, tutor_factory, cohort_factory, learner_factory,
        attendance_session_factory, secondary_enrollment_factory, request_factory,
    ):
        scenario = self._scenario(db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, secondary_enrollment_factory, request_factory)
        fs_tutor = scenario["fs_tutor"]
        learner = scenario["learner"]

        _as_tutor(client, monkeypatch, fs_tutor["tutorId"], fs_tutor["userId"])

        list_response = client.get("/api/reports/engagement-recency", params={"pageSize": 200})
        assert list_response.status_code == 200
        row = next(item for item in list_response.json()["items"] if item["id"] == learner["id"])
        assert row["lastLiveAttendance"] == "2026-08-01"
        assert row["latestEngagementDate"] == "2026-08-01"

        detail_response = client.get(f"/api/reports/engagement-recency/{learner['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["latestEngagementDate"] == "2026-08-01"

        # A min-days-since filter evaluated against the FS tutor's own
        # visible (older) date must still match -- the newer, hidden date
        # must never leak in to make the learner look more recently
        # engaged than what this tutor can actually see.
        filtered = client.get("/api/reports/engagement-recency", params={"minDaysSince": 40, "pageSize": 200})
        assert any(item["id"] == learner["id"] for item in filtered.json()["items"])

        export_response = client.get("/api/reports/engagement-recency/export")
        assert export_response.status_code == 200
        assert "2026-08-01" in export_response.text
        assert "2026-09-15" not in export_response.text

    def test_core_tutor_sees_only_their_own_newer_event(
        self, client, monkeypatch, db, admin_user, tutor_factory, cohort_factory, learner_factory,
        attendance_session_factory, secondary_enrollment_factory, request_factory,
    ):
        scenario = self._scenario(db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, secondary_enrollment_factory, request_factory)
        core_tutor = scenario["core_tutor"]
        learner = scenario["learner"]

        _as_tutor(client, monkeypatch, core_tutor["tutorId"], core_tutor["userId"])
        detail_response = client.get(f"/api/reports/engagement-recency/{learner['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["latestEngagementDate"] == "2026-09-15"

    def test_admin_sees_the_newer_event(
        self, client, monkeypatch, db, admin_user, tutor_factory, cohort_factory, learner_factory,
        attendance_session_factory, secondary_enrollment_factory, request_factory,
    ):
        scenario = self._scenario(db, admin_user, tutor_factory, cohort_factory, learner_factory, attendance_session_factory, secondary_enrollment_factory, request_factory)
        learner = scenario["learner"]

        _as_admin(client, monkeypatch)
        detail_response = client.get(f"/api/reports/engagement-recency/{learner['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["latestEngagementDate"] == "2026-09-15"
