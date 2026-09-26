import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from time import perf_counter
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import select
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.types import Message

from app.api.auth import admission_router
from app.api.auth import router as auth_router
from app.api.me import router as me_router
from app.api.organizations import router as organizations_router
from app.api.routes import health_router, status_router
from app.common.storage import ObjectStorage
from app.core.config import Settings, get_settings
from app.core.errors import http_error_handler, validation_error_handler
from app.core.logging import configure_logging, request_id_context
from app.db.session import SessionFactory, database_utc_now, engine
from app.infrastructure.object_storage import MinioObjectStorage
from app.modules.identity.dependencies import normal_cookie_name, pending_cookie_name
from app.modules.identity.models import ApplicationSession, PendingIdentitySession, User
from app.modules.identity.oidc import configured_oidc
from app.modules.identity.security import csrf_token_matches, digest_secret

REQUESTS = Counter(
    "saurorja_http_requests_total", "HTTP requests", ["method", "route", "status_class"]
)
REQUEST_DURATION = Histogram(
    "saurorja_http_request_duration_seconds", "HTTP request duration", ["method", "route"]
)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def configure_otel(settings: Settings) -> None:
    if not settings.otel_exporter_otlp_endpoint:
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    SQLAlchemyInstrumentor().instrument(engine=engine)


def create_app(
    settings: Settings | None = None,
    storage: ObjectStorage | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level, app_settings.otel_service_name, app_settings.app_env)
    app_storage = storage or MinioObjectStorage(
        app_settings.minio_endpoint,
        app_settings.minio_access_key,
        app_settings.minio_secret_key,
        app_settings.minio_bucket,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = app_settings
        application.state.object_storage = app_storage
        bucket_initializer: asyncio.Task[None] | None = None
        try:
            await asyncio.to_thread(app_storage.ensure_bucket)
        except Exception:
            logging.getLogger("saurorja.startup").error(
                "Object storage bucket initialization failed; API will report degraded status"
            )

            async def retry_bucket_initialization() -> None:
                retry_delay = 5
                while True:
                    await asyncio.sleep(retry_delay)
                    try:
                        await asyncio.to_thread(app_storage.ensure_bucket)
                        logging.getLogger("saurorja.startup").info(
                            "Object storage bucket initialization completed"
                        )
                        return
                    except Exception:
                        logging.getLogger("saurorja.startup").error(
                            "Object storage bucket initialization is still unavailable"
                        )
                        retry_delay = min(retry_delay * 2, 60)

            bucket_initializer = asyncio.create_task(retry_bucket_initialization())
        if app_settings.otel_exporter_otlp_endpoint:
            try:
                configure_otel(app_settings)
                FastAPIInstrumentor.instrument_app(
                    application,
                    excluded_urls=r".*/api/v1/auth/(?:callback|logout/callback).*",
                )
            except Exception:
                logging.getLogger("saurorja.startup").error(
                    "OpenTelemetry setup failed; continuing without tracing"
                )
        yield
        if bucket_initializer:
            bucket_initializer.cancel()
            await asyncio.gather(bucket_initializer, return_exceptions=True)

    application = FastAPI(
        title=app_settings.app_name, version=app_settings.app_version, lifespan=lifespan
    )
    application.state.settings = app_settings
    application.state.object_storage = app_storage
    application.state.oidc = configured_oidc(app_settings)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-CSRF-Token"],
    )
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(HTTPException, http_error_handler)
    application.include_router(health_router)
    application.include_router(status_router, prefix=app_settings.api_prefix)
    application.include_router(auth_router, prefix=app_settings.api_prefix)
    application.include_router(admission_router, prefix=app_settings.api_prefix)
    application.include_router(organizations_router, prefix=app_settings.api_prefix)
    application.include_router(me_router, prefix=app_settings.api_prefix)

    @application.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        candidate = request.headers.get("X-Request-ID", "")
        request_id = candidate if REQUEST_ID_PATTERN.fullmatch(candidate) else str(uuid4())
        token = request_id_context.set(request_id)
        request.state.request_id = request_id
        started = perf_counter()
        try:
            response: Response | None = None
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith(
                "/api/"
            ):
                origin = request.headers.get("Origin")
                if origin != app_settings.public_app_origin:
                    response = Response(
                        content=(
                            '{"error":{"code":"csrf_failed",'
                            '"message":"The request origin is not allowed",'
                            '"request_id":"' + request_id + '"}}'
                        ),
                        status_code=403,
                        media_type="application/json",
                    )
                else:
                    normal_value = request.cookies.get(normal_cookie_name(request))
                    pending_value = request.cookies.get(pending_cookie_name(request))
                if response is None and (normal_value or pending_value):
                    body = await request.body()

                    async def receive_body() -> Message:
                        return {"type": "http.request", "body": body, "more_body": False}

                    request._receive = receive_body
                    supplied = request.headers.get("X-CSRF-Token")
                    if not supplied and "application/x-www-form-urlencoded" in request.headers.get(
                        "content-type", ""
                    ):
                        try:
                            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
                            supplied = form.get("csrf_token", [None])[0]
                        except UnicodeDecodeError:
                            supplied = None
                    valid = False
                    if supplied:
                        with SessionFactory() as db:
                            database_now = database_utc_now(db)
                            if normal_value and not pending_value:
                                normal_session = db.scalar(
                                    select(ApplicationSession).where(
                                        ApplicationSession.session_token_hash
                                        == digest_secret(normal_value)
                                    )
                                )
                                if normal_session and normal_session.revoked_at is None:
                                    user = db.get(User, normal_session.user_id)
                                    valid = bool(
                                        user
                                        and user.status == "ACTIVE"
                                        and normal_session.absolute_expires_at > database_now
                                        and normal_session.last_seen_at
                                        > database_now - timedelta(hours=2)
                                        and csrf_token_matches(normal_session.csrf_secret, supplied)
                                    )
                            elif pending_value and not normal_value:
                                pending_session = db.scalar(
                                    select(PendingIdentitySession).where(
                                        PendingIdentitySession.token_hash
                                        == digest_secret(pending_value)
                                    )
                                )
                                valid = bool(
                                    pending_session
                                    and pending_session.consumed_at is None
                                    and pending_session.revoked_at is None
                                    and pending_session.absolute_expires_at > database_now
                                    and csrf_token_matches(pending_session.csrf_secret, supplied)
                                )
                    if not valid:
                        response = Response(
                            content=(
                                '{"error":{"code":"csrf_failed","message":"CSRF validation failed",'
                                '"request_id":"' + request_id + '"}}'
                            ),
                            status_code=403,
                            media_type="application/json",
                        )
                        response.headers["Cache-Control"] = "no-store"
            if response is None:
                response = await call_next(request)
        except Exception:
            logging.getLogger("saurorja.request").error("Unhandled request failure")
            response = Response(
                content=(
                    '{"error":{"code":"internal_error",'
                    '"message":"An unexpected error occurred","request_id":"' + request_id + '"}}'
                ),
                status_code=500,
                media_type="application/json",
            )
        assert response is not None
        route = getattr(request.scope.get("route"), "path", "unmatched")
        duration = perf_counter() - started
        logging.getLogger("saurorja.access").info(
            "%s %s %s", request.method, route, response.status_code
        )
        REQUESTS.labels(request.method, route, f"{response.status_code // 100}xx").inc()
        REQUEST_DURATION.labels(request.method, route).observe(duration)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path.endswith("/auth/callback") or request.url.path.endswith(
            "/invitations/accept"
        ):
            response.headers["Referrer-Policy"] = "no-referrer"
        request_id_context.reset(token)
        return response

    @application.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return application


app = create_app()
