"""Controlled learner CSV import (Phase 5): admin-only upload, paginated
preview with duplicate classification, explicit per-row skip/update
resolution, explicit confirm, and a downloadable error report.

Every route is thin -- all classification/persistence/import logic lives in
learner_import_lib.py, matching this codebase's convention of keeping
business logic in a lib module and routes as get_cursor() + one lib call.
"""

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from ..audit import write_audit_log
from ..auth import require_admin
from ..csv_utils import LEARNER_IMPORT_COLUMNS, CsvParseError, parse_learner_import_csv, stringify_rows_to_csv
from ..db import get_cursor
from ..learner_import_lib import (
    cancel_import_job,
    confirm_import_job,
    create_import_job,
    expire_due_learner_import_jobs,
    get_import_job,
    list_import_job_rows,
    resolve_import_row,
)

router = APIRouter(tags=["learners"])

ERROR_REPORT_COLUMNS = ["row_number", "learner_reference", "classification", "errors"]


class ImportRowResolveInput(BaseModel):
    resolution: Literal["skip", "update"]


@router.get("/learners/import-jobs/template")
def get_learner_import_template(_session: dict = Depends(require_admin)):
    csv_text = stringify_rows_to_csv([], LEARNER_IMPORT_COLUMNS)
    return {"csv": csv_text, "filename": "learner-import-template.csv"}


@router.post("/learners/import-jobs", status_code=201)
async def upload_learner_import(
    request: Request, file: UploadFile = File(...), session: dict = Depends(require_admin)
):
    raw = await file.read()
    try:
        parsed_rows = parse_learner_import_csv(raw)
    except CsvParseError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None

    with get_cursor() as cur:
        expire_due_learner_import_jobs(cur)
        job = create_import_job(cur, file.filename or "upload.csv", session["userId"], parsed_rows)

    write_audit_log(
        request,
        action="learner_import_uploaded",
        entity_type="learner_import_job",
        entity_id=job["id"],
        new_value={"filename": job["filename"], "totalRows": job["totalRows"]},
    )
    return job


@router.get("/learners/import-jobs/{job_id}")
def get_learner_import_job(job_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        expire_due_learner_import_jobs(cur)
        return get_import_job(cur, job_id)


@router.get("/learners/import-jobs/{job_id}/rows")
def list_learner_import_job_rows(
    job_id: int,
    page: int = 1,
    pageSize: int = 25,
    classification: str | None = None,
    _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        expire_due_learner_import_jobs(cur)
        return list_import_job_rows(cur, job_id, page=page, page_size=pageSize, classification=classification)


@router.patch("/learners/import-jobs/{job_id}/rows/{row_id}")
def resolve_learner_import_job_row(
    job_id: int,
    row_id: int,
    payload: ImportRowResolveInput,
    request: Request,
    session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        row = resolve_import_row(cur, job_id, row_id, payload.resolution, session["userId"])

    write_audit_log(
        request,
        action="learner_import_row_resolved",
        entity_type="learner_import_row",
        entity_id=row_id,
        new_value={"jobId": job_id, "resolution": payload.resolution},
    )
    return row


@router.post("/learners/import-jobs/{job_id}/confirm")
def confirm_learner_import_job(job_id: int, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        summary = confirm_import_job(cur, job_id, request, session)

    write_audit_log(
        request,
        action="learner_import_confirmed",
        entity_type="learner_import_job",
        entity_id=job_id,
        new_value=summary,
    )
    return summary


@router.post("/learners/import-jobs/{job_id}/cancel")
def cancel_learner_import_job(job_id: int, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        job = cancel_import_job(cur, job_id)

    write_audit_log(
        request, action="learner_import_cancelled", entity_type="learner_import_job", entity_id=job_id
    )
    return job


@router.get("/learners/import-jobs/{job_id}/rows/errors.csv")
def download_learner_import_errors(job_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        job = get_import_job(cur, job_id)
        listing = list_import_job_rows(cur, job_id, page=1, page_size=max(job["totalRows"], 1))

    csv_rows = [
        {
            "row_number": row["rowNumber"],
            "learner_reference": row["rawData"].get("learner_reference", ""),
            "classification": row["classification"],
            "errors": "; ".join(row["errors"]),
        }
        for row in listing["items"]
        if row["errors"]
    ]
    csv_text = stringify_rows_to_csv(csv_rows, ERROR_REPORT_COLUMNS, sanitize=True)
    return {"csv": csv_text, "filename": f"learner-import-{job_id}-errors.csv"}
