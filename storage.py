"""
S3-compatible object storage for guest photo/video uploads.

Local dev points at MinIO; production points at a Railway Storage Bucket.
Both speak S3, so only the env vars change:

    S3_BUCKET         bucket name
    S3_ENDPOINT_URL   S3 API endpoint (omit for real AWS)
    S3_REGION         defaults to us-east-1
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY

Uploads never pass through this API: the browser POSTs straight to the bucket
with a presigned form, so a 200 MB video never occupies server memory.
"""

import os
import uuid
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

S3_BUCKET = os.environ.get("S3_BUCKET")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")

# How long a guest has to finish an upload once the form is issued.
#
# Generous on purpose: a 1 GB video over venue wifi at 2 Mbps takes over an
# hour, and the old one-hour window expired mid-upload — failing at the very
# end, after the guest had already waited. The policy still pins the key, the
# content type and the size, so a longer-lived form grants nothing extra.
UPLOAD_URL_TTL_SECONDS = 6 * 60 * 60
# How long an admin gallery link stays valid.
DOWNLOAD_URL_TTL_SECONDS = 60 * 60 * 6

KEY_PREFIX = "photos"

# Only these reach the bucket. HEIC/HEIF are here because iPhones still produce
# them; the frontend converts to JPEG when the browser can decode it.
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/avif",
    "image/heic",
    "image/heif",
}
ALLOWED_VIDEO_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
}
ALLOWED_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES

EXTENSION_BY_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
    "image/heic": "heic",
    "image/heif": "heif",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
}

MIN_UPLOAD_BYTES = 1024                      # 1 KB — reject empty/garbage
MAX_IMAGE_BYTES = 25 * 1024 * 1024           # 25 MB — well above any phone photo

# 2 GB, chosen so five minutes of video fits at any setting a phone is likely
# to be on.
#
# Measured from the videos guests have actually uploaded, all 1080p or 720p:
# about 110 MB per minute, so five minutes is roughly 570 MB and this leaves
# ample room. The reason for going beyond that is 4K — nobody has used it yet,
# but 4K/30 runs around 375 MB per minute, which puts five minutes at about
# 1.9 GB. One guest with that setting on would otherwise be cut off mid-speech.
#
# 4K/60 (around 750 MB per minute) still will not fit five minutes, and cannot
# reasonably: that is nearly 4 GB, over an hour of uploading on mobile data. The
# too-large message says what to do instead.
#
# The practical ceiling here is patience, not storage. At a typical 8 Mbps
# uplink 2 GB takes about half an hour, and an interrupted upload restarts.
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024


class StorageNotConfigured(RuntimeError):
    """Raised when photo endpoints are hit but no bucket is configured."""


def is_configured() -> bool:
    """True when a bucket is set up, so RSVP keeps working without storage."""
    return bool(S3_BUCKET)


def max_bytes_for(content_type: str) -> int:
    """Per-type size ceiling, enforced by the bucket via the presigned policy."""
    return MAX_VIDEO_BYTES if content_type in ALLOWED_VIDEO_TYPES else MAX_IMAGE_BYTES


@lru_cache(maxsize=1)
def get_client():
    """Cached S3 client. Signature v4 is required by MinIO and Railway buckets."""
    if not is_configured():
        raise StorageNotConfigured(
            "S3_BUCKET is not set. Set it (plus credentials) to enable photo uploads."
        )
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL or None,
        region_name=S3_REGION,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def build_key(content_type: str) -> str:
    """
    Random key per upload: guests cannot guess or overwrite each other's files.
    The original filename is deliberately discarded (it can carry anything).
    """
    extension = EXTENSION_BY_TYPE.get(content_type, "bin")
    return f"{KEY_PREFIX}/{uuid.uuid4().hex}.{extension}"


def create_upload_form(content_type: str) -> dict:
    """
    Presigned POST the browser can submit directly to the bucket.

    The policy — not our code — enforces the exact key, the content type, and
    the size range, so a tampered client cannot escape the limits.
    """
    key = build_key(content_type)
    presigned = get_client().generate_presigned_post(
        Bucket=S3_BUCKET,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", MIN_UPLOAD_BYTES, max_bytes_for(content_type)],
        ],
        ExpiresIn=UPLOAD_URL_TTL_SECONDS,
    )
    return {"key": key, "url": presigned["url"], "fields": presigned["fields"]}


def object_exists(key: str) -> tuple[bool, int]:
    """
    Confirm an object really landed before we write a DB row.
    Returns (exists, size_bytes) so the recorded size is the bucket's, not the
    client's claim.
    """
    try:
        head = get_client().head_object(Bucket=S3_BUCKET, Key=key)
    except ClientError:
        return False, 0
    return True, int(head.get("ContentLength", 0))


def create_download_url(key: str) -> str:
    """Temporary read URL for the admin gallery. Bucket stays private."""
    return get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=DOWNLOAD_URL_TTL_SECONDS,
    )


def delete_object(key: str) -> None:
    """Remove an object from the bucket (admin delete)."""
    get_client().delete_object(Bucket=S3_BUCKET, Key=key)


# ----- Content sniffing -----
#
# A presigned policy can pin the *declared* content type but cannot look at the
# bytes, so a caller can upload anything under an image/jpeg form. Since the
# upload endpoint is public, we read the first bytes back out of the bucket and
# confirm the file really is what it claims before recording it.

_ISO_BMFF_BRANDS = {
    "heic": "image/heic", "heix": "image/heic", "hevc": "image/heic",
    "heim": "image/heic", "heis": "image/heic", "hevm": "image/heic",
    "hevs": "image/heic", "mif1": "image/heif", "msf1": "image/heif",
    "avif": "image/avif", "avis": "image/avif",
    "isom": "video/mp4", "iso2": "video/mp4", "iso4": "video/mp4",
    "iso5": "video/mp4", "iso6": "video/mp4", "mp41": "video/mp4",
    "mp42": "video/mp4", "avc1": "video/mp4", "dash": "video/mp4",
    "mmp4": "video/mp4", "m4v ": "video/mp4",
    "qt  ": "video/quicktime",
}

SNIFF_BYTES = 32


def sniff_content_type(head: bytes) -> str | None:
    """
    Identify a file from its leading bytes. Returns an allowed MIME type, or
    None when the bytes are not a media file we accept.
    """
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if head[4:8] == b"ftyp":
        brand = head[8:12].decode("ascii", errors="replace").lower()
        return _ISO_BMFF_BRANDS.get(brand)
    return None


def read_head(key: str, num_bytes: int = SNIFF_BYTES) -> bytes:
    """Fetch just the first bytes of an object — cheap enough to do per upload."""
    response = get_client().get_object(
        Bucket=S3_BUCKET, Key=key, Range=f"bytes=0-{num_bytes - 1}"
    )
    try:
        return response["Body"].read()
    finally:
        response["Body"].close()


def verify_stored_object(key: str) -> str | None:
    """
    Sniff an uploaded object. Returns the real content type, or None if the
    bytes are not acceptable media — in which case the object is deleted so
    junk cannot accumulate in the bucket.
    """
    try:
        head = read_head(key)
    except ClientError:
        return None

    content_type = sniff_content_type(head)
    if content_type is None or content_type not in ALLOWED_TYPES:
        try:
            delete_object(key)
        except ClientError:
            pass
        return None
    return content_type


def iter_objects():
    """Yield (key, size_bytes, last_modified) for every object under the prefix."""
    paginator = get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=f"{KEY_PREFIX}/"):
        for obj in page.get("Contents", []):
            yield obj["Key"], int(obj["Size"]), obj["LastModified"]


def delete_objects(keys: list[str]) -> int:
    """Bulk delete. Returns how many were removed."""
    if not keys:
        return 0
    client = get_client()
    removed = 0
    for start in range(0, len(keys), 1000):  # S3 caps delete_objects at 1000
        batch = keys[start : start + 1000]
        client.delete_objects(
            Bucket=S3_BUCKET,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )
        removed += len(batch)
    return removed


def read_object(key: str) -> bytes:
    """Fetch a whole object. Used by the thumbnail backfill; read-only."""
    response = get_client().get_object(Bucket=S3_BUCKET, Key=key)
    try:
        return response["Body"].read()
    finally:
        response["Body"].close()


def upload_bytes(data: bytes, content_type: str) -> str:
    """
    Store new bytes under a fresh random key and return the key.

    Always a brand-new UUID key: this can add objects to the bucket but can
    never overwrite one that exists.
    """
    key = build_key(content_type)
    get_client().put_object(
        Bucket=S3_BUCKET, Key=key, Body=data, ContentType=content_type
    )
    return key
