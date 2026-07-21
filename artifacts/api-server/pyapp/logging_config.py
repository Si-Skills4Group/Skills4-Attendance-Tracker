"""Structured application logging.

Every request produces exactly one JSON log line (timestamp, level,
environment, service, correlation id, route, method, status, duration,
user id, outcome) at INFO, escalated to WARNING when the request took
longer than its route class's slow-request threshold. Compatible with
Log Analytics / Application Insights, which both ingest stdout as text and
parse JSON fields out of it.

Never log: access/refresh tokens, Authorization headers, passwords, raw
learner CSV content, attendance notes, full learner records, or connection
strings. Only route path, method, status, timing, and user id are captured
here -- request/response bodies are never touched.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .config import get_auth_settings
from .correlation import get_correlation_id

SERVICE_NAME = "skills4attendance-api"

_LOG_RECORD_FIELDS = (
    "correlationId",
    "route",
    "method",
    "statusCode",
    "durationMs",
    "userId",
    "outcome",
    "environment",
)

# Slow-request thresholds in seconds, by route prefix -- deliberately named
# constants in one place rather than magic numbers scattered across
# routers. Longest match wins; anything unmatched uses the default.
SLOW_THRESHOLDS_SECONDS: dict[str, float] = {
    "/api/reports": 3.0,
    "/api/dashboard": 1.0,
    "/api/attendance/sessions": 2.0,
    "/api/learners/import-jobs": 2.0,
    "/api/tutors/import-jobs": 2.0,
}
DEFAULT_SLOW_THRESHOLD_SECONDS = 1.5

request_logger = logging.getLogger("skills4attendance-api.request")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "message": record.getMessage(),
        }
        for field in _LOG_RECORD_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def _slow_threshold_for(path: str) -> float:
    best_match_len = -1
    threshold = DEFAULT_SLOW_THRESHOLD_SECONDS
    for prefix, prefix_threshold in SLOW_THRESHOLDS_SECONDS.items():
        if path.startswith(prefix) and len(prefix) > best_match_len:
            best_match_len = len(prefix)
            threshold = prefix_threshold
    return threshold


def _current_environment() -> str:
    return "production" if get_auth_settings().is_production else "development"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        threshold_seconds = _slow_threshold_for(request.url.path)
        is_slow = (duration_ms / 1000) > threshold_seconds
        extra = {
            "correlationId": get_correlation_id(),
            "route": request.url.path,
            "method": request.method,
            "statusCode": response.status_code,
            "durationMs": duration_ms,
            "userId": getattr(request.state, "current_user_id", None),
            "outcome": "success" if response.status_code < 400 else "error",
            "environment": _current_environment(),
        }
        message = f"{request.method} {request.url.path} {response.status_code} {duration_ms}ms"
        if is_slow:
            message += f" (slow: exceeded {threshold_seconds}s threshold)"
        request_logger.log(logging.WARNING if is_slow else logging.INFO, message, extra=extra)
        return response
