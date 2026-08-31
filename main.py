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
    PhotoPublicOut,
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
    thumb_key        VARCHAR(500),
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
    """Create the photos table if it doesn't exist, and add newer columns."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_PHOTOS_SQL)
            cur.execute(
                "ALTER TABLE photos ADD COLUMN IF NOT EXISTS thumb_key VARCHAR(500)"
            )


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
    # file_count=0: this file was already counted against the file budget when
    # its upload URL was issued. Counting it again would halve the budget.
    check_upload_quota(request, file_count=0)

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

    # The thumbnail is a nicety: verify it like the main file, but a missing or
    # bad one must never sink the photo itself — record without it instead.
    thumb_key = body.thumb_key
    if thumb_key is not None:
        if not thumb_key.startswith(f"{storage.KEY_PREFIX}/") or thumb_key == body.storage_key:
            thumb_key = None
        elif storage.verify_stored_object(thumb_key) is None:
            thumb_key = None

    sql = f"""
        INSERT INTO {PHOTOS_TABLE}
            (storage_key, thumb_key, uploader_name, caption, content_type, size_bytes,
             width, height, duration_seconds, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (storage_key) DO UPDATE SET
            thumb_key = COALESCE(EXCLUDED.thumb_key, {PHOTOS_TABLE}.thumb_key),
            uploader_name = EXCLUDED.uploader_name,
            caption = EXCLUDED.caption
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(
            sql,
            (
                body.storage_key,
                thumb_key,
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
            f"""SELECT id, storage_key, thumb_key, uploader_name, caption, content_type,
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
        if data["thumb_key"]:
            data["thumb_url"] = storage.create_download_url(data["thumb_key"])
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
            f"DELETE FROM {PHOTOS_TABLE} WHERE id = %s RETURNING storage_key, thumb_key",
            (photo_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Bilden hittades inte.")

    storage.delete_object(row[0])
    if row[1]:
        storage.delete_object(row[1])


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
        cur.execute(f"SELECT storage_key, thumb_key FROM {PHOTOS_TABLE}")
        known = set()
        for storage_key, thumb_key in cur.fetchall():
            known.add(storage_key)
            if thumb_key:
                known.add(thumb_key)

    orphans = [
        key
        for key, _size, last_modified in storage.iter_objects()
        if key not in known and last_modified < cutoff
    ]
    removed = storage.delete_objects(orphans)
    return {"status": "ok", "deleted": removed}


@app.get("/photos/gallery", response_model=list[PhotoPublicOut])
def list_gallery(
    _: None = Depends(require_storage),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn: psycopg2.extensions.connection = Depends(get_db),
):
    """
    Public gallery listing. Anyone can read this.

    Returns only what the grid needs to draw itself — no uploader names, no
    captions, no sizes, no timestamps. Separate from GET /photos, which stays
    admin-only and carries the full metadata.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, storage_key, thumb_key, content_type, width, height
                FROM {PHOTOS_TABLE}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s""",
            (limit, offset),
        )
        rows = cur.fetchall()

    return [
        PhotoPublicOut(
            id=row[0],
            url=storage.create_download_url(row[1]),
            thumb_url=storage.create_download_url(row[2]) if row[2] else None,
            content_type=row[3],
            width=row[4],
            height=row[5],
        )
        for row in rows
    ]


THUMB_MAX_DIMENSION = 640
THUMB_JPEG_QUALITY = 72


@app.post("/photos/backfill-thumbnails")
def backfill_thumbnails(
    _: None = Depends(require_api_key),
    __: None = Depends(require_storage),
    limit: int = Query(25, ge=1, le=100),
    conn: psycopg2.extensions.connection = Depends(get_db),
):
    """
    Generate thumbnails for photos uploaded before thumbnails existed.

    Deliberately loss-proof: originals are only ever read, thumbnails are
    written under fresh random keys (so nothing can be overwritten), and a
    row's thumb_key is set only after the new object is confirmed present in
    the bucket. Only rows without a thumbnail are touched, and the only column
    written is thumb_key — so the run is idempotent and safe to repeat or
    abort at any point. A photo that fails is skipped and reported, never
    deleted.

    Videos are skipped: extracting a poster frame needs ffmpeg, which this
    service does not carry. Their tiles fall back to the full file.

    Processes up to `limit` rows per call and reports how many remain, so no
    single request runs long enough to hit a proxy timeout. Admin only.
    """
    import io

    from PIL import Image, ImageOps

    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, storage_key, content_type FROM {PHOTOS_TABLE}
                WHERE thumb_key IS NULL AND content_type LIKE 'image/%%'
                ORDER BY id
                LIMIT %s""",
            (limit,),
        )
        candidates = cur.fetchall()

    generated, failed = 0, []
    for photo_id, storage_key, _content_type in candidates:
        try:
            original = storage.read_object(storage_key)
            image = Image.open(io.BytesIO(original))
            # Phone photos carry their rotation in EXIF; bake it in so the
            # thumbnail is not sideways.
            image = ImageOps.exif_transpose(image)
            image.thumbnail((THUMB_MAX_DIMENSION, THUMB_MAX_DIMENSION))
            buffer = io.BytesIO()
            image.convert("RGB").save(
                buffer, format="JPEG", quality=THUMB_JPEG_QUALITY, optimize=True
            )
            thumb_key = storage.upload_bytes(buffer.getvalue(), "image/jpeg")

            # Belt and braces: point the row at the thumb only once the bucket
            # confirms the object landed.
            exists, _size = storage.object_exists(thumb_key)
            if not exists:
                failed.append(photo_id)
                continue

            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {PHOTOS_TABLE} SET thumb_key = %s WHERE id = %s AND thumb_key IS NULL",
                    (thumb_key, photo_id),
                )
            generated += 1
        except Exception:  # noqa: BLE001 - one bad file must not stop the rest
            failed.append(photo_id)

    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT count(*) FROM {PHOTOS_TABLE}
                WHERE thumb_key IS NULL AND content_type LIKE 'image/%%'"""
        )
        remaining = cur.fetchone()[0]

    return {"status": "ok", "generated": generated, "remaining": remaining, "failed": failed}
