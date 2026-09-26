# ADR 0002: Python and FastAPI backend

## Context

Later Saurorja work is expected to include data-heavy solar performance and analytics while Module 1 needs a typed, API-first backend foundation.

## Decision

Use Python 3.12+ with FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, and psycopg 3. Keep HTTP handlers thin and place behavior behind application/domain interfaces.

## Alternatives considered

- A JVM backend: mature but adds ecosystem and operational weight without a current advantage for this foundation.
- A JavaScript-only backend: reduces languages but is less aligned with the expected data/analytics ecosystem.

## Consequences

Python's ecosystem supports future analytics. Type checks, lint, dependency locking, and explicit interfaces are needed to preserve maintainability.
