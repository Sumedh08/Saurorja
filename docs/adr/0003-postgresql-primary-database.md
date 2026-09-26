# ADR 0003: PostgreSQL as the primary database

## Context

Module 1 needs durable relational storage and migration support but does not yet contain domain entities or a measured time-series workload.

## Decision

Use PostgreSQL as the only database, SQLAlchemy 2.x for sessions, and Alembic for migrations. Create no domain tables in Module 1.

## Alternatives considered

- MongoDB or a graph/vector database: no present data or query requirement supports them.
- TimescaleDB: defer until telemetry volume, retention, and query patterns justify the extension.
- Multiple databases: rejected because they multiply operations without a current need.

## Consequences

Transactions and relational constraints are available from the start with low operational complexity. Future telemetry performance must be measured before adding specialized storage.
