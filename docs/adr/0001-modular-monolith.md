# ADR 0001: Modular monolith

## Context

Saurorja needs clean domain ownership and a future path to decomposition, but Module 1 has no independently scaling workloads or teams that justify distributed runtime complexity.

## Decision

Run backend modules in one FastAPI application. Keep application and domain modules separated from infrastructure adapters and use explicit interfaces only where a current caller needs them.

## Alternatives considered

- Microservices from the start: rejected because they introduce network, deployment, and operational failure modes without a current requirement.
- A single unstructured application: rejected because future domain changes would have no enforced module ownership or dependency direction.

## Consequences

Local development and deployment remain simple. Module boundaries need review and discipline. Extraction remains possible after real workload or ownership needs are measured.
