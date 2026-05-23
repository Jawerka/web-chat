"""
Проверка API key и Origin для REST / WebSocket.
"""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse

from fastapi import HTTPException, Request, WebSocket, WebSocketException, status

from app.config import settings


def client_ip_from_request(request: Request | WebSocket) -> str:
    """IP клиента с учётом доверенного proxy."""
    if isinstance(request, WebSocket):
        client = request.client
        forwarded = request.headers.get("x-forwarded-for")
    else:
        client = request.client
        forwarded = request.headers.get("x-forwarded-for")

    proxies = settings.trusted_proxy_ip_set()
    if forwarded and proxies:
        first = forwarded.split(",")[0].strip()
        if client and client.host in proxies:
            return first
    if client and client.host:
        return client.host
    return "unknown"


def check_api_key(request: Request | WebSocket) -> None:
    """
    Проверить ключ доступа, если задан API_ACCESS_KEY в .env.

    Пустой ключ — проверка отключена (режим доверенной LAN).
    """
    required = (settings.api_access_key or "").strip()
    if not required:
        return

    provided = ""
    if isinstance(request, WebSocket):
        provided = (request.headers.get("x-api-key") or "").strip()
        if not provided:
            provided = (request.query_params.get("api_key") or "").strip()
    else:
        provided = (request.headers.get("x-api-key") or "").strip()
        if not provided:
            auth = request.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()

    if provided != required:
        if isinstance(request, WebSocket):
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Неверный или отсутствующий API key",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий API key",
        )


def check_ws_origin(websocket: WebSocket) -> None:
    """
    Проверить заголовок Origin для WebSocket (CSWSH).

    Если TRUSTED_WS_ORIGINS пуст — проверка отключена.
    """
    allowed = settings.trusted_ws_origins_list()
    if not allowed:
        return

    origin = (websocket.headers.get("origin") or "").strip()
    if not origin:
        # Некоторые клиенты не шлют Origin — разрешаем same-host запросы без Origin
        host = (websocket.headers.get("host") or "").strip()
        if host:
            for base in (settings.public_base_url, settings.public_base_url_vpn):
                if not base:
                    continue
                parsed = urlparse(base)
                if parsed.netloc == host:
                    return
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Origin не указан",
        )

    if origin not in allowed:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Origin не разрешён",
        )


def is_trusted_proxy_ip(host: str | None) -> bool:
    """Клиент за доверенным reverse proxy."""
    if not host:
        return False
    return host in settings.trusted_proxy_ip_set()
