"""Phase 9 CSV export service.

Two shapes are supported:

- export_csv_response: for bounded, single-entity reports (learner/cohort/
  tutor/organisation/attendance-hours) where the row count is small by
  definition (one entity's session history, or a handful of grouped
  buckets) -- fully materialised, still sanitised, still audited.
- stream_report_csv: for list-style reports (absence/lateness/register-
  completion/allocation-history) that could be large across a whole
  organisation and a wide date range -- checks the row count against
  MAX_EXPORT_ROWS *before* streaming anything (so an over-limit export is a
  clean 400, never a silent truncation), then streams bounded batches via a
  StreamingResponse and a server-side cursor loop, never materialising the
  full result set in Python.

Both paths always sanitise every cell via csv_utils.sanitize_csv_cell (CSV/
Excel formula-injection protection) and write exactly one audit_logs row per
export call (never the CSV content itself), tagged with a fresh per-export
correlation ID also returned as the X-Correlation-Id response header.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Callable

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from .audit import write_audit_log
from .csv_utils import sanitize_csv_cell, stringify_rows_to_csv
from .db import get_cursor

MAX_EXPORT_ROWS = 20_000
EXPORT_BATCH_SIZE = 1_000

# Bud fields are always exported under an explicit bud_ prefix, in a fixed
# order, kept separate from attendance columns -- never merged into a
# combined score, never left ambiguous about their source.
BUD_COLUMN_PREFIX_MAP = {
    "activityProgress": "bud_activity_progress",
    "activitiesOverdue": "bud_activities_overdue",
    "lastSubmissionDate": "bud_last_submission_date",
    "lastCompletedActivity": "bud_last_completed_activity",
    "statusDesc": "bud_status",
    "learningPlanUrl": "bud_learning_plan_url",
    "syncedAt": "bud_synced_at",
}


def with_bud_columns(row: dict, bud: dict | None) -> dict:
    result = dict(row)
    bud = bud or {}
    for field, column in BUD_COLUMN_PREFIX_MAP.items():
        result[column] = bud.get(field)
    return result


def _audit_export(
    request: Request,
    *,
    report_type: str,
    date_from: date | None,
    date_to: date | None,
    filters: dict,
    row_count: int,
    correlation_id: str,
    outcome: str,
) -> None:
    write_audit_log(
        request,
        action="export_report",
        entity_type="report_export",
        new_value={
            "reportType": report_type,
            "dateFrom": str(date_from) if date_from else None,
            "dateTo": str(date_to) if date_to else None,
            "filters": {k: v for k, v in filters.items() if v is not None},
            "rowCount": row_count,
            "correlationId": correlation_id,
            "outcome": outcome,
        },
    )


def export_csv_response(
    request: Request,
    *,
    report_type: str,
    rows: list[dict],
    columns: list[str],
    filename: str,
    date_from: date | None,
    date_to: date | None,
    filters: dict,
) -> Response:
    csv_text = stringify_rows_to_csv(rows, columns, sanitize=True)
    correlation_id = str(uuid.uuid4())
    _audit_export(
        request, report_type=report_type, date_from=date_from, date_to=date_to,
        filters=filters, row_count=len(rows), correlation_id=correlation_id, outcome="completed",
    )
    response = Response(content=csv_text, media_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["X-Correlation-Id"] = correlation_id
    return response


def stream_report_csv(
    request: Request,
    *,
    report_type: str,
    columns: list[str],
    filename: str,
    fetch_page: Callable[..., tuple[list[dict], int]],
    date_from: date | None,
    date_to: date | None,
    filters: dict,
    max_rows: int | None = None,
    batch_size: int | None = None,
) -> StreamingResponse:
    """fetch_page(cur, page, page_size) -> (rows, total) -- exactly the
    (rows, total) contract every pyapp/report_rows.py function already
    returns, so a report_rows function can be passed here directly.

    max_rows/batch_size default to the MAX_EXPORT_ROWS/EXPORT_BATCH_SIZE
    module constants, resolved here (not as parameter defaults) so tests
    can monkeypatch those constants and have it actually take effect --
    a parameter default is bound once at function-definition time and
    would never see a later monkeypatch."""
    max_rows = MAX_EXPORT_ROWS if max_rows is None else max_rows
    batch_size = EXPORT_BATCH_SIZE if batch_size is None else batch_size
    correlation_id = str(uuid.uuid4())

    with get_cursor() as cur:
        _, total = fetch_page(cur, 1, 1)
    if total > max_rows:
        _audit_export(
            request, report_type=report_type, date_from=date_from, date_to=date_to,
            filters=filters, row_count=total, correlation_id=correlation_id, outcome="rejected_over_limit",
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"This export would contain {total} rows, exceeding the maximum of {max_rows}. "
                "Please narrow your filters (e.g. a shorter date range, or a single cohort/tutor) and try again."
            ),
            headers={"X-Correlation-Id": correlation_id},
        )

    def generate():
        header_buf = io.StringIO()
        csv.writer(header_buf).writerow(columns)
        yield header_buf.getvalue()

        emitted = 0
        outcome = "completed"
        try:
            with get_cursor() as cur:
                page = 1
                while True:
                    rows, _ = fetch_page(cur, page, batch_size)
                    if not rows:
                        break
                    buf = io.StringIO()
                    writer = csv.writer(buf)
                    for row in rows:
                        writer.writerow([sanitize_csv_cell(row.get(c)) for c in columns])
                    yield buf.getvalue()
                    emitted += len(rows)
                    if len(rows) < batch_size:
                        break
                    page += 1
        except GeneratorExit:
            outcome = "client_disconnected"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            _audit_export(
                request, report_type=report_type, date_from=date_from, date_to=date_to,
                filters=filters, row_count=emitted, correlation_id=correlation_id, outcome=outcome,
            )

    response = StreamingResponse(generate(), media_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["X-Correlation-Id"] = correlation_id
    return response
