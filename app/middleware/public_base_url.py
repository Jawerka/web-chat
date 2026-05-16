"""Middleware: выбор PUBLIC_BASE_URL (LAN/VPN) по Host и IP клиента."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.public_url import bind_request_public_base_url, reset_request_public_base_url


class PublicBaseUrlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        token = bind_request_public_base_url(
            host=request.headers.get("host"),
            client_host=request.client.host if request.client else None,
            forwarded_host=request.headers.get("x-forwarded-host"),
            forwarded_proto=request.headers.get("x-forwarded-proto"),
        )
        try:
            return await call_next(request)
        finally:
            reset_request_public_base_url(token)
