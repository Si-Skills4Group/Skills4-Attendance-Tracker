from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..auth import USER_SELECT, _user_public, require_auth, verify_password
from ..audit import write_audit_log
from ..config import get_auth_settings
from ..db import get_cursor
from ..login_rate_limit import check_and_record_login_attempt

router = APIRouter(tags=["auth"])


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class LoginInput(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(payload: LoginInput, request: Request):
    settings = get_auth_settings()
    if settings.auth_mode != "local":
        raise HTTPException(status_code=410, detail="Password login has been replaced by Microsoft sign-in")

    key = _client_key(request)
    with get_cursor() as cur:
        with cur.connection.transaction():
            check_and_record_login_attempt(cur, key)

    if not payload.email or not payload.password:
        write_audit_log(request, action="login_failed", entity_type="security", new_value={"reason": "missing_credentials"})
        raise HTTPException(status_code=401, detail="Invalid credentials")

    with get_cursor() as cur:
        cur.execute(f"{USER_SELECT} WHERE email = %s", (payload.email.lower(),))
        user = cur.fetchone()

    if not user or not user["active"] or not verify_password(payload.password, user["passwordHash"]):
        write_audit_log(
            request, action="login_failed", entity_type="security",
            entity_id=user["id"] if user else None,
            new_value={"reason": "invalid_credentials", "email": payload.email.lower()},
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    request.state.session["userId"] = user["id"]
    request.state.session["role"] = user["role"]
    request.state.session["tutorId"] = user["tutorId"]

    write_audit_log(request, action="login", entity_type="user", entity_id=user["id"])

    return _user_public(user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request):
    if hasattr(request.state, "destroy_session"):
        request.state.destroy_session = True
    return None


@router.get("/auth/me")
def get_current_user(request: Request):
    require_auth(request)
    user = getattr(request.state, "current_user", None)
    if user:
        return user
    user_id = request.state.session.get("userId")
    with get_cursor() as cur:
        cur.execute(f"{USER_SELECT} WHERE id = %s", (user_id,))
        row = cur.fetchone()
    if not row or not row["active"]:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _user_public(row)
