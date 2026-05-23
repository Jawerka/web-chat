"""
Канонические коды ошибок приложения (P1.6, audit §4).

Используются в WS ``error`` и REST JSON-ответах.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ErrorCode:
    """Стабильные строковые коды для клиента и логов."""

    VALIDATION = "validation"
    AUTH = "auth_error"
    RATE_LIMIT = "rate_limit_error"
    BUSY = "busy"
    CANCELLED = "cancelled"
    TOOL_LOOP = "tool_loop"
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    GENERATION_ERROR = "generation_error"
    STORAGE_ERROR = "storage_error"
    NETWORK_ERROR = "network_error"
    INTERNAL = "internal"
    UNKNOWN_TYPE = "unknown_type"


@dataclass(frozen=True, slots=True)
class AppError(Exception):
    """Бизнес- или инфраструктурная ошибка с кодом для UI."""

    code: str
    user_message: str
    retryable: bool = False

    def __str__(self) -> str:
        return self.user_message

    def to_ws_payload(self) -> dict[str, Any]:
        return {
            "type": "error",
            "message": self.user_message,
            "code": self.code,
            "retryable": self.retryable,
        }

    def to_http_body(self, *, status_code: int = 400) -> dict[str, Any]:
        return {
            "detail": self.user_message,
            "code": self.code,
            "retryable": self.retryable,
        }


def app_error_from_code(
    code: str,
    message: str,
    *,
    retryable: bool | None = None,
) -> AppError:
    """Построить AppError с дефолтным retryable по коду."""
    if retryable is None:
        retryable = code in (
            ErrorCode.RATE_LIMIT,
            ErrorCode.LLM_ERROR,
            ErrorCode.NETWORK_ERROR,
            ErrorCode.GENERATION_ERROR,
        )
    return AppError(code=code, user_message=message, retryable=retryable)
