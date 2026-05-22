"""
WebSocket чата: /ws/{conversation_id}
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.ws_manager import manager
from app.db import session as db_session
from app.log_context import log_turn_context
from app.services.generation_state import get_generation_state
from app.db.models import MessageRole
from app.db.repositories import MessageRepository
from app.integrations.llm_client import LLMClient, LLMError
from app.integrations.runtime_config import IntegrationOverrides, parse_integration_overrides
from app.public_url import bind_request_public_base_url, reset_request_public_base_url
from app.services.agent_orchestrator import (
    AgentOrchestrator,
    ToolLoopExceeded,
    TurnCancelled,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


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

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        await manager.send_json(conversation_id, {"type": event_type, **payload})

    async with db_session.async_session_factory() as session:
        llm = LLMClient(base_url=integration.llm_base_url if integration else None)
        orchestrator = AgentOrchestrator(
            llm=llm,
            sd_webui_url=integration.sd_webui_url if integration else None,
        )
        try:
            await orchestrator.run_conversation_turn(
                session,
                conversation_id,
                user_text,
                attachment_ids,
                emit,
                cancel_event,
                llm_model=integration.llm_model if integration else None,
            )
            await session.commit()
        except TurnCancelled:
            await session.rollback()
            logger.info("turn done: cancelled за %.1fs", time.monotonic() - t0)
            await emit(
                "error",
                {
                    "message": "Генерация отменена",
                    "code": "cancelled",
                },
            )
        except ToolLoopExceeded as exc:
            await session.rollback()
            logger.warning("turn done: tool_loop за %.1fs — %s", time.monotonic() - t0, exc)
            await emit("error", {"message": str(exc), "code": "tool_loop"})
        except LLMError as exc:
            await session.rollback()
            logger.warning(
                "turn done: llm_error за %.1fs — %s",
                time.monotonic() - t0,
                exc,
            )
            await emit("error", {"message": str(exc), "code": "llm_error"})
        except ValueError as exc:
            await session.rollback()
            logger.warning("turn done: validation за %.1fs — %s", time.monotonic() - t0, exc)
            await emit("error", {"message": str(exc), "code": "validation"})
        except Exception:
            await session.rollback()
            logger.exception("turn done: internal за %.1fs", time.monotonic() - t0)
            await emit(
                "error",
                {
                    "message": "Внутренняя ошибка сервера",
                    "code": "internal",
                },
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

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        await manager.send_json(conversation_id, {"type": event_type, **payload})

    async with db_session.async_session_factory() as session:
        msg_repo = MessageRepository(session)
        message = await msg_repo.get_by_id(message_id)
        if message is None or message.conversation_id != conversation_id:
            await emit("error", {"message": "Сообщение не найдено", "code": "validation"})
            return

        if message.role == MessageRole.ASSISTANT:
            user_message = await msg_repo.get_previous_user_message(
                conversation_id,
                message.created_at,
            )
            if user_message is None:
                await emit(
                    "error",
                    {
                        "message": "Нет сообщения пользователя для перегенерации",
                        "code": "validation",
                    },
                )
                return
            user_message_id = user_message.id
        elif message.role == MessageRole.USER:
            user_message_id = message.id
        else:
            await emit(
                "error",
                {
                    "message": "Нельзя перегенерировать это сообщение",
                    "code": "validation",
                },
            )
            return

        llm = LLMClient(base_url=integration.llm_base_url if integration else None)
        orchestrator = AgentOrchestrator(
            llm=llm,
            sd_webui_url=integration.sd_webui_url if integration else None,
        )
        try:
            await orchestrator.run_regenerate_turn(
                session,
                conversation_id,
                user_message_id,
                emit,
                cancel_event,
                llm_model=integration.llm_model if integration else None,
            )
            await session.commit()
        except TurnCancelled:
            await session.rollback()
            logger.info("turn done: regenerate cancelled за %.1fs", time.monotonic() - t0)
            await emit(
                "error",
                {
                    "message": "Генерация отменена",
                    "code": "cancelled",
                },
            )
        except ToolLoopExceeded as exc:
            await session.rollback()
            logger.warning(
                "turn done: regenerate tool_loop за %.1fs — %s",
                time.monotonic() - t0,
                exc,
            )
            await emit("error", {"message": str(exc), "code": "tool_loop"})
        except LLMError as exc:
            await session.rollback()
            logger.warning(
                "turn done: regenerate llm_error за %.1fs — %s",
                time.monotonic() - t0,
                exc,
            )
            await emit("error", {"message": str(exc), "code": "llm_error"})
        except ValueError as exc:
            await session.rollback()
            logger.warning(
                "turn done: regenerate validation за %.1fs — %s",
                time.monotonic() - t0,
                exc,
            )
            await emit("error", {"message": str(exc), "code": "validation"})
        except Exception:
            await session.rollback()
            logger.exception(
                "turn done: regenerate internal за %.1fs",
                time.monotonic() - t0,
            )
            await emit(
                "error",
                {
                    "message": "Внутренняя ошибка сервера",
                    "code": "internal",
                },
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
    manager.set_active_task(conversation_id, task)
    task.add_done_callback(lambda _: manager.clear_active_task(conversation_id))


@router.websocket("/ws/{conversation_id}")
async def websocket_chat(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    """Интерактивный чат по WebSocket."""
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


async def _websocket_chat_loop(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    await manager.connect(conversation_id, websocket)
    async with db_session.async_session_factory() as session:
        gen_state = await get_generation_state(session, conversation_id)
    await websocket.send_json(
        {
            "type": "connected",
            "conversation_id": str(conversation_id),
            **gen_state,
        }
    )

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "cancel":
                manager.cancel_turn(conversation_id)
                continue

            if msg_type == "user_message":
                if manager.is_busy(conversation_id):
                    logger.warning(
                        "WS user_message отклонён: busy (активная задача уже идёт)",
                    )
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Уже выполняется генерация",
                            "code": "busy",
                        }
                    )
                    continue

                user_text = (data.get("text") or "").strip()
                if not user_text:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Пустое сообщение",
                            "code": "validation",
                        }
                    )
                    continue

                raw_ids = data.get("attachment_ids") or []
                attachment_ids: list[uuid.UUID] = []
                for raw in raw_ids:
                    try:
                        attachment_ids.append(uuid.UUID(str(raw)))
                    except ValueError:
                        pass

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
                continue

            if msg_type == "regenerate":
                if manager.is_busy(conversation_id):
                    logger.warning(
                        "WS regenerate отклонён: busy message_id=%s",
                        data.get("message_id"),
                    )
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Уже выполняется генерация",
                            "code": "busy",
                        }
                    )
                    continue
                try:
                    regen_id = uuid.UUID(str(data.get("message_id", "")))
                except ValueError:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Некорректный message_id",
                            "code": "validation",
                        }
                    )
                    continue
                integration = parse_integration_overrides(data)
                logger.info("WS regenerate принят: message_id=%s", regen_id)
                _start_background_turn(
                    conversation_id,
                    _schedule_regenerate_task(conversation_id, regen_id, integration),
                    turn_kind="regenerate",
                )
                continue

            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Неизвестный тип сообщения: {msg_type}",
                    "code": "unknown_type",
                }
            )

    except WebSocketDisconnect:
        manager.disconnect(conversation_id, websocket)
    except Exception:
        manager.disconnect(conversation_id, websocket)
        logger.exception("WS ошибка беседы %s", conversation_id)
