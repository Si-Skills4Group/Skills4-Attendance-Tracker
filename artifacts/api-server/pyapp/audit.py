import json
from typing import Any

from fastapi import Request

from .db import get_cursor


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def write_audit_log(
    request: Request,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    previous_value: Any = None,
    new_value: Any = None,
) -> None:
    session = getattr(request.state, "session", {}) or {}
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit_logs (user_id, action, entity_type, entity_id, previous_value, new_value, ip_address)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session.get("userId"),
                action,
                entity_type,
                entity_id,
                json.dumps(previous_value, default=str) if previous_value is not None else None,
                json.dumps(new_value, default=str) if new_value is not None else None,
                _client_ip(request),
            ),
        )
