"""
Middleware: API key и rate limit для REST.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.security.access import check_api_key, client_ip_from_request
from app.security.rate_limit import RateLimitExceeded, check_rate_limit

logger = logging.getLogger(__name__)

_RATE_LIMITED_PREFIXES = (
    "/api/upload",
    "/api/conversations",
    "/api/gallery",
)


class AccessControlMiddleware(BaseHTTPMiddleware):
    """Проверка ключа и лимита запросов для API."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        if path.startswith("/api/"):
            from fastapi import HTTPException

            try:
                check_api_key(request)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                )

            if request.method != "GET" or path.startswith("/api/upload"):
                if any(path.startswith(p) for p in _RATE_LIMITED_PREFIXES):
                    ip = client_ip_from_request(request)
                    bucket = f"{ip}:{path.split('/')[2] if len(path.split('/')) > 2 else 'api'}"
                    try:
                        check_rate_limit(bucket)
                    except RateLimitExceeded as rl:
                        return JSONResponse(
                            status_code=429,
                            content={
                                "detail": str(rl),
                                "code": "rate_limit_error",
                                "retry_after_sec": rl.retry_after_sec,
                            },
                            headers={"Retry-After": str(int(rl.retry_after_sec) + 1)},
                        )

        return await call_next(request)
