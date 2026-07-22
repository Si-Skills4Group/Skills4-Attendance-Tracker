import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .bootstrap import bootstrap_database
from .config import get_auth_settings
from .correlation import CORRELATION_HEADER, CorrelationIdMiddleware, get_correlation_id
from .db import get_cursor
from .learner_import_lib import expire_due_learner_import_jobs
from .logging_config import RequestLoggingMiddleware, configure_logging
from .login_rate_limit import prune_stale_login_attempts
from .rate_limit import prune_stale_rate_limit_attempts
from .scheduled_allocations_lib import apply_due_scheduled_allocations
from .session import SessionMiddleware
from .tutor_import_lib import expire_due_tutor_import_jobs
from .routers import (
    health,
    auth_routes,
    users,
    dashboard,
    tutors,
    tutor_imports,
    learners,
    learner_imports,
    cohorts,
    allocation_routes,
    attendance,
    attendance_summary,
    reports,
    audit_routes,
    settings,
    bud_sync,
)

configure_logging()
logger = logging.getLogger("skills4attendance-api")

auth_settings = get_auth_settings()
auth_settings.validate_startup()
bootstrap_database()

# Catch up on any prospective transfers that became due while the app was
# down (e.g. over a weekend) -- see scheduled_allocations_lib for why this
# is a lazy check rather than a cron job.
with get_cursor() as _cur:
    apply_due_scheduled_allocations(_cur)
    # Same lazy pattern as above, for learner CSV import jobs -- see
    # learner_import_lib for why there is no cron/background worker.
    expire_due_learner_import_jobs(_cur)
    # Same lazy pattern again, for stale login-rate-limit rows.
    prune_stale_login_attempts(_cur)
    # ...and for tutor CSV import jobs.
    expire_due_tutor_import_jobs(_cur)
    # ...and for the generic rate-limit table (CSV upload/export/etc).
    prune_stale_rate_limit_attempts(_cur)

app = FastAPI(title="Skills4Attendance API")

# Starlette wraps middleware in reverse of add order -- the LAST one added
# ends up outermost. CorrelationIdMiddleware must be outermost (added
# last) so its contextvar is still set when RequestLoggingMiddleware reads
# it after call_next returns; added the other way round, Correlation's own
# `finally` resets the contextvar before Logging ever gets to read it.
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(CorrelationIdMiddleware)

if auth_settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=auth_settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

if auth_settings.auth_mode == "local":
    app.add_middleware(SessionMiddleware)


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    detail = exc.detail
    body = dict(detail) if isinstance(detail, dict) else {"error": detail}
    body.setdefault("correlationId", get_correlation_id())
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    # str(exc) is not safe here -- Pydantic's default __str__ for a
    # RequestValidationError includes the server-side source file path and
    # line number of the route handler that raised it. exc.errors() is the
    # same structured, file-path-free data FastAPI itself would normally
    # render for a default 422, just under this app's existing 400 shape.
    messages = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
    return JSONResponse(
        status_code=400,
        content={"error": messages, "correlationId": get_correlation_id()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never expose the exception message, a stack trace, SQL/connection
    # details, or a file path to the client -- only a generic message and
    # the correlation ID a support engineer can grep the logs for. The full
    # exception (with traceback) is logged server-side only, at ERROR.
    #
    # Deliberately request.state.correlation_id, not get_correlation_id():
    # a bare Exception is dispatched by Starlette's ServerErrorMiddleware,
    # which sits outside CorrelationIdMiddleware, so by the time this
    # handler runs, that middleware's `finally` has already reset the
    # contextvar back to "" while the exception was propagating through
    # it. request.state was set directly on this same Request object
    # earlier and isn't affected by that.
    correlation_id = getattr(request.state, "correlation_id", "") or get_correlation_id()
    logger.error(
        "Unhandled exception (correlationId=%s): %s", correlation_id, exc, exc_info=exc,
        extra={"correlationId": correlation_id, "route": request.url.path, "method": request.method, "statusCode": 500},
    )
    # A bare Exception handler is dispatched by Starlette's
    # ServerErrorMiddleware, which sits OUTSIDE every middleware this app
    # adds (CorrelationIdMiddleware included) -- unlike an HTTPException,
    # this response never passes back through that middleware for it to
    # add the header, so it has to be set directly here instead.
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred. Please try again or contact support.",
            "correlationId": correlation_id,
        },
        headers={CORRELATION_HEADER: correlation_id} if correlation_id else None,
    )


for router in (
    health.router,
    auth_routes.router,
    users.router,
    dashboard.router,
    tutors.router,
    tutor_imports.router,
    learners.router,
    learner_imports.router,
    cohorts.router,
    allocation_routes.router,
    attendance.router,
    attendance_summary.router,
    reports.router,
    audit_routes.router,
    settings.router,
    bud_sync.router,
):
    app.include_router(router, prefix="/api")


static_dir = Path(os.environ.get("STATIC_DIR", "")).resolve()
index_file = static_dir / "index.html"

if index_file.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        requested = (static_dir / full_path).resolve()
        if requested.is_file() and static_dir in requested.parents:
            return FileResponse(requested)
        return FileResponse(index_file)
