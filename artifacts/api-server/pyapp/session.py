"""Server-side session middleware backed by the existing `user_sessions`
Postgres table (sid varchar pk, sess json, expire timestamp), the same table
the previous Node/express-session + connect-pg-simple stack used. Reusing it
keeps behavior (12h expiry, sid cookie name) identical across the rewrite.

The cookie value itself is also kept byte-for-byte compatible with
express-session's default *signed* cookie format (`s:<sid>.<hmac-sha256>`,
percent-encoded the same way Node's `cookie` package encodes it) so that
users who are already logged in via the old Express server stay logged in
after the cutover -- their existing browser cookie still verifies and
resolves to the same `sid` row in `user_sessions`.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote

import anyio
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .db import get_cursor

COOKIE_NAME = "s4a.sid"
SESSION_MAX_AGE = timedelta(hours=12)

SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET must be set. Did you forget to provision it?")

IS_PRODUCTION = os.environ.get("NODE_ENV") == "production" or os.environ.get("ENV") == "production"

# encodeURIComponent's "safe" set (RFC 3986 unreserved + a handful of others
# it leaves alone) -- matches what Node's `cookie` package produces when it
# serializes the cookie value.
_ENCODE_URI_SAFE = "!'()*-._~"


def _sign(value: str) -> str:
    """Port of the `cookie-signature` npm package's `sign()`."""
    mac = hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).digest()
    import base64

    sig = base64.b64encode(mac).decode().rstrip("=")
    return f"{value}.{sig}"


def _unsign(signed: str) -> str | None:
    """Port of `cookie-signature`'s `unsign()` -- returns the original value
    if the signature verifies, else None."""
    idx = signed.rfind(".")
    if idx == -1:
        return None
    value = signed[:idx]
    expected = _sign(value)
    if hmac.compare_digest(expected, signed):
        return value
    return None


def _encode_cookie_value(sid: str) -> str:
    return quote(f"s:{_sign(sid)}", safe=_ENCODE_URI_SAFE)


def _decode_cookie_value(raw: str) -> str | None:
    """Given the raw (still percent-encoded) cookie value, return the
    underlying sid if it verifies as a signed express-session cookie, or as
    a plain unsigned value (defensive fallback for older/manually-set
    cookies) -- else None."""
    try:
        decoded = unquote(raw)
    except Exception:
        return None
    if decoded.startswith("s:"):
        return _unsign(decoded[2:])
    return decoded or None


def _parse_cookie_header(header: str, name: str) -> str | None:
    for part in header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip() == name:
            return value.strip()
    return None


def _load_session_sync(sid: str) -> dict | None:
    with get_cursor() as cur:
        cur.execute(
            'SELECT sess, expire FROM user_sessions WHERE sid = %s',
            (sid,),
        )
        row = cur.fetchone()
        if not row:
            return None
        if row["expire"] < datetime.now(row["expire"].tzinfo or None):
            cur.execute("DELETE FROM user_sessions WHERE sid = %s", (sid,))
            return None
        sess = row["sess"]
        return sess if isinstance(sess, dict) else json.loads(sess)


def _save_session_sync(sid: str, data: dict) -> None:
    expire = datetime.now() + SESSION_MAX_AGE
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_sessions (sid, sess, expire) VALUES (%s, %s, %s)
            ON CONFLICT (sid) DO UPDATE SET sess = EXCLUDED.sess, expire = EXCLUDED.expire
            """,
            (sid, json.dumps(data), expire),
        )


def _delete_session_sync(sid: str) -> None:
    with get_cursor() as cur:
        cur.execute("DELETE FROM user_sessions WHERE sid = %s", (sid,))


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        raw_cookie_header = request.headers.get("cookie", "")
        raw_value = _parse_cookie_header(raw_cookie_header, COOKIE_NAME)
        sid = _decode_cookie_value(raw_value) if raw_value else None

        session: dict = {}
        if sid:
            loaded = await anyio.to_thread.run_sync(_load_session_sync, sid)
            if loaded is not None:
                session = loaded
            else:
                sid = None

        request.state.session = session
        request.state.session_id = sid
        request.state.destroy_session = False

        response = await call_next(request)

        if request.state.destroy_session:
            if request.state.session_id:
                await anyio.to_thread.run_sync(_delete_session_sync, request.state.session_id)
            response.delete_cookie(COOKIE_NAME, path="/")
            return response

        session = request.state.session
        if session:
            new_sid = request.state.session_id or secrets.token_hex(24)
            await anyio.to_thread.run_sync(_save_session_sync, new_sid, session)
            cookie_value = _encode_cookie_value(new_sid)
            parts = [
                f"{COOKIE_NAME}={cookie_value}",
                "Path=/",
                f"Max-Age={int(SESSION_MAX_AGE.total_seconds())}",
                "HttpOnly",
                "SameSite=Lax",
            ]
            if IS_PRODUCTION:
                parts.append("Secure")
            response.headers.append("set-cookie", "; ".join(parts))

        return response
