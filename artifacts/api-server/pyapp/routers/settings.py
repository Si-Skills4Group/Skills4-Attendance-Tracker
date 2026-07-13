from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..auth import require_admin, require_auth
from ..audit import write_audit_log
from ..db import get_cursor

router = APIRouter(tags=["settings"])

SETTINGS_SELECT = (
    'SELECT id, organisation_name AS "organisationName", '
    'low_attendance_threshold AS "lowAttendanceThreshold" FROM app_settings'
)


class SettingsUpdate(BaseModel):
    organisationName: str | None = Field(default=None, min_length=1)
    lowAttendanceThreshold: float | None = Field(default=None, ge=0, le=100)


def _get_or_create_settings(cur) -> dict:
    cur.execute(SETTINGS_SELECT)
    existing = cur.fetchone()
    if existing:
        return existing
    cur.execute(f"INSERT INTO app_settings DEFAULT VALUES RETURNING id")
    new_id = cur.fetchone()["id"]
    cur.execute(f"{SETTINGS_SELECT} WHERE id = %s", (new_id,))
    return cur.fetchone()


@router.get("/settings")
def get_settings(_session: dict = Depends(require_auth)):
    with get_cursor() as cur:
        return _get_or_create_settings(cur)


@router.patch("/settings")
def update_settings(payload: SettingsUpdate, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        existing = _get_or_create_settings(cur)

        updates = payload.model_dump(exclude_unset=True)
        column_map = {"organisationName": "organisation_name", "lowAttendanceThreshold": "low_attendance_threshold"}
        set_clauses = [f"{column_map[k]} = %s" for k in updates]
        params = list(updates.values())
        if set_clauses:
            cur.execute(
                f"UPDATE app_settings SET {', '.join(set_clauses)} WHERE id = %s",
                [*params, existing["id"]],
            )

        cur.execute(f"{SETTINGS_SELECT} WHERE id = %s", (existing["id"],))
        updated = cur.fetchone()

    write_audit_log(
        request, action="update", entity_type="settings", entity_id=updated["id"], previous_value=existing, new_value=updated
    )
    return updated
