"""
FastAPI app for RSVP: query and upload data from a frontend RSVP page.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # Load .env so API_KEY and DATABASE_URL work when testing locally

import psycopg2
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader

import storage
from db.connection import get_conn
from ratelimit import check_upload_quota
from schemas.input import (
    PhotoCountOut,
    PhotoCreate,
    PhotoCreateResponse,
    PhotoOut,
    PhotoUploadRequest,
    PhotoUploadResponse,
    PhotoUploadTarget,
    RsvpCreate,
    RsvpOut,
    RsvpSubmitResponse,
)
from schemas.db import (
    PHOTOS_TABLE,
    RSVPS_TABLE,
    RSVP_COLUMNS_INSERT,
    row_to_photo,
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
    ensure_photos_table()
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


# ----- Photos: guest uploads -----

CREATE_PHOTOS_SQL = """
CREATE TABLE IF NOT EXISTS photos (
    id               SERIAL PRIMARY KEY,
    storage_key      VARCHAR(500) NOT NULL UNIQUE,
    uploader_name    VARCHAR(255),
    caption          VARCHAR(500),
    content_type     VARCHAR(100) NOT NULL,
    size_bytes       BIGINT NOT NULL,
    width            INTEGER,
    height           INTEGER,
    duration_seconds REAL,
    created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS photos_created_at_idx ON photos (created_at DESC);
"""


def ensure_photos_table():
    """Create the photos table if it doesn't exist."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_PHOTOS_SQL)


def require_storage() -> None:
    """Dependency: photo endpoints need a bucket; RSVP works without one."""
    if not storage.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Bilduppladdning är inte konfigurerad än.",
        )


@app.post("/photos/upload-urls", response_model=PhotoUploadResponse)
def create_upload_urls(
    request: Request,
    body: PhotoUploadRequest,
    _: None = Depends(require_storage),
):
    """
    Hand out presigned upload forms so the browser can POST files straight to
    the bucket. Public (the photo page is ungated), so it is rate limited and
    every file is checked against the type allowlist and size ceiling here as
    well as in the bucket policy.
    """
    check_upload_quota(request, file_count=len(body.files))

    targets: list[PhotoUploadTarget] = []
    for item in body.files:
        content_type = item.content_type.split(";")[0].strip().lower()
        if content_type not in storage.ALLOWED_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Filtypen stöds inte: {content_type or 'okänd'}",
            )
        limit = storage.max_bytes_for(content_type)
        if item.size_bytes > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Filen är för stor (max {limit // (1024 * 1024)} MB).",
            )
        targets.append(PhotoUploadTarget(**storage.create_upload_form(content_type)))

    return PhotoUploadResponse(targets=targets)


@app.post("/photos", response_model=PhotoCreateResponse, status_code=201)
def create_photo(
    request: Request,
    body: PhotoCreate,
    _: None = Depends(require_storage),
    conn: psycopg2.extensions.connection = Depends(get_db),
):
    """
    Record a file that finished uploading. The object is verified against the
    bucket first, so a caller cannot invent rows for files that do not exist,
    and the stored size is the bucket's rather than the client's claim.
    """
    check_upload_quota(request)

    if not body.storage_key.startswith(f"{storage.KEY_PREFIX}/"):
        raise HTTPException(status_code=400, detail="Ogiltig fil-referens.")

    exists, size_bytes = storage.object_exists(body.storage_key)
    if not exists:
        raise HTTPException(status_code=404, detail="Filen hittades inte i lagringen.")

    # The presigned policy pins the declared type but cannot read the bytes, so
    # confirm the file really is media before recording it. Junk is deleted.
    content_type = storage.verify_stored_object(body.storage_key)
    if content_type is None:
        raise HTTPException(
            status_code=415,
            detail="Filen verkar inte vara en bild eller film.",
        )

    sql = f"""
        INSERT INTO {PHOTOS_TABLE}
            (storage_key, uploader_name, caption, content_type, size_bytes,
             width, height, duration_seconds, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (storage_key) DO UPDATE SET
            uploader_name = EXCLUDED.uploader_name,
            caption = EXCLUDED.caption
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                body.storage_key,
                body.uploader_name,
                body.caption,
                content_type,
                size_bytes,
                body.width,
                body.height,
                body.duration_seconds,
            ),
        )
        row = cur.fetchone()

    return PhotoCreateResponse(id=row[0])


@app.get("/photos/count", response_model=PhotoCountOut)
def count_photos(conn: psycopg2.extensions.connection = Depends(get_db)):
    """
    Public counter. Lets the upload page show how many files have arrived
    without exposing any of them.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {PHOTOS_TABLE}")
        row = cur.fetchone()
    return PhotoCountOut(count=row[0] if row else 0)


@app.get("/photos", response_model=list[PhotoOut])
def list_photos(
    _: None = Depends(require_api_key),
    __: None = Depends(require_storage),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn: psycopg2.extensions.connection = Depends(get_db),
):
    """
    List uploads with temporary view URLs. Admin only: requires X-API-Key when
    API_KEY is set. This is what the couple sees; guests never call it.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, storage_key, uploader_name, caption, content_type,
                       size_bytes, width, height, duration_seconds, created_at
                FROM {PHOTOS_TABLE}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s""",
            (limit, offset),
        )
        rows = cur.fetchall()

    photos = []
    for r in rows:
        data = row_to_photo(r)
        data["url"] = storage.create_download_url(data["storage_key"])
        photos.append(PhotoOut(**data))
    return photos


@app.delete("/photos/{photo_id}", status_code=204)
def delete_photo(
    photo_id: int,
    _: None = Depends(require_api_key),
    __: None = Depends(require_storage),
    conn: psycopg2.extensions.connection = Depends(get_db),
):
    """Delete an upload from both the bucket and the table. Admin only."""
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {PHOTOS_TABLE} WHERE id = %s RETURNING storage_key",
            (photo_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Bilden hittades inte.")

    storage.delete_object(row[0])


@app.post("/photos/cleanup-orphans")
def cleanup_orphans(
    _: None = Depends(require_api_key),
    __: None = Depends(require_storage),
    conn: psycopg2.extensions.connection = Depends(get_db),
):
    """
    Delete bucket objects that have no database row.

    A presigned form lets the browser write straight to the bucket, so an
    upload that is abandoned — or pushed by someone skipping POST /photos to
    dodge the content check — leaves a file nothing references. Objects younger
    than the presign TTL are left alone so uploads still in flight survive.
    Admin only; safe to run on a schedule.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=storage.UPLOAD_URL_TTL_SECONDS
    )

    with conn.cursor() as cur:
        cur.execute(f"SELECT storage_key FROM {PHOTOS_TABLE}")
        known = {row[0] for row in cur.fetchall()}

    orphans = [
        key
        for key, _size, last_modified in storage.iter_objects()
        if key not in known and last_modified < cutoff
    ]
    removed = storage.delete_objects(orphans)
    return {"status": "ok", "deleted": removed}
