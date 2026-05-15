"""
Оркестратор диалога с LLM и инструментами.

Цикл: запрос к LLM → tool_calls → выполнение → повтор.
Поддержка WebSocket-событий и сохранения в БД.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from copy import deepcopy

from app.config import settings
from app.integrations.media_utils import absolute_media_url
from app.db.models import Conversation, Message, MessageRole
from app.db.repositories import (
    AttachmentRepository,
    ConversationRepository,
    MessageRepository,
    PresetRepository,
)
from app.integrations.llm_client import LLMClient
from app.integrations.tool_executor import ToolExecutor, ToolResult
from app.services.attachment_service import AttachmentService
from app.services.message_builder import (
    append_images_markdown,
    build_user_content,
    history_to_llm_messages,
    rewrite_media_urls_in_text,
)

logger = logging.getLogger(__name__)

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


class ToolLoopExceeded(Exception):
    """Превышен лимит MAX_TOOL_ROUNDS."""


class TurnCancelled(Exception):
    """Генерация отменена пользователем."""


@dataclass
class AgentTurnResult:
    """Итог одного хода агента."""

    assistant_text: str
    image_urls: list[str] = field(default_factory=list)
    user_message: Message | None = None
    assistant_message: Message | None = None


class AgentOrchestrator:
    """Оркестрация LLM + tools."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self._llm = llm or LLMClient()
        self._tools = tool_executor

    def _executor(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
    ) -> ToolExecutor:
        if self._tools is not None:
            return self._tools
        return ToolExecutor(session, conversation_id=conversation_id)

    @staticmethod
    def _collect_tool_images(
        result,
        all_image_urls: list[str],
        all_image_asset_ids: list[str],
        media_url_rewrites: dict[str, str],
    ) -> None:
        """Добавить URL/asset id из результата инструмента."""
        for url in result.image_urls:
            if url not in all_image_urls:
                all_image_urls.append(url)
        if result.image_asset_ids:
            for aid in result.image_asset_ids:
                if aid not in all_image_asset_ids:
                    all_image_asset_ids.append(aid)
        if result.url_rewrites:
            media_url_rewrites.update(result.url_rewrites)

    @staticmethod
    def _finalize_assistant_text(
        completion_content: str | None,
        all_image_urls: list[str],
        media_url_rewrites: dict[str, str],
    ) -> str:
        """Текст ответа с актуальными URL картинок."""
        body = rewrite_media_urls_in_text(completion_content or "", media_url_rewrites)
        return append_images_markdown(body, all_image_urls)

    @staticmethod
    def _tool_loop_overflow_note() -> str:
        return (
            f"Достигнут лимит шагов с инструментами ({settings.max_tool_rounds}). "
            "Ниже все изображения, созданные на этом этапе."
        )

    async def _persist_assistant_message(
        self,
        *,
        msg_repo: MessageRepository,
        conv_repo: ConversationRepository,
        conversation: Conversation,
        content_from_llm: str | None,
        all_image_urls: list[str],
        all_image_asset_ids: list[str],
        media_url_rewrites: dict[str, str],
        tool_calls_meta: list[dict[str, Any]],
        overflow_note: str | None = None,
    ) -> Message:
        body = content_from_llm or ""
        if overflow_note:
            body = f"{overflow_note}\n\n{body}".strip() if body else overflow_note
        text = self._finalize_assistant_text(
            body,
            all_image_urls,
            media_url_rewrites,
        )
        message = await msg_repo.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content_text=text,
            content_json={
                "images": all_image_urls,
                "image_asset_ids": all_image_asset_ids,
                "tool_calls": tool_calls_meta,
                "reasoning": None,
            },
        )
        await conv_repo.touch(conversation)
        return message

    async def _complete_after_tool_limit(
        self,
        *,
        msg_repo: MessageRepository,
        conv_repo: ConversationRepository,
        conversation: Conversation,
        user_message: Message,
        content_from_llm: str | None,
        all_image_urls: list[str],
        all_image_asset_ids: list[str],
        media_url_rewrites: dict[str, str],
        tool_calls_meta: list[dict[str, Any]],
        emit: EventEmitter,
    ) -> AgentTurnResult | None:
        """Сохранить частичный ответ, если лимит tools исчерпан, но есть результат."""
        if not all_image_urls and not tool_calls_meta:
            return None
        assistant_message = await self._persist_assistant_message(
            msg_repo=msg_repo,
            conv_repo=conv_repo,
            conversation=conversation,
            content_from_llm=content_from_llm,
            all_image_urls=all_image_urls,
            all_image_asset_ids=all_image_asset_ids,
            media_url_rewrites=media_url_rewrites,
            tool_calls_meta=tool_calls_meta,
            overflow_note=self._tool_loop_overflow_note(),
        )
        await emit("done", {"assistant_message_id": str(assistant_message.id)})
        return AgentTurnResult(
            assistant_text=assistant_message.content_text or "",
            image_urls=all_image_urls,
            user_message=user_message,
            assistant_message=assistant_message,
        )

    @staticmethod
    def _llm_user_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Копия parts с абсолютными URL для vision API."""
        llm_parts = deepcopy(parts)
        for part in llm_parts:
            if part.get("type") == "image_url" and part.get("image_url", {}).get("url"):
                part["image_url"] = dict(part["image_url"])
                part["image_url"]["url"] = absolute_media_url(part["image_url"]["url"])
        return llm_parts

    async def run_conversation_turn(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        user_text: str,
        attachment_ids: list[uuid.UUID],
        emit: EventEmitter,
        cancel_event: asyncio.Event,
    ) -> AgentTurnResult:
        """
        Полный ход в беседе: сохранение user/assistant, стриминг WS-событий.

        Raises:
            ValueError: Беседа не найдена.
            ToolLoopExceeded: Слишком много раундов tools.
            TurnCancelled: Отмена через cancel_event.
            LLMError: Ошибка LLM.
        """
        conv_repo = ConversationRepository(session)
        preset_repo = PresetRepository(session)
        msg_repo = MessageRepository(session)
        att_repo = AttachmentRepository(session)

        conversation = await conv_repo.get_by_id(conversation_id)
        if conversation is None:
            raise ValueError("Беседа не найдена")

        preset = await preset_repo.get_by_id(conversation.preset_id)
        system_prompt = preset.system_prompt if preset else ""

        att_service = AttachmentService(session)
        attachments = await att_service.prepare_for_llm(attachment_ids)

        user_parts = build_user_content(user_text, attachments)
        user_message = await msg_repo.create(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content_text=user_text,
            content_json={"parts": user_parts},
        )
        if attachment_ids:
            await att_repo.link_to_message(
                attachment_ids,
                message_id=user_message.id,
                conversation_id=conversation_id,
            )

        await emit("ack", {"user_message_id": str(user_message.id)})
        await session.commit()
        logger.info(
            "БД: commit user-сообщения %s перед LLM/tools",
            user_message.id,
        )

        history = await msg_repo.list_for_llm(
            conversation_id,
            settings.max_history_messages,
        )
        history = [m for m in history if m.id != user_message.id]

        llm_messages: list[dict[str, Any]] = []
        if system_prompt:
            llm_messages.append({"role": "system", "content": system_prompt})
        llm_messages.extend(history_to_llm_messages(history))
        llm_messages.append({
            "role": "user",
            "content": self._llm_user_parts(user_parts),
        })

        all_image_urls: list[str] = []
        all_image_asset_ids: list[str] = []
        media_url_rewrites: dict[str, str] = {}
        tool_calls_meta: list[dict[str, Any]] = []

        for round_idx in range(settings.max_tool_rounds):
            if cancel_event.is_set():
                raise TurnCancelled("Генерация отменена")

            async def _on_delta(chunk: str) -> None:
                await emit("text_delta", {"content": chunk})

            completion = await self._llm.complete_with_stream(
                llm_messages,
                on_text_delta=_on_delta,
                cancel_event=cancel_event,
            )

            if cancel_event.is_set():
                raise TurnCancelled("Генерация отменена")

            if completion.tool_calls:
                llm_messages.append({
                    "role": "assistant",
                    "content": completion.content,
                    "tool_calls": completion.tool_calls,
                })
                tool_calls_meta.extend(completion.tool_calls)

                for tc in completion.tool_calls:
                    fn = tc["function"]
                    name = fn["name"]
                    args = self._llm.parse_tool_arguments(fn["arguments"])

                    await emit("tool_start", {"name": name, "arguments": args})
                    logger.info("tool_start: %s", name)

                    try:
                        result = await self._executor(
                            session,
                            conversation_id,
                        ).run(name, args)
                        result_content = result.content
                    except Exception as exc:
                        logger.exception("Ошибка инструмента %s", name)
                        result = ToolResult(
                            content=f"Ошибка инструмента {name}: {exc}",
                            image_urls=[],
                        )
                        result_content = result.content

                    self._collect_tool_images(
                        result,
                        all_image_urls,
                        all_image_asset_ids,
                        media_url_rewrites,
                    )
                    for url in result.image_urls:
                        await emit("image", {"urls": [url]})

                    await emit(
                        "tool_done",
                        {"name": name, "summary": result_content[:200]},
                    )

                    llm_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_content,
                    })

                logger.info("Раунд tools %d/%d", round_idx + 1, settings.max_tool_rounds)
                continue

            assistant_message = await self._persist_assistant_message(
                msg_repo=msg_repo,
                conv_repo=conv_repo,
                conversation=conversation,
                content_from_llm=completion.content,
                all_image_urls=all_image_urls,
                all_image_asset_ids=all_image_asset_ids,
                media_url_rewrites=media_url_rewrites,
                tool_calls_meta=tool_calls_meta,
            )
            await emit("done", {"assistant_message_id": str(assistant_message.id)})
            return AgentTurnResult(
                assistant_text=assistant_message.content_text or "",
                image_urls=all_image_urls,
                user_message=user_message,
                assistant_message=assistant_message,
            )

        partial = await self._complete_after_tool_limit(
            msg_repo=msg_repo,
            conv_repo=conv_repo,
            conversation=conversation,
            user_message=user_message,
            content_from_llm=None,
            all_image_urls=all_image_urls,
            all_image_asset_ids=all_image_asset_ids,
            media_url_rewrites=media_url_rewrites,
            tool_calls_meta=tool_calls_meta,
            emit=emit,
        )
        if partial is not None:
            return partial
        raise ToolLoopExceeded(
            f"Превышен лимит вызовов инструментов ({settings.max_tool_rounds})"
        )

    async def run_regenerate_turn(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        user_message_id: uuid.UUID,
        emit: EventEmitter,
        cancel_event: asyncio.Event,
    ) -> AgentTurnResult:
        """Перегенерировать ответ на существующее user-сообщение (без нового user)."""
        conv_repo = ConversationRepository(session)
        preset_repo = PresetRepository(session)
        msg_repo = MessageRepository(session)

        conversation = await conv_repo.get_by_id(conversation_id)
        if conversation is None:
            raise ValueError("Беседа не найдена")

        user_message = await msg_repo.get_by_id(user_message_id)
        if user_message is None or user_message.conversation_id != conversation_id:
            raise ValueError("Сообщение не найдено")
        if user_message.role != MessageRole.USER:
            raise ValueError("Перегенерация возможна только для сообщения пользователя")

        await msg_repo.delete_after(
            conversation_id,
            after_created_at=user_message.created_at,
        )

        preset = await preset_repo.get_by_id(conversation.preset_id)
        system_prompt = preset.system_prompt if preset else ""

        user_parts: list[dict[str, Any]] | str
        if user_message.content_json and "parts" in user_message.content_json:
            user_parts = user_message.content_json["parts"]
        else:
            user_parts = user_message.content_text or ""

        await emit("ack", {"user_message_id": str(user_message.id)})
        await session.commit()
        logger.info(
            "БД: commit после delete_after, user %s перед LLM/tools",
            user_message.id,
        )

        history = await msg_repo.list_for_llm(
            conversation_id,
            settings.max_history_messages,
        )
        history = [m for m in history if m.created_at < user_message.created_at]

        llm_messages: list[dict[str, Any]] = []
        if system_prompt:
            llm_messages.append({"role": "system", "content": system_prompt})
        llm_messages.extend(history_to_llm_messages(history))
        if isinstance(user_parts, list):
            llm_messages.append({
                "role": "user",
                "content": self._llm_user_parts(user_parts),
            })
        else:
            llm_messages.append({"role": "user", "content": user_parts})

        all_image_urls: list[str] = []
        all_image_asset_ids: list[str] = []
        media_url_rewrites: dict[str, str] = {}
        tool_calls_meta: list[dict[str, Any]] = []

        for round_idx in range(settings.max_tool_rounds):
            if cancel_event.is_set():
                raise TurnCancelled("Генерация отменена")

            async def _on_delta(chunk: str) -> None:
                await emit("text_delta", {"content": chunk})

            completion = await self._llm.complete_with_stream(
                llm_messages,
                on_text_delta=_on_delta,
                cancel_event=cancel_event,
            )

            if cancel_event.is_set():
                raise TurnCancelled("Генерация отменена")

            if completion.tool_calls:
                llm_messages.append({
                    "role": "assistant",
                    "content": completion.content,
                    "tool_calls": completion.tool_calls,
                })
                tool_calls_meta.extend(completion.tool_calls)

                for tc in completion.tool_calls:
                    fn = tc["function"]
                    name = fn["name"]
                    args = self._llm.parse_tool_arguments(fn["arguments"])
                    await emit("tool_start", {"name": name, "arguments": args})
                    logger.info("tool_start: %s", name)
                    try:
                        result = await self._executor(
                            session,
                            conversation_id,
                        ).run(name, args)
                        result_content = result.content
                    except Exception as exc:
                        logger.exception("Ошибка инструмента %s", name)
                        result = ToolResult(
                            content=f"Ошибка инструмента {name}: {exc}",
                            image_urls=[],
                        )
                        result_content = result.content
                    self._collect_tool_images(
                        result,
                        all_image_urls,
                        all_image_asset_ids,
                        media_url_rewrites,
                    )
                    for url in result.image_urls:
                        await emit("image", {"urls": [url]})
                    await emit(
                        "tool_done",
                        {"name": name, "summary": result_content[:200]},
                    )
                    llm_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_content,
                    })
                logger.info("Раунд tools %d/%d", round_idx + 1, settings.max_tool_rounds)
                continue

            assistant_message = await self._persist_assistant_message(
                msg_repo=msg_repo,
                conv_repo=conv_repo,
                conversation=conversation,
                content_from_llm=completion.content,
                all_image_urls=all_image_urls,
                all_image_asset_ids=all_image_asset_ids,
                media_url_rewrites=media_url_rewrites,
                tool_calls_meta=tool_calls_meta,
            )
            await emit("done", {"assistant_message_id": str(assistant_message.id)})
            return AgentTurnResult(
                assistant_text=assistant_message.content_text or "",
                image_urls=all_image_urls,
                user_message=user_message,
                assistant_message=assistant_message,
            )

        partial = await self._complete_after_tool_limit(
            msg_repo=msg_repo,
            conv_repo=conv_repo,
            conversation=conversation,
            user_message=user_message,
            content_from_llm=None,
            all_image_urls=all_image_urls,
            all_image_asset_ids=all_image_asset_ids,
            media_url_rewrites=media_url_rewrites,
            tool_calls_meta=tool_calls_meta,
            emit=emit,
        )
        if partial is not None:
            return partial
        raise ToolLoopExceeded(
            f"Превышен лимит вызовов инструментов ({settings.max_tool_rounds})"
        )

    async def run_turn(
        self,
        user_text: str,
        *,
        system_prompt: str | None = None,
        history: list[dict[str, Any]] | None = None,
        emit: EventEmitter | None = None,
    ) -> AgentTurnResult:
        """Упрощённый ход без БД (CLI test_agent)."""
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        all_image_urls: list[str] = []
        tools = self._tools or ToolExecutor()

        for _round_idx in range(settings.max_tool_rounds):
            completion = await self._llm.complete(messages)

            if completion.tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": completion.content,
                    "tool_calls": completion.tool_calls,
                })
                for tc in completion.tool_calls:
                    fn = tc["function"]
                    name = fn["name"]
                    args = self._llm.parse_tool_arguments(fn["arguments"])
                    if emit:
                        await emit("tool_start", {"name": name, "arguments": args})
                    try:
                        result = await tools.run(name, args)
                        result_content = result.content
                        result_urls = result.image_urls
                    except Exception as exc:
                        result_content = f"Ошибка инструмента {name}: {exc}"
                        result_urls = []
                    for url in result_urls:
                        if url not in all_image_urls:
                            all_image_urls.append(url)
                        if emit:
                            await emit("image", {"urls": [url]})
                    if emit:
                        await emit("tool_done", {"name": name, "summary": result_content[:200]})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result_content,
                    })
                continue

            text = append_images_markdown(completion.content or "", all_image_urls)
            if emit and text:
                await emit("text_delta", {"content": text})
            if emit:
                await emit("done", {})
            return AgentTurnResult(assistant_text=text, image_urls=all_image_urls)

        if all_image_urls:
            text = append_images_markdown(self._tool_loop_overflow_note(), all_image_urls)
            if emit:
                await emit("text_delta", {"content": text})
                await emit("done", {})
            return AgentTurnResult(assistant_text=text, image_urls=all_image_urls)
        raise ToolLoopExceeded(
            f"Превышен лимит вызовов инструментов ({settings.max_tool_rounds})"
        )
