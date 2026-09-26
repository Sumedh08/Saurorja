import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest
from joserfc import jwk, jwt
from joserfc.errors import JoseError

from app.core.config import Settings
from app.modules.identity.oidc import OIDCAdapter, configured_oidc
from app.modules.identity.security import digest_secret

CLIENT_ID = "saurorja-controlled-test"
CLIENT_SECRET = "controlled-test-secret"


class ControlledIssuer(ThreadingHTTPServer):
    allow_reuse_address = True
    issuer: str
    signing_key: Any
    token_signing_key: Any
    previous_signing_key: Any | None = None
    jwks_request_count = 0
    pkce_challenge: str | None = None
    nonce: str | None = None
    token_request_authenticated = False


class IssuerHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = cast(ControlledIssuer, self.server)
        if self.path == "/.well-known/openid-configuration":
            self._json(
                {
                    "issuer": server.issuer,
                    "authorization_endpoint": f"{server.issuer}/oauth/authorize",
                    "token_endpoint": f"{server.issuer}/oauth/token",
                    "jwks_uri": f"{server.issuer}/oauth/keys",
                    "end_session_endpoint": f"{server.issuer}/oidc/logout",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                    "code_challenge_methods_supported": ["S256"],
                }
            )
        elif self.path == "/oauth/keys":
            server.jwks_request_count += 1
            signing_key = (
                server.previous_signing_key
                if server.jwks_request_count == 1 and server.previous_signing_key is not None
                else server.signing_key
            )
            self._json({"keys": [signing_key.as_dict(private=False)]})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        server = cast(ControlledIssuer, self.server)
        if self.path != "/oauth/token":
            self.send_error(404)
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(content_length).decode("utf-8"))
        server.token_request_authenticated = bool(
            self.headers.get("Authorization", "").startswith("Basic ")
        )
        verifier = form.get("code_verifier", [""])[0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        valid_request = (
            server.token_request_authenticated
            and form.get("grant_type") == ["authorization_code"]
            and form.get("code") == ["controlled-code"]
            and bool(server.pkce_challenge)
            and challenge.rstrip(b"=").decode() == server.pkce_challenge
            and bool(server.nonce)
        )
        if not valid_request:
            self.send_error(400)
            return

        now = datetime.now(UTC)
        claims = {
            "iss": server.issuer,
            "sub": "controlled-user-subject",
            "aud": CLIENT_ID,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "nonce": server.nonce,
            "email": "controlled@example.test",
            "email_verified": True,
            "name": "Controlled Test User",
        }
        token = jwt.encode(
            {"alg": "RS256", "kid": "controlled-key"},
            claims,
            server.token_signing_key,
            algorithms=["RS256"],
        )
        self._json(
            {
                "access_token": "controlled-access-token",
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": token,
            }
        )

    def _json(self, value: dict[str, object]) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.mark.parametrize(
    ("rotate_signing_key", "invalid_signature"),
    [(False, False), (True, False), (False, True)],
)
def test_adapter_completes_code_pkce_discovery_jwks_validation_and_logout(
    rotate_signing_key: bool,
    invalid_signature: bool,
) -> None:
    signing_key = jwk.generate_key("RSA", 2048, parameters={"kid": "controlled-key"})
    previous_signing_key = (
        jwk.generate_key("RSA", 2048, parameters={"kid": "previous-key"})
        if rotate_signing_key
        else None
    )
    token_signing_key = (
        jwk.generate_key("RSA", 2048, parameters={"kid": "controlled-key"})
        if invalid_signature
        else signing_key
    )
    server = ControlledIssuer(("127.0.0.1", 0), IssuerHandler)
    host, port = cast(tuple[str, int], server.server_address)
    server.issuer = f"http://{host}:{port}"
    server.signing_key = signing_key
    server.token_signing_key = token_signing_key
    server.previous_signing_key = previous_signing_key
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = Settings(
        _env_file=None,
        app_env="test",
        oidc_issuer_url=server.issuer,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
        oidc_redirect_uri="http://localhost/api/v1/auth/callback",
        oidc_post_logout_redirect_uri="http://localhost/api/v1/auth/logout/callback",
        public_app_origin="http://localhost",
    )
    adapter = configured_oidc(settings)
    assert isinstance(adapter, OIDCAdapter)
    nonce = "controlled-server-nonce"
    verifier = "v" * 64
    try:
        authorization_url = asyncio.run(
            adapter.authorization_url(state="controlled-state", nonce=nonce, code_verifier=verifier)
        )
        query = parse_qs(urlsplit(authorization_url).query)
        expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"] == [expected_challenge.rstrip(b"=").decode()]
        assert query["state"] == ["controlled-state"]
        assert query["nonce"] == [nonce]
        server.pkce_challenge = query["code_challenge"][0]
        server.nonce = nonce

        if invalid_signature:
            with pytest.raises(JoseError):
                asyncio.run(
                    adapter.authenticate_callback(
                        code="controlled-code",
                        code_verifier=verifier,
                        expected_nonce_hash=digest_secret(nonce),
                    )
                )
        else:
            identity = asyncio.run(
                adapter.authenticate_callback(
                    code="controlled-code",
                    code_verifier=verifier,
                    expected_nonce_hash=digest_secret(nonce),
                )
            )
            assert identity.subject == "controlled-user-subject"
            assert identity.email == "controlled@example.test"
            assert identity.email_verified is True
            assert identity.display_name == "Controlled Test User"
        assert server.token_request_authenticated
        assert server.jwks_request_count == (2 if rotate_signing_key else 1)

        logout_url = asyncio.run(adapter.logout_url(state="logout-state"))
        assert logout_url is not None
        logout_query = parse_qs(urlsplit(logout_url).query)
        assert logout_query["client_id"] == [CLIENT_ID]
        assert logout_query["state"] == ["logout-state"]
        assert logout_query["post_logout_redirect_uri"] == [settings.oidc_post_logout_redirect_uri]
        assert "id_token_hint" not in logout_query
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
