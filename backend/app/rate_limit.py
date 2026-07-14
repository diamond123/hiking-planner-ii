import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.constants import RATE_LIMIT_PER_SECOND

WINDOW_SECONDS = 1.0

# Per-process, in-memory sliding-window log keyed by client host. Only correct
# for a single uvicorn worker; a multi-worker deployment would need a shared
# store (e.g. Redis) since each worker would otherwise count independently.
_request_log: dict[str, deque[float]] = defaultdict(deque)


async def enforce_rate_limit(request: Request) -> None:
    client_id = request.client.host if request.client else "unknown"
    now = time.monotonic()
    log = _request_log[client_id]

    while log and now - log[0] > WINDOW_SECONDS:
        log.popleft()

    if len(log) >= RATE_LIMIT_PER_SECOND:
        raise HTTPException(
            status_code=429,
            detail="Too many requests, please slow down.",
            headers={"Retry-After": "1"},
        )

    log.append(now)
