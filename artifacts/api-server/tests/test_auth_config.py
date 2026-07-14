import os

import pytest

from pyapp.config import AuthSettings


def test_production_rejects_local_auth(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "local")

    with pytest.raises(RuntimeError, match="AUTH_MODE=entra"):
        AuthSettings().validate_startup()


def test_production_rejects_wildcard_cors(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_MODE", "entra")
    monkeypatch.setenv("ENTRA_TENANT_ID", "tenant")
    monkeypatch.setenv("ENTRA_API_CLIENT_ID", "api-client")
    monkeypatch.setenv("ENTRA_EXPECTED_AUDIENCE", "api://api-client")
    monkeypatch.setenv("ENTRA_AUTHORITY", "https://login.microsoftonline.com/tenant/v2.0")
    monkeypatch.setenv("ENTRA_REQUIRED_SCOPE", "access_as_user")
    monkeypatch.setenv("ENTRA_ALLOWED_TENANT_ID", "tenant")
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")

    with pytest.raises(RuntimeError, match="must not allow"):
        AuthSettings().validate_startup()


def test_entra_mode_requires_identity_configuration(monkeypatch):
    for key in list(os.environ):
        if key.startswith("ENTRA_") or key in {"AUTH_MODE", "ENVIRONMENT", "ENV", "NODE_ENV"}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AUTH_MODE", "entra")

    with pytest.raises(RuntimeError, match="Missing required Entra configuration"):
        AuthSettings().validate_startup()
