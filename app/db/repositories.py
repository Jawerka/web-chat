"""
Репозитории доступа к данным (async SQLAlchemy).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import String, cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Attachment,
    Conversation,
    MediaAsset,
    Message,
    MessageRole,
    Preset,
    PromptMacro,
    PromptMacroCategory,
)


class PresetRepository:
    """CRUD и операции с пресетами."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[Preset]:
        """Все пресеты, отсортированные по sort_order."""
        result = await self._session.execute(
            select(Preset).order_by(Preset.sort_order, Preset.name)
        )
        return list(result.scalars().all())

    async def get_by_id(self, preset_id: uuid.UUID) -> Preset | None:
        """Пресет по id или None."""
        preset = await self._session.get(Preset, preset_id)
        if preset is not None:
            return preset
        # SQLite: id мог быть вставлен с дефисами (сырой SQL в migrate до нормализации).
        result = await self._session.execute(
            select(Preset).where(
                func.replace(cast(Preset.id, String), "-", "") == preset_id.hex,
            ).limit(1),
        )
        return result.scalar_one_or_none()

    async def get_default(self) -> Preset | None:
        """Пресет с is_default=true."""
        result = await self._session.execute(
            select(Preset).where(Preset.is_default.is_(True)).limit(1)
        )
        return result.scalar_one_or_none()

    async def set_default(self, preset_id: uuid.UUID) -> Preset | None:
        """
        Сделать пресет default для новых бесед.

        Сбрасывает is_default у остальных; ровно один пресет остаётся default.
        """
        preset = await self.get_by_id(preset_id)
        if preset is None:
            return None
        await self._session.execute(update(Preset).values(is_default=False))
        preset.is_default = True
        await self._session.flush()
        return preset

    async def update_system_prompt(
        self,
        preset_id: uuid.UUID,
        system_prompt: str,
    ) -> Preset | None:
        """Обновить системный промпт пресета."""
        preset = await self.get_by_id(preset_id)
        if preset is None:
            return None
        preset.system_prompt = system_prompt
        await self._session.flush()
        return preset


class ConversationRepository:
    """CRUD бесед."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[Conversation]:
        """Список бесед, новые сверху (updated_at DESC)."""
        result = await self._session.execute(
            select(Conversation).order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, conversation_id: uuid.UUID) -> Conversation | None:
        """Беседа по id или None."""
        return await self._session.get(Conversation, conversation_id)

    async def create(
        self,
        *,
        title: str,
        preset_id: uuid.UUID,
    ) -> Conversation:
        """Создать беседу."""
        now = datetime.now(UTC)
        conversation = Conversation(
            title=title,
            preset_id=preset_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(conversation)
        await self._session.flush()
        await self._session.refresh(conversation)
        return conversation

    async def search_by_title_words(
        self,
        words: list[str],
        *,
        limit: int = 20,
    ) -> list[Conversation]:
        """Беседы, в названии которых есть хотя бы одно из слов."""
        if not words:
            return []
        filters = [func.lower(Conversation.title).contains(w.lower()) for w in words]
        result = await self._session.execute(
            select(Conversation)
            .where(or_(*filters))
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update(
        self,
        conversation: Conversation,
        *,
        title: str | None = None,
        preset_id: uuid.UUID | None = None,
    ) -> Conversation:
        """Обновить поля беседы."""
        if title is not None:
            conversation.title = title
        if preset_id is not None:
            conversation.preset_id = preset_id
        conversation.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(conversation)
        return conversation

    async def delete(self, conversation: Conversation) -> None:
        """Удалить беседу (каскад messages)."""
        await self._session.delete(conversation)

    async def touch(self, conversation: Conversation) -> None:
        """Обновить updated_at беседы."""
        conversation.updated_at = datetime.now(UTC)
        await self._session.flush()


class MediaAssetRepository:
    """Изображения в БД."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, asset_id: uuid.UUID) -> MediaAsset | None:
        return await self._session.get(MediaAsset, asset_id)

    async def delete(self, asset: MediaAsset) -> None:
        """Удалить MediaAsset."""
        await self._session.delete(asset)
        await self._session.flush()

    async def list_images_recent(self, limit: int = 200) -> list[MediaAsset]:
        """Изображения из БД, новые первыми."""
        stmt = (
            select(MediaAsset)
            .where(MediaAsset.mime_type.like("image/%"))
            .order_by(MediaAsset.created_at.desc())
            .limit(max(1, min(500, int(limit))))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        data: bytes,
        mime_type: str,
        conversation_id: uuid.UUID | None = None,
        original_name: str | None = None,
        thumb_data: bytes | None = None,
        asset_id: uuid.UUID | None = None,
    ) -> MediaAsset:
        asset = MediaAsset(
            id=asset_id or uuid.uuid4(),
            conversation_id=conversation_id,
            mime_type=mime_type,
            data=data,
            thumb_data=thumb_data,
            original_name=original_name,
        )
        self._session.add(asset)
        await self._session.flush()
        await self._session.refresh(asset)
        return asset


class AttachmentRepository:
    """Операции с вложениями."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, attachment_id: uuid.UUID) -> Attachment | None:
        """Вложение по id или None."""
        return await self._session.get(Attachment, attachment_id)

    async def create(
        self,
        *,
        attachment_id: uuid.UUID,
        original_name: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        conversation_id: uuid.UUID | None = None,
        media_asset_id: uuid.UUID | None = None,
    ) -> Attachment:
        """Создать запись вложения в БД."""
        attachment = Attachment(
            id=attachment_id,
            original_name=original_name,
            mime_type=mime_type,
            size_bytes=size_bytes,
            storage_path=storage_path,
            conversation_id=conversation_id,
            media_asset_id=media_asset_id,
        )
        self._session.add(attachment)
        await self._session.flush()
        await self._session.refresh(attachment)
        return attachment

    async def update_extracted_text(
        self,
        attachment: Attachment,
        extracted_text: str,
    ) -> Attachment:
        """Сохранить кэш извлечённого текста."""
        attachment.extracted_text = extracted_text
        await self._session.flush()
        await self._session.refresh(attachment)
        return attachment

    async def link_to_message(
        self,
        attachment_ids: list[uuid.UUID],
        *,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
    ) -> None:
        """Привязать вложения к сообщению и беседе."""
        for aid in attachment_ids:
            att = await self.get_by_id(aid)
            if att is not None:
                att.message_id = message_id
                att.conversation_id = conversation_id
        await self._session.flush()

    async def list_ids_for_message(self, message_id: uuid.UUID) -> list[uuid.UUID]:
        """UUID вложений, привязанных к сообщению."""
        result = await self._session.execute(
            select(Attachment.id).where(Attachment.message_id == message_id)
        )
        return list(result.scalars().all())

    async def list_for_message(self, message_id: uuid.UUID) -> list[Attachment]:
        """Вложения, привязанные к сообщению."""
        result = await self._session.execute(
            select(Attachment).where(Attachment.message_id == message_id)
        )
        return list(result.scalars().all())


class MessageRepository:
    """Сообщения беседы."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, message_id: uuid.UUID) -> Message | None:
        return await self._session.get(Message, message_id)

    async def create(
        self,
        *,
        conversation_id: uuid.UUID,
        role: MessageRole,
        content_text: str | None = None,
        content_json: dict | None = None,
    ) -> Message:
        """Создать сообщение."""
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content_text=content_text,
            content_json=content_json,
        )
        self._session.add(message)
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def list_all_for_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> list[Message]:
        """Все сообщения беседы в хронологическом порядке."""
        result = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        return list(result.scalars().all())

    async def search_in_content(
        self,
        words: list[str],
        *,
        conversation_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[tuple[Message, Conversation]]:
        """Поиск по content_text: хотя бы одно слово совпало (user/assistant)."""
        if not words:
            return []

        word_filters = [
            func.lower(Message.content_text).contains(w.lower()) for w in words
        ]
        stmt = (
            select(Message, Conversation)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(
                Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]),
                Message.content_text.is_not(None),
                or_(*word_filters),
            )
        )
        if conversation_id is not None:
            stmt = stmt.where(Message.conversation_id == conversation_id)
        stmt = stmt.order_by(Message.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.all())

    async def get_streaming_assistant_message(
        self,
        conversation_id: uuid.UUID,
    ) -> Message | None:
        """Последний черновик assistant с флагом streaming в content_json."""
        result = await self._session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.ASSISTANT,
            )
            .order_by(Message.created_at.desc())
            .limit(15)
        )
        for message in result.scalars():
            payload = message.content_json if isinstance(message.content_json, dict) else {}
            if payload.get("streaming"):
                return message
        return None

    async def list_for_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int = 50,
        before_id: uuid.UUID | None = None,
    ) -> list[Message]:
        """История сообщений (хронологический порядок)."""
        query = select(Message).where(Message.conversation_id == conversation_id)
        if before_id is not None:
            before_msg = await self.get_by_id(before_id)
            if before_msg is not None:
                query = query.where(Message.created_at < before_msg.created_at)
        query = query.order_by(Message.created_at.desc()).limit(limit)
        result = await self._session.execute(query)
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    async def list_earliest_for_title(
        self,
        conversation_id: uuid.UUID,
        *,
        limit: int = 6,
    ) -> list[Message]:
        """Первые сообщения user/assistant для генерации заголовка."""
        result = await self._session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]),
            )
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_for_llm(
        self,
        conversation_id: uuid.UUID,
        max_messages: int,
    ) -> list[Message]:
        """Последние N сообщений user/assistant для контекста LLM."""
        query = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]),
            )
            .order_by(Message.created_at.desc())
            .limit(max_messages)
        )
        result = await self._session.execute(query)
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    async def update_content(
        self,
        message: Message,
        *,
        content_text: str,
        content_json: dict | None = None,
    ) -> Message:
        """Обновить текст сообщения."""
        message.content_text = content_text
        if content_json is not None:
            message.content_json = content_json
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def delete(self, message: Message) -> None:
        """Удалить одно сообщение."""
        await self._session.delete(message)
        await self._session.flush()

    async def delete_after(
        self,
        conversation_id: uuid.UUID,
        *,
        after_created_at: datetime,
    ) -> int:
        """Удалить сообщения строго после указанного времени."""
        result = await self._session.execute(
            delete(Message).where(
                Message.conversation_id == conversation_id,
                Message.created_at > after_created_at,
            )
        )
        await self._session.flush()
        return result.rowcount or 0

    async def delete_message_and_following(self, message: Message) -> None:
        """Удалить сообщение и все последующие в беседе."""
        await self._session.execute(
            delete(Message).where(
                Message.conversation_id == message.conversation_id,
                Message.created_at >= message.created_at,
            )
        )
        await self._session.flush()

    async def get_previous_user_message(
        self,
        conversation_id: uuid.UUID,
        before_created_at: datetime,
    ) -> Message | None:
        """Последнее user-сообщение строго до указанного времени."""
        result = await self._session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.USER,
                Message.created_at < before_created_at,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class PromptMacroRepository:
    """CRUD быстрых промптов (@alias)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(
        self,
        *,
        category: PromptMacroCategory | None = None,
    ) -> list[PromptMacro]:
        stmt = select(PromptMacro)
        if category is not None:
            stmt = stmt.where(PromptMacro.category == category)
        stmt = stmt.order_by(
            PromptMacro.category,
            PromptMacro.sort_order,
            PromptMacro.alias,
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, macro_id: uuid.UUID) -> PromptMacro | None:
        return await self._session.get(PromptMacro, macro_id)

    async def get_by_alias(self, alias: str) -> PromptMacro | None:
        normalized = alias.strip().lstrip("@").lower()
        result = await self._session.execute(
            select(PromptMacro).where(PromptMacro.alias == normalized).limit(1)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        category: PromptMacroCategory,
        alias: str,
        body: str,
        label: str | None = None,
        sort_order: int = 0,
    ) -> PromptMacro:
        macro = PromptMacro(
            category=category,
            alias=alias,
            body=body,
            label=label,
            sort_order=sort_order,
        )
        self._session.add(macro)
        await self._session.flush()
        await self._session.refresh(macro)
        return macro

    async def update(
        self,
        macro: PromptMacro,
        *,
        category: PromptMacroCategory | None = None,
        alias: str | None = None,
        body: str | None = None,
        label: str | None = None,
        sort_order: int | None = None,
    ) -> PromptMacro:
        if category is not None:
            macro.category = category
        if alias is not None:
            macro.alias = alias
        if body is not None:
            macro.body = body
        if label is not None:
            macro.label = label
        if sort_order is not None:
            macro.sort_order = sort_order
        await self._session.flush()
        await self._session.refresh(macro)
        return macro

    async def delete(self, macro: PromptMacro) -> None:
        await self._session.delete(macro)
        await self._session.flush()
