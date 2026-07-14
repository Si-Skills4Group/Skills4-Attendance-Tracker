from __future__ import annotations

import logging
from typing import Any

import bcrypt
from fastapi import HTTPException, Request

from .audit import write_audit_log
from .config import get_auth_settings
from .db import get_cursor
from .entra import EntraIdentity, TokenValidationError, validate_entra_access_token

logger = logging.getLogger("skills4attendance-api")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _user_public(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "firstName": user["firstName"],
        "lastName": user["lastName"],
        "displayName": user.get("displayName"),
        "email": user["email"],
        "role": user["role"],
        "active": user["active"],
        "tutorId": user["tutorId"],
        "entraObjectId": user.get("entraObjectId"),
        "entraTenantId": user.get("entraTenantId"),
        "lastLoginAt": user.get("lastLoginAt"),
    }


USER_SELECT = (
    'SELECT id, first_name AS "firstName", last_name AS "lastName", display_name AS "displayName", '
    'email, password_hash AS "passwordHash", role, active, tutor_id AS "tutorId", '
    'entra_object_id AS "entraObjectId", entra_tenant_id AS "entraTenantId", '
    'last_login_at AS "lastLoginAt", created_at AS "createdAt", updated_at AS "updatedAt" FROM users'
)


def _split_display_name(display_name: str | None, fallback_email: str | None) -> tuple[str, str]:
    source = (display_name or fallback_email or "Unknown User").strip()
    parts = source.split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return source, ""


def _load_entra_user(identity: EntraIdentity, request: Request) -> dict[str, Any]:
    with get_cursor() as cur:
        cur.execute(
            f"{USER_SELECT} WHERE entra_tenant_id = %s AND entra_object_id = %s",
            (identity.tenant_id, identity.object_id),
        )
        user = cur.fetchone()

        if not user and identity.email:
            cur.execute(
                f"{USER_SELECT} WHERE entra_object_id IS NULL AND email = %s",
                (identity.email.lower(),),
            )
            unlinked = cur.fetchone()
            if unlinked:
                cur.execute(
                    "UPDATE users SET entra_object_id = %s, entra_tenant_id = %s, updated_at = now() WHERE id = %s",
                    (identity.object_id, identity.tenant_id, unlinked["id"]),
                )
                write_audit_log(
                    request,
                    action="entra_auto_linked",
                    entity_type="user",
                    entity_id=unlinked["id"],
                    new_value={"entraObjectId": identity.object_id, "entraTenantId": identity.tenant_id, "email": identity.email},
                )
                cur.execute(f"{USER_SELECT} WHERE id = %s", (unlinked["id"],))
                user = cur.fetchone()

        if not user:
            write_audit_log(
                request,
                action="entra_user_not_provisioned",
                entity_type="security",
                new_value={
                    "entraObjectId": identity.object_id,
                    "entraTenantId": identity.tenant_id,
                    "email": identity.email,
                    "displayName": identity.display_name,
                },
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "access_not_provisioned",
                    "message": "Your Microsoft account is authenticated but has not been provisioned for Skills4Attendance.",
                    "identity": {
                        "entraObjectId": identity.object_id,
                        "entraTenantId": identity.tenant_id,
                        "email": identity.email,
                        "displayName": identity.display_name,
                    },
                },
            )

        if not user["active"]:
            request.state.current_user_id = user["id"]
            write_audit_log(
                request,
                action="inactive_user_denied",
                entity_type="security",
                entity_id=user["id"],
                new_value={"entraObjectId": identity.object_id, "entraTenantId": identity.tenant_id},
            )
            raise HTTPException(status_code=403, detail="Your Skills4Attendance account is inactive.")

        first_name = identity.first_name or user["firstName"]
        last_name = identity.last_name or user["lastName"]
        if not first_name and not last_name:
            first_name, last_name = _split_display_name(identity.display_name, identity.email)

        profile_updates = {
            "email": (identity.email or user["email"]).lower(),
            "first_name": first_name,
            "last_name": last_name,
            "display_name": identity.display_name or user.get("displayName"),
        }
        changed_profile = {
            key: value
            for key, value in profile_updates.items()
            if value is not None
            and value
            and value != {
                "email": user["email"],
                "first_name": user["firstName"],
                "last_name": user["lastName"],
                "display_name": user.get("displayName"),
            }[key]
        }
        cur.execute(
            """
            UPDATE users
            SET email = %s, first_name = %s, last_name = %s, display_name = %s,
                last_login_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (
                profile_updates["email"],
                profile_updates["first_name"],
                profile_updates["last_name"],
                profile_updates["display_name"],
                user["id"],
            ),
        )
        cur.execute(f"{USER_SELECT} WHERE id = %s", (user["id"],))
        updated = cur.fetchone()

    request.state.current_user_id = updated["id"]
    request.state.current_user = _user_public(updated)
    request.state.session = {
        "userId": updated["id"],
        "role": updated["role"],
        "tutorId": updated["tutorId"],
    }
    if changed_profile:
        write_audit_log(
            request,
            action="entra_profile_updated",
            entity_type="user",
            entity_id=updated["id"],
            previous_value={
                "email": user["email"],
                "firstName": user["firstName"],
                "lastName": user["lastName"],
                "displayName": user.get("displayName"),
            },
            new_value=changed_profile,
        )
    return request.state.session


def get_current_identity(request: Request) -> EntraIdentity:
    token = _bearer_token(request)
    if not token:
        logger.warning("Entra auth failed: no bearer token on %s", request.url.path)
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        identity = validate_entra_access_token(token)
    except TokenValidationError as exc:
        logger.warning("Entra auth failed: %s on %s", exc, request.url.path)
        raise HTTPException(status_code=401, detail="Authentication required")
    request.state.current_identity = identity
    return identity


def require_auth(request: Request) -> dict[str, Any]:
    settings = get_auth_settings()
    if settings.auth_mode == "local":
        session = getattr(request.state, "session", {}) or {}
        if not session.get("userId"):
            raise HTTPException(status_code=401, detail="Not authenticated")
        return session
    identity = get_current_identity(request)
    return _load_entra_user(identity, request)


def get_current_application_user(request: Request) -> dict[str, Any]:
    require_auth(request)
    user = getattr(request.state, "current_user", None)
    if user:
        return user
    session = getattr(request.state, "session", {}) or {}
    with get_cursor() as cur:
        cur.execute(f"{USER_SELECT} WHERE id = %s", (session.get("userId"),))
        row = cur.fetchone()
    if not row or not row["active"]:
        raise HTTPException(status_code=403, detail="Application access denied")
    return _user_public(row)


def require_active_user(request: Request) -> dict[str, Any]:
    return require_auth(request)


def require_role(*roles: str):
    def dependency(request: Request) -> dict[str, Any]:
        session = require_auth(request)
        if session.get("role") not in roles:
            write_audit_log(
                request,
                action="authorization_denied",
                entity_type="security",
                new_value={"requiredRoles": roles, "actualRole": session.get("role")},
            )
            raise HTTPException(status_code=403, detail="Access denied")
        return session

    return dependency


def require_admin(request: Request) -> dict[str, Any]:
    return require_role("admin")(request)


def require_administrator(request: Request) -> dict[str, Any]:
    return require_admin(request)


def require_tutor(request: Request) -> dict[str, Any]:
    return require_role("tutor")(request)


def require_cohort_access(cur, cohort_id: int, session: dict[str, Any]) -> dict[str, Any]:
    cur.execute('SELECT id, tutor_id AS "tutorId" FROM cohorts WHERE id = %s', (cohort_id,))
    cohort = cur.fetchone()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")
    if session.get("role") == "tutor" and cohort["tutorId"] != session.get("tutorId"):
        raise HTTPException(status_code=403, detail="Not allowed to access this cohort")
    return cohort


def require_learner_access(cur, learner_id: int, session: dict[str, Any]) -> dict[str, Any]:
    cur.execute('SELECT id, tutor_id AS "tutorId", cohort_id AS "cohortId" FROM learners WHERE id = %s', (learner_id,))
    learner = cur.fetchone()
    if not learner:
        raise HTTPException(status_code=404, detail="Learner not found")
    if session.get("role") == "tutor" and learner["tutorId"] != session.get("tutorId"):
        raise HTTPException(status_code=403, detail="Not allowed to access this learner")
    return learner


def require_attendance_access(cur, session_id: int, session: dict[str, Any]) -> dict[str, Any]:
    cur.execute(
        """
        SELECT s.id, s.cohort_id AS "cohortId", c.tutor_id AS "tutorId"
        FROM attendance_sessions s
        JOIN cohorts c ON s.cohort_id = c.id
        WHERE s.id = %s
        """,
        (session_id,),
    )
    attendance_session = cur.fetchone()
    if not attendance_session:
        raise HTTPException(status_code=404, detail="Attendance session not found")
    if session.get("role") == "tutor" and attendance_session["tutorId"] != session.get("tutorId"):
        raise HTTPException(status_code=403, detail="Not allowed to access this session")
    return attendance_session
