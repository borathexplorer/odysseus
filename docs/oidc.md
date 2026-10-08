# OpenID Connect sign-in and access policy

Odysseus supports authorization-code sign-in with PKCE, verified ID tokens,
subject-bound UserInfo, and an HttpOnly state cookie. Accounts are linked by
issuer and subject, never automatically by email. Local password accounts and
OIDC accounts can coexist. Configure MFA at the identity provider for OIDC users.

## Sign-in configuration

Set `OIDC_ENABLED=true`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, and
`OIDC_CLIENT_SECRET`. Register the exact HTTPS callback
`https://your-odysseus-host/api/auth/oidc/callback` with the provider and set
`OIDC_REDIRECT_URI` to that same URL behind a reverse proxy. Keep secrets in your
normal deployment secret configuration. Restart after changing configuration.

`OIDC_PROVIDER_NAME` optionally replaces the issuer hostname on the sign-in
button. `OIDC_PROVIDER_ICON` selects a built-in `shield` (default), `key`, or
`building` icon. Labels are rendered as text; remote image URLs are not accepted.
The page supports password-only, combined, and OIDC-only sign-in, including
provider-unavailable and retry states. Failed initial discovery is retried on
later sign-in/config requests, at most once every 30 seconds per worker.

`PASSWORD_AUTH_ENABLED` defaults to `true`. Setting it to `false` disables local
password login, first-run password setup, signup, local user creation, password
changes, and local MFA setup/changes. Native setup skips password administrator
creation. Authentication must remain enabled and OIDC credentials must be
configured; an inconsistent configuration fails application startup. Discovery
outages do not automatically re-enable passwords. Existing users, sessions, and
API tokens are preserved; this switch does not revoke them. API tokens remain
subject to their owner's current local permissions.

Before disabling passwords, verify a fresh OIDC sign-in and administrative
access through the actual HTTPS deployment. Keep access to deployment
configuration for recovery: restore `PASSWORD_AUTH_ENABLED=true` and restart
to use an existing local account if needed. This release leaves the default and
existing authentication methods enabled.

## Provider-managed roles and permissions

`OIDC_ADMIN_GROUPS` is a comma-separated list of groups granting administrator
access. `OIDC_GROUPS_CLAIM` defaults to `groups`; an exact claim key takes priority
over a dotted nested path such as `realm_access.roles`. Group values must be an
array of nonempty strings and comparisons are exact and case-sensitive.

`OIDC_PRIVILEGE_GROUPS` is a JSON object mapping supported permission names to
arrays of group names. Example `.env` configuration:

```dotenv
OIDC_ADMIN_GROUPS=odysseus-admins
OIDC_GROUPS_CLAIM=groups
OIDC_PRIVILEGE_GROUPS='{"can_use_bash":["odysseus-operators"],"can_use_research":["odysseus-researchers"],"can_use_agent":["odysseus-agents"]}'
```

Supported permissions are `can_use_agent`, `can_use_browser`, `can_use_bash`,
`can_use_documents`, `can_use_research`, `can_generate_images`, and
`can_manage_memory`. At least one matching group grants a mapped permission;
no matching group revokes it. An empty array in the configuration always denies
that permission to regular users. Unmapped permissions, message limits, and model
allowlists remain locally managed. Model administration uses the administrator
role; there is no separate model-management permission. Administrators retain
all privileges, even if a feature mapping would deny access to a regular user.

Mappings synchronize on every successful OIDC login. A valid empty groups array
revokes mapped grants, including the last administrator's mapped role. Recover
that role by restoring an administrator group at the provider and signing in.
Missing/malformed group claims refuse the new login without changing stored
roles or permissions. Unknown permission keys or malformed mapping JSON fail
startup. The admin UI marks provider-managed controls, and the API rejects local
edits to them. Local password accounts are unaffected by group mappings.

Permission changes become visible to existing sessions and other application
workers after synchronization. Provider-side changes alone are not an immediate
session revocation mechanism: this implementation does not poll the provider or
implement OIDC back-channel logout. Removing a mapping returns that setting to
local management and leaves its last synchronized value intact.

Without `OIDC_ADMIN_GROUPS`, the existing local role is retained. On an empty
installation, `OIDC_FIRST_USER_IS_ADMIN=true` (default) gives the first OIDC user
administrator access when no administrator group mapping exists. Prefer an
explicit administrator group mapping for controlled provisioning.

## Authentik

Use an OAuth2/OpenID Provider and its discovery issuer URL. Assign the scope
mappings to that provider and include their scopes in `OIDC_SCOPES` (default
`openid profile email`). Authentik's profile mapping can provide a `groups` array;
check your installed mapping, since custom mappings and older installations can
differ. A custom OAuth scope mapping can return:

```python
return {"groups": [group.name for group in request.user.groups.all()]}
```

Assign that mapping and request its scope, for example
`OIDC_SCOPES=openid profile email odysseus-access`. Make sure the claim is present
in the ID token or successful UserInfo response. Authentik's “Include claims in
id_token” controls ID-token inclusion; UserInfo must identify the same subject as
the verified ID token. Verify administrator, regular-user, individual-permission,
and group-removal cases with synthetic users before enforcing OIDC-only access.

Provider references: [Authentik OAuth2/OIDC provider](https://docs.goauthentik.io/add-secure-apps/providers/oauth2/)
and [scope/property mappings](https://docs.goauthentik.io/add-secure-apps/providers/property-mappings/).
