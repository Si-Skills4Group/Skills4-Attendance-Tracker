"""Session-level cover tutor reassignment: lets an Administrator hand ONE
attendance session's register to a different tutor (sickness, leave,
emergency cover) without touching the cohort's own tutor, learner
allocations, or historical attendance authorship.

Mirrors session_register_lib.py's plain-function style. This codebase has
no ORM (raw psycopg SQL throughout), so "one central rule" for who the
effective tutor of a session is means one shared set of SQL fragments and
one shared access predicate, reused everywhere -- not a single shared
query, since callers select different column sets.
"""
from typing import Any, Literal

from fastapi import HTTPException

from .auth import deny_object_access

CoverReason = Literal[
    "tutor_sickness",
    "annual_leave",
    "emergency_cover",
    "tutor_unavailable",
    "operational_reassignment",
    "other",
]

# Joined once, reused by every query that needs to know a session's cover
# tutor, its original (pre-cover) tutor snapshot, and who assigned it.
COVER_TUTOR_JOINS_SQL = """
    LEFT JOIN tutors ct ON s.cover_tutor_id = ct.id
    LEFT JOIN tutors cot ON s.cover_original_tutor_id = cot.id
    LEFT JOIN users cau ON s.cover_assigned_by = cau.id
"""

# The effective tutor for a session is the cover tutor when one is active,
# otherwise the cohort's own tutor -- this is the single definition every
# access check, list, and dashboard query builds on.
EFFECTIVE_TUTOR_ID_SQL = "COALESCE(s.cover_tutor_id, c.tutor_id)"
EFFECTIVE_TUTOR_NAME_SQL = (
    "COALESCE(concat(ct.first_name, ' ', ct.last_name), concat(t.first_name, ' ', t.last_name))"
)

COVER_TUTOR_SELECT_FIELDS = f"""
    s.cover_tutor_id AS "coverTutorId",
    CASE WHEN ct.id IS NULL THEN NULL ELSE concat(ct.first_name, ' ', ct.last_name) END AS "coverTutorName",
    s.cover_original_tutor_id AS "coverOriginalTutorId",
    CASE WHEN cot.id IS NULL THEN NULL ELSE concat(cot.first_name, ' ', cot.last_name) END AS "coverOriginalTutorName",
    s.cover_reason AS "coverReason",
    s.cover_notes AS "coverNotes",
    s.cover_assigned_at AS "coverAssignedAt",
    CASE WHEN cau.id IS NULL THEN NULL ELSE concat(cau.first_name, ' ', cau.last_name) END AS "coverAssignedByName",
    {EFFECTIVE_TUTOR_ID_SQL} AS "effectiveTutorId",
    {EFFECTIVE_TUTOR_NAME_SQL} AS "effectiveTutorName"
"""


def is_effective_tutor(attendance_session: dict[str, Any], tutor_id: int | None) -> bool:
    """The one place 'is this tutor allowed to act on this session' is
    decided: either the cohort's own tutor, or this session's specific
    cover tutor. Pure and DB-free so it's unit-testable in isolation."""
    if tutor_id is None:
        return False
    return tutor_id == attendance_session.get("tutorId") or tutor_id == attendance_session.get("coverTutorId")


def require_attendance_write_access(attendance_session: dict[str, Any], session: dict[str, Any]) -> None:
    """After require_attendance_access has confirmed base (read) access,
    blocks the ORIGINAL tutor from editing while an active cover tutor is
    assigned -- only the cover tutor (or an admin) may write while cover is
    active. The original tutor keeps read access; this only guards writes,
    and is called separately from every mutating endpoint rather than
    folded into require_attendance_access, which every read endpoint also
    calls."""
    if (
        session.get("role") == "tutor"
        and attendance_session.get("coverTutorId") is not None
        and session.get("tutorId") == attendance_session.get("tutorId")
        and session.get("tutorId") != attendance_session.get("coverTutorId")
    ):
        deny_object_access(
            "attendance_session",
            attendance_session["id"],
            "This session has been assigned to a cover tutor; you have read-only access while cover is active.",
        )


def get_eligible_cover_tutor_or_400(cur, cover_tutor_id: int, original_tutor_id: int | None) -> dict:
    """Validates the replacement tutor: must exist, be active, and not be
    the session's own tutor. Backend-enforced regardless of what the
    frontend's Tutor picker already filters to -- never rely on frontend
    hiding for this."""
    if cover_tutor_id == original_tutor_id:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "cover_tutor_same_as_original",
                "message": "The replacement tutor must be different from the session's current tutor.",
            },
        )
    cur.execute('SELECT id, active FROM tutors WHERE id = %s', (cover_tutor_id,))
    tutor = cur.fetchone()
    if not tutor:
        raise HTTPException(
            status_code=422,
            detail={"reason": "cover_tutor_not_found", "message": "The selected replacement tutor was not found."},
        )
    if not tutor["active"]:
        raise HTTPException(
            status_code=422,
            detail={"reason": "cover_tutor_inactive", "message": "The selected replacement tutor is not active."},
        )
    return tutor


def validate_cover_reason(reason: str, notes: str | None) -> None:
    if reason == "other" and not (notes and notes.strip()):
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "other_reason_requires_notes",
                "message": "Notes are required when the reason is 'Other'.",
            },
        )


def require_session_open_for_cover_change(attendance_session: dict[str, Any], register_status: str) -> None:
    """Cancelled and locked sessions never accept a cover-tutor change --
    locking is never bypassed automatically, matching every other register
    mutation's lock check. A completed (but unlocked) register IS allowed
    through here -- the caller distinguishes that case to audit it as a
    correction rather than a routine assignment, per spec."""
    if attendance_session.get("status") == "cancelled":
        raise HTTPException(
            status_code=409,
            detail={"reason": "session_cancelled", "message": "Cancelled sessions cannot have a cover tutor assigned."},
        )
    if register_status == "locked":
        raise HTTPException(
            status_code=409,
            detail={"reason": "register_locked", "message": "Register is locked. An Administrator must unlock it before changing cover."},
        )


def assign_or_change_cover_tutor(
    cur,
    attendance_session: dict[str, Any],
    cover_tutor_id: int,
    reason: str,
    notes: str | None,
    admin_user_id: int | None,
) -> dict[str, Any]:
    """Upsert semantics: first assignment when no cover is currently active,
    change when one already is. cover_original_tutor_id is captured only on
    first assignment and never overwritten by a later change, so it always
    reflects who was originally scheduled at the moment cover began."""
    was_change = attendance_session.get("coverTutorId") is not None
    original_tutor_id = (
        attendance_session["coverOriginalTutorId"] if was_change else attendance_session["tutorId"]
    )
    cur.execute(
        """
        UPDATE attendance_sessions
        SET cover_tutor_id = %s, cover_original_tutor_id = %s, cover_reason = %s, cover_notes = %s,
            cover_assigned_at = now(), cover_assigned_by = %s
        WHERE id = %s
        """,
        (cover_tutor_id, original_tutor_id, reason, notes, admin_user_id, attendance_session["id"]),
    )
    return {
        "wasChange": was_change,
        "previousCoverTutorId": attendance_session.get("coverTutorId"),
        "previousCoverReason": attendance_session.get("coverReason"),
    }


def remove_cover_tutor(cur, session_id: int) -> None:
    """Clears the active cover assignment. Never touches attendance_records
    -- any attendance already entered by the (former) cover tutor is kept
    exactly as-is, including its created_by/last_edited_by authorship."""
    cur.execute(
        """
        UPDATE attendance_sessions
        SET cover_tutor_id = NULL, cover_original_tutor_id = NULL, cover_reason = NULL,
            cover_notes = NULL, cover_assigned_at = NULL, cover_assigned_by = NULL
        WHERE id = %s
        """,
        (session_id,),
    )
