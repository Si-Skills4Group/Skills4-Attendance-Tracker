import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .session import SessionMiddleware
from .routers import (
    health,
    auth_routes,
    dashboard,
    tutors,
    learners,
    cohorts,
    allocation_routes,
    attendance,
    reports,
    audit_routes,
    settings,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("skills4attendance-api")

app = FastAPI(title="Skills4Attendance API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware)


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    detail = exc.detail
    body = detail if isinstance(detail, dict) else {"error": detail}
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


api = FastAPI()
for router in (
    health.router,
    auth_routes.router,
    dashboard.router,
    tutors.router,
    learners.router,
    cohorts.router,
    allocation_routes.router,
    attendance.router,
    reports.router,
    audit_routes.router,
    settings.router,
):
    app.include_router(router, prefix="/api")
