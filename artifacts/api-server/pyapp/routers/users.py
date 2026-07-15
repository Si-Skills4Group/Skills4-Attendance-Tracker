from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import USER_SELECT, _user_public, require_admin
from ..audit import write_audit_log
from ..db import get_cursor

router = APIRouter(tags=["users"])


class UserProvisionInput(BaseModel):
    entraObjectId: str = Field(min_length=1)
    entraTenantId: str = Field(min_length=1)
    email: str = Field(min_length=1)
    firstName: str = Field(min_length=1)
    lastName: str = Field(default="", min_length=0)
    displayName: str | None = None
    role: str
    tutorId: int | None = None
    active: bool = True


class UserUpdateInput(BaseModel):
    email: str | None = Field(default=None, min_length=1)
    firstName: str | None = Field(default=None, min_length=1)
    lastName: str | None = None
    displayName: str | None = None
    role: str | None = None
    tutorId: int | None = None
    active: bool | None = None


class UserRoleInput(BaseModel):
    role: str


class UserLinkTutorInput(BaseModel):
    tutorId: int | None = None


def _validate_role_mapping(role: str, tutor_id: int | None) -> None:
    if role not in {"admin", "tutor"}:
        raise HTTPException(status_code=400, detail="role must be admin or tutor")
    if role == "tutor" and tutor_id is None:
        raise HTTPException(status_code=400, detail="Tutor users must be linked to a tutor record")


def _ensure_tutor_exists(cur, tutor_id: int | None) -> None:
    if tutor_id is None:
        return
    cur.execute("SELECT id FROM tutors WHERE id = %s", (tutor_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=400, detail="Tutor record not found")


def _ensure_tutor_not_linked_elsewhere(cur, tutor_id: int | None, exclude_user_id: int | None) -> None:
    if tutor_id is None:
        return
    cur.execute(
        "SELECT id FROM users WHERE tutor_id = %s AND active = true AND id != %s",
        (tutor_id, exclude_user_id or 0),
    )
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="This tutor record is already linked to another active user")


def _active_admin_count(cur) -> int:
    cur.execute("SELECT count(*)::int AS count FROM users WHERE role = 'admin' AND active = true")
    return cur.fetchone()["count"]


@router.get("/users")
def list_users(
    search: str | None = None,
    role: str | None = None,
    active: bool | None = None,
    _session: dict = Depends(require_admin),
):
    clauses = []
    params: list = []
    if search:
        clauses.append(
            "(first_name ILIKE %s OR last_name ILIKE %s OR email ILIKE %s OR entra_object_id ILIKE %s)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])
    if role:
        clauses.append("role = %s")
        params.append(role)
    if active is not None:
        clauses.append("active = %s")
        params.append(active)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_cursor() as cur:
        cur.execute(f"{USER_SELECT} {where} ORDER BY last_name, first_name", params)
        return [_user_public(row) for row in cur.fetchall()]


@router.post("/users", status_code=201)
def provision_user(payload: UserProvisionInput, request: Request, _session: dict = Depends(require_admin)):
    _validate_role_mapping(payload.role, payload.tutorId)
    email = payload.email.lower()
    with get_cursor() as cur:
        _ensure_tutor_exists(cur, payload.tutorId)
        cur.execute(
            "SELECT id FROM users WHERE entra_tenant_id = %s AND entra_object_id = %s",
            (payload.entraTenantId, payload.entraObjectId),
        )
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="This Entra identity is already provisioned")
        cur.execute(f"{USER_SELECT} WHERE email = %s", (email,))
        existing_by_email = cur.fetchone()
        if existing_by_email and existing_by_email["entraObjectId"]:
            raise HTTPException(status_code=400, detail="A user with this email is already mapped to Entra")

        _ensure_tutor_not_linked_elsewhere(
            cur, payload.tutorId, existing_by_email["id"] if existing_by_email else None
        )

        if existing_by_email:
            cur.execute(
                """
                UPDATE users
                SET first_name = %s, last_name = %s, display_name = %s, role = %s, active = %s,
                    tutor_id = %s, entra_object_id = %s, entra_tenant_id = %s, updated_at = now()
                WHERE id = %s
                """,
                (
                    payload.firstName,
                    payload.lastName,
                    payload.displayName,
                    payload.role,
                    payload.active,
                    payload.tutorId,
                    payload.entraObjectId,
                    payload.entraTenantId,
                    existing_by_email["id"],
                ),
            )
            user_id = existing_by_email["id"]
        else:
            cur.execute(
                """
                INSERT INTO users
                  (first_name, last_name, display_name, email, role, active, tutor_id, entra_object_id, entra_tenant_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    payload.firstName,
                    payload.lastName,
                    payload.displayName,
                    email,
                    payload.role,
                    payload.active,
                    payload.tutorId,
                    payload.entraObjectId,
                    payload.entraTenantId,
                ),
            )
            user_id = cur.fetchone()["id"]
        cur.execute(f"{USER_SELECT} WHERE id = %s", (user_id,))
        user = cur.fetchone()

    write_audit_log(request, action="provision", entity_type="user", entity_id=user_id, new_value=_user_public(user))
    return _user_public(user)


@router.get("/users/{user_id}")
def get_user(user_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute(f"{USER_SELECT} WHERE id = %s", (user_id,))
        user = cur.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_public(user)


def _apply_user_updates(cur, session: dict, user_id: int, updates: dict) -> tuple[dict, dict]:
    """Core user-mutation logic shared by PATCH and the dedicated action endpoints."""
    cur.execute(f"{USER_SELECT} WHERE id = %s", (user_id,))
    existing = cur.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")

    next_role = updates.get("role", existing["role"])
    next_tutor_id = updates.get("tutorId", existing["tutorId"])
    _validate_role_mapping(next_role, next_tutor_id)
    _ensure_tutor_exists(cur, next_tutor_id)
    if "tutorId" in updates:
        _ensure_tutor_not_linked_elsewhere(cur, updates["tutorId"], user_id)

    if user_id == session.get("userId") and "role" in updates and updates["role"] != existing["role"]:
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    if existing["role"] == "admin" and existing["active"] and (
        updates.get("role") == "tutor" or updates.get("active") is False
    ):
        if _active_admin_count(cur) <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the final active administrator")

    column_map = {
        "email": "email",
        "firstName": "first_name",
        "lastName": "last_name",
        "displayName": "display_name",
        "role": "role",
        "tutorId": "tutor_id",
        "active": "active",
    }
    set_clauses = [f"{column_map[key]} = %s" for key in updates]
    params = [value.lower() if key == "email" and isinstance(value, str) else value for key, value in updates.items()]
    if set_clauses:
        cur.execute(
            f"UPDATE users SET {', '.join(set_clauses)}, updated_at = now() WHERE id = %s",
            [*params, user_id],
        )
    cur.execute(f"{USER_SELECT} WHERE id = %s", (user_id,))
    updated = cur.fetchone()
    return existing, updated


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: UserUpdateInput, request: Request, session: dict = Depends(require_admin)):
    updates = payload.model_dump(exclude_unset=True)
    with get_cursor() as cur:
        existing, updated = _apply_user_updates(cur, session, user_id, updates)

    write_audit_log(
        request,
        action="update",
        entity_type="user",
        entity_id=user_id,
        previous_value=_user_public(existing),
        new_value=_user_public(updated),
    )
    return _user_public(updated)


@router.post("/users/{user_id}/activate")
def activate_user(user_id: int, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        existing, updated = _apply_user_updates(cur, session, user_id, {"active": True})

    write_audit_log(
        request,
        action="activate",
        entity_type="user",
        entity_id=user_id,
        previous_value=_user_public(existing),
        new_value=_user_public(updated),
    )
    return _user_public(updated)


@router.post("/users/{user_id}/deactivate")
def deactivate_user(user_id: int, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        existing, updated = _apply_user_updates(cur, session, user_id, {"active": False})

    write_audit_log(
        request,
        action="deactivate",
        entity_type="user",
        entity_id=user_id,
        previous_value=_user_public(existing),
        new_value=_user_public(updated),
    )
    return _user_public(updated)


@router.post("/users/{user_id}/role")
def change_user_role(user_id: int, payload: UserRoleInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        existing, updated = _apply_user_updates(cur, session, user_id, {"role": payload.role})

    write_audit_log(
        request,
        action="role_change",
        entity_type="user",
        entity_id=user_id,
        previous_value=_user_public(existing),
        new_value=_user_public(updated),
    )
    return _user_public(updated)


@router.post("/users/{user_id}/link-tutor")
def link_user_tutor(
    user_id: int, payload: UserLinkTutorInput, request: Request, session: dict = Depends(require_admin)
):
    with get_cursor() as cur:
        existing, updated = _apply_user_updates(cur, session, user_id, {"tutorId": payload.tutorId})

    write_audit_log(
        request,
        action="link_tutor",
        entity_type="user",
        entity_id=user_id,
        previous_value=_user_public(existing),
        new_value=_user_public(updated),
    )
    return _user_public(updated)


@router.get("/users/{user_id}/audit")
def get_user_audit(user_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="User not found")
        cur.execute(
            """
            SELECT id, user_id AS "userId", action, entity_type AS "entityType", entity_id AS "entityId",
                   previous_value AS "previousValue", new_value AS "newValue", timestamp, ip_address AS "ipAddress"
            FROM audit_logs
            WHERE user_id = %s OR (entity_type = 'user' AND entity_id = %s)
            ORDER BY timestamp DESC
            LIMIT 100
            """,
            (user_id, user_id),
        )
        return cur.fetchall()
