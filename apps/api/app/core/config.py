from functools import lru_cache
from typing import Self
from urllib.parse import unquote, urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_DB_PASSWORD = "saurorja-dev-password"
DEV_MINIO_SECRET = "saurorja-dev-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Saurorja API"
    app_env: str = "development"
    app_version: str = "0.1.0"
    debug: bool = False
    api_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+psycopg://saurorja:saurorja-dev-password@localhost:5432/saurorja"
    )
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "saurorja"
    minio_secret_key: str = DEV_MINIO_SECRET
    minio_bucket: str = "saurorja"
    otel_service_name: str = "saurorja-api"
    otel_exporter_otlp_endpoint: str | None = None
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost"])
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_post_logout_redirect_uri: str | None = None
    oidc_scopes: str = "openid email profile"
    public_app_origin: str = "http://localhost"

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value == "/":
            raise ValueError("API_PREFIX must be an absolute path prefix")
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a standard Python log level")
        return normalized

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: list[str]) -> list[str]:
        return [origin.rstrip("/") for origin in origins]

    @field_validator("public_app_origin")
    @classmethod
    def validate_public_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("PUBLIC_APP_ORIGIN must be an HTTP origin without a path")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_production(self) -> Self:
        oidc_values = (
            self.oidc_issuer_url,
            self.oidc_client_id,
            self.oidc_client_secret,
            self.oidc_redirect_uri,
            self.oidc_post_logout_redirect_uri,
        )
        if any(oidc_values) and not all(oidc_values):
            raise ValueError(
                "OIDC issuer, client credentials, and redirect URIs must be configured together"
            )
        if self.oidc_scopes.split() and "openid" not in self.oidc_scopes.split():
            raise ValueError("OIDC_SCOPES must include openid")
        if self.oidc_configured:
            issuer = urlsplit(self.oidc_issuer_url or "")
            callback = urlsplit(self.oidc_redirect_uri or "")
            logout_callback = urlsplit(self.oidc_post_logout_redirect_uri or "")
            public_origin = urlsplit(self.public_app_origin)
            urls = (issuer, callback, logout_callback, public_origin)
            if any(
                not item.scheme
                or not item.netloc
                or item.username
                or item.password
                or item.fragment
                for item in urls
            ):
                raise ValueError(
                    "OIDC and public application URLs must be absolute and cannot "
                    "contain credentials or fragments"
                )
            if callback.path != f"{self.api_prefix}/auth/callback":
                raise ValueError("OIDC_REDIRECT_URI must target the configured auth callback path")
            if logout_callback.path != f"{self.api_prefix}/auth/logout/callback":
                raise ValueError(
                    "OIDC_POST_LOGOUT_REDIRECT_URI must target the configured logout callback path"
                )
            expected_origin = (public_origin.scheme, public_origin.netloc)
            if (callback.scheme, callback.netloc) != expected_origin or (
                logout_callback.scheme,
                logout_callback.netloc,
            ) != expected_origin:
                raise ValueError("OIDC callback URIs must use PUBLIC_APP_ORIGIN")
            if self.app_env.lower() == "development" and any(
                item.scheme == "http" and item.hostname not in {"localhost", "127.0.0.1", "::1"}
                for item in urls
            ):
                raise ValueError("HTTP OIDC URLs are permitted only for loopback development")
        if (
            self.app_env.lower() == "development"
            and urlsplit(self.public_app_origin).scheme == "http"
            and urlsplit(self.public_app_origin).hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("HTTP PUBLIC_APP_ORIGIN is permitted only for loopback development")
        if self.app_env.lower() == "production":
            if self.debug:
                raise ValueError("DEBUG must be false in production")
            database_password = unquote(urlsplit(self.database_url).password or "")
            if database_password == DEV_DB_PASSWORD:
                raise ValueError(
                    "DATABASE_URL must not use the development credential in production"
                )
            if self.minio_access_key == "saurorja" or self.minio_secret_key == DEV_MINIO_SECRET:
                raise ValueError(
                    "MinIO credentials must not use the development values in production"
                )
            if "*" in self.cors_origins:
                raise ValueError("CORS_ORIGINS cannot contain '*' in production")
            if not self.cors_origins:
                raise ValueError("CORS_ORIGINS must contain an explicit production allowlist")
            if not self.public_app_origin.startswith("https://"):
                raise ValueError("PUBLIC_APP_ORIGIN must use HTTPS in production")
            if self.oidc_configured and (
                not (self.oidc_issuer_url or "").startswith("https://")
                or not (self.oidc_redirect_uri or "").startswith("https://")
                or not (self.oidc_post_logout_redirect_uri or "").startswith("https://")
                or not self.public_app_origin.startswith("https://")
            ):
                raise ValueError("Production OIDC and public application URLs must use HTTPS")
            if not self.oidc_configured:
                raise ValueError("Production requires complete generic OIDC configuration")
        return self

    @property
    def oidc_configured(self) -> bool:
        return bool(
            self.oidc_issuer_url
            and self.oidc_client_id
            and self.oidc_client_secret
            and self.oidc_redirect_uri
            and self.oidc_post_logout_redirect_uri
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
