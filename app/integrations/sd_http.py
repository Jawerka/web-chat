"""
HTTP к SD WebUI: retry на сетевых сбоях и circuit breaker (BE-2).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.config import settings

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({502, 503, 504})


class SdUnavailableError(RuntimeError):
    """SD временно недоступен (сеть или circuit open)."""

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or "Сервис генерации изображений временно недоступен. Повторите через минуту.",
        )


class _SdCircuitBreaker:
    """Простой circuit breaker в памяти процесса."""

    def __init__(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    def is_open(self) -> bool:
        if self._open_until and time.monotonic() < self._open_until:
            return True
        if self._open_until and time.monotonic() >= self._open_until:
            self._open_until = 0.0
            self._failures = 0
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    def record_failure(self) -> None:
        self._failures += 1
        threshold = max(1, settings.sd_circuit_breaker_threshold)
        if self._failures >= threshold:
            cooldown = max(1.0, settings.sd_circuit_breaker_cooldown_sec)
            self._open_until = time.monotonic() + cooldown
            logger.warning(
                "SD circuit open на %.0f с после %d сбоев",
                cooldown,
                self._failures,
            )


_circuit = _SdCircuitBreaker()


def reset_sd_circuit_for_tests() -> None:
    """Сброс breaker (только тесты)."""
    _circuit._failures = 0
    _circuit._open_until = 0.0


def sd_post_json(
    session: requests.Session,
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float | int,
    operation: str = "sd",
) -> requests.Response:
    """
    POST JSON к SD с ограниченными retry и circuit breaker.

    Raises:
        SdUnavailableError: circuit open или исчерпаны попытки на сетевых сбоях.
        requests.HTTPError: HTTP-ошибка после retry.
    """
    if _circuit.is_open():
        raise SdUnavailableError()

    attempts = max(1, settings.sd_http_retries + 1)
    last_exc: BaseException | None = None

    for attempt in range(attempts):
        try:
            resp = session.post(url, json=payload, timeout=timeout)
            status = getattr(resp, "status_code", 200)
            if status in _RETRYABLE_STATUS and attempt < attempts - 1:
                logger.warning(
                    "SD %s HTTP %s, retry %d/%d",
                    operation,
                    status,
                    attempt + 1,
                    attempts,
                )
                time.sleep(0.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            _circuit.record_success()
            return resp
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < attempts - 1:
                logger.warning(
                    "SD %s сеть (%s), retry %d/%d",
                    operation,
                    type(exc).__name__,
                    attempt + 1,
                    attempts,
                )
                time.sleep(0.5 * (attempt + 1))
                continue
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in _RETRYABLE_STATUS and attempt < attempts - 1:
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
                continue
            raise

    _circuit.record_failure()
    logger.error("SD %s недоступен после %d попыток: %s", operation, attempts, last_exc)
    raise SdUnavailableError() from last_exc


def sd_interrupt(session: requests.Session, sd_base: str) -> bool:
    """
    Прервать текущую генерацию на SD WebUI (POST /sdapi/v1/interrupt).

    Returns:
        True если запрос отправлен успешно.
    """
    url = f"{sd_base.rstrip('/')}/sdapi/v1/interrupt"
    try:
        resp = session.post(url, timeout=10)
        resp.raise_for_status()
        logger.info("SD interrupt отправлен")
        return True
    except requests.RequestException as exc:
        logger.warning("SD interrupt не удался: %s", exc)
        return False
