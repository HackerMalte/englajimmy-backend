"""
Download every guest upload from the bucket to a local folder.

Pulls straight from object storage, so nothing passes through the Railway
service — no request timeouts, no memory pressure, and no double transfer.

Incremental: a file already present with a matching size is skipped, so this is
safe and cheap to re-run as more guests upload.

Files land in one folder per guest, named from the database metadata:
    Anna-Andersson/2026-08-24_1323_007.jpg
    Elise-Harrysson/2026-08-24_1401_008.jpg
Set API_KEY (and optionally API_BASE) for that; without it, everything goes to
_okand-gast with its storage key.

Usage (production credentials live in .env.prod, kept out of git):
    uv run --env-file .env.prod python download_photos.py ./wedding-photos
    uv run --env-file .env.prod python download_photos.py ./wedding-photos --dry-run
"""

import os
import re
import sys
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import storage

API_BASE = os.environ.get(
    "API_BASE", "https://englajimmy-backend-production.up.railway.app"
)
API_KEY = os.environ.get("API_KEY")
WORKERS = 8


def safe(text: str) -> str:
    """Make a name safe for a filename without mangling Swedish characters."""
    cleaned = re.sub(r"[^\w\-åäöÅÄÖ]+", "-", text, flags=re.UNICODE).strip("-")
    return cleaned[:60] or "okand"


def fetch_metadata() -> dict[str, dict]:
    """Map storage key -> row, so downloads can be named after the guest."""
    if not API_KEY:
        print("  (API_KEY not set — files will keep their storage keys)")
        return {}
    request = urllib.request.Request(
        f"{API_BASE}/photos?limit=500", headers={"X-API-Key": API_KEY}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            rows = json.load(response)
    except Exception as error:  # noqa: BLE001 - metadata is a nicety, not required
        print(f"  (could not read metadata: {error} — falling back to storage keys)")
        return {}
    return {row["storage_key"]: row for row in rows}


def local_path(key: str, row: dict | None) -> Path:
    """
    Where a file lands: one folder per guest, so you can see at a glance who
    sent what. Files with no name recorded go to _okand-gast.
    """
    extension = key.rsplit(".", 1)[-1]
    if not row:
        return Path("_okand-gast") / key.split("/")[-1]
    stamp = str(row.get("created_at", ""))[:16].replace("T", "_").replace(":", "")
    who = safe(row.get("uploader_name") or "") or "_okand-gast"
    return Path(who) / f"{stamp}_{row['id']:03d}.{extension}"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    target = Path(args[0] if args else "wedding-photos")
    target.mkdir(parents=True, exist_ok=True)

    if not storage.is_configured():
        print("S3_BUCKET is not set. Point the env at the bucket first.")
        return 1

    print(f"bucket:  {storage.S3_BUCKET}")
    print(f"target:  {target.resolve()}")
    metadata = fetch_metadata()

    objects = list(storage.iter_objects())
    total_bytes = sum(size for _key, size, _modified in objects)
    print(f"found:   {len(objects)} files, {total_bytes / 1024 / 1024:.1f} MB\n")

    planned, skipped = [], 0
    for key, size, _modified in objects:
        destination = target / local_path(key, metadata.get(key))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.stat().st_size == size:
            skipped += 1
            continue
        planned.append((key, size, destination))

    if skipped:
        print(f"skipping {skipped} already downloaded\n")

    if dry_run:
        for key, size, destination in planned:
            rel = destination.relative_to(target)
            print(f"  would download {rel}  ({size / 1024:.0f} kB)")
        print(f"\n{len(planned)} file(s) would be downloaded.")
        return 0

    if not planned:
        print("Everything is already downloaded.")
        return 0

    client = storage.get_client()
    done = 0

    def grab(item):
        key, _size, destination = item
        # Download to a temp name first, so an interrupted run cannot leave a
        # truncated file that a later run would mistake for complete.
        partial = destination.with_suffix(destination.suffix + ".part")
        client.download_file(storage.S3_BUCKET, key, str(partial))
        partial.replace(destination)
        return str(destination.relative_to(target))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for name in pool.map(grab, planned):
            done += 1
            print(f"  [{done}/{len(planned)}] {name}")

    print(f"\nDone. {done} file(s) in {target.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
