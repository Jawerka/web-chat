"""
Черновик ответа ассистента во время стриминга — сохранение в БД для перезагрузки страницы.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_manager import manager
from app.config import settings
from app.db.models import Conversation, Message, MessageRole
from app.db.repositories import ConversationRepository, MessageRepository
from app.services.turn_status import STREAMING, TOOL_RUNNING, patch_active_turn_phase

logger = logging.getLogger(__name__)

EventEmitter = Any  # Callable[[str, dict], Awaitable[None]]


class AssistantStreamDraft:
    """Накопление текста стрима с периодической записью в БД."""

    def __init__(
        self,
        session: AsyncSession,
        msg_repo: MessageRepository,
        conv_repo: ConversationRepository,
        conversation: Conversation,
        emit: EventEmitter,
    ) -> None:
        self._session = session
        self._msg_repo = msg_repo
        self._conv_repo = conv_repo
        self._conversation = conversation
        self._emit = emit
        self._message: Message | None = None
        self._json_cache: dict[str, Any] = {}
        self._buffer = ""
        self._flush_lock = asyncio.Lock()
        self._pending_flush = False
        self._last_flushed_len = 0

    @property
    def message(self) -> Message | None:
        return self._message

    @property
    def text(self) -> str:
        return self._buffer

    def _content_json(self) -> dict[str, Any]:
        """Только in-memory кэш — не читать ORM после commit (MissingGreenlet)."""
        return dict(self._json_cache)

    def _current_content_text(self) -> str:
        if self._message is None:
            return self._buffer
        return self._buffer or (self._message.content_text or "")

    async def _ensure_message(self) -> Message:
        """Гарантировать черновик assistant в БД."""
        if self._message is not None:
            return self._message
        stale = await self._msg_repo.settle_stale_streaming_assistant_messages(
            self._conversation.id,
            keep_message_id=None,
        )
        if stale:
            logger.info(
                "Снят streaming с %d зависших черновиков (conv=%s)",
                stale,
                self._conversation.id,
            )
        initial_json = patch_active_turn_phase(
            {
                "images": [],
                "image_asset_ids": [],
                "streaming": True,
            },
            turn_phase=STREAMING,
            legacy_phase="text",
        )
        self._json_cache = dict(initial_json)
        self._message = await self._msg_repo.create(
            conversation_id=self._conversation.id,
            role=MessageRole.ASSISTANT,
            content_text="",
            content_json=initial_json,
        )
        await self._conv_repo.touch(self._conversation)
        await self._session.commit()
        manager.set_streaming_message(self._conversation.id, self._message.id)
        await self._emit(
            "assistant_draft",
            {"assistant_message_id": str(self._message.id)},
        )
        logger.info("Черновик ответа %s создан", self._message.id)
        return self._message

    async def _update_content_json(self, patch: dict[str, Any]) -> None:
        if self._message is None:
            return
        merged = self._content_json()
        merged.update(patch)
        self._json_cache = merged
        await self._msg_repo.update_content(
            self._message,
            content_text=self._current_content_text(),
            content_json=merged,
        )
        await self._conv_repo.touch(self._conversation)
        await self._session.commit()

    async def on_delta(self, chunk: str) -> None:
        if not chunk:
            return
        self._buffer += chunk
        if self._message is None:
            await self._ensure_message()
        elif self._content_json().get("phase") == "tool":
            await self._update_content_json(
                patch_active_turn_phase(
                    {"active_tool": None},
                    turn_phase=STREAMING,
                    legacy_phase="text",
                ),
            )
        await self._emit("text_delta", {"content": chunk})
        self._pending_flush = True
        asyncio.create_task(self._debounced_flush())
        if len(self._buffer) - self._last_flushed_len >= settings.stream_flush_min_bytes:
            asyncio.create_task(self.flush())

    async def _debounced_flush(self) -> None:
        await asyncio.sleep(0.35)
        if not self._pending_flush:
            return
        self._pending_flush = False
        await self.flush()

    async def flush(self) -> None:
        async with self._flush_lock:
            if self._message is None:
                return
            await self._msg_repo.update_content(
                self._message,
                content_text=self._buffer,
            )
            await self._conv_repo.touch(self._conversation)
            await self._session.commit()
            self._last_flushed_len = len(self._buffer)

    async def enter_tool_round(self, active_tool: str | None = None) -> None:
        """
        Перейти к фазе tools, сохранив черновик (не удалять из БД).

        Нужно для восстановления UI после перезагрузки во время SD/MCP.
        """
        await self._ensure_message()
        if self._buffer:
            await self._msg_repo.update_content(
                self._message,
                content_text=self._buffer,
            )
        cj = self._content_json()
        patch = patch_active_turn_phase(
            {
                "streaming": True,
                "active_tool": active_tool,
                "images": cj.get("images") or [],
                "image_asset_ids": cj.get("image_asset_ids") or [],
            },
            turn_phase=TOOL_RUNNING,
            legacy_phase="tool",
        )
        await self._update_content_json(patch)
        manager.set_streaming_message(self._conversation.id, self._message.id)
        # Не очищаем _buffer: текст всех раундов LLM накапливается до финального persist.
        logger.info(
            "Черновик %s: фаза tool (%s)",
            self._message.id,
            active_tool,
        )

    async def set_active_tool(self, name: str) -> None:
        """Обновить имя активного инструмента в черновике."""
        if self._message is None:
            return
        await self._update_content_json(
            patch_active_turn_phase(
                {"active_tool": name},
                turn_phase=TOOL_RUNNING,
                legacy_phase="tool",
            ),
        )

    async def add_images(
        self,
        urls: list[str],
        asset_ids: list[str] | None = None,
    ) -> None:
        """Дописать URL сгенерированных картинок в content_json черновика."""
        if self._message is None or not urls:
            return
        cj = self._content_json()
        images = list(cj.get("images") or [])
        aids = list(cj.get("image_asset_ids") or [])
        for url in urls:
            if url not in images:
                images.append(url)
        if asset_ids:
            for aid in asset_ids:
                if aid not in aids:
                    aids.append(aid)
        await self._update_content_json(
            {
                "images": images,
                "image_asset_ids": aids,
                "streaming": True,
            }
        )

    async def discard(self) -> None:
        """Удалить черновик (только при отмене / ошибке)."""
        if self._message is None:
            return
        mid = self._message.id
        await self._msg_repo.delete(self._message)
        await self._session.commit()
        manager.clear_streaming_message(self._conversation.id)
        logger.info("Черновик ответа %s удалён", mid)
        self._message = None
        self._buffer = ""
