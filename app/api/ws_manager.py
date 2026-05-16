"""
Менеджер WebSocket-подключений по беседам.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Подключения conversation_id → set[WebSocket] и события отмены."""

    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
        self._cancel_events: dict[uuid.UUID, asyncio.Event] = {}
        self._active_tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._streaming_messages: dict[uuid.UUID, uuid.UUID] = {}

    async def connect(self, conversation_id: uuid.UUID, websocket: WebSocket) -> None:
        """Принять WebSocket и зарегистрировать."""
        await websocket.accept()
        self._connections[conversation_id].add(websocket)
        logger.info("WS подключён: беседа %s", conversation_id)

    def disconnect(self, conversation_id: uuid.UUID, websocket: WebSocket) -> bool:
        """
        Убрать WebSocket из пула.

        Returns:
            True, если к беседе больше нет активных подключений.
        """
        conns = self._connections.get(conversation_id)
        if not conns:
            return True
        conns.discard(websocket)
        if not conns:
            del self._connections[conversation_id]
            return True
        return False

    async def send_json(
        self,
        conversation_id: uuid.UUID,
        payload: dict,
        *,
        exclude: WebSocket | None = None,
    ) -> None:
        """Отправить JSON всем подключённым клиентам беседы."""
        dead: list[WebSocket] = []
        for ws in list(self._connections.get(conversation_id, [])):
            if ws is exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(conversation_id, ws)

    def reset_cancel(self, conversation_id: uuid.UUID) -> asyncio.Event:
        """Создать/сбросить событие отмены для нового turn."""
        event = asyncio.Event()
        self._cancel_events[conversation_id] = event
        self.clear_streaming_message(conversation_id)
        return event

    def set_streaming_message(
        self,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
    ) -> None:
        self._streaming_messages[conversation_id] = message_id

    def get_streaming_message(self, conversation_id: uuid.UUID) -> uuid.UUID | None:
        return self._streaming_messages.get(conversation_id)

    def clear_streaming_message(self, conversation_id: uuid.UUID) -> None:
        self._streaming_messages.pop(conversation_id, None)

    def cancel_turn(self, conversation_id: uuid.UUID) -> None:
        """Сигнал отмены текущей генерации."""
        event = self._cancel_events.get(conversation_id)
        if event is not None:
            event.set()
            logger.info("WS cancel: беседа %s", conversation_id)

    def get_cancel_event(self, conversation_id: uuid.UUID) -> asyncio.Event:
        return self._cancel_events.setdefault(conversation_id, asyncio.Event())

    def set_active_task(self, conversation_id: uuid.UUID, task: asyncio.Task[None]) -> None:
        self._active_tasks[conversation_id] = task

    def clear_active_task(self, conversation_id: uuid.UUID) -> None:
        self._active_tasks.pop(conversation_id, None)

    def is_busy(self, conversation_id: uuid.UUID) -> bool:
        task = self._active_tasks.get(conversation_id)
        return task is not None and not task.done()


manager = ConnectionManager()
