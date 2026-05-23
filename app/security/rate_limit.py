"""
In-memory rate limiting по IP (скользящее окно).

Для одного процесса web-chat; при горизонтальном масштабировании — Redis (P2).
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from app.config import settings

_lock = Lock()
_buckets: dict[str, list[float]] = defaultdict(list)


class RateLimitExceeded(Exception):
    """Превышен лимит запросов."""

    def __init__(self, retry_after_sec: float) -> None:
        self.retry_after_sec = retry_after_sec
        super().__init__(f"Слишком много запросов, повторите через {retry_after_sec:.0f} с")


def _prune(key: str, now: float, window: float) -> None:
    cutoff = now - window
    times = _buckets[key]
    while times and times[0] <= cutoff:
        times.pop(0)


def check_rate_limit(key: str, *, limit: int | None = None, window_sec: int | None = None) -> None:
    """
    Учесть один запрос; при превышении — RateLimitExceeded.

    Args:
        key: Обычно IP клиента + суффикс endpoint.
    """
    if not settings.rate_limit_enabled:
        return
    lim = limit if limit is not None else settings.rate_limit_requests
    window = float(window_sec if window_sec is not None else settings.rate_limit_window_sec)
    if lim <= 0:
        return

    now = time.monotonic()
    with _lock:
        _prune(key, now, window)
        times = _buckets[key]
        if len(times) >= lim:
            retry = window - (now - times[0]) if times else window
            raise RateLimitExceeded(max(retry, 0.5))
        times.append(now)


def reset_rate_limits_for_tests() -> None:
    """Очистить счётчики (pytest)."""
    with _lock:
        _buckets.clear()
