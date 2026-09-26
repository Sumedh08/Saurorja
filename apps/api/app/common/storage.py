from typing import Protocol


class ObjectStorage(Protocol):
    def ensure_bucket(self) -> None: ...

    def is_available(self) -> bool: ...
