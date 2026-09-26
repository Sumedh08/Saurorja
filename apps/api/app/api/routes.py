from fastapi import APIRouter, Depends, Request

from app.api.schemas import DependencyStatus, HealthResponse, StatusResponse
from app.application.status import read_service_health
from app.common.storage import ObjectStorage
from app.core.config import Settings
from app.db.session import check_database
from app.modules.identity.dependencies import require_user
from app.modules.identity.models import User

health_router = APIRouter()
status_router = APIRouter()


@health_router.get("/health", response_model=HealthResponse, include_in_schema=False)
def health() -> HealthResponse:
    return HealthResponse(status="healthy")


@status_router.get("/status", response_model=StatusResponse)
def status(request: Request, _: User = Depends(require_user)) -> StatusResponse:
    settings: Settings = request.app.state.settings
    storage: ObjectStorage = request.app.state.object_storage
    health = read_service_health(settings, storage, check_database)
    return StatusResponse(
        service=health.service,
        status=health.status,
        version=health.version,
        environment=health.environment,
        database=DependencyStatus(status=health.database.status),
        object_storage=DependencyStatus(status=health.object_storage.status),
    )
