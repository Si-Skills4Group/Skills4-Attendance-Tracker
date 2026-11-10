# Operational runbook

Diagnostic steps for specific failure scenarios in Skills4Attendance, using the tools actually available in this deployment: `az containerapp logs`, the structured JSON request logs (`pyapp/logging_config.py`), the `/api/health/live` and `/api/health/ready` endpoints, the audit viewer (`/audit-log`), and the `audit_logs`/`rate_limit_attempts` tables.

**Useful commands referenced throughout:**
```
az containerapp logs show -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --follow
az containerapp logs show -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --tail 200
curl https://<deployed-fqdn>/api/health/live
curl https://<deployed-fqdn>/api/health/ready
```
Every log line is one JSON object with `timestamp`, `level`, `service`, `environment`, `correlationId`, `route`, `method`, `statusCode`, `durationMs`, `userId`, `outcome` — filter/search on these fields (e.g. `az containerapp logs show ... | grep '"statusCode":500'`, or pipe through `jq` if available).

---

## 1. A user can't sign in

- Confirm it's not everyone: check `az containerapp logs show ... --tail 200` for a spike in `route: "/api/auth/*"` with non-2xx status around the reported time. If it's isolated to one user, it's almost certainly an Entra-side account issue (disabled account, MFA challenge failure, conditional access policy), not an application bug.
- Check the audit log for that user's email/object ID: a `login_failed` entry (added in Phase 10) with `reason: "invalid_credentials"` or `"missing_credentials"` in `newValue` confirms the app rejected a real attempt rather than the request never arriving.
- If entries show a 401 from the API on a request that includes a `Authorization: Bearer` header (visible in the frontend network tab, not in logs — tokens are never logged), the token itself may be failing one of `entra.py`'s checks (wrong tenant, wrong audience, expired). Ask the user to sign out fully and back in — a stale cached token is the most common cause.
- If the whole tenant is affected, check Entra's own service health status (Azure Portal → Entra ID → this is outside the application's control).
- **"Sign-in could not start", or a blank screen stuck after returning from Entra**: a sign-in redirect that didn't finish cleanly (closed tab, back button, dropped connection) used to leave a stale `sessionStorage` interaction flag (any key ending `.interaction.status`) behind — msal-react doesn't clear it on a failed `handleRedirectPromise`, so every later attempt in that tab failed immediately. This is now handled automatically (`artifacts/skills4attendance/src/auth/msal.ts`): a `LOGIN_FAILURE`/`ACQUIRE_TOKEN_FAILURE` listener clears the flag proactively, and the login button itself retries once before giving up. If a user still reports this, it's almost certainly a browser/tab old enough to predate this fix — a full page refresh (not just retrying the button) clears the stale flag manually.

## 2. User is authenticated but "not provisioned" / no account in the app

- This means Entra accepted the sign-in but no matching row exists in `users` for that Entra identity.
- Check the audit log filtered by `entityType: user` around the relevant time for a `create` action — if an admin was expected to provision this user and didn't, that's the fix (Users page, as an admin).
- Confirm the user's Entra email/object ID matches exactly what's stored (case, typos) — provisioning matches on stable Entra identity, not display name.

## 3. A tutor reports they can't see a cohort/learner they expect to

- First confirm expectation, not a bug: query the `cohort_tutor_allocations` (or equivalent allocation table — check current schema in `bootstrap.py`) for that tutor+cohort. If there's no active allocation, this is working as designed (`require_cohort_access` correctly denies unallocated tutors) — the fix is allocating the tutor via Allocation, not a code change.
- If an allocation exists but access still fails, check the audit log for an `authorization_denied` entry for that tutor/cohort pair around the reported time (Phase 10 added audit coverage for this exact object-level-403 path) — the `newValue` will show which access-check helper (`require_cohort_access`, etc.) rejected it and why.
- Check whether the allocation is soft-deleted or scheduled-but-not-yet-applied (`scheduled_allocations.status`) rather than currently active.
- **A Tutor self-service action getting a 403 may be correct, not a bug**: a Tutor can rename their own cohort (`PATCH /cohorts/{id}` accepts a Tutor caller but rejects, 403, any field other than `name`) and cancel a session in their own cohort (`POST /attendance/sessions/{id}/cancel` is scoped by `require_attendance_access`, not admin-only) — but every other cohort field (schedule, tutor assignment, the `active` flag) and every other session action remain Administrator-only. Confirm which case you're looking at before treating a 403 as a regression.

## 3a. A Tutor has flagged a cohort inactive — it's not actually going inactive

A Tutor flagging their own cohort inactive (`POST /cohorts/{id}/flag-inactive`, reason required) never changes `cohorts.active` directly — it only sets `inactive_flagged_at`/`inactive_flagged_by`, surfaced to Administrators as a "Cohorts Flagged Inactive" card on the admin dashboard (`_cohorts_flagged_inactive` in `pyapp/routers/dashboard.py`). This is by design: the cohort stays fully active (sessions still expected, still appears everywhere) until an Administrator explicitly actions the flag:

- `POST /cohorts/{id}/confirm-inactive-flag` (admin-only) — sets `active = false` and clears the flag in one step.
- `POST /cohorts/{id}/reject-inactive-flag` (admin-only, reason required) — clears the flag, cohort stays active.
- `POST /cohorts/{id}/unflag-inactive` — the flagging Tutor (or an admin) can retract it themselves before either happens.

If a cohort looks "stuck" showing as flagged with sessions still running as normal, that's the expected pending state, not a bug — check the dashboard card or the cohort's own detail page for who flagged it and when. A direct admin `activate`/`deactivate` call also clears any stale flag unconditionally, so a flag can never survive an admin bypassing this workflow entirely.

## 4. Attendance register won't load

- Check `/api/health/ready` — if `checks.database != "ok"`, this is scenario 10 (DB unavailable), not a register-specific bug.
- Check the structured logs for the specific `GET /api/attendance/sessions/{id}/register`-shaped request; a slow response (`durationMs` above the 2000ms threshold logged in `logging_config.py`'s `SLOW_THRESHOLDS_SECONDS`) suggests a performance issue rather than a hard failure — check cohort size and whether this coincides with a broader slowdown (scenario 13).
- A 404 means the session ID doesn't exist or was soft-deleted — confirm via the audit log whether a `delete` action was recorded for that `attendance_session` entity recently.
- A 403 is an allocation/ownership issue — see scenario 3.

## 5. Bulk register save fails partway / reports an error

- Look up the correlation ID shown in the frontend's error toast (Phase 10 added this — any 500-class error now includes `(Reference: <correlationId>)`) and search the container logs for that exact ID: `az containerapp logs show ... --tail 500 | grep <correlationId>`. This pinpoints the exact request and its full server-side error context (logged at ERROR level with `exc_info`, never sent to the client).
- Because register save writes are wrapped in a single DB transaction with the audit write (Phase 10), a failed save cannot leave a partial set of rows changed with no audit trail, or an audit entry claiming success for a save that actually rolled back — if some rows in a bulk save appear changed and others don't, re-check whether those rows individually validated (e.g. one row failed a business rule like an excess-hours check requiring `overrideReason`) rather than assuming a partial-transaction bug.
- Check for a 429 in the logs around that time — the historical-attendance-edit rate limit (30/hour/user, Phase 10) could be the actual cause if the user was making many corrections in a short window; the audit log will show a `rate_limited` entry.

## 6. Two tutors editing the same register at once — conflicting/overwritten changes

- This is what `register_version` optimistic concurrency (Phase 10 hardened this to a real atomic `WHERE register_version = %s` guard) exists to prevent. The second save should have received a `409` with `{"reason": "stale_register_version", "currentVersion": ...}`, not silently overwritten the first.
- If a user reports their changes "disappeared," check the audit log for two consecutive `save_register`-type entries close in time on the same session — the second entry's `previousValue` should reflect the first save's `newValue`, proving no silent overwrite occurred. If it doesn't, this is a genuine bug — escalate with both audit entry IDs and correlation IDs.
- The fix in the moment: ask the second user to refresh the register (fetches the current `registerVersion`) before retrying their edit.

## 6a. A register pops up a "roster may have changed" alert

Registers proactively check whether their cohort's roster may have changed (a transfer, a Functional Skills enrollment change, or a learner status change) since it was last synced, rather than relying on a tutor to remember to check manually (`pyapp/session_register_lib.py`). This is expected behaviour, not an error:

- "Review Roster Changes" opens the existing refresh dialog showing the diff — reviewing and confirming it (even when the diff turns out empty) marks it reviewed, so it won't stay permanently stuck open.
- If a tutor reports this popping up repeatedly for a cohort with no actual roster change, confirm whether a learner on that cohort has a status that flips back and forth (e.g. a Break in Learning that's being toggled) rather than assuming the check itself is wrong.
- A paused (Break in Learning) learner is correctly excluded from register expectations the same way a withdrawn/completed learner is (fixed alongside this alert — previously BIL learners were still expected to attend); reactivating them is detected correctly too.

## 7. A report's numbers don't match what's expected

- Reports read from `attendance_metrics.py`/`report_rows.py`, which exclude soft-deleted learners/cohorts/sessions by design (Phase 9's soft-delete work) — first confirm the discrepancy isn't simply a deleted record correctly no longer appearing.
- Cross-check against the audit log for the specific learner/session in question — look for `update`/`cancel`/`lock` actions on the relevant attendance rows around the reporting period; the field-level diff in the audit viewer's detail dialog shows exactly what changed and when.
- If Bud-sourced fields (via `learner_progress`) look wrong or missing, see scenario 9 — a Bud data issue should never be confused with an attendance-calculation bug; they're independent data sources joined only on stable identifiers (`uln`), never on name.
- **Functional Skills report** (`/reports/functional-skills`, admin-only, `pyapp/routers/reports.py`): overall and per-subject attendance, per-FS-cohort attendance/register-completion, and at-risk FS learners, with CSV export. This is the one place for FS attendance health specifically — a learner missing from it is more likely a cohort `membershipType`/`subject` mismatch (every FS cohort needs `membershipType = "secondary"` and a matching `subject`, enforced in `update_cohort`, `pyapp/routers/cohorts.py`) or a missing `learner_cohort_enrollments` row than a report bug.
- A paused (Break in Learning) learner is excluded from eligibility/attendance expectations the same way withdrawn/completed learners are — if report numbers look off for a learner who's currently paused, that's the fix landing correctly, not a discrepancy.

## 8. CSV import (learner or tutor) fails or produces unexpected results

- Check `/api/learners/import-jobs/{id}` (or the tutor equivalent) for the job's `status` and any error detail recorded on it.
- A `429` on the upload or confirm step is the new Phase 10 rate limit (20/hour upload, 10/hour confirm) — check the audit log for a `rate_limited` entry with `entityType: security` before assuming a genuine import bug.
- Import confirmation writes the resulting learner/tutor changes and the audit entry in one transaction (pre-existing pattern, unchanged this phase) — a failed confirm cannot have partially applied; if some rows appear applied and others don't, check each row's individual `classification`/`resolution` value rather than assuming a transaction bug.
- Correlation-ID-based log lookup (as in scenario 5) applies here too for a hard failure during upload/classification/confirm.
- **A matched learner's tutor/cohort in the CSV doesn't match their current one**: this is never applied silently — a plain field update deliberately can't move a learner between tutors/cohorts (bypasses `apply_transfer`'s validation and history-writing). The import preview shows the current-vs-proposed cohort clearly and only transfers it when the admin explicitly checks "Transfer to this cohort" per row (`pyapp/learner_import_lib.py`), going through the same `apply_transfer` path as a manual Allocation-screen transfer (so `learner_allocation_history` is written either way). If a learner's tutor/cohort looks unchanged after an import that should have moved them, check whether that row's checkbox was actually ticked before confirming.

## 9. Bud (`learner_progress`) data looks stale or missing

- Check `/api/health/ready` — `checks.bud` will read `"ok"` (recent sync data present), `"no_data"` (view has never been populated / is currently empty), or `"degraded"` (the check itself errored, e.g. the view is temporarily unreachable). **None of these ever cause `/health/ready` to report overall failure** — Bud is explicitly a non-blocking, optional dependency for core attendance functionality (a deliberate Phase 10 design decision), so a Bud problem alone should never be escalated as an application-down incident.
- The sync into `public.learner_progress` is external to this application (owned by the separate Bud LMS system per `project_bud_lms_sync` — this app only reads it, read-only, joined on stable IDs). A stale-Bud-data report should be routed to whoever owns that external sync process, not treated as an app bug.
- Confirm the application itself hasn't tried to write to or recreate the view — `git grep -n "learner_progress"` in `pyapp/` should show only `SELECT` usage in `bud_progress.py`.

## 9a. Bud sync trial (`/bud-sync-trial`): new learners stop appearing, or a learner's status looks wrong

The Phase 11 Bud delta-sync is a curated, Administrator-reviewed queue (`pyapp/bud_sync_lib.py`), not an automatic feed — most "it's stuck" reports trace back to one of three known, non-bug causes:

- **New learners dry up even though `learner_progress` keeps growing**: check `bud_sync_job.new_learners_detected` across recent jobs vs `total_source_rows_examined` — if rows are growing but `new` stays at 0, the incoming learners are very likely landing in `conflict`/`tutor_unmatched` instead. Query `tutors WHERE active AND (external_system_id IS NULL OR external_system_id = '')` — Bud's `tutor_id` has no dedicated column, it's stored in this generic, admin-editable field (`_find_tutor_by_bud_id` in `bud_sync_lib.py`), and any active tutor missing it blocks every one of their learners from ever becoming a reviewable "new learner" item. **Also check for a duplicate tutor record**: the same person sometimes exists twice (one active, one inactive, e.g. after an email-domain change) with the Bud mapping sitting on the *inactive* one — `_find_tutor_by_bud_id` treats an inactive match as unmatched, so the fix is moving `external_system_id` to the active record (clear it from the inactive one too, since more than one match is also treated as unmatched).
- **A learner's Attendance status disagrees with what Bud now reports, but no sync has ever flagged it**: this can be invisible by design, not a bug you can fix by re-running a sync. `_classify_existing_learner_update` deliberately never diffs status on a learner's *first-ever* Bud match (accepted baseline is empty) — their Bud status is "filling a gap," not a change to review — so if that learner is never actually committed/linked, the status mismatch can sit unreviewed indefinitely with no warning anywhere. Worse, if a first-observation match (or a `relink_to_new_enrollment`) *is* committed while Bud's status was already Withdrawn/Completed, that status gets silently baked into `bud_learner_link.accepted_values.statusDesc` with the learner's own `learners.status` never updated — every later sync then sees "no change" and never flags it again. There is no automatic re-check for this; it requires a manual reconciliation pass: compare every `learners WHERE status = 'active'` row's `learner_ref` against `learner_progress.learner_reference` (taking the row with the latest `start_date` if a reference has more than one, since a re-enrolled learner keeps their old Bud row around forever) and diff the live `status_desc` against `BUD_STATUS_TO_LEARNER_STATUS` (`pyapp/bud_status_mapping.py`). Correcting a learner to `withdrawn`/`completed` needs an effective date Bud's feed never supplies (`REQUIRES_EFFECTIVE_DATE`) — there is no source for this other than an Administrator; it is not safe to infer one, and `_change_learner_status`/`change_learner_status` (`pyapp/routers/learners.py`) will reject the change without it.
- **A "new learner" item can't be approved, and "Already represented" doesn't work either**: `learners.learner_ref` is `NOT NULL UNIQUE` with no `deleted_at` exception, so a soft-deleted learner permanently holds their old reference — a genuinely new Bud row reusing it fails the create, and `link_existing_learner` *also* excludes soft-deleted learners (`deleted_at IS NULL` check), so its own suggested fallback doesn't work either. There is no "restore a deleted learner" endpoint. If this is genuinely the same person re-appearing (check `audit_logs` for the original `delete_learner` entry's `reason`/timestamp against Bud's current status), the fix is a direct, audited restore (clear `deleted_at`/`deleted_by`/`deletion_reason`, write a `restore_learner` audit entry) followed by the normal "Already represented" link — there is no UI path for this, it has to be done directly.
- **Ambiguous-sibling conflicts** (`learner_reference_matches_multiple_bud_rows` / `linked_bud_record_has_sibling_enrollment`) are the expected shape of a learner re-enrolling on a new Bud standard while their old plan is still sitting there (Bud never deletes a superseded enrolment) — resolve via Relink (`relink_to_new_enrollment`), which refuses (409, `relink_target_status_mismatch`) to silently relink onto a plan Bud already reports as Withdrawn/Completed unless explicitly confirmed, specifically to prevent re-baking the second bullet's "invisible forever" trap.

## 10. Database appears unavailable

- `/api/health/ready` returning `503` with `checks.database: "unavailable"` confirms this from the app's own perspective without leaking connection details.
- `az postgres flexible-server show --name s4-attendance-pg-gzn5bh --resource-group Skills-4-Attendance-Tracker --query state` — confirm the server's own reported state (`Ready`, `Stopped`, etc.).
- If the server is `Ready` but the app still can't connect, suspect a credential/secret mismatch (e.g. after a password rotation that didn't get pushed to the Container App secret) — verify `az containerapp show -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --query "properties.configuration.secrets"` lists `db-url` as present (values are never returned by this command, only names — check the app boot logs for a connection-refused/auth-failed error instead).
- If the server itself is down, see `docs/backup-and-recovery.md` for restore procedures — this is the scenario that document's §1 exists for.

## 11. Latest Container App revision is unhealthy after a deploy

- `az containerapp revision list -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --query "[].{name,active,healthState,provisioningState}" -o table` — check `healthState`.
- `az containerapp logs show -n s4-attendance-app-gzn5bh -g Skills-4-Attendance-Tracker --tail 200` — a crash at boot most commonly means `AuthSettings.validate_startup()` failed (missing/misconfigured Entra env var in production) or a `bootstrap.py` DDL statement failed (e.g. a new constraint violated by existing data — see the deployment checklist's migration-review step, which exists to catch this *before* deploy).
- Rollback: see `docs/backup-and-recovery.md` §2 (reactivate the prior revision, shift traffic back).

## 12. A schema/bootstrap change failed to apply

- There is no separate migration step to "retry" — `bootstrap.py` runs at every app boot. A failure here crashes the boot (scenario 11), it does not leave the app half-migrated and running.
- Read the boot log for the specific SQL statement that failed (psycopg errors include the statement). The most common cause is a new `CHECK` constraint or unique index rejecting pre-existing data that violates it — this is exactly what the deployment checklist's migration-review step (§3) is meant to catch beforehand by querying production read-only before shipping the constraint.
- Fix forward by correcting the offending data (via the normal application, so the fix is audited) or by adjusting the constraint in `bootstrap.py` if it was genuinely too strict, then redeploy. Do not attempt to hand-edit the schema directly on the production server outside of `bootstrap.py` — that creates permanent drift between what the code assumes and what's actually there.

## 13. Spike in a specific HTTP status-code family

- **401 spike**: almost always an Entra token issue at scale (e.g. an app registration config change, a JWKS rotation not yet picked up) — check timing against any recent Entra-side config change; not usually an application bug.
- **403 spike**: check the audit log for a burst of `authorization_denied` entries — if they cluster on one `entityType`/action, check whether an allocation or role change was recently made incorrectly (scenario 3's mechanism, at volume).
- **409 spike**: register-version conflicts (scenario 6) at unusual volume suggest either genuine concurrent-editing load (e.g. many tutors correcting the same session at once, ask why) or a frontend bug re-sending stale `registerVersion` values after a UI state bug — check whether the spike correlates with a recent frontend deploy.
- **422 spike**: validation errors — check the safe, structured error messages (Phase 10 rewrote these to list the specific failing fields, e.g. `"pageSize: ensure this value is less than or equal to 200"`) in the logs; usually indicates a frontend/backend contract mismatch after an API change, or a client hitting the new `pageSize` clamp with a stale hardcoded value.
- **500 spike**: search logs for `"level":"ERROR"` in the same window; every entry includes a correlation ID and full server-side traceback (never sent to the client) — this is the highest-priority spike to investigate immediately, and per Phase 10's design, the client-facing error will always include `(Reference: <correlationId>)` for exactly this lookup.
- **429 spike**: check `rate_limit_attempts`/the audit log's `rate_limited` entries for which `action` is being hit — either a genuine abusive/broken client (e.g. a retry loop) or the configured limit is too tight for real usage and needs tuning (the limits are documented, easily adjustable constants in `pyapp/rate_limit.py`'s call sites, not hardcoded magic scattered around).
