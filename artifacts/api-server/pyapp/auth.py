import bcrypt
from fastapi import HTTPException, Request


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def require_auth(request: Request) -> dict:
    session = request.state.session
    if not session.get("userId"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


def require_admin(request: Request) -> dict:
    session = require_auth(request)
    if session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return session
