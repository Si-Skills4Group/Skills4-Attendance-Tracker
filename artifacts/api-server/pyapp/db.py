"""Database connection pool. Talks to the same Postgres database and tables
that the Drizzle schema in @workspace/db defines -- no schema changes here,
this is a pure client-language swap.
"""
import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL must be set. Did you forget to provision a database?"
    )

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=10, kwargs={"autocommit": True})


@contextmanager
def get_cursor():
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur
