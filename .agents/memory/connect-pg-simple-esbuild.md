---
name: connect-pg-simple + esbuild bundling
description: createTableIfMissing in connect-pg-simple fails at runtime when the server is bundled with esbuild into a single file.
---

`connect-pg-simple`'s `createTableIfMissing: true` reads a bundled `table.sql`
asset via a path relative to its own module location at runtime
(`_rawEnsureSessionStoreTable`). When the API server is bundled by esbuild
into a single `dist/index.mjs` (a common pattern for Node backends on
Replit), that relative path no longer resolves, so the read throws
`ENOENT: ... dist/table.sql`. The error is easy to miss because express-session
still issues a `Set-Cookie` on every request (a brand new session each time)
instead of surfacing an auth failure — symptoms look like "cookies don't
persist" or "every request is unauthenticated," not "table missing."

**Why:** discovered while debugging a fully-working login endpoint whose
session never survived to the next request; the fix wasn't in the auth code
at all, it was in table provisioning.

**How to apply:** when using `connect-pg-simple` (or any store with a similar
runtime-asset-lookup auto-create feature) in a project whose server gets
bundled into a single file, set `createTableIfMissing: false` and create the
session table with an explicit one-off SQL migration instead (see
`connect-pg-simple`'s `table.sql` for the canonical schema — `sid varchar
primary key, sess json not null, expire timestamp(6) not null`, plus an index
on `expire`).
