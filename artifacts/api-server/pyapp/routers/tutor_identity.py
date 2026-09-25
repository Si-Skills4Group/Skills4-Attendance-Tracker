"""Stage 2: admin-only tutor identity diagnostics and a controlled,
preview-then-commit tutor mapping correction workflow. See
tutor_identity_lib.py's module docstring for the identity-vs-eligibility
distinction this deliberately preserves throughout."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..auth import require_admin
from ..db import get_cursor
from ..tutor_identity_lib import (
    build_tutor_identity_diagnostics,
    commit_tutor_mapping_correction,
    preview_tutor_mapping_correction,
)

router = APIRouter(tags=["tutor-identity"])


@router.get("/tutor-identity/diagnostics")
def get_tutor_identity_diagnostics(_session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        try:
            result = build_tutor_identity_diagnostics(cur)
        finally:
            cur.execute("COMMIT")
    return result


class MappingCorrectionPreviewRequest(BaseModel):
    sourceTutorId: int
    targetTutorId: int
    budTutorId: str


@router.post("/tutor-identity/mapping-corrections/preview")
def post_mapping_correction_preview(
    body: MappingCorrectionPreviewRequest, _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        return preview_tutor_mapping_correction(cur, body.sourceTutorId, body.targetTutorId, body.budTutorId)


class MappingCorrectionCommitRequest(BaseModel):
    sourceTutorId: int
    targetTutorId: int
    budTutorId: str
    expectedSourceTutorUpdatedAt: str
    expectedTargetTutorUpdatedAt: str
    reason: str
    identityConfirmedByAdmin: bool = False


@router.post("/tutor-identity/mapping-corrections/commit")
def post_mapping_correction_commit(
    body: MappingCorrectionCommitRequest, request: Request, session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        return commit_tutor_mapping_correction(
            cur, body.sourceTutorId, body.targetTutorId, body.budTutorId,
            body.expectedSourceTutorUpdatedAt, body.expectedTargetTutorUpdatedAt,
            body.reason, request, session, body.identityConfirmedByAdmin,
        )
