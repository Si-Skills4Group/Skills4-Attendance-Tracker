# Production deployment checklist

For deploying `main` to the live Container App (`s4-attendance-app-gzn5bh`, resource group `Skills-4-Attendance-Tracker`). Follow in order. This checklist itself does not deploy anything — every phase in this repo's history has followed "implement, test, report, deploy only on separate explicit instruction," and that stands.

## 1. Branch and commit verification

- [ ] Confirm you're deploying from `main`, and that `git status` is clean (no uncommitted changes you didn't mean to ship).
- [ ] `git log origin/main..HEAD` should be empty (nothing local and unpushed) — push first if not.
- [ ] Read the diff since the last deploy (`git diff <last-deployed-sha>..HEAD --stat`) and sanity-check it matches what you intend to ship.

## 2. Test status

- [ ] Backend: `pytest` full suite green (525+ tests as of Phase 10; run from `artifacts/api-server` with `TEST_DATABASE_URL` set — see README.md).
- [ ] Frontend: `tsc --noEmit`, `vitest run`, `vite build` all green (via the win32 workaround cycle in `pnpm-workspace.yaml` if building on Windows — see README.md's Gotchas section; revert the workaround afterwards).
- [ ] No test was skipped or marked `xfail` to make this pass.

## 3. Migration review

There is no Alembic (or any other migration tool) in this codebase — schema lives entirely in `artifacts/api-server/pyapp/bootstrap.py` as one idempotent DDL string, re-run in full on every app boot (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and — as of Phase 10 — a `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` pair for CHECK constraints, since Postgres has no `ADD CONSTRAINT IF NOT EXISTS`).

- [ ] Diff `bootstrap.py` since the last deploy. Confirm every change is additive (new `ADD COLUMN`/`CREATE INDEX`/`CREATE TABLE`, never a `DROP COLUMN`/`DROP TABLE`/destructive `ALTER`).
- [ ] If a new CHECK constraint or unique index was added, confirm (via a read-only query against production, see `test_db_integrity.py` for the shape) that no existing row would violate it — bootstrap runs the `ALTER TABLE ... ADD CONSTRAINT` unconditionally on boot, and Postgres validates existing rows at add-time. A violation here means **the app fails to start**.
- [ ] If `lib/db/src/schema` (the Drizzle TypeScript schema, the source-of-truth for table shape per the README) changed, confirm `bootstrap.py`'s DDL was updated to match by hand — there is no automated link between the two.

## 4. Database backup verification

- [ ] Confirm today's automated backup exists and is recent: `az postgres flexible-server show --name s4-attendance-pg-gzn5bh --resource-group Skills-4-Attendance-Tracker` — see `docs/backup-and-recovery.md` for what "recent" means given the current 7-day retention window.
- [ ] For a migration that adds a constraint/index (item 3), a fresh manual restore-point checkpoint is not required — Postgres Flexible Server's continuous backup already covers point-in-time restore back through the retention window.

## 5. Environment-variable verification

Compare the Container App's current env/secrets against `docs/entra-phase2.md`'s "Backend Runtime Variables" list and this repo's `.env.example`-equivalent (there isn't one checked in — cross-check against `artifacts/api-server/.env`'s keys, values excluded):

- [ ] `AUTH_MODE=entra` (production must never run `local`; `validate_startup()` enforces this at boot, but confirm the env var is actually set correctly regardless).
- [ ] `ENVIRONMENT=production` (drives `is_production`, which gates the CORS/`AUTH_MODE` checks and the structured-logging `environment` field).
- [ ] `ENTRA_TENANT_ID`, `ENTRA_ALLOWED_TENANT_ID`, `ENTRA_API_CLIENT_ID`, `ENTRA_EXPECTED_AUDIENCE`, `ENTRA_AUTHORITY`, `ENTRA_REQUIRED_SCOPE` all present — see item 6.
- [ ] `DATABASE_URL` points at the `attendance` database (never `attendance_test`), with the current, non-expired `s4admin` password.
- [ ] `ALLOWED_ORIGINS` matches the deployed frontend origin(s) exactly, comma-separated, no `*`.
- [ ] `ADMIN_EMAIL`/`ADMIN_ENTRA_OBJECT_ID`/`ADMIN_ENTRA_TENANT_ID` — only relevant on a from-scratch bootstrap; confirm these are NOT accidentally set to seed an unintended admin on a deploy to an already-provisioned environment (bootstrap re-runs on every boot, but `ON CONFLICT (email) DO NOTHING` makes re-seeding a no-op once the row exists — still worth a glance if these were recently touched).

## 6. Entra settings

- [ ] SPA app registration's redirect URIs include the exact deployed frontend URL (`https://s4-attendance-app-gzn5bh.thankfuldesert-ce8f3462.uksouth.azurecontainerapps.io/` as of this writing — re-check if the FQDN ever changes, e.g. a custom domain).
- [ ] API app registration's `access_as_user` scope still has admin consent granted tenant-wide.
- [ ] If either app registration changed, allow a few minutes for Entra's own metadata/JWKS caching before testing sign-in.

## 7. CORS

- [ ] `ALLOWED_ORIGINS` (item 5) is the single source of truth — re-confirm it matches the frontend's actual origin, not a stale one from an earlier custom-domain attempt.

## 8. Container image tag

- [ ] Build and tag with a descriptive, immutable tag (e.g. `phase-10-security-hardening`), **not** only `:latest` — `:latest` is fine as a convenience alias pushed alongside, but the Container App update itself should reference the specific tag so a later `az containerapp revision list` shows exactly what's running, and so a rollback (item 13) can target a known-good tag precisely.
- [ ] Confirm the build used the exact same `VITE_ENTRA_*`/`VITE_API_*` build-args as the previous production build (see `docs/entra-phase2.md`) — extract them from the currently-running bundle if unsure (`grep` the built JS for `VITE_ENTRA_` — see this phase's own deploy history for the exact command).

## 9. Infrastructure what-if

There is no infrastructure-as-code in this repo (Container App, ACR, and Postgres Flexible Server were all provisioned by hand via `az` CLI/Portal — see `docs/azure-configuration.md`). There is nothing to run `az deployment group what-if` against. If IaC is introduced later, this step becomes mandatory before every apply.

## 10. Migration execution

Bootstrap runs automatically as part of application startup (`bootstrap_database()` in `pyapp/main.py`, called before the FastAPI app object is even constructed) — there is no separate migration-execution step. The new Container App revision applies the new schema the moment it starts.

- [ ] Because of this, a schema change and a code change **always ship together** in the same image — there is no way to run a migration ahead of the code that depends on it. Plan constraint/index additions accordingly (see item 3's rowcheck).

## 11. Backend deployment

```
az acr build --registry s4attgzn5bhacr --image skills4attendance:<tag> --image skills4attendance:latest \
  --build-arg VITE_ENTRA_CLIENT_ID=... [... see item 8] \
  https://<token>@github.com/Si-Skills4Group/Skills4-Attendance-Tracker.git#main
az containerapp update -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker \
  --image s4attgzn5bhacr.azurecr.io/skills4attendance:<tag>
```
(The frontend is built as part of the same multi-stage Docker image — see `Dockerfile` — there is no separate frontend deployment step.)

- [ ] On Windows, building from a local source directory hits a path-length limit walking `node_modules`'s pnpm store; use the remote GitHub-context form above instead of a local path.

## 12. Smoke tests

Immediately after the new revision goes live:

- [ ] `GET /api/health/live` → `{"status": "ok"}`.
- [ ] `GET /api/health/ready` → `{"status": "ok", "checks": {"database": "ok", ...}}`.
- [ ] `GET /openapi.json` (or `/api/openapi.json`) lists the expected endpoint count — a sudden drop signals a broken import somewhere.
- [ ] Sign in as an existing admin and tutor through the real UI (not just curl) — confirms Entra end-to-end, not just that the process boots.
- [ ] Load a cohort's attendance register, save a draft, confirm the page updates without a manual refresh (regression-prone area — see the Phase 9.5 fix history).
- [ ] Open `/audit-log` as admin, confirm entries load and a tutor session gets "Not authorized" instead of an error.

## 13. Rollback decision points

Revisions are **not** deleted on update (`activeRevisionsMode: Single` just means only one gets live traffic at a time) — confirmed via `az containerapp revision list -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --all`, which as of this writing still shows every revision back to the app's creation.

- [ ] If a smoke test fails: `az containerapp revision activate --revision <previous-good-revision>` then `az containerapp ingress traffic set --revision-weight <previous-good-revision>=100` to send traffic back immediately, buying time to fix forward.
- [ ] **Caveat**: if the failed deploy included an additive schema change (item 3), rolling the *application* back does not undo the schema change — the old code simply ignores the new column/index/constraint, which is safe by construction (nothing in `bootstrap.py`'s additive style requires the new column to be populated). A rollback is not safe if the failed deploy included a genuinely breaking (non-additive) DDL change — which item 3 exists to prevent from ever being deployed in the first place.
- [ ] If the failure is data-related rather than code-related, see `docs/backup-and-recovery.md`'s point-in-time restore section instead of an application rollback.

## 14. Post-deployment monitoring

- [ ] Watch `az containerapp logs show -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --follow` for the first few minutes — every request now logs one structured JSON line (`pyapp/logging_config.py`) with route/status/duration; watch for an unexpected spike in `statusCode >= 500` or `outcome: error`.
- [ ] Check `/api/audit-log` (or query `audit_logs` directly) for any `rate_limited`/`authorization_denied` spike immediately post-deploy — could indicate a client misconfiguration (e.g. a frontend build pointing at the wrong API base URL, causing repeated 401/403 retries).
- [ ] See `docs/runbook.md` for what to do about a spike in any specific status-code family.
