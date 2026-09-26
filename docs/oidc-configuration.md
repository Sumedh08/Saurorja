# OIDC provider configuration

Saurorja is a generic OpenID Connect relying party. FastAPI owns the Authorization Code flow, PKCE, callback validation, and Saurorja sessions. ZITADEL is the documented default self-hosted provider; no provider SDK or provider organization model is used by the application.

## Provider client

Create a confidential web client with these properties:

- Authorization Code flow enabled; PKCE with S256 required.
- Client secret configured only on the Saurorja server.
- No implicit flow and no wildcard redirect URIs.
- Exact redirect URI: `{PUBLIC_APP_ORIGIN}{API_PREFIX}/auth/callback`.
- Exact post-logout redirect URI: `{PUBLIC_APP_ORIGIN}{API_PREFIX}/auth/logout/callback`.
- Required scopes: `openid email profile`.
- The ID Token audience includes the configured client ID.
- Invitation acceptance requires `email` and the JSON boolean claim `email_verified: true`.
- Discovery metadata issuer must exactly match `OIDC_ISSUER_URL`; discovery and JWKS must be available to the API.

Configure the following generic settings in `.env`:

```dotenv
APP_ENV=development
PUBLIC_APP_ORIGIN=http://localhost
API_PREFIX=/api/v1
OIDC_ISSUER_URL=https://identity.example.org
OIDC_CLIENT_ID=saurorja-web
OIDC_CLIENT_SECRET=<server-side-client-secret>
OIDC_REDIRECT_URI=http://localhost/api/v1/auth/callback
OIDC_POST_LOGOUT_REDIRECT_URI=http://localhost/api/v1/auth/logout/callback
OIDC_SCOPES=openid email profile
```

Use an issuer hostname that the API container can resolve and that the browser can reach. The issuer value is compared exactly with discovery and token claims; do not put an internal-only hostname in discovery if the browser cannot navigate to the provider. Loopback HTTP is accepted only for local development. For public deployment, use HTTPS for the public origin, issuer, callback, and logout URI, and terminate TLS at the public reverse proxy. The base Compose entrypoint is HTTP for loopback development and does not provision certificates.

After changing OIDC configuration, recreate the API so the static configuration is reloaded:

```shell
docker compose up -d --build api web
```

If OIDC settings are absent in development, the application still serves liveness and the anonymous shell, but login returns a safe `authentication_unavailable` response. Partial configuration and insecure production URLs fail startup validation. There is no development identity bypass.

## Browser and logout behavior

The browser is redirected to the configured issuer. Authorization code, tokens, PKCE verifier, and OIDC claims stay server-side. The browser receives only an opaque HttpOnly Saurorja session cookie. Local logout invalidates that session and clears the cookie before Saurorja attempts RP-Initiated Logout. If the provider has no logout endpoint or the upstream request fails, the local session remains terminated and the user reaches the signed-out page. Saurorja does not retain refresh tokens or an ID Token hint by default.

The API requires the issuer to be reachable from the container for discovery, token exchange, JWKS, and optional UserInfo. If email is absent from a signed ID Token and an invitation requires it, the API may request UserInfo and checks that its `sub` exactly matches the ID Token subject.

## Provider replacement

The application stores external identity as the exact `(issuer, subject)` pair and does not use provider organizations or roles. Replacing an issuer therefore requires a controlled operator identity-link/migration procedure for each affected Saurorja User; matching email addresses never link accounts automatically.
