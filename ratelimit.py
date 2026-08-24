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

# Per IP, per hour. A guest emptying a camera roll stays well under these.
MAX_PRESIGN_REQUESTS = 40
MAX_FILES = 300

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
    Raises 429 with a Swedish message the frontend can show as-is.
    """
    now = time.monotonic()
    ip = client_ip(request)

    request_stamps = _requests[ip]
    file_stamps = _files[ip]
    _prune(request_stamps, now)
    _prune(file_stamps, now)

    if len(request_stamps) >= MAX_PRESIGN_REQUESTS:
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
