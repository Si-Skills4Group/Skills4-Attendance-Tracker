from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import hash_password, require_admin
from ..audit import write_audit_log
from ..csv_utils import (
    TUTOR_CSV_COLUMNS,
    ImportCsvInput,
    PreviewCsvInput,
    parse_csv_to_rows,
    stringify_rows_to_csv,
)
from ..db import get_cursor

router = APIRouter(tags=["tutors"])

TUTOR_SELECT = (
    'SELECT id, user_id AS "userId", first_name AS "firstName", last_name AS "lastName", '
    'email, employee_ref AS "employeeRef", phone, active, external_system_id AS "externalSystemId", '
    'created_at AS "createdAt", updated_at AS "updatedAt" FROM tutors'
)


def _active_cohorts_for_tutor(cur, tutor_id: int) -> list:
    cur.execute('SELECT id, name FROM cohorts WHERE tutor_id = %s AND active = true', (tutor_id,))
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


@router.post("/tutors", status_code=201)
def create_tutor(payload: TutorInput, request: Request, _session: dict = Depends(require_admin)):
    email = payload.email.lower()
    with get_cursor() as cur:
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

    write_audit_log(request, action="create", entity_type="tutor", entity_id=tutor_id, new_value=tutor)
    return tutor


def _parse_csv_active(value: str | None) -> bool:
    if not value:
        return True
    return value.strip().lower() not in {"false", "0", "no"}


@router.get("/tutors/csv-template")
def get_tutor_csv_template(_session: dict = Depends(require_admin)):
    csv_text = stringify_rows_to_csv([], TUTOR_CSV_COLUMNS)
    return {"csv": csv_text, "filename": "tutor-import-template.csv"}


@router.post("/tutors/csv-preview")
def preview_tutor_csv(payload: PreviewCsvInput, _session: dict = Depends(require_admin)):
    try:
        parsed_rows = parse_csv_to_rows(payload.csv)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse CSV content")

    with get_cursor() as cur:
        cur.execute("SELECT email FROM users")
        email_set = {r["email"] for r in cur.fetchall() if r["email"]}

    seen_emails: set[str] = set()
    rows = []
    for index, data in enumerate(parsed_rows):
        row_number = index + 1
        errors = []
        if not data.get("firstName"):
            errors.append("firstName is required")
        if not data.get("lastName"):
            errors.append("lastName is required")
        if not data.get("email"):
            errors.append("email is required")

        is_duplicate = False
        duplicate_reason = None
        email = (data.get("email") or "").lower()
        if email and email in email_set:
            is_duplicate = True
            duplicate_reason = "email already exists"
        elif email and email in seen_emails:
            is_duplicate = True
            duplicate_reason = "duplicate email within this file"
        if email:
            seen_emails.add(email)

        rows.append(
            {
                "rowNumber": row_number,
                "data": data,
                "isDuplicate": is_duplicate,
                "duplicateReason": duplicate_reason,
                "errors": errors,
            }
        )

    total_rows = len(rows)
    invalid_rows = len([r for r in rows if r["errors"]])
    duplicate_rows = len([r for r in rows if r["isDuplicate"]])
    valid_rows = len([r for r in rows if not r["errors"] and not r["isDuplicate"]])

    return {
        "totalRows": total_rows,
        "validRows": valid_rows,
        "invalidRows": invalid_rows,
        "duplicateRows": duplicate_rows,
        "rows": rows,
    }


@router.post("/tutors/csv-import")
def import_tutor_csv(payload: ImportCsvInput, request: Request, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute("SELECT email FROM users")
        existing_emails = {r["email"] for r in cur.fetchall() if r["email"]}

        imported = 0
        skipped = 0
        errors = []

        for index, row in enumerate(payload.rows):
            row_number = index + 1
            required = ["firstName", "lastName", "email"]
            if not all(row.get(f) for f in required):
                errors.append({"rowNumber": row_number, "field": None, "message": "Missing required field"})
                skipped += 1
                continue

            email = row["email"].lower()
            if email in existing_emails:
                errors.append(
                    {"rowNumber": row_number, "field": "email", "message": "Email already exists -- row skipped"}
                )
                skipped += 1
                continue

            active = _parse_csv_active(row.get("active"))

            cur.execute(
                """
                INSERT INTO users (first_name, last_name, email, role, active)
                VALUES (%s, %s, %s, 'tutor', %s) RETURNING id
                """,
                (row["firstName"], row["lastName"], email, active),
            )
            user_id = cur.fetchone()["id"]

            cur.execute(
                """
                INSERT INTO tutors (user_id, first_name, last_name, email, employee_ref, active, external_system_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (
                    user_id,
                    row["firstName"],
                    row["lastName"],
                    email,
                    row.get("employeeRef") or None,
                    active,
                    row.get("externalSystemId") or None,
                ),
            )
            tutor_id = cur.fetchone()["id"]

            cur.execute("UPDATE users SET tutor_id = %s WHERE id = %s", (tutor_id, user_id))

            existing_emails.add(email)
            imported += 1

    write_audit_log(
        request, action="csv_import", entity_type="tutor", new_value={"imported": imported, "skipped": skipped}
    )

    return {"imported": imported, "skipped": skipped, "errors": errors}


@router.get("/tutors/{tutor_id}")
def get_tutor(tutor_id: int, _session: dict = Depends(require_admin)):
    with get_cursor() as cur:
        cur.execute(f"{TUTOR_SELECT} WHERE id = %s", (tutor_id,))
        tutor = cur.fetchone()
    if not tutor:
        raise HTTPException(status_code=404, detail="Tutor not found")
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
    )
    return tutor


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
