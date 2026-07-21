"""Request correlation IDs.

Every request gets a correlation ID: a caller-supplied `X-Correlation-Id`
header is reused if it's a plausible ID (bounded length, safe charset),
otherwise one is generated server-side. It's stashed both on
`request.state.correlation_id` (for handlers that already have a Request)
and in a contextvar (for logging/audit code that doesn't), echoed back as a
response header on every response, and threaded into structured logs and
audit rows so a support engineer can go from a logged error straight to the
matching audit trail for that request.
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-Id"

# Bounded length and a safe charset -- an incoming header is just untrusted
# client input; reject anything that isn't plausibly an ID (UUIDs, ULIDs,
# and similar all fit this) rather than reflecting arbitrary/oversized
# strings back into logs and audit rows.
_MAX_LEN = 100
_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
_current_request: ContextVar[Request | None] = ContextVar("current_request", default=None)


def get_correlation_id() -> str:
    """Reads the current request's correlation ID from context. Returns ''
    outside of a request (e.g. at import time, in a background task)."""
    return _correlation_id.get()


def get_current_request() -> Request | None:
    """Reads the in-flight Request from context, for code that needs one
    (e.g. auditing an object-level 403) without it being threaded through
    every intervening function signature and call site. Returns None
    outside of a request -- callers must tolerate that (e.g. direct unit
    tests of access-check logic that don't go through the middleware)."""
    return _current_request.get()


def _sanitize_incoming(value: str | None) -> str | None:
    if not value or len(value) > _MAX_LEN:
        return None
    if not _SAFE_PATTERN.match(value):
        return None
    return value


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = _sanitize_incoming(request.headers.get(CORRELATION_HEADER)) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        correlation_token = _correlation_id.set(correlation_id)
        request_token = _current_request.set(request)
        try:
            response = await call_next(request)
        finally:
            _correlation_id.reset(correlation_token)
            _current_request.reset(request_token)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response
