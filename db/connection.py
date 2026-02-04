"""
Database connection for FastAPI.
Use as a dependency: get_db() yields a connection, closes after request.
"""

import os
from contextlib import contextmanager

import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:wAQnovYEHldUZBxlvZaUfKekRLsthkMf@yamabiko.proxy.rlwy.net:36616/railway",
)


@contextmanager
def get_conn():
    """Context manager: yields a DB connection, closes on exit."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
