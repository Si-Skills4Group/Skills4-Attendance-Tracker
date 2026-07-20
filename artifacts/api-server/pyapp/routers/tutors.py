from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import hash_password, require_admin
from ..audit import write_audit_log
from ..db import get_cursor

router = APIRouter(tags=["tutors"])

TUTOR_SELECT = (
    'SELECT id, user_id AS "userId", first_name AS "firstName", last_name AS "lastName", '
    'email, employee_ref AS "employeeRef", phone, active, external_system_id AS "externalSystemId", '
    'created_at AS "createdAt", updated_at AS "updatedAt" FROM tutors'
)


def _active_cohorts_for_tutor(cur, tutor_id: int) -> list:
    cur.execute(
        'SELECT id, name FROM cohorts WHERE tutor_id = %s AND active = true AND deleted_at IS NULL', (tutor_id,)
    )
    return cur.fetchall()


class TutorInput(BaseModel):
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    email: str = Field(min_length=1)
    password: str | None = Field(default=None, min_length=8)
    employeeRef: str | None = Field(default=None, min_length=1)
    phone: str | None = None
    active: bool = True
    externalSystemId: str | None = None


class TutorUpdate(BaseModel):
    firstName: str | None = Field(default=None, min_length=1)
    lastName: str | None = Field(default=None, min_length=1)
    email: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=8)
    employeeRef: str | None = Field(default=None, min_length=1)
    phone: str | None = None
    active: bool | None = None
    externalSystemId: str | None = None


@router.get("/tutors")
def list_tutors(active: bool | None = None, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute(TUTOR_SELECT)
        rows = cur.fetchall()
    if active is not None:
        rows = [r for r in rows if r["active"] == active]
    return rows


def _create_tutor(cur, payload: TutorInput, request: Request, session: dict) -> dict:
    """Cursor-accepting internal shared with the CSV import confirm step
    (tutor_imports.py), which needs this to run inside its own transaction
    rather than opening a separate pooled connection -- mirrors
    routers/learners.py's _create_learner extraction from Phase 5."""
    email = payload.email.lower()
    cur.execute("SELECT id FROM users WHERE email = %s", (email,))
    if cur.fetchone():
        raise HTTPException(status_code=400, detail="A user with this email already exists")

    cur.execute(
        """
        INSERT INTO users (first_name, last_name, email, password_hash, role, active)
        VALUES (%s, %s, %s, %s, 'tutor', %s) RETURNING id
        """,
        (
            payload.firstName,
            payload.lastName,
            email,
            hash_password(payload.password) if payload.password else None,
            payload.active,
        ),
    )
    user_id = cur.fetchone()["id"]

    cur.execute(
        """
        INSERT INTO tutors (user_id, first_name, last_name, email, employee_ref, phone, active, external_system_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            user_id,
            payload.firstName,
            payload.lastName,
            email,
            payload.employeeRef,
            payload.phone,
            payload.active,
            payload.externalSystemId,
        ),
    )
    tutor_id = cur.fetchone()["id"]

    cur.execute("UPDATE users SET tutor_id = %s WHERE id = %s", (tutor_id, user_id))

    cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
    tutor = cur.fetchone()

    write_audit_log(request, action="create", entity_type="tutor", entity_id=tutor_id, new_value=tutor, cur=cur)
    return tutor


@router.post("/tutors", status_code=201)
def create_tutor(payload: TutorInput, request: Request, session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        return _create_tutor(cur, payload, request, session)


@router.get("/tutors/{tutor_id}")
def get_tutor(tutor_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
        tutor = cur.fetchone()
    if not tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    return tutor


def _update_tutor(cur, tutor_id: int, payload: TutorUpdate, request: Request, session: dict, confirm: bool) -> dict:
    """Cursor-accepting internal shared with the CSV import confirm step --
    see _create_tutor's docstring."""
    cur.execute("SELECT * FROM tutors WHERE id = %s", (tutor_id,))
    existing = cur.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Tutor not found")

    updates = payload.model_dump(exclude_unset=True, exclude={"password"})

    if updates.get("active") is False and existing["active"]:
        active_cohorts = _active_cohorts_for_tutor(cur, tutor_id)
        if active_cohorts and not confirm:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "active_cohorts_assigned",
                    "message": "This tutor has active cohorts assigned. Confirm to deactivate anyway.",
                    "cohorts": active_cohorts,
                },
            )

    column_map = {
        "firstName": "first_name",
        "lastName": "last_name",
        "email": "email",
        "employeeRef": "employee_ref",
        "phone": "phone",
        "active": "active",
        "externalSystemId": "external_system_id",
    }
    set_clauses = []
    params: list = []
    for key, value in updates.items():
        set_clauses.append(f"{column_map[key]} = %s")
        params.append(value)

    if set_clauses:
        cur.execute(
            f"UPDATE tutors SET {', '.join(set_clauses)}, updated_at = now() WHERE id = %s RETURNING id",
            [*params, tutor_id],
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Tutor not found")

    user_updates: dict = {}
    if "firstName" in updates:
        user_updates["first_name"] = updates["firstName"]
    if "lastName" in updates:
        user_updates["last_name"] = updates["lastName"]
    if "email" in updates:
        user_updates["email"] = updates["email"].lower()
    if "active" in updates:
        user_updates["active"] = updates["active"]
    if payload.password:
        user_updates["password_hash"] = hash_password(payload.password)

    if user_updates:
        set_clauses = [f"{col} = %s" for col in user_updates]
        cur.execute(
            f"UPDATE users SET {', '.join(set_clauses)} WHERE id = %s",
            [*user_updates.values(), existing["user_id"]],
        )

    cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
    tutor = cur.fetchone()

    write_audit_log(
        request,
        action="update",
        entity_type="tutor",
        entity_id=tutor_id,
        previous_value=existing,
        new_value=tutor,
        cur=cur,
    )
    return tutor


@router.patch("/tutors/{tutor_id}")
def update_tutor(
    tutor_id: int,
    payload: TutorUpdate,
    request: Request,
    confirm: bool = False,
    _session: dict = Depends(require_admin),
):
    with get_cursor() as cur:
        return _update_tutor(cur, tutor_id, payload, request, _session, confirm)


@router.post("/tutors/{tutor_id}/activate")
def activate_tutor(tutor_id: int, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM tutors WHERE id = %s", (tutor_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Tutor not found")

        cur.execute("UPDATE tutors SET active = true, updated_at = now() WHERE id = %s", (tutor_id,))
        cur.execute("UPDATE users SET active = true WHERE id = %s", (existing["user_id"],))
        cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
        tutor = cur.fetchone()

    write_audit_log(request, action="activate", entity_type="tutor", entity_id=tutor_id, previous_value=existing, new_value=tutor)
    return tutor


@router.post("/tutors/{tutor_id}/deactivate")
def deactivate_tutor(tutor_id: int, request: Request, confirm: bool = False, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM tutors WHERE id = %s", (tutor_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Tutor not found")

        active_cohorts = _active_cohorts_for_tutor(cur, tutor_id)
        if active_cohorts and not confirm:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "active_cohorts_assigned",
                    "message": "This tutor has active cohorts assigned. Confirm to deactivate anyway.",
                    "cohorts": active_cohorts,
                },
            )

        cur.execute("UPDATE tutors SET active = false, updated_at = now() WHERE id = %s", (tutor_id,))
        cur.execute("UPDATE users SET active = false WHERE id = %s", (existing["user_id"],))
        cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
        tutor = cur.fetchone()

    write_audit_log(request, action="deactivate", entity_type="tutor", entity_id=tutor_id, previous_value=existing, new_value=tutor)
    return tutor
