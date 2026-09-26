import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_health_is_liveness_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.check_database", lambda: (_ for _ in ()).throw(AssertionError())
    )

    class UnavailableStorage:
        def ensure_bucket(self) -> None:
            raise ConnectionError

        def is_available(self) -> bool:
            raise AssertionError("liveness must not check storage")

    with TestClient(create_app(Settings(_env_file=None), UnavailableStorage())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_health_endpoint_contract(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
