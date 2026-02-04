"""
Database connection for FastAPI.
Use as a dependency: get_db() yields a connection, closes after request.
"""

import os
from contextlib import contextmanager

import psycopg2

# Always use env: set DATABASE_URL in Railway (auto when Postgres is linked) or in .env locally.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is not set. "
        "Set it in Railway (link Postgres) or in a .env file for local runs."
    )


# Fail fast if DB is unreachable (e.g. Railway not reachable from local network)
CONNECT_TIMEOUT_SECONDS = 10


@contextmanager
def get_conn():
    """Context manager: yields a DB connection, closes on exit."""
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
