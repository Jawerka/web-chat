"""Тесты канонических ошибок (P1.6)."""

from __future__ import annotations

from app.errors import AppError, ErrorCode, app_error_from_code


def test_app_error_ws_payload() -> None:
    err = AppError(
        code=ErrorCode.VALIDATION,
        user_message="Пустое сообщение",
        retryable=False,
    )
    payload = err.to_ws_payload()
    assert payload["type"] == "error"
    assert payload["code"] == "validation"
    assert payload["message"] == "Пустое сообщение"
    assert payload["retryable"] is False


def test_app_error_from_code_rate_limit_retryable() -> None:
    err = app_error_from_code(ErrorCode.RATE_LIMIT, "Слишком много запросов")
    assert err.retryable is True


def test_error_codes_stable() -> None:
    assert ErrorCode.BUSY == "busy"
    assert ErrorCode.TOOL_LOOP == "tool_loop"
