import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..auth import USER_SELECT, _user_public, require_auth, verify_password
from ..audit import write_audit_log
from ..config import get_auth_settings
from ..db import get_cursor

router = APIRouter(tags=["auth"])

# Simple in-memory fixed-window-ish rate limiter mirroring the previous
# express-rate-limit config: 10 attempts / 15 minutes per client IP.
_login_attempts: dict[str, deque] = defaultdict(deque)
_WINDOW_SECONDS = 15 * 60
_MAX_ATTEMPTS = 10


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
    now = time.time()
    attempts = _login_attempts[key]
    while attempts and attempts[0] < now - _WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= _MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    attempts.append(now)

    if not payload.email or not payload.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    with get_cursor() as cur:
        cur.execute(f"{USER_SELECT} WHERE email = %s", (payload.email.lower(),))
        user = cur.fetchone()

    if not user or not user["active"] or not verify_password(payload.password, user["passwordHash"]):
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
