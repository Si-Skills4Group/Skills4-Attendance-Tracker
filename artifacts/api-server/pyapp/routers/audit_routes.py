from datetime import datetime

from fastapi import APIRouter, Depends

from ..auth import require_admin
from ..db import get_cursor

router = APIRouter(tags=["audit"])


@router.get("/audit-log")
def list_audit_log(
    entityType: str | None = None,
    entityId: int | None = None,
    userId: int | None = None,
    action: str | None = None,
    dateFrom: str | None = None,
    dateTo: str | None = None,
    page: int = 1,
    pageSize: int = 25,
    _session: dict = Depends(require_admin),
):
    clauses = []
    params: list = []
    if entityType:
        clauses.append("a.entity_type = %s")
        params.append(entityType)
    if entityId is not None:
        clauses.append("a.entity_id = %s")
        params.append(entityId)
    if userId is not None:
        clauses.append("a.user_id = %s")
        params.append(userId)
    if action:
        clauses.append("a.action = %s")
        params.append(action)
    if dateFrom:
        clauses.append("a.timestamp >= %s")
        params.append(datetime.fromisoformat(dateFrom))
    if dateTo:
        clauses.append("a.timestamp <= %s")
        params.append(datetime.fromisoformat(dateTo))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT a.id, a.user_id AS "userId",
                   CASE WHEN u.id IS NULL THEN NULL ELSE concat(u.first_name, ' ', u.last_name) END AS "userName",
                   a.action, a.entity_type AS "entityType", a.entity_id AS "entityId",
                   a.previous_value AS "previousValue", a.new_value AS "newValue",
                   a.timestamp, a.ip_address AS "ipAddress"
            FROM audit_logs a
            LEFT JOIN users u ON a.user_id = u.id
            {where}
            ORDER BY a.timestamp DESC
            LIMIT %s OFFSET %s
            """,
            [*params, pageSize, (page - 1) * pageSize],
        )
        items = cur.fetchall()

        cur.execute(f"SELECT count(*)::int AS count FROM audit_logs a {where}", params)
        total = cur.fetchone()["count"]

    return {"items": items, "total": total, "page": page, "pageSize": pageSize}
