"""
Upload sample data to the Railway DB.
Uses schemas/input.py for validation and schemas/db.py for table/columns.
"""

import os
import psycopg2

from schemas.input import UserCreate
from schemas.db import USERS_TABLE, USER_COLUMNS_INSERT

# Set DATABASE_URL in .env for local runs, or in Railway when Postgres is linked.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL environment variable is not set. Add it to .env or your environment.")

# Sample data — validated by Pydantic before insert
SAMPLES = [
    UserCreate(email="alice@example.com", name="Alice"),
    UserCreate(email="bob@example.com", name="Bob", is_active=True),
    UserCreate(email="charlie@example.com", name="Charlie", is_active=False),
]


CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(255) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now()
);
"""


def main() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_USERS_SQL)
            columns = ", ".join(USER_COLUMNS_INSERT)
            placeholders = ", ".join(["%s"] * len(USER_COLUMNS_INSERT))
            sql = f"INSERT INTO {USERS_TABLE} ({columns}) VALUES ({placeholders})"

            for u in SAMPLES:
                cur.execute(sql, (u.email, u.name, u.is_active))

        conn.commit()
        print(f"Inserted {len(SAMPLES)} users.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
