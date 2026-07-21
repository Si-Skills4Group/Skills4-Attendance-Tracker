"""Tests for the generalized Postgres-backed rate limiter (pyapp/rate_limit.py)
used by CSV upload, import confirmation, report export, historical
attendance edits, and user-role changes -- pyapp/login_rate_limit.py has its
own dedicated tests for the IP-keyed login-specific limiter this generalizes
the pattern from."""
import os

import pytest
from fastapi import HTTPException

from pyapp.rate_limit import check_and_record_rate_limit, prune_stale_rate_limit_attempts


def _attempt(cur, action: str, rate_key: str, max_attempts: int = 3, window_minutes: int = 60, now=None) -> None:
    with cur.connection.transaction():
        check_and_record_rate_limit(cur, action=action, rate_key=rate_key, max_attempts=max_attempts, window_minutes=window_minutes, now=now)


@pytest.fixture
def rate_key():
    return f"test-key-{os.urandom(4).hex()}"


def test_allows_attempts_up_to_the_limit(db, rate_key):
    for _ in range(3):
        _attempt(db, "csv_upload", rate_key)  # must not raise


def test_rejects_the_attempt_over_the_limit(db, rate_key):
    for _ in range(3):
        _attempt(db, "csv_upload", rate_key)
    with pytest.raises(HTTPException) as exc:
        _attempt(db, "csv_upload", rate_key)
    assert exc.value.status_code == 429


def test_different_actions_have_independent_budgets(db, rate_key):
    """The same user hitting export a lot must not also throttle their
    CSV uploads -- each action is its own bucket."""
    for _ in range(3):
        _attempt(db, "csv_upload", rate_key)
    for _ in range(3):
        _attempt(db, "report_export", rate_key)  # independent budget, must not raise


def test_different_keys_have_independent_budgets(db):
    key_a, key_b = f"test-key-{os.urandom(4).hex()}", f"test-key-{os.urandom(4).hex()}"
    for _ in range(3):
        _attempt(db, "csv_upload", key_a)
    for _ in range(3):
        _attempt(db, "csv_upload", key_b)  # independent budget, must not raise


def test_window_expiry_frees_up_budget(db, rate_key):
    import datetime

    old = datetime.datetime.now() - datetime.timedelta(minutes=61)
    for _ in range(3):
        _attempt(db, "csv_upload", rate_key, window_minutes=60, now=old)
    # A fresh attempt "now" (61 minutes later) must not see the expired
    # attempts and must be allowed.
    _attempt(db, "csv_upload", rate_key, window_minutes=60)


def test_rate_limited_attempt_is_audited(db, rate_key, request_factory, admin_user):
    from pyapp.correlation import _current_request

    request = request_factory(session=admin_user)
    token = _current_request.set(request)
    try:
        for _ in range(3):
            _attempt(db, "csv_upload", rate_key)
        with pytest.raises(HTTPException):
            _attempt(db, "csv_upload", rate_key)
    finally:
        _current_request.reset(token)

    db.execute(
        "SELECT action, new_value FROM audit_logs WHERE action = 'rate_limited' ORDER BY id DESC LIMIT 1"
    )
    row = db.fetchone()
    assert row is not None
    assert rate_key in row["new_value"]


def test_prune_removes_only_stale_rows(db, rate_key):
    import datetime

    old = datetime.datetime.now() - datetime.timedelta(hours=25)
    _attempt(db, "csv_upload", rate_key, now=old)
    fresh_key = f"test-key-{os.urandom(4).hex()}"
    _attempt(db, "csv_upload", fresh_key)

    prune_stale_rate_limit_attempts(db, max_window_minutes=24 * 60)

    db.execute("SELECT rate_key FROM rate_limit_attempts WHERE rate_key IN (%s, %s)", (rate_key, fresh_key))
    remaining = {row["rate_key"] for row in db.fetchall()}
    assert remaining == {fresh_key}
