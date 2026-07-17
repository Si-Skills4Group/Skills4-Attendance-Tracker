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

DO $$
BEGIN
  CREATE TYPE session_status AS ENUM ('scheduled', 'cancelled');
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
ALTER TABLE tutors ADD COLUMN IF NOT EXISTS phone text;

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

CREATE INDEX IF NOT EXISTS idx_cohorts_tutor_id ON cohorts (tutor_id);
CREATE INDEX IF NOT EXISTS idx_cohorts_programme ON cohorts (programme);
CREATE INDEX IF NOT EXISTS idx_cohorts_active ON cohorts (active);

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

ALTER TABLE learners ADD COLUMN IF NOT EXISTS actual_end_date date;
ALTER TABLE learners ADD COLUMN IF NOT EXISTS withdrawal_date date;

CREATE UNIQUE INDEX IF NOT EXISTS learners_uln_unique
  ON learners (uln)
  WHERE uln IS NOT NULL AND uln <> '';
CREATE INDEX IF NOT EXISTS idx_learners_status ON learners (status);
CREATE INDEX IF NOT EXISTS idx_learners_programme ON learners (programme);

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

CREATE INDEX IF NOT EXISTS idx_allocation_history_learner_effective
  ON learner_allocation_history (learner_id, effective_date);

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

CREATE TABLE IF NOT EXISTS scheduled_allocations (
  id serial PRIMARY KEY,
  learner_id integer NOT NULL,
  new_tutor_id integer,
  new_cohort_id integer,
  effective_date date NOT NULL,
  transfer_reason text,
  created_by integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'pending',
  applied_at timestamptz,
  cancelled_at timestamptz,
  cancelled_by integer
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduled_allocations_one_pending_per_learner
  ON scheduled_allocations (learner_id)
  WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_scheduled_allocations_due
  ON scheduled_allocations (effective_date)
  WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS learner_import_jobs (
  id serial PRIMARY KEY,
  filename text NOT NULL,
  uploaded_by integer NOT NULL,
  status text NOT NULL DEFAULT 'uploaded',
  total_rows integer NOT NULL DEFAULT 0,
  new_count integer NOT NULL DEFAULT 0,
  exact_existing_count integer NOT NULL DEFAULT 0,
  probable_duplicate_count integer NOT NULL DEFAULT 0,
  possible_duplicate_count integer NOT NULL DEFAULT 0,
  identifier_conflict_count integer NOT NULL DEFAULT 0,
  invalid_count integer NOT NULL DEFAULT 0,
  result_summary jsonb,
  last_error text,
  started_importing_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learner_import_jobs_status ON learner_import_jobs (status);

CREATE TABLE IF NOT EXISTS learner_import_rows (
  id serial PRIMARY KEY,
  job_id integer NOT NULL,
  row_number integer NOT NULL,
  raw_data jsonb NOT NULL,
  classification text NOT NULL,
  proposed_action text NOT NULL,
  resolution text,
  resolved_by integer,
  resolved_at timestamptz,
  match_details jsonb NOT NULL DEFAULT '{}'::jsonb,
  matched_learner_id integer,
  cohort_match_status text,
  matched_cohort_id integer,
  errors jsonb NOT NULL DEFAULT '[]'::jsonb,
  warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
  import_result text,
  import_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_learner_import_rows_job_classification
  ON learner_import_rows (job_id, classification);
CREATE UNIQUE INDEX IF NOT EXISTS idx_learner_import_rows_job_row_number
  ON learner_import_rows (job_id, row_number);

CREATE TABLE IF NOT EXISTS tutor_import_jobs (
  id serial PRIMARY KEY,
  filename text NOT NULL,
  uploaded_by integer NOT NULL,
  status text NOT NULL DEFAULT 'uploaded',
  total_rows integer NOT NULL DEFAULT 0,
  new_count integer NOT NULL DEFAULT 0,
  exact_existing_count integer NOT NULL DEFAULT 0,
  probable_duplicate_count integer NOT NULL DEFAULT 0,
  identifier_conflict_count integer NOT NULL DEFAULT 0,
  invalid_count integer NOT NULL DEFAULT 0,
  result_summary jsonb,
  last_error text,
  started_importing_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tutor_import_jobs_status ON tutor_import_jobs (status);

CREATE TABLE IF NOT EXISTS tutor_import_rows (
  id serial PRIMARY KEY,
  job_id integer NOT NULL,
  row_number integer NOT NULL,
  raw_data jsonb NOT NULL,
  classification text NOT NULL,
  proposed_action text NOT NULL,
  resolution text,
  resolved_by integer,
  resolved_at timestamptz,
  match_details jsonb NOT NULL DEFAULT '{}'::jsonb,
  matched_tutor_id integer,
  errors jsonb NOT NULL DEFAULT '[]'::jsonb,
  warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
  import_result text,
  import_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tutor_import_rows_job_classification
  ON tutor_import_rows (job_id, classification);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tutor_import_rows_job_row_number
  ON tutor_import_rows (job_id, row_number);

CREATE TABLE IF NOT EXISTS login_attempts (
  id serial PRIMARY KEY,
  ip_key text NOT NULL,
  attempted_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_key_time ON login_attempts (ip_key, attempted_at);

CREATE INDEX IF NOT EXISTS idx_attendance_sessions_cohort_date ON attendance_sessions (cohort_id, session_date);
CREATE INDEX IF NOT EXISTS idx_learners_cohort_id ON learners (cohort_id);
CREATE INDEX IF NOT EXISTS idx_learners_tutor_id ON learners (tutor_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_entity ON audit_logs (entity_type, entity_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs (user_id, timestamp DESC);

-- Phase 6: attendance session lifecycle (cancel/edit-confirm/duplicate-override)
-- and a persisted expected-learner register snapshot (see session_register_lib.py).
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS status session_status NOT NULL DEFAULT 'scheduled';
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS cancelled_at timestamptz;
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS cancellation_reason text;
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS cancelled_by integer;
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS register_locked_at timestamptz;
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS register_locked_by integer;
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS override_reason text;
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS updated_by integer;
-- Explicit marker (rather than "no snapshot rows exist yet") so a session
-- whose register legitimately has zero eligible learners is never mistaken
-- for one that hasn't been generated, and silently regenerated later.
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS register_generated_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_attendance_sessions_status ON attendance_sessions (status);

CREATE TABLE IF NOT EXISTS session_expected_learners (
  id serial PRIMARY KEY,
  session_id integer NOT NULL,
  learner_id integer NOT NULL,
  cohort_id integer NOT NULL,
  source_allocation_history_id integer,
  generated_at timestamptz NOT NULL DEFAULT now(),
  generated_by integer,
  UNIQUE (session_id, learner_id)
);
CREATE INDEX IF NOT EXISTS idx_session_expected_learners_session ON session_expected_learners (session_id);
CREATE INDEX IF NOT EXISTS idx_session_expected_learners_learner ON session_expected_learners (learner_id);

-- Phase 7: register concurrency, explicit completion, and locking (the
-- register_locked_at/register_locked_by columns above were added in Phase 6
-- for forward-compatibility and are wired up for real here).
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS register_version integer NOT NULL DEFAULT 1;
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS completed_at timestamptz;
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS completed_by integer;
ALTER TABLE attendance_sessions ADD COLUMN IF NOT EXISTS lock_reason text;
CREATE INDEX IF NOT EXISTS idx_attendance_sessions_updated_at ON attendance_sessions (updated_at);

ALTER TABLE attendance_records ADD COLUMN IF NOT EXISTS created_by integer;
ALTER TABLE attendance_records ADD COLUMN IF NOT EXISTS expected_register_row_id integer;
CREATE INDEX IF NOT EXISTS idx_attendance_records_learner_id ON attendance_records (learner_id);
CREATE INDEX IF NOT EXISTS idx_attendance_records_status ON attendance_records (status);

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
