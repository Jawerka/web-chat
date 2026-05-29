"""
Менеджер WebSocket-подключений по беседам.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from app.log_context import log_turn_context

logger = logging.getLogger(__name__)

# Секунды без сокетов, после которых чистим служебное состояние беседы
_SESSION_IDLE_SEC = 300
# Интервал фоновой уборки «зомби»-задач
_SWEEP_INTERVAL_SEC = 60


@dataclass
class ConversationSessionState:
    """Состояние одной беседы в WS-менеджере."""

    websockets: set[WebSocket] = field(default_factory=set)
    cancel_event: asyncio.Event | None = None
    active_task: asyncio.Task[None] | None = None
    streaming_message_id: uuid.UUID | None = None
    last_progress: dict[str, Any] | None = None
    last_activity: float = field(default_factory=time.monotonic)


class ConnectionManager:
    """Подключения и turn-state по conversation_id."""

    def __init__(self) -> None:
        self._sessions: dict[uuid.UUID, ConversationSessionState] = {}
        self._system_websockets: set[WebSocket] = set()
        self._sweeper_task: asyncio.Task[None] | None = None
        self._turn_locks: dict[uuid.UUID, threading.Lock] = defaultdict(threading.Lock)

    def _session(self, conversation_id: uuid.UUID) -> ConversationSessionState:
        state = self._sessions.get(conversation_id)
        if state is None:
            state = ConversationSessionState()
            self._sessions[conversation_id] = state
        state.last_activity = time.monotonic()
        return state

    def ensure_sweeper(self) -> None:
        """Запустить фоновую уборку (один раз на процесс)."""
        if self._sweeper_task is not None and not self._sweeper_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._sweeper_task = loop.create_task(self._sweeper_loop())

    async def _sweeper_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_SWEEP_INTERVAL_SEC)
                self._sweep_idle_sessions()
                self._sweep_done_tasks()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("WS sweeper: ошибка")

    def _sweep_done_tasks(self) -> None:
        for cid, state in list(self._sessions.items()):
            task = state.active_task
            if task is not None and task.done():
                state.active_task = None
                logger.debug("WS sweeper: снята завершённая задача conv=%s", cid)

    def _sweep_idle_sessions(self) -> None:
        now = time.monotonic()
        for cid, state in list(self._sessions.items()):
            if state.websockets:
                continue
            if state.active_task is not None and not state.active_task.done():
                continue
            if now - state.last_activity < _SESSION_IDLE_SEC:
                continue
            self._cleanup_session_state(cid, state, reason="idle")

    def _cleanup_session_state(
        self,
        conversation_id: uuid.UUID,
        state: ConversationSessionState,
        *,
        reason: str,
        cancel_running: bool = False,
    ) -> None:
        if state.active_task is not None and not state.active_task.done():
            if cancel_running and state.cancel_event is not None:
                state.cancel_event.set()
            return
        state.cancel_event = None
        state.active_task = None
        state.streaming_message_id = None
        state.last_progress = None
        if conversation_id in self._sessions and not state.websockets:
            del self._sessions[conversation_id]
        logger.debug("WS state очищен (%s): conv=%s", reason, conversation_id)

    async def connect_system(self, websocket: WebSocket) -> None:
        """Системный канал: галерея, логи (без conversation_id)."""
        await websocket.accept()
        self._system_websockets.add(websocket)
        self.ensure_sweeper()
        logger.info("WS system подключён (всего %d)", len(self._system_websockets))

    def disconnect_system(self, websocket: WebSocket) -> None:
        self._system_websockets.discard(websocket)

    async def broadcast_system(self, payload: dict) -> None:
        """Отправить JSON всем подписчикам /ws/events."""
        if not self._system_websockets:
            return
        dead: list[WebSocket] = []
        for ws in list(self._system_websockets):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_system(ws)

    async def connect(self, conversation_id: uuid.UUID, websocket: WebSocket) -> None:
        """Принять WebSocket и зарегистрировать."""
        await websocket.accept()
        self._session(conversation_id).websockets.add(websocket)
        self.ensure_sweeper()
        logger.info("WS подключён: беседа %s", conversation_id)

    def disconnect(self, conversation_id: uuid.UUID, websocket: WebSocket) -> bool:
        """
        Убрать WebSocket из пула.

        Returns:
            True, если к беседе больше нет активных подключений.
        """
        state = self._sessions.get(conversation_id)
        if state is None:
            return True
        state.websockets.discard(websocket)
        state.last_activity = time.monotonic()
        if state.websockets:
            return False
        if state.active_task is not None and not state.active_task.done():
            logger.info(
                "WS disconnect: беседа %s — клиент отключился, фоновая генерация продолжается",
                conversation_id,
            )
            return True
        self._cleanup_session_state(conversation_id, state, reason="disconnect")
        return True

    async def send_json(
        self,
        conversation_id: uuid.UUID,
        payload: dict,
        *,
        exclude: WebSocket | None = None,
    ) -> None:
        """Отправить JSON всем подключённым клиентам беседы."""
        state = self._sessions.get(conversation_id)
        if state is None:
            return
        dead: list[WebSocket] = []
        for ws in list(state.websockets):
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
        state = self._session(conversation_id)
        event = asyncio.Event()
        state.cancel_event = event
        self.clear_streaming_message(conversation_id)
        return event

    def set_streaming_message(
        self,
        conversation_id: uuid.UUID,
        message_id: uuid.UUID,
    ) -> None:
        self._session(conversation_id).streaming_message_id = message_id

    def get_streaming_message(self, conversation_id: uuid.UUID) -> uuid.UUID | None:
        state = self._sessions.get(conversation_id)
        return state.streaming_message_id if state else None

    def clear_streaming_message(self, conversation_id: uuid.UUID) -> None:
        state = self._sessions.get(conversation_id)
        if state is not None:
            state.streaming_message_id = None

    def set_progress(
        self,
        conversation_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> None:
        """Последний статус для resume / generation-status."""
        self._session(conversation_id).last_progress = dict(payload)

    def get_progress(self, conversation_id: uuid.UUID) -> dict[str, Any] | None:
        state = self._sessions.get(conversation_id)
        if state is None or state.last_progress is None:
            return None
        return dict(state.last_progress)

    def clear_progress(self, conversation_id: uuid.UUID) -> None:
        state = self._sessions.get(conversation_id)
        if state is not None:
            state.last_progress = None

    def cancel_turn(self, conversation_id: uuid.UUID) -> None:
        """Сигнал отмены текущей генерации."""
        state = self._sessions.get(conversation_id)
        if state is not None and state.cancel_event is not None:
            state.cancel_event.set()
            logger.info("WS cancel: беседа %s", conversation_id)

    def get_cancel_event(self, conversation_id: uuid.UUID) -> asyncio.Event:
        state = self._session(conversation_id)
        if state.cancel_event is None:
            state.cancel_event = asyncio.Event()
        return state.cancel_event

    def set_active_task(self, conversation_id: uuid.UUID, task: asyncio.Task[None]) -> None:
        self._session(conversation_id).active_task = task

    def try_start_turn(
        self,
        conversation_id: uuid.UUID,
        runner: Callable[[asyncio.Event], Awaitable[None]],
        *,
        turn_kind: str,
    ) -> bool:
        """
        Атомарно проверить busy и запустить фоновую задачу хода (P3.5).

        Returns:
            False, если генерация уже идёт.
        """
        lock = self._turn_locks[conversation_id]
        with lock:
            if self.is_busy(conversation_id):
                return False
            cancel_event = self.reset_cancel(conversation_id)

            async def _wrapped(ce: asyncio.Event) -> None:
                with log_turn_context(conversation_id, turn_kind=turn_kind):
                    await runner(ce)

            task = asyncio.create_task(_wrapped(cancel_event))

            def _on_turn_done(t: asyncio.Task[None]) -> None:
                self.clear_active_task(conversation_id)
                if not t.cancelled() and t.exception() is not None:
                    logger.debug(
                        "turn task завершилась с ошибкой: conv=%s",
                        conversation_id,
                    )

            self.set_active_task(conversation_id, task)
            task.add_done_callback(_on_turn_done)
        return True

    def clear_active_task(self, conversation_id: uuid.UUID) -> None:
        state = self._sessions.get(conversation_id)
        if state is not None:
            state.active_task = None

    def is_busy(self, conversation_id: uuid.UUID) -> bool:
        state = self._sessions.get(conversation_id)
        if state is None:
            return False
        task = state.active_task
        return task is not None and not task.done()

    def busy_conversation_ids(self) -> set[uuid.UUID]:
        """Беседы с активной фоновой генерацией."""
        return {
            cid
            for cid, state in self._sessions.items()
            if state.active_task is not None and not state.active_task.done()
        }

    def websocket_count(self) -> int:
        """Число открытых WS-сокетов (все беседы + system)."""
        chat = sum(len(state.websockets) for state in self._sessions.values())
        return chat + len(self._system_websockets)

    def system_websocket_count(self) -> int:
        return len(self._system_websockets)

    async def close_all(
        self,
        *,
        code: int = 1001,
        reason: str = "server_shutdown",
        notify: dict | None = None,
    ) -> None:
        """Закрыть все WS с уведомлением (graceful shutdown)."""
        if self._sweeper_task is not None and not self._sweeper_task.done():
            self._sweeper_task.cancel()
            try:
                await self._sweeper_task
            except asyncio.CancelledError:
                pass
            self._sweeper_task = None

        all_ws: list[WebSocket] = list(self._system_websockets)
        for state in self._sessions.values():
            all_ws.extend(list(state.websockets))

        reason_bytes = reason[:123]
        for ws in all_ws:
            try:
                if notify is not None:
                    await ws.send_json(notify)
                await ws.close(code=code, reason=reason_bytes)
            except Exception:
                pass

        self._system_websockets.clear()
        for cid, state in list(self._sessions.items()):
            state.websockets.clear()
            self._cleanup_session_state(cid, state, reason="shutdown", cancel_running=True)
        logger.info("WS: закрыто %d подключений (code=%s)", len(all_ws), code)

    def active_turn_count(self) -> int:
        """Число бесед с незавершённой фоновой задачей."""
        return len(self.busy_conversation_ids())


manager = ConnectionManager()
