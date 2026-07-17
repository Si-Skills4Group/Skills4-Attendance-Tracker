from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import require_admin, require_attendance_access, require_auth, require_cohort_access
from ..audit import write_audit_log
from ..db import get_cursor
from ..session_register_lib import (
    apply_register_refresh,
    cancel_session,
    compute_register_refresh,
    ensure_expected_learners_snapshot,
    ensure_expected_learners_snapshots_bulk,
    find_duplicate_session,
    session_date_outside_cohort_range,
)

router = APIRouter(tags=["attendance"])

SESSION_SELECT = """
    SELECT s.id, s.cohort_id AS "cohortId", c.name AS "cohortName",
           c.tutor_id AS "tutorId",
           CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
           s.session_date AS "sessionDate", s.planned_start_time AS "plannedStartTime",
           s.planned_end_time AS "plannedEndTime", s.planned_duration_hours AS "plannedDurationHours",
           s.title, s.notes, s.created_by AS "createdBy",
           s.status, s.cancelled_at AS "cancelledAt", s.cancellation_reason AS "cancellationReason",
           s.override_reason AS "overrideReason",
           s.created_at AS "createdAt", s.updated_at AS "updatedAt"
    FROM attendance_sessions s
    JOIN cohorts c ON s.cohort_id = c.id
    LEFT JOIN tutors t ON c.tutor_id = t.id
"""

# Same base select as SESSION_SELECT, but with recordedCount/expectedCount
# computed as correlated subqueries per row instead of via _with_counts in
# a Python loop -- one round trip to the database regardless of how many
# sessions are returned, instead of 1 + 2N. expectedCount reads the
# session_expected_learners snapshot (see session_register_lib) rather than
# recomputing eligibility live, so a listed session's count matches exactly
# what its own register page shows -- callers must have already ensured the
# snapshot exists for every session_id in this result set (list_attendance_sessions
# does this via ensure_expected_learners_snapshots_bulk before running this query).
SESSION_SELECT_WITH_COUNTS = """
    SELECT s.id, s.cohort_id AS "cohortId", c.name AS "cohortName",
           c.tutor_id AS "tutorId",
           CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
           s.session_date AS "sessionDate", s.planned_start_time AS "plannedStartTime",
           s.planned_end_time AS "plannedEndTime", s.planned_duration_hours AS "plannedDurationHours",
           s.title, s.notes, s.created_by AS "createdBy",
           s.status, s.cancelled_at AS "cancelledAt", s.cancellation_reason AS "cancellationReason",
           s.override_reason AS "overrideReason",
           s.created_at AS "createdAt", s.updated_at AS "updatedAt",
           (SELECT count(*)::int FROM attendance_records ar WHERE ar.session_id = s.id) AS "recordedCount",
           (SELECT count(*)::int FROM session_expected_learners sel WHERE sel.session_id = s.id) AS "expectedCount"
    FROM attendance_sessions s
    JOIN cohorts c ON s.cohort_id = c.id
    LEFT JOIN tutors t ON c.tutor_id = t.id
"""


class AttendanceSessionInput(BaseModel):
    cohortId: int
    sessionDate: date
    plannedStartTime: str = Field(min_length=1)
    plannedEndTime: str = Field(min_length=1)
    plannedDurationHours: float = Field(ge=0)
    title: str = Field(min_length=1)
    notes: str | None = None
    force: bool = False
    overrideReason: str | None = None


class AttendanceSessionUpdate(BaseModel):
    sessionDate: date | None = None
    plannedStartTime: str | None = Field(default=None, min_length=1)
    plannedEndTime: str | None = Field(default=None, min_length=1)
    plannedDurationHours: float | None = Field(default=None, ge=0)
    title: str | None = None
    notes: str | None = None
    confirmChange: bool = False


class SessionCancelInput(BaseModel):
    reason: str = Field(min_length=1)
    confirmWithAttendance: bool = False


class RefreshRegisterInput(BaseModel):
    confirm: bool = False


class RegisterEntryInput(BaseModel):
    learnerId: int
    status: str
    hoursAttended: float = Field(ge=0)
    minutesLate: int = Field(ge=0)
    notes: str | None = None
    overrideReason: str | None = None


class AttendanceRegisterInput(BaseModel):
    entries: list[RegisterEntryInput]


def _compute_register_status(status: str, recorded_count: int, expected_count: int) -> str:
    """Derived from recordedCount/expectedCount rather than stored, so
    there's exactly one source of truth for completion -- see
    session_register_lib module docstring / Phase 6 plan for why."""
    if status == "cancelled":
        return "cancelled"
    if recorded_count == 0:
        return "not_started"
    if expected_count > 0 and recorded_count >= expected_count:
        return "completed"
    return "in_progress"


def _with_counts(cur, session_row: dict) -> dict:
    """Assumes the caller has already ensured the expected-learners
    snapshot exists for this session (ensure_expected_learners_snapshot) --
    this function only reads counts, it never generates."""
    cur.execute(
        "SELECT count(*)::int AS count FROM attendance_records WHERE session_id = %s", (session_row["id"],)
    )
    recorded_count = cur.fetchone()["count"]
    cur.execute(
        "SELECT count(*)::int AS count FROM session_expected_learners WHERE session_id = %s", (session_row["id"],)
    )
    expected_count = cur.fetchone()["count"]
    register_status = _compute_register_status(session_row["status"], recorded_count, expected_count)
    return {
        **session_row,
        "recordedCount": recorded_count,
        "expectedCount": expected_count,
        "registerStatus": register_status,
    }


@router.get("/attendance/sessions")
def list_attendance_sessions(
    cohortId: int | None = None,
    tutorId: int | None = None,
    dateFrom: str | None = None,
    dateTo: str | None = None,
    status: str | None = None,
    registerStatus: str | None = None,
    session: dict = Depends(require_auth),
):
    clauses = []
    params: list = []
    if session.get("role") == "tutor" and session.get("tutorId"):
        clauses.append("c.tutor_id = %s")
        params.append(session["tutorId"])
    elif tutorId is not None:
        clauses.append("c.tutor_id = %s")
        params.append(tutorId)
    if cohortId is not None:
        clauses.append("s.cohort_id = %s")
        params.append(cohortId)
    if dateFrom:
        clauses.append("s.session_date >= %s")
        params.append(dateFrom)
    if dateTo:
        clauses.append("s.session_date <= %s")
        params.append(dateTo)
    if status:
        clauses.append("s.status = %s")
        params.append(status)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as cur:
        if cohortId is not None:
            # Explicit access check independent of the tutor-scoping filter
            # above -- without this, a tutor passing another tutor's
            # cohortId just silently gets an empty list (the AND-ed filter
            # never matches), not a proper 403/404. IDOR probing should get
            # a real access-denied response, not an empty-but-200 result.
            require_cohort_access(cur, cohortId, session)

        cur.execute(f"SELECT s.id FROM attendance_sessions s JOIN cohorts c ON s.cohort_id = c.id {where}", params)
        session_ids = [row["id"] for row in cur.fetchall()]
        ensure_expected_learners_snapshots_bulk(cur, session_ids)

        cur.execute(f"{SESSION_SELECT_WITH_COUNTS} {where} ORDER BY s.session_date DESC", params)
        rows = cur.fetchall()

    for row in rows:
        row["registerStatus"] = _compute_register_status(row["status"], row["recordedCount"], row["expectedCount"])
    if registerStatus:
        rows = [row for row in rows if row["registerStatus"] == registerStatus]
    return rows


@router.post("/attendance/sessions", status_code=201)
def create_attendance_session(payload: AttendanceSessionInput, request: Request, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        require_cohort_access(cur, payload.cohortId, session)
        cur.execute(
            'SELECT start_date AS "startDate", end_date AS "endDate" FROM cohorts WHERE id = %s',
            (payload.cohortId,),
        )
        cohort_dates = cur.fetchone()

        conflict_reasons = []
        if find_duplicate_session(cur, payload.cohortId, payload.sessionDate, payload.plannedStartTime):
            conflict_reasons.append("duplicate_session")
        if session_date_outside_cohort_range(cohort_dates, payload.sessionDate):
            conflict_reasons.append("outside_cohort_date_range")

        if conflict_reasons:
            if not payload.force:
                raise HTTPException(status_code=409, detail={"reasons": conflict_reasons})
            if session.get("role") != "admin":
                raise HTTPException(
                    status_code=403, detail="Only an Administrator can override a duplicate or out-of-range session"
                )
            if not payload.overrideReason or not payload.overrideReason.strip():
                raise HTTPException(status_code=400, detail="overrideReason is required to override this check")

        cur.execute(
            """
            INSERT INTO attendance_sessions
                (cohort_id, session_date, planned_start_time, planned_end_time, planned_duration_hours,
                 title, notes, created_by, override_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                payload.cohortId,
                payload.sessionDate,
                payload.plannedStartTime,
                payload.plannedEndTime,
                payload.plannedDurationHours,
                payload.title,
                payload.notes,
                session["userId"],
                payload.overrideReason if conflict_reasons else None,
            ),
        )
        new_id = cur.fetchone()["id"]

        ensure_expected_learners_snapshot(cur, new_id, payload.cohortId, payload.sessionDate, session["userId"])

        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (new_id,))
        full = _with_counts(cur, cur.fetchone())

    write_audit_log(request, action="create", entity_type="attendance_session", entity_id=new_id, new_value=full)
    if conflict_reasons:
        write_audit_log(
            request,
            action="duplicate_override",
            entity_type="attendance_session",
            entity_id=new_id,
            new_value={"reasons": conflict_reasons, "overrideReason": payload.overrideReason},
        )
    return full


@router.get("/attendance/sessions/{session_id}")
def get_attendance_session(session_id: int, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()

        ensure_expected_learners_snapshot(
            cur, session_row["id"], session_row["cohortId"], session_row["sessionDate"], session.get("userId")
        )
        full_session = _with_counts(cur, session_row)

        cur.execute(
            """
            SELECT l.id AS "learnerId", concat(l.first_name, ' ', l.last_name) AS "learnerName",
                   l.learner_ref AS "learnerRef",
                   ar.id AS "recordId", ar.status, ar.hours_attended AS "hoursAttended",
                   ar.minutes_late AS "minutesLate", ar.notes, ar.override_reason AS "overrideReason",
                   ar.last_edited_by AS "lastEditedBy",
                   CASE WHEN u.id IS NULL THEN NULL ELSE concat(u.first_name, ' ', u.last_name) END AS "lastEditedByName"
            FROM session_expected_learners sel
            JOIN learners l ON l.id = sel.learner_id
            LEFT JOIN attendance_records ar ON ar.learner_id = l.id AND ar.session_id = %s
            LEFT JOIN users u ON ar.last_edited_by = u.id
            WHERE sel.session_id = %s
            ORDER BY l.last_name, l.first_name
            """,
            (session_id, session_id),
        )
        entries = cur.fetchall()

    for e in entries:
        if e["status"] is None:
            e["status"] = "absent_unauthorised"
        if e["hoursAttended"] is None:
            e["hoursAttended"] = 0
        if e["minutesLate"] is None:
            e["minutesLate"] = 0

    return {"session": full_session, "entries": entries}


@router.get("/attendance/sessions/{session_id}/expected-learners")
def get_session_expected_learners(session_id: int, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()

        ensure_expected_learners_snapshot(
            cur, session_row["id"], session_row["cohortId"], session_row["sessionDate"], session.get("userId")
        )
        cur.execute(
            """
            SELECT l.id AS "learnerId", concat(l.first_name, ' ', l.last_name) AS "learnerName",
                   l.learner_ref AS "learnerRef"
            FROM session_expected_learners sel
            JOIN learners l ON l.id = sel.learner_id
            WHERE sel.session_id = %s
            ORDER BY l.last_name, l.first_name
            """,
            (session_id,),
        )
        return cur.fetchall()


@router.post("/attendance/sessions/{session_id}/generate-register")
def generate_session_register(session_id: int, session: dict = Depends(require_auth)):
    """Idempotent -- safe to call even though session creation and every
    register read already ensure the snapshot exists. Useful as an explicit
    "make sure this register is ready" action (e.g. support/debugging)."""
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()

        ensure_expected_learners_snapshot(
            cur, session_row["id"], session_row["cohortId"], session_row["sessionDate"], session.get("userId")
        )
        return _with_counts(cur, session_row)


@router.patch("/attendance/sessions/{session_id}")
def update_attendance_session(
    session_id: int, payload: AttendanceSessionUpdate, request: Request, session: dict = Depends(require_auth)
):
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        existing = cur.fetchone()
        if existing["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Cancelled sessions cannot be edited")

        updates = payload.model_dump(exclude_unset=True, exclude={"confirmChange"})
        schedule_fields = {"sessionDate", "plannedStartTime", "plannedEndTime"}
        if schedule_fields & updates.keys():
            cur.execute(
                "SELECT count(*)::int AS count FROM attendance_records WHERE session_id = %s", (session_id,)
            )
            if cur.fetchone()["count"] > 0 and not payload.confirmChange:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "reason": "attendance_already_recorded",
                        "message": "This session already has recorded attendance. Confirm to change "
                        "the date/time anyway -- existing attendance will not be affected.",
                    },
                )

        column_map = {
            "sessionDate": "session_date",
            "plannedStartTime": "planned_start_time",
            "plannedEndTime": "planned_end_time",
            "plannedDurationHours": "planned_duration_hours",
            "title": "title",
            "notes": "notes",
        }
        set_clauses = [f"{column_map[k]} = %s" for k in updates]
        params = list(updates.values())
        if set_clauses:
            set_clauses.append("updated_by = %s")
            params.append(session["userId"])
            cur.execute(
                f"UPDATE attendance_sessions SET {', '.join(set_clauses)} WHERE id = %s RETURNING id",
                [*params, session_id],
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Attendance session not found")

        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        full = _with_counts(cur, cur.fetchone())

    write_audit_log(
        request,
        action="update",
        entity_type="attendance_session",
        entity_id=session_id,
        previous_value=existing,
        new_value=full,
    )
    return full


@router.post("/attendance/sessions/{session_id}/cancel")
def cancel_attendance_session(
    session_id: int, payload: SessionCancelInput, request: Request, session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Attendance session not found")
        if existing["status"] == "cancelled":
            raise HTTPException(status_code=400, detail="Session is already cancelled")

        cancel_session(cur, existing, payload.reason, payload.confirmWithAttendance, session["userId"])

        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        full = _with_counts(cur, cur.fetchone())

    write_audit_log(
        request,
        action="cancel",
        entity_type="attendance_session",
        entity_id=session_id,
        previous_value={"status": existing["status"]},
        new_value={"status": "cancelled", "reason": payload.reason},
    )
    return full


@router.post("/attendance/sessions/{session_id}/refresh-register")
def refresh_session_register(
    session_id: int, payload: RefreshRegisterInput, request: Request, session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Attendance session not found")
        if existing["status"] == "cancelled":
            raise HTTPException(status_code=400, detail="Cancelled sessions cannot be refreshed")
        if existing["sessionDate"] < date.today():
            raise HTTPException(status_code=400, detail="Historical sessions cannot be refreshed")

        ensure_expected_learners_snapshot(
            cur, existing["id"], existing["cohortId"], existing["sessionDate"], session.get("userId")
        )
        full = _with_counts(cur, existing)
        if full["registerStatus"] == "completed":
            raise HTTPException(status_code=400, detail="Completed registers cannot be refreshed")

        diff = compute_register_refresh(cur, existing)
        if not payload.confirm:
            return diff
        result = apply_register_refresh(cur, existing, diff, session["userId"])

    write_audit_log(
        request,
        action="refresh_register",
        entity_type="attendance_session",
        entity_id=session_id,
        new_value={
            "added": [r["learnerId"] for r in result["added"]],
            "removed": [r["learnerId"] for r in result["removed"]],
            "blocked": [r["learnerId"] for r in result["blocked"]],
        },
    )
    return result


@router.put("/attendance/sessions/{session_id}/register")
def save_attendance_register(
    session_id: int, payload: AttendanceRegisterInput, request: Request, session: dict = Depends(require_auth)
):
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()
        if session_row["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Cancelled sessions cannot accept attendance")

        ensure_expected_learners_snapshot(
            cur, session_id, session_row["cohortId"], session_row["sessionDate"], session.get("userId")
        )

        planned = float(session_row["plannedDurationHours"])
        for entry in payload.entries:
            if entry.hoursAttended > planned and not entry.overrideReason:
                raise HTTPException(
                    status_code=400,
                    detail="An override reason is required when hours attended exceeds the planned session duration",
                )
        learner_ids = [entry.learnerId for entry in payload.entries]
        if learner_ids:
            # Membership is resolved from the frozen register snapshot, not
            # the learner's *current* cohort -- so editing a past session
            # still works for a learner who has since transferred elsewhere.
            cur.execute("SELECT learner_id FROM session_expected_learners WHERE session_id = %s", (session_id,))
            allowed_ids = {row["learner_id"] for row in cur.fetchall()}
            if not set(learner_ids).issubset(allowed_ids):
                raise HTTPException(status_code=403, detail="Register contains learners outside this session cohort")

        for entry in payload.entries:
            cur.execute(
                """
                INSERT INTO attendance_records
                    (session_id, learner_id, status, hours_attended, minutes_late, notes, override_reason, last_edited_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, learner_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    hours_attended = EXCLUDED.hours_attended,
                    minutes_late = EXCLUDED.minutes_late,
                    notes = EXCLUDED.notes,
                    override_reason = EXCLUDED.override_reason,
                    last_edited_by = EXCLUDED.last_edited_by
                """,
                (
                    session_id,
                    entry.learnerId,
                    entry.status,
                    entry.hoursAttended,
                    entry.minutesLate,
                    entry.notes,
                    entry.overrideReason,
                    session["userId"],
                ),
            )

    write_audit_log(
        request,
        action="save_register",
        entity_type="attendance_session",
        entity_id=session_id,
        new_value={"entries": len(payload.entries)},
    )

    return get_attendance_session(session_id, session)


@router.post("/attendance/sessions/{session_id}/mark-all-present")
def mark_all_present(session_id: int, request: Request, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()
        if session_row["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Cancelled sessions cannot accept attendance")

        ensure_expected_learners_snapshot(
            cur, session_id, session_row["cohortId"], session_row["sessionDate"], session.get("userId")
        )
        cur.execute("SELECT learner_id FROM session_expected_learners WHERE session_id = %s", (session_id,))
        learner_ids = [row["learner_id"] for row in cur.fetchall()]

        for learner_id in learner_ids:
            cur.execute(
                """
                INSERT INTO attendance_records
                    (session_id, learner_id, status, hours_attended, minutes_late, last_edited_by)
                VALUES (%s, %s, 'present', %s, 0, %s)
                ON CONFLICT (session_id, learner_id) DO UPDATE SET
                    status = 'present', hours_attended = EXCLUDED.hours_attended,
                    minutes_late = 0, last_edited_by = EXCLUDED.last_edited_by
                """,
                (session_id, learner_id, session_row["plannedDurationHours"], session["userId"]),
            )

    write_audit_log(
        request,
        action="mark_all_present",
        entity_type="attendance_session",
        entity_id=session_id,
        new_value={"learnerCount": len(learner_ids)},
    )

    return get_attendance_session(session_id, session)
