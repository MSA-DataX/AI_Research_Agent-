from __future__ import annotations

import threading
import time
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse

from config import API_KEY, RATE_LIMIT_PER_MINUTE

_PUBLIC_PREFIXES = ("/docs", "/redoc", "/openapi.json")
_PUBLIC_EXACT = {"/", "/favicon.ico"}


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


_BUCKETS: dict[str, list[float]] = {}
_LOCK = threading.Lock()


def _check_rate(ip: str, limit: int, window_seconds: int = 60) -> bool:
    if limit <= 0:
        return True
    now = time.time()
    with _LOCK:
        bucket = _BUCKETS.setdefault(ip, [])
        bucket[:] = [t for t in bucket if now - t < window_seconds]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def reset_rate_limit() -> None:
    with _LOCK:
        _BUCKETS.clear()


def api_key_enabled() -> bool:
    from config import API_KEY as CURRENT_KEY
    return bool(CURRENT_KEY)


def current_rate_limit() -> int:
    from config import RATE_LIMIT_PER_MINUTE as CURRENT_LIMIT
    return CURRENT_LIMIT


def current_api_key() -> str:
    from config import API_KEY as CURRENT_KEY
    return CURRENT_KEY


async def security_middleware(request: Request, call_next):
    path = request.url.path

    if _is_public(path):
        return await call_next(request)

    key = current_api_key()
    if key:
        provided = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
        if provided != key:
            return JSONResponse(
                {"detail": {"code": "UNAUTHORIZED", "message": "invalid or missing X-API-Key"}},
                status_code=401,
            )

    limit = current_rate_limit()
    if limit > 0:
        ip = request.client.host if request.client else "unknown"
        if not _check_rate(ip, limit):
            return JSONResponse(
                {"detail": {"code": "RATE_LIMITED", "message": f"max {limit} req/min"}},
                status_code=429,
                headers={"Retry-After": "60"},
            )

    return await call_next(request)


def cors_origins() -> Iterable[str]:
    from config import CORS_ORIGINS as CURRENT
    return CURRENT
