# API Server (FastAPI)

Python/FastAPI rewrite of the Skills4Attendance backend. Serves the API
contract defined in `lib/api-spec/openapi.yaml` under the `/api` prefix.

- Talks directly to the shared Postgres database (`DATABASE_URL`) via
  raw SQL (psycopg) -- no ORM, no Drizzle. Table/column names match the
  Drizzle schema in `lib/db`; that package is not imported from Python.
- Sessions are stored in the existing `user_sessions` table (same
  `sid`/`sess`/`expire` shape the previous express-session +
  connect-pg-simple stack used), so logins/cookies keep working across
  the rewrite. Cookie name: `s4a.sid`.
- Python dependencies are managed at the repo root (`pyproject.toml` /
  `.pythonlibs`) via the package-management tooling -- use
  `installLanguagePackages({ language: "python", ... })`, not pip
  directly.
- Run locally: `python -m uvicorn --app-dir artifacts/api-server pyapp.main:app --host 0.0.0.0 --port $PORT --reload`
  (this is exactly what the artifact's dev workflow runs).

## Layout

- `pyapp/main.py` -- app assembly, CORS, session middleware, exception handlers
- `pyapp/session.py` -- cookie/session middleware backed by `user_sessions`
- `pyapp/auth.py` -- password hashing (bcrypt) + auth dependencies
- `pyapp/db.py` -- psycopg connection pool
- `pyapp/attendance_calc.py`, `attendance_data.py`, `allocation_lib.py`,
  `csv_utils.py`, `learners_query.py` -- shared business-logic helpers
- `pyapp/routers/*.py` -- one module per resource, mirroring the OpenAPI tags
