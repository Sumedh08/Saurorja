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

ISSUER = "https://issuer.example.com"
CLIENT_ID = "saurorja-test-client"


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
