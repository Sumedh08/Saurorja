import os
from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.core.config import Settings
from app.db.base import Base
from app.db.session import engine
from app.main import create_app
from app.modules.identity import models as identity_models  # noqa: F401


class FakeStorage:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.ensure_calls = 0
        self.health_calls = 0

    def ensure_bucket(self) -> None:
        self.ensure_calls += 1

    def is_available(self) -> bool:
        self.health_calls += 1
        return self.available


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        cors_origins=["https://testserver"],
        public_app_origin="https://testserver",
    )


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def client(
    settings: Settings, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr("app.api.routes.check_database", lambda: True)
    application = create_app(settings, storage)
    with TestClient(application, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture(scope="session", autouse=True)
def postgres_schema() -> Iterator[None]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        yield
        return
    if not urlsplit(url).path.rstrip("/").endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must point to a database whose name ends in _test")
    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_postgres_test_data() -> Iterator[None]:
    if not os.getenv("TEST_DATABASE_URL"):
        yield
        return
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.exec_driver_sql(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE")
    yield
