# ADR 0008: Explicit application-level Organization tenancy

- Status: Accepted
- Date: 2026-09-26

## Context

Users may have Memberships in multiple Organizations. Every browser and future API client must identify the Organization being accessed, and the server must validate that the current User has an active Membership and sufficient role for every scoped operation.

## Decision

Use one PostgreSQL database and shared schema. Put the Organization UUID in every Organization-scoped API resource path and mirror it in frontend URLs. Resolve an `OrganizationContext` from the authenticated ACTIVE User and ACTIVE Membership for each request. Scope all target lookups by both Organization ID and resource ID in application operations. Return uniform not-found behavior for missing and inaccessible Organization resources. Do not store current Organization state in a session or use a tenant header as authority. PostgreSQL RLS is not introduced in Module 2.

## Alternatives considered

- Client-side filtering: rejected because it is not an authorization boundary.
- Mutable active-Organization session state: rejected because it hides request scope and behaves poorly for deep links and API clients.
- Schema/database per tenant or RLS: deferred because a shared schema with explicit application checks meets current requirements with lower operational complexity.

## Consequences

- Application operations and repository queries must accept and use explicit Organization scope.
- Tests must cover cross-Organization IDs and role enforcement server-side.
- A future RLS decision can add defense in depth without changing the Organization identity model or URL semantics.
