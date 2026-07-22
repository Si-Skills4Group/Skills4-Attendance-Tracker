"""Controlled Bud delta-synchronisation trial -- Administrator-only. See
pyapp/bud_sync_lib.py for the matching/classification/commit logic; this
router is deliberately thin (get_cursor() + one lib call per route),
mirroring the learner_imports.py/tutor_imports.py convention."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import bud_sync_lib
from ..auth import require_admin
from ..audit import write_audit_log
from ..db import get_cursor

router = APIRouter(tags=["bud-sync"])


class BaselineInput(BaseModel):
    notes: str | None = None


class BaselineResetInput(BaseModel):
    reason: str = Field(min_length=1)


class ItemUpdateInput(BaseModel):
    fieldUpdates: dict[str, str] | None = None
    approved: bool | None = None


class CommitInput(BaseModel):
    itemIds: list[int] = Field(min_length=1)
    approvalReason: str = Field(min_length=1)
    limitOverrideReason: str | None = None


@router.get("/bud-sync/status")
def get_status(_session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return bud_sync_lib.get_source_status(cur)


@router.post("/bud-sync/baseline", status_code=201)
def establish_baseline(payload: BaselineInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return bud_sync_lib.establish_baseline(cur, request, session, notes=payload.notes)


@router.post("/bud-sync/baseline/reset", status_code=201)
def reset_baseline(payload: BaselineResetInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return bud_sync_lib.reset_baseline(cur, request, session, payload.reason)


@router.get("/bud-sync/baseline/history")
def baseline_history(_session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return bud_sync_lib.list_baseline_history(cur)


@router.post("/bud-sync/preview", status_code=201)
def create_preview(request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return bud_sync_lib.run_preview(cur, request, session)


@router.get("/bud-sync/jobs")
def list_jobs(
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        return bud_sync_lib.list_jobs(cur, page, pageSize)


@router.get("/bud-sync/jobs/{job_id}")
def get_job(job_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return bud_sync_lib.get_job(cur, job_id)


@router.get("/bud-sync/jobs/{job_id}/items")
def list_items(
    job_id: int,
    matchStatus: Literal["new", "existing_update", "unchanged", "conflict", "existing_before_trial", "skipped"] | None = None,
    actionType: Literal["create_learner", "update_learner", "create_cohort", "create_allocation",
                         "transfer_tutor", "change_start_date", "change_status", "none"] | None = None,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        return bud_sync_lib.list_items(cur, job_id, matchStatus, actionType, page, pageSize)


@router.patch("/bud-sync/jobs/{job_id}/items/{item_id}")
def update_item(job_id: int, item_id: int, payload: ItemUpdateInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        updated = bud_sync_lib.update_item(cur, job_id, item_id, payload.fieldUpdates, payload.approved)
        write_audit_log(
            request, action="bud_sync_item_resolved", entity_type="bud_sync_item", entity_id=item_id,
            new_value={"fieldUpdates": payload.fieldUpdates, "approved": payload.approved}, cur=cur,
        )
    return updated


@router.post("/bud-sync/jobs/{job_id}/commit")
def commit_job(job_id: int, payload: CommitInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return bud_sync_lib.run_commit(
            cur, job_id, payload.itemIds, payload.approvalReason, payload.limitOverrideReason, request, session
        )


@router.get("/bud-sync/unmatched-pre-baseline")
def unmatched_pre_baseline(
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        return bud_sync_lib.get_unmatched_pre_baseline(cur, page, pageSize)
