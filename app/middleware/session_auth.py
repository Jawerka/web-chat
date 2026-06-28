"""
Middleware: редирект на /login и 401 для API без сессии.
"""

from __future__ import annotations

from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.config import settings
from app.security.trusted_internal import is_trusted_internal_request
from app.services.auth_service import session_user_id_from_request

_PUBLIC_PREFIXES = (
    "/static/",
    "/favicon.ico",
    "/login",
    "/api/auth/login",
    "/api/auth/logout",
)

_PUBLIC_EXACT = (
    "/api/health",
    "/api/health/logs",
    "/api/config",
)


def _is_public_path(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def _is_sd_bridge_token_fetch(request: Request) -> bool:
    """SD extension on .52: одноразовый token вместо cookie."""
    return request.method == "GET" and request.url.path.startswith("/api/sd-bridge/import/")


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """Требовать сессию для HTML и REST (кроме публичных путей)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.auth_enabled:
            return await call_next(request)

        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        if _is_sd_bridge_token_fetch(request):
            return await call_next(request)

        if is_trusted_internal_request(request):
            return await call_next(request)

        if session_user_id_from_request(request) is not None:
            return await call_next(request)

        if path.startswith("/api/") or path.startswith("/media/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Требуется вход", "code": "auth_required"},
            )

        if path.startswith("/ws/"):
            return await call_next(request)

        next_url = quote(path)
        return RedirectResponse(url=f"/login?next={next_url}", status_code=302)
