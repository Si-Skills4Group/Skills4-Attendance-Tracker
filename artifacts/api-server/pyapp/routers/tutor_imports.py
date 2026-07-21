"""Controlled tutor CSV import: admin-only upload, paginated preview with
duplicate classification, explicit per-row skip/update resolution,
explicit confirm, and a downloadable error report. Mirrors
routers/learner_imports.py -- see that module for the fuller design
rationale. Every route is thin -- all classification/persistence/import
logic lives in tutor_import_lib.py.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from ..audit import write_audit_log
from ..auth import require_admin
from ..csv_utils import TUTOR_IMPORT_COLUMNS, CsvParseError, parse_tutor_import_csv, stringify_rows_to_csv
from ..db import get_cursor
from ..rate_limit import check_and_record_rate_limit
from ..tutor_import_lib import (
    cancel_import_job,
    confirm_import_job,
    create_import_job,
    expire_due_tutor_import_jobs,
    get_import_job,
    list_import_job_rows,
    resolve_import_row,
)

router = APIRouter(tags=["tutors"])

ERROR_REPORT_COLUMNS = ["row_number", "email", "classification", "errors"]


class TutorImportRowResolveInput(BaseModel):
    resolution: Literal["skip", "update"]


@router.get("/tutors/import-jobs/template")
def get_tutor_import_template(_session: dict = Depends(require_admin)):
    csv_text = stringify_rows_to_csv([], TUTOR_IMPORT_COLUMNS)
    return {"csv": csv_text, "filename": "tutor-import-template.csv"}


@router.post("/tutors/import-jobs", status_code=201)
async def upload_tutor_import(
    request: Request, file: UploadFile = File(...), session: dict = Depends(require_admin)
):
    raw = await file.read()
    try:
        parsed_rows = parse_tutor_import_csv(raw)
    except CsvParseError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None

    with get_cursor() as cur:
        with cur.connection.transaction():
            check_and_record_rate_limit(
                cur, action="csv_upload", rate_key=f"user:{session['userId']}", max_attempts=20, window_minutes=60,
            )
        expire_due_tutor_import_jobs(cur)
        job = create_import_job(cur, file.filename or "upload.csv", session["userId"], parsed_rows)

    write_audit_log(
        request,
        action="tutor_import_uploaded",
        entity_type="tutor_import_job",
        entity_id=job["id"],
        new_value={"filename": job["filename"], "totalRows": job["totalRows"]},
    )
    return job


@router.get("/tutors/import-jobs/{job_id}")
def get_tutor_import_job(job_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        expire_due_tutor_import_jobs(cur)
        return get_import_job(cur, job_id)


@router.get("/tutors/import-jobs/{job_id}/rows")
def list_tutor_import_job_rows(
    job_id: int,
    page: int = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
    classification: str | None = None,
    _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        expire_due_tutor_import_jobs(cur)
        return list_import_job_rows(cur, job_id, page=page, page_size=pageSize, classification=classification)


@router.patch("/tutors/import-jobs/{job_id}/rows/{row_id}")
def resolve_tutor_import_job_row(
    job_id: int,
    row_id: int,
    payload: TutorImportRowResolveInput,
    request: Request,
    session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        row = resolve_import_row(cur, job_id, row_id, payload.resolution, session["userId"])

    write_audit_log(
        request,
        action="tutor_import_row_resolved",
        entity_type="tutor_import_row",
        entity_id=row_id,
        new_value={"jobId": job_id, "resolution": payload.resolution},
    )
    return row


@router.post("/tutors/import-jobs/{job_id}/confirm")
def confirm_tutor_import_job(job_id: int, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        with cur.connection.transaction():
            check_and_record_rate_limit(
                cur, action="import_confirm", rate_key=f"user:{session['userId']}", max_attempts=10, window_minutes=60,
            )
        summary = confirm_import_job(cur, job_id, request, session)

    write_audit_log(
        request,
        action="tutor_import_confirmed",
        entity_type="tutor_import_job",
        entity_id=job_id,
        new_value=summary,
    )
    return summary


@router.post("/tutors/import-jobs/{job_id}/cancel")
def cancel_tutor_import_job(job_id: int, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        job = cancel_import_job(cur, job_id)

    write_audit_log(request, action="tutor_import_cancelled", entity_type="tutor_import_job", entity_id=job_id)
    return job


@router.get("/tutors/import-jobs/{job_id}/rows/errors.csv")
def download_tutor_import_errors(job_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        job = get_import_job(cur, job_id)
        listing = list_import_job_rows(cur, job_id, page=1, page_size=max(job["totalRows"], 1))

    csv_rows = [
        {
            "row_number": row["rowNumber"],
            "email": row["rawData"].get("email", ""),
            "classification": row["classification"],
            "errors": "; ".join(row["errors"]),
        }
        for row in listing["items"]
        if row["errors"]
    ]
    csv_text = stringify_rows_to_csv(csv_rows, ERROR_REPORT_COLUMNS, sanitize=True)
    return {"csv": csv_text, "filename": f"tutor-import-{job_id}-errors.csv"}
