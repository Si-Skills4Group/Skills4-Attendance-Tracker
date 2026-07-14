"""Production database bootstrap for empty Azure PostgreSQL instances."""

from __future__ import annotations

import os

import bcrypt
import psycopg


DDL = """
DO $$
BEGIN
  CREATE TYPE user_role AS ENUM ('admin', 'tutor');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
  CREATE TYPE learner_status AS ENUM ('active', 'withdrawn', 'completed', 'paused');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
  CREATE TYPE attendance_status AS ENUM (
    'present',
    'absent_authorised',
    'absent_unauthorised',
    'late',
    'not_expected',
    'withdrawn'
  );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END
$$;

DO $$
BEGIN
  CREATE TYPE delivery_day AS ENUM (
    'monday',
    'tuesday',
    'wednesday',
    'thursday',
    'friday',
    'saturday',
    'sunday'
  );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END
$$;

CREATE TABLE IF NOT EXISTS users (
  id serial PRIMARY KEY,
  first_name text NOT NULL,
  last_name text NOT NULL,
  email text NOT NULL UNIQUE,
  password_hash text,
  role user_role NOT NULL,
  active boolean NOT NULL DEFAULT true,
  display_name text,
  entra_object_id text,
  entra_tenant_id text,
  tutor_id integer,
  last_login_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS entra_object_id text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS entra_tenant_id text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS users_entra_identity_unique
  ON users (entra_tenant_id, entra_object_id)
  WHERE entra_tenant_id IS NOT NULL AND entra_object_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS tutors (
  id serial PRIMARY KEY,
  user_id integer NOT NULL UNIQUE,
  first_name text NOT NULL,
  last_name text NOT NULL,
  email text NOT NULL,
  employee_ref text UNIQUE,
  active boolean NOT NULL DEFAULT true,
  external_system_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE tutors ALTER COLUMN employee_ref DROP NOT NULL;

CREATE TABLE IF NOT EXISTS cohorts (
  id serial PRIMARY KEY,
  name text NOT NULL,
  programme text NOT NULL,
  level text NOT NULL,
  tutor_id integer,
  delivery_day delivery_day NOT NULL,
  session_start_time text NOT NULL,
  session_end_time text NOT NULL,
  start_date date NOT NULL,
  end_date date,
  active boolean NOT NULL DEFAULT true,
  external_system_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS learners (
  id serial PRIMARY KEY,
  learner_ref text NOT NULL UNIQUE,
  uln text,
  first_name text NOT NULL,
  last_name text NOT NULL,
  email text,
  employer text,
  programme text NOT NULL,
  level text NOT NULL,
  start_date date NOT NULL,
  planned_end_date date,
  status learner_status NOT NULL DEFAULT 'active',
  tutor_id integer,
  cohort_id integer,
  external_system_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS learner_allocation_history (
  id serial PRIMARY KEY,
  learner_id integer NOT NULL,
  previous_tutor_id integer,
  new_tutor_id integer,
  previous_cohort_id integer,
  new_cohort_id integer,
  effective_date date NOT NULL,
  transfer_reason text,
  changed_by integer NOT NULL,
  changed_date timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attendance_sessions (
  id serial PRIMARY KEY,
  cohort_id integer NOT NULL,
  session_date date NOT NULL,
  planned_start_time text NOT NULL,
  planned_end_time text NOT NULL,
  planned_duration_hours numeric NOT NULL,
  title text,
  notes text,
  created_by integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attendance_records (
  id serial PRIMARY KEY,
  session_id integer NOT NULL,
  learner_id integer NOT NULL,
  status attendance_status NOT NULL,
  hours_attended numeric NOT NULL DEFAULT 0,
  minutes_late integer NOT NULL DEFAULT 0,
  notes text,
  override_reason text,
  last_edited_by integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, learner_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id serial PRIMARY KEY,
  user_id integer,
  action text NOT NULL,
  entity_type text NOT NULL,
  entity_id integer,
  previous_value text,
  new_value text,
  timestamp timestamptz NOT NULL DEFAULT now(),
  ip_address text
);

CREATE TABLE IF NOT EXISTS app_settings (
  id serial PRIMARY KEY,
  organisation_name text NOT NULL DEFAULT 'Skills4Group',
  low_attendance_threshold numeric NOT NULL DEFAULT 85
);

CREATE TABLE IF NOT EXISTS user_sessions (
  sid varchar PRIMARY KEY,
  sess json NOT NULL,
  expire timestamp NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_expire ON user_sessions (expire);

INSERT INTO app_settings (id, organisation_name, low_attendance_threshold)
VALUES (1, 'Skills4Group', 85)
ON CONFLICT (id) DO NOTHING;
"""


def _seed_admin(conn: psycopg.Connection) -> None:
    email = os.environ.get("ADMIN_EMAIL")
    if not email:
        return

    is_production = (
        os.environ.get("ENVIRONMENT") == "production"
        or os.environ.get("ENV") == "production"
        or os.environ.get("NODE_ENV") == "production"
    )
    entra_object_id = os.environ.get("ADMIN_ENTRA_OBJECT_ID")
    entra_tenant_id = os.environ.get("ADMIN_ENTRA_TENANT_ID") or os.environ.get("ENTRA_TENANT_ID")
    if is_production and (not entra_object_id or not entra_tenant_id):
        return

    password = os.environ.get("ADMIN_PASSWORD")
    password_hash = None
    if password and not is_production:
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode(
            "utf-8"
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users
              (first_name, last_name, email, password_hash, role, active, display_name, entra_object_id, entra_tenant_id)
            VALUES (%s, %s, %s, %s, 'admin', true, %s, %s, %s)
            ON CONFLICT (email) DO NOTHING
            """,
            (
                os.environ.get("ADMIN_FIRST_NAME", "Admin"),
                os.environ.get("ADMIN_LAST_NAME", "User"),
                email.lower(),
                password_hash,
                os.environ.get("ADMIN_DISPLAY_NAME"),
                entra_object_id,
                entra_tenant_id,
            ),
        )


def bootstrap_database() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return

    with psycopg.connect(database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        _seed_admin(conn)
