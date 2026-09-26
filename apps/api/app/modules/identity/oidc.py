import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from authlib.integrations.starlette_client import OAuth

from app.core.config import Settings
from app.modules.identity.security import digest_secret


class OIDCAdapterError(Exception):
    """Raised when the configured provider cannot complete a protocol operation."""


@dataclass(frozen=True)
class OIDCIdentity:
    subject: str
    email: str | None
    email_verified: bool
    display_name: str | None


def _validate_authorized_party(claims: Mapping[str, Any], client_id: str) -> None:
    audience = claims.get("aud")
    if isinstance(audience, str):
        audiences = [audience]
    elif isinstance(audience, list) and all(isinstance(value, str) for value in audience):
        audiences = audience
    else:
        raise OIDCAdapterError("ID token audience is invalid")
    if client_id not in audiences:
        raise OIDCAdapterError("ID token audience mismatch")

    authorized_party = claims.get("azp")
    if authorized_party is not None and authorized_party != client_id:
        raise OIDCAdapterError("ID token authorized party mismatch")
    if len(audiences) > 1 and authorized_party != client_id:
        raise OIDCAdapterError("ID token authorized party is required for multiple audiences")


class OIDCAdapter:
    """Keep Authlib and provider-protocol handling behind one application boundary."""

    def __init__(self, oauth: OAuth, settings: Settings) -> None:
        issuer = settings.oidc_issuer_url
        client_id = settings.oidc_client_id
        redirect_uri = settings.oidc_redirect_uri
        post_logout_redirect_uri = settings.oidc_post_logout_redirect_uri
        if not issuer or not client_id or not redirect_uri or not post_logout_redirect_uri:
            raise ValueError("OIDC adapter requires a complete client configuration")
        self._oauth = oauth
        self._issuer = issuer
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._post_logout_redirect_uri = post_logout_redirect_uri
        self._scope = settings.oidc_scopes

    async def check_provider(self) -> None:
        """Check discovery metadata needed by the configured code + PKCE client."""
        client = self._oauth.create_client("saurorja")
        metadata = await client.load_server_metadata()
        if metadata.get("issuer") != self._issuer:
            raise OIDCAdapterError("provider issuer mismatch")
        for endpoint in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = metadata.get(endpoint)
            if not isinstance(value, str) or not value:
                raise OIDCAdapterError("required provider endpoint is missing")
        response_types = metadata.get("response_types_supported")
        if not isinstance(response_types, list) or "code" not in response_types:
            raise OIDCAdapterError("provider does not support authorization code flow")
        challenge_methods = metadata.get("code_challenge_methods_supported")
        if not isinstance(challenge_methods, list) or "S256" not in challenge_methods:
            raise OIDCAdapterError("provider does not support PKCE S256")
        auth_methods = metadata.get("token_endpoint_auth_methods_supported")
        if isinstance(auth_methods, list) and "client_secret_basic" not in auth_methods:
            raise OIDCAdapterError("provider does not support the configured client authentication")

    async def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
    ) -> str:
        client = self._oauth.create_client("saurorja")
        metadata = await client.load_server_metadata()
        if metadata.get("issuer") != self._issuer:
            raise OIDCAdapterError("provider issuer mismatch")
        authorization = await client.create_authorization_url(
            self._redirect_uri,
            response_type="code",
            scope=self._scope,
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
        )
        return str(authorization["url"])

    async def authenticate_callback(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce_hash: bytes,
    ) -> OIDCIdentity:
        client = self._oauth.create_client("saurorja")
        metadata = await client.load_server_metadata()
        if metadata.get("issuer") != self._issuer:
            raise OIDCAdapterError("provider issuer mismatch")
        token = await client.fetch_access_token(
            redirect_uri=self._redirect_uri,
            code=code,
            code_verifier=code_verifier,
            grant_type="authorization_code",
        )
        if not isinstance(token, dict) or not isinstance(token.get("id_token"), str):
            raise OIDCAdapterError("ID token missing")
        claims = await client.parse_id_token(
            token,
            nonce=None,
            claims_options={"iss": {"values": [self._issuer]}},
        )
        _validate_authorized_party(claims, self._client_id)
        subject = claims.get("sub")
        nonce = claims.get("nonce")
        if (
            not isinstance(subject, str)
            or not subject
            or not isinstance(nonce, str)
            or not hmac.compare_digest(expected_nonce_hash, digest_secret(nonce))
        ):
            raise OIDCAdapterError("ID token claims invalid")

        details: dict[str, Any] = dict(claims)
        if (
            (not details.get("email") or details.get("email_verified") is not True)
            and metadata.get("userinfo_endpoint")
            and token.get("access_token")
        ):
            userinfo = await client.userinfo(token=token)
            if userinfo.get("sub") != subject:
                raise OIDCAdapterError("userinfo subject mismatch")
            for key in ("email", "email_verified", "name"):
                if key not in details and key in userinfo:
                    details[key] = userinfo[key]

        email = details.get("email")
        display_name = details.get("name")
        return OIDCIdentity(
            subject=subject,
            email=email if isinstance(email, str) else None,
            email_verified=details.get("email_verified") is True,
            display_name=display_name if isinstance(display_name, str) else None,
        )

    async def logout_url(
        self,
        *,
        state: str,
    ) -> str | None:
        client = self._oauth.create_client("saurorja")
        metadata = await client.load_server_metadata()
        if metadata.get("issuer") != self._issuer:
            raise OIDCAdapterError("provider issuer mismatch")
        if not metadata.get("end_session_endpoint"):
            return None
        logout = await client.create_logout_url(
            post_logout_redirect_uri=self._post_logout_redirect_uri,
            state=state,
            client_id=self._client_id,
        )
        return str(logout["url"])


def configured_oidc(settings: Settings) -> OIDCAdapter | None:
    if not settings.oidc_issuer_url:
        return None
    client = OAuth()
    issuer = settings.oidc_issuer_url.rstrip("/")
    client.register(
        name="saurorja",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=f"{issuer}/.well-known/openid-configuration",
        client_kwargs={
            "scope": settings.oidc_scopes,
            "code_challenge_method": "S256",
        },
    )
    return OIDCAdapter(client, settings)
