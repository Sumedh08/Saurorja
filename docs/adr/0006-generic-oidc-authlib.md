# ADR 0006: Generic OIDC with Authlib

- Status: Accepted for Module 2
- Date: 2026-09-26

## Context

Module 2 needs a standards-based OIDC relying party in FastAPI while keeping OIDC transaction state and Saurorja application sessions in PostgreSQL. The browser must receive no OIDC access or refresh tokens, and the Saurorja session must remain an opaque HttpOnly cookie. Authlib's Starlette convenience flow stores temporary state in `request.session`, which requires Starlette `SessionMiddleware` and is not the approved application-session design.

## Decision

Use Authlib 1.8.0 as the OIDC/OAuth protocol library. Use its low-level `StarletteOAuth2App` methods directly: `create_authorization_url`, `fetch_access_token`, `parse_id_token`, `userinfo`, and `create_logout_url`. Do not use the Starlette helpers `authorize_redirect`, `authorize_access_token`, `logout_redirect`, or `validate_logout_response`, and do not install `SessionMiddleware`.

Saurorja persists one-time transaction state, nonce digest, PKCE verifier, and browser-binding digest in PostgreSQL. The application consumes transaction state once, passes the authorization code and verifier to Authlib for code redemption, and uses Authlib discovery/JWKS/signature, issuer, audience, expiry, and authorized-party claim validation. After Authlib has validated the signed ID Token, Saurorja compares the returned nonce digest with the transaction's stored digest. Provider metadata cannot disable that binding check. OIDC tokens are discarded after the callback and are never persisted or sent to the browser.

Local logout invalidates PostgreSQL session state first. If discovery advertises an RP-Initiated Logout endpoint, Saurorja builds the standards-based request with client ID, one-time state, and the registered post-logout URI, without retaining an ID Token hint by default.

## Compatibility spike

The Authlib 1.8.0 spike ran against the actual package using a scratch `uv` environment and no project or application-session middleware. It verified:

- The low-level authorization URL method accepts caller-owned state, nonce, and PKCE verifier and emits `code_challenge_method=S256` plus a code challenge.
- The code redemption method accepts explicit callback URI, code, and verifier without reading Starlette request/session state.
- The ID Token parser receives explicit expected issuer and client configuration and validates through discovery/JWKS; the implementation can add the digest-based transaction nonce check after parser validation.
- The logout URL method emits the configured post-logout URI, caller-owned state, and client ID without cookie-backed state.
- The selected methods are available on Authlib's async Starlette client integration while avoiding the session-dependent convenience helpers.

The spike also signed an ID Token with a temporary local RSA key, supplied its public JWK through discovery metadata, and verified successful signature, exact issuer, client audience, and expiry validation through Authlib. This used no live credentials and did not add a test key or provider to the repository.

The local spike did not contact a live configured IdP. Provider-specific issuer metadata, JWKS rotation, and whether that provider accepts logout without an ID Token hint remain live deployment checks. The application starts without OIDC configuration, and local logout remains authoritative if upstream logout is unavailable.

## Alternatives considered

- Authlib's Starlette convenience flow: rejected because it assumes Starlette session storage for protocol state and would conflict with PostgreSQL-backed opaque sessions.
- Handwritten OIDC validation: rejected because it would duplicate security-sensitive protocol and JWT behavior.
- A different OIDC library: not selected because Authlib's low-level API passed the architecture compatibility spike without replacing Saurorja's persistence or session boundaries.

## Consequences

- OIDC details remain inside one infrastructure adapter; domain identity uses exact issuer/subject values and does not depend on Authlib or ZITADEL types.
- The application owns transaction single-use, browser binding, PendingIdentitySession admission, and application sessions.
- Authlib upgrades require rerunning the compatibility and security tests, especially discovery/JWKS, nonce/audience validation, and logout behavior.
- No ID Token hint is retained unless a concrete configured provider proves it necessary; upstream logout behavior must be recorded for that provider.
