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
from app.services.generation_state import get_generation_state
from app.db.models import MessageRole
from app.db.repositories import MessageRepository
from app.integrations.llm_client import LLMClient, LLMError
from app.integrations.runtime_config import IntegrationOverrides, parse_integration_overrides
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
            await emit(
                "error",
                {
                    "message": "Генерация отменена",
                    "code": "cancelled",
                },
            )
        except ToolLoopExceeded as exc:
            await session.rollback()
            await emit("error", {"message": str(exc), "code": "tool_loop"})
        except LLMError as exc:
            await session.rollback()
            await emit("error", {"message": str(exc), "code": "llm_error"})
        except ValueError as exc:
            await session.rollback()
            await emit("error", {"message": str(exc), "code": "validation"})
        except Exception:
            await session.rollback()
            logger.exception("Ошибка turn беседы %s", conversation_id)
            await emit(
                "error",
                {
                    "message": "Внутренняя ошибка сервера",
                    "code": "internal",
                },
            )


async def _run_regenerate_task(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    cancel_event: asyncio.Event,
    *,
    integration: IntegrationOverrides | None = None,
) -> None:
    """Перегенерация ответа на user-сообщение (или на предыдущее user для assistant)."""

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
            await emit(
                "error",
                {
                    "message": "Генерация отменена",
                    "code": "cancelled",
                },
            )
        except ToolLoopExceeded as exc:
            await session.rollback()
            await emit("error", {"message": str(exc), "code": "tool_loop"})
        except LLMError as exc:
            await session.rollback()
            await emit("error", {"message": str(exc), "code": "llm_error"})
        except ValueError as exc:
            await session.rollback()
            await emit("error", {"message": str(exc), "code": "validation"})
        except Exception:
            await session.rollback()
            logger.exception("Ошибка regenerate беседы %s", conversation_id)
            await emit(
                "error",
                {
                    "message": "Внутренняя ошибка сервера",
                    "code": "internal",
                },
            )


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


def _start_background_turn(conversation_id: uuid.UUID, coro) -> None:
    """Запустить фоновую задачу с учётом busy/cancel."""
    cancel_event = manager.reset_cancel(conversation_id)
    task = asyncio.create_task(coro(cancel_event))
    manager.set_active_task(conversation_id, task)
    task.add_done_callback(lambda _: manager.clear_active_task(conversation_id))


@router.websocket("/ws/{conversation_id}")
async def websocket_chat(websocket: WebSocket, conversation_id: uuid.UUID) -> None:
    """Интерактивный чат по WebSocket."""
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
                _start_background_turn(
                    conversation_id,
                    _schedule_turn_task(
                        conversation_id,
                        user_text,
                        list(attachment_ids),
                        integration,
                    ),
                )
                continue

            if msg_type == "regenerate":
                if manager.is_busy(conversation_id):
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
                _start_background_turn(
                    conversation_id,
                    _schedule_regenerate_task(conversation_id, regen_id, integration),
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
