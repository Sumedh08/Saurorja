import asyncio
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from authlib.integrations.starlette_client import OAuth
from joserfc import jwk, jwt
from joserfc.errors import JoseError

from app.core.config import Settings
from app.modules.identity.oidc import (
    OIDCAdapter,
    OIDCAdapterError,
    _validate_authorized_party,
)

ISSUER = "https://issuer.example.com"
CLIENT_ID = "saurorja-test-client"


class MetadataClient:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.metadata = metadata

    async def load_server_metadata(self) -> dict[str, object]:
        return self.metadata


class MetadataOAuth:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.client = MetadataClient(metadata)

    def create_client(self, name: str) -> MetadataClient:
        assert name == "saurorja"
        return self.client


def adapter_for_metadata(metadata: dict[str, object]) -> OIDCAdapter:
    settings = Settings(
        _env_file=None,
        app_env="test",
        oidc_issuer_url=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret="test-client-secret",
        oidc_redirect_uri="https://app.example.com/api/v1/auth/callback",
        oidc_post_logout_redirect_uri="https://app.example.com/api/v1/auth/logout/callback",
        public_app_origin="https://app.example.com",
    )
    return OIDCAdapter(MetadataOAuth(metadata), settings)


def supported_metadata() -> dict[str, object]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
    }


def test_oidc_preflight_accepts_supported_discovery_metadata() -> None:
    asyncio.run(adapter_for_metadata(supported_metadata()).check_provider())


@pytest.mark.parametrize(
    "changes",
    [
        {"issuer": "https://other.example.com"},
        {"response_types_supported": ["token"]},
        {"response_types_supported": None},
        {"code_challenge_methods_supported": ["plain"]},
        {"code_challenge_methods_supported": None},
        {"token_endpoint_auth_methods_supported": ["none"]},
        {"jwks_uri": None},
    ],
)
def test_oidc_preflight_rejects_incompatible_discovery_metadata(
    changes: dict[str, object],
) -> None:
    metadata = supported_metadata()
    metadata.update(changes)
    with pytest.raises(OIDCAdapterError):
        asyncio.run(adapter_for_metadata(metadata).check_provider())


def configured_client(public_jwk: Mapping[str, object]) -> Any:
    oauth = OAuth()
    oauth.register(
        name="saurorja",
        client_id=CLIENT_ID,
        client_secret="test-client-secret",
        server_metadata_url=f"{ISSUER}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
    )
    client = oauth.create_client("saurorja")
    client.server_metadata.update(
        {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks": {"keys": [public_jwk]},
            "id_token_signing_alg_values_supported": ["RS256"],
            "_loaded_at": time.time(),
        }
    )
    return client


def id_token(
    private_key: Any,
    *,
    issuer: str = ISSUER,
    audience: str = CLIENT_ID,
    expiry: int = 3600,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": issuer,
        "sub": "local-subject",
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expiry)).timestamp()),
        "nonce": "nonce-from-server-transaction",
    }
    return jwt.encode(
        {"alg": "RS256", "kid": "local-test-key"}, claims, private_key, algorithms=["RS256"]
    )


def test_authlib_authorization_url_uses_server_owned_state_nonce_and_pkce() -> None:
    key = jwk.generate_key("RSA", 2048, parameters={"kid": "local-test-key"})
    client = configured_client(key.as_dict(private=False))
    url = asyncio.run(
        client.create_authorization_url(
            "https://app.example.com/api/v1/auth/callback",
            response_type="code",
            scope="openid email profile",
            state="server-owned-state",
            nonce="server-owned-nonce",
            code_verifier="a" * 64,
        )
    )["url"]
    query = parse_qs(urlsplit(url).query)
    assert query["state"] == ["server-owned-state"]
    assert query["nonce"] == ["server-owned-nonce"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"]


def test_authlib_validates_a_locally_signed_id_token_without_session_middleware() -> None:
    key = jwk.generate_key("RSA", 2048, parameters={"kid": "local-test-key"})
    client = configured_client(key.as_dict(private=False))
    token = id_token(key)
    claims = asyncio.run(
        client.parse_id_token(
            {"id_token": token, "access_token": "transient-test-token"},
            nonce=None,
            claims_options={"iss": {"values": [ISSUER]}},
        )
    )
    assert claims["sub"] == "local-subject"
    assert claims["nonce"] == "nonce-from-server-transaction"


@pytest.mark.parametrize(
    ("issuer", "audience", "expiry"),
    [
        ("https://attacker.example.com", CLIENT_ID, 3600),
        (ISSUER, "another-client", 3600),
        (ISSUER, CLIENT_ID, -3600),
    ],
)
def test_authlib_rejects_wrong_issuer_audience_and_expired_tokens(
    issuer: str, audience: str, expiry: int
) -> None:
    key = jwk.generate_key("RSA", 2048, parameters={"kid": "local-test-key"})
    client = configured_client(key.as_dict(private=False))
    token = id_token(key, issuer=issuer, audience=audience, expiry=expiry)
    with pytest.raises(JoseError):
        asyncio.run(
            client.parse_id_token(
                {"id_token": token, "access_token": "transient-test-token"},
                nonce=None,
                claims_options={"iss": {"values": [ISSUER]}},
            )
        )


@pytest.mark.parametrize(
    ("audience", "authorized_party", "valid"),
    [
        (CLIENT_ID, None, True),
        ([CLIENT_ID], None, True),
        ([CLIENT_ID, "other-client"], CLIENT_ID, True),
        ([CLIENT_ID, "other-client"], None, False),
        ([CLIENT_ID, "other-client"], "other-client", False),
        (CLIENT_ID, "other-client", False),
    ],
)
def test_authorized_party_is_checked_for_multiple_or_mismatched_audiences(
    audience: str | list[str], authorized_party: str | None, valid: bool
) -> None:
    claims: dict[str, object] = {"aud": audience}
    if authorized_party is not None:
        claims["azp"] = authorized_party
    if valid:
        _validate_authorized_party(claims, CLIENT_ID)
    else:
        with pytest.raises(OIDCAdapterError):
            _validate_authorized_party(claims, CLIENT_ID)
