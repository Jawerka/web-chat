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
    QUOTA_EXCEEDED = "quota_exceeded"
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

    def to_ws_payload(self, *, error_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "error",
            "message": self.user_message,
            "code": self.code,
            "retryable": self.retryable,
        }
        if error_id:
            payload["error_id"] = error_id
        return payload

    def to_http_body(self, *, status_code: int = 400, error_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "detail": self.user_message,
            "code": self.code,
            "retryable": self.retryable,
        }
        if error_id:
            body["error_id"] = error_id
        return body


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
