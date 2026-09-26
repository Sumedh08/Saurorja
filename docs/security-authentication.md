# Authentication and access security

## Authentication boundary

FastAPI is a generic OIDC relying party using Authorization Code + PKCE S256, one-time state, nonce, a separate browser binding, issuer discovery, JWKS signature checks, exact issuer and audience validation, expiry validation, and `azp` validation where applicable. Authlib is used only through low-level protocol methods; its default Starlette cookie session middleware is not installed. OIDC tokens stay in server memory only as required by the callback/UserInfo operation and are not persisted or returned to the browser.

Normal sessions use a 12-hour absolute lifetime and a 2-hour idle timeout. Pending identity sessions expire absolutely after 10 minutes and can access only the admission workflow, CSRF retrieval, and logout. PostgreSQL stores only a SHA-256 digest of each opaque authentication cookie secret. It stores each independent 32-byte CSRF synchronizer secret recoverably so the authenticated `/api/v1/auth/csrf` endpoint can return it. CSRF possession alone never authenticates a request.

Local Saurorja logout is authoritative: it revokes the server-side session and clears cookies before best-effort upstream RP-Initiated Logout. No ID Token hint or refresh token is retained by default. Upstream logout failure cannot restore local access.

## Cookie and request protection

Production cookies are Secure, HttpOnly, SameSite=Lax, Path=/, have no Domain attribute, and use the `__Host-` prefix. `Secure=false` is allowed only for loopback HTTP when `APP_ENV=development`. Public deployment requires TLS at the public reverse proxy. The provided Compose HTTP entrypoint is for local development.

Every unsafe cookie-authenticated request requires both the 32-byte session-bound CSRF token and an exact Origin matching `PUBLIC_APP_ORIGIN`. This is independent of OIDC login state/nonce protection. Tokens remain in frontend memory only and are absent from URLs, persistent browser storage, analytics, referrers, and logs. XSS can still act as the current user; CSRF protection does not mitigate XSS.

## Admission and invitations

OIDC authentication alone grants no Saurorja access. An unknown identity receives only a short-lived PendingIdentitySession. A pending identity may inspect or accept an applicable invitation and log out, but cannot read the normal profile, Organizations, or member data.

Invitation URLs carry a high-entropy token in the URL fragment. The browser removes the fragment immediately and submits the token to FastAPI in a POST body. PostgreSQL stores only the token digest. Acceptance requires the same authenticated identity to present the token and a verified email that normalizes to the invitation address. Email is a mutable contact/collision field, not an identity key. Exact issuer/subject identity is never merged or linked automatically by email. Invitation validation/consumption, User/identity creation when needed, Membership creation, and session conversion commit atomically.

## Authorization and tenant isolation

Every Organization-scoped URL names an Organization UUID. The API resolves an ACTIVE User and ACTIVE Membership for each operation and applies the role matrix in application operations. Target records are queried within that Organization; changing a path UUID cannot escape the tenant boundary. Missing and inaccessible Organizations share not-found semantics. Frontend visibility is convenience only; server-side authorization is mandatory.

Only an Owner may grant, demote, suspend, or remove an Owner. Owner mutations serialize on the Organization row and preserve at least one ACTIVE Owner. Admin cannot manage Owners or grant OWNER, including by targeting its own Membership. Last-Owner successor changes during global User disablement are validated and committed in one transaction. Emergency `auth recover-owner` is a narrow CLI-only exception and is unavailable while an ACTIVE Owner exists.

## Disabled users and operator actions

Normal and pending requests re-check global User status. Operator disablement changes status and revokes normal and identity-matching pending sessions in the same transaction. Memberships and identity links remain intact; re-enabling does not restore sessions.

Bootstrap, Organization provisioning, disable/enable, controlled identity linking, and Owner recovery are host-operator CLI actions. They require an operator label and reason; destructive operations require target confirmation where appropriate. Successful operations record stable Saurorja IDs and safe action details transactionally. The record is not a generalized business audit stream.

## Logging and deployment

Application access logs record method, route template, status, duration, environment, service, request ID, and safe categories. Traefik and Uvicorn access-path logging are disabled for the OIDC callback boundary; callback spans are excluded from optional OTel instrumentation. Never log passwords, OIDC tokens/codes/state/nonce/PKCE, raw cookies, session or CSRF secrets, invitation tokens, OIDC subjects, or email addresses. Metrics labels use bounded route templates and status classes, never user/Organization identifiers.

Set `APP_ENV=production`, complete OIDC settings, an HTTPS public origin/issuer/callback, unique database/MinIO/Grafana credentials, and an explicit CORS allowlist before public exposure. Protect PostgreSQL credentials, backups, and volumes because they contain identity and session security state. Do not expose Grafana or Prometheus through Traefik; the current stack binds their debug ports to loopback.
