# Module 2 Specification — Identity, Organizations & Tenancy Foundation

**Status:** Approved implementation contract
**Scope:** Module 2 only. This document is authoritative for the implementation.

## Problem Statement

Module 1 provides a runnable self-hosted modular-monolith foundation but has no application identity or access control. Its status API is unauthenticated, and it has no Saurorja User, Organization, Membership, or tenant boundary. The product cannot safely admit business users or support organization-scoped work until it can authenticate people, connect them to Saurorja identities, and enforce Organization membership on the server.

Module 2 establishes that foundation without adding solar-domain objects or changing the system into distributed services.

## Solution

Add generic OIDC browser authentication owned by FastAPI, backed by PostgreSQL opaque sessions. Keep OIDC credentials and tokens on the server. Model Saurorja Users independently from external identities, and grant product access only through explicit Organization Memberships. Resolve an explicit Organization ID on every organization-scoped request and authorize it against the current User and Membership.

Owners and Admins manage the limited organization and membership capabilities defined below. Operators use explicit CLI commands for installation bootstrap, Organization provisioning, global User status, identity linking, ordinary Owner succession during User disablement, and emergency Owner recovery. Invitations bind possession of a one-time token to a verified OIDC email. No authenticated identity gains Saurorja access solely by authenticating at the IdP.

## 1. Goals

- Provide real browser login and local logout using OIDC Authorization Code with PKCE.
- Use ZITADEL as the documented default self-hosted IdP while keeping Saurorja application/domain behavior generic OIDC.
- Keep OIDC access, refresh, and ID tokens out of browser JavaScript and browser storage.
- Store only opaque Saurorja session identifiers in HttpOnly cookies; persist session state in PostgreSQL.
- Create stable Saurorja Users, exact issuer/subject identity links, Organizations, Memberships, and Invitations.
- Enforce the approved coarse organization-role matrix and tenant boundary in application operations.
- Support invite-based onboarding, operator-only installation bootstrap, and operator-only additional Organization provisioning.
- Preserve Module 1 REST, PostgreSQL, Alembic, FastAPI, Next.js, Traefik, Prometheus, Grafana, structured logging, and optional OpenTelemetry conventions.
- Keep operations self-hosted and cloud-independent without adding Redis, an IdP service to the base Compose stack, or another infrastructure dependency.

## 2. Explicit Non-goals

No public Organization signup; password authentication; platform-admin web role; granular permission-string framework; organization business-capability classifications; Site, Asset, inverter, telemetry, weather, forecasting, diagnostics, economics, work orders, technician profile, email/SMTP, notifications, billing, subscriptions, customer portal, LLM/copilot, vector database, or solar-specific business logic.

No Redis, Kafka, RabbitMQ, database-per-tenant, schema-per-tenant, PostgreSQL RLS, microservices, gRPC, Kubernetes, event sourcing, CQRS, generic event bus, generalized audit platform, or background-job service.

No API bearer-token product for third-party clients is defined in Module 2. The Module 2 browser uses the Saurorja cookie session. A later API-client authentication contract requires a separate design.

## 3. User Stories

1. As an invited business user, I want to authenticate with the configured identity provider, so that I do not need a Saurorja password.
2. As an authenticated person with no Saurorja membership, I want to see a safe no-access state, so that IdP authentication alone does not grant product access.
3. As an invited person, I want to accept an invitation after authenticating with the matching verified email, so that the invitation is bound to token possession and identity proof.
4. As an existing Saurorja User, I want to belong to multiple Organizations, so that one login can serve my separate business relationships.
5. As a member of multiple Organizations, I want to choose an Organization from an explicit URL-based context, so that links and API requests identify their tenant unambiguously.
6. As an Organization member, I want to see basic Organization information and my own membership, so that I understand the context and access assigned to me.
7. As an Owner, I want to view the Organization member directory and manage non-Owner memberships, so that I can maintain access.
8. As an Admin, I want to administer ordinary members and invitations, so that routine access work does not require an Owner.
9. As a Manager, I want to view the Organization and member directory, so that I can understand who belongs to it without changing access.
10. As a Technician or Viewer, I want to see the Organization and my own membership, so that I can confirm my access without seeing the full directory.
11. As an Owner, I want to promote an existing member to Owner, so that ownership can be shared deliberately.
12. As an Owner, I want to demote, suspend, or remove another Owner only when another active Owner will remain, so that no Organization is left without an active Owner.
13. As an Owner or Admin, I want to create and revoke invitations, so that new members can join without email infrastructure.
14. As an invitee, I want a pending invitation page to disclose only the inviting Organization and invitation terms, so that I can confirm what I am accepting.
15. As an operator, I want to bootstrap the first Organization and Owner from a trusted IdP issuer and subject, so that a fresh installation has an initial authorized user without a public bootstrap endpoint.
16. As an operator, I want to provision another Organization for an existing active User, so that new tenants remain invite/admin-created.
17. As an operator, I want to disable a Saurorja User and revoke all of their sessions, so that a disallowed account loses access immediately after the operation commits.
18. As an operator disabling a sole Owner, I want to name a valid active successor for every affected Organization, so that the operation preserves ownership invariants atomically.
19. As an operator, I want to enable a disabled User without restoring Memberships or sessions, so that re-enabling does not silently recreate access.
20. As an operator, I want to link an exact external identity to an existing User explicitly, so that controlled identity migration does not depend on email matching.
21. As a security operator, I want local logout to invalidate Saurorja access even when the IdP is unavailable, so that upstream logout failure cannot restore local access.
22. As a user, I want the application to expire an idle or old session, so that unattended browser sessions do not live indefinitely.
23. As an API consumer, I want Organization IDs in resource paths and consistent errors, so that tenant context is explicit and testable.
24. As an operator, I want narrowly scoped records of privileged CLI actions, so that bootstrap and recovery can be traced without a generalized audit platform.
25. As a developer, I want deterministic tests for OIDC validation, tenant isolation, role enforcement, and transaction races, so that security invariants are not inferred from UI behavior.
26. As a trusted self-hosted operator, I want a tightly constrained CLI to restore an Owner when normal Organization administration cannot do so, so that recovery does not require routine direct database surgery.

## 4. Domain Terminology and Ownership

- **User:** Stable Saurorja application identity. User ID is the application’s durable key; email is mutable profile/contact data.
- **ExternalIdentity:** Verified link between a Saurorja User and one external OIDC identity, uniquely keyed by exact issuer and subject.
- **Organization:** Business/entity represented in Saurorja. It has no EPC/O&M/RESCO type or capability field in this module.
- **Membership:** A User’s Organization-scoped relationship, role, and lifecycle. A User’s role is never global.
- **Invitation:** Single-use admission offer for one Organization, verified email, and non-Owner role.
- **AuthenticatedPrincipal:** Request-scoped representation of a valid ApplicationSession and ACTIVE User. It contains internal User ID and authentication context needed by application operations; it is not an IdP token.
- **OrganizationContext:** Request-scoped Organization plus caller’s active Membership, resolved from the URL Organization ID and authenticated principal.
- **PendingIdentitySession:** Short-lived, restricted server-side session for an authenticated OIDC identity that is not linked to a Saurorja User. It is not an ApplicationSession and cannot access normal application resources.
- **InvitationAcceptanceAttempt:** Short-lived proof that the caller possessed a valid invitation token. It binds the invitation to a pending or normal Saurorja session without retaining the raw token.
- **OperatorAction:** Narrowly scoped record of a successfully committed privileged CLI operation.

Keep identity/admission responsibilities and Organization/Membership responsibilities as distinct modules inside the existing FastAPI process. Route handlers remain thin; application operations own transactions and authorization decisions. PostgreSQL remains shared by all modules.

Follow the existing synchronous SQLAlchemy 2.x Session/dependency style in Module 1. Do not rewrite the database layer to async solely for authentication. Use the current configuration, error envelope, request-ID, and same-origin Traefik conventions; extend them only where this security contract requires it. Do not add placeholder Repository, Clock, ExternalConnector, or EventPublisher interfaces.

## 5. Domain Model and Persistent Concepts

All IDs are UUIDs generated by the application or PostgreSQL using an established secure/random UUID implementation. Timestamps are timezone-aware UTC. Records are not hard-deleted through Module 2 workflows, except expired/revoked ephemeral session and transaction data during cleanup.

### 5.1 User

- **Purpose:** Stable Saurorja application identity.
- **Identity:** UUID primary key. Never identify a User by email.
- **Fields:** ID, status ACTIVE/DISABLED, nullable email, nullable normalized email, nullable email-verified timestamp, nullable display name, created/updated timestamps, status-changed timestamp.
- **Profile rules:** Bootstrap may create the User with no email or display name. On successful linked OIDC authentication, profile data may be refreshed from claims. Only a strictly boolean email_verified=true claim may populate verified-email profile fields. A collision with another User’s verified normalized email must not merge accounts or overwrite the existing profile value.
- **Creation:** Only invitation acceptance, initial bootstrap, or the narrowly scoped emergency `auth recover-owner` workflow may create a User. Ordinary OIDC authentication never creates one.
- **Lifecycle:** ACTIVE ↔ DISABLED; neither state is terminal. User records are not deleted in Module 2.
- **Relationships:** One-to-many ExternalIdentity, Membership, and ApplicationSession.
- **Invariants:** Disabled Users cannot establish sessions or perform authenticated operations. Disabling preserves identities, Organizations, and Memberships. Re-enabling creates no session and changes no Membership.

### 5.2 ExternalIdentity

- **Purpose:** Separate Saurorja identity from configured OIDC provider identity.
- **Identity:** UUID primary key; unique exact issuer/subject pair.
- **Fields:** ID, User ID, exact issuer, exact subject, created timestamp, last-authenticated timestamp.
- **Issuer semantics:** Persist the exact issuer value validated from configured discovery metadata. Do not case-fold, trim, or otherwise rewrite issuer or subject for matching.
- **Lifecycle:** Created only by successful invitation acceptance, installation bootstrap, explicit trusted operator identity-link command, or the narrowly scoped emergency `auth recover-owner` workflow. No email-based creation/linking. Links are not removed or reassigned by ordinary APIs.
- **Relationships:** Belongs to exactly one User. A User may have multiple ExternalIdentity rows.
- **Invariant:** An exact issuer/subject pair maps to no more than one Saurorja User.

### 5.3 Organization

- **Purpose:** Business/entity tenant.
- **Identity:** UUID primary key, used in all Organization-scoped API and frontend URLs.
- **Fields:** ID, name, created/updated timestamps.
- **Lifecycle:** Created by initial bootstrap or operator-only provisioning. No delete, deactivate, business capability, or type workflow is introduced.
- **Relationships:** Has Memberships and Invitations.
- **Invariant:** Every committed Organization has at least one effective active Owner: a Membership with role OWNER and status ACTIVE whose User status is also ACTIVE. A globally DISABLED User’s retained Membership does not count as an available Owner. This cross-row invariant is enforced transactionally by every supported write path, not by a UI check.

### 5.4 Membership

- **Purpose:** Organization-scoped access relationship.
- **Identity:** UUID primary key.
- **Fields:** ID, Organization ID, User ID, role, status, created/updated timestamps, last-activated timestamp, last-suspended timestamp, nullable terminal removed timestamp.
- **Roles:** OWNER, ADMIN, MANAGER, TECHNICIAN, VIEWER.
- **Statuses:** ACTIVE, SUSPENDED, REMOVED.
- **Lifecycle:** ACTIVE ↔ SUSPENDED; ACTIVE → REMOVED; SUSPENDED → REMOVED. REMOVED is terminal. Rejoining after removal normally requires a fresh accepted Invitation and a new Membership row; the only exception is the narrowly scoped emergency `auth recover-owner` operation, which preserves the removed row and creates one new row for its exact recovery identity and Organization.
- **Relationships:** Belongs to one User and one Organization.
- **Invariants:** At most one Membership for a User/Organization pair whose status is not REMOVED. SUSPENDED keeps its role but grants no Organization access. Role and status are checked on every protected operation.

### 5.5 Invitation

- **Purpose:** Single-use admission to one Organization for a normalized email and non-Owner role.
- **Identity:** UUID primary key; unique token digest; no raw token is persisted.
- **Fields:** ID, Organization ID, email, normalized email, role ADMIN/MANAGER/TECHNICIAN/VIEWER only, 32-byte SHA-256 token digest, status PENDING/ACCEPTED/REVOKED/EXPIRED, creator Membership ID, created/expiry/accepted/revoked timestamps, accepted User ID.
- **Default expiry:** Seven days from creation. Expiration is checked against database UTC time. A PENDING row transitions to EXPIRED when touched after expiry; no scheduled worker is required.
- **Lifecycle:** PENDING → ACCEPTED, REVOKED, or EXPIRED. Final states are terminal.
- **Uniqueness:** At most one PENDING invitation per Organization/normalized-email pair. An expired/revoked invitation does not prevent a new invite.
- **Invariant:** Invitations never directly grant OWNER. Owner promotion is a separate authenticated Owner action.

### 5.6 ApplicationSession

- **Purpose:** Server-side Saurorja browser session.
- **Identity:** UUID primary key plus a unique hash of the opaque cookie secret.
- **Fields:** ID, 32-byte session-secret digest, User ID, ExternalIdentity ID, 32-byte high-entropy CSRF secret stored recoverably in PostgreSQL, normalized verified-email snapshot and verified boolean at authentication, created/last-seen/absolute-expiry/revocation timestamps, revocation reason.
- **Lifecycle:** Active until logout, explicit revocation, User disablement, rotation, absolute expiration, or idle expiration. All are terminal for that session row. A new successful login creates a new session.
- **Invariants:** Raw session secret exists only in the browser cookie and process memory while issued/validated. PostgreSQL stores only the session-secret digest. The CSRF secret is a separate value and is stored recoverably server-side so the CSRF endpoint can return it; it never authenticates a request. Do not store access or refresh tokens. User status is checked on every protected request.

### 5.7 PendingIdentitySession

- **Purpose:** Restricted admission session after OIDC authentication when the exact identity is not linked to a Saurorja User.
- **Identity:** UUID plus unique hash of a separate opaque cookie secret.
- **Fields:** ID, token digest, exact issuer/subject, nullable display name and normalized verified email, verified boolean, 32-byte high-entropy CSRF secret stored recoverably in PostgreSQL, created/absolute-expiry/consumed/revoked timestamps.
- **Lifetime:** Ten minutes absolute; no idle extension.
- **Lifecycle:** Active only until conversion by successful invitation acceptance, logout, explicit revocation, or expiry. Consumed/expired/revoked are terminal.
- **Invariants:** It is not an ApplicationSession. It permits only invitation claim/inspection/acceptance and logout/CSRF-token retrieval needed for those operations. Before each operation, re-resolve issuer/subject; if it has become linked to a DISABLED User, reject and revoke the pending session.

### 5.8 OIDC Transaction State

- **Purpose:** One-time, short-lived server-side state for login and logout redirects.
- **Identity:** UUID primary key and unique state digest.
- **Fields:** Transaction kind, state digest, nonce digest for login, PKCE verifier for login, browser-binding digest for login, exact expected issuer, safe relative return path, optional Invitation ID proven by token submission, created/expiry/consumed timestamps.
- **Lifetime:** Ten minutes absolute. Expired transactions are terminal.
- **Storage:** State, nonce, and browser-binding values are random and compared by digest. The PKCE verifier must be available for code redemption, so it is stored server-side only for the transaction lifetime and deleted/cleared on consumption/expiry. Never log these values.
- **Browser binding:** Login initiation sets a separate short-lived HttpOnly cookie. One in-flight login per browser is supported; a new initiation replaces the browser-binding cookie, making an older parallel callback fail closed. This cookie is not an application session.
- **Lifecycle:** PENDING → CONSUMED on validated success or validated provider error; PENDING → EXPIRED by time. No replay.

### 5.9 Installation/Bootstrap State

- **Purpose:** Record whether initial bootstrap has completed and make concurrent/repeated bootstrap safe.
- **Identity:** Singleton row enforced by primary key/check constraint.
- **Fields:** Initialized timestamp, initial Organization/User/ExternalIdentity IDs, and Organization name supplied at bootstrap.
- **Lifecycle:** Absent → initialized once. Terminal; no reset command.
- **Invariant:** No HTTP route can create or reset this row. Installation state and initial Organization/User/identity/Owner Membership commit in one transaction.

### 5.10 InvitationAcceptanceAttempt

- **Purpose:** Preserve proof of invitation-token possession across OIDC redirects without putting the token in a query string or browser storage.
- **Identity:** UUID primary key.
- **Fields:** ID, Invitation ID, exactly one of ApplicationSession ID or PendingIdentitySession ID, created/expiry timestamps, consumed timestamp.
- **Lifetime:** No longer than ten minutes and never later than invitation expiry.
- **Lifecycle:** Created after the raw token is validated; consumed atomically with acceptance; expired attempts are unusable.
- **Invariants:** It stores neither token nor token digest. It is bound to the session that proved possession and cannot be transferred by presenting its UUID alone. Acceptance rechecks invitation state, expiry, email, identity conflict, User status, and Membership uniqueness.

### 5.11 OperatorAction

- **Purpose:** Narrow traceability for completed privileged CLI operations.
- **Fields:** ID, constrained action type, non-email operator label, required non-sensitive reason, target IDs/details as structured JSON, non-null operation UUID for safe command retries, created timestamp.
- **Recorded operations:** Bootstrap, Organization provisioning, User disable/enable, explicit identity link, Owner succession during disablement, and emergency Owner recovery (`OWNER_RECOVERED`).
- **Invariant:** The record commits in the same transaction as the operation. Details contain stable Saurorja IDs and affected Organization/successor mapping, not raw OIDC subjects, email addresses, session values, or tokens. This is not a general domain-event or audit system.

## 6. State Transitions

| Concept | Allowed transitions | Terminal behavior |
|---|---|---|
| User | ACTIVE ↔ DISABLED by operator CLI | No hard deletion. DISABLED rejects authentication and every request. |
| Membership | ACTIVE ↔ SUSPENDED; ACTIVE/SUSPENDED → REMOVED | REMOVED is terminal. Rejoining normally requires a new row from an accepted Invitation; only emergency `auth recover-owner` may create a new row without an Invitation, under Section 12.5. |
| Invitation | PENDING → ACCEPTED, REVOKED, or EXPIRED | All final states are terminal. Expiry is materialized lazily when touched. |
| ApplicationSession | Active → revoked, rotated, idle-expired, absolute-expired, or User-disabled | Terminal; never reactivate a session. |
| PendingIdentitySession | Active → consumed, revoked, or expired | Terminal; successful acceptance creates a distinct ApplicationSession. |
| OIDC transaction | PENDING → CONSUMED or EXPIRED | Terminal and single use, including provider-error callback. |
| InvitationAcceptanceAttempt | Active → consumed or expired | Terminal and bound to its original session. |
| Installation state | Absent → initialized | Terminal; no reset or public bypass. |

An invitation acceptance with a pending identity performs User creation, ExternalIdentity creation, Membership creation, Invitation consumption, attempt consumption, pending-session consumption, and normal ApplicationSession creation in one PostgreSQL transaction. It checks all constraints and uses the locking rules below. The browser receives the new cookie only after commit; if the response is lost, the user can authenticate again through the now-linked identity.

The emergency `auth recover-owner` workflow is the only operator exception that can create a User or ExternalIdentity outside bootstrap, invitation acceptance, and explicit identity linking; its one-Organization scope and transaction are specified in Section 12.5.

## 7. Authentication Architecture

### 7.1 Ownership and flow

FastAPI is the generic OIDC relying party and owns Saurorja sessions. Next.js is a same-origin browser UI and never owns OIDC tokens.

1. The browser navigates to login. FastAPI creates a server-side OIDC transaction with independent cryptographically secure state and nonce, PKCE verifier/challenge using S256, browser-binding secret, expected configured issuer, expiry, and a validated local return path.
2. FastAPI sets a short-lived transaction browser-binding cookie with HttpOnly, SameSite=Lax, Path=/, no Domain, and Secure in production, then redirects to the configured authorization endpoint with response_type=code, scope including openid email profile, state, nonce, and PKCE challenge. Use query response mode so the Lax transaction cookie accompanies the top-level callback GET.
3. The IdP authenticates the person and redirects to the exact registered FastAPI callback URI using the authorization-code query response.
4. FastAPI requires callback state to match the transaction digest and the separate browser-binding cookie to match the saved digest. It consumes state once and redeems the code server-to-server with the saved verifier and client authentication.
5. FastAPI validates the ID Token using a maintained OIDC/OAuth library: signature and permitted algorithm, exact issuer, configured client ID in aud, azp when required for multiple audiences, expiration and time claims, transaction nonce, and non-empty subject. Require discovery issuer equality. Use discovery and JWKS from the configured issuer only; cache keys, refresh once for an unknown key ID, and fail closed if validation cannot complete.
6. If verified email is absent from the ID Token but required for an invitation, UserInfo may be called server-to-server with the transient access token. Require UserInfo sub to equal validated ID Token sub. Require email_verified to be the JSON boolean true for invitation acceptance. Discard access/refresh tokens after the OIDC operation; never persist or return them.
7. Resolve exact issuer/subject. Perform network token exchange and claim validation before opening the short database mutation transaction. For an existing identity, lock the mapped User row in shared mode, require User ACTIVE, refresh permitted profile attributes, rotate to a new ApplicationSession, and revoke prior browser session state. This lock serializes login with operator disablement: if disable commits first, login sees DISABLED; if login commits first, disable revokes the new session. For an unknown identity, do not create a User or identity link; create only a ten-minute PendingIdentitySession and revoke prior browser session state in the same transaction. If the exact identity belongs to a DISABLED User, reject; do not downgrade it into an unlinked pending identity or revoke an unrelated existing session on a failed admission.
8. Redirect to a clean, same-origin frontend path. Do not put tokens or raw identity claims in redirect parameters.

Discovery and issuer are operator configuration, never selected by request parameters. Require HTTPS issuer/discovery and registered callback/logout URIs in production. Accept HTTP only for explicitly local development loopback URLs. Reject unsafe external return paths, protocol-relative paths, and open redirects.

### 7.2 OIDC library gate

Before selecting a concrete library, perform and record a technical spike proving all of the following:

- Authorization Code + PKCE S256, state, nonce, exact issuer/audience validation, discovery/JWKS rotation, callback error handling, and no token exposure to browser code.
- The library can use the application’s PostgreSQL-backed transaction/session persistence or be cleanly limited to OIDC protocol operations without installing its default cookie session as Saurorja authentication.
- Callback/session hooks permit single-use transaction consumption and the approved PendingIdentitySession path.
- Tokens and callback parameters can be excluded from library, framework, proxy, and tracing logs.
- RP-Initiated Logout behavior is verified against the configured self-hosted provider.

Authlib may be evaluated. Its documented Starlette flow uses session state and examples may place temporary credentials in a signed cookie; that default must not silently replace the opaque PostgreSQL application session. Do not write OIDC protocol validation from scratch. Record the selected maintained library and justification in implementation documentation/ADR after the spike.

### 7.3 Logout and upstream IdP semantics

Local logout is authoritative and ordered:

1. Invalidate the server-side ApplicationSession or PendingIdentitySession.
2. Clear its browser cookie in the response.
3. If supported/configured, redirect the browser to the discovery-advertised RP-Initiated Logout endpoint with a one-time state, configured client_id, and exact registered post-logout URI.
4. Validate returned logout state and show the signed-out page. Upstream failure never restores a Saurorja session.

Do not retain an ID Token hint by default. RP-Initiated Logout permits client_id with a post-logout URI without an ID Token hint, although redirect behavior is provider-dependent. During the provider spike, verify whether the selected provider accepts this. If it requires an id_token_hint for the agreed redirect behavior, the implementation must either (a) retain only that ID Token encrypted server-side for no longer than the ApplicationSession lifetime using an established encryption library and separately managed key, or (b) explicitly document upstream logout as unsupported while preserving local logout. Do not retain access or refresh tokens. Document the decision and minimum retained fields before implementation.

### 7.4 Authentication failure behavior

Invalid state, nonce, browser binding, issuer, audience, signature, expiry, callback code, disabled User, or transaction replay produces a safe generic authentication failure and no normal session. Do not reveal whether an issuer/subject or User exists. Consume/expire the transaction when a callback is attributable to it. Keep an existing session unchanged on a failed OIDC callback; rotate it only after validated authentication succeeds.

## 8. Application Session, Pending Identity, and CSRF

### 8.1 Normal ApplicationSession

- Cookie value: at least 256 bits of CSPRNG output, URL-safe encoded. Cookie carries only this opaque value.
- Database lookup: SHA-256 digest of cookie value; unique indexed digest only. Never persist the raw secret.
- PostgreSQL lifetime: 12-hour absolute lifetime and 2-hour idle timeout. No remember-me option. Every successful authenticated request checks both, checks User ACTIVE, and updates last_seen_at atomically using database time.
- Set browser cookie Max-Age no later than the server-side absolute expiration; idle expiration is enforced server-side and may expire earlier. On an expired/revoked session response, clear the cookie.
- Cookie: HttpOnly, SameSite=Lax, Path=/, no Domain. Production uses Secure and a __Host- prefixed name. Development may use a distinct non-prefixed cookie name and Secure=false only for loopback HTTP; never enable this exception outside APP_ENV=development.
- CSRF state: persist a separate 32-byte high-entropy CSRF secret in this row in recoverable form; it is not the session secret. The session cookie remains the only authentication credential.
- Rotation: create a fresh session after successful OIDC authentication and revoke the prior session in the same transaction. Generate and persist a new CSRF secret for every new session. Pending-to-normal conversion consumes the pending session and creates a fresh normal session with a fresh CSRF secret atomically.
- Revocation: logout, User disablement, rotation, and operator revocation set revoked_at. Protected requests reject revoked, expired, idle, or disabled-User sessions.
- Cleanup: expiration is enforced on every request without waiting for cleanup. A bounded CLI prune command may delete terminal normal-session rows after 30 days and pending-identity, OIDC transaction, and invitation-attempt rows 24 hours after terminal expiry/consumption. Invitations and OperatorActions are retained. No Redis, cron service, or background worker is required; operators may schedule the CLI externally. Cleanup must never be required to make expired state invalid.

### 8.2 PendingIdentitySession

Use its own independent high-entropy cookie, digest, cookie name, 32-byte recoverable CSRF secret, and ten-minute absolute expiry. Persist only the pending cookie-secret digest; persist the separate CSRF secret in recoverable form. The pending cookie is HttpOnly, SameSite=Lax, Path=/, no Domain, Secure in production. It cannot be interpreted as a normal session. On acceptance, consume the pending session, rotate to a new normal session and CSRF secret, clear the pending cookie, and set the normal cookie only after the transaction commits. Pending sessions can only retrieve their pending/claim state, retrieve their session-bound CSRF token, submit/inspect/accept an applicable invitation, and logout.

### 8.3 CSRF

OIDC login CSRF/state protection and application API CSRF protection are separate controls.

- **OIDC transaction protection:** independent unpredictable state and nonce, PKCE S256, one-time PostgreSQL transaction, and separate HttpOnly browser-binding cookie. Callback must match all transaction bindings; state alone is insufficient.
- **Cookie-authenticated API protection:** every unsafe method (POST, PUT, PATCH, DELETE) requires a session-bound CSRF token in X-CSRF-Token and an exact allowed Origin matching the configured public web origin. The top-level logout form may submit the same session-bound token in a hidden form field so the response can navigate directly to the IdP without exposing any optional ID Token hint to frontend JavaScript. Generate a 32-byte high-entropy CSRF secret for every normal or pending session and persist that separate value in recoverable form in PostgreSQL so `/api/v1/auth/csrf` can return it. This is conventional synchronizer-token storage; do not hash it and then claim it can be reconstructed, and do not add custom encryption solely to retain a hash-only CSRF property. The endpoint first authenticates the opaque session cookie, reads that session’s CSRF secret, returns it as unpadded base64url with `Cache-Control: no-store`, and does not create or rotate state. Possession of the CSRF token alone never authenticates a request. Rotate it whenever the corresponding application or pending session rotates, including pending-to-normal conversion. Require both the token and exact configured public-web Origin for every unsafe cookie-authenticated request; missing/invalid Origin or token fails closed. For invitation acceptance, compare the invitation email to the verified-email snapshot on the current session; do not rely on a client-submitted email or retain an OIDC token.
- SameSite=Lax is defense in depth, not the CSRF mechanism. GET routes do not mutate product/domain state. OIDC login initiation and callback are protocol exceptions: they create/consume only short-lived, state-bound OIDC transaction/session records and are protected by state, nonce, PKCE, browser binding, exact issuer validation, and one-time consumption. Invitation login POST also validates same-origin Origin.
- The UI may hold the CSRF token in memory only. It must not be placed in a URL, persistent browser storage, logs, analytics, or referrers. Parse only the expected unpadded-base64url encoding of exactly 32 bytes and compare the supplied value to the session-bound secret using a constant-time comparison. CSRF state is never returned without a valid normal/pending session cookie and is not an authentication credential. XSS can act as the user and is not solved by CSRF tokens.

## 9. Authorization Matrix

All checks use the caller’s current ACTIVE User and ACTIVE Membership as read from PostgreSQL for the request/operation. Frontend control visibility is convenience only; the application operation is authoritative.

| Capability | OWNER | ADMIN | MANAGER | TECHNICIAN | VIEWER |
|---|---:|---:|---:|---:|---:|
| View basic Organization metadata | Yes | Yes | Yes | Yes | Yes |
| View own Membership/profile | Yes | Yes | Yes | Yes | Yes |
| View non-removed member directory | Yes | Yes | Yes | No | No |
| View/manage Invitations | Yes | Yes | No | No | No |
| Edit basic Organization name | Yes | Yes | No | No | No |
| Invite ADMIN/MANAGER/TECHNICIAN/VIEWER | Yes | Yes | No | No | No |
| Change role of a non-Owner Membership among non-Owner roles | Yes | Yes | No | No | No |
| Suspend/reactivate/remove a non-Owner Membership | Yes | Yes | No | No | No |
| Grant/promote to OWNER | Yes | No | No | No | No |
| Demote/suspend/remove an OWNER | Yes, subject to last-Owner invariant | No | No | No | No |

Additional rules:

- An ADMIN cannot grant OWNER, mutate any Membership whose current role is OWNER, or bypass the restriction by targeting its own Membership. Admins may change, suspend, reactivate, or remove non-Owner memberships, including their own, only within the same non-Owner limits.
- OWNER operations may target another Owner and may target their own Owner Membership if another ACTIVE Owner remains after the transaction. Last-Owner protection rejects self-demotion, self-suspension, or self-removal when it would leave no active Owner.
- Membership role changes do not reactivate a SUSPENDED Membership. Reactivation is a separate operation.
- REMOVED Memberships are immutable and cannot be reactivated or role-changed.
- No invitation, API request, or Admin action can grant OWNER.
- In normal Organization APIs, only an Owner may promote a non-removed existing Membership to OWNER. A suspended Member may be promoted while remaining suspended; reactivation still requires an Owner if the resulting target role is OWNER. Operator CLI workflows may also create the initial Owner only as part of initial bootstrap or explicit Organization provisioning.
- Operators do not have a web role and cannot use these APIs. Beyond initial Owner creation during bootstrap/provisioning, the only operator role-granting exceptions are explicit successor promotion inside atomic User disablement and the purpose-specific emergency `auth recover-owner` workflow. Recovery may affect only the named Organization and exact configured OIDC identity, under the preconditions in Section 12.5; it is not a generic force-role command or an HTTP capability.

## 10. Tenant Isolation and Organization Context

Every Organization-scoped path contains an explicit UUID, for example /api/v1/organizations/{organization_id}/.... The frontend mirrors it as /organizations/{organization_id}/.... Do not store a mutable current Organization in an ApplicationSession, cookie, header-only context, or server process.

For every organization-scoped operation:

1. Resolve a valid ApplicationSession and ACTIVE User into an AuthenticatedPrincipal.
2. Parse the Organization ID from the route.
3. Load the Organization and caller’s non-removed Membership scoped by both IDs.
4. Require Membership ACTIVE.
5. Build an OrganizationContext containing the loaded Organization, Membership, and role.
6. Authorize the requested operation from that context.
7. Query or mutate all tenant-owned records with that Organization ID in the same application operation/transaction.

No operation may trust a client Organization ID without this resolution. Target Membership and Invitation lookups must include both target ID and Organization ID; never fetch an unscoped target and authorize after reading its sensitive fields. A Membership ID from another Organization is indistinguishable from a missing one. Missing and inaccessible Organization resources use the same 404 response to limit enumeration. The server enforces this for browser and future clients alike.

Use one PostgreSQL database and shared schema. No RLS is introduced. Keep tenant-sensitive operations in the Organization module/application boundary; routes do not make direct database queries or implement role checks.

## 11. Invitations

### 11.1 Creation and token handling

Only OWNER/ADMIN may create an Invitation. Validate email with one established standards-aware library (email-validator or equivalent), using the same validation and normalization function for input and verified OIDC claims. Persist the validator’s normalized form. Do not strip plus-addresses, remove Gmail dots, infer provider aliases, or use email as identity. Require a non-Owner role and set expiry to seven days.

Generate at least 256 bits of CSPRNG token entropy using an established runtime primitive. Store only a SHA-256 digest; high-entropy random tokens do not need custom password hashing. Return the raw token exactly once in a no-store create response as a same-origin invitation URL whose token is in the URL fragment, for example /invitations/accept#t=<token>. Never return it from list/detail endpoints. If the response is lost, revoke the unusable invitation and create a replacement; do not persist raw/encrypted token for later retrieval.

### 11.2 Browser handling and OIDC binding

The invite page reads the token from location.hash into transient memory, immediately removes the fragment with history.replaceState, and does not write it to localStorage, sessionStorage, cookies, analytics, telemetry, or logs. Do not load third-party scripts on invite/callback pages. Set Referrer-Policy: no-referrer on invitation and callback pages.

- Anonymous invitee: submit token in a same-origin POST body to login initiation. FastAPI validates it, stores only the Invitation ID in the short-lived OIDC transaction, and redirects to the IdP. The token never appears in query/path or proxy logs.
- Already-authenticated or pending invitee: submit the token in a same-origin POST body to create an attempt bound to this session.
- On OIDC callback, bind the transaction’s Invitation ID to an acceptance attempt for the resulting normal or pending session. The attempt records proof that a valid token was presented; raw token and digest are not copied into it.
- A user may also sign in first and then submit an invitation token from the invite link while their pending identity session is valid.

### 11.3 Inspection and atomic acceptance

Inspection returns only Organization name, invited email, offered non-Owner role, and expiry; it does not expose issuer/subject, inviter identity internals, session state, or unrelated Users.

Acceptance requires:

- A valid unconsumed attempt bound to the current normal or pending session.
- A still-PENDING, unexpired, non-revoked Invitation.
- Exact authenticated issuer/subject for the current session.
- OIDC email_verified as boolean true.
- Normalized verified email equal to the Invitation normalized email.
- No conflicting verified normalized email on another Saurorja User.
- ACTIVE User if an ExternalIdentity already maps to an existing User.
- No non-removed Membership for the same User and Organization.

The transaction locks and revalidates the Invitation and required identity/User/Organization rows; it creates or resolves the User, creates the ExternalIdentity only for the exact authenticated identity when that pair is not already linked, creates ACTIVE Membership with the invited role, consumes the Invitation and attempt, and (for pending identity) consumes pending state and creates a normal ApplicationSession. If an operator explicitly linked the pending identity to an existing User after the pending session was created, acceptance re-resolves the pair and uses that ACTIVE User; a disabled mapping is rejected. Any failure rolls back every write. Replay and concurrent acceptance can produce at most one Membership and one accepted invitation.

If the verified email belongs to a different Saurorja User while the current exact ExternalIdentity does not map to that User, return a generic identity-conflict response and make no changes. Do not auto-link, merge, or create a duplicate User. Resolution requires a trusted operator identity-link action. If email is absent, unverified, invalid, or mismatched, do not accept.

Revocation and acceptance serialize on the Invitation row. If revocation commits first, acceptance fails. If acceptance commits first, later revocation returns already-unavailable without reversing the membership. Invalid token responses are uniform and do not reveal Organization/email state.

## 12. Bootstrap and Operator Workflows

All operator commands require a configured non-email operator label and a reason. Commands use normal application validation and PostgreSQL transactions; no command prints secrets, email addresses, or OIDC subjects. The OperatorAction row commits with the state change. Operator inputs may use stable UUIDs and exact issuer/subject obtained from trusted IdP administration. Prefer hidden prompt or protected stdin for subject input so shell history/process listings do not capture it.

### 12.1 Initial bootstrap

- CLI only; no public HTTP route, bootstrap token, temporary authentication mode, or bypass.
- Preconditions: installation state absent; no User, ExternalIdentity, Organization, or Membership rows already exist; valid configured OIDC issuer; non-empty Organization name; exact issuer and subject from trusted IdP administration. Do not require operator-entered email/display name.
- One transaction: acquire a dedicated transaction-scoped PostgreSQL advisory lock or equivalent singleton serialization; inspect installation state; create Organization, ACTIVE User with initially null profile, ExternalIdentity, ACTIVE OWNER Membership, singleton installation state, and OperatorAction; commit.
- Profile attributes are populated from verified OIDC claims on a later successful login. Operator subject input proves only which IdP identity is being pre-authorized; it is not email/profile proof.
- Idempotency: an exact repeat with the same initial Organization name and exact issuer/subject succeeds as a no-op after verifying initial rows still correspond. Any different input or inconsistent/partially altered state fails with a conflict; never create a second initial Organization or Owner through a retry.
- Concurrent invocations serialize. One succeeds; the other observes exact completion (no-op) or conflicting initialization (fail). No partial state is visible.

### 12.2 Additional Organization provisioning

CLI only. Require a new name and an existing ACTIVE Saurorja User UUID with an ExternalIdentity for the currently configured issuer, so the initial Owner can authenticate. In one transaction create the Organization and that User’s initial ACTIVE OWNER Membership plus OperatorAction. Do not create a User or identity from email/issuer claims here. Require an operator-supplied operation UUID unique in OperatorAction; an exact retry is a no-op, while reuse with different inputs is a conflict. No web API can create an unrelated Organization.

### 12.3 User disable/enable

disable-user takes User UUID, required reason, and an explicit repeated successor mapping organization_id=user_id for each Organization where the target is sole ACTIVE Owner.

The command:

1. Locks the target User and supplied successor User rows in stable UUID order.
2. Finds every Organization where the target has an ACTIVE OWNER Membership. The operation is invoked for an ACTIVE target User; effective Owner counts always join User and require User status ACTIVE.
3. Locks every affected Organization row in stable UUID order, then relevant Membership rows in stable UUID order.
4. Recomputes active Owner counts and sole-Owner Organizations after acquiring locks.
5. For every Organization where the target is the sole effective active Owner, requires exactly one explicitly supplied successor. Each must be a different existing ACTIVE User with an ACTIVE Membership in that Organization. Do not infer or choose successors. Extra mappings for Organizations where the target is not the sole effective active Owner are rejected.
6. Promotes each successor Membership to OWNER; disables the target User; revokes all ApplicationSessions for the target; revokes PendingIdentitySessions whose exact issuer/subject now resolves to the target; writes one OperatorAction including the target, affected Organizations, successor map, and counts; commits atomically.

If any successor is missing, extra, duplicated, target is invalid, an Organization already has no effective active Owner, or any constraint fails, rollback the entire operation. No successor promotion or session revocation may be partial. A target who is not a sole effective active Owner needs no successor, but every affected Organization must retain at least one effective active Owner after the operation.

enable-user changes DISABLED to ACTIVE and records OperatorAction. It does not recreate or reactivate sessions, memberships, invitations, or identity links.

If no eligible successor exists, the disable command fails without mutation. Prefer restoring a currently linked Owner’s ACTIVE status/IdP access or using normal invitation and Owner-promotion workflows. If those cannot restore a usable Owner, the purpose-specific emergency `auth recover-owner` command in Section 12.5 is the supported self-hosted recovery path; it is not a general disable-user successor substitute and does not make arbitrary Memberships or role changes.

### 12.4 Controlled identity linking

Operator CLI only. Link an exact configured issuer/subject to an explicit existing User UUID after verifying identity ownership in trusted IdP administration. Require reason and confirmation. Never use email to choose the target. Existing exact mapping to the same User is idempotent; mapping to another User is a conflict and is never silently reassigned. Linking does not create a User, grant Membership, or bypass disabled status. A disabled User remains unable to authenticate.

### 12.5 Owner succession and emergency recovery

#### 12.5.1 Normal operator succession during disablement

The ordinary operator Owner-promotion exception is the successor promotion inside the atomic `disable-user` transaction above. It requires an already ACTIVE User and an already ACTIVE Membership and an explicit Organization mapping. The preparation path uses normal invitations/acceptance and Owner/Admin authorization.

#### 12.5.2 Emergency `auth recover-owner`

This is an emergency self-hosted operator recovery mechanism for an Organization that cannot regain a usable Owner through normal application workflows. It exists to avoid making direct database surgery the supported recovery procedure. The operator must explicitly attest, provide a mandatory reason, and confirm the named Organization; the database cannot independently determine whether a human Owner can authenticate at the IdP. This command does not revoke or demote any other Owner.

The command requires an explicit Organization UUID, the exact configured issuer and exact subject for the intended recovery identity, an explicit non-email operator label, a non-empty reason, and a unique operation UUID. Validate the supplied issuer byte-for-byte against the single configured issuer. Read the subject through a hidden prompt or protected stdin, not command-line arguments. Show a sanitized summary before mutation and require the operator to confirm the Organization UUID and emergency-recovery intent interactively. In non-interactive mode, refuse unless an explicit confirmation value equals the Organization UUID. Do not print or log the subject.

In one PostgreSQL transaction, resolve the exact `(issuer, subject)` pair only; never search or link by email. For an existing mapping, lock its mapped User row in shared mode and require ACTIVE status, then lock the named Organization row, then relevant Membership rows, and recheck all preconditions. If it maps to an ACTIVE User, use that User. In the named Organization, promote an existing ACTIVE non-Owner Membership; reactivate and promote a SUSPENDED Membership; or, if no non-removed Membership exists, create exactly one ACTIVE OWNER Membership. A REMOVED Membership remains terminal; preserve that row and create one new ACTIVE OWNER Membership as the narrowly scoped recovery exception to the ordinary fresh-Invitation rejoin rule. If the exact identity is genuinely unlinked, lock the Organization, insert only the minimum ACTIVE User (email/display name null), ExternalIdentity, and ACTIVE OWNER Membership required for this one Organization, and let uniqueness constraints arbitrate concurrent identity creation. No existing User row exists to pre-lock in this unlinked branch; the newly inserted row is transaction-locked. Verified OIDC claims may populate mutable profile attributes on a later successful login. A DISABLED mapped User, inconsistent or conflicting identity mapping, configured-issuer mismatch, missing Organization, or target that is already an ACTIVE OWNER on a new operation fails closed; do not enable Users, merge/link identities, or make unrelated Membership changes.

The operation must leave the Organization with at least one effective ACTIVE OWNER at commit. It creates/reactivates/promotes only the explicitly identified recovery identity in the explicitly named Organization. Do not grant OWNER through HTTP, invitations, an arbitrary User list, a generic force-role/set-role option, or platform-admin web role. If the needed identity or Membership state cannot satisfy these exact rules, fail without partial changes and use the already specified trusted identity-link/enable/normal admission workflow where applicable.

Write one `OWNER_RECOVERED` OperatorAction in the same transaction. Record the operation UUID, operator label, reason, Organization/User/ExternalIdentity/Membership UUIDs, prior and resulting Membership role/status, and effective Owner count before/after. Do not store raw issuer, subject, email, token, session value, or credential in action details. First resolve the supplied exact identity and look up the operation UUID: if a committed action exists, return its prior result as a no-op only when action type, Organization, ExternalIdentity/User IDs, operator label, and reason match exactly; reuse with different inputs is a conflict. If no action exists, acquire the required locks and recheck the operation UUID before changing state so concurrent retries converge. A unique-operation constraint race must roll back and re-read the committed action to distinguish exact replay from conflicting reuse. A new operation UUID targeting an already ACTIVE OWNER is rejected rather than treated as an additional promotion.

Concurrent recoveries lock the same Organization row and then relevant User/Membership rows following Section 13. Recheck identity mapping, Membership state, and the postcondition after locks. The exact issuer/subject unique constraint resolves races creating the same ExternalIdentity; on a uniqueness race, roll back and retry resolution in a fresh transaction or fail safely without partial rows. Same-operation concurrent retries converge on one OperatorAction and one Membership. Different operations cannot create duplicate non-removed Memberships or leave fewer than one effective ACTIVE OWNER.

## 13. Concurrency and Transaction Requirements

Cross-row Owner invariants cannot be protected by a row-local CHECK constraint. Count an effective active Owner only when both Membership status is ACTIVE and the related User status is ACTIVE. Every supported operation that may change an Owner role/status, create an Owner, remove an Owner, or disable a User must use the shared locking protocol:

- Lock all relevant User rows first where the operation changes/checks global User status, in ascending UUID order.
- Every unsafe authenticated application operation locks the principal User row in shared mode and verifies ACTIVE inside the mutation transaction before acquiring Organization locks. Operator disable obtains an exclusive lock on the target User row. Therefore an in-flight mutation commits before disable, or waits and then rejects after disable; it cannot commit an authorized write after the disable transaction.
- Lock affected Organization rows using SELECT FOR UPDATE in ascending UUID order.
- Lock affected Membership/Invitation rows in ascending UUID order after parent locks.
- Re-read all eligibility, active Owner counts, status, and target Organization scope after locks are held. Perform changes and OperatorAction in one transaction.
- Every owner-changing writer, including promotion, demotion, suspension, removal, reactivation, bootstrap, provisioning, invitation acceptance, global disablement, and emergency recovery, follows the same Organization-row serialization rule. No alternate write path may bypass it.
- `auth recover-owner` locks an existing mapped User row in shared mode before the Organization; for a genuinely unlinked identity no User row exists to pre-lock, so it locks the Organization before inserting the new User/identity/Membership and relies on the exact issuer/subject unique constraint for cross-Organization creation races. It rechecks identity/Membership state and the active-Owner postcondition inside the transaction.

Required race outcomes:

| Race | Required result |
|---|---|
| Two concurrent Owner demotion/removal/suspension attempts | Organization row serialization; at most one may remove the last active Owner. Later transaction rechecks and fails with last-owner conflict. |
| Concurrent promotion and demotion | Serialized per Organization; committed state always has an active Owner. |
| Two acceptance attempts for one Invitation | Invitation row lock and terminal state allow at most one acceptance. |
| First acceptance of same issuer/subject through different Invitations | UNIQUE exact issuer/subject prevents duplicate ExternalIdentity/User creation. The losing transaction rolls back fully and retries resolution against the now-linked ACTIVE User or returns a safe conflict; neither Invitation is consumed twice. |
| Invitation revoke vs accept | Both lock Invitation; one wins, the other observes final state and makes no partial writes. |
| Concurrent invitation creation for same Organization/normalized email | Partial unique index allows only one PENDING Invitation; loser receives invitation_conflict. Expired prior PENDING state is materialized as EXPIRED before retry. |
| Two invitations/acceptances for same User + Organization | Partial unique index prevents duplicate non-removed Membership; loser returns membership conflict and does not consume an unrelated Invitation. |
| Concurrent bootstrap | Singleton/advisory serialization plus unique state; exact second attempt is a no-op, conflicting attempt fails, no partial initial state. |
| User disable vs membership owner changes | Disable locks target/successor Users then all affected Organizations in stable order and recomputes state. Owner mutations serialize on Organization rows. |
| User disable with several sole-Owner Organizations | One transaction, sorted Organization locks, exact successor map for all, all promotions/revocation/status/action commit or all roll back. |
| Owner succession | Same Organization lock protocol; successor must be an existing ACTIVE User with an existing ACTIVE Membership at commit. |
| Concurrent `auth recover-owner` for one Organization | Organization lock serializes attempts; recheck operation UUID, exact identity, and Membership after locking. Same operation UUID/input returns one committed result; same UUID with changed inputs conflicts; different operations cannot duplicate a non-removed Membership or violate the active-Owner postcondition. |
| Concurrent recovery links for same exact issuer/subject across Organizations | UNIQUE exact issuer/subject permits one ExternalIdentity. A losing transaction rolls back and resolves again in a fresh transaction or fails safely; it never creates a duplicate User/identity link. |

Use PostgreSQL in concurrency tests. SQLite is not an acceptable substitute for validating partial indexes, row-lock serialization, and atomicity. If serialization/deadlock is detected, retry only bounded transactions that are safe to rerun; never retry a transaction after emitting a raw invitation token unless response semantics remain one-time and safe.

## 14. PostgreSQL Schema

Use native UUID, TIMESTAMPTZ, BYTEA for fixed-size digests, JSONB only for narrow operator details, foreign keys, and CHECK constraints. Text+CHECK or PostgreSQL enums may be used consistently for finite statuses/roles; Alembic must own their lifecycle. Do not create solar-domain tables.

| Table | Required schema, constraints, indexes, deletion behavior |
|---|---|
| users | PK ID; status CHECK ACTIVE/DISABLED; nullable email, normalized email, email_verified_at, display_name; created/updated/status-change timestamps. CHECK verified timestamp and normalized email are either both present or both absent. Unique partial index on normalized email where verified timestamp and normalized value are non-null. This is only a current verified-contact collision guard, never an identity key; it includes DISABLED Users. FK targets RESTRICT; no hard delete. |
| external_identities | PK ID; User FK RESTRICT; exact non-empty issuer and subject; created/last-authenticated timestamps. UNIQUE exact issuer/subject; index User ID. |
| organizations | PK ID; non-empty bounded name; created/updated timestamps. No delete endpoint. All FKs RESTRICT. |
| memberships | PK ID; Organization/User FKs RESTRICT; role/status CHECKs; created/updated and last-transition timestamps. Partial UNIQUE User/Organization where status is not REMOVED; indexes Organization/status/role and User/status. No hard deletion. Owner-count invariant is transactional. |
| invitations | PK ID; Organization FK RESTRICT; creator Membership FK RESTRICT; nullable accepted User FK RESTRICT; email and normalized email; role CHECK excluding OWNER; unique 32-byte token_hash; status CHECK; created/expiry/accepted/revoked timestamps. Partial UNIQUE Organization/normalized-email where status is PENDING; indexes Organization/status/created and expiry. Expired PENDING rows are materialized as EXPIRED before replacement. |
| application_sessions | PK ID; unique 32-byte session_token_hash; User FK RESTRICT; ExternalIdentity FK RESTRICT; required random `csrf_secret` BYTEA stored recoverably with CHECK `octet_length(csrf_secret)=32` (not hashed); auth-time normalized verified-email snapshot and verified flag; created/last-seen/absolute-expiry/revocation timestamps and reason. Index User/revoked, absolute expiry, and last seen. Do not cascade-delete User data. The authentication session secret remains hash-only. Add encrypted ID-token-hint ciphertext only if the OIDC spike proves the configured provider requires it; otherwise omit the column and do not retain an ID Token. |
| pending_identity_sessions | PK ID; unique 32-byte token digest; exact issuer/subject; nullable display name/normalized verified email; verified boolean; required random `csrf_secret` BYTEA stored recoverably with CHECK `octet_length(csrf_secret)=32` (not hashed); created/absolute-expiry/consumed/revoked timestamps. Index issuer/subject/expiry. No User FK because the identity must not create a User. The pending authentication cookie secret remains hash-only. |
| oidc_transactions | PK ID; kind CHECK LOGIN/LOGOUT; unique state digest; nullable nonce digest, PKCE verifier, browser-binding digest, expected issuer, safe return path, optional Invitation FK RESTRICT; created/expiry/consumed timestamps. CHECK constraints require login-only values for LOGIN and permit logout state without PKCE. Index expiry. PKCE verifier exists only until one-time consumption/expiry. |
| invitation_acceptance_attempts | PK ID; Invitation FK RESTRICT; nullable ApplicationSession and PendingIdentitySession FKs with ON DELETE CASCADE; CHECK exactly one session FK is non-null; created/expiry/consumed timestamps. Index Invitation/consumed and each session binding. No token/digest copy. |
| installation_state | Singleton key with CHECK value=1; initial Organization/User/ExternalIdentity FKs RESTRICT; original Organization name; initialized timestamp. Unique singleton prevents multiple initializations. No delete/reset command. |
| operator_actions | PK ID; constrained action type including `OWNER_RECOVERED`; unique operation_id; bounded non-email operator label; required reason; optional target User/Organization FKs RESTRICT; JSONB object with stable IDs/mappings and bounded prior/result state only; created timestamp. Index action type/created and target IDs. Recovery details may include ExternalIdentity and Membership UUIDs and active-Owner counts, never raw issuer/subject/email/token. Not a generic event stream. |

### Email collision semantics

- Normalize invitation email and verified OIDC email through the same configured email-validation library and normalization function.
- The normalized email unique index applies to the current verified email profile across ACTIVE and DISABLED Users.
- Email can change and is not used to resolve ExternalIdentity, membership ownership, or ordinary account login. Exact issuer/subject remains the only automatic identity match.
- If a known linked User authenticates with a new verified email that collides, do not overwrite the existing profile field or merge/link Users; authentication may continue and a privacy-safe operational event may be recorded. Invitation acceptance for that session must reject identity conflict.
- An unverified email is never used for invitation comparison or uniqueness.

## 15. Migration Plan

The implementation adds a new Alembic revision after the Module 1 empty baseline. It creates identity/admission/Organization tables, checks, indexes, and finite database types in dependency order. No legacy identity migration is needed because Module 1 has no domain tables. The migration must not create Site/Asset/solar tables.

Downgrade may remove only Module 2 tables/types in reverse FK order and is explicitly destructive to Module 2 data; document backup expectations. Application startup must not auto-create schema. The existing migration command remains the sole schema owner. Migration CI and tests run against PostgreSQL, including checks for partial indexes and transaction behavior.

## 16. API Contract

All Module 2 routes use the existing /api/v1 prefix and JSON error envelope:

~~~json
{"error":{"code":"stable_code","message":"Safe message","request_id":"..."}}
~~~

No route returns a traceback, database detail, OIDC claim set, provider token, raw cookie, issuer/subject, identity-link metadata, or unrelated account existence. Unsafe JSON endpoints require the CSRF header and exact Origin. Auth callbacks and browser redirects use no-store/referrer-safe headers.

Unless a row below specifies a redirect or a different code, successful GET and PATCH operations return HTTP 200, successful resource-creation POST operations return HTTP 201, and successful DELETE operations return HTTP 204. All failures use the stable error categories in Section 17.

### 16.1 Existing health/status

| Method and path | Auth/context | Contract |
|---|---|---|
| GET /health | Public, no Organization context | Preserve Module 1 liveness only: HTTP 200 with status healthy when the API process can serve requests. It must not test dependencies or disclose auth state. |
| GET /api/v1/status | Normal ApplicationSession and ACTIVE User; no Organization context; pending/anonymous rejected | Preserve exact Module 1 shape: {"service":"saurorja-api","status":"healthy|degraded","version":"...","environment":"...","database":{"status":"connected|disconnected"},"object_storage":{"status":"connected|disconnected"}}. Return HTTP 200 for healthy or degraded dependency status. Keep /metrics internal to backend/monitoring network and not exposed through Traefik. |

Healthy example:

~~~json
{"service":"saurorja-api","status":"healthy","version":"0.1.0","environment":"development","database":{"status":"connected"},"object_storage":{"status":"connected"}}
~~~

When a dependency is unavailable, retain the same keys, set overall status to degraded, and set only the affected dependency status to disconnected. Never return exception details or credentials.

The Module 1 status screen is intentionally replaced by the Module 2 anonymous sign-in shell. Dependency health remains available through the authenticated status endpoint; liveness remains public.

### 16.2 Authentication and admission endpoints

| Method and path | Authentication | Request and response | Expected outcomes |
|---|---|---|---|
| GET /api/v1/auth/login | Anonymous browser navigation | Optional query return_to, restricted to a safe local path; no body. | 302/303 to configured authorization endpoint; 503 authentication_unavailable if OIDC is not configured. |
| POST /api/v1/auth/login | Anonymous invite flow; exact Origin; no application session required | Form body contains token and optional safe return_to. Validate token, save Invitation ID in transaction, set transaction cookie, redirect to IdP. Do not log body. | 303 to IdP; generic 404 invitation_unavailable for invalid token; 503 if OIDC unavailable. |
| GET /api/v1/auth/callback | Anonymous, transaction/browser-bound | OIDC query callback with code/state; no request model exposes query values. | 303 to clean same-origin outcome on success; generic 400/401 on failure; no-store; clear transaction cookie. |
| GET /api/v1/auth/session | Anonymous/normal/pending | HTTP 200 with exactly one state variant: {"state":"anonymous"}; {"state":"authenticated"}; or {"state":"pending_identity","invitation_available":false}. Do not expose issuer/subject or session metadata. Fetch profile separately from /api/v1/me. | Invalid/expired cookie is cleared and yields anonymous variant. |
| GET /api/v1/auth/csrf | Valid normal or pending session cookie | Reads that session’s persisted 32-byte CSRF secret and returns {"csrf_token":"<unpadded-base64url>"}; Cache-Control: no-store. The opaque session cookie remains required and is the only authentication credential. | 200; invalid/expired/disabled-user session returns generic unauthenticated and clears invalid cookie as appropriate. No state is created or rotated by this read. Token never appears in URL/log/analytics/storage. |
| POST /api/v1/auth/logout | Normal/pending/anonymous; session-bound CSRF+exact Origin when a valid cookie session exists | Top-level form POST; hidden CSRF field for browser navigation. Revoke local state and clear cookies before optional upstream redirect. | 303 to supported IdP logout or local signed-out page; idempotent. Upstream failure never restores local session. |
| GET /api/v1/auth/logout/callback | One-time logout state, no application session | No body. Validates logout state and redirects to clean signed-out page. | 303; invalid state returns safe signed-out outcome. |
| GET /api/v1/auth/pending | PendingIdentitySession only | {"invitation":null} or minimal {"invitation":{"attempt_id":"uuid","organization_name":"...","email":"...","role":"...","expires_at":"..."}}. | 200; expired/revoked pending session rejected. |

Auth session response profile fields are only Saurorja display name and current profile email/verification status where appropriate. Do not return ExternalIdentity IDs, issuer, subject, tokens, IdP roles, or raw session state.

### 16.3 Saurorja User API

| Method and path | Authentication | Request and response | Expected outcomes |
|---|---|---|---|
| GET /api/v1/me | Normal ApplicationSession and ACTIVE User; no Organization context | No body; HTTP 200 response has User ID, nullable display_name, nullable email, and boolean email_verified. These are Saurorja profile attributes only; no issuer/subject, ExternalIdentity IDs, tokens, or session internals. | 401 unauthenticated for anonymous, pending, expired, revoked, or disabled User. |

### 16.4 Invitation acceptance endpoints

| Method and path | Authentication | Request and response | Expected outcomes |
|---|---|---|---|
| POST /api/v1/invitation-acceptance-attempts | Normal ApplicationSession or PendingIdentitySession; CSRF+Origin | Request {"token":"..."}; response {"id":"uuid","expires_at":"..."}. Token is not echoed or retained in attempt. | 201; invalid/revoked/expired token gives uniform 404 invitation_unavailable. |
| GET /api/v1/invitation-acceptance-attempts/{attempt_id} | Same bound session only | {"id":"uuid","organization":{"id":"uuid","name":"..."},"email":"...","role":"...","expires_at":"..."}. | 200; other session/missing attempt gives uniform 404. |
| POST /api/v1/invitation-acceptance-attempts/{attempt_id}/accept | Same bound normal or pending session; CSRF+Origin | Empty body or {"confirm":true}; response {"organization_id":"uuid","membership_id":"uuid","role":"..."}. Pending success also sets normal cookie and clears pending cookie. | 201; 401/403/404/409 for safe auth, CSRF, unavailable attempt, identity/membership conflict. Atomic and single use. |

### 16.5 Organization and Membership endpoints

All Organization paths require a normal ApplicationSession, ACTIVE User, active Membership, and OrganizationContext. A PendingIdentitySession is never accepted.

| Method and path | Allowed roles | Contract |
|---|---|---|
| GET /api/v1/organizations | Any ACTIVE Membership | No body; response {"items":[{"id":"uuid","name":"...","role":"..."}],"next_cursor":null}. Lists only caller’s ACTIVE memberships. |
| GET /api/v1/organizations/{organization_id} | All five roles | No body; response {"id":"uuid","name":"...","created_at":"...","updated_at":"..."}. Inaccessible and nonexistent are the same 404. |
| PATCH /api/v1/organizations/{organization_id} | OWNER, ADMIN | Request {"name":"..."}; HTTP 200 response is the Organization representation above. No other fields are accepted. |
| GET /api/v1/organizations/{organization_id}/memberships/me | All five roles | No body; response includes membership ID, user profile fields appropriate to self, role, status, and lifecycle timestamps. |
| GET /api/v1/organizations/{organization_id}/memberships | OWNER, ADMIN, MANAGER | Optional bounded limit/cursor; HTTP 200 response {"items":[{"membership_id":"uuid","display_name":"...","email":"...","role":"...","status":"..."}],"next_cursor":null}. OWNER/ADMIN may receive email; MANAGER receives display name, role, status only. No issuer/subject or User status internals. |
| PATCH /api/v1/organizations/{organization_id}/memberships/{membership_id}/role | OWNER for allowed promotion/demotion; ADMIN for non-Owner-to-non-Owner changes | Request {"role":"..."}; HTTP 200 response is a safe Membership summary. Target User must be ACTIVE for promotion to OWNER. Application locks Organization/Membership, validates actor/target/current role, and enforces active Owner invariant. |
| POST /api/v1/organizations/{organization_id}/memberships/{membership_id}/suspend | OWNER for Owner targets; OWNER/ADMIN for non-Owner targets | Empty body; HTTP 200 response is a safe Membership summary. ACTIVE → SUSPENDED. Last effective active Owner cannot be suspended. |
| POST /api/v1/organizations/{organization_id}/memberships/{membership_id}/reactivate | OWNER for Owner targets; OWNER/ADMIN for non-Owner targets | Empty body; HTTP 200 response is a safe Membership summary. SUSPENDED → ACTIVE, retaining role. Target User must be ACTIVE. REMOVED is not reactivated. |
| DELETE /api/v1/organizations/{organization_id}/memberships/{membership_id} | OWNER for Owner targets; OWNER/ADMIN for non-Owner targets | No body; soft removal to terminal REMOVED. Last effective active Owner cannot be removed. Returns 204. |

Membership target queries always include Organization ID. A target ID from another Organization returns the same 404 as a missing target.

### 16.6 Invitation administration endpoints

| Method and path | Allowed roles | Contract |
|---|---|---|
| GET /api/v1/organizations/{organization_id}/invitations | OWNER, ADMIN | Optional bounded limit/cursor; HTTP 200 response {"items":[{"id":"uuid","email":"...","role":"...","status":"...","expires_at":"..."}],"next_cursor":null}; never raw token/hash. |
| POST /api/v1/organizations/{organization_id}/invitations | OWNER, ADMIN | Body contains email and role ADMIN/MANAGER/TECHNICIAN/VIEWER. Returns 201 with ID, email, role, expiry, and one-time fragment invitation URL. Cache-Control no-store. Existing PENDING invite for normalized email returns 409 invitation_conflict. |
| POST /api/v1/organizations/{organization_id}/invitations/{invitation_id}/revoke | OWNER, ADMIN | Empty body; PENDING → REVOKED; serialized with acceptance. HTTP 200 returns safe final state; repeated revocation is idempotent. |

No endpoint creates an Organization, User directly, OWNER invitation, global disable/enable, external identity link, or operator recovery action.

## 17. Error Semantics

Continue Module 1 error envelope and request ID behavior. Messages remain safe and stable.

| Category | External behavior |
|---|---|
| Unauthenticated / invalid session / disabled User | 401 unauthenticated or invalid_session with generic message. Do not confirm User existence or disabled status. |
| Unauthorized role/action | 403 forbidden; do not describe hidden target details. |
| Organization missing or inaccessible | Uniform 404 organization_not_found. |
| Membership target missing or outside Organization | Uniform 404 membership_not_found. |
| Invalid/expired/revoked/consumed invitation token | Uniform 404 invitation_unavailable before a valid bound attempt exists. |
| Invitation changed after attempt/revoked/expired/consumed | 409 invitation_unavailable; do not expose invited person or unrelated Organization details. |
| Verified email/identity collision | 409 identity_conflict; no conflicting account metadata. |
| Duplicate pending invitation for same Organization/email | 409 invitation_conflict; no token is returned for a second pending invite. |
| Duplicate non-removed Membership | 409 membership_conflict. |
| Last active Owner would be lost | 409 last_owner_required. |
| CSRF token/Origin invalid | 403 csrf_failed. |
| OIDC callback/issuer/state/nonce/audience/token error | Generic 400/401 authentication_failed; no provider details or token claims. |
| OIDC not configured/provider unavailable | 503 authentication_unavailable; safe operational message. |
| Unexpected failure | 500 internal_error, request ID only; details stay in sanitized server logs. |

Do not provide an endpoint to test whether an arbitrary email, subject, User, or Organization exists. Valid token holders may see only their own invitation details.

## 18. Frontend Behavior

- Replace Module 1’s status-only root screen with a small anonymous Saurorja shell and sign-in action. The UI must never fake an authenticated user.
- Use the same-origin API through the current Traefik /api route. Browser requests include cookies; no OIDC token is stored in React state, localStorage, sessionStorage, IndexedDB, or URL.
- After session discovery, fetch `/api/v1/auth/csrf` for a valid normal/pending session and keep the returned CSRF token in memory only. Attach it with exact same-origin requests for unsafe methods; discard/refetch it after login, logout, or pending-to-normal session rotation. Never persist it in browser storage, URLs, analytics, or referrers.
- Callback redirects to clean paths. The shell uses the existing TanStack Query pattern for auth/session and Organization data; anonymous users see sign-in, pending users see only pending admission, and authenticated users load their permitted Organization list.
- A user with one active Organization is directed to its /organizations/{uuid} URL; a user with several sees a chooser. The selected Organization is encoded only by URL, not persisted as current session state.
- No active memberships shows a no-access state with sign-out and invitation entry points. It does not suggest that IdP login alone grants access.
- Invitation page handles fragment token immediately as specified above; offers sign-in/accept flow; displays a summary only after server validation; shows a one-time copy UI after Owner/Admin creation. Keep the raw create-response URL only in transient component memory until copied or dismissed; do not add it to query caches, route state, persistent storage, analytics, or referrer.
- Organization shell shows basic Organization name/context and role-appropriate member/invitation actions. Hiding controls is not authorization.
- Member directory, invitation list, role changes, suspend/reactivate/remove, and Organization metadata use server responses and safe errors. Destructive Owner actions may explain last-Owner rejection, but client-side prechecks are never authoritative.
- Logout posts with CSRF and navigates through optional upstream logout. The signed-out page must not assume the IdP session ended if upstream logout failed.
- No full dashboard or solar UI.

## 19. OIDC Provider Configuration and Operations

Use generic settings such as OIDC_ISSUER_URL, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, OIDC_REDIRECT_URI, OIDC_POST_LOGOUT_REDIRECT_URI, OIDC_SCOPES, PUBLIC_APP_ORIGIN, and optional upstream logout configuration. Configuration is static operator configuration, not user-selectable. For ID Tokens, OIDC_CLIENT_ID is the required audience; do not invent a separate API access-token audience because bearer access tokens are not accepted in this module. Session lifetimes and cookie security rules are fixed by this specification rather than freely weakenable environment settings.

- Production issuer, callback, public origin, and post-logout URI use HTTPS and exact registrations.
- Required scopes are openid email profile; additional scopes require a concrete use.
- For ID Tokens, the configured client ID is the required audience and must be present in aud; validate azp when required for multiple audiences. No separate resource-server access-token audience is used in Module 2 because API access tokens are not accepted or retained. Do not accept access tokens as ID Tokens.
- Require a verified email claim for invitation acceptance. The operator must configure the IdP to issue standard email and boolean email_verified; if not available, invitation acceptance fails closed.
- Configure an OIDC confidential web client with Authorization Code, PKCE S256, exact callback URI, and exact post-logout URI. Do not enable implicit flow or wildcard redirects.
- ZITADEL is the default documented provider. No ZITADEL SDK, Organization, role, or API concept enters Saurorja domain code. The base Module 1 Compose topology is not expanded with an IdP service by this specification; operators configure a self-hosted OIDC issuer separately.
- If all OIDC settings are absent in local development, the API may start for liveness and anonymous auth-shell/session calls, but login must fail closed with authentication_unavailable; no development identity bypass. Partial OIDC configuration fails validation. Production requires complete OIDC configuration and HTTPS.

## 20. Security Requirements and Threat Considerations

1. **Authorization code/token leakage:** Callback is server-only, immediately redirects to a clean path, and serves no callback HTML or third-party assets. Never log query strings, request bodies, authorization codes, state, nonce, PKCE verifier, ID/access/refresh tokens, Authorization headers, cookies, or CSRF values.
2. **Proxy/access logging:** The current application logger records route template rather than URL query, but Traefik access logging is enabled in Module 1. Before OIDC callbacks are enabled, configure proxy/Uvicorn/library logs to omit callback query values, cookie/header values, and invitation POST bodies; if this cannot be proven, disable access-path logging at the proxy. Verify emitted logs with synthetic canary values.
3. **Session fixation/theft:** Opaque high-entropy identifier, hash-only lookup, rotation after successful authentication, HttpOnly, Secure in production, Path=/, no Domain, SameSite=Lax, idle/absolute expiries, server-side revocation.
4. **OIDC issuer/key/token attacks:** Fixed configured issuer, exact discovery issuer check, signature/JWKS validation, strict audience/authorized-party/expiry/nonce/state checks, PKCE S256, one-time transaction, no untrusted issuer discovery.
5. **CSRF:** Separate login transaction binding and API session-bound CSRF plus exact Origin for unsafe cookie-authenticated requests. SameSite is defense in depth.
6. **XSS:** No tokens in JS or browser storage; HttpOnly session cookie; normal output escaping and secure headers; avoid third-party scripts on invite/callback routes. CSRF does not mitigate active XSS.
7. **Invitation theft/replay:** 256-bit token, fragment transport, immediate fragment removal, POST body only, hash-only persistent token, single-use locked acceptance, bounded expiry, revocation, session-bound claim, no copy-after-create endpoint.
8. **Email collision/account linking:** Email is mutable profile/contact; exact issuer/subject is identity; require verified boolean and common normalization; no auto-merge/link; collision leaves no partial User/Membership/invitation consumption.
9. **Tenant isolation:** Explicit path Organization ID, active Membership resolution and authorization per request, all target queries scoped by Organization, uniform not-found behavior, direct database access stays in application operations.
10. **Privilege escalation:** Role matrix in application layer, no Owner invitations, no Admin Owner operations including self-targeting, transactional last-Owner serialization, no client-only controls.
11. **Disabled User:** Check ACTIVE on every normal and pending operation; operator disables and revokes normal and identity-matching pending sessions in one transaction; preserve memberships for re-enable.
12. **Enumeration:** Generic auth and invitation errors; no public User/Organization lookup; only caller’s Organization list; member fields limited by role.
13. **Operator misuse:** CLI-only privileged commands, required operator label/reason, stable target IDs, explicit successor map, confirmation for destructive operations where practical, transactional OperatorAction. `auth recover-owner` is limited to one explicit Organization and exact configured issuer/subject after operator attestation that normal workflows cannot restore a usable Owner; it does not demote another Owner or expose arbitrary role/Membership mutation. No generic force-role command exists.
14. **Production transport/cookies:** TLS is mandatory at the public reverse proxy. Production cookies are Secure; trusted proxy headers are accepted only from configured proxy hops. Do not permit insecure-cookie mode in production. Local HTTP exception is loopback development only. The browser UI/API remain same-origin through Traefik; do not enable wildcard or credentialed cross-origin CORS for cookie authentication.
15. **Secrets/redaction:** OIDC client secret and any optional encryption key come from environment/secret injection and are never committed or logged. Use Module 1 redaction conventions for DSNs and MinIO. Do not log email, OIDC subject, auth tokens, codes, raw cookies, invitation tokens, or session secrets.
16. **Database exposure:** Session/transaction rows contain sensitive security state. Restrict database credentials, backups, and operator SQL access; protect database/volume backups. CSRF secrets are recoverable server-side values but are not authentication credentials; protect them as session-bound security state. PKCE verifier exists only briefly in server-side transaction state.
17. **Logout:** Local invalidation and cookie clearing happen before upstream navigation. IdP unavailability cannot preserve local access. Do not claim IdP logout succeeded unless confirmed by callback/provider outcome.

## 21. Observability and Operator Traceability

Continue Module 1 JSON logs with timestamp, level, service, environment, request ID, and safe message. Authentication/authorization event categories may include login started/succeeded/failed, pending identity created/expired, session revoked/expired, logout local/upstream outcome, invitation created/revoked/accepted/conflict, authorization denied, and operator action category/outcome.

Never log passwords, access/refresh/ID tokens, authorization codes, state/nonce/PKCE values, raw cookies, Authorization headers, session secrets/digests, CSRF tokens, invitation tokens/digests, OIDC subjects, or email addresses. Do not include claims or callback query strings in traces. Logs may contain request ID, route template, safe error category, duration, status class, and OperatorAction UUID. Do not include identity/Organization IDs as Prometheus labels. Metrics labels remain bounded method, route template, status class, and outcome category.

OpenTelemetry remains optional and vendor-neutral. Traces must redact auth query strings and sensitive headers/body fields before export; no collector is required for application operation. /metrics remains reachable by Prometheus over the backend network only.

operator_actions is the durable trace record for successful privileged CLI operations. It is written transactionally with the operation. CLI failures emit a sanitized category and request/operation ID; they do not write misleading success records. No generalized business audit history is added.

## 22. Testing Strategy

Test externally visible contracts at the highest practical seam. Keep deterministic unit tests for value normalization and policy decisions, API tests for authorization/response behavior, and real PostgreSQL integration tests for constraints and races. Do not use SQLite to claim PostgreSQL locking/index guarantees.

### Authentication/OIDC

- Successful linked login; unknown identity becomes pending and creates no User; exact identity mapped to a disabled User is denied rather than downgraded to pending.
- Invalid/replayed/mismatched state, browser binding, nonce, issuer, audience, azp, signature/JWKS key, expired code/token, provider error, and unsafe return path fail closed.
- JWKS key rotation refresh behavior and discovery issuer mismatch.
- Profile sync from verified claims; no email-based link; collision does not merge/overwrite; UserInfo subject mismatch rejected.
- Successful login rotates session and CSRF; failed callback preserves existing normal session; no OIDC token reaches browser storage, cookie, response body, or logs.
- `/auth/csrf` returns the current recoverably persisted CSRF value only with its valid opaque session cookie; possessing the CSRF value without that cookie does not authenticate. No-store behavior and absence from URLs/logs/analytics/persistent browser storage are verified.
- Session absolute and idle expiry, logout invalidation, User disable rejection, and upstream logout success/unsupported/failure semantics.
- Prove missing OIDC settings do not enable a fake identity and production rejects insecure/missing settings.

### Pending identity and sessions

- Pending cookie is distinct; pending cannot access /status, /me, Organization, Membership, or invitation administration routes.
- Pending only performs claim/inspect/accept/logout; pending expiry, revocation, consumption, and repeated acceptance fail.
- Pending-to-normal conversion occurs only with an accepted invitation; cookie rotation and session persistence behavior.
- Application session-secret hash-only lookup, recoverable server-side CSRF storage/retrieval, session-bound CSRF plus exact Origin, normal/pending CSRF rotation (including pending-to-normal conversion), 12-hour absolute and 2-hour idle timeout, disabled-User revocation, and prune behavior. A prior CSRF token fails after rotation; CSRF token alone without the session cookie is rejected.

### Invitation

- Creation validates role/email, normalization equality, seven-day expiry, one-time fragment link, hash-only token persistence, and no token in later GET/list response.
- Fragment is cleared before navigation/network; token is absent from request URLs, referrers, logs, and browser persistent storage; login POST carries it only in body.
- Acceptance succeeds only with token proof, active session, strictly verified matching email, and exact identity; verified mismatch/unverified/missing email fail.
- Replay, expiry, revocation, revoked-vs-acceptance race, parallel acceptance, duplicate Membership, duplicate pending invite, and lost-token replacement flow.
- Email collision with another User rejects without merge, duplicate User, partial membership, or invitation consumption.

### Tenant and authorization

- Organization A cannot read/mutate Organization B; manually changing URL UUID fails uniformly.
- Target Membership/Invitation IDs from another Organization cannot escape context.
- Every role/action in the approved matrix; Admin cannot mutate/promote/demote/suspend/remove Owner or self-promote; self-target demotion rules.
- Last Owner cannot be removed, suspended, demoted, or disabled. Concurrent attempts preserve at least one active Owner.
- SUSPENDED/REMOVED Membership behaviors and rejoin through a fresh Invitation; verify that only `auth recover-owner` may create a new Membership while preserving a terminal REMOVED row.

### Concurrency and CLI

- PostgreSQL-backed concurrent Owner mutation and promotion/demotion tests.
- Concurrent acceptance/revocation and duplicate membership tests.
- Concurrent bootstrap (same and conflicting inputs), exact idempotency, and no partial rows.
- Disable with multiple explicit successors succeeds atomically; missing/inactive/missing-membership/extra successor rolls back status, promotions, revocations, and OperatorAction.
- `auth recover-owner` requires explicit Organization, exact configured issuer/subject, operator label/reason, and confirmation; wrong issuer, missing confirmation, disabled/conflicting identity, or already-Owner new operation fails closed.
- Recovery with an existing ACTIVE User promotes/reactivates/creates only the one required Membership; a genuinely unlinked identity creates only the minimum ACTIVE User, ExternalIdentity, and OWNER Membership with null profile; removed Membership history remains terminal. Exact operation replay is a no-op; operation UUID reuse with changed inputs conflicts.
- Recovery leaves at least one effective ACTIVE OWNER, writes `OWNER_RECOVERED` transactionally without raw subject/email/token, and rolls back all User/identity/Membership/action writes on failure. Concurrent same-Organization and same-identity recovery attempts are tested on PostgreSQL.
- Operator bootstrap/provision/disable/enable/identity-link/succession/recovery tests, including action record contents and no subject/email/token in output; there is no HTTP recovery endpoint.
- Prune command is bounded, repeatable, and does not determine auth validity.

### Frontend and integration

- Test anonymous, authenticated, pending, no-access, single/multi-Organization chooser, explicit Organization URL, member directory, role-appropriate controls, invitation creation/copy-once, acceptance, and logout.
- Add a small UI test harness only as needed (for example Vitest + Testing Library); assert rendered behavior and API requests, not component internals.
- Use an in-process controlled OIDC test issuer for deterministic protocol tests, plus a live browser smoke test against a configured self-hosted OIDC provider for Definition of Done.
- Preserve Module 1 regression checks: public /health, authenticated status response shape/degraded semantics, request IDs, Prometheus scrape, migration startup, and same-origin Traefik / and /api routing.

Prior art in Module 1 includes API route/health/status/error tests, config tests, and conditional PostgreSQL integration tests. Extend those seams rather than creating a parallel test architecture.

API tests also assert the Module 1 error envelope/request ID contract for every stable Module 2 error category, safe messages, and absence of account/Organization enumeration details.

## 23. Developer Experience

Keep the existing Make targets and add clear commands for migration/check, backend tests/lint/typecheck, frontend lint/typecheck/build, local Compose up/down, OIDC configuration validation/auth smoke check, and an operator CLI with explicit subcommands such as auth bootstrap-admin, auth provision-organization, auth disable-user, auth enable-user, auth link-identity, auth recover-owner, and auth prune. Do not put identity subjects or invitation tokens in command arguments where shell history/process listings may capture them; support hidden prompt or protected stdin for those values. `auth recover-owner` additionally requires explicit Organization UUID, operator label, reason, operation UUID, and confirmation as defined in Section 12.5.

The CLI must run inside the existing API environment with a normal database URL and must not need an IdP admin API credential. It accepts identity values provided by a trusted operator. Bootstrap is never implicit; `auth recover-owner` is the only recovery command permitted to create a User and does so only with the explicit preconditions in Section 12.5. The local workflow documents that a self-hosted OIDC issuer must be configured; no cloud-managed service or IdP service is added to the base stack by this specification.

.env.example documents generic OIDC setting names with blank/non-secret placeholders, cookie mode, exact local callback/public origin expectations, and that a real self-hosted IdP is required for login. Never put a usable OIDC client secret in the example file.

## 24. Documentation and ADR Changes

Update architecture docs to describe the User/ExternalIdentity boundary, server-side sessions, OrganizationContext, application-level tenancy, operator workflows, and Module 1 status behavior change.

Add operator-facing authentication documentation covering ZITADEL as documented default and provider-neutral settings; client/callback/post-logout registration; scopes and verified-email claims; PKCE and HTTPS; local-development exceptions; bootstrap prerequisites/idempotency/no-reset behavior; Organization provisioning and User disable/enable; sole-Owner successor mapping/recovery/all-or-nothing failure; emergency `auth recover-owner` preconditions, confirmation, traceability, idempotency and no-usable-Owner attestation; invitation copy-once/replacement flow and verified-email requirement; backup/retention and bounded session pruning; local versus upstream logout semantics and provider-specific verification. Document that PostgreSQL stores a separate recoverable CSRF secret while application/pending session-cookie secrets remain hash-only.

Add focused ADRs for generic OIDC and FastAPI as relying party; PostgreSQL-backed opaque browser sessions and no browser tokens; and explicit application-level Organization tenancy without RLS in Module 2.

Do not rewrite Module 1 ADRs unless implementation reveals a direct conflict. Do not create new docs or ADR files during this specification-only phase.

## 25. Implementation Order

1. Complete and record the OIDC library/session-compatibility spike, including configured-provider discovery, audience/PKCE, cookies, logout without/with ID Token hint, and log redaction. Do not begin application integration until the spike confirms a maintained library fits the approved boundary.
2. Add typed OIDC/session configuration and fail-closed behavior; retain the local-only cookie exception and optional tracing behavior.
3. Add PostgreSQL models and Alembic migration for identity, tenancy, invitations, sessions, pending identities, transaction state, bootstrap state, acceptance attempts, and operator actions.
4. Implement OIDC transaction/session persistence, protocol validation, callback, linked identity resolution, pending identity, CSRF, local logout, and optional upstream logout.
5. Implement User/ExternalIdentity profile synchronization and operator bootstrap, provision, link, disable, enable, prune, successor, and emergency `recover-owner` workflows.
6. Implement OrganizationContext and role-checked Organization/Membership/Invitation application operations with parent-row locking, scoping, error semantics, and transaction boundaries.
7. Implement invitation fragment handling, admission shell, Organization chooser, member/invitation management, role-appropriate controls, and logout.
8. Add PostgreSQL race tests, frontend behavior tests, OIDC test issuer coverage, and live configured-provider browser verification.
9. Update README, architecture/security/operator/OIDC docs, ADRs, and Compose/Traefik only as needed for authentication routing, callback-log redaction, environment values, and same-origin cookies. Do not add public Grafana routing or any new runtime dependency solely for sessions.
10. Run migrations, backend/frontend checks, Compose and Traefik smoke checks, real OIDC login/logout, CSRF/log-redaction probes, PostgreSQL Owner-race tests, and Prometheus scrape verification before claiming completion.

## 26. OIDC and Logout Technical References

- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)
- [OAuth 2.0 Security Best Current Practice, RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html)
- [OpenID Connect RP-Initiated Logout 1.0](https://openid.net/specs/openid-connect-rpinitiated-1_0.html)
- [Authlib Starlette OAuth client documentation](https://docs.authlib.org/en/1.6.9/client/starlette.html)
- [python-email-validator](https://github.com/JoshData/python-email-validator)

The standards permit use of client_id for a post-logout redirect without id_token_hint, but an ID Token hint is recommended and a provider may impose stricter behavior. Verify the chosen provider rather than assuming. The Authlib spike must account for its documented session middleware behavior; it is not authorization to adopt cookie-stored OAuth state/credentials as the Saurorja application session.

## 27. Explicit Blockers, Risks, and Deviations

- **No architectural contradiction found** between approved Module 2 decisions and inspected Module 1 code. Module 1 has no existing authentication/domain implementation to migrate.
- **Required implementation gate, not a design blocker:** the maintained OIDC library is intentionally not selected until the required spike proves it preserves the opaque PostgreSQL session, transaction, logging, and pending-identity design.
- **Provider-specific logout remains a verification point:** default behavior stores no ID Token hint. The implementation must test the configured provider. If the provider requires a hint, use the conditional encrypted server-side field or document upstream logout unsupported; local logout remains mandatory.
- **Proxy logging is a concrete integration risk:** Module 1 currently enables Traefik access logging. Before OIDC callbacks are enabled, the implementation must verify callback query values and cookie/header/body values cannot be logged. Application route-template logging alone is insufficient proof.
- **Self-hosted IdP operations remain external to base Compose:** Module 2 needs a configured self-hosted OIDC issuer for login demonstration; this spec does not add an IdP container.
- **Emergency Owner recovery relies on a trusted-operator attestation:** software can verify the Organization, exact identity mapping, locks, Membership state, and post-commit active-Owner invariant, but cannot independently prove that no human Owner can authenticate through the IdP. `auth recover-owner` therefore requires an explicit reason and confirmation and records the action; it is limited to one named Organization and one exact configured identity. This is an intentional, narrowly scoped self-hosted recovery path, not a generic role override.
- **Intentional Module 1 behavior change:** /api/v1/status becomes authenticated to an ACTIVE Saurorja User; /health remains public liveness. The Module 1 status-only frontend is replaced by the authenticated/onboarding shell.
- **No deviation from the approved Module 2 architecture** is proposed. The session-bound InvitationAcceptanceAttempt is a narrowly scoped persistence record required to carry fragment-token proof safely across redirects without browser persistence or raw-token storage.

## 28. Definition of Done

Module 2 is complete only when all of the following are objectively verified:

- A live browser Authorization Code + PKCE login against a configured self-hosted OIDC issuer succeeds for a linked identity.
- Unknown authenticated identity creates only a restricted ten-minute PendingIdentitySession and no User; the pending identity cannot call any normal protected route.
- No OIDC access/refresh token is exposed to browser JavaScript, browser storage, application cookies, or API responses. Session cookie contents are opaque and database lookup is digest-only.
- Session rotation, 12-hour absolute expiry, 2-hour idle expiry, logout invalidation, User disablement, secure production cookie attributes, local HTTP development exception, and pruning are verified.
- Local logout always invalidates Saurorja first; upstream logout behavior is tested/documented for the configured provider, including unsupported/failing behavior and the ID Token hint decision.
- OIDC callback state, nonce, browser binding, issuer, audience/authorized-party, signature/JWKS, expiry, and replay failures are rejected.
- `/auth/csrf` returns the recoverably stored per-session CSRF value only to a valid opaque-cookie session; CSRF alone never authenticates. CSRF state rotates with normal/pending session rotation and pending conversion.
- Cookie-authenticated unsafe requests reject missing/invalid session-bound CSRF tokens and wrong/missing Origin. CSRF value is absent from URLs, logs, analytics, referrers, and persistent browser storage.
- Invitation creation returns a token once, stores only the hash, uses a fragment URL, and raw token is absent from query/access logs, referrers, analytics, and persistent browser storage.
- Verified-email invitation acceptance succeeds atomically; unverified/mismatched/colliding identity, replay, expiry, revocation, and duplicate Membership fail without partial writes.
- Cross-Organization access and manually changed Organization/target IDs fail server-side with uniform not-found behavior where appropriate.
- All five roles follow the approved matrix, including Admin restrictions and Owner-only operations.
- Last-Owner protection survives concurrent Membership mutations in PostgreSQL.
- Bootstrap is CLI-only, transactional, exact-idempotent, and conflicts safely. Organization provisioning, identity linking, User disable/enable, pruning, and Owner successor workflow are tested.
- Emergency `auth recover-owner` works only with explicit Organization UUID, exact configured issuer/subject, operator label/reason, and confirmation; it is transactional, safely idempotent, creates only the minimum recovery state, preserves active Owner and identity invariants, writes `OWNER_RECOVERED`, and has no HTTP or generic role-mutation surface.
- Global disable revokes normal and matching pending sessions. Sole-Owner disable requires explicit valid successor for every affected Organization and commits promotions, User status, revocation, and OperatorAction atomically or makes no changes.
- Alembic migrations apply cleanly against PostgreSQL and no solar-domain tables exist.
- Backend tests, lint, typecheck, frontend tests, lint, typecheck, and production build pass.
- Existing Module 1 liveness, database behavior, Prometheus scraping, Traefik web/API routing, and optional OpenTelemetry startup remain operational.
- Callback/proxy/application logs are inspected with synthetic secrets and contain no codes, states, tokens, cookies, invitation tokens, email, or OIDC subject.
- README, architecture/security/OIDC/operator docs, and ADRs match verified implementation.

Do not report an item complete based solely on unit tests where Definition of Done requires PostgreSQL, Docker Compose, proxy, or live OIDC verification.
