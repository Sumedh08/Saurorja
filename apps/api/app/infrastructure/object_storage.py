from minio import Minio


class MinioObjectStorage:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._bucket = bucket
        self._client = Minio(
            endpoint.removeprefix("http://").removeprefix("https://"),
            access_key=access_key,
            secret_key=secret_key,
            secure=endpoint.startswith("https://"),
        )

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def is_available(self) -> bool:
        try:
            return self._client.bucket_exists(self._bucket)
        except Exception:
            return False
