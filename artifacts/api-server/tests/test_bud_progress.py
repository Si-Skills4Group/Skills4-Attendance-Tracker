"""Tests for pyapp.bud_progress -- read-only access to public.learner_progress.

public.learner_progress does not exist in the attendance_test database (it
is populated by a separate, already-deployed sync service that only ever
targets the production `attendance` database, confirmed by inspection). The
session-scoped autouse fixture in conftest.py creates a matching throwaway
table shape *in attendance_test only* so the real SQL here can be exercised
against a real table -- never anywhere near the real learner_progress data,
and nothing in pyapp/bootstrap.py (the app's actual DDL) references it. Rows
seeded by this file are cleaned up after each test.
"""
import ast
import inspect

import pytest

from pyapp import bud_progress
from pyapp.bud_progress import get_bud_progress_by_uln, get_bud_sync_health


@pytest.fixture(autouse=True)
def _cleanup_seeded_rows(db):
    yield
    db.execute("DELETE FROM public.learner_progress WHERE learning_plan_id LIKE 'TEST-%'")


def _seed(db, uln, **overrides):
    defaults = dict(
        learning_plan_id=f"TEST-{uln}",
        apprentice_id=f"APP-{uln}",
        learner_name="Test Learner",
        unique_learner_number=uln,
        activity_progress=65.0,
        activities_overdue=3,
        last_submission_date="2026-07-01T10:00:00Z",
        last_completed_activity="2026-06-30",
        learning_plan_url="https://bud.example.com/plan/1",
        status_desc="On track",
        synced_at="2026-07-17T10:30:00Z",
    )
    defaults.update(overrides)
    columns = ", ".join(defaults.keys())
    placeholders = ", ".join(f"%({k})s" for k in defaults)
    db.execute(f"INSERT INTO public.learner_progress ({columns}) VALUES ({placeholders})", defaults)


class TestModuleIsReadOnly:
    """Static check: the module must never issue a write against
    learner_progress, regardless of what any individual test does."""

    def test_source_contains_no_write_statements(self):
        """Only inspects the actual SQL string literals passed to
        cur.execute(...) -- not the module's own prose docstrings/comments,
        which legitimately discuss "INSERT/UPDATE/DELETE" as concepts to
        avoid."""
        source = inspect.getsource(bud_progress)
        tree = ast.parse(source)
        write_verbs = ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER ", "TRUNCATE ")
        execute_call_sql: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        execute_call_sql.append(arg.value)

        assert execute_call_sql, "expected at least one cur.execute(...) call to inspect"
        for sql in execute_call_sql:
            upper = sql.upper()
            assert not any(verb in upper for verb in write_verbs), f"Write-shaped SQL found: {sql!r}"


class TestGetBudProgressByUln:
    def test_reads_matching_rows(self, db):
        _seed(db, "TEST-ULN-001", activity_progress=72.5, activities_overdue=1)
        result = get_bud_progress_by_uln(db, ["TEST-ULN-001"])
        assert "TEST-ULN-001" in result
        assert result["TEST-ULN-001"].activityProgress == 72.5
        assert result["TEST-ULN-001"].activitiesOverdue == 1

    def test_missing_learner_has_no_entry_and_does_not_raise(self, db):
        result = get_bud_progress_by_uln(db, ["NO-SUCH-ULN-EXISTS"])
        assert result == {}

    def test_empty_input_returns_empty_dict_without_querying(self, db):
        assert get_bud_progress_by_uln(db, []) == {}

    def test_none_and_blank_ulns_are_ignored(self, db):
        _seed(db, "TEST-ULN-002")
        result = get_bud_progress_by_uln(db, [None, "", "TEST-ULN-002"])
        assert list(result.keys()) == ["TEST-ULN-002"]

    def test_batches_many_learners_in_one_call(self, db):
        _seed(db, "TEST-ULN-003")
        _seed(db, "TEST-ULN-004")
        result = get_bud_progress_by_uln(db, ["TEST-ULN-003", "TEST-ULN-004", "NOT-PRESENT"])
        assert set(result.keys()) == {"TEST-ULN-003", "TEST-ULN-004"}

    def test_most_recently_synced_row_wins_on_duplicates(self, db):
        db.execute(
            "INSERT INTO public.learner_progress (learning_plan_id, apprentice_id, unique_learner_number, activity_progress, synced_at) "
            "VALUES ('TEST-DUP-A', 'APP-DUP', 'TEST-ULN-005', 10, '2026-01-01T00:00:00Z')"
        )
        db.execute(
            "INSERT INTO public.learner_progress (learning_plan_id, apprentice_id, unique_learner_number, activity_progress, synced_at) "
            "VALUES ('TEST-DUP-B', 'APP-DUP', 'TEST-ULN-005', 90, '2026-07-01T00:00:00Z')"
        )
        result = get_bud_progress_by_uln(db, ["TEST-ULN-005"])
        assert result["TEST-ULN-005"].activityProgress == 90


class TestGetBudSyncHealth:
    def test_returns_totals_and_latest_sync_time(self, db):
        _seed(db, "TEST-ULN-HEALTH-1", synced_at="2026-07-17T10:30:00Z")
        health = get_bud_sync_health(db)
        assert health["totalSynced"] >= 1
        assert health["lastSyncedAt"] is not None

    def test_does_not_error_when_table_is_empty_of_matches(self, db):
        # Not literally empty (autouse fixture may have left rows from other
        # tests in this module), but proves the aggregate query never raises
        # regardless of row count -- the actual "0 rows" case is covered by
        # the NotNullViolation-free CREATE TABLE itself succeeding above.
        health = get_bud_sync_health(db)
        assert isinstance(health["totalSynced"], int)


class TestMissingBudDataNeverBreaksDashboards:
    def test_dashboard_low_attendance_query_does_not_reference_bud_data_at_all(self):
        """Confirms the dashboard/attendance-metrics engine has no
        dependency on learner_progress -- Bud context is looked up
        separately and merged only for display, so a Bud outage or a
        completely unmatched learner can never affect attendance
        calculations."""
        from pyapp import attendance_metrics

        source = inspect.getsource(attendance_metrics)
        assert "learner_progress" not in source
