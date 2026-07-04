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
from app.db.models import Conversation, Message, MessageRole
from app.db.repositories import (
    AttachmentRepository,
    ConversationRepository,
    MessageRepository,
    PresetRepository,
    PromptMacroRepository,
)
from app.diag_logging import log_event, summarize_llm_messages
from app.integrations.llm_client import LLMClient, LLMCompletion
from app.integrations.media_utils import asset_llm_media_url, rewrite_image_url_for_llm
from app.integrations.tool_definitions import tools_for_preset_slug
from app.integrations.tool_executor import ToolExecutor, ToolResult
from app.services.attachment_service import AttachmentService
from app.services.conversation_title_service import maybe_generate_conversation_title
from app.services.img2img_tool_coalesce import (
    COALESCED_TOOL_NOTE,
    ToolCallBatch,
    group_tool_call_batches,
)
from app.services.message_builder import (
    append_img2img_batch_hint,
    append_img2img_init_hints,
    build_img2img_init_hint_text,
    build_user_content,
    canonical_stored_image_urls,
    filter_available_image_attachments,
    filter_unreachable_image_parts,
    finalize_assistant_text,
    format_wd14_tag_block,
    history_to_llm_messages,
    is_img2img_gen_preset_instruction_block,
    refresh_user_parts_for_regenerate,
    sanitize_llm_messages_for_vision,
    strip_img2img_gen_preset_prefix,
)
from app.services.prompt_macro_service import (
    alias_map_from_macros,
    expand_parts_for_llm,
)
from app.services.streaming_draft import AssistantStreamDraft
from app.services.turn_context import TurnContext
from app.services.turn_db import open_turn_session
from app.services.turn_exceptions import (
    ToolAntiLoopExceeded,
    ToolLoopExceeded,
    TurnCancelled,
)
from app.services.turn_realtime import emit_turn_progress, turn_realtime
from app.services.turn_status import patch_completed
from app.services.user_progress import (
    STAGE_LLM_THINKING,
    STAGE_LLM_TOOLS,
    STAGE_WD_TAGGER,
    build_progress,
    is_sd_tool,
)
from app.services.wd14_tag_service import tag_user_attachments

logger = logging.getLogger(__name__)

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]

# Один активный SD-запрос на процесс (P4.2); extract_text/get_gallery — параллельно.
_SD_TOOL_SEMAPHORE = asyncio.Semaphore(1)


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
        session: AsyncSession | None,
        conversation_id: uuid.UUID,
        *,
        source_user_message_id: uuid.UUID | None = None,
        cancel_event: asyncio.Event | None = None,
        emit_progress_cb: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        emit_image_cb: Callable[[list[str], list[str] | None], Awaitable[None]]
        | None = None,
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
            emit_image=emit_image_cb,
        )

    @staticmethod
    def _collect_tool_images(
        result,
        all_image_urls: list[str],
        all_image_asset_ids: list[str],
        media_url_rewrites: dict[str, str],
    ) -> None:
        """Добавить URL/asset id из результата инструмента."""
        if result.image_asset_ids:
            for aid in result.image_asset_ids:
                if aid not in all_image_asset_ids:
                    all_image_asset_ids.append(aid)
        for url in canonical_stored_image_urls(
            result.image_urls,
            result.image_asset_ids,
        ):
            if url not in all_image_urls:
                all_image_urls.append(url)
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
        existing_message_id: uuid.UUID | None = None,
        rag_sources: list[dict[str, Any]] | None = None,
        reasoning: str | None = None,
    ) -> Message:
        body = content_from_llm or ""
        if overflow_note:
            body = f"{overflow_note}\n\n{body}".strip() if body else overflow_note
        text = self._finalize_assistant_text(body, media_url_rewrites)
        reasoning_text = (reasoning or "").strip() or None
        stored_images = canonical_stored_image_urls(
            all_image_urls,
            all_image_asset_ids,
        )
        payload: dict[str, Any] = {
            "images": stored_images,
            "image_asset_ids": all_image_asset_ids,
            "tool_calls": tool_calls_meta,
            "reasoning": reasoning_text,
        }
        if rag_sources:
            payload["rag_sources"] = rag_sources
        content_json = patch_completed(payload)
        if existing_message_id is not None:
            existing_message = await msg_repo.get_by_id(existing_message_id)
            if existing_message is not None:
                await msg_repo.update_content(
                    existing_message,
                    content_text=text,
                    content_json=content_json,
                )
                await conv_repo.touch(conversation)
                turn_realtime().clear_streaming_message(conversation.id)
                return existing_message
        message = await msg_repo.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content_text=text,
            content_json=content_json,
        )
        await conv_repo.touch(conversation)
        turn_realtime().clear_streaming_message(conversation.id)
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
        turn_realtime().clear_progress(conversation_id)
        await emit("done", payload)

    _ANTI_LOOP_SKIP_MSG = (
        "Вызов инструмента пропущен: лимит повторов в этом ходе."
    )
    _ANTI_LOOP_EARLY_DONE_NOTE = (
        "Генерация завершена: повторные вызовы инструмента остановлены."
    )

    async def _complete_turn_after_anti_loop(
        self,
        *,
        ctx: TurnContext,
        reasoning: str | None,
        overflow_note: str | None = None,
    ) -> AgentTurnResult | None:
        """Сохранить частичный ответ после лимита anti-loop."""
        async with open_turn_session() as session:
            msg_repo = MessageRepository(session)
            conv_repo = ConversationRepository(session)
            conversation = await conv_repo.get_by_id(ctx.conversation_id)
            user_message = await msg_repo.get_by_id(ctx.user_message_id)
            if conversation is None or user_message is None:
                return None
            partial = await self._complete_after_tool_limit(
                session,
                msg_repo=msg_repo,
                conv_repo=conv_repo,
                conversation=conversation,
                user_message=user_message,
                content_from_llm=None,
                all_image_urls=ctx.all_image_urls,
                all_image_asset_ids=ctx.all_image_asset_ids,
                media_url_rewrites=ctx.media_url_rewrites,
                tool_calls_meta=ctx.tool_calls_meta,
                emit=ctx.emit,
                llm_model=ctx.llm_model,
                existing_message_id=ctx.existing_message_id,
                overflow_note=overflow_note,
                rag_sources=ctx.rag_sources,
                reasoning=reasoning,
            )
            await session.commit()
        return partial

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
        existing_message_id: uuid.UUID | None = None,
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
            existing_message_id=existing_message_id,
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

    async def _execute_single_tool_call(
        self,
        *,
        ctx: TurnContext,
        tc: dict[str, Any],
        name: str,
        args: dict[str, Any],
        turn_executor: ToolExecutor,
        round_idx: int,
    ) -> tuple[dict[str, Any], str, ToolResult]:
        """Один tool_call; SD-tools сериализуются через _SD_TOOL_SEMAPHORE (P4.2)."""
        stream_draft = ctx.stream_draft
        assert stream_draft is not None
        await stream_draft.set_active_tool(name)
        await ctx.emit("tool_start", {"name": name, "arguments": args})
        log_event(
            logger,
            "tool_start",
            f"tool_start {name} round={round_idx + 1}",
            tool=name,
            round=round_idx + 1,
        )
        try:

            async def _run() -> ToolResult:
                return await turn_executor.run(name, args)

            if is_sd_tool(name):
                async with _SD_TOOL_SEMAPHORE:
                    result = await _run()
            else:
                result = await _run()
        except Exception as exc:
            logger.exception("Ошибка инструмента %s", name)
            result = ToolResult(
                content=f"Ошибка инструмента {name}: {exc}",
                image_urls=[],
            )
        return tc, name, result

    async def _apply_tool_result_to_ctx(
        self,
        *,
        ctx: TurnContext,
        tc: dict[str, Any],
        name: str,
        result: ToolResult,
    ) -> None:
        stream_draft = ctx.stream_draft
        assert stream_draft is not None
        result_content = result.content
        self._collect_tool_images(
            result,
            ctx.all_image_urls,
            ctx.all_image_asset_ids,
            ctx.media_url_rewrites,
        )
        await stream_draft.add_images(
            result.image_urls,
            result.image_asset_ids,
        )
        if not result.images_streamed:
            for url in canonical_stored_image_urls(
                result.image_urls,
                result.image_asset_ids,
            ):
                await ctx.emit("image", {"urls": [url]})
        await ctx.emit(
            "tool_done",
            {"name": name, "summary": result_content[:200]},
        )
        ctx.llm_messages.append(
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_content,
            }
        )

    async def _apply_coalesced_sibling_tool_message(
        self,
        *,
        ctx: TurnContext,
        tc: dict[str, Any],
        name: str,
        result: ToolResult,
    ) -> None:
        """Ответ LLM на объединённый sibling tool_call (без повторной отправки картинок)."""
        stream_draft = ctx.stream_draft
        assert stream_draft is not None
        note = f"{COALESCED_TOOL_NOTE}\n\n{result.content[:500]}"
        await stream_draft.set_active_tool(name)
        await ctx.emit(
            "tool_done",
            {"name": name, "summary": COALESCED_TOOL_NOTE[:200], "coalesced": True},
        )
        ctx.llm_messages.append(
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": note,
            }
        )

    async def _run_completion_tool_calls(
        self,
        *,
        ctx: TurnContext,
        completion,
        turn_executor: ToolExecutor,
        round_idx: int,
    ) -> AgentTurnResult | None:
        """Выполнить tool_calls; при anti-loop — только лог и мягкое завершение хода."""
        stream_draft = ctx.stream_draft
        assert stream_draft is not None
        reasoning = self._turn_reasoning(stream_draft, completion)

        parsed: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
        for tc in completion.tool_calls:
            fn = tc["function"]
            parsed.append(
                (
                    tc,
                    fn["name"],
                    self._llm.parse_tool_arguments(fn["arguments"]),
                )
            )
        batches = group_tool_call_batches(parsed)
        pending: list[ToolCallBatch] = []

        for batch in batches:
            tc, name, _args = batch.primary
            exec_args = batch.execution_args()

            try:
                ctx.tool_state.before_tool(
                    name,
                    exec_args,
                    cancel_event=ctx.cancel_event,
                )
            except ToolAntiLoopExceeded as exc:
                log_event(
                    logger,
                    "anti_loop",
                    str(exc),
                    level=logging.WARNING,
                    tool=name,
                    kind=exc.kind,
                )
                for sibling_tc, sibling_name, _ in batch.entries:
                    await stream_draft.set_active_tool(sibling_name)
                    await ctx.emit(
                        "tool_done",
                        {
                            "name": sibling_name,
                            "summary": self._ANTI_LOOP_SKIP_MSG[:200],
                            "skipped": True,
                            "kind": exc.kind,
                        },
                    )
                    ctx.llm_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": sibling_tc["id"],
                            "content": self._ANTI_LOOP_SKIP_MSG,
                        }
                    )
                if exc.kind == "duplicate":
                    ctx.consecutive_tool_skips += 1
                should_finish = exc.kind == "max_same" or (
                    exc.kind == "duplicate"
                    and ctx.consecutive_tool_skips
                    >= settings.max_consecutive_tool_skips
                    and bool(ctx.all_image_urls)
                )
                if should_finish:
                    overflow = (
                        self._ANTI_LOOP_EARLY_DONE_NOTE
                        if exc.kind == "duplicate"
                        else None
                    )
                    partial = await self._complete_turn_after_anti_loop(
                        ctx=ctx,
                        reasoning=reasoning,
                        overflow_note=overflow,
                    )
                    if partial is not None:
                        return partial
                    break
                continue

            pending.append(batch)

        if pending:
            executed = await asyncio.gather(
                *[
                    self._execute_single_tool_call(
                        ctx=ctx,
                        tc=batch.primary[0],
                        name=batch.name,
                        args=batch.execution_args(),
                        turn_executor=turn_executor,
                        round_idx=round_idx,
                    )
                    for batch in pending
                ],
            )
            ctx.consecutive_tool_skips = 0
            for batch, (_tc, name, result) in zip(pending, executed, strict=True):
                primary_tc = batch.primary[0]
                await self._apply_tool_result_to_ctx(
                    ctx=ctx,
                    tc=primary_tc,
                    name=name,
                    result=result,
                )
                for sibling_tc, sibling_name, _ in batch.entries[1:]:
                    await self._apply_coalesced_sibling_tool_message(
                        ctx=ctx,
                        tc=sibling_tc,
                        name=sibling_name,
                        result=result,
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
        wd_tagger: bool = True,
    ) -> AgentTurnResult:
        """
        Полный ход в беседе: сохранение user/assistant, стриминг WS-событий.

        Сессия БД открывается только на короткие операции (P3.1), не на время LLM/SD.

        Raises:
            ValueError: Беседа не найдена.
            ToolLoopExceeded: Слишком много раундов tools.
            TurnCancelled: Отмена через cancel_event.
            LLMError: Ошибка LLM.
        """
        async def push_progress(payload: dict[str, Any]) -> None:
            await emit_turn_progress(conversation_id, payload)

        preset_tools: list[dict[str, Any]] | None = None
        llm_messages: list[dict[str, Any]] = []
        rag_hits: list[dict[str, Any]] | None = None
        user_message_id: uuid.UUID

        async with open_turn_session() as session:
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

            wd14_entries = []
            if wd_tagger and settings.wd_tagger_enabled and attachments:
                await push_progress(build_progress(STAGE_WD_TAGGER))
                wd14_entries = await tag_user_attachments(session, attachments)

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
            llm_text_for_build = user_text
            if wd14_entries:
                tag_block = format_wd14_tag_block(wd14_entries)
                if tag_block:
                    llm_text_for_build = f"{tag_block}\n\n{user_text}" if user_text else tag_block
            stored_parts = build_user_content(stored_text, attachments)
            llm_parts = build_user_content(llm_text_for_build, attachments)
            if preset and preset.slug == "img2img":
                llm_parts = append_img2img_init_hints(
                    llm_parts,
                    attachments,
                    image_parts=llm_parts,
                )
                llm_parts = append_img2img_batch_hint(llm_parts, llm_text_for_build)
            content_json: dict[str, Any] = {"parts": stored_parts}
            if wd14_entries:
                content_json["wd14"] = [e.to_dict() for e in wd14_entries]
            user_message = await msg_repo.create(
                conversation_id=conversation_id,
                role=MessageRole.USER,
                content_text=stored_text,
                content_json=content_json,
            )
            user_message_id = user_message.id
            if attachment_ids:
                await att_repo.link_to_message(
                    attachment_ids,
                    message_id=user_message.id,
                    conversation_id=conversation_id,
                )
            await conv_repo.update(conversation, clear_composer_draft=True)
            purged = await att_repo.delete_pending_for_conversation(conversation_id)
            if purged:
                logger.info(
                    "Удалено %d неотправленных pending-вложений беседы %s",
                    purged,
                    conversation_id,
                )

            await emit("ack", {"user_message_id": str(user_message.id)})

            await push_progress(build_progress(STAGE_LLM_THINKING))
            stale = await msg_repo.settle_stale_streaming_assistant_messages(
                conversation_id,
            )
            if stale:
                logger.info(
                    "Снят streaming с %d зависших черновиков перед новым ходом",
                    stale,
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

            if system_prompt:
                llm_messages.append({"role": "system", "content": system_prompt})
            llm_messages.extend(
                history_to_llm_messages(history, alias_to_body=alias_to_body),
            )
            llm_messages.append(
                {
                    "role": "user",
                    "content": self._llm_user_parts(
                        expand_parts_for_llm(llm_parts, alias_to_body),
                    ),
                }
            )

            await session.commit()
            logger.info(
                "БД: commit user-сообщения %s перед LLM/tools",
                user_message.id,
            )

        turn_ctx = TurnContext(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            emit=emit,
            cancel_event=cancel_event,
            llm_model=llm_model,
            llm_messages=llm_messages,
            rag_sources=rag_hits or None,
        )
        stream_draft = turn_ctx.stream_draft
        assert stream_draft is not None

        async def push_tool_images(
            urls: list[str],
            asset_ids: list[str] | None = None,
        ) -> None:
            if not urls:
                return
            await stream_draft.add_images(urls, asset_ids)
            for url in canonical_stored_image_urls(urls, asset_ids):
                await emit("image", {"urls": [url]})

        completion: LLMCompletion | None = None

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
            async with open_turn_session() as session:
                llm_payload = await sanitize_llm_messages_for_vision(
                    session,
                    llm_messages,
                )
                await session.commit()
            completion = await self._llm.complete_with_stream(
                llm_payload,
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
                turn_ctx.llm_messages.append(
                    {
                        "role": "assistant",
                        "content": completion.content,
                        "tool_calls": completion.tool_calls,
                    }
                )
                turn_ctx.tool_calls_meta.extend(completion.tool_calls)

                turn_executor = self._executor(
                    None,
                    conversation_id,
                    source_user_message_id=user_message_id,
                    cancel_event=cancel_event,
                    emit_progress_cb=push_progress,
                    emit_image_cb=push_tool_images,
                )
                anti_loop_done = await self._run_completion_tool_calls(
                    ctx=turn_ctx,
                    completion=completion,
                    turn_executor=turn_executor,
                    round_idx=round_idx,
                )
                if anti_loop_done is not None:
                    return anti_loop_done

                logger.info("Раунд tools %d/%d", round_idx + 1, settings.max_tool_rounds)
                continue

            await stream_draft.flush()
            async with open_turn_session() as session:
                msg_repo = MessageRepository(session)
                conv_repo = ConversationRepository(session)
                conversation = await conv_repo.get_by_id(conversation_id)
                user_message = await msg_repo.get_by_id(user_message_id)
                if conversation is None or user_message is None:
                    raise ValueError("Беседа или user-сообщение не найдены")
                assistant_message = await self._persist_assistant_message(
                    msg_repo=msg_repo,
                    conv_repo=conv_repo,
                    conversation=conversation,
                    content_from_llm=self._merge_streamed_llm_text(
                        stream_draft.text,
                        completion.content,
                    ),
                    all_image_urls=turn_ctx.all_image_urls,
                    all_image_asset_ids=turn_ctx.all_image_asset_ids,
                    media_url_rewrites=turn_ctx.media_url_rewrites,
                    tool_calls_meta=turn_ctx.tool_calls_meta,
                    existing_message_id=stream_draft.message_id,
                    rag_sources=turn_ctx.rag_sources,
                    reasoning=self._turn_reasoning(stream_draft, completion),
                )
                await self._emit_turn_done(
                    session,
                    conversation_id,
                    assistant_message,
                    emit,
                    llm_model=llm_model,
                )
                await session.commit()
            return AgentTurnResult(
                assistant_text=assistant_message.content_text or "",
                image_urls=turn_ctx.all_image_urls,
                user_message=user_message,
                assistant_message=assistant_message,
            )

        assert completion is not None
        async with open_turn_session() as session:
            msg_repo = MessageRepository(session)
            conv_repo = ConversationRepository(session)
            conversation = await conv_repo.get_by_id(conversation_id)
            user_message = await msg_repo.get_by_id(user_message_id)
            if conversation is None or user_message is None:
                raise ValueError("Беседа или user-сообщение не найдены")
            partial = await self._complete_after_tool_limit(
                session,
                msg_repo=msg_repo,
                conv_repo=conv_repo,
                conversation=conversation,
                user_message=user_message,
                content_from_llm=None,
                all_image_urls=turn_ctx.all_image_urls,
                all_image_asset_ids=turn_ctx.all_image_asset_ids,
                media_url_rewrites=turn_ctx.media_url_rewrites,
                tool_calls_meta=turn_ctx.tool_calls_meta,
                emit=emit,
                llm_model=llm_model,
                existing_message_id=stream_draft.message_id,
                overflow_note=self._tool_loop_overflow_note(),
                rag_sources=turn_ctx.rag_sources,
                reasoning=self._turn_reasoning(stream_draft, completion),
            )
            await session.commit()
        if partial is not None:
            return partial
        raise ToolLoopExceeded(f"Превышен лимит вызовов инструментов ({settings.max_tool_rounds})")

    async def run_regenerate_turn(
        self,
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
        async def push_progress(payload: dict[str, Any]) -> None:
            await emit_turn_progress(conversation_id, payload)

        preset_tools: list[dict[str, Any]] | None = None
        llm_messages: list[dict[str, Any]] = []
        rag_hits: list[dict[str, Any]] | None = None

        async with open_turn_session() as session:
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

            await push_progress(build_progress(STAGE_LLM_THINKING))
            stale = await msg_repo.settle_stale_streaming_assistant_messages(
                conversation_id,
            )
            if stale:
                logger.info(
                    "Снят streaming с %d зависших черновиков перед перегенерацией",
                    stale,
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

            if system_prompt:
                llm_messages.append({"role": "system", "content": system_prompt})
            llm_messages.extend(
                history_to_llm_messages(history, alias_to_body=alias_to_body),
            )
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

            await session.commit()
            logger.info(
                "БД: commit после delete_after, user %s перед LLM/tools",
                user_message.id,
            )

        turn_ctx = TurnContext(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            emit=emit,
            cancel_event=cancel_event,
            llm_model=llm_model,
            llm_messages=llm_messages,
            rag_sources=rag_hits or None,
        )
        stream_draft = turn_ctx.stream_draft
        assert stream_draft is not None

        async def push_tool_images(
            urls: list[str],
            asset_ids: list[str] | None = None,
        ) -> None:
            if not urls:
                return
            await stream_draft.add_images(urls, asset_ids)
            for url in canonical_stored_image_urls(urls, asset_ids):
                await emit("image", {"urls": [url]})

        completion: LLMCompletion | None = None

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
            async with open_turn_session() as session:
                llm_payload = await sanitize_llm_messages_for_vision(
                    session,
                    llm_messages,
                )
                await session.commit()
            completion = await self._llm.complete_with_stream(
                llm_payload,
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
                turn_ctx.llm_messages.append(
                    {
                        "role": "assistant",
                        "content": completion.content,
                        "tool_calls": completion.tool_calls,
                    }
                )
                turn_ctx.tool_calls_meta.extend(completion.tool_calls)

                turn_executor = self._executor(
                    None,
                    conversation_id,
                    source_user_message_id=user_message_id,
                    cancel_event=cancel_event,
                    emit_progress_cb=push_progress,
                    emit_image_cb=push_tool_images,
                )
                anti_loop_done = await self._run_completion_tool_calls(
                    ctx=turn_ctx,
                    completion=completion,
                    turn_executor=turn_executor,
                    round_idx=round_idx,
                )
                if anti_loop_done is not None:
                    return anti_loop_done

                logger.info("Раунд tools %d/%d", round_idx + 1, settings.max_tool_rounds)
                continue

            await stream_draft.flush()
            async with open_turn_session() as session:
                msg_repo = MessageRepository(session)
                conv_repo = ConversationRepository(session)
                conversation = await conv_repo.get_by_id(conversation_id)
                user_message = await msg_repo.get_by_id(user_message_id)
                if conversation is None or user_message is None:
                    raise ValueError("Беседа или user-сообщение не найдены")
                assistant_message = await self._persist_assistant_message(
                    msg_repo=msg_repo,
                    conv_repo=conv_repo,
                    conversation=conversation,
                    content_from_llm=self._merge_streamed_llm_text(
                        stream_draft.text,
                        completion.content,
                    ),
                    all_image_urls=turn_ctx.all_image_urls,
                    all_image_asset_ids=turn_ctx.all_image_asset_ids,
                    media_url_rewrites=turn_ctx.media_url_rewrites,
                    tool_calls_meta=turn_ctx.tool_calls_meta,
                    existing_message_id=stream_draft.message_id,
                    rag_sources=turn_ctx.rag_sources,
                    reasoning=self._turn_reasoning(stream_draft, completion),
                )
                await self._emit_turn_done(
                    session,
                    conversation_id,
                    assistant_message,
                    emit,
                    llm_model=llm_model,
                )
                await session.commit()
            return AgentTurnResult(
                assistant_text=assistant_message.content_text or "",
                image_urls=turn_ctx.all_image_urls,
                user_message=user_message,
                assistant_message=assistant_message,
            )

        assert completion is not None
        async with open_turn_session() as session:
            msg_repo = MessageRepository(session)
            conv_repo = ConversationRepository(session)
            conversation = await conv_repo.get_by_id(conversation_id)
            user_message = await msg_repo.get_by_id(user_message_id)
            if conversation is None or user_message is None:
                raise ValueError("Беседа или user-сообщение не найдены")
            partial = await self._complete_after_tool_limit(
                session,
                msg_repo=msg_repo,
                conv_repo=conv_repo,
                conversation=conversation,
                user_message=user_message,
                content_from_llm=None,
                all_image_urls=turn_ctx.all_image_urls,
                all_image_asset_ids=turn_ctx.all_image_asset_ids,
                media_url_rewrites=turn_ctx.media_url_rewrites,
                tool_calls_meta=turn_ctx.tool_calls_meta,
                emit=emit,
                llm_model=llm_model,
                existing_message_id=stream_draft.message_id,
                overflow_note=self._tool_loop_overflow_note(),
                rag_sources=turn_ctx.rag_sources,
                reasoning=self._turn_reasoning(stream_draft, completion),
            )
            await session.commit()
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
                async def _run_cli_tool(
                    tc: dict[str, Any],
                ) -> tuple[dict[str, Any], str, str, list[str]]:
                    fn = tc["function"]
                    name = fn["name"]
                    args = self._llm.parse_tool_arguments(fn["arguments"])
                    if emit:
                        await emit("tool_start", {"name": name, "arguments": args})
                    try:

                        async def _run() -> ToolResult:
                            return await tools.run(name, args)

                        if is_sd_tool(name):
                            async with _SD_TOOL_SEMAPHORE:
                                result = await _run()
                        else:
                            result = await _run()
                        result_content = result.content
                        result_urls = list(result.image_urls)
                    except Exception as exc:
                        result_content = f"Ошибка инструмента {name}: {exc}"
                        result_urls = []
                    return tc, name, result_content, result_urls

                cli_results = await asyncio.gather(
                    *[_run_cli_tool(tc) for tc in completion.tool_calls],
                )
                for tc, name, result_content, result_urls in cli_results:
                    for url in canonical_stored_image_urls(result_urls, None):
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
