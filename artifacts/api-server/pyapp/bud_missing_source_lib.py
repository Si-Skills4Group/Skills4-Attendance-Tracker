"""Stage 2, item 6: a reverse-presence check for previously-linked,
internally active learners whose linked Bud learning-plan row has gone
missing from public.learner_progress.

This is the one piece of Stage 2 that persists its own state
(bud_missing_source_exception, see bootstrap.py) rather than computing
everything fresh on every read like the rest of allocation_reconciliation_
lib -- the whole point is tracking this OVER TIME: when a row first went
missing, whether it's still missing as of the latest check, and when (if
ever) it came back. sync_missing_source_exceptions is idempotent and safe
to call as often as detection is triggered (an upsert against a partial
unique index, never a plain INSERT) -- repeated calls, however many, never
create a duplicate OPEN exception for the same learner+plan pair.

This module NEVER infers completion, withdrawal, or deletion from a
missing row, and never writes to learners.status or any other business
data -- disappearance from an externally-owned, unverifiable-for-
completeness source table is evidence for a human to review, not evidence
of what actually happened to the learner. See fetch_source_info's own
existing completeness caveat in allocation_reconciliation_lib.py -- the
same caveat applies here and is surfaced alongside every result, not just
asserted once and forgotten.

Stage 2 verification pass, item 1: GET must be read-only, so detection
(sync_missing_source_exceptions) is never called from the GET route --
only from refresh_missing_source_exceptions, which the admin-only POST
/bud-missing-source/refresh route calls. bud_missing_source_refresh_status
is a single-row table (bootstrap.py) tracking when detection last ran and
whether it last succeeded, WITHOUT discarding the last successful results
on a failure: a failed attempt only ever writes last_attempted_at/
last_error, never touching last_succeeded_at/last_newly_opened/
last_resolved -- so the interface can always show "last known good"
figures even after a failed refresh attempt."""
from __future__ import annotations

from fastapi import HTTPException, Request

from .audit import write_audit_log
from .bud_sync_lib import _fetch_bud_rows


def sync_missing_source_exceptions(cur) -> dict:
    """Upserts the OPEN/resolved state of every reverse-presence exception
    against the CURRENT source. Read-only against learners/learner_progress/
    bud_learner_link; the only writes are to bud_missing_source_exception
    itself, which holds no business data (no status, no allocation, nothing
    an admin or a report elsewhere would read as fact about the learner).

    The whole detection pass -- every open/resolve write -- runs in ONE
    transaction: if anything fails partway through (a DB error, a
    connectivity blip), everything rolls back rather than leaving some
    exceptions updated and others not. Nothing here can ever mark a
    learner's status regardless of success or failure, so a failed or
    partial run cannot be mistaken for -- or produce -- a confirmed learner
    departure; at worst, the next successful call simply catches up."""
    with cur.connection.transaction():
        cur.execute(
            """
            SELECT bl.internal_learner_id AS "internalLearnerId", bl.bud_learning_plan_id AS "budLearningPlanId",
                   l.learner_ref AS "learnerRef"
            FROM bud_learner_link bl
            JOIN learners l ON l.id = bl.internal_learner_id AND l.deleted_at IS NULL AND l.status = 'active'
            """
        )
        linked_active = cur.fetchall()

        cur.execute("SELECT learning_plan_id FROM public.learner_progress WHERE learning_plan_id IS NOT NULL")
        present_plan_ids = {r["learning_plan_id"] for r in cur.fetchall()}

        newly_missing = 0

        for link in linked_active:
            plan_id = link["budLearningPlanId"]
            if plan_id in present_plan_ids:
                continue
            cur.execute(
                """
                INSERT INTO bud_missing_source_exception
                    (internal_learner_id, bud_learning_plan_id, learner_reference, status, last_confirmed_missing_at)
                VALUES (%s, %s, %s, 'open', now())
                ON CONFLICT (internal_learner_id, bud_learning_plan_id) WHERE status = 'open'
                DO UPDATE SET last_confirmed_missing_at = now()
                RETURNING (xmax = 0) AS "wasInsert"
                """,
                (link["internalLearnerId"], plan_id, link["learnerRef"]),
            )
            if cur.fetchone()["wasInsert"]:
                newly_missing += 1

        # Resolve any OPEN exception whose plan_id is back in the source --
        # history is kept (status flips to 'resolved', the row is never
        # deleted), so a later re-disappearance opens a fresh row rather
        # than silently reusing a stale one.
        cur.execute(
            """
            UPDATE bud_missing_source_exception
            SET status = 'resolved', resolved_at = now(), updated_at = now()
            WHERE status = 'open' AND bud_learning_plan_id = ANY(%s)
            RETURNING id
            """,
            (list(present_plan_ids),),
        )
        resolved = len(cur.fetchall())

    return {"newlyOpened": newly_missing, "resolved": resolved}


def refresh_missing_source_exceptions(cur, request: Request, session: dict) -> dict:
    """The ONLY caller of sync_missing_source_exceptions -- the admin-only
    POST /bud-missing-source/refresh route. last_attempted_at is written
    FIRST, as its own statement (autocommitted immediately, surviving any
    later failure) so there is always a record an attempt was made, even
    if detection itself then fails; last_succeeded_at and the result
    counts are only ever written on success, so a failed refresh can never
    overwrite -- or be mistaken for -- the last known-good results."""
    cur.execute(
        "UPDATE bud_missing_source_refresh_status SET last_attempted_at = now(), last_triggered_by = %s WHERE id = 1",
        (session["userId"],),
    )
    try:
        result = sync_missing_source_exceptions(cur)
    except Exception as exc:
        cur.execute(
            "UPDATE bud_missing_source_refresh_status SET last_error = %s WHERE id = 1",
            (str(exc),),
        )
        write_audit_log(
            request, action="bud_missing_source_refresh_failed", entity_type="bud_missing_source_refresh_status",
            new_value={"error": str(exc)},
        )
        raise
    cur.execute(
        """
        UPDATE bud_missing_source_refresh_status
        SET last_succeeded_at = now(), last_newly_opened = %s, last_resolved = %s, last_error = NULL
        WHERE id = 1
        """,
        (result["newlyOpened"], result["resolved"]),
    )
    write_audit_log(
        request, action="bud_missing_source_refresh_succeeded", entity_type="bud_missing_source_refresh_status",
        new_value=result, cur=cur,
    )
    return result


def get_missing_source_refresh_status(cur) -> dict:
    """Read-only. Never triggers detection -- just reports the state
    refresh_missing_source_exceptions last left behind."""
    cur.execute(
        """
        SELECT last_attempted_at AS "lastAttemptedAt", last_triggered_by AS "lastTriggeredBy",
               last_succeeded_at AS "lastSucceededAt", last_newly_opened AS "lastNewlyOpened",
               last_resolved AS "lastResolved", last_error AS "lastError"
        FROM bud_missing_source_refresh_status WHERE id = 1
        """
    )
    status = cur.fetchone()
    if status is None:
        # Defensive only -- bootstrap.py seeds this row unconditionally;
        # this should be unreachable outside a fresh, not-yet-bootstrapped
        # database.
        raise HTTPException(status_code=500, detail="Missing-source refresh status row not found")
    return status


def list_missing_source_exceptions(cur, status: str | None = "open") -> list[dict]:
    """Read-only listing. otherPlansForPersonStillPresent and
    personEntirelyAbsentFromSource are computed FRESH at read time against
    the current source (never stored -- they can change between checks
    independently of whether the specific linked plan itself is missing),
    so this always reflects the current source snapshot rather than a
    possibly-stale value captured when the exception was first opened."""
    clauses = []
    params: list = []
    if status:
        clauses.append("e.status = %s")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    cur.execute(
        f"""
        SELECT e.id, e.internal_learner_id AS "internalLearnerId", e.bud_learning_plan_id AS "budLearningPlanId",
               e.learner_reference AS "learnerReference", e.status, e.first_detected_at AS "firstDetectedAt",
               e.last_confirmed_missing_at AS "lastConfirmedMissingAt", e.resolved_at AS "resolvedAt",
               l.first_name AS "firstName", l.last_name AS "lastName", l.status AS "learnerStatus"
        FROM bud_missing_source_exception e
        LEFT JOIN learners l ON l.id = e.internal_learner_id
        {where}
        ORDER BY e.first_detected_at DESC
        """,
        params,
    )
    exceptions = cur.fetchall()
    if not exceptions:
        return []

    bud_rows = _fetch_bud_rows(cur)
    references_present = {r["learnerReference"] for r in bud_rows if r.get("learnerReference")}
    plan_count_by_reference: dict[str, int] = {}
    for r in bud_rows:
        ref = r.get("learnerReference")
        if ref:
            plan_count_by_reference[ref] = plan_count_by_reference.get(ref, 0) + 1

    results = []
    for exc in exceptions:
        reference = exc["learnerReference"]
        person_present_elsewhere = bool(reference and reference in references_present)
        results.append({
            **exc,
            "learnerName": f"{exc['firstName']} {exc['lastName']}" if exc["firstName"] else None,
            # Reverse-presence classification, computed fresh -- never
            # inferred from the missing row alone:
            # - otherPlansForPersonStillPresent: the linked plan itself is
            #   gone, but this person's OWN reference still appears on at
            #   least one other current source row.
            # - personEntirelyAbsentFromSource: this person's reference
            #   does not appear ANYWHERE in the current source at all.
            "otherPlansForPersonStillPresent": person_present_elsewhere,
            "personEntirelyAbsentFromSource": bool(reference) and not person_present_elsewhere,
            "sourceReferenceUnknown": not bool(reference),
        })
    return results
