"""
Generate thumbnails for photos uploaded before thumbnails existed.

Runs on your machine, not on the server. That is deliberate: this is a one-off
job, and putting an image library into the API's dependencies is what took the
service down on 2026-08-31. Pillow is pulled in per-run by uv, so it never
becomes a deployed dependency.

Loss-proof by construction:
  - originals are only ever READ; no code path here writes or deletes one
  - thumbnails are written under fresh random keys, so nothing can be overwritten
  - a photo's row is only updated after the bucket confirms the thumbnail landed
  - the only column written is thumb_key
  - photos that already have a thumbnail are skipped, so re-running is safe
  - a photo that fails is reported and skipped, never modified

Videos are skipped: their poster frames come from the browser at upload time.

Usage:
    uv run --with pillow --env-file .env.prod python backfill_thumbnails.py --dry-run
    uv run --with pillow --env-file .env.prod python backfill_thumbnails.py
"""

import io
import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

import storage

API_BASE = os.environ.get(
    "API_BASE", "https://englajimmy-backend-production.up.railway.app"
)
API_KEY = os.environ.get("API_KEY")

THUMB_MAX_DIMENSION = 640
THUMB_JPEG_QUALITY = 72


def api(method: str, path: str, payload: dict | None = None) -> dict:
    request = urllib.request.Request(
        API_BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY or ""},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def make_thumbnail(original: bytes) -> bytes:
    from PIL import Image, ImageOps

    image = Image.open(io.BytesIO(original))
    # Phone photos carry rotation in EXIF; bake it in so thumbnails are upright.
    image = ImageOps.exif_transpose(image)
    image.thumbnail((THUMB_MAX_DIMENSION, THUMB_MAX_DIMENSION))
    buffer = io.BytesIO()
    image.convert("RGB").save(
        buffer, format="JPEG", quality=THUMB_JPEG_QUALITY, optimize=True
    )
    return buffer.getvalue()


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if not API_KEY:
        print("API_KEY is not set. Use --env-file .env.prod.")
        return 1
    if not storage.is_configured():
        print("S3_BUCKET is not set. Use --env-file .env.prod.")
        return 1

    print(f"bucket: {storage.S3_BUCKET}")
    print(f"api:    {API_BASE}\n")

    photos = api("GET", "/photos?limit=500")
    pending = [
        p
        for p in photos
        if not p.get("thumb_key") and str(p.get("content_type", "")).startswith("image/")
    ]
    videos = [p for p in photos if str(p.get("content_type", "")).startswith("video/")]

    print(f"{len(photos)} photos total")
    print(f"{len(pending)} need a thumbnail")
    print(f"{len(videos)} videos skipped (posters come from the browser)\n")

    if dry_run:
        for photo in pending:
            print(f"  would process id={photo['id']} ({photo['size_bytes'] / 1024:.0f} kB)")
        return 0

    # Inventory before, so the run can prove it added and changed nothing else.
    before = {key: size for key, size, _ in storage.iter_objects()}

    done, failed = 0, []
    for photo in pending:
        try:
            original = storage.read_object(photo["storage_key"])
            thumb = make_thumbnail(original)
            thumb_key = storage.upload_bytes(thumb, "image/jpeg")
            api("PATCH", f"/photos/{photo['id']}/thumbnail", {"thumb_key": thumb_key})
            done += 1
            print(
                f"  [{done}/{len(pending)}] id={photo['id']}  "
                f"{photo['size_bytes'] / 1024:.0f} kB -> {len(thumb) / 1024:.0f} kB"
            )
        except Exception as error:  # noqa: BLE001 - one bad file must not stop the rest
            failed.append((photo["id"], str(error)[:80]))

    after = {key: size for key, size, _ in storage.iter_objects()}
    missing = [k for k in before if k not in after]
    changed = [k for k in before if k in after and after[k] != before[k]]
    added = len([k for k in after if k not in before])

    print(f"\nthumbnails created: {done}")
    print(f"originals missing:  {len(missing)}   (must be 0)")
    print(f"originals changed:  {len(changed)}   (must be 0)")
    print(f"objects added:      {added}")
    if failed:
        print(f"\nfailed ({len(failed)}), left untouched:")
        for photo_id, message in failed:
            print(f"  id={photo_id}: {message}")
    if missing or changed:
        print("\nDATA LOSS DETECTED — stop and investigate.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
