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
from .db import get_cursor
from .learner_import_lib import expire_due_learner_import_jobs
from .login_rate_limit import prune_stale_login_attempts
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
)

logging.basicConfig(level=logging.INFO)
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

app = FastAPI(title="Skills4Attendance API")

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
    body = detail if isinstance(detail, dict) else {"error": detail}
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


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
