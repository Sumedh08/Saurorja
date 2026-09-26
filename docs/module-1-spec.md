# Saurorja Module 1 — Industrial Foundation Specification

**Status:** Approved for implementation
**Scope:** Foundation only. No solar, customer, asset, or operational domain logic.

## Problem Statement

Saurorja needs a production-oriented, self-hosted foundation that developers can run locally and operators can deploy on their own infrastructure. The repository is currently empty, so this module must establish the monorepo conventions, runtime, persistence and storage connections, API, minimal web status screen, observability, CI, and architecture records without prematurely implementing product domains.

## Solution

Build a modular monolith: one FastAPI backend application and one Next.js frontend, with PostgreSQL as the sole database and MinIO as S3-compatible object storage. Traefik provides the local entry point. Prometheus and Grafana provide a small, self-hostable metrics path. Keep module interfaces and dependencies local to the application; future decomposition remains possible without adding networked services now.

The user-facing web page reads the backend status endpoint through TanStack Query and shows liveness, dependency readiness, and version. The same status contract is useful to operators and automation. A separate liveness endpoint allows container orchestration to distinguish a running API process from its dependencies.

## User Stories

1. As a developer, I want a documented local environment, so that I can run the complete foundation without a cloud account.
2. As a developer, I want environment-based validated settings, so that local and production configuration are explicit.
3. As an operator, I want the frontend served through one reverse-proxy entry point, so that users do not need to know internal service ports.
4. As an operator, I want API liveness separated from dependency readiness, so that a temporary database outage does not look like a dead process.
5. As an operator, I want to see PostgreSQL and object-storage connectivity, so that I can diagnose the deployment foundation.
6. As a developer, I want database sessions managed through dependencies, so that request handlers do not own engine lifecycle.
7. As a developer, I want schema changes managed by Alembic, so that database evolution is repeatable.
8. As a developer, I want object storage behind one interface, so that MinIO details do not spread into callers.
9. As an operator, I want request IDs propagated through logs and responses, so that a request can be traced across components.
10. As an operator, I want structured logs and Prometheus metrics, so that the foundation can be monitored with self-hosted tools.
11. As a contributor, I want CI to lint, type-check, test, and build both applications, so that regressions are caught before merge.
12. As a future contributor, I want architecture decisions and intended module seams documented, so that later domain work stays consistent with the modular monolith.
13. As an operator, I want development credentials clearly distinguished from production secrets, so that example configuration is not mistaken for secure deployment configuration.

## Implementation Decisions

### Architecture and module interfaces

- Use a modular monolith. All current backend modules execute in one Python application and share one PostgreSQL deployment. Do not add microservices, distributed orchestration, CQRS, event sourcing, or DB-per-service.
- Keep dependencies pointed inward: API handlers call application operations; domain rules belong in domain modules when they exist; infrastructure adapters implement interfaces used by the application. Module 1 contains no solar-domain rules.
- Keep route handlers thin. They validate/serialize requests and responses, invoke the relevant application operation, and translate known errors through shared error handling.
- Implement only the immediately used `ObjectStorage` interface, with a MinIO adapter. The interface exposes the capability needed by this module: verify connectivity and ensure/check the configured bucket. It does not expose upload workflows.
- Do not add placeholder interfaces for `Repository`, `Clock`, `ExternalConnector`, or `EventPublisher`. Document them as possible future seams in the architecture guide. Do not add a generic internal event bus or transactional outbox now.
- Document future domain namespaces—organizations, sites, assets, telemetry, weather, performance, diagnostics, economics, work orders, copilot, and connectors—without creating empty directories or placeholder modules. Add a namespace when its first real module is implemented.
- Omit `packages/shared/` until a real maintained or generated contract is shared between the frontend and backend.

### Proposed file tree

```text
saurorja/
├── apps/
│   ├── api/
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── core/             # settings, logging, request IDs, errors, security
│   │   │   ├── api/              # router, v1 endpoints, response models
│   │   │   ├── application/      # system status orchestration
│   │   │   ├── db/               # SQLAlchemy engine/session and Alembic integration
│   │   │   ├── common/           # currently used cross-cutting storage interface
│   │   │   └── infrastructure/   # MinIO adapter and infrastructure health checks
│   │   ├── alembic/              # migration environment and initial empty revision
│   │   ├── tests/                # API, configuration, and adapter tests
│   │   └── pyproject.toml
│   └── web/
│       ├── src/app/              # App Router status page and layout
│       ├── src/components/       # status display and loading/error states
│       ├── src/lib/              # typed status client and TanStack Query setup
│       ├── public/
│       ├── package.json
│       └── Dockerfile
├── infra/
│   ├── traefik/                  # static proxy configuration
│   ├── prometheus/               # scrape configuration
│   ├── grafana/                  # provisioned datasource/dashboard
│   └── docker/                   # API Dockerfile and related build assets
├── scripts/                      # small developer/CI helper scripts only
├── docs/
│   ├── module-1-spec.md
│   ├── architecture.md
│   └── adr/
│       ├── 0001-modular-monolith.md
│       ├── 0002-python-fastapi-backend.md
│       ├── 0003-postgresql-primary-database.md
│       ├── 0004-rest-api-first.md
│       └── 0005-self-hosted-infrastructure.md
├── .github/workflows/ci.yml
├── .env.example
├── .pre-commit-config.yaml
├── docker-compose.yml
├── Makefile
├── README.md
└── .gitignore
```

The tree is intentional and excludes empty future-domain folders and the unused shared package. The exact placement of Docker build files may be adjusted during implementation if Compose/build-context ergonomics require it, without changing runtime responsibilities.

### Runtime topology

```text
Browser
  │ http://localhost
  ▼
Traefik ───────────────► Next.js web
  │ /api/* and /health
  ▼
FastAPI API ────────────► PostgreSQL
  │                     └► MinIO
  └ /metrics ◄────────── Prometheus ─── Grafana
```

- Compose services: `web`, `api`, `postgres`, `minio`, `traefik`, `prometheus`, and `grafana`.
- Traefik routes `/` to web and `/api/*` to API. It also routes `/health` to the API for the specified liveness contract. Internal ports may be published for debugging only where documented; normal use goes through Traefik.
- Grafana is not routed through Traefik. Publish it only on a documented loopback/debug port (default `127.0.0.1:3000`). Traefik exposes only the web application and API routes.
- Prometheus scrapes the API metrics endpoint. Grafana is provisioned with a Prometheus datasource and one small service-health dashboard if it remains straightforward.
- Use named persistent volumes for PostgreSQL, MinIO, and Grafana. Use a dedicated Compose network or networks with only required connectivity. Add health checks and `depends_on` readiness conditions where supported and useful; application startup must still handle dependency outages safely.
- Use non-root container users wherever upstream images and filesystem permissions permit. Traefik's Docker provider may require read-only Docker socket access; document this host-level trust implication. No Kubernetes or cloud-managed services.
- `docker compose up --build` is the primary local launch path.

### Component responsibilities

| Module | Responsibility |
|---|---|
| API core | Validated settings, structured logging, request-ID middleware, secure headers, CORS policy, and safe error translation. |
| API routing | Versioned REST routes and response schemas; invokes application operations without owning status orchestration. |
| Application status operation | Combines read-only dependency health observations into the versioned status result. |
| Database module | SQLAlchemy 2.x engine/session lifecycle, request-scoped session dependency, connection check, and Alembic migration environment. |
| Storage interface and MinIO adapter | Hide SDK details; connect/check configured bucket and expose a health result. |
| Web status client | Fetch and type the backend status response from the configured API base path. |
| Web status page | Show Saurorja branding and API, database, object-storage, and version state, with loading and backend-unavailable states. |
| Traefik | Provide local HTTP routing to web and API. |
| Prometheus/Grafana | Scrape API metrics and expose basic local visualization. |

### API contracts

All application API routes use `/api/v1`.

**`GET /health`** is a liveness endpoint, outside the versioned application API. It checks that the API process can serve requests and does not require database or MinIO availability.

Healthy response (`200`):

```json
{"status":"healthy"}
```

**`GET /api/v1/status`** reports application and dependency readiness. On a healthy deployment:

```json
{
  "service": "saurorja-api",
  "status": "healthy",
  "version": "0.1.0",
  "environment": "development",
  "database": {"status": "connected"},
  "object_storage": {"status": "connected"}
}
```

- Use explicit Pydantic response models. `version` comes from validated application settings.
- When a dependency is unavailable, return the same response shape with overall `status: "degraded"` and the affected dependency status `"disconnected"`. The status route remains useful during dependency outages; it does not return credentials, connection strings, exception text, or stack traces.
- The frontend treats a network/API failure as “backend unavailable,” distinct from a successful degraded status response.
- Unexpected API failures use one consistent JSON error envelope, for example `{"error":{"code":"internal_error","message":"An unexpected error occurred","request_id":"…"}}`. Known validation/not-found/unavailable conditions use stable codes and safe messages. Never serialize raw exceptions to clients.
- Include the request ID in an `X-Request-ID` response header. Accept a bounded, validated incoming ID or replace invalid input with a generated UUID.

### Configuration model

Use Pydantic v2 and `pydantic-settings`. Load environment variables and an optional local `.env` file. Keep settings in one typed settings module and validate them at startup.

Required setting names:

- `APP_NAME`, `APP_ENV`, `APP_VERSION`, `DEBUG`, `API_PREFIX`
- `DATABASE_URL`
- `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`
- `OTEL_SERVICE_NAME`, `LOG_LEVEL`

Local-safe defaults may be used for non-secret application metadata and local endpoints. `.env.example` supplies explicit development-only database and MinIO credentials and documents that they are not production secrets. If `APP_ENV=production`, reject the known example credentials and unsafe debug configuration. Production CORS origins must be an explicit allowlist; wildcard CORS is not permitted in production. Compose passes container-network endpoints (such as `postgres` and `minio`) while local non-container development can use localhost endpoints.

Do not commit `.env`; add it to `.gitignore`. Sanitize database URLs and credential-bearing settings from logs and exception context. Grafana's local default credentials, if used, must be development-only and configurable through environment variables.

### Database design

- PostgreSQL is the only database in Module 1. Use SQLAlchemy 2.x and psycopg 3 with a single engine/session factory per process.
- Provide a dependency-managed session with deterministic close/rollback behavior and an independent lightweight connection health check.
- Configure Alembic and include an initial revision with no domain tables. No infrastructure table is needed for the status screen.
- Do not add TimescaleDB now. Reconsider it only after real telemetry workload characteristics and query requirements are measured.
- Avoid logging raw SQL parameters where they could contain secrets.

### Object storage design

- Use MinIO through a narrow `ObjectStorage` seam; callers do not import the MinIO SDK.
- The adapter uses configured endpoint and credentials, checks connectivity, and idempotently ensures the configured application bucket exists. Health status is connected only when the endpoint responds and the bucket is available.
- Do not implement upload/download workflows or document models in Module 1.
- Handle storage failures as a dependency status in `/api/v1/status`; do not expose SDK exceptions. Bucket ensure runs idempotently in an explicit best-effort startup/init path. Request-time health/status checks are read-only and never create the bucket; API startup continues if MinIO is unavailable.

### Frontend

- Use the latest stable Next.js available when implementation begins, TypeScript strict mode, App Router, Tailwind CSS, shadcn/ui primitives only if they simplify the minimal page, and TanStack Query.
- The root page displays: “Saurorja”, “Distributed Solar Intelligence”, API health, database connectivity, object-storage connectivity, and version `0.1.0` from the backend response.
- Fetch `/api/v1/status` through the configured same-origin `/api` route. TanStack Query owns request state and bounded refresh behavior; no hard-coded health values are presented as live status.
- Provide a clear loading state, a backend-unavailable state, and a responsive, accessible, restrained layout. A successful degraded response shows which dependency is disconnected.
- Do not add dashboard, solar, user, or asset screens.

### Observability

- Emit structured logs to stdout with timestamp, level, service, environment, request ID, and message. Use a standard Python JSON logging formatter or equivalent with consistent fields.
- Middleware creates/propagates a request ID. Ensure errors and access logs avoid raw secrets and do not emit unbounded attacker-controlled values.
- Expose Prometheus-compatible request count, request duration, and response status class metrics. Label by bounded route template/method/status class; do not label with raw paths, request IDs, or other high-cardinality values.
- Add vendor-neutral OpenTelemetry instrumentation for FastAPI and database operations. Keep exporters configurable and tracing disabled or safely no-op by default when no collector endpoint is configured. The application must start and operate normally without a collector/exporter. No hosted observability dependency and no collector service in Module 1.
- Prometheus scrapes the API. Grafana is included in Compose with a provisioned datasource and at most one basic service-health dashboard.

### Testing decisions

Tests target observable behavior at the highest practical seam and avoid asserting private implementation details.

- API contract tests: `/health` liveness response and `/api/v1/status` healthy/degraded response models.
- Configuration tests: environment loading, local defaults, required settings, and production rejection of development credentials/wildcard origins.
- Database health tests: connected and failed connection behavior through the injected/session seam, without requiring network access for unit coverage.
- Object-storage tests: connected, bucket initialization/check, and unavailable behavior through a fake adapter or injected client; keep MinIO SDK details local to adapter tests.
- Error handling tests: stable JSON error shape, safe client message, and request ID propagation.
- Run an integration path against PostgreSQL in CI using a PostgreSQL service container where needed. Compose smoke verification should cover MinIO and the full local routing path when Docker is available.
- Frontend verification: ESLint, TypeScript typecheck, and production build. If adding UI tests, limit them to loading, unavailable, healthy, and degraded status rendering; do not add a testing framework without a concrete test need.
- Ruff lint/format, mypy (or Pyright; choose one and configure it consistently), pytest, frontend lint/typecheck/build run in CI on pushes and pull requests.
- Pre-commit checks cover Python lint/format, frontend lint where practical, whitespace/end-of-file, and an obvious secret scan.

### CI and developer experience

- GitHub Actions runs backend lint, type check, and tests; frontend lint, type check, and build. Use PostgreSQL service containers for integration tests that need a real database. CI does not deploy.
- Provide Make targets: `dev`, `test`, `lint`, `format`, `typecheck`, `migrate`, `compose-up`, and `compose-down`, with clear prerequisites and working directories.
- README documents prerequisites, `.env.example` copy, `docker compose up --build`, app/API URLs, health verification, migration command, shutdown, persistent data, internal debug ports, and security assumptions.
- Fresh clone should not require a cloud account or external managed service.

### Security baseline

- Authentication, authorization, RBAC, and user management are explicitly absent from Module 1.
- No real secrets in source, docs, CI files, or logs. Use environment-based secret configuration and reject known development credentials in production mode.
- Apply reasonable secure response headers. CORS is explicit in production. Keep internal stack traces server-side and return safe error envelopes.
- Validate request IDs and avoid log injection or high-cardinality metric labels. Do not log credentials or raw credential-bearing URLs.
- Document that the foundation is unauthenticated and should be deployed behind an appropriately controlled network until a later authentication module exists.

### Architecture documentation and ADRs

Create `docs/architecture.md` covering the modular monolith, FastAPI/Python, REST-first API, PostgreSQL, MinIO, self-hosted deployment, why there are no microservices or Kafka, and a measured future scaling path. Include future seam candidates without defining unused interfaces. Mention transactional outbox only as a possible later option if durable event delivery becomes a demonstrated requirement.

Create ADRs 0001–0005 for modular monolith, Python/FastAPI, PostgreSQL, REST API first, and self-hosted infrastructure. Each records context, decision, alternatives, and consequences. Add a concise `CONTEXT.md` glossary only when domain terms have crystallized; this foundation introduces no solar domain vocabulary requiring a glossary.

### Explicitly excluded

No users, organizations, authentication, RBAC, sites, assets, inverter connectors, telemetry ingestion, forecasting, pvlib, OpenSTEF, Chronos, anomaly detection, tariffs, financial logic, LangGraph, LLM integration, work orders, notifications, billing, synthetic data, Kafka, Redis, RabbitMQ, Cassandra, MongoDB, Neo4j, Pinecone, Elasticsearch, Spark, Databricks, dbt, Kubernetes, Eureka, Ribbon, saga orchestration, strangler pattern, gRPC, service mesh, DB-per-service, event sourcing, or CQRS.

## Testing Decisions

The test plan above is part of the feature contract. Tests must exercise externally visible status, configuration, health-check, storage, and error behavior through the highest practical interface. Existing project tests do not exist because the repository is empty; there is no prior art to preserve. The implementation must avoid brittle tests that depend on internal class layout or exact exception text.

## Out of Scope

All explicitly excluded capabilities listed above, plus upload workflows, domain tables, multi-tenant behavior, authentication, deployment automation, hosted cloud services, and a full observability dashboard. Module 1 creates a runnable foundation and documentation only.

## Definition of Done

- A fresh clone can follow README instructions and `.env.example` to configure local development.
- `docker compose up --build` starts web, API, PostgreSQL, MinIO, Traefik, Prometheus, and Grafana with appropriate health checks and persistent volumes.
- The browser loads the status page through Traefik; the page calls the backend and reflects real status data.
- API liveness and versioned status contracts behave as specified, including dependency degradation and safe errors.
- PostgreSQL health and MinIO connectivity/bucket checks work; Alembic migration commands work without creating future domain tables.
- Backend and frontend tests/checks, lint, type checks, and frontend production build pass.
- CI executes the required checks on push and pull request.
- The observability stack starts and Prometheus can scrape API metrics; logs include the defined structured fields.
- Architecture guide and all five ADRs exist and agree with the implementation.
- Local Compose verification is run. Any environment limitation or unverified requirement is reported explicitly rather than claimed as successful.

## Further Notes and Remaining Risks

1. This spec proposes that a degraded `/api/v1/status` response retain HTTP 200 and the same response schema. This makes dependency status consumable by the status UI; container readiness should use the detailed response/body or a future dedicated readiness probe if strict HTTP readiness semantics are required.
2. MinIO bucket initialization must remain idempotent and best-effort at startup; status checks must remain read-only so MinIO outages can be reported as degraded health.
3. The Docker provider for Traefik can require access to the Docker socket. Even read-only mounting should be reviewed for the target host's threat model; a file-provider alternative may be preferable if Docker API access is not acceptable.
4. “Latest stable Next.js” is time-sensitive and must be resolved against the stable release available when implementation begins. Python 3.12+ is required.
5. Grafana provisioning, non-root execution, and the complete Compose stack depend on upstream container image behavior and should be verified in the actual local Docker environment. Grafana is local/debug only and is not exposed through Traefik.
6. The upstream MinIO Community Edition repository is archived and pullable prebuilt images are unavailable in the configured registries. To preserve the requested MinIO runtime without a third-party binary, Compose builds a pinned community release from source; the first build is substantially slower than pulling a maintained image. Review this upstream maintenance risk before production use.
