from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.common.storage import ObjectStorage
from app.core.config import Settings


@dataclass(frozen=True)
class DependencyHealth:
    status: Literal["connected", "disconnected"]


@dataclass(frozen=True)
class ServiceHealth:
    service: str
    status: Literal["healthy", "degraded"]
    version: str
    environment: str
    database: DependencyHealth
    object_storage: DependencyHealth


def read_service_health(
    settings: Settings,
    storage: ObjectStorage,
    database_check: Callable[[], bool],
) -> ServiceHealth:
    database_connected = database_check()
    storage_connected = storage.is_available()
    return ServiceHealth(
        service="saurorja-api",
        status="healthy" if database_connected and storage_connected else "degraded",
        version=settings.app_version,
        environment=settings.app_env,
        database=DependencyHealth("connected" if database_connected else "disconnected"),
        object_storage=DependencyHealth("connected" if storage_connected else "disconnected"),
    )
