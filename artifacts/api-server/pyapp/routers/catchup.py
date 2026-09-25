"""Stage 3: tutor-confirmed catch-up completion for a recorded absence.
Reuses the exact same session-authority rules as the rest of attendance
data (require_attendance_access / require_attendance_write_access) -- a
catch-up confirmation is exactly as sensitive as recording the original
attendance, so it gets exactly the same access rules, never a broader one.
Every GET here is read-only."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import require_attendance_access, require_auth, require_cohort_access, require_learner_access
from ..catchup_lib import (
    CatchupMethod,
    correct_catchup,
    get_catchup_history,
    get_catchup_with_state,
    list_catchup_followup,
    record_catchup,
    revoke_catchup,
)
from ..cover_tutor_lib import require_attendance_write_access
from ..db import get_cursor

router = APIRouter(tags=["catchup"])


class RecordCatchupInput(BaseModel):
    completionDate: date
    method: CatchupMethod
    note: str


class CorrectCatchupInput(BaseModel):
    completionDate: date
    method: CatchupMethod
    note: str
    reason: str


class RevokeCatchupInput(BaseModel):
    reason: str


@router.get("/attendance/sessions/{session_id}/catchup/{learner_id}")
def get_catchup_endpoint(session_id: int, learner_id: int, session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        return get_catchup_with_state(cur, session_id, learner_id)


@router.post("/attendance/sessions/{session_id}/catchup/{learner_id}")
def record_catchup_endpoint(
    session_id: int, learner_id: int, payload: RecordCatchupInput, request: Request,
    session: dict = Depends(require_auth),
):
    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        return record_catchup(cur, session_id, learner_id, payload.completionDate, payload.method, payload.note, request, session)


@router.put("/attendance/sessions/{session_id}/catchup/{learner_id}")
def correct_catchup_endpoint(
    session_id: int, learner_id: int, payload: CorrectCatchupInput, request: Request,
    session: dict = Depends(require_auth),
):
    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        return correct_catchup(
            cur, session_id, learner_id, payload.completionDate, payload.method, payload.note, payload.reason,
            request, session,
        )


@router.post("/attendance/sessions/{session_id}/catchup/{learner_id}/revoke")
def revoke_catchup_endpoint(
    session_id: int, learner_id: int, payload: RevokeCatchupInput, request: Request,
    session: dict = Depends(require_auth),
):
    with get_cursor() as cur:
        attendance_session = require_attendance_access(cur, session_id, session)
        require_attendance_write_access(attendance_session, session)
        return revoke_catchup(cur, session_id, learner_id, payload.reason, request, session)


@router.get("/attendance/sessions/{session_id}/catchup/{learner_id}/history")
def get_catchup_history_endpoint(session_id: int, learner_id: int, session: dict = Depends(require_auth)):
    """Read-only correction/revocation history for this learner/session's
    catch-up record -- gated by ordinary session read access (the same
    require_attendance_access every other read on this session uses), not
    the admin-only generic /audit-log route, since a tutor with legitimate
    access to this session should be able to see its own history without
    a broader admin grant."""
    with get_cursor() as cur:
        require_attendance_access(cur, session_id, session)
        catchup = get_catchup_with_state(cur, session_id, learner_id)["catchup"]
        if catchup is None:
            return []
        return get_catchup_history(cur, catchup["id"])


@router.get("/attendance/catchup/follow-up")
def list_catchup_followup_endpoint(
    weekStart: date,
    weekEnd: date,
    cohortId: int | None = None,
    learnerId: int | None = None,
    status: Literal["outstanding", "completed"] | None = None,
    search: str | None = None,
    page: int = 1,
    pageSize: int = 25,
    session: dict = Depends(require_auth),
):
    """Read-only. Weekly absence-follow-up view (item 4) -- one row per
    absent learner/session pair, selected by the ORIGINAL session date
    range, using the frozen session_expected_learners roster. A tutor sees
    only sessions they have attendance authority over (own cohort or
    per-session cover); an admin sees everything. Explicit cohortId/
    learnerId filters are IDOR-hardened the same way list_attendance_sessions
    already is (attendance.py) -- a tutor probing another tutor's cohort/
    learner gets a 403, not a silently-empty 200. `search` (learner name) is
    a free-text narrowing filter ANDed onto the same tutor-scoped WHERE
    clause as everything else here, so it can never widen a tutor's own
    results -- no separate access check is needed for it. Every filter is
    applied server-side before pagination (list_catchup_followup), so a
    learner beyond the first page is always reachable via search rather
    than requiring a larger page size."""
    if weekEnd < weekStart:
        raise HTTPException(status_code=400, detail="weekEnd cannot be before weekStart")
    if pageSize < 1 or pageSize > 200:
        raise HTTPException(status_code=400, detail="pageSize must be between 1 and 200")

    with get_cursor() as cur:
        if session.get("role") == "tutor":
            if cohortId is not None:
                require_cohort_access(cur, cohortId, session)
            if learnerId is not None:
                require_learner_access(cur, learnerId, session)
        return list_catchup_followup(cur, session, weekStart, weekEnd, cohortId, learnerId, status, page, pageSize, search)
