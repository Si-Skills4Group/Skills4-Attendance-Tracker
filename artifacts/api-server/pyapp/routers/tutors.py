from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import hash_password, require_admin
from ..audit import write_audit_log
from ..db import get_cursor

router = APIRouter(tags=["tutors"])

TUTOR_SELECT = (
    'SELECT id, user_id AS "userId", first_name AS "firstName", last_name AS "lastName", '
    'email, employee_ref AS "employeeRef", active, external_system_id AS "externalSystemId", '
    'created_at AS "createdAt", updated_at AS "updatedAt" FROM tutors'
)


class TutorInput(BaseModel):
    firstName: str = Field(min_length=1)
    lastName: str = Field(min_length=1)
    email: str = Field(min_length=1)
    password: str = Field(min_length=8)
    employeeRef: str = Field(min_length=1)
    active: bool = True
    externalSystemId: str | None = None


class TutorUpdate(BaseModel):
    firstName: str | None = Field(default=None, min_length=1)
    lastName: str | None = Field(default=None, min_length=1)
    email: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=8)
    employeeRef: str | None = Field(default=None, min_length=1)
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


@router.post("/tutors", status_code=201)
def create_tutor(payload: TutorInput, request: Request, _session: dict = Depends(require_admin)):
    email = payload.email.lower()
    with get_cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="A user with this email already exists")

        password_hash = hash_password(payload.password)
        cur.execute(
            """
            INSERT INTO users (first_name, last_name, email, password_hash, role)
            VALUES (%s, %s, %s, %s, 'tutor') RETURNING id
            """,
            (payload.firstName, payload.lastName, email, password_hash),
        )
        user_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO tutors (user_id, first_name, last_name, email, employee_ref, active, external_system_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                user_id,
                payload.firstName,
                payload.lastName,
                email,
                payload.employeeRef,
                payload.active,
                payload.externalSystemId,
            ),
        )
        tutor_id = cur.fetchone()["id"]

        cur.execute("UPDATE users SET tutor_id = %s WHERE id = %s", (tutor_id, user_id))

        cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
        tutor = cur.fetchone()

    write_audit_log(request, action="create", entity_type="tutor", entity_id=tutor_id, new_value=tutor)
    return tutor


@router.get("/tutors/{tutor_id}")
def get_tutor(tutor_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
        tutor = cur.fetchone()
    if not tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
    return tutor


@router.patch("/tutors/{tutor_id}")
def update_tutor(tutor_id: int, payload: TutorUpdate, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM tutors WHERE id = %s", (tutor_id,))
        existing = cur.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Tutor not found")

        updates = payload.model_dump(exclude_unset=True, exclude={"password"})
        column_map = {
            "firstName": "first_name",
            "lastName": "last_name",
            "email": "email",
            "employeeRef": "employee_ref",
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
                f"UPDATE tutors SET {', '.join(set_clauses)} WHERE id = %s RETURNING id",
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
    )
    return tutor
