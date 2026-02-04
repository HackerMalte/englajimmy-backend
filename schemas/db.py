"""
Database layer: single source of truth for table and column definitions.

Define:
- Table names
- Column names and types (for SQL and for mapping Pydantic ↔ DB)

Use this when building raw SQL or when configuring an ORM.
"""

from datetime import datetime
from typing import Any

# ----- Table and column names (avoid typos, reuse everywhere) -----

USERS_TABLE = "users"

USER_COLUMNS = (
    "id",           # SERIAL PRIMARY KEY
    "email",        # VARCHAR(255) UNIQUE NOT NULL
    "name",         # VARCHAR(255) NOT NULL
    "is_active",    # BOOLEAN DEFAULT true
    "created_at",   # TIMESTAMPTZ DEFAULT now()
)

USER_COLUMNS_INSERT = ("email", "name", "is_active")  # exclude id, created_at


def row_to_user(row: tuple[str, ...]) -> dict[str, Any]:
    """Map a DB row (id, email, name, is_active, created_at) to a dict for Pydantic."""
    return {
        "id": row[0],
        "email": row[1],
        "name": row[2],
        "is_active": row[3],
        "created_at": row[4],
    }


# ----- RSVPs table (for frontend RSVP page) -----

RSVPS_TABLE = "rsvps"

RSVP_COLUMNS = (
    "id",
    "name",
    "email",
    "coming",
    "allergies",
    "transport_assist",
    "created_at",
)

RSVP_COLUMNS_INSERT = ("name", "email", "coming", "allergies", "transport_assist")


def row_to_rsvp(row: tuple) -> dict[str, Any]:
    """Map a DB row to a dict for RsvpOut."""
    return {
        "id": row[0],
        "name": row[1],
        "email": row[2],
        "coming": row[3],
        "allergies": row[4],
        "transport_assist": row[5],
        "created_at": row[6],
    }
