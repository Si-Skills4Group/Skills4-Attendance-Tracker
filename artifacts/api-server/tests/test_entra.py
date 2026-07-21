"""Direct tests of pyapp.entra.validate_entra_access_token against
hand-crafted, real RS256-signed JWTs -- the rest of the suite exercises
authorization logic through mocked sessions (see test_permissions.py),
which never actually calls into this module's token-decoding/claim-
validation path. These tests build a throwaway RSA keypair, monkeypatch
_jwk_client to hand back its public key (standing in for a real Entra
JWKS endpoint), and assert the exact rejection reason for each class of
bad token the production code is supposed to catch."""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from pyapp import entra as entra_module
from pyapp.config import get_auth_settings
from pyapp.entra import TokenValidationError, validate_entra_access_token

TENANT_ID = "test-tenant-id"
AUDIENCE = "test-api-client-id"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
SCOPE = "access_as_user"


@pytest.fixture(scope="module")
def rsa_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _patch_jwk_client(monkeypatch, rsa_private_key):
    """Stands in for a real Entra JWKS fetch -- get_signing_key_from_jwt
    normally resolves a `kid` against the tenant's published keys; here it
    just always returns our test key's public half, so jwt.decode's
    signature check exercises the same code path against a key we control."""
    public_key = rsa_private_key.public_key()

    class _FakeSigningKey:
        key = public_key

    class _FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey()

    monkeypatch.setattr(entra_module, "_jwk_client", lambda settings: _FakeJwkClient())
    get_auth_settings.cache_clear()
    yield
    get_auth_settings.cache_clear()


def _make_token(rsa_private_key, **claim_overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "tid": TENANT_ID,
        "oid": "11111111-1111-1111-1111-111111111111",
        "sub": "22222222-2222-2222-2222-222222222222",
        "scp": SCOPE,
        "ver": "2.0",
        "exp": now + 3600,
        "iat": now,
        "nbf": now,
        "name": "Test User",
        "preferred_username": "test.user@example.com",
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, rsa_private_key, algorithm="RS256")


class TestValidEntraToken:
    def test_a_well_formed_token_is_accepted(self, rsa_private_key):
        token = _make_token(rsa_private_key)
        identity = validate_entra_access_token(token)
        assert identity.object_id == "11111111-1111-1111-1111-111111111111"
        assert identity.tenant_id == TENANT_ID
        assert identity.email == "test.user@example.com"

    def test_audience_may_also_be_the_bare_api_client_id(self, rsa_private_key):
        # entra_expected_audience and entra_api_client_id are both
        # "test-api-client-id" in this test environment (see conftest.py),
        # but production config can set them to different values (e.g.
        # ENTRA_EXPECTED_AUDIENCE="api://<client-id>" vs the bare client
        # id) -- either must be accepted.
        token = _make_token(rsa_private_key, aud=get_auth_settings().entra_api_client_id)
        identity = validate_entra_access_token(token)
        assert identity.object_id


class TestRejectedEntraTokens:
    def test_wrong_audience_is_rejected(self, rsa_private_key):
        token = _make_token(rsa_private_key, aud="some-other-app-client-id")
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(token)

    def test_graph_token_audience_is_rejected(self, rsa_private_key):
        """A Microsoft Graph access token (aud=https://graph.microsoft.com)
        must never be accepted as this API's own access token, even
        though it's a validly-signed Entra token for the right tenant."""
        token = _make_token(rsa_private_key, aud="https://graph.microsoft.com")
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(token)

    def test_wrong_tenant_is_rejected(self, rsa_private_key):
        token = _make_token(rsa_private_key, tid="99999999-9999-9999-9999-999999999999")
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(token)

    def test_missing_required_scope_is_rejected(self, rsa_private_key):
        token = _make_token(rsa_private_key, scp="some_other_scope")
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(token)

    def test_id_token_shaped_claims_are_rejected(self, rsa_private_key):
        """An ID token has no `scp` claim at all (delegated scopes are an
        access-token-only concept) and its `aud` is the client app's own
        id, not this API's -- simulate both simultaneously, since that's
        what a real ID token looks like."""
        token = _make_token(rsa_private_key, aud="some-spa-client-id", scp=None)
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(token)

    def test_expired_token_is_rejected(self, rsa_private_key):
        now = int(time.time())
        token = _make_token(rsa_private_key, exp=now - 3600, iat=now - 7200)
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(token)

    def test_tampered_signature_is_rejected(self, rsa_private_key):
        token = _make_token(rsa_private_key)
        # Flip a character deep in the signature segment.
        header, payload, signature = token.split(".")
        tampered_signature = ("A" if signature[-1] != "A" else "B") + signature[1:]
        tampered = f"{header}.{payload}.{tampered_signature}"
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(tampered)

    def test_missing_oid_is_rejected(self, rsa_private_key):
        token = _make_token(rsa_private_key, oid=None)
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(token)

    def test_unsupported_token_version_is_rejected(self, rsa_private_key):
        token = _make_token(rsa_private_key, ver="1.0")
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(token)

    def test_wrong_issuer_is_rejected(self, rsa_private_key):
        token = _make_token(rsa_private_key, iss="https://login.microsoftonline.com/some-other-tenant/v2.0")
        with pytest.raises(TokenValidationError):
            validate_entra_access_token(token)
