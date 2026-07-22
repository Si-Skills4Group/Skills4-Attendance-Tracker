"""Security and Bud-source-safety guarantees: every /api/bud-sync/* route
is Administrator-only (a Tutor gets a real 403 over HTTP, not just a
frontend route guard), unauthenticated callers get 401, and this module
never issues a write against public.learner_progress and never builds an
alternate Bud API client."""
import ast
import inspect

from pyapp import auth as auth_module, bud_sync_lib


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


class TestTutorCannotAccessAnyBudSyncRoute:
    def test_status(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.get("/api/bud-sync/status").status_code == 403

    def test_establish_baseline(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.post("/api/bud-sync/baseline", json={}).status_code == 403

    def test_reset_baseline(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.post("/api/bud-sync/baseline/reset", json={"reason": "x"}).status_code == 403

    def test_baseline_history(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.get("/api/bud-sync/baseline/history").status_code == 403

    def test_preview(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.post("/api/bud-sync/preview").status_code == 403

    def test_list_jobs(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.get("/api/bud-sync/jobs").status_code == 403

    def test_get_job(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.get("/api/bud-sync/jobs/1").status_code == 403

    def test_list_items(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.get("/api/bud-sync/jobs/1/items").status_code == 403

    def test_update_item(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.patch("/api/bud-sync/jobs/1/items/1", json={}).status_code == 403

    def test_commit(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.post("/api/bud-sync/jobs/1/commit", json={"itemIds": [1], "approvalReason": "x"}).status_code == 403

    def test_unmatched_pre_baseline(self, client, monkeypatch):
        _as_tutor(monkeypatch)
        assert client.get("/api/bud-sync/unmatched-pre-baseline").status_code == 403


class TestUnauthenticatedCannotAccessAnyBudSyncRoute:
    def test_status_without_auth(self, client):
        response = client.get("/api/bud-sync/status")
        assert response.status_code == 401

    def test_commit_without_auth(self, client):
        response = client.post("/api/bud-sync/jobs/1/commit", json={"itemIds": [1], "approvalReason": "x"})
        assert response.status_code == 401


class TestAdminCanReachTheStatusEndpoint:
    def test_status_over_http(self, client, monkeypatch):
        _as_admin(monkeypatch)
        response = client.get("/api/bud-sync/status")
        assert response.status_code == 200
        assert "activeBaseline" in response.json()


class TestBudSourceRemainsReadOnly:
    def test_bud_sync_lib_contains_no_write_statements_against_learner_progress(self):
        """Same static-AST technique as test_bud_progress.py's own
        TestModuleIsReadOnly -- inspects only the SQL string literals passed
        to cur.execute(...), not prose comments/docstrings."""
        source = inspect.getsource(bud_sync_lib)
        tree = ast.parse(source)
        write_verbs = ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER ", "TRUNCATE ")
        execute_call_sql: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        execute_call_sql.append(arg.value)

        write_statements_against_learner_progress = [
            sql for sql in execute_call_sql
            if "learner_progress" in sql.lower() and any(verb in sql.upper() for verb in write_verbs)
        ]
        assert write_statements_against_learner_progress == []

    def test_no_alternate_bud_api_client_or_http_call_exists(self):
        """This trial reads Bud data exclusively through
        public.learner_progress -- confirms bud_sync_lib never makes an
        HTTP call of its own (no requests/httpx usage), which would imply a
        second, unauthorised Bud integration path."""
        source = inspect.getsource(bud_sync_lib)
        assert "requests." not in source
        assert "httpx." not in source
        assert "urlopen" not in source

    def test_no_scheduled_job_or_cron_infrastructure_exists(self):
        """Confirms this module never registers a background task, cron
        trigger, or scheduler -- every sync run is a direct, synchronous
        Administrator action (preview/commit), never unattended."""
        source = inspect.getsource(bud_sync_lib)
        for forbidden in ("BackgroundTasks", "add_job", "schedule.every", "Celery", "APScheduler"):
            assert forbidden not in source
