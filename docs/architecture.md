# Saurorja Architecture

## Overview

Saurorja is a self-hosted modular monolith. One FastAPI process owns the backend application and its module calls; one Next.js application serves the web interface. Traefik is the local HTTP entry point. PostgreSQL stores relational application data and identity/session state, while MinIO provides S3-compatible object storage. Prometheus scrapes API metrics and Grafana is available on a local-only debug port. Module 2 adds generic OIDC login, opaque server-side sessions, Organizations, Memberships, invitations, and application-level tenant checks; solar-domain data remains out of scope.

The dependency direction is API routes → application operations → domain modules → infrastructure adapters. Module 1 established shared configuration, database sessions, logging, errors, metrics, and storage capability. Module 2 adds identity and organization modules while preserving those conventions. Route handlers stay thin; authorization and tenant checks run in application operations.

## Why a modular monolith

The initial product has no measured scaling boundary that warrants distributed deployment. A single backend keeps local setup, failure modes, and operations small while keeping domain modules behind explicit interfaces. A future module can be extracted only when measured team, scaling, or isolation requirements justify the network and deployment cost.

## Why Python and FastAPI

Python has a strong data and energy analytics ecosystem for later solar performance work. FastAPI provides typed REST request/response handling and OpenAPI documentation while leaving application and domain rules outside route handlers. SQLAlchemy 2.x and Alembic provide explicit database sessions and schema migration management.

## Why REST first

REST over HTTP is easy to inspect, proxy, and consume from browsers and external integrations. It supports the current status use case without adding a second protocol. Revisit protocols only when an actual interaction has requirements REST cannot meet.

## Why PostgreSQL

PostgreSQL provides a mature relational system with transactions, constraints, indexing, and a low operational burden for the current platform foundation. It is the only database in Module 1. TimescaleDB may be evaluated later against real telemetry ingestion, retention, and query measurements; no extension is installed now.

## Why MinIO

MinIO offers self-hosted S3-compatible object storage without a cloud-provider dependency. Backend callers use a narrow `ObjectStorage` interface so SDK details stay in its adapter. Bucket initialization is idempotent and best-effort during application startup; status requests only observe availability. The upstream community image is no longer pullable, so Compose builds a pinned community release from source. The upstream repository is archived; this maintenance risk must be reviewed before production use.

## Why self-hosted infrastructure

Docker Compose runs the web app, API, PostgreSQL, MinIO, Traefik, Prometheus, and Grafana on a developer machine or self-hosted server. No cloud-managed services are required. Named volumes preserve database, object, metrics, and Grafana data across container recreation.

## Why no microservices or Kafka

There are no independent bounded workloads or durable asynchronous delivery requirements yet. Microservices, Kafka, Redis, brokers, and orchestration would increase operational burden before solving a demonstrated problem. A lightweight internal event publisher or transactional outbox can be reconsidered only when a concrete durable event consumer exists.

## Current seams and future path

`ObjectStorage` is the only infrastructure seam implemented now because both startup initialization and status reporting use it. Future `Repository`, `Clock`, `ExternalConnector`, and `EventPublisher` seams are documented possibilities, not placeholder interfaces. Add each only when a real caller needs it and behavior varies behind the seam.

Future domain areas are organizations, sites, assets, telemetry, weather, performance, diagnostics, economics, work orders, copilot, and connectors. They should be introduced as modules within this application, sharing the initial database deployment while preserving module-owned schemas and inward dependencies. Do not create directories or tables ahead of the first domain behavior.

Scale in measured steps: profile API/DB/storage workloads; tune PostgreSQL indexes, pooling, and retention; add background execution or durable event delivery for a demonstrated workload; isolate a module into a separate deployable only when its scaling, fault, or ownership needs outweigh distributed-system costs. Kafka, TimescaleDB, or separate services require a documented requirement and decision record.

## Observability and security

Structured JSON logs go to stdout with service, environment, timestamp, level, request ID, and message. Request metrics use bounded route templates. OpenTelemetry export is optional and remains inactive without a configured OTLP endpoint. Prometheus scrapes the API internally. Grafana binds to loopback port 3000 and is not routed through Traefik.

Authentication is delegated to a configured standards-compliant OIDC provider. FastAPI owns the relying-party flow and PostgreSQL-backed application sessions; the browser receives only opaque HttpOnly Saurorja session cookies. OIDC tokens are never stored in browser JavaScript or persistent browser storage. Cookie-authenticated unsafe requests require session-bound CSRF state and exact Origin validation.

Organization-scoped operations include an explicit Organization UUID and resolve an active User and Membership on every request. The shared PostgreSQL schema uses application-level tenant isolation; frontend filtering is not an authorization boundary. Owner-preservation and invitation-consumption invariants are protected transactionally.

Production deployments require HTTPS at the public reverse proxy, Secure cookies, complete OIDC configuration, explicit CORS origins, and non-development credentials. The included Compose HTTP entrypoint is for loopback development; operators must provide TLS termination for public deployments. Traefik access logging and Uvicorn access logging are disabled so callback query values cannot be written to proxy/application access logs. Authentication logs use safe categories and never contain tokens, cookies, session secrets, invitation secrets, OIDC subjects, or email addresses. Traefik uses the Docker provider with a read-only Docker socket mount for local discovery; operators should assess Docker socket access against their host threat model.

## Local routing

- `http://localhost/` → Next.js anonymous/authenticated shell
- `http://localhost/api/v1/status` → authenticated FastAPI dependency status endpoint
- `http://localhost/health` → FastAPI liveness endpoint
- `http://127.0.0.1:3000/` → Grafana local/debug UI (not through Traefik)
- `http://127.0.0.1:9090/` → Prometheus local/debug UI

The browser and API use a generic OIDC provider configured by the operator. ZITADEL is the documented default, but the base Compose stack does not install or manage an identity provider. See [OIDC configuration](oidc-configuration.md) and [operator authentication workflows](operator-authentication.md).
