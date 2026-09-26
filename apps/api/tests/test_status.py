from typing import cast
from uuid import uuid4

import pytest
from conftest import FakeStorage
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.identity.dependencies import require_user
from app.modules.identity.models import User


def test_status_reports_connected_dependencies(client: TestClient) -> None:
    cast(FastAPI, client.app).dependency_overrides[require_user] = lambda: User(
        id=uuid4(), status="ACTIVE"
    )
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json() == {
        "service": "saurorja-api",
        "status": "healthy",
        "version": "0.1.0",
        "environment": "test",
        "database": {"status": "connected"},
        "object_storage": {"status": "connected"},
    }


def test_status_reports_degraded_dependency(
    client: TestClient, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    cast(FastAPI, client.app).dependency_overrides[require_user] = lambda: User(
        id=uuid4(), status="ACTIVE"
    )
    monkeypatch.setattr("app.api.routes.check_database", lambda: False)
    storage.available = False
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["database"]["status"] == "disconnected"
    assert response.json()["object_storage"]["status"] == "disconnected"


def test_status_checks_storage_without_initializing_bucket(
    client: TestClient, storage: FakeStorage
) -> None:
    cast(FastAPI, client.app).dependency_overrides[require_user] = lambda: User(
        id=uuid4(), status="ACTIVE"
    )
    response = client.get("/api/v1/status")
    assert response.status_code == 200
    assert storage.ensure_calls == 1
    assert storage.health_calls == 1
