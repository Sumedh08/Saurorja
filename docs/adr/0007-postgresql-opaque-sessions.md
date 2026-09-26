# ADR 0007: PostgreSQL-backed opaque browser sessions

- Status: Accepted
- Date: 2026-09-26

## Context

Module 2 requires browser authentication without exposing OIDC tokens to JavaScript and without adding Redis or a session service. Sessions must be revocable immediately when a User is disabled, and unlinked OIDC identities need a restricted pending-admission session.

## Decision

Store normal and pending session records in PostgreSQL. Give the browser a separate high-entropy opaque HttpOnly cookie for each session type; persist only the SHA-256 digest of each authentication secret. Store a separate high-entropy synchronizer CSRF secret recoverably in each server-side row so the CSRF endpoint can return it after authenticating the opaque cookie. Use 12-hour absolute and 2-hour idle limits for normal sessions, and a 10-minute absolute limit for pending identity sessions. No remember-me or refresh-token persistence is included.

## Alternatives considered

- Browser-held OAuth tokens: rejected because XSS-accessible storage would expose IdP credentials and complicate revocation.
- Signed JWT application sessions: rejected because immediate global User disablement and server-side invalidation require state anyway.
- Redis or a dedicated session service: rejected because PostgreSQL already exists and no current scale requirement justifies additional infrastructure.
- Hash-only CSRF storage: rejected because `/auth/csrf` must return the synchronizer token and a one-way digest cannot be reversed.

## Consequences

- Session lookup, expiry, revocation, and CSRF retrieval are handled by existing PostgreSQL.
- PostgreSQL backups and credentials protect sensitive session state; session-cookie secrets remain hash-only.
- A bounded operator prune command removes old terminal rows; cleanup does not determine whether a session is valid.
- CSRF state is recoverable server-side but never authenticates without the opaque session cookie.
