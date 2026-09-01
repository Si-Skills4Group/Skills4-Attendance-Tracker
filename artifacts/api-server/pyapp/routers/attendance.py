from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..attendance_row_rules import (
    AttendanceStatus,
    diff_entry,
    is_historical_save,
    requires_change_reason,
    validate_entry,
)
from ..auth import require_admin, require_attendance_access, require_auth, require_cohort_access
from ..audit import write_audit_log
from ..cover_tutor_lib import (
    COVER_TUTOR_JOINS_SQL,
    COVER_TUTOR_SELECT_FIELDS,
    CoverReason,
    assign_or_change_cover_tutor,
    get_eligible_cover_tutor_or_400,
    remove_cover_tutor,
    require_attendance_write_access,
    require_session_open_for_cover_change,
    validate_cover_reason,
)
from ..db import get_cursor
from ..rate_limit import check_and_record_rate_limit
from ..session_register_lib import (
    apply_register_refresh,
    bump_register_version,
    cancel_session,
    compute_register_refresh,
    ensure_expected_learners_snapshot,
    ensure_expected_learners_snapshots_bulk,
    find_duplicate_session,
    lock_register,
    session_date_outside_cohort_range,
    unlock_register,
)

router = APIRouter(tags=["attendance"])

SESSION_SELECT = f"""
    SELECT s.id, s.cohort_id AS "cohortId", c.name AS "cohortName",
           c.tutor_id AS "tutorId",
           CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
           s.session_date AS "sessionDate", s.planned_start_time AS "plannedStartTime",
           s.planned_end_time AS "plannedEndTime", s.planned_duration_hours AS "plannedDurationHours",
           s.title, s.notes, s.created_by AS "createdBy",
           s.status, s.cancelled_at AS "cancelledAt", s.cancellation_reason AS "cancellationReason",
           s.override_reason AS "overrideReason",
           s.register_version AS "registerVersion",
           s.completed_at AS "completedAt", s.completed_by AS "completedBy",
           s.register_locked_at AS "registerLockedAt", s.register_locked_by AS "registerLockedBy",
           s.lock_reason AS "lockReason",
           {COVER_TUTOR_SELECT_FIELDS},
           s.created_at AS "createdAt", s.updated_at AS "updatedAt"
    FROM attendance_sessions s
    JOIN cohorts c ON s.cohort_id = c.id
    LEFT JOIN tutors t ON c.tutor_id = t.id
    {COVER_TUTOR_JOINS_SQL}
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
SESSION_SELECT_WITH_COUNTS = f"""
    SELECT s.id, s.cohort_id AS "cohortId", c.name AS "cohortName",
           c.tutor_id AS "tutorId",
           CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
           s.session_date AS "sessionDate", s.planned_start_time AS "plannedStartTime",
           s.planned_end_time AS "plannedEndTime", s.planned_duration_hours AS "plannedDurationHours",
           s.title, s.notes, s.created_by AS "createdBy",
           s.status, s.cancelled_at AS "cancelledAt", s.cancellation_reason AS "cancellationReason",
           s.override_reason AS "overrideReason",
           s.register_version AS "registerVersion",
           s.completed_at AS "completedAt", s.completed_by AS "completedBy",
           s.register_locked_at AS "registerLockedAt", s.register_locked_by AS "registerLockedBy",
           s.lock_reason AS "lockReason",
           {COVER_TUTOR_SELECT_FIELDS},
           s.created_at AS "createdAt", s.updated_at AS "updatedAt",
           (SELECT count(*)::int FROM attendance_records ar WHERE ar.session_id = s.id) AS "recordedCount",
           (SELECT count(*)::int FROM session_expected_learners sel WHERE sel.session_id = s.id) AS "expectedCount"
    FROM attendance_sessions s
    JOIN cohorts c ON s.cohort_id = c.id
    LEFT JOIN tutors t ON c.tutor_id = t.id
    {COVER_TUTOR_JOINS_SQL}
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


class SessionDeleteInput(BaseModel):
    reason: str = Field(min_length=1)
    confirmWithAttendance: bool = False


class RefreshRegisterInput(BaseModel):
    confirm: bool = False


class CompleteRegisterInput(BaseModel):
    registerVersion: int


class LockRegisterInput(BaseModel):
    reason: str = Field(min_length=1)
    registerVersion: int


class UnlockRegisterInput(BaseModel):
    reason: str = Field(min_length=1)
    registerVersion: int


class CoverTutorInput(BaseModel):
    coverTutorId: int
    reason: CoverReason
    notes: str | None = None
    registerVersion: int


class RemoveCoverTutorInput(BaseModel):
    reason: str = Field(min_length=1)
    confirmWithAttendance: bool = False
    registerVersion: int


class RegisterEntryInput(BaseModel):
    learnerId: int
    status: AttendanceStatus
    hoursAttended: float = Field(ge=0)
    minutesLate: int = Field(ge=0)
    notes: str | None = Field(default=None, max_length=1000)
    overrideReason: str | None = None


class AttendanceRegisterInput(BaseModel):
    registerVersion: int
    entries: list[RegisterEntryInput]
    changeReason: str | None = None


def _compute_register_status(status: str, recorded_count: int, expected_count: int, locked_at) -> str:
    """Derived from recordedCount/expectedCount rather than stored, so
    there's exactly one source of truth for completion -- see
    session_register_lib module docstring / Phase 6 plan for why."""
    if status == "cancelled":
        return "cancelled"
    if locked_at is not None:
        return "locked"
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
    register_status = _compute_register_status(
        session_row["status"], recorded_count, expected_count, session_row["registerLockedAt"]
    )
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
        # OR-ed with cover_tutor_id so a session covered by this tutor
        # appears in their list too -- without pulling in the rest of that
        # session's cohort, since the match is per-session, not per-cohort.
        clauses.append("(c.tutor_id = %s OR s.cover_tutor_id = %s)")
        params.append(session["tutorId"])
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

    clauses.append("s.deleted_at IS NULL")
    clauses.append("c.deleted_at IS NULL")
    where = f"WHERE {' AND '.join(clauses)}"
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
        row["registerStatus"] = _compute_register_status(
            row["status"], row["recordedCount"], row["expectedCount"], row["registerLockedAt"]
        )
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
                write_audit_log(
                    request,
                    action="authorization_denied",
                    entity_type="cohort",
                    entity_id=payload.cohortId,
                    new_value={"reason": "non_admin_duplicate_session_override_attempt", "conflictReasons": conflict_reasons},
                )
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
def generate_session_register(session_id: int, request: Request, session: dict = Depends(require_auth)):
    """Idempotent -- safe to call even though session creation and every
    register read already ensure the snapshot exists. Useful as an explicit
    "make sure this register is ready" action (e.g. support/debugging)."""
    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()

        ensure_expected_learners_snapshot(
            cur, session_row["id"], session_row["cohortId"], session_row["sessionDate"], session.get("userId")
        )
        full = _with_counts(cur, session_row)

    write_audit_log(request, action="generate_register", entity_type="attendance_session", entity_id=session_id)
    return full


@router.patch("/attendance/sessions/{session_id}")
def update_attendance_session(
    session_id: int, payload: AttendanceSessionUpdate, request: Request, session: dict = Depends(require_auth)
):
    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        existing = cur.fetchone()
        if existing["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Cancelled sessions cannot be edited")
        if existing["registerLockedAt"] is not None:
            raise HTTPException(status_code=409, detail="Register is locked. An Administrator must unlock it before editing.")

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
    session_id: int, payload: SessionCancelInput, request: Request, session: dict = Depends(require_auth)
):
    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s AND s.deleted_at IS NULL", (session_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Attendance session not found")
        if existing["status"] == "cancelled":
            raise HTTPException(status_code=400, detail="Session is already cancelled")

        with cur.connection.transaction():
            cancel_session(cur, existing, payload.reason, payload.confirmWithAttendance, session["userId"])
            write_audit_log(
                request,
                action="cancel",
                entity_type="attendance_session",
                entity_id=session_id,
                previous_value={"status": existing["status"]},
                new_value={"status": "cancelled", "reason": payload.reason},
                cur=cur,
            )

        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        full = _with_counts(cur, cur.fetchone())

    return full


@router.post("/attendance/sessions/{session_id}/delete", status_code=204)
def delete_attendance_session(
    session_id: int, payload: SessionDeleteInput, request: Request, session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM attendance_sessions WHERE id = %s", (session_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Attendance session not found")
        if existing["deleted_at"] is not None:
            raise HTTPException(status_code=400, detail="Session is already deleted")

        # Mirrors cancel_session's own guard exactly: never hide recorded
        # attendance out from under an admin without an explicit "yes,
        # delete it anyway" confirmation -- the data is retained either way
        # (soft delete), but disappearing from every report is a big enough
        # consequence to warrant the same two-step confirm.
        cur.execute("SELECT count(*)::int AS count FROM attendance_records WHERE session_id = %s", (session_id,))
        recorded_count = cur.fetchone()["count"]
        if recorded_count > 0 and not payload.confirmWithAttendance:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "attendance_already_recorded",
                    "message": "This session already has recorded attendance. Confirm to delete anyway -- "
                               "the recorded attendance will be preserved, not deleted, but the session will "
                               "no longer appear in any report.",
                    "recordedCount": recorded_count,
                },
            )

        with cur.connection.transaction():
            cur.execute(
                "UPDATE attendance_sessions SET deleted_at = now(), deleted_by = %s, deletion_reason = %s, "
                "updated_at = now() WHERE id = %s",
                (session["userId"], payload.reason, session_id),
            )
            write_audit_log(
                request,
                action="delete_session",
                entity_type="attendance_session",
                entity_id=session_id,
                previous_value=existing,
                new_value={"deletedAt": "now", "reason": payload.reason},
                cur=cur,
            )
    return None


@router.post("/attendance/sessions/{session_id}/refresh-register")
def refresh_session_register(
    session_id: int, payload: RefreshRegisterInput, request: Request, session: dict = Depends(require_auth)
):
    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s AND s.deleted_at IS NULL", (session_id,))
        existing = cur.fetchone()
        if existing["status"] == "cancelled":
            raise HTTPException(status_code=400, detail="Cancelled sessions cannot be refreshed")
        if existing["sessionDate"] < date.today():
            raise HTTPException(status_code=400, detail="Historical sessions cannot be refreshed")

        ensure_expected_learners_snapshot(
            cur, existing["id"], existing["cohortId"], existing["sessionDate"], session.get("userId")
        )
        full = _with_counts(cur, existing)
        if full["registerStatus"] in ("completed", "locked"):
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
    is_admin = session.get("role") == "admin"

    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()
        if session_row["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Cancelled sessions cannot accept attendance")

        ensure_expected_learners_snapshot(
            cur, session_id, session_row["cohortId"], session_row["sessionDate"], session.get("userId")
        )
        before = _with_counts(cur, session_row)

        if before["registerStatus"] == "locked":
            raise HTTPException(
                status_code=409, detail="Register is locked. An Administrator must unlock it before editing."
            )
        if payload.registerVersion != before["registerVersion"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "stale_register_version",
                    "message": "This register has changed since you loaded it. Reload to see the latest version before saving.",
                    "currentVersion": before["registerVersion"],
                },
            )

        # One batched fetch for both the allowed-learner-ids check and each
        # entry's expected_register_row_id -- avoids a per-entry query.
        cur.execute(
            'SELECT id, learner_id AS "learnerId" FROM session_expected_learners WHERE session_id = %s',
            (session_id,),
        )
        expected_rows = cur.fetchall()
        expected_row_id_by_learner = {row["learnerId"]: row["id"] for row in expected_rows}
        learner_ids = [entry.learnerId for entry in payload.entries]
        if not set(learner_ids).issubset(expected_row_id_by_learner.keys()):
            raise HTTPException(status_code=403, detail="Register contains learners outside this session cohort")

        cur.execute(
            """
            SELECT learner_id AS "learnerId", status, hours_attended AS "hoursAttended",
                   minutes_late AS "minutesLate", notes, override_reason AS "overrideReason"
            FROM attendance_records WHERE session_id = %s
            """,
            (session_id,),
        )
        existing_by_learner = {row["learnerId"]: row for row in cur.fetchall()}

        planned = float(session_row["plannedDurationHours"])
        historical = is_historical_save(before["registerStatus"], session_row["sessionDate"])

        errors: list[dict] = []
        diffs: dict[int, dict] = {}
        created_learner_ids: list[int] = []
        for entry in payload.entries:
            errors.extend(
                validate_entry(
                    learner_id=entry.learnerId,
                    status=entry.status,
                    hours_attended=entry.hoursAttended,
                    minutes_late=entry.minutesLate,
                    override_reason=entry.overrideReason,
                    planned_hours=planned,
                    is_admin=is_admin,
                )
            )
            existing_entry = existing_by_learner.get(entry.learnerId)
            if existing_entry is None:
                created_learner_ids.append(entry.learnerId)
            diff = diff_entry(
                existing_entry,
                {
                    "status": entry.status,
                    "hoursAttended": entry.hoursAttended,
                    "minutesLate": entry.minutesLate,
                    "notes": entry.notes,
                    "overrideReason": entry.overrideReason,
                },
            )
            if diff:
                diffs[entry.learnerId] = diff

        material_changes = any(requires_change_reason(d) for d in diffs.values())
        if historical and material_changes and not (payload.changeReason and payload.changeReason.strip()):
            errors.append(
                {
                    "learnerId": None,
                    "field": "changeReason",
                    "message": "A reason is required when editing historical attendance",
                }
            )

        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors})

        is_first_save = before["recordedCount"] == 0

        if historical and material_changes:
            with cur.connection.transaction():
                check_and_record_rate_limit(
                    cur, action="historical_attendance_edit", rate_key=f"user:{session['userId']}",
                    max_attempts=30, window_minutes=60,
                )

        with cur.connection.transaction():
            for entry in payload.entries:
                cur.execute(
                    """
                    INSERT INTO attendance_records
                        (session_id, learner_id, status, hours_attended, minutes_late, notes, override_reason,
                         expected_register_row_id, created_by, last_edited_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id, learner_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        hours_attended = EXCLUDED.hours_attended,
                        minutes_late = EXCLUDED.minutes_late,
                        notes = EXCLUDED.notes,
                        override_reason = EXCLUDED.override_reason,
                        expected_register_row_id = EXCLUDED.expected_register_row_id,
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
                        expected_row_id_by_learner.get(entry.learnerId),
                        session["userId"],
                        session["userId"],
                    ),
                )
            bump_register_version(cur, session_id, expected_version=payload.registerVersion)
            write_audit_log(
                request,
                action="save_register",
                entity_type="attendance_session",
                entity_id=session_id,
                new_value={
                    "isFirstSave": is_first_save,
                    "changeReason": payload.changeReason if material_changes else None,
                    "created": created_learner_ids,
                    "changes": [{"learnerId": learner_id, "fields": diff} for learner_id, diff in diffs.items()],
                    "totalEntries": len(payload.entries),
                },
                cur=cur,
            )

    return get_attendance_session(session_id, session)


@router.post("/attendance/sessions/{session_id}/complete-register")
def complete_register(
    session_id: int, payload: CompleteRegisterInput, request: Request, session: dict = Depends(require_auth)
):
    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()
        if session_row["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Cancelled sessions cannot be completed")

        ensure_expected_learners_snapshot(
            cur, session_id, session_row["cohortId"], session_row["sessionDate"], session.get("userId")
        )
        before = _with_counts(cur, session_row)
        if before["registerStatus"] == "locked":
            raise HTTPException(status_code=409, detail="Register is locked")
        if payload.registerVersion != before["registerVersion"]:
            raise HTTPException(
                status_code=409,
                detail={"reason": "stale_register_version", "currentVersion": before["registerVersion"]},
            )

        planned = float(session_row["plannedDurationHours"])
        cur.execute(
            """
            SELECT sel.learner_id AS "learnerId", ar.id AS "recordId", ar.status,
                   ar.hours_attended AS "hoursAttended", ar.minutes_late AS "minutesLate"
            FROM session_expected_learners sel
            LEFT JOIN attendance_records ar ON ar.learner_id = sel.learner_id AND ar.session_id = sel.session_id
            WHERE sel.session_id = %s
            """,
            (session_id,),
        )
        rows = cur.fetchall()

        errors: list[dict] = []
        for row in rows:
            if row["recordId"] is None:
                errors.append(
                    {
                        "learnerId": row["learnerId"],
                        "field": "status",
                        "message": "Attendance has not been recorded for this learner",
                    }
                )
                continue
            errors.extend(
                validate_entry(
                    learner_id=row["learnerId"],
                    status=row["status"],
                    hours_attended=float(row["hoursAttended"]),
                    minutes_late=row["minutesLate"],
                    override_reason=None,
                    planned_hours=planned,
                    is_admin=True,
                    check_override=False,
                )
            )
        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors})

        with cur.connection.transaction():
            cur.execute(
                "UPDATE attendance_sessions SET completed_at = now(), completed_by = %s WHERE id = %s",
                (session["userId"], session_id),
            )
            bump_register_version(cur, session_id, expected_version=payload.registerVersion)
            write_audit_log(
                request,
                action="complete_register",
                entity_type="attendance_session",
                entity_id=session_id,
                new_value={"completedBy": session["userId"]},
                cur=cur,
            )
    return get_attendance_session(session_id, session)


@router.post("/attendance/sessions/{session_id}/lock")
def lock_attendance_register(
    session_id: int, payload: LockRegisterInput, request: Request, session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s AND s.deleted_at IS NULL", (session_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Attendance session not found")

        ensure_expected_learners_snapshot(
            cur, existing["id"], existing["cohortId"], existing["sessionDate"], session.get("userId")
        )
        before = _with_counts(cur, existing)
        if before["registerStatus"] == "locked":
            raise HTTPException(status_code=400, detail="Register is already locked")
        if before["registerStatus"] != "completed":
            raise HTTPException(status_code=400, detail="Only a completed register can be locked")
        if payload.registerVersion != before["registerVersion"]:
            raise HTTPException(
                status_code=409,
                detail={"reason": "stale_register_version", "currentVersion": before["registerVersion"]},
            )

        with cur.connection.transaction():
            lock_register(cur, existing, payload.reason, session["userId"])
            bump_register_version(cur, session_id, expected_version=payload.registerVersion)
            write_audit_log(
                request,
                action="lock_register",
                entity_type="attendance_session",
                entity_id=session_id,
                new_value={"reason": payload.reason},
                cur=cur,
            )

        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        full = _with_counts(cur, cur.fetchone())

    return full


@router.post("/attendance/sessions/{session_id}/unlock")
def unlock_attendance_register(
    session_id: int, payload: UnlockRegisterInput, request: Request, session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s AND s.deleted_at IS NULL", (session_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Attendance session not found")
        if existing["registerLockedAt"] is None:
            raise HTTPException(status_code=400, detail="Register is not locked")
        if payload.registerVersion != existing["registerVersion"]:
            raise HTTPException(
                status_code=409,
                detail={"reason": "stale_register_version", "currentVersion": existing["registerVersion"]},
            )

        with cur.connection.transaction():
            unlock_register(cur, existing)
            bump_register_version(cur, session_id, expected_version=payload.registerVersion)
            write_audit_log(
                request,
                action="unlock_register",
                entity_type="attendance_session",
                entity_id=session_id,
                previous_value={"lockReason": existing["lockReason"]},
                new_value={"reason": payload.reason},
                cur=cur,
            )

        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        full = _with_counts(cur, cur.fetchone())
    return full


@router.post("/attendance/sessions/{session_id}/mark-all-present")
def mark_all_present(session_id: int, request: Request, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()
        if session_row["status"] == "cancelled":
            raise HTTPException(status_code=409, detail="Cancelled sessions cannot accept attendance")

        ensure_expected_learners_snapshot(
            cur, session_id, session_row["cohortId"], session_row["sessionDate"], session.get("userId")
        )
        cur.execute("SELECT learner_id FROM session_expected_learners WHERE session_id = %s", (session_id,))
        learner_ids = [row["learner_id"] for row in cur.fetchall()]

        with cur.connection.transaction():
            for learner_id in learner_ids:
                cur.execute(
                    """
                    INSERT INTO attendance_records
                        (session_id, learner_id, status, hours_attended, minutes_late, created_by, last_edited_by)
                    VALUES (%s, %s, 'present', %s, 0, %s, %s)
                    ON CONFLICT (session_id, learner_id) DO UPDATE SET
                        status = 'present', hours_attended = EXCLUDED.hours_attended,
                        minutes_late = 0, last_edited_by = EXCLUDED.last_edited_by
                    """,
                    (session_id, learner_id, session_row["plannedDurationHours"], session["userId"], session["userId"]),
                )
            bump_register_version(cur, session_id)

    write_audit_log(
        request,
        action="mark_all_present",
        entity_type="attendance_session",
        entity_id=session_id,
        new_value={"learnerCount": len(learner_ids)},
    )

    return get_attendance_session(session_id, session)


@router.put("/attendance/sessions/{session_id}/cover")
def assign_cover_tutor(
    session_id: int, payload: CoverTutorInput, request: Request, session: dict = Depends(require_admin)
):
    """Upsert: assigns a cover tutor when none is active, or changes the
    existing one -- the only difference is which audit action name is
    written, so one endpoint (matching this router's own PUT .../register
    upsert precedent) handles both rather than two nearly-identical ones."""
    with get_cursor() as cur:
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s AND s.deleted_at IS NULL", (session_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Attendance session not found")

        ensure_expected_learners_snapshot(
            cur, existing["id"], existing["cohortId"], existing["sessionDate"], session.get("userId")
        )
        before = _with_counts(cur, existing)
        require_session_open_for_cover_change(existing, before["registerStatus"])
        if payload.registerVersion != before["registerVersion"]:
            raise HTTPException(
                status_code=409,
                detail={"reason": "stale_register_version", "currentVersion": before["registerVersion"]},
            )
        validate_cover_reason(payload.reason, payload.notes)
        get_eligible_cover_tutor_or_400(cur, payload.coverTutorId, existing["tutorId"])

        # A completed register's delivery tutor is never silently changed --
        # still Administrator-only and reason-required like any other
        # assignment, but always audited distinctly as a correction.
        is_correction = before["registerStatus"] == "completed"

        with cur.connection.transaction():
            result = assign_or_change_cover_tutor(
                cur, existing, payload.coverTutorId, payload.reason, payload.notes, session["userId"]
            )
            bump_register_version(cur, session_id, expected_version=payload.registerVersion)
            if is_correction:
                action = "cover_tutor_correction"
            elif result["wasChange"]:
                action = "cover_tutor_changed"
            else:
                action = "cover_tutor_assigned"
            write_audit_log(
                request,
                action=action,
                entity_type="attendance_session",
                entity_id=session_id,
                previous_value={
                    "coverTutorId": result["previousCoverTutorId"],
                    "coverReason": result["previousCoverReason"],
                },
                new_value={
                    "coverTutorId": payload.coverTutorId,
                    "reason": payload.reason,
                    "notes": payload.notes,
                    "registerStatusAtChange": before["registerStatus"],
                },
                cur=cur,
            )

        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        full = _with_counts(cur, cur.fetchone())
    return full


@router.post("/attendance/sessions/{session_id}/cover/remove")
def remove_cover_tutor_endpoint(
    session_id: int, payload: RemoveCoverTutorInput, request: Request, session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s AND s.deleted_at IS NULL", (session_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Attendance session not found")
        if existing["coverTutorId"] is None:
            raise HTTPException(status_code=400, detail="This session does not have a cover tutor assigned")

        ensure_expected_learners_snapshot(
            cur, existing["id"], existing["cohortId"], existing["sessionDate"], session.get("userId")
        )
        before = _with_counts(cur, existing)
        require_session_open_for_cover_change(existing, before["registerStatus"])
        if payload.registerVersion != before["registerVersion"]:
            raise HTTPException(
                status_code=409,
                detail={"reason": "stale_register_version", "currentVersion": before["registerVersion"]},
            )

        # Mirrors cancel_session's/delete's own two-step confirm exactly --
        # never remove cover out from under recorded attendance without an
        # explicit "yes, anyway"; the data is preserved either way.
        cur.execute("SELECT count(*)::int AS count FROM attendance_records WHERE session_id = %s", (session_id,))
        recorded_count = cur.fetchone()["count"]
        if recorded_count > 0 and not payload.confirmWithAttendance:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "attendance_already_recorded",
                    "message": "This session already has recorded attendance, entered while cover was active. "
                    "Confirm to remove cover anyway -- the recorded attendance will be preserved, not deleted.",
                    "recordedCount": recorded_count,
                },
            )

        with cur.connection.transaction():
            remove_cover_tutor(cur, session_id)
            bump_register_version(cur, session_id, expected_version=payload.registerVersion)
            write_audit_log(
                request,
                action="cover_tutor_removed",
                entity_type="attendance_session",
                entity_id=session_id,
                previous_value={
                    "coverTutorId": existing["coverTutorId"],
                    "coverReason": existing["coverReason"],
                },
                new_value={"reason": payload.reason, "recordedCountAtRemoval": recorded_count},
                cur=cur,
            )

        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        full = _with_counts(cur, cur.fetchone())
    return full
