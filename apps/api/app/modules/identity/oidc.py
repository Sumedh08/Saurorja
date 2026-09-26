from authlib.integrations.starlette_client import OAuth

from app.core.config import Settings


def configured_oidc(settings: Settings) -> OAuth | None:
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
    return client
