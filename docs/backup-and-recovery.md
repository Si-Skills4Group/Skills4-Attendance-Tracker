# Backup and recovery

Covers the three things that can go wrong — the database, the application deployment, and an accidental/incorrect data change — and how each is actually recovered given what is really configured today. Every claim below is labelled:

- **Verified** — confirmed via a live, read-only `az` CLI query against the actual resource, quoted below.
- **Recommended** — not currently configured; would improve recovery posture; not implemented this pass per the approved plan's decision to document infrastructure changes rather than apply them.
- **Manual check required** — cannot be confirmed by a read-only query; needs an actual person to perform and confirm (e.g. running a real restore).

## 1. Database (Azure Postgres Flexible Server `s4-attendance-pg-gzn5bh`)

### Verified configuration

```
az postgres flexible-server show --name s4-attendance-pg-gzn5bh \
  --resource-group Skills-4-Attendance-Tracker \
  --query "{backupRetentionDays:backup.backupRetentionDays, geoRedundantBackup:backup.geoRedundantBackup, highAvailability:highAvailability.mode, version:version, sku:sku}"
```

| Setting | Value |
|---|---|
| Backup retention | **7 days** |
| Geo-redundant backup | **Disabled** |
| High availability | **Disabled** |
| Postgres version | 16 |
| SKU | `Standard_B1ms` (Burstable tier) |

Azure Postgres Flexible Server takes automated backups continuously (full backups on a schedule plus transaction-log backups in between) for as long as the configured retention window, and supports **point-in-time restore (PITR)** to any moment within that window. This is a platform-managed feature — no application code or `bootstrap.py` change is required for it to work, and none of this phase's changes affect it.

### What this actually means in practice

- **Recovery window: any point in the last 7 days.** A restore requested today can go back to as early as 7 days ago, not further.
- **Restoring creates a brand-new server**, not an in-place rewrite of `s4-attendance-pg-gzn5bh`. The restored data lands on a new server name; cutting the application over means updating the Container App's `db-url` secret to point at the new server and redeploying/restarting — there is no "restore in place" option on Flexible Server.
- **Geo-redundant backup is disabled**, so a full regional outage of the primary Azure region would make backups (not just the live server) unavailable until the region recovers. There is currently no cross-region recovery path.
- **High availability is disabled**, so a zonal/node-level failure of the single instance is an outage, not an automatic failover — this is a separate concern from backup/restore (it affects uptime, not data durability), noted here because it's frequently conflated with backup posture.

### How to actually perform a restore (documented procedure — not yet exercised)

1. `az postgres flexible-server restore --name <new-server-name> --resource-group Skills-4-Attendance-Tracker --source-server s4-attendance-pg-gzn5bh --restore-time <ISO8601 timestamp within the last 7 days>`
2. Wait for the new server to reach `Ready` state (`az postgres flexible-server show --name <new-server-name> ...`).
3. Verify the restored data on the new server directly (connect with `psql`, spot-check row counts / a known recent record) **before** cutting the application over — do not point production traffic at an unverified restore.
4. Update the Container App's `db-url` secret (`az containerapp secret set -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --secrets db-url=<new connection string>`) and restart the active revision.
5. Confirm `/api/health/ready` reports `"database": "ok"` against the new server, then run the smoke tests in `docs/deployment-checklist.md` §12.
6. Decide what to do with the old server (`s4-attendance-pg-gzn5bh`) — do not delete it until the restored server has been running successfully for a reasonable observation period.

### Manual check required

- **A real restore has never been performed or tested against this server.** The procedure above is derived from Azure's documented restore mechanism, not from having exercised it. Before relying on this in a genuine incident, perform at least one test restore to a scratch server, confirm the data is intact and the app can connect to it, then delete the scratch server. This is the single most important unverified item in this document.

### Recommended (not implemented this pass)

- **Enable geo-redundant backup** if a full-region Azure outage is a risk the organisation wants covered — currently there is no recovery path for that scenario at all.
- **Increase backup retention beyond 7 days** if there's a business need to recover from an error discovered later than a week after it happened (e.g. an incorrect bulk data change noticed at month-end). The audit log (see §3) partially mitigates this for *auditable* changes, but not for a raw data-corruption scenario.
- **Enable high availability** (zone-redundant) if the 7-day-backup-only posture is judged insufficient for uptime requirements — this is an availability improvement, not a backup one, but is often decided alongside backup policy.

## 2. Application deployment (Container App `s4-attendance-app-gzn5bh`)

### Verified configuration

```
az containerapp show -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker \
  --query "properties.configuration.activeRevisionsMode"
```
→ `"Single"` — only one revision receives traffic at a time; there is no blue/green traffic-splitting configured.

```
az containerapp revision list -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --all --query "[].{name,active,created}" -o table
```
→ Every revision created since the app's inception (`--0000001` through the current `--0000028`, ~28 revisions spanning 2026-07-14 through 2026-07-21) is still listed. Only the current revision shows `Active: True`; every prior one shows `Active: False` — **deactivated, not deleted.** This confirms rollback by reactivating a specific prior revision is genuinely possible, not just a theoretical Container Apps feature.

```
az containerapp show -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --query identity
```
→ `{"type": "None"}` — no managed identity; secrets are plain Container App secrets (see `docs/entra-phase2.md` and the secrets-review section of the Phase 10 completion report for what's stored there), not Key Vault references.

### Recovery procedure: rollback to a prior revision

1. Identify the last known-good revision name: `az containerapp revision list -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --all --query "[].{name,active,created}" -o table`.
2. Reactivate it: `az containerapp revision activate -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --revision <revision-name>`.
3. Move traffic to it: `az containerapp ingress traffic set -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --revision-weight <revision-name>=100`.
4. Verify with the smoke tests in `docs/deployment-checklist.md` §12.

**Caveat (schema coupling):** because `bootstrap.py` runs on every container boot and this codebase has no separate migration step (see `docs/deployment-checklist.md` §10), a schema change always ships in the same image as the code that depends on it. Rolling the *application* back to a prior revision does **not** roll back the database schema — the old code simply runs against whatever schema state the newest revision already applied. This is safe as long as every schema change is additive (the established, enforced convention — see the deployment checklist's migration-review step), because old code ignorant of a new column/index/constraint continues to work unaffected. It would **not** be safe to roll back application code after a genuinely breaking schema change; the checklist's migration-review step exists specifically to make sure that scenario never ships in the first place.

**Revision retention is not permanent** — Azure Container Apps has its own internal revision garbage-collection behavior for very old, long-inactive revisions on some plans. The ~28 revisions currently retained cover this application's entire history to date, but this should not be treated as an unlimited archive; the container registry (`s4attgzn5bhacr`) holding the actual image tags is the durable source of truth for "what code was running when" over the long term.

## 3. Accidental or incorrect data changes (not a server failure)

For a mistake that isn't a server crash — an admin accidentally clears a register, a bulk role change goes wrong, a CSV import is confirmed with the wrong mapping — the recovery path is different from both of the above:

- **The audit log (`audit_logs` table, viewable at `/audit-log`) is the primary tool.** Every mutating action covered by Phase 10's audit-completeness work (see the completion report) records the actor, timestamp, previous value, and new value. For most single-record mistakes, the fix is: look up the relevant audit entries, read the `previousValue`, and manually re-apply it through the normal application UI/API (not a direct DB write) so the correction itself is also audited.
- **For a mistake that's too large or too old for manual audit-log-guided correction** (e.g. it predates the audit trail for that action, or affects too many rows to fix by hand), point-in-time restore (§1) is the fallback — but recall the 7-day window and the fact that it requires standing up a new server and manually reconciling any legitimate changes made *after* the mistake but *before* the restore point. This is a heavy, manual, last-resort operation, not a routine one.
- **Audit records are never automatically purged.** See `docs/requirements-traceability.md`'s retention section for the explicit no-auto-delete decision — this means the audit trail described above remains available indefinitely under current configuration, which is deliberately more generous than the 7-day database backup window for exactly this "figure out what happened and fix it by hand" use case.

## 4. Summary table

| Recovery scenario | Mechanism | Status |
|---|---|---|
| DB server/data loss, within 7 days | Point-in-time restore to a new server | Verified configured; restore procedure **not** test-exercised |
| DB server/data loss, region-wide outage | None currently | Recommended: enable geo-redundant backup |
| Bad application deploy | Reactivate + re-target traffic to a prior Container App revision | Verified possible (revisions retained); routine, low-risk |
| Bad deploy that included non-additive schema change | Not safely reversible by revision rollback alone | Prevented upstream by the deployment checklist's migration-review gate, not a recovery-time control |
| Single incorrect data change (admin/user error) | Audit log lookup + manual correction through the app | Verified available (audit log persisted, no auto-purge) |
| Widespread/old incorrect data change | Point-in-time restore + manual reconciliation | Same caveats as row 1; manual and heavy |
