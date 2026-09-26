import logging
import os
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID

import pytest
from conftest import FakeStorage
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.session import SessionFactory
from app.main import create_app
from app.modules.identity.models import (
    ApplicationSession,
    ExternalIdentity,
    OIDCTransaction,
    PendingIdentitySession,
    User,
)
from app.modules.identity.oidc import OIDCAdapterError, OIDCIdentity
from app.modules.identity.security import digest_secret, new_secret

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="OIDC flow integration tests require PostgreSQL"
)

ISSUER = "https://issuer.test"
ORIGIN = "https://testserver"
ACCESS_TOKEN_CANARY = "access-token-canary-must-not-leak"
ID_TOKEN_CANARY = "id-token-canary-must-not-leak"
REFRESH_TOKEN_CANARY = "refresh-token-canary-must-not-leak"


class FakeOIDCClient:
    def __init__(self, *, subject: str, verified_email: str | None = None) -> None:
        self.subject = subject
        self.verified_email = verified_email
        self.invalid_nonce: str | None = None
        self.authorization_arguments: dict[str, Any] = {}

    async def load_server_metadata(self) -> dict[str, str]:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
        }

    async def create_authorization_url(self, redirect_uri: str, **kwargs: Any) -> dict[str, str]:
        del redirect_uri
        self.authorization_arguments = kwargs
        query = urlencode({key: str(value) for key, value in kwargs.items()})
        return {"url": f"{ISSUER}/authorize?{query}"}

    async def fetch_access_token(self, **kwargs: Any) -> dict[str, str]:
        self.token_exchange_arguments = kwargs
        return {
            "id_token": ID_TOKEN_CANARY,
            "access_token": ACCESS_TOKEN_CANARY,
            "refresh_token": REFRESH_TOKEN_CANARY,
        }

    async def parse_id_token(
        self, token: dict[str, str], *, nonce: str | None, claims_options: dict[str, Any]
    ) -> dict[str, Any]:
        assert token["id_token"] == ID_TOKEN_CANARY
        assert nonce is None
        assert claims_options["iss"]["values"] == [ISSUER]
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "sub": self.subject,
            "nonce": self.invalid_nonce or self.authorization_arguments["nonce"],
            "name": "OIDC Test User",
        }
        if self.verified_email:
            claims["email"] = self.verified_email
            claims["email_verified"] = True
        return claims


class FakeOIDC:
    def __init__(self, client: FakeOIDCClient) -> None:
        self.client = client

    async def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
    ) -> str:
        metadata = await self.client.load_server_metadata()
        if metadata.get("issuer") != ISSUER:
            raise OIDCAdapterError("provider issuer mismatch")
        result = await self.client.create_authorization_url(
            f"{ISSUER}/callback",
            response_type="code",
            scope="openid email profile",
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
        )
        return result["url"]

    async def authenticate_callback(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce_hash: bytes,
    ) -> OIDCIdentity:
        assert code == "code-canary"
        assert code_verifier
        nonce = self.client.invalid_nonce or self.client.authorization_arguments["nonce"]
        if not expected_nonce_hash or digest_secret(nonce) != expected_nonce_hash:
            raise OIDCAdapterError("nonce mismatch")
        return OIDCIdentity(
            subject=self.client.subject,
            email=self.client.verified_email,
            email_verified=self.client.verified_email is not None,
            display_name="OIDC Test User",
        )

    async def logout_url(
        self,
        *,
        state: str,
    ) -> str | None:
        del state
        return None


def build_client(oidc_client: FakeOIDCClient) -> TestClient:
    settings = Settings(
        _env_file=None,
        app_env="test",
        cors_origins=[ORIGIN],
        public_app_origin=ORIGIN,
        oidc_issuer_url=ISSUER,
        oidc_client_id="saurorja-test-client",
        oidc_client_secret="test-only-secret",
        oidc_redirect_uri=f"{ORIGIN}/api/v1/auth/callback",
        oidc_post_logout_redirect_uri=f"{ORIGIN}/api/v1/auth/logout/callback",
    )
    application = create_app(settings, FakeStorage())
    application.state.oidc = FakeOIDC(oidc_client)
    return TestClient(application, base_url=ORIGIN)


def begin_login(client: TestClient, *, existing_cookie: str | None = None) -> tuple[str, str]:
    application = cast(Any, client.app)
    headers = {"Cookie": f"__Host-saurorja_session={existing_cookie}"} if existing_cookie else {}
    response = client.get("/api/v1/auth/login", headers=headers, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"{ISSUER}/authorize?")
    binding = SimpleCookie()
    binding_headers = response.headers.get_list("set-cookie")
    for header in binding_headers:
        binding.load(header)
    binding_name = (
        "saurorja_oidc_binding"
        if application.state.settings.app_env == "development"
        else "__Host-saurorja_oidc_binding"
    )
    binding_header = next(header for header in binding_headers if header.startswith(binding_name))
    assert "Path=/" in binding_header
    assert "Domain=" not in binding_header
    if application.state.settings.app_env != "development":
        assert "Secure" in binding_header and "HttpOnly" in binding_header
    oidc = application.state.oidc
    return oidc.client.authorization_arguments["state"], binding[binding_name].value


def response_cookie(response: Any, name: str) -> tuple[str, str]:
    cookie_header = next(
        value
        for value in reversed(response.headers.get_list("set-cookie"))
        if value.startswith(f"{name}=") and value.split("=", 1)[1].split(";", 1)[0]
    )
    return cookie_header, cookie_header.split("=", 1)[1].split(";", 1)[0]


def seed_linked_user() -> tuple[UUID, str]:
    session_secret = new_secret()
    with SessionFactory.begin() as db:
        user = User(
            status="ACTIVE",
            email="before-login@example.com",
            normalized_email="before-login@example.com",
            email_verified_at=datetime.now(UTC),
            display_name="Before Login",
        )
        db.add(user)
        db.flush()
        identity = ExternalIdentity(user_id=user.id, issuer=ISSUER, subject="linked-subject")
        db.add(identity)
        db.flush()
        db.add(
            ApplicationSession(
                session_token_hash=digest_secret(session_secret),
                csrf_secret=os.urandom(32),
                user_id=user.id,
                external_identity_id=identity.id,
                auth_email="before-login@example.com",
                auth_email_verified=True,
                absolute_expires_at=datetime.now(UTC) + timedelta(hours=12),
            )
        )
        return user.id, session_secret


def test_linked_oidc_login_rotates_session_and_keeps_tokens_server_side(
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_id, old_secret = seed_linked_user()
    fake_client = FakeOIDCClient(subject="linked-subject", verified_email="after-login@example.com")
    with build_client(fake_client) as client:
        state, binding = begin_login(client, existing_cookie=old_secret)
        with SessionFactory() as db:
            transaction = db.scalar(
                select(OIDCTransaction).where(OIDCTransaction.state_hash == digest_secret(state))
            )
            assert transaction is not None
            assert transaction.nonce_hash == digest_secret(
                fake_client.authorization_arguments["nonce"]
            )
            assert transaction.pkce_verifier == fake_client.authorization_arguments["code_verifier"]
            assert transaction.browser_binding_hash == digest_secret(binding)

        with caplog.at_level(logging.INFO):
            response = client.get(
                f"/api/v1/auth/callback?{urlencode({'code': 'code-canary', 'state': state})}",
                headers={
                    "Cookie": f"__Host-saurorja_session={old_secret}; "
                    f"__Host-saurorja_oidc_binding={binding}"
                },
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        binding_expiry = next(
            header
            for header in response.headers.get_list("set-cookie")
            if header.startswith("__Host-saurorja_oidc_binding=")
        )
        assert "Path=/" in binding_expiry and "Max-Age=0" in binding_expiry
        assert "Domain=" not in binding_expiry
        assert "Secure" in binding_expiry and "HttpOnly" in binding_expiry
        assert all(
            canary not in response.text and canary not in str(response.headers)
            for canary in (ACCESS_TOKEN_CANARY, ID_TOKEN_CANARY, REFRESH_TOKEN_CANARY)
        )
        app_logs = "\n".join(
            record.getMessage() for record in caplog.records if record.name.startswith("saurorja")
        )
        for sensitive_value in (
            "code-canary",
            state,
            old_secret,
            binding,
            "linked-subject",
            "after-login@example.com",
            ACCESS_TOKEN_CANARY,
            ID_TOKEN_CANARY,
            REFRESH_TOKEN_CANARY,
        ):
            assert sensitive_value not in app_logs
        session_header, new_session_secret_value = response_cookie(
            response, "__Host-saurorja_session"
        )
        assert "Secure" in session_header and "HttpOnly" in session_header
        assert new_session_secret_value != old_secret

        with SessionFactory() as db:
            prior_session = db.scalar(
                select(ApplicationSession).where(
                    ApplicationSession.session_token_hash == digest_secret(old_secret)
                )
            )
            current_session = db.scalar(
                select(ApplicationSession).where(
                    ApplicationSession.session_token_hash == digest_secret(new_session_secret_value)
                )
            )
            user = db.get(User, user_id)
            assert prior_session is not None and prior_session.revocation_reason == "rotated"
            assert current_session is not None and current_session.user_id == user_id
            assert user is not None and user.email == "after-login@example.com"
        profile = client.get(
            "/api/v1/me", headers={"Cookie": f"__Host-saurorja_session={new_session_secret_value}"}
        )
        assert profile.status_code == 200
        assert profile.json()["email"] == "after-login@example.com"


def test_unlinked_oidc_identity_gets_only_pending_admission_session() -> None:
    fake_client = FakeOIDCClient(subject="unlinked-subject", verified_email="pending@example.com")
    with build_client(fake_client) as client:
        state, binding = begin_login(client)
        response = client.get(
            f"/api/v1/auth/callback?{urlencode({'code': 'code-canary', 'state': state})}",
            headers={"Cookie": f"__Host-saurorja_oidc_binding={binding}"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        pending_header, raw_pending_secret = response_cookie(response, "__Host-saurorja_pending")
        assert "Secure" in pending_header and "HttpOnly" in pending_header

        with SessionFactory() as db:
            assert db.scalar(select(User.id)) is None
            assert db.scalar(select(ExternalIdentity.id)) is None
            pending = db.scalar(
                select(PendingIdentitySession).where(
                    PendingIdentitySession.token_hash == digest_secret(raw_pending_secret)
                )
            )
            assert pending is not None and pending.email_verified
            assert pending.normalized_email == "pending@example.com"
        headers = {"Cookie": f"__Host-saurorja_pending={raw_pending_secret}"}
        assert (
            client.get("/api/v1/auth/session", headers=headers).json()["state"]
            == "pending_identity"
        )
        assert client.get("/api/v1/auth/pending", headers=headers).status_code == 200
        assert client.get("/api/v1/me", headers=headers).status_code == 401
        assert client.get("/api/v1/organizations", headers=headers).status_code == 401


def test_invalid_state_and_nonce_fail_closed_without_replacing_existing_session() -> None:
    user_id, old_secret = seed_linked_user()
    fake_client = FakeOIDCClient(subject="linked-subject", verified_email="after-login@example.com")
    fake_client.invalid_nonce = "wrong-nonce"
    with build_client(fake_client) as client:
        state, binding = begin_login(client, existing_cookie=old_secret)
        bad_state = client.get(
            f"/api/v1/auth/callback?{urlencode({'code': 'code-canary', 'state': 'wrong-state'})}",
            headers={
                "Cookie": (
                    f"__Host-saurorja_session={old_secret}; __Host-saurorja_oidc_binding={binding}"
                )
            },
            follow_redirects=False,
        )
        assert bad_state.status_code == 303
        assert bad_state.headers["location"] == "/?auth=failed"
        invalid_nonce = client.get(
            f"/api/v1/auth/callback?{urlencode({'code': 'code-canary', 'state': state})}",
            headers={
                "Cookie": (
                    f"__Host-saurorja_session={old_secret}; __Host-saurorja_oidc_binding={binding}"
                )
            },
            follow_redirects=False,
        )
        assert invalid_nonce.status_code == 303
        assert invalid_nonce.headers["location"] == "/?auth=failed"
        assert (
            client.get(
                "/api/v1/me", headers={"Cookie": f"__Host-saurorja_session={old_secret}"}
            ).status_code
            == 200
        )
        with SessionFactory() as db:
            assert db.scalar(
                select(ApplicationSession.id).where(ApplicationSession.user_id == user_id)
            )
            assert db.scalar(
                select(ApplicationSession).where(ApplicationSession.revoked_at.is_(None))
            )
