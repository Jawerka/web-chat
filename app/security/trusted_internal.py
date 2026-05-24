"""
Доверенные IP внутренних сервисов (LLM, SD, LAN).

Клиенты с этих адресов могут обращаться к узкому набору путей без cookie-сессии.
Хосты из .env и URL из настроек чата (WS) автоматически резолвятся в IP.
"""

from __future__ import annotations

import logging
import socket
import time
from functools import lru_cache
from ipaddress import ip_address
from threading import Lock
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from app.config import settings
from app.security.access import client_ip_from_request

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC = 30.0

# Пути, доступные внутренним сервисам без сессии (узкий machine-to-machine контур).
_TRUSTED_PATH_PREFIXES = (
    "/media/asset/",
)
_TRUSTED_PATH_EXACT = frozenset({"/api/health", "/api/health/logs"})

_lock = Lock()
_dynamic_hosts: set[str] = set()
_cache_at: float = 0.0
_cache_ips: frozenset[str] = frozenset()


def host_from_url(url: str | None) -> str | None:
    """Извлечь hostname из http(s) URL."""
    if not url or not str(url).strip():
        return None
    parsed = urlparse(str(url).strip())
    host = (parsed.hostname or "").strip().lower()
    return host or None


@lru_cache(maxsize=128)
def resolve_host_to_ips(host: str) -> frozenset[str]:
    """IP-адреса хоста (литерал или DNS)."""
    h = host.strip().lower()
    if not h:
        return frozenset()
    try:
        return frozenset({str(ip_address(h))})
    except ValueError:
        pass
    ips: set[str] = set()
    try:
        for res in socket.getaddrinfo(h, None, type=socket.SOCK_STREAM):
            ips.add(str(ip_address(res[4][0])))
    except OSError as exc:
        logger.debug("trusted_internal: не удалось резолвить %s: %s", h, exc)
        return frozenset()
    return frozenset(ips)


def normalize_client_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        return str(ip_address(ip.strip()))
    except ValueError:
        return ip.strip()


def _hosts_from_settings() -> set[str]:
    hosts: set[str] = set()
    for url in (
        settings.llm_base_url,
        settings.sd_webui_url,
        settings.public_base_url,
        settings.public_base_url_vpn,
    ):
        h = host_from_url(url)
        if h:
            hosts.add(h)
    for raw in settings.trusted_internal_ips.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            ip_address(item)
            continue
        except ValueError:
            hosts.add(item.lower())
    return hosts


def _ips_from_hosts(hosts: set[str]) -> set[str]:
    ips: set[str] = set()
    for raw in settings.trusted_internal_ips.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            ips.add(str(ip_address(item)))
        except ValueError:
            pass
    for host in hosts:
        ips.update(resolve_host_to_ips(host))
    if settings.trusted_internal_allow_loopback:
        ips.add("127.0.0.1")
        ips.add("::1")
    return ips


def _rebuild_cache() -> frozenset[str]:
    global _cache_at, _cache_ips
    with _lock:
        hosts = _hosts_from_settings() | set(_dynamic_hosts)
        _cache_ips = frozenset(_ips_from_hosts(hosts))
        _cache_at = time.monotonic()
        return _cache_ips


def invalidate_trusted_internal_cache() -> None:
    """Сбросить кэш (после регистрации URL с клиента)."""
    global _cache_at
    with _lock:
        _cache_at = 0.0


def get_trusted_internal_ips() -> frozenset[str]:
    """Актуальный набор доверенных IP."""
    global _cache_at, _cache_ips
    now = time.monotonic()
    with _lock:
        if _cache_ips and now - _cache_at < _CACHE_TTL_SEC:
            return _cache_ips
    return _rebuild_cache()


def refresh_trusted_internal_from_settings() -> frozenset[str]:
    """Пересобрать доверенные IP из .env (старт приложения)."""
    invalidate_trusted_internal_cache()
    ips = _rebuild_cache()
    if ips:
        logger.info(
            "trusted_internal: %d IP (LLM/SD/PUBLIC_BASE_URL + extras)",
            len(ips),
            extra={"event": "trusted_internal_refresh", "ip_count": len(ips)},
        )
    return ips


def register_integration_urls(
    llm_base_url: str | None,
    sd_webui_url: str | None,
) -> None:
    """Зарегистрировать хосты из настроек чата (localStorage → WS)."""
    changed = False
    with _lock:
        for url in (llm_base_url, sd_webui_url):
            host = host_from_url(url)
            if host and host not in _dynamic_hosts:
                _dynamic_hosts.add(host)
                changed = True
    if changed:
        invalidate_trusted_internal_cache()
        hosts = sorted(_dynamic_hosts)
        logger.info(
            "trusted_internal: зарегистрированы хосты из UI %s",
            ", ".join(hosts),
            extra={"event": "trusted_internal_register", "hosts": hosts},
        )


def trusted_internal_hosts_summary() -> dict[str, list[str]]:
    """Сводка для UI: хосты и резолвленные IP."""
    with _lock:
        env_hosts = sorted(_hosts_from_settings())
        ui_hosts = sorted(_dynamic_hosts)
    ips = sorted(get_trusted_internal_ips())
    return {
        "env_hosts": env_hosts,
        "ui_hosts": ui_hosts,
        "ips": ips,
    }


def is_trusted_internal_path(path: str) -> bool:
    if path in _TRUSTED_PATH_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _TRUSTED_PATH_PREFIXES)


def is_trusted_internal_client(request: Request) -> bool:
    client_ip = normalize_client_ip(client_ip_from_request(request))
    if not client_ip:
        return False
    return client_ip in get_trusted_internal_ips()


def is_trusted_internal_request(request: Request) -> bool:
    return is_trusted_internal_path(request.url.path) and is_trusted_internal_client(
        request,
    )
