# Saurorja

Self-hosted, vendor-neutral distributed solar performance intelligence and O&M platform.

**Module 2 establishes identity and Organization access.** Saurorja is a self-hosted modular monolith with a generic OIDC relying party, PostgreSQL-backed opaque sessions, Organizations, Memberships, invitations, and explicit tenant checks. Solar Sites, Assets, telemetry, and performance logic are not implemented yet.

## Requirements

- Docker Engine with the Compose plugin
- For local checks outside containers: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node.js 22+, and npm
- Ports 80, 3000, and 9090 available (Grafana and Prometheus bind to loopback)

## Run the complete local stack

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Compose starts PostgreSQL, runs the Alembic migration job, then starts the API and web app behind Traefik. The API waits for successful migration and MinIO startup. The source-build step may take several minutes. MinIO bucket initialization is idempotent in the API startup/init path; if MinIO is unavailable, the API starts, reports degraded status, and retries initialization outside request handling. API request-time health checks never create buckets. The pinned upstream MinIO repository is archived, so operators should track its maintenance/security status before production use.

The web shell and anonymous API endpoints start without an identity provider. Login intentionally fails closed until a self-hosted OIDC provider is configured. Follow [OIDC setup](docs/oidc-configuration.md), restart Compose, and use the operator bootstrap workflow to create the first Organization Owner.

Open:

- Web application: <http://localhost>
- API dependency status: <http://localhost/api/v1/status> (requires an authenticated Saurorja session)
- API liveness: <http://localhost/health>
- Grafana local/debug UI: <http://127.0.0.1:3000> (default development credentials are `admin` / `saurorja-grafana-dev`)
- Prometheus local/debug UI: <http://127.0.0.1:9090>

Traefik routes only the web application and API. Grafana and Prometheus are not routed through Traefik; their host bindings are limited to loopback. PostgreSQL and MinIO have no published host ports by default.

`/api/v1/status` reports PostgreSQL and object-storage readiness for a signed-in Saurorja user. `/health` is process liveness and does not check either dependency.

## Configure OIDC and bootstrap the first Owner

Saurorja does not install an identity provider in Compose. Configure a confidential OIDC client at a self-hosted provider (ZITADEL is the documented default), then set the generic `OIDC_*` values in `.env`. The issuer and callback URLs must be reachable by both the browser and API container and must match exactly. The required scopes are `openid email profile`; invitation acceptance requires a verified email claim. See [OIDC configuration](docs/oidc-configuration.md) for exact client settings.

After Compose has completed the migration and the OIDC provider is reachable, run the bootstrap command from an interactive terminal. Obtain the exact issuer and subject from the trusted IdP administration interface; the subject is entered through a hidden prompt, not a command-line argument:

```shell
docker compose exec -it api python -m app.cli auth bootstrap-admin \
  --organization-name "Example Operations" \
  --issuer "https://identity.example.org" \
  --operator "installation operator" \
  --reason "Initial self-hosted installation" \
  --operation-id "<new-unique-uuid>"
```

The command creates the initial Organization, User identity link, and OWNER Membership only when the installation is uninitialized. Sign in using that identity, then manage Members and invitations in the Organization UI. Other privileged commands are documented in [operator workflows](docs/operator-authentication.md).

## Developer commands

```shell
make dev           # docker compose up --build
make test          # backend pytest suite
make lint          # Ruff and ESLint
make format        # Ruff format and safe fixes
make typecheck     # mypy and TypeScript
make migrate       # run Alembic in the Compose environment
make compose-up
make compose-down
```

Install local dependencies and run the backend directly:

```shell
uv sync --project apps/api --all-groups
uv run --project apps/api --directory apps/api uvicorn app.main:app --reload --no-access-log
```

For direct local API development, set `DATABASE_URL` and MinIO endpoint settings to services reachable from the host. Compose injects container-network addresses. The frontend's normal API path is same-origin through Traefik.

## Configuration and persistence

Copy `.env.example` to `.env` and adjust values for a local deployment. The included values are public development examples and must not be used in production. Production mode rejects known example database and MinIO credentials, rejects debug mode and wildcard CORS, requires complete OIDC configuration, and requires HTTPS URLs. Supply strong unique credentials and an explicit CORS allowlist. Public deployments must terminate TLS at the public reverse proxy; the base Compose HTTP entrypoint is intended for loopback development.

Named volumes persist PostgreSQL, MinIO, Prometheus, and Grafana data. `docker compose down` preserves them. `docker compose down -v` deletes them and all persisted local data.

## Database migrations

The Compose `migrate` job runs `alembic upgrade head` on stack startup. To apply it manually, run `make migrate`. The initial revision is intentionally empty; Module 1 creates no solar or user tables. PostgreSQL is the only database. TimescaleDB can be evaluated later against measured telemetry volume and query patterns.

## Observability

The API emits structured JSON logs to stdout, propagates bounded request IDs, and exposes Prometheus request count and duration metrics at `/metrics` on the internal API network. Prometheus scrapes the API. Grafana is provisioned with Prometheus and a basic service-health dashboard.

OpenTelemetry instrumentation is optional. Leave `OTEL_EXPORTER_OTLP_ENDPOINT` blank to run with no exporter or collector. Configure an OTLP HTTP endpoint to enable trace export. The local Compose stack does not start an OTel collector.

## Quality checks and CI

GitHub Actions runs backend lint, format check, type check, migration, and tests against a PostgreSQL service container, plus frontend lint, type check, and production build. Pre-commit config includes whitespace, YAML/JSON, Ruff, and Gitleaks checks.

## Security assumptions

Saurorja delegates authentication to the configured OIDC provider and owns Organization authorization. Authentication alone does not grant Saurorja access; Users need active Memberships. The browser receives only opaque HttpOnly application-session cookies, and unsafe cookie-authenticated requests require a session-bound CSRF token plus exact Origin validation. Production requires HTTPS and Secure cookies. Operator commands are privileged and must be run only by trusted host operators; their actions are transactionally recorded. Protect PostgreSQL backups because they contain session and identity state. Traefik's Docker provider reads the Docker socket in read-only mode for local discovery; operators should assess socket access against their host threat model.

## Architecture

See [docs/architecture.md](docs/architecture.md), [Module 1 spec](docs/module-1-spec.md), [Module 2 spec](docs/module-2-spec.md), [OIDC configuration](docs/oidc-configuration.md), [operator workflows](docs/operator-authentication.md), [authentication security](docs/security-authentication.md), and [docs/adr](docs/adr/) for architecture decisions.
