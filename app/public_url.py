"""
Публичные базовые URL для медиа: LAN и VPN.

Браузер получает URL по Host / сети клиента (contextvar).
LLM и внутренние fetch — всегда LAN (доступ с хоста LLM в 192.168.88.0/24).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from app.config import settings

_request_base: ContextVar[str | None] = ContextVar("request_public_base_url", default=None)

_LAN_NET = ip_network("192.168.88.0/24")
_VPN_NET = ip_network("10.99.99.0/24")


def public_base_url_lan() -> str:
    """Основной URL (LAN), из PUBLIC_BASE_URL."""
    return settings.public_base_url.rstrip("/")


def public_base_url_vpn() -> str | None:
    """VPN URL из PUBLIC_BASE_URL_VPN; None — только LAN."""
    raw = (settings.public_base_url_vpn or "").strip()
    return raw.rstrip("/") if raw else None


def all_public_base_urls() -> list[str]:
    """Все настроенные базовые URL (без дубликатов)."""
    lan = public_base_url_lan()
    vpn = public_base_url_vpn()
    if vpn and vpn != lan:
        return [lan, vpn]
    return [lan]


def public_base_url_for_llm() -> str:
    """Базовый URL для LLM vision и img2img (хост LLM в LAN)."""
    return public_base_url_lan()


def resolve_public_base_url(*, for_llm: bool = False) -> str:
    """
    Базовый URL для сборки абсолютных ссылок на /media/…

    for_llm=True — всегда LAN.
    Иначе — из contextvar (HTTP/WS запрос) или LAN по умолчанию.
    """
    if for_llm:
        return public_base_url_for_llm()
    ctx = _request_base.get()
    if ctx:
        return ctx.rstrip("/")
    return public_base_url_lan()


def absolute_media_path(path: str, *, for_llm: bool = False) -> str:
    """Относительный /media/… → полный URL."""
    if not path.startswith("/media/"):
        return path
    return f"{resolve_public_base_url(for_llm=for_llm)}{path}"


def strip_public_base(url: str) -> str:
    """Убрать известный PUBLIC_BASE (LAN или VPN), вернуть путь или исходник."""
    for base in all_public_base_urls():
        if url.startswith(base):
            suffix = url[len(base) :]
            return suffix if suffix.startswith("/") else f"/{suffix}"
    return url


def is_trusted_media_url(url: str) -> bool:
    """URL указывает на /media/… этого сервера (любой настроенный base или относительный)."""
    if url.startswith("/media/"):
        return True
    return any(url.startswith(base) for base in all_public_base_urls())


def _host_only(host: str | None) -> str:
    if not host:
        return ""
    return host.split(",")[0].strip().split(":")[0].lower()


def _host_from_base(base: str) -> str:
    return (_host_only(urlparse(base).hostname) or "").lower()


def _ip_in_net(host: str, network: ip_network) -> bool:
    try:
        return ip_address(host) in network
    except ValueError:
        return False


def _pick_base_for_host(host: str | None, client_host: str | None) -> str:
    lan = public_base_url_lan()
    vpn = public_base_url_vpn()
    host_only = _host_only(host)
    client_only = _host_only(client_host)

    if vpn:
        if host_only and host_only == _host_from_base(vpn):
            return vpn
        if client_only and _ip_in_net(client_only, _VPN_NET):
            return vpn

    if host_only and host_only == _host_from_base(lan):
        return lan
    if client_only and _ip_in_net(client_only, _LAN_NET):
        return lan

    if host:
        scheme = "http"
        port = settings.web_port
        if ":" in host.split(",")[0].strip():
            return f"{scheme}://{host.split(',')[0].strip()}"
        if port not in (80, 443):
            return f"{scheme}://{host_only}:{port}"
        return f"{scheme}://{host_only}"

    return lan


def bind_request_public_base_url(
    *,
    host: str | None = None,
    client_host: str | None = None,
    forwarded_host: str | None = None,
    forwarded_proto: str | None = None,
) -> Token:
    """Установить базовый URL для текущего HTTP/WebSocket запроса."""
    effective_host = (forwarded_host or host or "").strip() or None
    base = _pick_base_for_host(effective_host, client_host)
    if forwarded_proto and forwarded_host:
        parsed = urlparse(base)
        if not parsed.scheme or parsed.scheme == "http":
            host_part = effective_host or ""
            if host_part:
                base = f"{forwarded_proto}://{host_part.split(',')[0].strip()}"
    return _request_base.set(base.rstrip("/"))


def reset_request_public_base_url(token: Token) -> None:
    _request_base.reset(token)
