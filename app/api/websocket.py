"""
WebSocket чата: /ws/{conversation_id}
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException, status

from app.api.ws_events import broadcast_generation_update
from app.api.ws_manager import manager
from app.db import session as db_session
from app.log_context import log_ws_session
from app.security.access import check_api_key, check_ws_origin, client_ip_from_request
from app.security.rate_limit import RateLimitExceeded, check_rate_limit
from app.services.conversation_access import get_accessible_conversation
from app.services.generation_state import get_generation_state
from app.services.request_user import resolve_request_user_from_websocket
from app.services.turn_recovery import settle_interrupted_turn
from app.db.models import MessageRole
from app.db.repositories import MessageRepository
from app.integrations.llm_client import LLMClient, LLMError
from app.integrations.runtime_config import IntegrationOverrides, parse_integration_overrides
from app.public_url import bind_request_public_base_url, reset_request_public_base_url
from app.errors import AppError, ErrorCode, app_error_from_code
from app.services.agent_orchestrator import (
    AgentOrchestrator,
    ToolLoopExceeded,
    TurnCancelled,
)
from app.diag_logging import log_event
from app.services.message_builder import is_img2img_gen_preset_instruction_block

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

_GEN_PUSH = frozenset({"tool_start", "tool_done", "done", "ack"})


def _make_turn_emit(conversation_id: uuid.UUID):
    """Emit для хода: WS-события + generation_update на ключевых типах."""

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        await manager.send_json(conversation_id, {"type": event_type, **payload})
        if event_type in _GEN_PUSH:
            await broadcast_generation_update(conversation_id)

    return emit


def _ws_error_payload(
    code: str,
    message: str,
    *,
    retryable: bool | None = None,
    **extra: Any,
) -> dict[str, Any]:
    error_id = str(uuid.uuid4()) if code == ErrorCode.INTERNAL else None
    err = app_error_from_code(code, message, retryable=retryable)
    payload = err.to_ws_payload(error_id=error_id) | extra
    if error_id:
        logger.error(
            "ws_error error_id=%s code=%s message=%s",
            error_id,
            code,
            message,
            extra={
                "event": "ws_error",
                "error_id": error_id,
                "error_code": code,
            },
        )
    return payload


async def _emit_error(
    emit,
    *,
    code: str,
    message: str,
    retryable: bool = False,
    **extra: Any,
) -> None:
    payload = _ws_error_payload(code, message, retryable=retryable, **extra)
    await emit("error", {k: v for k, v in payload.items() if k != "type"})


async def _execute_and_handle_turn(
    conversation_id: uuid.UUID,
    emit: Callable[..., Awaitable[None]],
    turn_fn: Callable[[], Awaitable[None]],
    *,
    log_label: str,
) -> None:
    """
    Выполнить ход агента и единообразно обработать ошибки для WS (P3.3).

    Новые исключения хода добавлять сюда, а не дублировать в _run_turn_task /
    _run_regenerate_task.
    """
    import time

    t0 = time.monotonic()
    try:
        await turn_fn()
    except TurnCancelled:
        await _commit_or_settle_turn(
            conversation_id,
            status_code="cancelled",
        )
        logger.info("%s done: cancelled за %.1fs", log_label, time.monotonic() - t0)
        await _emit_error(
            emit,
            code=ErrorCode.CANCELLED,
            message="Генерация отменена",
        )
    except ToolLoopExceeded as exc:
        await _commit_or_settle_turn(
            conversation_id,
            status_code="tool_loop",
            status_message=str(exc),
        )
        logger.warning(
            "%s done: tool_loop за %.1fs — %s",
            log_label,
            time.monotonic() - t0,
            exc,
        )
        await _emit_error(emit, code=ErrorCode.TOOL_LOOP, message=str(exc))
    except LLMError as exc:
        await _commit_or_settle_turn(
            conversation_id,
            status_code="llm_error",
            status_message=str(exc),
        )
        logger.warning(
            "%s done: llm_error за %.1fs — %s",
            log_label,
            time.monotonic() - t0,
            exc,
        )
        await _emit_error(
            emit,
            code=ErrorCode.LLM_ERROR,
            message=str(exc),
            retryable=True,
        )
    except AppError as exc:
        logger.warning(
            "%s done: %s за %.1fs — %s",
            log_label,
            exc.code,
            time.monotonic() - t0,
            exc,
        )
        await _emit_error(
            emit,
            code=exc.code,
            message=exc.user_message,
            retryable=exc.retryable,
        )
    except ValueError as exc:
        logger.warning(
            "%s done: validation за %.1fs — %s",
            log_label,
            time.monotonic() - t0,
            exc,
        )
        await _emit_error(emit, code=ErrorCode.VALIDATION, message=str(exc))
    except Exception:
        await _commit_or_settle_turn(
            conversation_id,
            status_code="internal",
            status_message="Внутренняя ошибка сервера",
        )
        logger.exception("%s done: internal за %.1fs", log_label, time.monotonic() - t0)
        await _emit_error(
            emit,
            code=ErrorCode.INTERNAL,
            message="Внутренняя ошибка сервера",
        )
    else:
        logger.info("%s done: ok за %.1fs", log_label, time.monotonic() - t0)


async def _commit_or_settle_turn(
    conversation_id: uuid.UUID,
    *,
    status_code: str,
    status_message: str | None = None,
) -> None:
    """
    Сохранить частичный черновик вместо полного rollback.

    При сбое БД не подменяет status_code (клиент уже получит исходный код ошибки).
    """
    async with db_session.async_session_factory() as session:
        try:
            await settle_interrupted_turn(
                session,
                conversation_id,
                status_code=status_code,
                status_message=status_message,
            )
            await session.commit()
        except Exception:
            logger.critical(
                "Не удалось зафиксировать прерванный turn (status=%s, conv=%s)",
                status_code,
                conversation_id,
                exc_info=True,
            )
            try:
                await session.rollback()
            except Exception:
                logger.exception(
                    "rollback после сбоя settle также не удался (conv=%s)",
                    conversation_id,
                )


async def _run_turn_task(
    conversation_id: uuid.UUID,
    user_text: str,
    attachment_ids: list[uuid.UUID],
    cancel_event: asyncio.Event,
    *,
    display_text: str | None = None,
    integration: IntegrationOverrides | None = None,
) -> None:
    """Фоновая задача хода агента."""
    preview = (user_text[:80] + "…") if len(user_text) > 80 else user_text
    logger.info(
        "turn start: user_message text=%r attachments=%d",
        preview,
        len(attachment_ids),
    )

    emit = _make_turn_emit(conversation_id)

    llm = LLMClient(base_url=integration.llm_base_url if integration else None)
    orchestrator = AgentOrchestrator(
        llm=llm,
        sd_webui_url=integration.sd_webui_url if integration else None,
    )
    macro_ctx = integration.macro_context if integration else "selected"
    doc_rag = integration.document_rag if integration else False

    async def turn_fn() -> None:
        await orchestrator.run_conversation_turn(
            conversation_id,
            user_text,
            attachment_ids,
            emit,
            cancel_event,
            display_text=display_text,
            llm_model=integration.llm_model if integration else None,
            macro_context=macro_ctx,
            document_rag=doc_rag,
        )

    await _execute_and_handle_turn(
        conversation_id,
        emit,
        turn_fn,
        log_label="turn",
    )


async def _run_regenerate_task(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    cancel_event: asyncio.Event,
    *,
    integration: IntegrationOverrides | None = None,
    llm_text_override: str | None = None,
) -> None:
    """Перегенерация ответа на user-сообщение (или на предыдущее user для assistant)."""
    logger.info("turn start: regenerate message_id=%s", message_id)

    emit = _make_turn_emit(conversation_id)

    async with db_session.async_session_factory() as session:
        msg_repo = MessageRepository(session)
        message = await msg_repo.get_by_id(message_id)
        if message is None or message.conversation_id != conversation_id:
            await _emit_error(emit, code=ErrorCode.VALIDATION, message="Сообщение не найдено")
            return

        if message.role == MessageRole.ASSISTANT:
            user_message = await msg_repo.get_previous_user_message(
                conversation_id,
                message.created_at,
            )
            if user_message is None:
                await _emit_error(
                    emit,
                    code=ErrorCode.VALIDATION,
                    message="Нет сообщения пользователя для перегенерации",
                )
                return
            user_message_id = user_message.id
        elif message.role == MessageRole.USER:
            user_message_id = message.id
        else:
            await _emit_error(
                emit,
                code=ErrorCode.VALIDATION,
                message="Нельзя перегенерировать это сообщение",
            )
            return

    llm = LLMClient(base_url=integration.llm_base_url if integration else None)
    orchestrator = AgentOrchestrator(
        llm=llm,
        sd_webui_url=integration.sd_webui_url if integration else None,
    )
    macro_ctx = integration.macro_context if integration else "selected"
    doc_rag = integration.document_rag if integration else False

    async def turn_fn() -> None:
        await orchestrator.run_regenerate_turn(
            conversation_id,
            user_message_id,
            emit,
            cancel_event,
            llm_model=integration.llm_model if integration else None,
            macro_context=macro_ctx,
            document_rag=doc_rag,
            llm_text_override=llm_text_override,
        )

    await _execute_and_handle_turn(
        conversation_id,
        emit,
        turn_fn,
        log_label="turn regenerate",
    )


def _schedule_turn_task(
    conversation_id: uuid.UUID,
    user_text: str,
    attachment_ids: list[uuid.UUID],
    integration: IntegrationOverrides | None = None,
    display_text: str | None = None,
):
    """Фабрика корутины хода (без замыкания на переменные цикла WS)."""

    async def runner(cancel_event: asyncio.Event) -> None:
        await _run_turn_task(
            conversation_id,
            user_text,
            attachment_ids,
            cancel_event,
            display_text=display_text,
            integration=integration,
        )

    return runner


def _schedule_regenerate_task(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    integration: IntegrationOverrides | None = None,
    *,
    llm_text_override: str | None = None,
):
    """Фабрика корутины перегенерации."""

    async def runner(cancel_event: asyncio.Event) -> None:
        await _run_regenerate_task(
            conversation_id,
            message_id,
            cancel_event,
            integration=integration,
            llm_text_override=llm_text_override,
        )

    return runner


def _start_background_turn(
    conversation_id: uuid.UUID,
    runner: Callable[[asyncio.Event], Awaitable[None]],
    *,
    turn_kind: str,
) -> bool:
    """
    Запустить фоновую задачу хода (атомарно с busy-check).

    Returns:
        False, если генерация уже идёт.
    """
    return manager.try_start_turn(
        conversation_id,
        runner,
        turn_kind=turn_kind,
    )


@router.websocket("/ws/events")
async def websocket_system_events(websocket: WebSocket) -> None:
    """Системные события: gallery_update, logs_append (P1.3)."""
    check_api_key(websocket)
    check_ws_origin(websocket)
    subscriber_user_id = None
    from app.db import session as db_session
    from app.services.request_user import resolve_request_user_from_websocket

    async with db_session.async_session_factory() as session:
        request_user = await resolve_request_user_from_websocket(websocket, session)
        if request_user is not None:
            subscriber_user_id = request_user.id
    await manager.connect_system(
        websocket,
        subscriber_user_id=subscriber_user_id,
    )
    try:
        await websocket.send_json({"type": "connected", "channel": "system"})
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect_system(websocket)


@router.websocket("/ws/{conversation_id}")
async def websocket_chat(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    """Интерактивный чат по WebSocket."""
    # До accept — иначе WebSocketException не сработает
    check_api_key(websocket)
    check_ws_origin(websocket)
    base_token = bind_request_public_base_url(
        host=websocket.headers.get("host"),
        client_host=websocket.client.host if websocket.client else None,
        forwarded_host=websocket.headers.get("x-forwarded-host"),
        forwarded_proto=websocket.headers.get("x-forwarded-proto"),
    )
    try:
        await _websocket_chat_loop(websocket, conversation_id)
    finally:
        reset_request_public_base_url(base_token)


async def _handle_ws_message(
    websocket: WebSocket,
    conversation_id: uuid.UUID,
    data: dict[str, Any] | None,
) -> bool:
    """
    Обработать одно входящее WS-сообщение.

    Returns:
        False — закрыть цикл (disconnect sentinel).
    """
    if data is None:
        return False

    msg_type = data.get("type")

    if msg_type == "ping":
        await websocket.send_json({"type": "pong"})
        return True

    if msg_type == "cancel":
        manager.cancel_turn(conversation_id)
        return True

    if msg_type == "user_message":
        try:
            ip = client_ip_from_request(websocket)
            check_rate_limit(f"{ip}:ws_message")
        except RateLimitExceeded as rl:
            await websocket.send_json(
                _ws_error_payload(
                    ErrorCode.RATE_LIMIT,
                    str(rl),
                    retry_after_sec=rl.retry_after_sec,
                )
            )
            return True

        llm_text = (data.get("text") or "").strip()
        if not llm_text:
            await websocket.send_json(
                _ws_error_payload(ErrorCode.VALIDATION, "Пустое сообщение"),
            )
            return True

        display_raw = data.get("display_text")
        display_text = (
            str(display_raw).strip()
            if display_raw is not None
            else None
        )

        raw_ids = data.get("attachment_ids") or []
        attachment_ids: list[uuid.UUID] = []
        invalid_ids: list[str] = []
        for raw in raw_ids:
            try:
                attachment_ids.append(uuid.UUID(str(raw)))
            except ValueError:
                invalid_ids.append(str(raw))
        if invalid_ids:
            await websocket.send_json(
                _ws_error_payload(
                    ErrorCode.VALIDATION,
                    f"Невалидные attachment_id: {', '.join(invalid_ids)}",
                )
            )
            return True

        integration = parse_integration_overrides(data)
        hint_head = llm_text.split("\n\n", 1)[0].strip() if llm_text else ""
        has_gen_preset = bool(
            hint_head and is_img2img_gen_preset_instruction_block(hint_head)
        )
        log_event(
            logger,
            "img2img_gen_preset_ws",
            "WS user_message",
            attachments=len(attachment_ids),
            llm_text_len=len(llm_text),
            display_text_len=len(display_text) if display_text is not None else None,
            has_display_text=display_text is not None,
            has_gen_preset_block=has_gen_preset,
            llm_text_preview=llm_text[:120] if llm_text else "",
        )
        logger.info(
            "WS user_message принят: %d вложений, llm_override=%s, gen_preset_block=%s",
            len(attachment_ids),
            bool(integration.llm_model or integration.llm_base_url),
            has_gen_preset,
        )
        started = _start_background_turn(
            conversation_id,
            _schedule_turn_task(
                conversation_id,
                llm_text,
                list(attachment_ids),
                integration,
                display_text=display_text,
            ),
            turn_kind="user_message",
        )
        if not started:
            logger.warning(
                "WS user_message отклонён: busy (активная задача уже идёт)",
            )
            await websocket.send_json(
                _ws_error_payload(ErrorCode.BUSY, "Уже выполняется генерация"),
            )
        return True

    if msg_type == "regenerate":
        try:
            regen_id = uuid.UUID(str(data.get("message_id", "")))
        except ValueError:
            await websocket.send_json(
                _ws_error_payload(ErrorCode.VALIDATION, "Некорректный message_id"),
            )
            return True
        integration = parse_integration_overrides(data)
        regen_llm_raw = data.get("text")
        llm_text_override = (
            str(regen_llm_raw).strip() if regen_llm_raw is not None else None
        )
        hint_head = (
            llm_text_override.split("\n\n", 1)[0].strip() if llm_text_override else ""
        )
        has_gen_preset = bool(
            hint_head and is_img2img_gen_preset_instruction_block(hint_head)
        )
        log_event(
            logger,
            "img2img_gen_preset_ws",
            "WS regenerate",
            message_id=str(regen_id),
            llm_text_len=len(llm_text_override) if llm_text_override else 0,
            has_gen_preset_block=has_gen_preset,
            llm_text_preview=llm_text_override[:120] if llm_text_override else "",
        )
        logger.info(
            "WS regenerate принят: message_id=%s, llm_override=%s, gen_preset_block=%s",
            regen_id,
            bool(llm_text_override),
            has_gen_preset,
        )
        started = _start_background_turn(
            conversation_id,
            _schedule_regenerate_task(
                conversation_id,
                regen_id,
                integration,
                llm_text_override=llm_text_override,
            ),
            turn_kind="regenerate",
        )
        if not started:
            logger.warning(
                "WS regenerate отклонён: busy message_id=%s",
                data.get("message_id"),
            )
            await websocket.send_json(
                _ws_error_payload(ErrorCode.BUSY, "Уже выполняется генерация"),
            )
        return True

    await websocket.send_json(
        _ws_error_payload(
            ErrorCode.UNKNOWN_TYPE,
            f"Неизвестный тип сообщения: {msg_type}",
        )
    )
    return True


async def _ws_inbox_receiver(
    websocket: WebSocket,
    inbox: asyncio.Queue[dict[str, Any] | None],
) -> None:
    """Читать JSON с сокета и складывать в очередь (не блокировать обработку)."""
    try:
        while True:
            data = await websocket.receive_json()
            await inbox.put(data)
    except WebSocketDisconnect:
        await inbox.put(None)
    except Exception:
        await inbox.put(None)
        raise


async def _websocket_chat_loop(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    ws_session_id = uuid.uuid4().hex[:12]
    with log_ws_session(ws_session_id):
        await _websocket_chat_loop_inner(websocket, conversation_id, ws_session_id)


async def _websocket_chat_loop_inner(
    websocket: WebSocket,
    conversation_id: uuid.UUID,
    ws_session_id: str,
) -> None:
    logger.debug("WS session start: %s conv=%s", ws_session_id, conversation_id)
    async with db_session.async_session_factory() as session:
        try:
            user = await resolve_request_user_from_websocket(websocket, session)
        except ValueError as exc:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason=str(exc),
            ) from exc
        if await get_accessible_conversation(session, conversation_id, user) is None:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Беседа не найдена",
            )
        gen_state = await get_generation_state(session, conversation_id)
    await manager.connect(conversation_id, websocket)
    await websocket.send_json(
        {
            "type": "connected",
            "conversation_id": str(conversation_id),
            **gen_state,
        }
    )

    inbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    receiver = asyncio.create_task(_ws_inbox_receiver(websocket, inbox))
    try:
        while True:
            data = await inbox.get()
            if not await _handle_ws_message(websocket, conversation_id, data):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WS internal: беседа %s", conversation_id)
    finally:
        receiver.cancel()
        try:
            await receiver
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        manager.disconnect(conversation_id, websocket)
