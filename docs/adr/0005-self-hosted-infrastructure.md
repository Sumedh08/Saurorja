# ADR 0005: Self-hosted infrastructure

## Context

Saurorja targets EPCs, O&M providers, RESCOs, and asset operators that need control over deployment and data location.

## Decision

Provide a Docker Compose stack using PostgreSQL, MinIO, Traefik, Prometheus, and Grafana. Do not require a cloud-managed service. Grafana is available on a loopback debug port, not through Traefik.

## Alternatives considered

- Managed cloud databases/storage/metrics: rejected because they constrain deployment choice and vendor neutrality.
- Kubernetes: rejected because the current stack does not need cluster orchestration.
- Kafka or Redis: rejected because no current asynchronous or cache workload justifies another persistent service.

## Consequences

Local and self-hosted setups share one reproducible topology. Operators own backups, upgrades, credentials, and host hardening. The Docker provider's socket access must be considered in host threat models.
