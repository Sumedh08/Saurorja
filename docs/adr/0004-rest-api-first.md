# ADR 0004: REST API first

## Context

The foundation exposes health/status data to a browser and will later need integration-friendly interfaces.

## Decision

Use versioned REST endpoints over HTTP under `/api/v1`, with explicit Pydantic request and response models.

## Alternatives considered

- gRPC: adds protocol and tooling complexity for browser-facing status calls.
- GraphQL: no current client query-composition need.
- Direct database access from the web application: couples clients to persistence and bypasses API contracts.

## Consequences

The API is easy to inspect and proxy. Future clients can share its stable contracts; protocol changes need a concrete requirement.
