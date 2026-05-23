"""
WebSocket чата: /ws/{conversation_id}
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException, status

from app.api.ws_events import broadcast_generation_update
from app.api.ws_manager import manager
from app.db import session as db_session
from app.log_context import log_turn_context, log_ws_session
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

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


def _ws_error_payload(
    code: str,
    message: str,
    *,
    retryable: bool | None = None,
    **extra: Any,
) -> dict[str, Any]:
    err = app_error_from_code(code, message, retryable=retryable)
    return err.to_ws_payload() | extra


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


async def _commit_or_settle_turn(
    session,
    conversation_id: uuid.UUID,
    *,
    status_code: str,
    status_message: str | None = None,
) -> None:
    """Сохранить частичный черновик вместо полного rollback."""
    await settle_interrupted_turn(
        session,
        conversation_id,
        status_code=status_code,
        status_message=status_message,
    )
    await session.commit()


async def _run_turn_task(
    conversation_id: uuid.UUID,
    user_text: str,
    attachment_ids: list[uuid.UUID],
    cancel_event: asyncio.Event,
    *,
    integration: IntegrationOverrides | None = None,
) -> None:
    """Фоновая задача хода агента с отдельной сессией БД."""
    import time

    t0 = time.monotonic()
    preview = (user_text[:80] + "…") if len(user_text) > 80 else user_text
    logger.info(
        "turn start: user_message text=%r attachments=%d",
        preview,
        len(attachment_ids),
    )

    _GEN_PUSH = frozenset({"tool_start", "tool_done", "done", "ack"})

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        await manager.send_json(conversation_id, {"type": event_type, **payload})
        if event_type in _GEN_PUSH:
            await broadcast_generation_update(conversation_id)

    async with db_session.async_session_factory() as session:
        llm = LLMClient(base_url=integration.llm_base_url if integration else None)
        orchestrator = AgentOrchestrator(
            llm=llm,
            sd_webui_url=integration.sd_webui_url if integration else None,
        )
        try:
            macro_ctx = integration.macro_context if integration else "selected"
            doc_rag = integration.document_rag if integration else False
            await orchestrator.run_conversation_turn(
                session,
                conversation_id,
                user_text,
                attachment_ids,
                emit,
                cancel_event,
                llm_model=integration.llm_model if integration else None,
                macro_context=macro_ctx,
                document_rag=doc_rag,
            )
            await session.commit()
        except TurnCancelled:
            await _commit_or_settle_turn(
                session,
                conversation_id,
                status_code="cancelled",
            )
            logger.info("turn done: cancelled за %.1fs", time.monotonic() - t0)
            await _emit_error(
                emit,
                code=ErrorCode.CANCELLED,
                message="Генерация отменена",
            )
        except ToolLoopExceeded as exc:
            await _commit_or_settle_turn(
                session,
                conversation_id,
                status_code="tool_loop",
                status_message=str(exc),
            )
            logger.warning("turn done: tool_loop за %.1fs — %s", time.monotonic() - t0, exc)
            await _emit_error(emit, code=ErrorCode.TOOL_LOOP, message=str(exc))
        except LLMError as exc:
            await _commit_or_settle_turn(
                session,
                conversation_id,
                status_code="llm_error",
                status_message=str(exc),
            )
            logger.warning(
                "turn done: llm_error за %.1fs — %s",
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
            await session.rollback()
            logger.warning("turn done: %s за %.1fs — %s", exc.code, time.monotonic() - t0, exc)
            await _emit_error(
                emit,
                code=exc.code,
                message=exc.user_message,
                retryable=exc.retryable,
            )
        except ValueError as exc:
            await session.rollback()
            logger.warning("turn done: validation за %.1fs — %s", time.monotonic() - t0, exc)
            await _emit_error(emit, code=ErrorCode.VALIDATION, message=str(exc))
        except Exception:
            await _commit_or_settle_turn(
                session,
                conversation_id,
                status_code="internal",
                status_message="Внутренняя ошибка сервера",
            )
            logger.exception("turn done: internal за %.1fs", time.monotonic() - t0)
            await _emit_error(
                emit,
                code=ErrorCode.INTERNAL,
                message="Внутренняя ошибка сервера",
            )
        else:
            logger.info("turn done: ok за %.1fs", time.monotonic() - t0)


async def _run_regenerate_task(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    cancel_event: asyncio.Event,
    *,
    integration: IntegrationOverrides | None = None,
) -> None:
    """Перегенерация ответа на user-сообщение (или на предыдущее user для assistant)."""
    import time

    t0 = time.monotonic()
    logger.info("turn start: regenerate message_id=%s", message_id)

    _GEN_PUSH = frozenset({"tool_start", "tool_done", "done", "ack"})

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        await manager.send_json(conversation_id, {"type": event_type, **payload})
        if event_type in _GEN_PUSH:
            await broadcast_generation_update(conversation_id)

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
        try:
            macro_ctx = integration.macro_context if integration else "selected"
            doc_rag = integration.document_rag if integration else False
            await orchestrator.run_regenerate_turn(
                session,
                conversation_id,
                user_message_id,
                emit,
                cancel_event,
                llm_model=integration.llm_model if integration else None,
                macro_context=macro_ctx,
                document_rag=doc_rag,
            )
            await session.commit()
        except TurnCancelled:
            await _commit_or_settle_turn(
                session,
                conversation_id,
                status_code="cancelled",
            )
            logger.info("turn done: regenerate cancelled за %.1fs", time.monotonic() - t0)
            await _emit_error(
                emit,
                code=ErrorCode.CANCELLED,
                message="Генерация отменена",
            )
        except ToolLoopExceeded as exc:
            await _commit_or_settle_turn(
                session,
                conversation_id,
                status_code="tool_loop",
                status_message=str(exc),
            )
            logger.warning(
                "turn done: regenerate tool_loop за %.1fs — %s",
                time.monotonic() - t0,
                exc,
            )
            await _emit_error(emit, code=ErrorCode.TOOL_LOOP, message=str(exc))
        except LLMError as exc:
            await _commit_or_settle_turn(
                session,
                conversation_id,
                status_code="llm_error",
                status_message=str(exc),
            )
            logger.warning(
                "turn done: regenerate llm_error за %.1fs — %s",
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
            await session.rollback()
            logger.warning(
                "turn done: regenerate %s за %.1fs — %s",
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
            await session.rollback()
            logger.warning(
                "turn done: regenerate validation за %.1fs — %s",
                time.monotonic() - t0,
                exc,
            )
            await _emit_error(emit, code=ErrorCode.VALIDATION, message=str(exc))
        except Exception:
            await _commit_or_settle_turn(
                session,
                conversation_id,
                status_code="internal",
                status_message="Внутренняя ошибка сервера",
            )
            logger.exception(
                "turn done: regenerate internal за %.1fs",
                time.monotonic() - t0,
            )
            await _emit_error(
                emit,
                code=ErrorCode.INTERNAL,
                message="Внутренняя ошибка сервера",
            )
        else:
            logger.info("turn done: regenerate ok за %.1fs", time.monotonic() - t0)


def _schedule_turn_task(
    conversation_id: uuid.UUID,
    user_text: str,
    attachment_ids: list[uuid.UUID],
    integration: IntegrationOverrides | None = None,
):
    """Фабрика корутины хода (без замыкания на переменные цикла WS)."""

    async def runner(cancel_event: asyncio.Event) -> None:
        await _run_turn_task(
            conversation_id,
            user_text,
            attachment_ids,
            cancel_event,
            integration=integration,
        )

    return runner


def _schedule_regenerate_task(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    integration: IntegrationOverrides | None = None,
):
    """Фабрика корутины перегенерации."""

    async def runner(cancel_event: asyncio.Event) -> None:
        await _run_regenerate_task(
            conversation_id,
            message_id,
            cancel_event,
            integration=integration,
        )

    return runner


def _start_background_turn(
    conversation_id: uuid.UUID,
    coro,
    *,
    turn_kind: str,
) -> None:
    """Запустить фоновую задачу с учётом busy/cancel."""

    async def _wrapped(cancel_event: asyncio.Event) -> None:
        with log_turn_context(conversation_id, turn_kind=turn_kind):
            await coro(cancel_event)

    cancel_event = manager.reset_cancel(conversation_id)
    task = asyncio.create_task(_wrapped(cancel_event))

    def _on_turn_done(t: asyncio.Task[None]) -> None:
        manager.clear_active_task(conversation_id)
        if not t.cancelled() and t.exception() is not None:
            logger.debug("turn task завершилась с ошибкой: conv=%s", conversation_id)

    manager.set_active_task(conversation_id, task)
    task.add_done_callback(_on_turn_done)


@router.websocket("/ws/events")
async def websocket_system_events(websocket: WebSocket) -> None:
    """Системные события: gallery_update, logs_append (P1.3)."""
    check_api_key(websocket)
    check_ws_origin(websocket)
    await manager.connect_system(websocket)
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

        if manager.is_busy(conversation_id):
            logger.warning(
                "WS user_message отклонён: busy (активная задача уже идёт)",
            )
            await websocket.send_json(
                _ws_error_payload(ErrorCode.BUSY, "Уже выполняется генерация"),
            )
            return True

        user_text = (data.get("text") or "").strip()
        if not user_text:
            await websocket.send_json(
                _ws_error_payload(ErrorCode.VALIDATION, "Пустое сообщение"),
            )
            return True

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
        logger.info(
            "WS user_message принят: %d вложений, llm_override=%s",
            len(attachment_ids),
            bool(integration.llm_model or integration.llm_base_url),
        )
        _start_background_turn(
            conversation_id,
            _schedule_turn_task(
                conversation_id,
                user_text,
                list(attachment_ids),
                integration,
            ),
            turn_kind="user_message",
        )
        return True

    if msg_type == "regenerate":
        if manager.is_busy(conversation_id):
            logger.warning(
                "WS regenerate отклонён: busy message_id=%s",
                data.get("message_id"),
            )
            await websocket.send_json(
                _ws_error_payload(ErrorCode.BUSY, "Уже выполняется генерация"),
            )
            return True
        try:
            regen_id = uuid.UUID(str(data.get("message_id", "")))
        except ValueError:
            await websocket.send_json(
                _ws_error_payload(ErrorCode.VALIDATION, "Некорректный message_id"),
            )
            return True
        integration = parse_integration_overrides(data)
        logger.info("WS regenerate принят: message_id=%s", regen_id)
        _start_background_turn(
            conversation_id,
            _schedule_regenerate_task(conversation_id, regen_id, integration),
            turn_kind="regenerate",
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
