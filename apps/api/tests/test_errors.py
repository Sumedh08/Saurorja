from fastapi.testclient import TestClient


def test_api_errors_have_safe_consistent_shape(client: TestClient) -> None:
    response = client.get("/does-not-exist", headers={"X-Request-ID": "test-request-42"})
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "The requested resource was not found",
            "request_id": "test-request-42",
        }
    }
    assert response.headers["X-Request-ID"] == "test-request-42"
    assert "Traceback" not in response.text


def test_invalid_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": 'bad"id'})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != 'bad"id'
