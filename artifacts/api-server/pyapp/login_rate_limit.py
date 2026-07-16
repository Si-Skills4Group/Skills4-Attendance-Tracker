"""Postgres-backed login rate limiting.

Replaces an in-memory per-process dict, which didn't actually work once the
Container App scaled to multiple replicas -- an attacker load-balanced
across replicas got roughly (replica count x budget) attempts instead of
just budget. No Redis/external cache exists in this app; this follows the
same lazy-sweep convention as scheduled_allocations_lib/learner_import_lib
(no cron, pruned inline), with cross-replica correctness coming from a
transaction-scoped Postgres advisory lock rather than any new
infrastructure.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException

WINDOW_SECONDS = 15 * 60
MAX_ATTEMPTS = 10


def check_and_record_login_attempt(cur, ip_key: str, now: datetime | None = None) -> None:
    """Must be called inside `with cur.connection.transaction():` -- the
    advisory lock is transaction-scoped (auto-released on commit/rollback,
    so a dropped request never leaves anything to clean up) and it, plus
    the count-then-insert, must be atomic across concurrent requests from
    the same IP hitting different replicas. Raises HTTPException(429) if
    the IP is already over budget for the current window."""
    now = now or datetime.now()
    window_start = now - timedelta(seconds=WINDOW_SECONDS)

    # Serializes concurrent attempts from the SAME ip_key across every
    # replica (they all talk to the same Postgres); attempts from
    # different IPs proceed concurrently as normal.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ip_key,))

    cur.execute(
        "DELETE FROM login_attempts WHERE ip_key = %s AND attempted_at < %s",
        (ip_key, window_start),
    )
    cur.execute("SELECT count(*)::int AS count FROM login_attempts WHERE ip_key = %s", (ip_key,))
    if cur.fetchone()["count"] >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    cur.execute("INSERT INTO login_attempts (ip_key, attempted_at) VALUES (%s, %s)", (ip_key, now))


def prune_stale_login_attempts(cur, as_of: datetime | None = None) -> None:
    """Boot-time sweep for rows belonging to IPs that never attempt again --
    the per-attempt prune in check_and_record_login_attempt only cleans the
    current IP's own rows, so this is what keeps the table from growing
    unboundedly from abandoned attackers or one-off client scripts."""
    as_of = as_of or datetime.now()
    cur.execute(
        "DELETE FROM login_attempts WHERE attempted_at < %s",
        (as_of - timedelta(seconds=WINDOW_SECONDS),),
    )
