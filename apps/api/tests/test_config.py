import pytest
from pydantic import ValidationError

from app.core.config import DEV_DB_PASSWORD, DEV_MINIO_SECRET, Settings


def test_settings_defaults_are_development_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = Settings(_env_file=None)
    assert settings.app_version == "0.1.0"
    assert settings.api_prefix == "/api/v1"
    assert settings.app_env == "development"


def test_settings_load_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_VERSION", "0.2.0")
    assert Settings(_env_file=None).app_version == "0.2.0"


def test_production_rejects_example_credentials() -> None:
    with pytest.raises(ValidationError, match="development credential"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url=f"postgresql+psycopg://user:{DEV_DB_PASSWORD}@localhost:5432/saurorja",
            minio_secret_key=DEV_MINIO_SECRET,
        )


def test_production_rejects_wildcard_cors_and_debug() -> None:
    with pytest.raises(ValidationError, match="DEBUG must be false"):
        Settings(_env_file=None, app_env="production", debug=True, cors_origins=["*"])


def test_development_allows_loopback_http_oidc_urls() -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        oidc_issuer_url="http://localhost:8080",
        oidc_client_id="saurorja",
        oidc_client_secret="test-secret",
        oidc_redirect_uri="http://localhost/api/v1/auth/callback",
        oidc_post_logout_redirect_uri="http://localhost/api/v1/auth/logout/callback",
        public_app_origin="http://localhost",
    )
    assert settings.oidc_configured


def test_development_rejects_remote_http_oidc_issuer() -> None:
    with pytest.raises(ValidationError, match="loopback development"):
        Settings(
            _env_file=None,
            app_env="development",
            oidc_issuer_url="http://idp.example.com",
            oidc_client_id="saurorja",
            oidc_client_secret="test-secret",
            oidc_redirect_uri="http://localhost/api/v1/auth/callback",
            oidc_post_logout_redirect_uri="http://localhost/api/v1/auth/logout/callback",
            public_app_origin="http://localhost",
        )


def test_production_requires_complete_https_oidc_configuration() -> None:
    with pytest.raises(ValidationError, match="complete generic OIDC"):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+psycopg://user:strong-password@db:5432/saurorja",
            minio_access_key="operator-defined-user",
            minio_secret_key="operator-defined-secret",
            cors_origins=["https://saurorja.example.com"],
            public_app_origin="https://saurorja.example.com",
        )
