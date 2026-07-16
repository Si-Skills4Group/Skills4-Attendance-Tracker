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
    cur=None,
) -> None:
    """Writes one audit_logs row. By default opens its own cursor (a new
    pooled connection); pass an existing cursor via `cur` when the write
    must participate in a caller's transaction (e.g. the CSV import
    confirm step) rather than committing independently under autocommit."""
    session = getattr(request.state, "session", {}) or {}
    current_user_id = getattr(request.state, "current_user_id", None)
    sql = """
        INSERT INTO audit_logs (user_id, action, entity_type, entity_id, previous_value, new_value, ip_address)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    params = (
        current_user_id or session.get("userId"),
        action,
        entity_type,
        entity_id,
        json.dumps(previous_value, default=str) if previous_value is not None else None,
        json.dumps(new_value, default=str) if new_value is not None else None,
        _client_ip(request),
    )
    if cur is not None:
        cur.execute(sql, params)
        return
    with get_cursor() as new_cur:
        new_cur.execute(sql, params)
