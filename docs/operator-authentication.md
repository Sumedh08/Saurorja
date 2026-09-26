# Operator authentication and Organization workflows

Module 2 privileged actions run on the trusted self-hosted host through `app.cli`. They are not HTTP endpoints and are not available to an Organization role. Start the Compose stack first; use an interactive terminal for commands that prompt for an exact OIDC subject.

```shell
docker compose exec -it api python -m app.cli auth --help
```

Every mutating command requires an operator label, a non-empty reason, and a unique operation UUID. Keep the UUID to retry an uncertain command safely. The operation and its `OperatorAction` record commit together. Do not put OIDC subjects, invitation tokens, cookies, or session values in command arguments.

## Initial installation

After configuring the OIDC provider, bootstrap only an uninitialized installation:

```shell
docker compose exec -it api python -m app.cli auth bootstrap-admin \
  --organization-name "Example Operations" \
  --issuer "https://identity.example.org" \
  --operator "installation operator" \
  --reason "Initial self-hosted installation" \
  --operation-id "<new-unique-uuid>"
```

The exact subject is requested with a hidden terminal prompt. Supply the issuer and subject obtained from the trusted identity-provider administration interface. The command creates one Organization, User, ExternalIdentity, and OWNER Membership in a single transaction. It does not authenticate the identity or create a session. Repeating the exact completed bootstrap is idempotent; conflicting repeat inputs fail.

## Provision another Organization

Only the host operator can create an unrelated Organization in Module 2. The selected Owner must be an existing ACTIVE Saurorja User with an identity linked to the configured issuer:

```shell
docker compose exec -it api python -m app.cli auth provision-organization \
  --name "Operations West" \
  --user-id "<active-user-uuid>" \
  --operator "installation operator" \
  --reason "Provision approved operator customer" \
  --operation-id "<new-unique-uuid>"
```

Organization Owners and Admins cannot create unrelated Organizations.

## User status and identity linking

Global User disablement and re-enablement are host-operator actions. Disabling a User revokes all of the User's application sessions and identity-matching pending sessions while preserving Organizations, Memberships, and identity links. Re-enabling does not recreate sessions or Memberships.

If the User is the sole ACTIVE Owner of any Organization, provide one explicit active Membership successor for each such Organization:

```shell
docker compose exec -it api python -m app.cli auth disable-user \
  --user-id "<user-uuid>" \
  --successor "<organization-uuid>=<active-successor-user-uuid>" \
  --operator "installation operator" \
  --reason "Disable access following operator review" \
  --operation-id "<new-unique-uuid>" \
  --confirm-user "<user-uuid>"
```

Repeat `--successor` for each affected Organization. The transaction validates the full successor map, promotes those existing Memberships, disables the target, revokes sessions, and records the action atomically. Missing, extra, inactive, or out-of-Organization successors abort the whole operation.

```shell
docker compose exec -it api python -m app.cli auth enable-user \
  --user-id "<user-uuid>" \
  --operator "installation operator" \
  --reason "Restore operator-approved account access" \
  --operation-id "<new-unique-uuid>"
```

`link-identity` explicitly associates an exact configured issuer and hidden-prompt subject with an existing User. It never uses email matching. Use it only for a trusted provider migration or identity correction after verifying both records through the IdP administration interface.

## Emergency Owner recovery

`auth recover-owner` is an emergency, one-Organization recovery mechanism. It is rejected while an ACTIVE Owner exists, because ordinary Organization administration remains available in that state. It cannot demote another Owner, accept email as identity proof, create arbitrary Memberships, or mutate arbitrary roles.

```shell
docker compose exec -it api python -m app.cli auth recover-owner \
  --organization-id "<organization-uuid>" \
  --issuer "https://identity.example.org" \
  --operator "installation operator" \
  --reason "No active Owner can restore Organization administration" \
  --operation-id "<new-unique-uuid>" \
  --confirm-organization "<organization-uuid>"
```

The exact subject is requested through a hidden prompt. A mapped identity must belong to an ACTIVE User. If unlinked, the command may create only the minimum User, ExternalIdentity, and OWNER Membership for that exact identity and Organization; email and display name remain unset until verified OIDC claims arrive. Conflicting or disabled mappings fail closed. Organization locking, one transaction, and an `OWNER_RECOVERED` operator record preserve the active-Owner invariant and provide traceability. If a suitable active identity cannot be established at the IdP, recover that identity with the provider first.

## Bounded cleanup

Expired/terminal session and OIDC transaction rows do not grant access. Cleanup is operator-triggered and bounded; no background queue or service is required:

```shell
docker compose exec api python -m app.cli auth prune --limit 1000
```

Pruning does not delete Users, Organizations, Memberships, Invitations, or operator records.
