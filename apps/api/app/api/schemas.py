from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["healthy"]


class DependencyStatus(BaseModel):
    status: Literal["connected", "disconnected"]


class StatusResponse(BaseModel):
    service: str
    status: Literal["healthy", "degraded"]
    version: str
    environment: str
    database: DependencyStatus
    object_storage: DependencyStatus
