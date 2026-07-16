"""Tests for the Postgres-backed login rate limiter (replaces the old
in-memory per-process dict, which didn't work across multiple Container
App replicas)."""

import os
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from pyapp.db import get_cursor
from pyapp.login_rate_limit import (
    MAX_ATTEMPTS,
    WINDOW_SECONDS,
    check_and_record_login_attempt,
    prune_stale_login_attempts,
)


def _attempt(cur, ip_key: str, now=None) -> None:
    with cur.connection.transaction():
        check_and_record_login_attempt(cur, ip_key, now=now)


@pytest.fixture
def ip_key():
    return f"test-ip-{os.urandom(4).hex()}"


@pytest.fixture(autouse=True)
def _cleanup_login_attempts(db):
    yield
    db.execute("DELETE FROM login_attempts WHERE ip_key LIKE 'test-ip-%'")


def test_allows_attempts_up_to_the_limit(db, ip_key):
    for _ in range(MAX_ATTEMPTS):
        _attempt(db, ip_key)  # must not raise


def test_blocks_the_attempt_after_the_limit(db, ip_key):
    for _ in range(MAX_ATTEMPTS):
        _attempt(db, ip_key)
    with pytest.raises(HTTPException) as exc:
        _attempt(db, ip_key)
    assert exc.value.status_code == 429


def test_a_blocked_attempt_is_not_itself_recorded(db, ip_key):
    """The 429 must roll back before the INSERT, so retrying later (once
    the window rolls forward) isn't penalized by the rejected attempt
    itself on top of the real ones."""
    for _ in range(MAX_ATTEMPTS):
        _attempt(db, ip_key)
    with pytest.raises(HTTPException):
        _attempt(db, ip_key)
    db.execute("SELECT count(*)::int AS c FROM login_attempts WHERE ip_key = %s", (ip_key,))
    assert db.fetchone()["c"] == MAX_ATTEMPTS


def test_different_ips_have_independent_budgets(db, ip_key):
    other_ip = f"test-ip-{os.urandom(4).hex()}"
    for _ in range(MAX_ATTEMPTS):
        _attempt(db, ip_key)
    _attempt(db, other_ip)  # must not raise -- separate budget
    db.execute("DELETE FROM login_attempts WHERE ip_key = %s", (other_ip,))


def test_attempts_outside_the_window_do_not_count_towards_the_limit(db, ip_key):
    old_time = datetime.now() - timedelta(seconds=WINDOW_SECONDS + 60)
    for _ in range(MAX_ATTEMPTS):
        _attempt(db, ip_key, now=old_time)
    _attempt(db, ip_key)  # must not raise -- the stale ones get pruned first


def test_prune_stale_login_attempts_removes_old_rows_but_keeps_recent_ones(db, ip_key):
    old_time = datetime.now() - timedelta(seconds=WINDOW_SECONDS + 60)
    db.execute("INSERT INTO login_attempts (ip_key, attempted_at) VALUES (%s, %s)", (ip_key, old_time))
    _attempt(db, ip_key)  # a recent, in-window attempt

    prune_stale_login_attempts(db)

    db.execute("SELECT count(*)::int AS c FROM login_attempts WHERE ip_key = %s", (ip_key,))
    assert db.fetchone()["c"] == 1


def test_budget_is_shared_across_separate_connections_not_per_process(ip_key):
    """Simulates two Container App replicas as two independent pooled
    connections -- this is exactly the case the old in-memory dict got
    wrong (each replica had its own budget). Both connections must share
    one count against Postgres."""
    with get_cursor() as cur1:
        for _ in range(5):
            _attempt(cur1, ip_key)

    with get_cursor() as cur2:
        for _ in range(5):
            _attempt(cur2, ip_key)
        # 10 attempts total across the two "replicas" -- the 11th, from
        # either connection, must be blocked.
        with pytest.raises(HTTPException) as exc:
            _attempt(cur2, ip_key)
        assert exc.value.status_code == 429
