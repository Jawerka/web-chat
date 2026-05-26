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
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.diag_logging import log_event, summarize_llm_messages
from app.db.models import Conversation, Message, MessageRole
from app.db.repositories import (
    AttachmentRepository,
    ConversationRepository,
    MessageRepository,
    PresetRepository,
    PromptMacroRepository,
)
from app.integrations.llm_client import LLMClient, LLMCompletion
from app.integrations.tool_definitions import tools_for_preset_slug
from app.integrations.media_utils import asset_llm_media_url, rewrite_image_url_for_llm
from app.integrations.tool_executor import ToolExecutor, ToolResult
from app.services.attachment_service import AttachmentService
from app.services.conversation_title_service import maybe_generate_conversation_title
from app.services.message_builder import (
    strip_img2img_gen_preset_prefix,
    is_img2img_gen_preset_instruction_block,
    append_img2img_init_hints,
    build_img2img_init_hint_text,
    build_user_content,
    filter_available_image_attachments,
    filter_unreachable_image_parts,
    finalize_assistant_text,
    history_to_llm_messages,
    refresh_user_parts_for_regenerate,
    sanitize_llm_messages_for_vision,
)
from app.services.prompt_macro_service import (
    alias_map_from_macros,
    expand_parts_for_llm,
)
from app.api.ws_events import emit_progress
from app.api.ws_manager import manager
from app.services.conversation_tool_state import ConversationToolState
from app.services.user_progress import STAGE_LLM_THINKING, STAGE_LLM_TOOLS, build_progress
from app.services.streaming_draft import AssistantStreamDraft
from app.services.turn_status import patch_completed

logger = logging.getLogger(__name__)

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


class ToolLoopExceeded(Exception):
    """Превышен лимит MAX_TOOL_ROUNDS."""


class ToolAntiLoopExceeded(ToolLoopExceeded):
    """P1.4: дубликат вызова или лимит одного SD-tool в ходе (без UI-ошибки)."""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind  # "duplicate" | "max_same"


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
        *,
        sd_webui_url: str | None = None,
    ) -> None:
        self._llm = llm or LLMClient()
        self._tools = tool_executor
        self._sd_webui_url = sd_webui_url

    def _executor(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        *,
        source_user_message_id: uuid.UUID | None = None,
        cancel_event: asyncio.Event | None = None,
        emit_progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> ToolExecutor:
        if self._tools is not None:
            return self._tools
        return ToolExecutor(
            session,
            conversation_id=conversation_id,
            sd_webui_url=self._sd_webui_url,
            source_user_message_id=source_user_message_id,
            cancel_event=cancel_event,
            emit_progress=emit_progress_cb,
        )

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
    def _merge_streamed_llm_text(
        streamed: str,
        completion_content: str | None,
    ) -> str | None:
        """Объединить накопленный стрим черновика с финальным content от LLM."""
        buf = (streamed or "").strip()
        llm = (completion_content or "").strip()
        if buf and llm:
            if buf == llm or llm in buf:
                return buf
            if buf in llm:
                return llm
            if len(buf) > len(llm):
                return buf
            if len(llm) > len(buf):
                return llm
            return f"{buf}\n\n{llm}".strip()
        return buf or llm or None

    @staticmethod
    def _finalize_assistant_text(
        completion_content: str | None,
        media_url_rewrites: dict[str, str],
    ) -> str:
        """Текст ответа без markdown-картинок (изображения — в content_json)."""
        return finalize_assistant_text(
            completion_content,
            media_url_rewrites=media_url_rewrites,
        )

    @staticmethod
    def _tool_loop_overflow_note() -> str:
        return (
            f"Достигнут лимит шагов с инструментами ({settings.max_tool_rounds}). "
            "Ниже все изображения, созданные на этом этапе."
        )

    @staticmethod
    def _turn_reasoning(
        stream_draft: AssistantStreamDraft,
        completion: LLMCompletion,
    ) -> str | None:
        text = (stream_draft.reasoning or completion.reasoning or "").strip()
        return text or None

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
        existing_message: Message | None = None,
        rag_sources: list[dict[str, Any]] | None = None,
        reasoning: str | None = None,
    ) -> Message:
        body = content_from_llm or ""
        if overflow_note:
            body = f"{overflow_note}\n\n{body}".strip() if body else overflow_note
        text = self._finalize_assistant_text(body, media_url_rewrites)
        reasoning_text = (reasoning or "").strip() or None
        payload: dict[str, Any] = {
            "images": all_image_urls,
            "image_asset_ids": all_image_asset_ids,
            "tool_calls": tool_calls_meta,
            "reasoning": reasoning_text,
        }
        if rag_sources:
            payload["rag_sources"] = rag_sources
        content_json = patch_completed(payload)
        if existing_message is not None:
            await msg_repo.update_content(
                existing_message,
                content_text=text,
                content_json=content_json,
            )
            await conv_repo.touch(conversation)
            manager.clear_streaming_message(conversation.id)
            return existing_message
        message = await msg_repo.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content_text=text,
            content_json=content_json,
        )
        await conv_repo.touch(conversation)
        manager.clear_streaming_message(conversation.id)
        return message

    async def _emit_turn_done(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        assistant_message: Message,
        emit: EventEmitter,
        *,
        llm_model: str | None = None,
    ) -> None:
        """Событие done + опционально автозаголовок беседы."""
        new_title = await maybe_generate_conversation_title(
            session,
            conversation_id,
            self._llm,
            model=llm_model,
        )
        payload: dict[str, Any] = {
            "assistant_message_id": str(assistant_message.id),
        }
        if new_title:
            payload["conversation_title"] = new_title
        manager.clear_progress(conversation_id)
        await emit("done", payload)

    _ANTI_LOOP_SKIP_MSG = (
        "Вызов инструмента пропущен: лимит повторов в этом ходе."
    )

    async def _complete_after_tool_limit(
        self,
        session: AsyncSession,
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
        llm_model: str | None = None,
        existing_message: Message | None = None,
        overflow_note: str | None = None,
        rag_sources: list[dict[str, Any]] | None = None,
        reasoning: str | None = None,
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
            overflow_note=overflow_note,
            existing_message=existing_message,
            rag_sources=rag_sources,
            reasoning=reasoning,
        )
        await self._emit_turn_done(
            session,
            conversation.id,
            assistant_message,
            emit,
            llm_model=llm_model,
        )
        return AgentTurnResult(
            assistant_text=assistant_message.content_text or "",
            image_urls=all_image_urls,
            user_message=user_message,
            assistant_message=assistant_message,
        )

    async def _run_completion_tool_calls(
        self,
        *,
        completion,
        tool_state: ConversationToolState,
        turn_executor: ToolExecutor,
        stream_draft: AssistantStreamDraft,
        llm_messages: list[dict[str, Any]],
        all_image_urls: list[str],
        all_image_asset_ids: list[str],
        media_url_rewrites: dict[str, str],
        emit: EventEmitter,
        round_idx: int,
        cancel_event: asyncio.Event,
        session: AsyncSession,
        msg_repo: MessageRepository,
        conv_repo: ConversationRepository,
        conversation: Conversation,
        user_message: Message,
        tool_calls_meta: list[dict[str, Any]],
        llm_model: str | None,
        rag_sources: list[dict[str, Any]] | None = None,
        reasoning: str | None = None,
    ) -> AgentTurnResult | None:
        """Выполнить tool_calls; при anti-loop — только лог и мягкое завершение хода."""
        for tc in completion.tool_calls:
            fn = tc["function"]
            name = fn["name"]
            args = self._llm.parse_tool_arguments(fn["arguments"])

            try:
                tool_state.before_tool(name, args, cancel_event=cancel_event)
            except ToolAntiLoopExceeded as exc:
                logger.warning("anti-loop: %s", exc)
                await stream_draft.set_active_tool(name)
                await emit(
                    "tool_done",
                    {
                        "name": name,
                        "summary": self._ANTI_LOOP_SKIP_MSG[:200],
                        "skipped": True,
                    },
                )
                llm_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": self._ANTI_LOOP_SKIP_MSG,
                    }
                )
                if exc.kind == "max_same":
                    partial = await self._complete_after_tool_limit(
                        session,
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
                        llm_model=llm_model,
                        existing_message=stream_draft.message,
                        overflow_note=None,
                        rag_sources=rag_sources,
                        reasoning=reasoning,
                    )
                    if partial is not None:
                        return partial
                    break
                continue

            await stream_draft.set_active_tool(name)
            await emit("tool_start", {"name": name, "arguments": args})
            logger.info("tool_start: %s (round=%d)", name, round_idx + 1)

            try:
                result = await turn_executor.run(name, args)
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
            await stream_draft.add_images(
                result.image_urls,
                result.image_asset_ids,
            )
            for url in result.image_urls:
                await emit("image", {"urls": [url]})

            await emit(
                "tool_done",
                {"name": name, "summary": result_content[:200]},
            )

            llm_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_content,
                }
            )
        return None

    @staticmethod
    def _llm_user_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Копия parts с URL для vision API (/media/asset/{id}/llm при необходимости)."""
        llm_parts = deepcopy(parts)
        for part in llm_parts:
            if part.get("type") != "image_url":
                continue
            raw_asset = part.get("asset_id")
            if raw_asset and not (part.get("image_url") or {}).get("url"):
                try:
                    part["image_url"] = {
                        "url": asset_llm_media_url(uuid.UUID(str(raw_asset)), absolute=True),
                    }
                except ValueError:
                    pass
            elif part.get("image_url", {}).get("url"):
                part["image_url"] = dict(part["image_url"])
                part["image_url"]["url"] = rewrite_image_url_for_llm(
                    part["image_url"]["url"],
                )
        return llm_parts

    async def run_conversation_turn(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        user_text: str,
        attachment_ids: list[uuid.UUID],
        emit: EventEmitter,
        cancel_event: asyncio.Event,
        *,
        display_text: str | None = None,
        llm_model: str | None = None,
        macro_context: str = "selected",
        document_rag: bool = False,
    ) -> AgentTurnResult:
        """
        Полный ход в беседе: сохранение user/assistant, стриминг WS-событий.

        macro_context: ``selected`` | ``full`` | ``semantic`` (top-K по user_text).
        document_rag: подмешать top-K фрагментов документов беседы в system prompt.

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
        preset_tools = tools_for_preset_slug(preset.slug if preset else None)

        att_service = AttachmentService(session)
        attachments = await filter_available_image_attachments(
            session,
            await att_service.prepare_for_llm(attachment_ids),
        )

        stored_text = (
            display_text
            if display_text is not None
            else strip_img2img_gen_preset_prefix(user_text)
        )
        hint_head = user_text.split("\n\n", 1)[0].strip() if user_text else ""
        has_gen_preset = bool(
            hint_head and is_img2img_gen_preset_instruction_block(hint_head)
        )
        log_event(
            logger,
            "img2img_gen_preset_turn",
            "user turn text split",
            preset_slug=preset.slug if preset else None,
            has_gen_preset_block=has_gen_preset,
            llm_text_len=len(user_text),
            stored_text_len=len(stored_text),
            display_text_provided=display_text is not None,
            user_text_preview=user_text[:120] if user_text else "",
            stored_text_preview=stored_text[:120] if stored_text else "",
        )
        stored_parts = build_user_content(stored_text, attachments)
        llm_parts = build_user_content(user_text, attachments)
        if preset and preset.slug == "img2img":
            llm_parts = append_img2img_init_hints(
                llm_parts,
                attachments,
                image_parts=llm_parts,
            )
        user_message = await msg_repo.create(
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content_text=stored_text,
            content_json={"parts": stored_parts},
        )
        if attachment_ids:
            await att_repo.link_to_message(
                attachment_ids,
                message_id=user_message.id,
                conversation_id=conversation_id,
            )

        await emit("ack", {"user_message_id": str(user_message.id)})

        async def push_progress(payload: dict[str, Any]) -> None:
            await emit_progress(conversation_id, payload)

        await push_progress(build_progress(STAGE_LLM_THINKING))
        stale = await msg_repo.settle_stale_streaming_assistant_messages(conversation_id)
        if stale:
            logger.info(
                "Снят streaming с %d зависших черновиков перед новым ходом",
                stale,
            )
        await session.commit()
        logger.info(
            "БД: commit user-сообщения %s перед LLM/tools",
            user_message.id,
        )

        macro_repo = PromptMacroRepository(session)
        all_macros = await macro_repo.list_all()
        alias_to_body = alias_map_from_macros(all_macros)
        from app.services.macro_search_service import apply_macro_context_to_system

        system_prompt = await apply_macro_context_to_system(
            session,
            system_prompt,
            macro_context,
            user_text=stored_text or user_text,
            all_macros=all_macros,
        )
        if macro_context == "full":
            logger.info(
                "macro_context=full: каталог %d макросов в system (лимит %d симв.)",
                len(all_macros),
                settings.macro_context_full_max_chars,
            )
        elif macro_context == "semantic":
            logger.info("macro_context=semantic: top-K по запросу пользователя")

        from app.services.document_rag_service import append_document_rag_to_system

        system_prompt, rag_hits = await append_document_rag_to_system(
            session,
            conversation_id,
            stored_text or user_text,
            system_prompt,
            client_enabled=document_rag,
        )

        history = await msg_repo.list_for_llm(
            conversation_id,
            settings.max_history_messages,
        )
        history = [m for m in history if m.id != user_message.id]

        llm_messages: list[dict[str, Any]] = []
        if system_prompt:
            llm_messages.append({"role": "system", "content": system_prompt})
        llm_messages.extend(history_to_llm_messages(history, alias_to_body=alias_to_body))
        llm_messages.append(
            {
                "role": "user",
                "content": self._llm_user_parts(
                    expand_parts_for_llm(llm_parts, alias_to_body),
                ),
            }
        )

        all_image_urls: list[str] = []
        all_image_asset_ids: list[str] = []
        media_url_rewrites: dict[str, str] = {}
        tool_calls_meta: list[dict[str, Any]] = []
        tool_state = ConversationToolState()
        stream_draft = AssistantStreamDraft(
            session,
            msg_repo,
            conv_repo,
            conversation,
            emit,
        )

        for round_idx in range(settings.max_tool_rounds):
            if cancel_event.is_set():
                raise TurnCancelled("Генерация отменена")

            if round_idx > 0:
                await push_progress(build_progress(STAGE_LLM_THINKING))

            async def _on_delta(chunk: str) -> None:
                await stream_draft.on_delta(chunk)

            async def _on_reasoning(chunk: str) -> None:
                await stream_draft.on_reasoning_delta(chunk)

            log_event(
                logger,
                "llm_request",
                "LLM complete_with_stream",
                turn="conversation",
                round=round_idx + 1,
                model=llm_model or "",
                **summarize_llm_messages(llm_messages),
            )
            completion = await self._llm.complete_with_stream(
                await sanitize_llm_messages_for_vision(session, llm_messages),
                on_text_delta=_on_delta,
                on_reasoning_delta=_on_reasoning,
                cancel_event=cancel_event,
                tools=preset_tools,
                model=llm_model,
            )

            if cancel_event.is_set():
                raise TurnCancelled("Генерация отменена")

            if completion.tool_calls:
                await push_progress(build_progress(STAGE_LLM_TOOLS))
                first_tool = completion.tool_calls[0]["function"]["name"]
                await stream_draft.enter_tool_round(active_tool=first_tool)
                llm_messages.append(
                    {
                        "role": "assistant",
                        "content": completion.content,
                        "tool_calls": completion.tool_calls,
                    }
                )
                tool_calls_meta.extend(completion.tool_calls)

                turn_executor = self._executor(
                    session,
                    conversation_id,
                    source_user_message_id=user_message.id,
                    cancel_event=cancel_event,
                    emit_progress_cb=push_progress,
                )
                anti_loop_done = await self._run_completion_tool_calls(
                    completion=completion,
                    tool_state=tool_state,
                    turn_executor=turn_executor,
                    stream_draft=stream_draft,
                    llm_messages=llm_messages,
                    all_image_urls=all_image_urls,
                    all_image_asset_ids=all_image_asset_ids,
                    media_url_rewrites=media_url_rewrites,
                    emit=emit,
                    round_idx=round_idx,
                    cancel_event=cancel_event,
                    session=session,
                    msg_repo=msg_repo,
                    conv_repo=conv_repo,
                    conversation=conversation,
                    user_message=user_message,
                    tool_calls_meta=tool_calls_meta,
                    llm_model=llm_model,
                    rag_sources=rag_hits or None,
                    reasoning=self._turn_reasoning(stream_draft, completion),
                )
                if anti_loop_done is not None:
                    return anti_loop_done

                logger.info("Раунд tools %d/%d", round_idx + 1, settings.max_tool_rounds)
                continue

            await stream_draft.flush()
            assistant_message = await self._persist_assistant_message(
                msg_repo=msg_repo,
                conv_repo=conv_repo,
                conversation=conversation,
                content_from_llm=self._merge_streamed_llm_text(
                    stream_draft.text,
                    completion.content,
                ),
                all_image_urls=all_image_urls,
                all_image_asset_ids=all_image_asset_ids,
                media_url_rewrites=media_url_rewrites,
                tool_calls_meta=tool_calls_meta,
                existing_message=stream_draft.message,
                rag_sources=rag_hits or None,
                reasoning=self._turn_reasoning(stream_draft, completion),
            )
            await self._emit_turn_done(
                session,
                conversation_id,
                assistant_message,
                emit,
                llm_model=llm_model,
            )
            return AgentTurnResult(
                assistant_text=assistant_message.content_text or "",
                image_urls=all_image_urls,
                user_message=user_message,
                assistant_message=assistant_message,
            )

        partial = await self._complete_after_tool_limit(
            session,
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
            llm_model=llm_model,
            existing_message=stream_draft.message,
            overflow_note=self._tool_loop_overflow_note(),
            rag_sources=rag_hits or None,
            reasoning=self._turn_reasoning(stream_draft, completion),
        )
        if partial is not None:
            return partial
        raise ToolLoopExceeded(f"Превышен лимит вызовов инструментов ({settings.max_tool_rounds})")

    async def run_regenerate_turn(
        self,
        session: AsyncSession,
        conversation_id: uuid.UUID,
        user_message_id: uuid.UUID,
        emit: EventEmitter,
        cancel_event: asyncio.Event,
        *,
        llm_model: str | None = None,
        macro_context: str = "selected",
        document_rag: bool = False,
        llm_text_override: str | None = None,
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
        preset_tools = tools_for_preset_slug(preset.slug if preset else None)

        user_parts: list[dict[str, Any]] | str
        if user_message.content_json and "parts" in user_message.content_json:
            user_parts = user_message.content_json["parts"]
        else:
            user_parts = user_message.content_text or ""

        await emit("ack", {"user_message_id": str(user_message.id)})

        async def push_progress(payload: dict[str, Any]) -> None:
            await emit_progress(conversation_id, payload)

        await push_progress(build_progress(STAGE_LLM_THINKING))
        stale = await msg_repo.settle_stale_streaming_assistant_messages(conversation_id)
        if stale:
            logger.info(
                "Снят streaming с %d зависших черновиков перед перегенерацией",
                stale,
            )
        await session.commit()
        logger.info(
            "БД: commit после delete_after, user %s перед LLM/tools",
            user_message.id,
        )

        macro_repo = PromptMacroRepository(session)
        all_macros = await macro_repo.list_all()
        alias_to_body = alias_map_from_macros(all_macros)
        regen_query = user_message.content_text or ""
        if not regen_query.strip() and isinstance(user_parts, list):
            regen_query = " ".join(
                p.get("text", "")
                for p in user_parts
                if isinstance(p, dict) and p.get("type") == "text"
            )
        from app.services.macro_search_service import apply_macro_context_to_system

        system_prompt = await apply_macro_context_to_system(
            session,
            system_prompt,
            macro_context,
            user_text=regen_query,
            all_macros=all_macros,
        )
        if macro_context == "full":
            logger.info(
                "macro_context=full (regenerate): каталог %d макросов в system",
                len(all_macros),
            )

        from app.services.document_rag_service import append_document_rag_to_system

        system_prompt, rag_hits = await append_document_rag_to_system(
            session,
            conversation_id,
            regen_query,
            system_prompt,
            client_enabled=document_rag,
        )

        history = await msg_repo.list_for_llm(
            conversation_id,
            settings.max_history_messages,
        )
        history = [m for m in history if m.created_at < user_message.created_at]

        att_repo = AttachmentRepository(session)
        user_attachments = await filter_available_image_attachments(
            session,
            await att_repo.list_for_message(user_message_id),
        )

        llm_user_text = (llm_text_override or "").strip() or None
        if llm_user_text:
            hint_head = llm_user_text.split("\n\n", 1)[0].strip()
            has_gen_preset = bool(
                hint_head and is_img2img_gen_preset_instruction_block(hint_head)
            )
            log_event(
                logger,
                "img2img_gen_preset_turn",
                "regenerate with llm_text override",
                preset_slug=preset.slug if preset else None,
                has_gen_preset_block=has_gen_preset,
                llm_text_len=len(llm_user_text),
                stored_text_len=len(user_message.content_text or ""),
                user_text_preview=llm_user_text[:120],
            )

        llm_messages: list[dict[str, Any]] = []
        if system_prompt:
            llm_messages.append({"role": "system", "content": system_prompt})
        llm_messages.extend(history_to_llm_messages(history, alias_to_body=alias_to_body))
        if llm_user_text:
            regen_parts = build_user_content(llm_user_text, user_attachments)
            regen_parts = expand_parts_for_llm(regen_parts, alias_to_body)
            if preset and preset.slug == "img2img":
                regen_parts = append_img2img_init_hints(
                    regen_parts,
                    user_attachments,
                    image_parts=regen_parts,
                )
            regen_parts = await filter_unreachable_image_parts(session, regen_parts)
            llm_messages.append(
                {
                    "role": "user",
                    "content": self._llm_user_parts(regen_parts),
                }
            )
        elif isinstance(user_parts, list):
            regen_parts = refresh_user_parts_for_regenerate(
                user_parts,
                user_attachments,
                fallback_text=user_message.content_text or "",
            )
            regen_parts = expand_parts_for_llm(regen_parts, alias_to_body)
            if preset and preset.slug == "img2img":
                regen_parts = append_img2img_init_hints(
                    regen_parts,
                    user_attachments,
                    image_parts=regen_parts,
                )
            regen_parts = await filter_unreachable_image_parts(session, regen_parts)
            llm_messages.append(
                {
                    "role": "user",
                    "content": self._llm_user_parts(regen_parts),
                }
            )
        else:
            from app.services.prompt_macro_service import expand_macro_text

            regen_text = expand_macro_text(str(user_parts), alias_to_body)
            if preset and preset.slug == "img2img":
                hint = build_img2img_init_hint_text(user_attachments, None)
                if hint:
                    regen_text = f"{regen_text}\n\n{hint}" if regen_text else hint
            llm_messages.append(
                {
                    "role": "user",
                    "content": regen_text,
                }
            )

        all_image_urls: list[str] = []
        all_image_asset_ids: list[str] = []
        media_url_rewrites: dict[str, str] = {}
        tool_calls_meta: list[dict[str, Any]] = []
        tool_state = ConversationToolState()
        stream_draft = AssistantStreamDraft(
            session,
            msg_repo,
            conv_repo,
            conversation,
            emit,
        )

        for round_idx in range(settings.max_tool_rounds):
            if cancel_event.is_set():
                raise TurnCancelled("Генерация отменена")

            if round_idx > 0:
                await push_progress(build_progress(STAGE_LLM_THINKING))

            async def _on_delta(chunk: str) -> None:
                await stream_draft.on_delta(chunk)

            async def _on_reasoning(chunk: str) -> None:
                await stream_draft.on_reasoning_delta(chunk)

            log_event(
                logger,
                "llm_request",
                "LLM complete_with_stream (regenerate)",
                turn="regenerate",
                round=round_idx + 1,
                model=llm_model or "",
                **summarize_llm_messages(llm_messages),
            )
            completion = await self._llm.complete_with_stream(
                await sanitize_llm_messages_for_vision(session, llm_messages),
                on_text_delta=_on_delta,
                on_reasoning_delta=_on_reasoning,
                cancel_event=cancel_event,
                tools=preset_tools,
                model=llm_model,
            )

            if cancel_event.is_set():
                raise TurnCancelled("Генерация отменена")

            if completion.tool_calls:
                await push_progress(build_progress(STAGE_LLM_TOOLS))
                first_tool = completion.tool_calls[0]["function"]["name"]
                await stream_draft.enter_tool_round(active_tool=first_tool)
                llm_messages.append(
                    {
                        "role": "assistant",
                        "content": completion.content,
                        "tool_calls": completion.tool_calls,
                    }
                )
                tool_calls_meta.extend(completion.tool_calls)

                turn_executor = self._executor(
                    session,
                    conversation_id,
                    source_user_message_id=user_message.id,
                    cancel_event=cancel_event,
                    emit_progress_cb=push_progress,
                )
                anti_loop_done = await self._run_completion_tool_calls(
                    completion=completion,
                    tool_state=tool_state,
                    turn_executor=turn_executor,
                    stream_draft=stream_draft,
                    llm_messages=llm_messages,
                    all_image_urls=all_image_urls,
                    all_image_asset_ids=all_image_asset_ids,
                    media_url_rewrites=media_url_rewrites,
                    emit=emit,
                    round_idx=round_idx,
                    cancel_event=cancel_event,
                    session=session,
                    msg_repo=msg_repo,
                    conv_repo=conv_repo,
                    conversation=conversation,
                    user_message=user_message,
                    tool_calls_meta=tool_calls_meta,
                    llm_model=llm_model,
                    rag_sources=rag_hits or None,
                    reasoning=self._turn_reasoning(stream_draft, completion),
                )
                if anti_loop_done is not None:
                    return anti_loop_done

                logger.info("Раунд tools %d/%d", round_idx + 1, settings.max_tool_rounds)
                continue

            await stream_draft.flush()
            assistant_message = await self._persist_assistant_message(
                msg_repo=msg_repo,
                conv_repo=conv_repo,
                conversation=conversation,
                content_from_llm=self._merge_streamed_llm_text(
                    stream_draft.text,
                    completion.content,
                ),
                all_image_urls=all_image_urls,
                all_image_asset_ids=all_image_asset_ids,
                media_url_rewrites=media_url_rewrites,
                tool_calls_meta=tool_calls_meta,
                existing_message=stream_draft.message,
                rag_sources=rag_hits or None,
                reasoning=self._turn_reasoning(stream_draft, completion),
            )
            await self._emit_turn_done(
                session,
                conversation_id,
                assistant_message,
                emit,
                llm_model=llm_model,
            )
            return AgentTurnResult(
                assistant_text=assistant_message.content_text or "",
                image_urls=all_image_urls,
                user_message=user_message,
                assistant_message=assistant_message,
            )

        partial = await self._complete_after_tool_limit(
            session,
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
            llm_model=llm_model,
            existing_message=stream_draft.message,
            overflow_note=self._tool_loop_overflow_note(),
            rag_sources=rag_hits or None,
            reasoning=self._turn_reasoning(stream_draft, completion),
        )
        if partial is not None:
            return partial
        raise ToolLoopExceeded(f"Превышен лимит вызовов инструментов ({settings.max_tool_rounds})")

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
                messages.append(
                    {
                        "role": "assistant",
                        "content": completion.content,
                        "tool_calls": completion.tool_calls,
                    }
                )
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
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_content,
                        }
                    )
                continue

            text = finalize_assistant_text(completion.content)
            if emit and text:
                await emit("text_delta", {"content": text})
            if emit:
                await emit("done", {})
            return AgentTurnResult(assistant_text=text, image_urls=all_image_urls)

        if all_image_urls:
            text = finalize_assistant_text(self._tool_loop_overflow_note())
            if emit:
                await emit("text_delta", {"content": text})
                await emit("done", {})
            return AgentTurnResult(assistant_text=text, image_urls=all_image_urls)
        raise ToolLoopExceeded(f"Превышен лимит вызовов инструментов ({settings.max_tool_rounds})")
