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


# ----- Photos table (guest photo/video uploads) -----

PHOTOS_TABLE = "photos"

PHOTO_COLUMNS = (
    "id",
    "storage_key",
    "thumb_key",
    "uploader_name",
    "caption",
    "content_type",
    "size_bytes",
    "width",
    "height",
    "duration_seconds",
    "created_at",
)

PHOTO_COLUMNS_INSERT = (
    "storage_key",
    "uploader_name",
    "caption",
    "content_type",
    "size_bytes",
    "width",
    "height",
    "duration_seconds",
)


def row_to_photo(row: tuple) -> dict[str, Any]:
    """Map a DB row to a dict for PhotoOut."""
    return {
        "id": row[0],
        "storage_key": row[1],
        "thumb_key": row[2],
        "uploader_name": row[3],
        "caption": row[4],
        "content_type": row[5],
        "size_bytes": row[6],
        "width": row[7],
        "height": row[8],
        "duration_seconds": row[9],
        "created_at": row[10],
    }
