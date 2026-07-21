"""Postgres-backed rate limiting for sensitive authenticated actions (CSV
upload, import confirmation, report export, historical attendance edits,
role changes).

Deliberately separate from pyapp/login_rate_limit.py, which is IP-keyed for
an anonymous, pre-auth endpoint (POST /auth/login, itself unreachable in
production since AUTH_MODE=entra) and already has its own tests covering
its exact table/behavior -- left untouched rather than risk breaking a
working, tested control for a cosmetic "one shared implementation" refactor.
This module targets a different shape of problem: authenticated endpoints,
rate-limited per acting user rather than per IP (a shared office IP with
several tutors behind it shouldn't throttle all of them together).

Same cross-replica-safe design as the login limiter: no Redis or other
shared cache exists in this app, so correctness across multiple Container
Apps replicas comes from a transaction-scoped Postgres advisory lock
(auto-released on commit/rollback) around a count-then-insert, all replicas
talking to the same Postgres. This is an interim, single-database-backed
control -- fine at this app's current traffic, but the recommended
long-term shared approach if traffic grows enough to matter is Azure API
Management's built-in rate limiting or a Redis-backed limiter; deliberately
not introducing either as a new infrastructure dependency here.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException

from .audit import write_audit_log
from .correlation import get_current_request


def check_and_record_rate_limit(
    cur, *, action: str, rate_key: str, max_attempts: int, window_minutes: int, now: datetime | None = None
) -> None:
    """Must be called inside `with cur.connection.transaction():` -- see
    pyapp/login_rate_limit.py's identical requirement for why. Raises
    HTTPException(429) if `rate_key` is already over budget for `action`
    in the current window; otherwise records this attempt."""
    now = now or datetime.now()
    window_start = now - timedelta(minutes=window_minutes)

    # Serializes concurrent attempts for the SAME (action, rate_key) across
    # every replica; different actions/keys proceed concurrently as normal.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{action}:{rate_key}",))

    cur.execute(
        "DELETE FROM rate_limit_attempts WHERE action = %s AND rate_key = %s AND attempted_at < %s",
        (action, rate_key, window_start),
    )
    cur.execute(
        "SELECT count(*)::int AS count FROM rate_limit_attempts WHERE action = %s AND rate_key = %s",
        (action, rate_key),
    )
    if cur.fetchone()["count"] >= max_attempts:
        request = get_current_request()
        if request is not None:
            # Not passed cur=cur -- this sits inside the caller's
            # transaction, which rolls back once the HTTPException below
            # propagates; the audit row must survive that rollback.
            write_audit_log(
                request, action="rate_limited", entity_type="security",
                new_value={"limitedAction": action, "rateKey": rate_key, "maxAttempts": max_attempts, "windowMinutes": window_minutes},
            )
        raise HTTPException(
            status_code=429,
            detail=f"You've made too many {action.replace('_', ' ')} requests. Please wait and try again.",
        )

    cur.execute("INSERT INTO rate_limit_attempts (action, rate_key, attempted_at) VALUES (%s, %s, %s)", (action, rate_key, now))


def prune_stale_rate_limit_attempts(cur, as_of: datetime | None = None, max_window_minutes: int = 24 * 60) -> None:
    """Boot-time sweep, same lazy convention as prune_stale_login_attempts
    -- keeps the table from growing unboundedly. max_window_minutes should
    be at least as large as the largest window_minutes passed to
    check_and_record_rate_limit anywhere in the app."""
    as_of = as_of or datetime.now()
    cur.execute(
        "DELETE FROM rate_limit_attempts WHERE attempted_at < %s",
        (as_of - timedelta(minutes=max_window_minutes),),
    )
