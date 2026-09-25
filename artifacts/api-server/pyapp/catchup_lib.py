"""Stage 3: tutor-confirmed catch-up completion for a recorded absence.

Business requirement: a tutor can confirm that an absent learner
subsequently watched the session recording or completed an appropriate
catch-up activity. The original absence remains recorded, and reporting
distinguishes live attendance from subsequent catch-up.

Hard rules this module enforces everywhere:
- The original attendance_records row (status, hours_attended) is NEVER
  written by anything in this module. Catch-up lives entirely in its own
  table (attendance_catchup, bootstrap.py) linked by (session_id,
  learner_id) -- never a column bolted onto attendance_records.
- This is TUTOR CONFIRMATION, not automated video-view tracking -- every
  write requires a human-entered note and, for a correction or
  revocation, a reason.
- Eligible original statuses are exactly absent_authorised and
  absent_unauthorised. An unrecorded status (NULL) is not an absence and
  is never catch-up eligible, regardless of how long it's been
  unrecorded.
- One row per (session_id, learner_id) for the WHOLE lifecycle (recorded
  -> corrected -> revoked -> re-recorded) -- never a fresh row per
  submission, and never hard-deleted. Every transition is additionally
  audited via audit_logs (entity_type="attendance_catchup",
  entity_id=<this row's id>), which is what "view history" reads.
- "Effective" (whether a catch-up currently counts toward participation)
  is NEVER stored -- it is re-derived live, every time, from the catch-up
  row's own status plus the CURRENT attendance_records.status and the
  session's CURRENT cancelled state. A later correction of the original
  attendance to present/late/another ineligible status, or a session
  cancellation, therefore automatically stops an existing catch-up row
  from counting, without this module ever needing to notice or rewrite
  anything when that happens.
- Concurrency: every write locks the relevant attendance_records row with
  SELECT ... FOR UPDATE inside the same transaction as the catch-up write,
  so a concurrent attendance correction (which also UPDATEs that row) is
  serialised against it rather than raced -- whichever transaction
  commits first is what the other sees. The (session_id, learner_id)
  UNIQUE constraint on attendance_catchup is the backstop against two
  concurrent "Record catch-up" submissions producing duplicate rows; the
  explicit "already recorded" check before that is the friendly, expected
  path, not the only protection.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Literal

from fastapi import HTTPException, Request

from .attendance_metrics import uk_today
from .audit import write_audit_log
from .session_register_lib import ensure_expected_learners_snapshot

CatchupMethod = Literal["recording_watched", "activity_completed"]

# The only two original statuses catch-up may ever be recorded against.
# Deliberately a plain set, not derived from ZERO_HOURS_STATUSES or any
# other existing grouping, which include not_expected/withdrawn/bil --
# none of those are absences at all.
ELIGIBLE_ABSENCE_STATUSES = {"absent_authorised", "absent_unauthorised"}

CATCHUP_SELECT = """
    SELECT id, session_id AS "sessionId", learner_id AS "learnerId", status,
           completion_date AS "completionDate", method, note,
           original_status_at_recording AS "originalStatusAtRecording",
           recorded_by AS "recordedBy", recorded_at AS "recordedAt",
           last_updated_by AS "lastUpdatedBy", updated_at AS "updatedAt",
           revoked_at AS "revokedAt", revoked_by AS "revokedBy", revocation_reason AS "revocationReason"
    FROM attendance_catchup
"""


def get_catchup(cur, session_id: int, learner_id: int) -> dict | None:
    cur.execute(f"{CATCHUP_SELECT} WHERE session_id = %s AND learner_id = %s", (session_id, learner_id))
    return cur.fetchone()


def _derive_state(catchup: dict | None, current_status: str | None, session_cancelled: bool) -> dict:
    """The one place "does this catch-up currently count" is decided --
    read this before touching any reporting/list code that shows catch-up
    state. Never trust a stored flag; always call this against fresh
    inputs."""
    if catchup is None:
        return {"catchup": None, "effective": False, "ineligibleReason": None}
    if catchup["status"] == "revoked":
        return {"catchup": catchup, "effective": False, "ineligibleReason": "revoked"}
    if session_cancelled:
        return {"catchup": catchup, "effective": False, "ineligibleReason": "session_cancelled"}
    if current_status not in ELIGIBLE_ABSENCE_STATUSES:
        return {"catchup": catchup, "effective": False, "ineligibleReason": "original_status_changed"}
    return {"catchup": catchup, "effective": True, "ineligibleReason": None}


def get_catchup_with_state(cur, session_id: int, learner_id: int) -> dict:
    """Read-only. Used by the single-row GET endpoint and by the record/
    correct/revoke endpoints' response -- always returns the SAME shape,
    with the live-derived effective/ineligibleReason alongside whatever
    catch-up row (if any) exists."""
    cur.execute(
        "SELECT status = 'cancelled' AS cancelled FROM attendance_sessions WHERE id = %s",
        (session_id,),
    )
    session_row = cur.fetchone()
    session_cancelled = bool(session_row and session_row["cancelled"])

    cur.execute(
        "SELECT status FROM attendance_records WHERE session_id = %s AND learner_id = %s",
        (session_id, learner_id),
    )
    attendance_row = cur.fetchone()
    current_status = attendance_row["status"] if attendance_row else None

    catchup = get_catchup(cur, session_id, learner_id)
    return _derive_state(catchup, current_status, session_cancelled)


def _fetch_session_and_lock_absence(cur, session_id: int, learner_id: int) -> tuple[dict, dict | None]:
    """Must be called inside the caller's own `with cur.connection.
    transaction():`. Locks the attendance_records row (if one exists) for
    the rest of that transaction -- a concurrent attendance correction
    (which also UPDATEs this same row) blocks until this transaction
    commits or rolls back, and vice versa, so neither can act on a
    now-stale view of the other's change."""
    cur.execute(
        """
        SELECT s.id, s.cohort_id AS "cohortId", s.session_date AS "sessionDate", s.status
        FROM attendance_sessions s WHERE s.id = %s AND s.deleted_at IS NULL
        """,
        (session_id,),
    )
    session_row = cur.fetchone()
    if not session_row:
        raise HTTPException(status_code=404, detail="Attendance session not found")

    # Same "ensure the frozen roster exists" call every other session read/
    # write endpoint makes first (session_register_lib.py) -- without it, a
    # session nobody has opened a register for yet would have no
    # session_expected_learners rows at all, and the "was this learner
    # expected" eligibility check below would reject for the wrong reason.
    ensure_expected_learners_snapshot(cur, session_row["id"], session_row["cohortId"], session_row["sessionDate"])

    cur.execute(
        'SELECT id, status, hours_attended AS "hoursAttended" FROM attendance_records '
        "WHERE session_id = %s AND learner_id = %s FOR UPDATE",
        (session_id, learner_id),
    )
    attendance_row = cur.fetchone()
    return session_row, attendance_row


def _validate_eligibility(cur, session_id: int, learner_id: int, session_row: dict, attendance_row: dict | None, completion_date: date) -> None:
    cur.execute(
        "SELECT id FROM session_expected_learners WHERE session_id = %s AND learner_id = %s",
        (session_id, learner_id),
    )
    if not cur.fetchone():
        raise HTTPException(status_code=400, detail="This learner was not expected at this session")

    if session_row["status"] == "cancelled":
        raise HTTPException(status_code=400, detail="This session is cancelled -- catch-up cannot be recorded against it")
    if session_row["sessionDate"] > uk_today():
        raise HTTPException(status_code=400, detail="This session has not occurred yet")

    if attendance_row is None or attendance_row["status"] not in ELIGIBLE_ABSENCE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="Catch-up can only be recorded for a learner marked absent (authorised or unauthorised) at "
                   "this session -- an unrecorded or other status is not eligible",
        )

    if completion_date < session_row["sessionDate"]:
        raise HTTPException(status_code=400, detail="Completion date cannot be before the original session date")
    if completion_date > uk_today():
        raise HTTPException(status_code=400, detail="Completion date cannot be in the future")


def record_catchup(
    cur, session_id: int, learner_id: int, completion_date: date, method: CatchupMethod, note: str,
    request: Request, session: dict,
) -> dict:
    """Tutor confirmation, not automated tracking -- a note is always
    required. Creates a fresh row, or -- if this learner/session pair has
    a REVOKED row from before -- reactivates that same row rather than
    creating a second one (the UNIQUE(session_id, learner_id) constraint
    would reject a second row outright). An already-'recorded' row is
    rejected with a clear 409 pointing at the correction action instead --
    this is the primary defence against duplicate submissions; the UNIQUE
    constraint is the concurrency backstop for two simultaneous attempts."""
    if not note or not note.strip():
        raise HTTPException(status_code=400, detail="A note describing the completion evidence or activity is required")

    with cur.connection.transaction():
        session_row, attendance_row = _fetch_session_and_lock_absence(cur, session_id, learner_id)
        _validate_eligibility(cur, session_id, learner_id, session_row, attendance_row, completion_date)

        existing = get_catchup(cur, session_id, learner_id)
        if existing is not None and existing["status"] == "recorded":
            raise HTTPException(
                status_code=409,
                detail="Catch-up has already been recorded for this learner and session -- use the correction "
                       "action to change it",
            )

        clean_note = note.strip()
        if existing is None:
            cur.execute(
                """
                INSERT INTO attendance_catchup
                    (session_id, learner_id, status, completion_date, method, note,
                     original_status_at_recording, recorded_by)
                VALUES (%s, %s, 'recorded', %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, learner_id) DO NOTHING
                RETURNING id
                """,
                (session_id, learner_id, completion_date, method, clean_note, attendance_row["status"], session["userId"]),
            )
            inserted = cur.fetchone()
            if inserted is None:
                # Lost a genuine race to a concurrent submission that landed
                # between our own existence check above and this INSERT --
                # the UNIQUE constraint caught it. Report the same friendly
                # 409 rather than a raw constraint-violation error.
                raise HTTPException(
                    status_code=409,
                    detail="Catch-up has already been recorded for this learner and session -- use the correction "
                           "action to change it",
                )
            catchup_id = inserted["id"]
            action = "catchup_recorded"
            previous_value = None
        else:
            # existing["status"] == "revoked" -- re-recording reuses the
            # same row (see bootstrap.py's own comment: one row per
            # learner/session for the entire lifecycle).
            previous_value = {k: v for k, v in existing.items()}
            cur.execute(
                """
                UPDATE attendance_catchup SET
                    status = 'recorded', completion_date = %s, method = %s, note = %s,
                    original_status_at_recording = %s, last_updated_by = %s, updated_at = now(),
                    revoked_at = NULL, revoked_by = NULL, revocation_reason = NULL
                WHERE id = %s
                """,
                (completion_date, method, clean_note, attendance_row["status"], session["userId"], existing["id"]),
            )
            catchup_id = existing["id"]
            action = "catchup_re_recorded"

        write_audit_log(
            request, action=action, entity_type="attendance_catchup", entity_id=catchup_id,
            previous_value=previous_value,
            new_value={
                "sessionId": session_id, "learnerId": learner_id, "completionDate": str(completion_date),
                "method": method, "note": clean_note,
            },
            cur=cur,
        )

    return get_catchup_with_state(cur, session_id, learner_id)


def correct_catchup(
    cur, session_id: int, learner_id: int, completion_date: date, method: CatchupMethod, note: str, reason: str,
    request: Request, session: dict,
) -> dict:
    """Corrects the details of an already-recorded, still-active catch-up
    entry (completion date, method, or note) -- never re-validates the
    ORIGINAL absence's eligibility (that may have already drifted since
    recording; the point of "effective"/ineligibleReason is to surface
    that separately, not to block a correction of the catch-up's own
    evidence)."""
    if not reason or not reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to correct a catch-up record")
    if not note or not note.strip():
        raise HTTPException(status_code=400, detail="A note describing the completion evidence or activity is required")

    with cur.connection.transaction():
        cur.execute(
            'SELECT session_date AS "sessionDate" FROM attendance_sessions WHERE id = %s AND deleted_at IS NULL',
            (session_id,),
        )
        session_row = cur.fetchone()
        if not session_row:
            raise HTTPException(status_code=404, detail="Attendance session not found")

        existing = get_catchup(cur, session_id, learner_id)
        if existing is None or existing["status"] != "recorded":
            raise HTTPException(status_code=404, detail="No active catch-up record to correct")

        if completion_date < session_row["sessionDate"]:
            raise HTTPException(status_code=400, detail="Completion date cannot be before the original session date")
        if completion_date > uk_today():
            raise HTTPException(status_code=400, detail="Completion date cannot be in the future")

        clean_note = note.strip()
        previous_value = {k: v for k, v in existing.items()}
        cur.execute(
            """
            UPDATE attendance_catchup SET
                completion_date = %s, method = %s, note = %s, last_updated_by = %s, updated_at = now()
            WHERE id = %s
            """,
            (completion_date, method, clean_note, session["userId"], existing["id"]),
        )
        write_audit_log(
            request, action="catchup_corrected", entity_type="attendance_catchup", entity_id=existing["id"],
            previous_value=previous_value,
            new_value={"completionDate": str(completion_date), "method": method, "note": clean_note, "reason": reason.strip()},
            cur=cur,
        )

    return get_catchup_with_state(cur, session_id, learner_id)


def revoke_catchup(cur, session_id: int, learner_id: int, reason: str, request: Request, session: dict) -> dict:
    if not reason or not reason.strip():
        raise HTTPException(status_code=400, detail="A reason is required to revoke a catch-up record")

    with cur.connection.transaction():
        existing = get_catchup(cur, session_id, learner_id)
        if existing is None or existing["status"] != "recorded":
            raise HTTPException(status_code=404, detail="No active catch-up record to revoke")

        previous_value = {k: v for k, v in existing.items()}
        cur.execute(
            """
            UPDATE attendance_catchup SET
                status = 'revoked', revoked_at = now(), revoked_by = %s, revocation_reason = %s,
                last_updated_by = %s, updated_at = now()
            WHERE id = %s
            """,
            (session["userId"], reason.strip(), session["userId"], existing["id"]),
        )
        write_audit_log(
            request, action="catchup_revoked", entity_type="attendance_catchup", entity_id=existing["id"],
            previous_value=previous_value,
            new_value={"reason": reason.strip()},
            cur=cur,
        )

    return get_catchup_with_state(cur, session_id, learner_id)


def get_catchup_history(cur, catchup_id: int) -> list[dict]:
    """Read-only. The audit trail for one catch-up record's whole
    lifecycle -- scoped to catch-up actions only, via the same generic
    audit_logs table the rest of the app uses, but exposed through this
    session-authority-gated endpoint rather than the admin-only generic
    /audit-log route, so a tutor with legitimate write access to this
    session can see it too (never broadened beyond that session's own
    access rule).

    audit_logs.previous_value/new_value are plain `text` columns holding
    a JSON string (write_audit_log always json.dumps()s before storing,
    bootstrap.py) -- parsed back into a dict/None here, so callers (the
    catchup router's response schema, and the frontend history panel) see
    a real structured value, not a string that still needs parsing."""
    cur.execute(
        """
        SELECT a.id, a.user_id AS "userId",
               CASE WHEN u.id IS NULL THEN NULL ELSE concat(u.first_name, ' ', u.last_name) END AS "userName",
               a.action, a.previous_value AS "previousValue", a.new_value AS "newValue", a.timestamp
        FROM audit_logs a
        LEFT JOIN users u ON a.user_id = u.id
        WHERE a.entity_type = 'attendance_catchup' AND a.entity_id = %s
        ORDER BY a.timestamp DESC
        """,
        (catchup_id,),
    )
    rows = cur.fetchall()
    for row in rows:
        row["previousValue"] = json.loads(row["previousValue"]) if row["previousValue"] is not None else None
        row["newValue"] = json.loads(row["newValue"]) if row["newValue"] is not None else None
    return rows
    return cur.fetchall()


def list_catchup_followup(
    cur, session: dict, week_start: date, week_end: date,
    cohort_id: int | None, learner_id: int | None, status_filter: Literal["outstanding", "completed"] | None,
    page: int, page_size: int, search: str | None = None,
) -> dict:
    """The weekly absence-follow-up list (item 4). The selected week
    always refers to the ORIGINAL session date -- a catch-up completed
    later still appears against that original absence, since this only
    ever filters by s.session_date, never by completion_date. Reads the
    frozen session_expected_learners snapshot (via the sel/attendance_records
    join), never current cohort membership, so a learner's later transfer
    never rewrites which historical sessions they show up against here.

    Every filter (status/cohort/learner/search) is applied in this ONE
    query's WHERE clause, before COUNT(*) and before LIMIT/OFFSET -- the
    full authorised dataset is always filtered first, then paginated, never
    the other way around, so no eligible absence beyond the first page is
    ever unreachable. ORDER BY ends with s.id, sel.learner_id (the row's
    own unique key, per the (session_id, learner_id) join) as an explicit
    tie-breaker -- session_date/name alone can tie across different rows,
    and an unstable order under LIMIT/OFFSET can otherwise skip or repeat
    rows across pages."""
    role = session.get("role")
    tutor_id = session.get("tutorId")

    clauses = [
        "ar.status IN ('absent_authorised', 'absent_unauthorised')",
        "s.status != 'cancelled'", "s.deleted_at IS NULL", "c.deleted_at IS NULL", "l.deleted_at IS NULL",
        "s.session_date >= %(weekStart)s", "s.session_date <= %(weekEnd)s",
    ]
    params: dict = {"weekStart": week_start, "weekEnd": week_end, "pageSize": page_size, "offset": (page - 1) * page_size}

    if role == "tutor":
        # Same cohort-tutor-or-cover-tutor scoping as _sessions_awaiting_completion
        # (dashboard.py) -- cover access stays limited to the specific
        # session it was granted for, never the wider cohort. Every other
        # filter below (including search) is ANDed onto this clause, so
        # none of them can ever widen a tutor's results beyond their own
        # scope -- they can only narrow it further.
        clauses.append("(c.tutor_id = %(tutorId)s OR s.cover_tutor_id = %(tutorId)s)")
        params["tutorId"] = tutor_id

    if cohort_id is not None:
        clauses.append("s.cohort_id = %(cohortId)s")
        params["cohortId"] = cohort_id
    if learner_id is not None:
        clauses.append("sel.learner_id = %(learnerId)s")
        params["learnerId"] = learner_id
    if search:
        clauses.append("concat(l.first_name, ' ', l.last_name) ILIKE %(search)s")
        params["search"] = f"%{search}%"

    if status_filter == "outstanding":
        clauses.append("(cu.id IS NULL OR cu.status = 'revoked')")
    elif status_filter == "completed":
        clauses.append("cu.id IS NOT NULL AND cu.status = 'recorded'")

    where = " AND ".join(clauses)
    base_from = """
        FROM session_expected_learners sel
        JOIN attendance_sessions s ON s.id = sel.session_id
        JOIN cohorts c ON s.cohort_id = c.id
        JOIN learners l ON l.id = sel.learner_id
        JOIN attendance_records ar ON ar.session_id = sel.session_id AND ar.learner_id = sel.learner_id
        LEFT JOIN attendance_catchup cu ON cu.session_id = sel.session_id AND cu.learner_id = sel.learner_id
    """

    cur.execute(f"SELECT count(*)::int AS n {base_from} WHERE {where}", params)
    total = cur.fetchone()["n"]

    cur.execute(
        f"""
        SELECT sel.learner_id AS "learnerId", concat(l.first_name, ' ', l.last_name) AS "learnerName",
               s.id AS "sessionId", s.cohort_id AS "cohortId", c.name AS "cohortName",
               s.session_date AS "sessionDate", s.title AS "sessionTitle",
               ar.status AS "originalStatus",
               cu.id AS "catchupId", cu.status AS "catchupStatus",
               cu.completion_date AS "completionDate", cu.method, cu.note,
               CASE WHEN cu.id IS NULL OR cu.status = 'revoked' THEN false ELSE true END AS "effective"
        {base_from}
        WHERE {where}
        ORDER BY s.session_date DESC, l.last_name, l.first_name, s.id, sel.learner_id
        LIMIT %(pageSize)s OFFSET %(offset)s
        """,
        params,
    )
    items = cur.fetchall()
    return {"items": items, "total": total, "page": page, "pageSize": page_size}
