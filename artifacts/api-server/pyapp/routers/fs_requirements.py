"""Stage 5: admin-only Functional Skills subject requirement upload --
see fs_requirement_lib.py for the full matching/storage/permission design.
Every route is thin: get_cursor() + one lib call, matching this codebase's
convention (routers/learner_imports.py). Admin-only throughout (item 4's
"Clear requirement" action, and every upload/preview/commit endpoint),
via the same require_admin every other admin-only feature uses. GET
endpoints are read-only -- confirmed by their own tests."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from ..audit import write_audit_log
from ..auth import require_admin, require_auth, require_learner_access
from ..csv_utils import FS_REQUIREMENT_IMPORT_COLUMNS, CsvParseError, parse_fs_requirement_import_csv, stringify_rows_to_csv
from ..db import get_cursor
from ..fs_requirement_lib import (
    cancel_import_job,
    clear_requirement,
    confirm_import_job,
    create_import_job,
    expire_due_fs_requirement_import_jobs,
    fetch_allocation_rows,
    get_import_job,
    get_requirement,
    list_import_job_rows,
)
from ..rate_limit import check_and_record_rate_limit

router = APIRouter(tags=["functional-skills-requirements"])


class ClearRequirementInput(BaseModel):
    reason: str


@router.get("/functional-skills-requirements/import-jobs/template")
def get_fs_requirement_import_template(_session: dict = Depends(require_admin)):
    csv_text = stringify_rows_to_csv([], FS_REQUIREMENT_IMPORT_COLUMNS)
    return {"csv": csv_text, "filename": "functional-skills-requirement-template.csv"}


@router.post("/functional-skills-requirements/import-jobs", status_code=201)
async def upload_fs_requirement_import(request: Request, file: UploadFile = File(...), session: dict = Depends(require_admin)):
    raw = await file.read()
    try:
        parsed_rows = parse_fs_requirement_import_csv(raw)
    except CsvParseError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None

    with get_cursor() as cur:
        with cur.connection.transaction():
            check_and_record_rate_limit(
                cur, action="csv_upload", rate_key=f"user:{session['userId']}", max_attempts=20, window_minutes=60,
            )
        expire_due_fs_requirement_import_jobs(cur)
        job = create_import_job(cur, file.filename or "upload.csv", session["userId"], parsed_rows)

    write_audit_log(
        request, action="fs_requirement_import_uploaded", entity_type="fs_requirement_import_job",
        entity_id=job["id"], new_value={"filename": job["filename"], "totalRows": job["totalRows"]},
    )
    return job


@router.get("/functional-skills-requirements/import-jobs/{job_id}")
def get_fs_requirement_import_job(job_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        expire_due_fs_requirement_import_jobs(cur)
        return get_import_job(cur, job_id)


@router.get("/functional-skills-requirements/import-jobs/{job_id}/rows")
def list_fs_requirement_import_job_rows(
    job_id: int, page: int = 1, pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    outcome: str | None = None, _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        expire_due_fs_requirement_import_jobs(cur)
        return list_import_job_rows(cur, job_id, page, pageSize, outcome)


@router.post("/functional-skills-requirements/import-jobs/{job_id}/confirm")
def confirm_fs_requirement_import_job(job_id: int, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return confirm_import_job(cur, job_id, request, session)


@router.post("/functional-skills-requirements/import-jobs/{job_id}/cancel")
def cancel_fs_requirement_import_job(job_id: int, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        expire_due_fs_requirement_import_jobs(cur)
        job = cancel_import_job(cur, job_id)
    write_audit_log(request, action="fs_requirement_import_cancelled", entity_type="fs_requirement_import_job", entity_id=job_id)
    return job


@router.get("/functional-skills-requirements/allocation")
def get_fs_requirement_allocation(
    search: str | None = None,
    subject: str | None = None,
    missingOnly: bool = False,
    status: str | None = None,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    _session: dict = Depends(require_admin),
):
    """Item 6's admin allocation view -- organisation-wide, admin-only
    (there is no tutor-scoped variant of this screen in the spec)."""
    with get_cursor() as cur:
        rows, total = fetch_allocation_rows(
            cur, search=search, subject=subject, missing_only=missingOnly, status=status, page=page, page_size=pageSize,
        )
    return {"items": rows, "total": total, "page": page, "pageSize": pageSize}


@router.get("/functional-skills-requirements/learner/{learnerId}")
def get_learner_fs_requirement(learnerId: int, session: dict = Depends(require_auth)):
    """Item 6: shown on the learner detail screen "within existing access
    permissions" -- gated by require_learner_access, the same check every
    other per-learner read in this codebase uses, rather than a new
    admin-only rule (upload/preview/commit/clear remain admin-only)."""
    with get_cursor() as cur:
        require_learner_access(cur, learnerId, session)
        return get_requirement(cur, learnerId)


@router.post("/functional-skills-requirements/learner/{learnerId}/clear")
def clear_learner_fs_requirement(learnerId: int, payload: ClearRequirementInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return clear_requirement(cur, learnerId, payload.reason, request, session)
