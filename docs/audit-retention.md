# Audit log retention policy

## Decision

Audit records (`audit_logs` table) are **retained indefinitely** under the current configuration. No automatic deletion, archival, or purge exists anywhere in the codebase, and none is planned without separate, explicit approval.

## Why

- The organisation has funding/compliance-audit requirements that make historical attendance and administrative-action records valuable well beyond typical operational log retention windows.
- The audit log is also the primary tool for manually recovering from an incorrect data change (see `docs/backup-and-recovery.md` §3) — a shorter retention window than the database's own 7-day backup retention would actively remove the more useful recovery tool first.
- No one has yet measured actual audit table growth in this application; setting a retention window now would be a guess, not a data-driven decision.

## What this means concretely

- There is no scheduled job, cron task, or admin-triggered control anywhere in the codebase that deletes rows from `audit_logs`.
- The audit viewer (`/audit-log`) intentionally has **no delete or purge control in its UI** — per the Phase 10 brief's explicit instruction not to include a destructive purge control without approval.
- Storage growth is unbounded over time. This is a known, accepted tradeoff, not an oversight.

## Revisit when

- Audit table size or query performance in the audit viewer becomes a measured problem (not a hypothetical one).
- A specific compliance requirement defines a maximum or minimum retention period that should be enforced instead of "indefinite."

If either happens, the recommended next step is a **time-boxed archive strategy** (e.g. move audit rows older than N years to cheaper storage, queryable separately, rather than deleting them) rather than outright deletion — preserving the recovery and compliance value of older records while controlling primary-table growth. This is a recommendation for a future phase, not something implemented now.
