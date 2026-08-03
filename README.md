# Skills4Attendance

Attendance and allocation management for Skills4Group's pharmacy apprenticeship programmes — tracks learners, tutors, cohorts, attendance sessions, and tutor/cohort allocation, with Microsoft Entra ID sign-in.

## Stack

- **Frontend**: React 19 + Vite + TypeScript, Tailwind 4, shadcn/ui, MSAL (Entra ID) — [artifacts/skills4attendance](artifacts/skills4attendance)
- **Backend**: Python 3.13 + FastAPI, talking directly to Postgres via `psycopg` (no ORM) — [artifacts/api-server](artifacts/api-server)
- **Database**: PostgreSQL. [lib/db](lib/db) holds the Drizzle/TypeScript schema, which is the source of truth for table shape; the Python backend mirrors it by hand in [bootstrap.py](artifacts/api-server/pyapp/bootstrap.py)'s DDL (kept in sync manually — Drizzle itself isn't imported from Python)
- **API contract**: [lib/api-spec/openapi.yaml](lib/api-spec/openapi.yaml) is the source of truth. [lib/api-zod](lib/api-zod) and [lib/api-client-react](lib/api-client-react) are generated from it via Orval — never hand-edit files under their `generated/` folders
- **Auth**: Microsoft Entra ID (Azure AD) via MSAL on the frontend, validated by the FastAPI backend against tenant JWKS. See [docs/entra-phase2.md](docs/entra-phase2.md) for the full architecture, app registration setup, and required environment variables
- **Deployment**: Docker multi-stage build (frontend build → Python runtime) pushed to Azure Container Registry and run on Azure Container Apps — see [Dockerfile](Dockerfile)
- **CI/CD**: [.github/workflows/ci-cd.yml](.github/workflows/ci-cd.yml) — every push/PR to `main` runs the full backend `pytest` and frontend `tsc`/`vitest`/`vite build` suites; production deploy is a separate, manually-triggered `workflow_dispatch` run (Actions tab → CI/CD → Run workflow) that only proceeds if both test jobs pass, then builds/pushes the image and updates the Container App via Azure OIDC login (no stored cloud credentials). See [docs/deployment-checklist.md](docs/deployment-checklist.md) for the full deploy procedure.

## Repo layout

- `artifacts/skills4attendance` — the React SPA
- `artifacts/api-server/pyapp` — the FastAPI backend (routers, auth, DB access, and `bud_sync_lib.py`/`bud_progress.py` for the read-only Bud LMS integration — delta-sync of learner progress/status into `learner_progress`, never written back)
- `artifacts/mockup-sandbox` — standalone design/prototype sandbox, not part of the deployed app
- `lib/db` — Drizzle schema (Postgres source of truth)
- `lib/api-spec` — OpenAPI spec (API contract source of truth)
- `lib/api-zod`, `lib/api-client-react` — generated API types/hooks, do not edit by hand
- `scripts` — misc workspace tooling
- `.github/workflows/ci-cd.yml` — test-on-every-push CI and manually-triggered production deploy
- `docs/entra-phase2.md` — Entra ID authentication architecture and rollout notes
- `docs/deployment-checklist.md` — step-by-step production deploy procedure (current CI/CD-based flow)

## Local development

Requires Node.js 24, pnpm, Python 3.11+, and a reachable Postgres database.

**Environment**: create `.env` files (gitignored) at:
- `artifacts/skills4attendance/.env` — `PORT`, `BASE_PATH`, and `VITE_ENTRA_*`/`VITE_API_*` vars (see docs/entra-phase2.md)
- `artifacts/api-server/.env` — `AUTH_MODE`, `ENTRA_*`, `DATABASE_URL`, `ALLOWED_ORIGINS` (loaded automatically via `python-dotenv`; see docs/entra-phase2.md). Also set `TEST_DATABASE_URL` here, pointed at a dedicated `attendance_test` database — `pytest` (via `tests/conftest.py`) refuses to run without it, and deliberately never falls back to `DATABASE_URL`, so a missing test database can't silently run tests against production.

**Run the backend**:
```
pip install -r requirements.txt
uvicorn --app-dir artifacts/api-server pyapp.main:app --reload --port 8080
```

**Run the frontend**:
```
pnpm install
pnpm --filter @workspace/skills4attendance run dev
```

**Other useful commands**:
- `pnpm run typecheck` — typecheck the `lib/*` packages (build references); the frontend app has its own `pnpm --filter @workspace/skills4attendance run typecheck`
- `pnpm --filter @workspace/api-spec run codegen` — regenerate `lib/api-zod`/`lib/api-client-react` after editing `lib/api-spec/openapi.yaml`

## Gotchas

- **Windows**: [pnpm-workspace.yaml](pnpm-workspace.yaml) deliberately strips every non-Linux platform binary (esbuild, rollup, lightningcss, Tailwind's oxide engine) — the frontend cannot be built natively on Windows. Use WSL2 (or the Docker build) for frontend work on a Windows machine.
- After editing the OpenAPI spec, always re-run codegen — the generated packages have gone stale before (missing endpoints, mismatched lockfile) and silently broken things that looked fine in the spec.
- Keep `lib/db/src/schema` and `bootstrap.py`'s DDL in sync by hand when changing the Postgres schema; there's no automated migration linking the two.
