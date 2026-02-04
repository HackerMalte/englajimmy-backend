"""
FastAPI app for RSVP: query and upload data from a frontend RSVP page.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # Load .env so API_KEY and DATABASE_URL work when testing locally

import psycopg2
from psycopg2 import IntegrityError
from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader

from db.connection import get_conn
from schemas.input import RsvpCreate, RsvpOut
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
    email           VARCHAR(255) NOT NULL UNIQUE,
    coming          BOOLEAN DEFAULT true,
    allergies       VARCHAR(500),
    transport_assist BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now()
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
            
            # Ensure one RSVP per email: add unique constraint if not already present
            cur.execute("""
                SELECT 1 FROM pg_constraint 
                WHERE conrelid = 'rsvps'::regclass AND conname = 'rsvps_email_key'
            """)
            if cur.fetchone() is None:
                cur.execute("ALTER TABLE rsvps ADD CONSTRAINT rsvps_email_key UNIQUE (email)")


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


@app.post("/rsvps", response_model=RsvpOut, status_code=201)
def create_rsvp(body: RsvpCreate, conn: psycopg2.extensions.connection = Depends(get_db)):
    """Submit an RSVP from the frontend form. One RSVP per email."""
    columns = ", ".join(RSVP_COLUMNS_INSERT)
    placeholders = ", ".join(["%s"] * len(RSVP_COLUMNS_INSERT))
    sql = f"INSERT INTO {RSVPS_TABLE} ({columns}) VALUES ({placeholders}) RETURNING id, name, email, coming, allergies, transport_assist, created_at"
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (body.name, body.email, body.coming, body.allergies, body.transport_assist),
            )
            row = cur.fetchone()
    except IntegrityError as e:
        if e.pgcode == "23505":  # unique_violation
            raise HTTPException(
                status_code=409,
                detail="An RSVP with this email address has already been submitted.",
            ) from e
        raise
    if not row:
        raise HTTPException(status_code=500, detail="Insert failed")
    return RsvpOut(**row_to_rsvp(row))


@app.get("/health")
def health():
    """Simple health check (no DB)."""
    return {"status": "ok"}
