from app.infrastructure.object_storage import MinioObjectStorage


class ClientStub:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.exists_calls = 0
        self.created = False

    def bucket_exists(self, bucket: str) -> bool:
        self.exists_calls += 1
        return self.available

    def make_bucket(self, bucket: str) -> None:
        self.created = True
        self.available = True


def test_storage_initialization_creates_missing_bucket() -> None:
    storage = MinioObjectStorage("http://localhost:9000", "access", "secret", "bucket")
    client = ClientStub(False)
    storage._client = client  # type: ignore[assignment]
    storage.ensure_bucket()
    assert client.created


def test_storage_health_is_read_only() -> None:
    storage = MinioObjectStorage("http://localhost:9000", "access", "secret", "bucket")
    client = ClientStub()
    storage._client = client  # type: ignore[assignment]
    assert storage.is_available()
    assert not client.created


def test_storage_health_returns_false_for_unavailable_bucket() -> None:
    storage = MinioObjectStorage("http://localhost:9000", "access", "secret", "bucket")
    storage._client = ClientStub(False)  # type: ignore[assignment]
    assert not storage.is_available()
