from __future__ import annotations

import os
from functools import lru_cache


class AuthSettings:
    def __init__(self) -> None:
        self.environment = os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "development"
        self.auth_mode = os.environ.get("AUTH_MODE", "entra").lower()
        self.entra_tenant_id = os.environ.get("ENTRA_TENANT_ID") or os.environ.get("ENTRA_ALLOWED_TENANT_ID")
        self.entra_allowed_tenant_id = os.environ.get("ENTRA_ALLOWED_TENANT_ID") or self.entra_tenant_id
        self.entra_api_client_id = os.environ.get("ENTRA_API_CLIENT_ID")
        self.entra_expected_audience = os.environ.get("ENTRA_EXPECTED_AUDIENCE") or (
            f"api://{self.entra_api_client_id}" if self.entra_api_client_id else None
        )
        self.entra_authority = (
            os.environ.get("ENTRA_AUTHORITY")
            or (f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0" if self.entra_tenant_id else None)
        )
        self.entra_required_scope = os.environ.get("ENTRA_REQUIRED_SCOPE", "access_as_user")
        self.allowed_origins = [
            origin.strip()
            for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        ]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production" or os.environ.get("NODE_ENV") == "production"

    @property
    def issuer(self) -> str | None:
        if self.entra_tenant_id:
            return f"https://login.microsoftonline.com/{self.entra_tenant_id}/v2.0"
        return None

    def validate_startup(self) -> None:
        if self.auth_mode not in {"entra", "local"}:
            raise RuntimeError("AUTH_MODE must be either 'entra' or 'local'")
        if self.is_production and self.auth_mode != "entra":
            raise RuntimeError("Production startup requires AUTH_MODE=entra")
        if self.auth_mode == "local" and not os.environ.get("SESSION_SECRET"):
            raise RuntimeError("SESSION_SECRET is required when AUTH_MODE=local")
        if self.auth_mode == "entra":
            missing = [
                name
                for name, value in {
                    "ENTRA_TENANT_ID": self.entra_tenant_id,
                    "ENTRA_API_CLIENT_ID": self.entra_api_client_id,
                    "ENTRA_EXPECTED_AUDIENCE": self.entra_expected_audience,
                    "ENTRA_AUTHORITY": self.entra_authority,
                    "ENTRA_REQUIRED_SCOPE": self.entra_required_scope,
                    "ENTRA_ALLOWED_TENANT_ID": self.entra_allowed_tenant_id,
                }.items()
                if not value
            ]
            if missing:
                raise RuntimeError(f"Missing required Entra configuration: {', '.join(missing)}")
        if self.is_production and not self.allowed_origins:
            raise RuntimeError("Production startup requires ALLOWED_ORIGINS")
        if self.is_production and "*" in self.allowed_origins:
            raise RuntimeError("Production CORS must not allow '*'")


@lru_cache
def get_auth_settings() -> AuthSettings:
    return AuthSettings()
