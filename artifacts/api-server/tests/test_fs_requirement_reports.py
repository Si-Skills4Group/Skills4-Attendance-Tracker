"""Stage 5: HTTP-level permission checks for the Functional Skills
requirement upload/allocation feature -- admin-only upload/preview/
commit/cancel/clear/allocation, and require_learner_access (not
admin-only) on the learner-detail read, matching item 6's "within
existing access permissions"."""
from fastapi import Request

from pyapp import auth as auth_module

_REAL_REQUIRE_AUTH = auth_module.require_auth


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


CSV_BYTES = b"learnerID,AIM\r\n"


class TestUploadPermissions:
    def test_tutor_cannot_upload(self, client, monkeypatch, tutor_factory):
        tutor = tutor_factory()
        _as_tutor(client, monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.post("/api/functional-skills-requirements/import-jobs", files={"file": ("f.csv", CSV_BYTES, "text/csv")})
        assert response.status_code == 403

    def test_admin_can_upload_and_get_the_template(self, client, monkeypatch):
        _as_admin(client, monkeypatch)
        template = client.get("/api/functional-skills-requirements/import-jobs/template")
        assert template.status_code == 200
        assert "learnerID" in template.json()["csv"]

        upload = client.post("/api/functional-skills-requirements/import-jobs", files={"file": ("f.csv", CSV_BYTES, "text/csv")})
        assert upload.status_code == 201


class TestCommitCancelClearPermissions:
    def test_tutor_cannot_confirm_cancel_or_clear(self, client, monkeypatch, tutor_factory, learner_factory):
        tutor = tutor_factory()
        learner = learner_factory()
        _as_admin(client, monkeypatch)
        upload = client.post("/api/functional-skills-requirements/import-jobs", files={"file": ("f.csv", CSV_BYTES, "text/csv")})
        job_id = upload.json()["id"]

        _as_tutor(client, monkeypatch, tutor["tutorId"], tutor["userId"])
        assert client.post(f"/api/functional-skills-requirements/import-jobs/{job_id}/confirm").status_code == 403
        assert client.post(f"/api/functional-skills-requirements/import-jobs/{job_id}/cancel").status_code == 403
        assert client.post(f"/api/functional-skills-requirements/learner/{learner['id']}/clear", json={"reason": "x"}).status_code == 403

    def test_admin_can_confirm_an_empty_job(self, client, monkeypatch):
        _as_admin(client, monkeypatch)
        upload = client.post("/api/functional-skills-requirements/import-jobs", files={"file": ("f.csv", CSV_BYTES, "text/csv")})
        job_id = upload.json()["id"]
        response = client.post(f"/api/functional-skills-requirements/import-jobs/{job_id}/confirm")
        assert response.status_code == 200


class TestAllocationViewPermissions:
    def test_tutor_cannot_view_the_allocation_view(self, client, monkeypatch, tutor_factory):
        tutor = tutor_factory()
        _as_tutor(client, monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.get("/api/functional-skills-requirements/allocation")
        assert response.status_code == 403

    def test_admin_can_view_the_allocation_view(self, client, monkeypatch):
        _as_admin(client, monkeypatch)
        response = client.get("/api/functional-skills-requirements/allocation")
        assert response.status_code == 200
        assert "items" in response.json() and "total" in response.json()


class TestLearnerDetailReadPermissions:
    def test_tutor_can_see_their_own_learners_requirement(self, client, monkeypatch, tutor_factory, cohort_factory, learner_factory):
        tutor = tutor_factory()
        cohort = cohort_factory(tutor_id=tutor["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=tutor["tutorId"])

        _as_tutor(client, monkeypatch, tutor["tutorId"], tutor["userId"])
        response = client.get(f"/api/functional-skills-requirements/learner/{learner['id']}")
        assert response.status_code == 200
        assert response.json() is None

    def test_tutor_cannot_see_another_tutors_learner_requirement(self, client, monkeypatch, tutor_factory, cohort_factory, learner_factory):
        owner = tutor_factory()
        other = tutor_factory()
        cohort = cohort_factory(tutor_id=owner["tutorId"])
        learner = learner_factory(cohort_id=cohort["id"], tutor_id=owner["tutorId"])

        _as_tutor(client, monkeypatch, other["tutorId"], other["userId"])
        response = client.get(f"/api/functional-skills-requirements/learner/{learner['id']}")
        assert response.status_code == 403

    def test_admin_can_see_any_learners_requirement(self, client, monkeypatch, learner_factory):
        learner = learner_factory()
        _as_admin(client, monkeypatch)
        response = client.get(f"/api/functional-skills-requirements/learner/{learner['id']}")
        assert response.status_code == 200


class TestGetEndpointsAreReadOnly:
    def test_get_endpoints_perform_no_writes(self, db, client, monkeypatch, learner_factory):
        learner = learner_factory()
        _as_admin(client, monkeypatch)

        db.execute("SELECT count(*) AS n FROM learner_fs_requirements")
        before_req = db.fetchone()["n"]
        db.execute("SELECT count(*) AS n FROM fs_requirement_import_jobs")
        before_jobs = db.fetchone()["n"]

        client.get("/api/functional-skills-requirements/import-jobs/template")
        client.get(f"/api/functional-skills-requirements/learner/{learner['id']}")
        client.get("/api/functional-skills-requirements/allocation")

        db.execute("SELECT count(*) AS n FROM learner_fs_requirements")
        assert db.fetchone()["n"] == before_req
        db.execute("SELECT count(*) AS n FROM fs_requirement_import_jobs")
        assert db.fetchone()["n"] == before_jobs
