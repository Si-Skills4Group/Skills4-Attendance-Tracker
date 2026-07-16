from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..allocation_lib import learners_expected_in_cohort_as_of
from ..auth import require_attendance_access, require_auth, require_cohort_access
from ..audit import write_audit_log
from ..db import get_cursor

router = APIRouter(tags=["attendance"])

SESSION_SELECT = """
    SELECT s.id, s.cohort_id AS "cohortId", c.name AS "cohortName",
           c.tutor_id AS "tutorId",
           CASE WHEN t.id IS NULL THEN NULL ELSE concat(t.first_name, ' ', t.last_name) END AS "tutorName",
           s.session_date AS "sessionDate", s.planned_start_time AS "plannedStartTime",
           s.planned_end_time AS "plannedEndTime", s.planned_duration_hours AS "plannedDurationHours",
           s.title, s.notes, s.created_by AS "createdBy",
           s.created_at AS "createdAt", s.updated_at AS "updatedAt"
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


class AttendanceSessionUpdate(BaseModel):
    sessionDate: date | None = None
    plannedStartTime: str | None = Field(default=None, min_length=1)
    plannedEndTime: str | None = Field(default=None, min_length=1)
    plannedDurationHours: float | None = Field(default=None, ge=0)
    title: str | None = None
    notes: str | None = None


class RegisterEntryInput(BaseModel):
    learnerId: int
    status: str
    hoursAttended: float = Field(ge=0)
    minutesLate: int = Field(ge=0)
    notes: str | None = None
    overrideReason: str | None = None


class AttendanceRegisterInput(BaseModel):
    entries: list[RegisterEntryInput]


def _with_counts(cur, session_row: dict) -> dict:
    cur.execute(
        "SELECT count(*)::int AS count FROM attendance_records WHERE session_id = %s", (session_row["id"],)
    )
    recorded_count = cur.fetchone()["count"]
    expected_count = len(
        learners_expected_in_cohort_as_of(cur, session_row["cohortId"], session_row["sessionDate"])
    )
    return {**session_row, "recordedCount": recorded_count, "expectedCount": expected_count}


@router.get("/attendance/sessions")
def list_attendance_sessions(
    cohortId: int | None = None,
    tutorId: int | None = None,
    dateFrom: str | None = None,
    dateTo: str | None = None,
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

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as cur:
        if cohortId is not None:
            # Explicit access check independent of the tutor-scoping filter
            # above -- without this, a tutor passing another tutor's
            # cohortId just silently gets an empty list (the AND-ed filter
            # never matches), not a proper 403/404. IDOR probing should get
            # a real access-denied response, not an empty-but-200 result.
            require_cohort_access(cur, cohortId, session)
        cur.execute(f"{SESSION_SELECT} {where} ORDER BY s.session_date DESC", params)
        rows = cur.fetchall()
        return [_with_counts(cur, r) for r in rows]


@router.post("/attendance/sessions", status_code=201)
def create_attendance_session(payload: AttendanceSessionInput, request: Request, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        require_cohort_access(cur, payload.cohortId, session)

        if not payload.force:
            cur.execute(
                "SELECT id FROM attendance_sessions WHERE cohort_id = %s AND session_date = %s",
                (payload.cohortId, payload.sessionDate),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=409, detail="A session already exists for this cohort on this date"
                )

        cur.execute(
            """
            INSERT INTO attendance_sessions
                (cohort_id, session_date, planned_start_time, planned_end_time, planned_duration_hours,
                 title, notes, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
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
            ),
        )
        new_id = cur.fetchone()["id"]

        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (new_id,))
        full = _with_counts(cur, cur.fetchone())

    write_audit_log(request, action="create", entity_type="attendance_session", entity_id=new_id, new_value=full)
    return full


@router.get("/attendance/sessions/{session_id}")
def get_attendance_session(session_id: int, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()

        full_session = _with_counts(cur, session_row)

        expected_ids = learners_expected_in_cohort_as_of(cur, session_row["cohortId"], session_row["sessionDate"])
        if not expected_ids:
            entries = []
        else:
            cur.execute(
                """
                SELECT l.id AS "learnerId", concat(l.first_name, ' ', l.last_name) AS "learnerName",
                       l.learner_ref AS "learnerRef",
                       ar.id AS "recordId", ar.status, ar.hours_attended AS "hoursAttended",
                       ar.minutes_late AS "minutesLate", ar.notes, ar.override_reason AS "overrideReason",
                       ar.last_edited_by AS "lastEditedBy",
                       CASE WHEN u.id IS NULL THEN NULL ELSE concat(u.first_name, ' ', u.last_name) END AS "lastEditedByName"
                FROM learners l
                LEFT JOIN attendance_records ar ON ar.learner_id = l.id AND ar.session_id = %s
                LEFT JOIN users u ON ar.last_edited_by = u.id
                WHERE l.id = ANY(%s)
                ORDER BY l.last_name, l.first_name
                """,
                (session_id, expected_ids),
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


@router.patch("/attendance/sessions/{session_id}")
def update_attendance_session(
    session_id: int, payload: AttendanceSessionUpdate, request: Request, session: dict = Depends(require_auth)
):
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        existing = cur.fetchone()

        updates = payload.model_dump(exclude_unset=True)
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


@router.put("/attendance/sessions/{session_id}/register")
def save_attendance_register(
    session_id: int, payload: AttendanceRegisterInput, request: Request, session: dict = Depends(require_auth)
):
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        cur.execute(f"{SESSION_SELECT} WHERE s.id = %s", (session_id,))
        session_row = cur.fetchone()

        planned = float(session_row["plannedDurationHours"])
        for entry in payload.entries:
            if entry.hoursAttended > planned and not entry.overrideReason:
                raise HTTPException(
                    status_code=400,
                    detail="An override reason is required when hours attended exceeds the planned session duration",
                )
        learner_ids = [entry.learnerId for entry in payload.entries]
        if learner_ids:
            # Membership is resolved as-of the session's date, not the
            # learner's *current* cohort -- so editing a past session still
            # works for a learner who has since transferred elsewhere.
            allowed_ids = set(learners_expected_in_cohort_as_of(cur, session_row["cohortId"], session_row["sessionDate"]))
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

        learner_ids = learners_expected_in_cohort_as_of(cur, session_row["cohortId"], session_row["sessionDate"])

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
