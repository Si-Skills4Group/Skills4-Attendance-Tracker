from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from .config import AuthSettings, get_auth_settings


class TokenValidationError(Exception):
    pass


@dataclass(frozen=True)
class EntraIdentity:
    object_id: str
    tenant_id: str
    subject: str
    email: str | None
    first_name: str | None
    last_name: str | None
    display_name: str | None
    claims: dict[str, Any]


_metadata_cache: dict[str, Any] | None = None
_metadata_expires_at = 0.0
_jwks_client: PyJWKClient | None = None


def _metadata(settings: AuthSettings) -> dict[str, Any]:
    global _metadata_cache, _metadata_expires_at
    now = time.time()
    if _metadata_cache and _metadata_expires_at > now:
        return _metadata_cache

    if not settings.entra_authority:
        raise TokenValidationError("Identity provider is not configured")

    url = f"{settings.entra_authority.rstrip('/')}/.well-known/openid-configuration"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            _metadata_cache = json.loads(response.read().decode("utf-8"))
            _metadata_expires_at = now + 3600
            return _metadata_cache
    except Exception as exc:
        raise TokenValidationError("Could not load identity provider metadata") from exc


def _jwk_client(settings: AuthSettings) -> PyJWKClient:
    global _jwks_client
    if _jwks_client:
        return _jwks_client
    metadata = _metadata(settings)
    jwks_uri = metadata.get("jwks_uri")
    if not jwks_uri:
        raise TokenValidationError("Identity provider metadata is missing jwks_uri")
    _jwks_client = PyJWKClient(jwks_uri, cache_keys=True)
    return _jwks_client


def _claim_text(claims: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = claims.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def validate_entra_access_token(token: str) -> EntraIdentity:
    settings = get_auth_settings()
    try:
        signing_key = _jwk_client(settings).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.entra_expected_audience,
            issuer=settings.issuer,
            options={"require": ["exp", "iat", "aud", "iss"]},
            leeway=60,
        )
    except Exception as exc:
        raise TokenValidationError("Invalid access token") from exc

    tenant_id = _claim_text(claims, "tid")
    if not tenant_id or tenant_id != settings.entra_allowed_tenant_id:
        raise TokenValidationError("Unexpected token tenant")

    token_version = claims.get("ver")
    if token_version and token_version != "2.0":
        raise TokenValidationError("Unsupported token version")

    scopes = set((_claim_text(claims, "scp") or "").split())
    if settings.entra_required_scope not in scopes:
        raise TokenValidationError("Token lacks required delegated scope")

    object_id = _claim_text(claims, "oid")
    subject = _claim_text(claims, "sub")
    if not object_id or not subject:
        raise TokenValidationError("Token lacks a stable subject")

    return EntraIdentity(
        object_id=object_id,
        tenant_id=tenant_id,
        subject=subject,
        email=_claim_text(claims, "preferred_username", "upn", "email"),
        first_name=_claim_text(claims, "given_name"),
        last_name=_claim_text(claims, "family_name"),
        display_name=_claim_text(claims, "name"),
        claims=claims,
    )
