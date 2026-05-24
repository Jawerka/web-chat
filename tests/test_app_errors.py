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


def test_app_error_ws_payload_with_error_id() -> None:
    err = AppError(
        code=ErrorCode.INTERNAL,
        user_message="Сбой",
        retryable=True,
    )
    payload = err.to_ws_payload(error_id="eid-123")
    assert payload["error_id"] == "eid-123"


def test_ws_internal_error_payload_has_error_id() -> None:
    from app.api.websocket import _ws_error_payload

    payload = _ws_error_payload(ErrorCode.INTERNAL, "Внутренняя ошибка")
    assert payload["code"] == ErrorCode.INTERNAL
    assert "error_id" in payload
    assert len(payload["error_id"]) >= 32


def test_error_codes_stable() -> None:
    assert ErrorCode.BUSY == "busy"
    assert ErrorCode.TOOL_LOOP == "tool_loop"
