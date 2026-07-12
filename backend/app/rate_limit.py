"""Small process-local fixed-window limits for security-sensitive bootstrap endpoints.

These limits are deliberately dependency-free and conservative. Deployments with more than
one API process should additionally enforce distributed limits at their reverse proxy; the
application limit remains a last line of defence for every process.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException

_LOCK = threading.Lock()
_HITS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def check_rate_limit(scope: str, key: str, *, limit: int, window_seconds: int) -> None:
    """Raise a 429 after ``limit`` attempts for ``scope``/``key`` in the window."""
    now = time.monotonic()
    cutoff = now - window_seconds
    bucket_key = (scope, key or "unknown")
    with _LOCK:
        hits = _HITS[bucket_key]
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, int(window_seconds - (now - hits[0]) + 0.999))
            raise HTTPException(
                429,
                "Too many requests. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)


def reset_rate_limits() -> None:
    """Test helper; production code never resets security counters."""
    with _LOCK:
        _HITS.clear()
