"""
FastAPI app for RSVP: query and upload data from a frontend RSVP page.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # Load .env so API_KEY and DATABASE_URL work when testing locally

import psycopg2
from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader

from db.connection import get_conn
from schemas.input import RsvpCreate, RsvpOut, RsvpSubmitResponse
from schemas.db import (
    RSVPS_TABLE,
    RSVP_COLUMNS_INSERT,
    row_to_rsvp,
)

# Auth: set API_KEY in Railway (or .env) to protect GET /rsvps. Leave unset for open list (dev only).
API_KEY = os.environ.get("API_KEY")
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

CREATE_RSVPS_SQL = """
CREATE TABLE IF NOT EXISTS rsvps (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    email           VARCHAR(255) NOT NULL,
    coming          BOOLEAN DEFAULT true,
    allergies       VARCHAR(500),
    transport_assist BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (name, email)
);
"""


def ensure_rsvps_table():
    """Create rsvps table if it doesn't exist, or migrate existing table to new schema."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Create table if it doesn't exist
            cur.execute(CREATE_RSVPS_SQL)
            
            # Check if table has old columns and migrate
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'rsvps'
            """)
            existing_columns = {row[0] for row in cur.fetchall()}
            
            # Migrate: rename attending -> coming if needed
            if 'attending' in existing_columns and 'coming' not in existing_columns:
                cur.execute("ALTER TABLE rsvps RENAME COLUMN attending TO coming")
            
            # Migrate: drop old message column if it exists
            if 'message' in existing_columns:
                cur.execute("ALTER TABLE rsvps DROP COLUMN IF EXISTS message")
            
            # Migrate: add new columns if they don't exist
            if 'allergies' not in existing_columns:
                cur.execute("ALTER TABLE rsvps ADD COLUMN IF NOT EXISTS allergies VARCHAR(500)")
            
            if 'transport_assist' not in existing_columns:
                cur.execute("ALTER TABLE rsvps ADD COLUMN IF NOT EXISTS transport_assist BOOLEAN DEFAULT false")
            
            # Remove old email-only unique constraint if it exists
            cur.execute("""
                SELECT 1 FROM pg_constraint 
                WHERE conrelid = 'rsvps'::regclass AND conname = 'rsvps_email_key'
            """)
            if cur.fetchone() is not None:
                cur.execute("ALTER TABLE rsvps DROP CONSTRAINT rsvps_email_key")
            
            # Ensure one RSVP per (name, email) combo: add unique constraint if not already present
            cur.execute("""
                SELECT 1 FROM pg_constraint 
                WHERE conrelid = 'rsvps'::regclass AND conname = 'rsvps_name_email_key'
            """)
            if cur.fetchone() is None:
                cur.execute("ALTER TABLE rsvps ADD CONSTRAINT rsvps_name_email_key UNIQUE (name, email)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_rsvps_table()
    yield


app = FastAPI(
    title="Englajimmy RSVP API",
    description="Query and submit RSVPs for the frontend RSVP page.",
    lifespan=lifespan,
)

# Allow frontend to call this API from another origin (e.g. localhost:3000 or your deployed site)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict to your frontend origin in production, e.g. ["https://yoursite.com"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    """Dependency: yields a DB connection for the request."""
    with get_conn() as conn:
        yield conn


def require_api_key(api_key: str | None = Security(API_KEY_HEADER)) -> None:
    """Dependency: require X-API-Key header when API_KEY env is set. Used for GET /rsvps."""
    if not API_KEY:
        return  # No key configured → allow (e.g. local dev)
    if not api_key or api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.get("/")
async def root():
    return {"message": "Englajimmy RSVP API", "docs": "/docs"}


@app.get("/rsvps", response_model=list[RsvpOut])
def list_rsvps(
    _: None = Depends(require_api_key),
    conn: psycopg2.extensions.connection = Depends(get_db),
):
    """List all RSVPs. Requires X-API-Key header when API_KEY is set in env (recommended in production)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, name, email, coming, allergies, transport_assist, created_at FROM {RSVPS_TABLE} ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
    return [RsvpOut(**row_to_rsvp(r)) for r in rows]


@app.post("/rsvps", response_model=RsvpSubmitResponse, status_code=201)
def create_rsvp(
    _: None = Depends(require_api_key),
    body: RsvpCreate = ...,
    conn: psycopg2.extensions.connection = Depends(get_db),
):
    """
    Submit an RSVP. One RSVP per (name, email) combo.
    If the same name+email already exists, replaces the old entry and returns updated=true.
    Requires X-API-Key when API_KEY is set.
    """
    # Upsert: insert or replace if (name, email) already exists
    sql = f"""
        INSERT INTO {RSVPS_TABLE} (name, email, coming, allergies, transport_assist, created_at)
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (name, email) DO UPDATE SET
            coming = EXCLUDED.coming,
            allergies = EXCLUDED.allergies,
            transport_assist = EXCLUDED.transport_assist,
            created_at = now()
        RETURNING (xmax = 0) AS inserted
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (body.name, body.email, body.coming, body.allergies, body.transport_assist),
        )
        row = cur.fetchone()
    
    was_inserted = row[0] if row else True  # xmax=0 means INSERT, otherwise UPDATE
    if was_inserted:
        return RsvpSubmitResponse(status="ok", message="RSVP submitted successfully.", updated=False)
    else:
        return RsvpSubmitResponse(status="ok", message="RSVP updated successfully.", updated=True)


@app.get("/health")
def health():
    """Simple health check (no DB)."""
    return {"status": "ok"}
