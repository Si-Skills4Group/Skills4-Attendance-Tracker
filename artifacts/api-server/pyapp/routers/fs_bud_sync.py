"""Bud Functional Skills requirement sync -- Administrator-only. See
pyapp/fs_bud_sync_lib.py for the classification/commit logic; this router
is deliberately thin (get_cursor() + one lib call per route), mirroring
routers/aptem_sync.py's own convention. No baseline step exists here --
see fs_bud_sync_lib.py's module docstring for why."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from .. import fs_bud_sync_lib
from ..auth import require_admin
from ..db import get_cursor

router = APIRouter(tags=["fs-bud-sync"])

_MatchStatus = Literal["new", "changed", "unchanged", "unmatched", "blocked_by_clear"]


class ItemUpdateInput(BaseModel):
    approved: bool


class BulkApproveInput(BaseModel):
    itemIds: list[int] = Field(min_length=1)


class CommitInput(BaseModel):
    itemIds: list[int] = Field(min_length=1)
    approvalReason: str = Field(min_length=1)


@router.get("/fs-bud-sync/status")
def get_status(_session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return fs_bud_sync_lib.get_source_status(cur)


@router.post("/fs-bud-sync/preview", status_code=201)
def create_preview(request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return fs_bud_sync_lib.run_preview(cur, request, session)


@router.get("/fs-bud-sync/jobs")
def list_jobs(
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        return fs_bud_sync_lib.list_jobs(cur, page, pageSize)


@router.get("/fs-bud-sync/jobs/{job_id}")
def get_job(job_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return fs_bud_sync_lib.get_job(cur, job_id)


@router.get("/fs-bud-sync/jobs/{job_id}/summary")
def get_job_summary(job_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return fs_bud_sync_lib.get_job_summary(cur, job_id)


@router.get("/fs-bud-sync/jobs/{job_id}/items")
def list_items(
    job_id: int,
    matchStatus: _MatchStatus | None = None,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        return fs_bud_sync_lib.list_items(cur, job_id, matchStatus, page, pageSize)


@router.patch("/fs-bud-sync/jobs/{job_id}/items/{item_id}")
def update_item(job_id: int, item_id: int, payload: ItemUpdateInput, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return fs_bud_sync_lib.update_item(cur, job_id, item_id, payload.approved)


@router.post("/fs-bud-sync/jobs/{job_id}/items/bulk-approve")
def bulk_approve_items(job_id: int, payload: BulkApproveInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return fs_bud_sync_lib.bulk_approve_items(cur, job_id, payload.itemIds, request, session)


@router.post("/fs-bud-sync/jobs/{job_id}/commit")
def commit_job(job_id: int, payload: CommitInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return fs_bud_sync_lib.run_commit(cur, job_id, payload.itemIds, payload.approvalReason, request, session)
