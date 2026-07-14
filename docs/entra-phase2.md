# Skills4Attendance Phase 2 Entra Authentication

## Architecture

Skills4Attendance uses application-managed Microsoft Entra authentication.

- The React SPA signs users in with MSAL using the authorization-code flow with PKCE.
- The SPA requests an access token for the Skills4Attendance API scope.
- FastAPI validates the bearer access token against the tenant OpenID metadata and JWKS.
- FastAPI maps the token `tid` and `oid` claims to a local `users` row.
- The local database remains authoritative for active status, role, tutor link, and record-level permissions.

Do not enable a separate Container Apps login experience unless the trust boundary is reviewed and direct backend access is restricted.

## App Registrations

Create two single-tenant app registrations in the Skills4Group tenant.

### Skills4Attendance API

- Supported account type: this organisation only.
- Application ID URI: `api://<API_CLIENT_ID>`.
- Expose delegated scope: `access_as_user`.
- Admin consent: grant the SPA permission to this delegated scope.
- Optional app roles may mirror `Administrator` and `Tutor`, but application roles in the database remain authoritative.

### Skills4Attendance SPA

- Supported account type: this organisation only.
- Platform: Single-page application.
- Redirect URIs:
  - `http://localhost:<port>/`
  - Azure development URL
  - UAT URL, where applicable
  - Production URL
- Logout redirect URIs should match the approved app URLs.
- No client secret.
- API permission: delegated `access_as_user` for the Skills4Attendance API.

## Frontend Build Variables

Set these at build time:

- `VITE_ENTRA_CLIENT_ID`
- `VITE_ENTRA_TENANT_ID`
- `VITE_ENTRA_AUTHORITY`
- `VITE_ENTRA_REDIRECT_URI`
- `VITE_ENTRA_POST_LOGOUT_REDIRECT_URI`
- `VITE_API_SCOPE`
- `VITE_API_BASE_URL`

Example API scope:

```text
api://<API_CLIENT_ID>/access_as_user
```

## Backend Runtime Variables

Set these on the Container App:

- `AUTH_MODE=entra`
- `ENVIRONMENT=production`
- `ENTRA_TENANT_ID`
- `ENTRA_API_CLIENT_ID`
- `ENTRA_EXPECTED_AUDIENCE`
- `ENTRA_AUTHORITY`
- `ENTRA_REQUIRED_SCOPE=access_as_user`
- `ENTRA_ALLOWED_TENANT_ID`
- `ALLOWED_ORIGINS`
- `DATABASE_URL`

`ALLOWED_ORIGINS` is a comma-separated allow list. Do not use `*` in production.

## Initial Administrator Mapping

Do not create the first production administrator from email alone.

Recommended process:

1. Ask the intended administrator to sign in once.
2. FastAPI will deny access and audit `entra_user_not_provisioned` with object ID, tenant ID, email, and display name.
3. Confirm the object ID and tenant ID with the administrator through an approved channel.
4. Provision that identity as `admin` using the administrator user-management endpoint or a reviewed one-off database change.

The bootstrap supports `ADMIN_ENTRA_OBJECT_ID` and `ADMIN_ENTRA_TENANT_ID` for controlled initial provisioning, but those values should come from secure environment configuration.

## Local Development

Preferred local development uses the real tenant and localhost redirect URI.

`AUTH_MODE=local` remains available only outside production for rollback and development. Production startup fails if local auth is enabled.

## Rollback

- The schema changes are additive.
- Existing users, tutors, learners, attendance, allocations, and audit logs are preserved.
- If Entra rollout fails before migration cleanup, deploy the previous container image and keep the added columns unused.
- Do not drop `password_hash` or old session infrastructure until the production Entra cutover is verified.
