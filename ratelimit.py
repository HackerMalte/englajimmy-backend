"""
Small in-process rate limiter for the public upload endpoints.

The photo page is deliberately ungated, so these caps are the only thing
standing between the bucket and a bored stranger. Deliberately simple:
counters live in memory, so they reset on deploy and are per-instance. That is
fine for one Railway service; move to Redis if this ever runs replicated.
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

WINDOW_SECONDS = 60 * 60

# Two budgets per IP per hour, with distinct jobs:
#
#   MAX_FILES     bounds how much can land in the bucket. This is the one meant
#                 to bind, and it is sized for a guest emptying a camera roll.
#   MAX_REQUESTS  bounds request flooding, nothing else. It must stay well
#                 above what legitimate uploading costs, or it binds first and
#                 the file budget never applies.
#
# Getting one file into the bucket costs roughly 1.25 requests: uploads are
# batched four at a time for the upload-url call, then each file is recorded
# individually. So MAX_FILES files cost about MAX_FILES * 1.25 requests, and
# MAX_REQUESTS is set above that with room to spare.
#
# An earlier version capped requests at 40, which blocked real guests after
# about 32 files while the 300-file budget sat unused.
MAX_FILES = 600
MAX_REQUESTS = 1_000

_requests: dict[str, deque[float]] = defaultdict(deque)
_files: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    """
    Caller IP. Railway sits behind a proxy, so prefer the first X-Forwarded-For
    hop and fall back to the socket address locally.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune(stamps: deque[float], now: float) -> None:
    while stamps and now - stamps[0] > WINDOW_SECONDS:
        stamps.popleft()


def check_upload_quota(request: Request, file_count: int = 1) -> None:
    """
    Charge one request plus `file_count` files against this IP's hourly budget.

    Pass file_count=0 for calls that do not add a new file — recording an upload
    that was already counted when its upload URL was issued, for instance.
    Counting it twice would halve the effective file budget.

    Raises 429 with a Swedish message the frontend can show as-is.
    """
    now = time.monotonic()
    ip = client_ip(request)

    request_stamps = _requests[ip]
    file_stamps = _files[ip]
    _prune(request_stamps, now)
    _prune(file_stamps, now)

    if len(request_stamps) >= MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="För många uppladdningar just nu. Försök igen om en stund.",
        )
    if len(file_stamps) + file_count > MAX_FILES:
        raise HTTPException(
            status_code=429,
            detail="Du har laddat upp många filer den senaste timmen. Försök igen senare.",
        )

    request_stamps.append(now)
    for _ in range(file_count):
        file_stamps.append(now)
