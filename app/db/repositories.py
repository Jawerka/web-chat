"""
Репозитории доступа к данным (async SQLAlchemy).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import String, cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Attachment,
    Conversation,
    DocumentChunk,
    MediaAsset,
    MediaFavorite,
    Message,
    MessageRole,
    Preset,
    PromptMacro,
    PromptMacroCategory,
    User,
    UserRole,
)


class UserRepository:
    """Пользователи (P2.2)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_login(self, login: str) -> User | None:
        normalized = login.strip().lower()
        result = await self._session.execute(
            select(User).where(User.login == normalized).limit(1),
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.slug == slug).limit(1),
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[User]:
        """Все пользователи (для admin API)."""
        result = await self._session.execute(
            select(User).order_by(User.login),
        )
        return list(result.scalars().all())

    async def create_user(
        self,
        *,
        login: str,
        password_hash: str,
        display_name: str,
        role: UserRole = UserRole.USER,
        slug: str | None = None,
    ) -> User:
        login_norm = login.strip().lower()
        slug_val = (slug or login_norm).strip().lower()
        role_val = role.value if isinstance(role, UserRole) else str(role)
        user = User(
            login=login_norm,
            slug=slug_val,
            display_name=display_name.strip() or login_norm,
            password_hash=password_hash,
            role=role_val,
            is_active=True,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def get_or_create_legacy_header_user(
        self,
        *,
        slug: str,
        display_name: str,
        password_hash: str,
    ) -> User:
        """Только для режима X-Web-Chat-User без сессий (тесты / legacy)."""
        existing = await self.get_by_slug(slug)
        if existing is not None:
            return existing
        return await self.create_user(
            login=slug,
            password_hash=password_hash,
            display_name=display_name,
            slug=slug,
            role=UserRole.USER,
        )

    async def touch_last_login(self, user: User) -> None:
        user.last_login_at = datetime.now(UTC)
        await self._session.flush()

    async def update_password_hash(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        await self._session.flush()


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

    @staticmethod
    def _active_only(stmt):
        return stmt.where(Conversation.deleted_at.is_(None))

    @staticmethod
    def _trash_only(stmt):
        return stmt.where(Conversation.deleted_at.isnot(None))

    async def list_all(
        self,
        *,
        owner_user_id: uuid.UUID | None = None,
    ) -> list[Conversation]:
        """Список активных бесед, новые сверху (updated_at DESC)."""
        stmt = self._active_only(
            select(Conversation).order_by(Conversation.updated_at.desc()),
        )
        if owner_user_id is not None:
            stmt = stmt.where(Conversation.owner_user_id == owner_user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_trash(
        self,
        *,
        owner_user_id: uuid.UUID | None = None,
    ) -> list[Conversation]:
        """Беседы в корзине, недавно удалённые сверху."""
        stmt = self._trash_only(
            select(Conversation).order_by(Conversation.deleted_at.desc()),
        )
        if owner_user_id is not None:
            stmt = stmt.where(Conversation.owner_user_id == owner_user_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, conversation_id: uuid.UUID) -> Conversation | None:
        """Беседа по id или None."""
        return await self._session.get(Conversation, conversation_id)

    async def get_by_id_for_owner(
        self,
        conversation_id: uuid.UUID,
        *,
        owner_user_id: uuid.UUID | None,
        include_deleted: bool = False,
    ) -> Conversation | None:
        """Беседа по id с проверкой владельца (P2.2). owner_user_id=None — без фильтра."""
        conversation = await self.get_by_id(conversation_id)
        if conversation is None:
            return None
        if owner_user_id is not None and conversation.owner_user_id != owner_user_id:
            return None
        if conversation.deleted_at is not None and not include_deleted:
            return None
        return conversation

    async def count_by_owner(self, owner_user_id: uuid.UUID) -> int:
        """Число активных бесед пользователя."""
        result = await self._session.execute(
            self._active_only(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.owner_user_id == owner_user_id),
            ),
        )
        return int(result.scalar() or 0)

    async def count_orphans(self) -> int:
        """Беседы без owner_user_id."""
        result = await self._session.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.owner_user_id.is_(None)),
        )
        return int(result.scalar() or 0)

    async def assign_orphan_conversations(self, owner_user_id: uuid.UUID) -> int:
        """Назначить владельца всем беседам с owner_user_id IS NULL."""
        result = await self._session.execute(
            update(Conversation)
            .where(Conversation.owner_user_id.is_(None))
            .values(owner_user_id=owner_user_id),
        )
        return int(result.rowcount or 0)

    async def create(
        self,
        *,
        title: str,
        preset_id: uuid.UUID,
        owner_user_id: uuid.UUID | None = None,
    ) -> Conversation:
        """Создать беседу."""
        now = datetime.now(UTC)
        conversation = Conversation(
            title=title,
            preset_id=preset_id,
            owner_user_id=owner_user_id,
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
        owner_user_id: uuid.UUID | None = None,
    ) -> list[Conversation]:
        """Беседы, в названии которых есть хотя бы одно из слов."""
        if not words:
            return []
        filters = [func.lower(Conversation.title).contains(w.lower()) for w in words]
        stmt = self._active_only(
            select(Conversation)
            .where(or_(*filters))
            .order_by(Conversation.updated_at.desc())
            .limit(limit),
        )
        if owner_user_id is not None:
            stmt = stmt.where(Conversation.owner_user_id == owner_user_id)
        result = await self._session.execute(stmt)
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

    async def move_to_trash(self, conversation: Conversation) -> Conversation:
        """Переместить беседу в корзину (soft delete)."""
        conversation.deleted_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(conversation)
        return conversation

    async def restore_from_trash(self, conversation: Conversation) -> Conversation:
        """Восстановить беседу из корзины."""
        conversation.deleted_at = None
        conversation.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(conversation)
        return conversation

    async def delete_permanent(self, conversation: Conversation) -> None:
        """Окончательно удалить беседу (каскад messages)."""
        await self._session.delete(conversation)

    async def empty_trash_for_owner(
        self,
        *,
        owner_user_id: uuid.UUID | None = None,
    ) -> int:
        """Окончательно удалить все беседы в корзине текущего владельца."""
        stmt = self._trash_only(select(Conversation))
        if owner_user_id is not None:
            stmt = stmt.where(Conversation.owner_user_id == owner_user_id)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        for conv in rows:
            await self._session.delete(conv)
        if rows:
            await self._session.flush()
        return len(rows)

    async def purge_trash_older_than(self, *, days: int) -> int:
        """Окончательно удалить беседы в корзине старше days суток."""
        cutoff = datetime.now(UTC) - timedelta(days=days)
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.deleted_at.isnot(None),
                Conversation.deleted_at < cutoff,
            ),
        )
        rows = list(result.scalars().all())
        for conv in rows:
            await self._session.delete(conv)
        if rows:
            await self._session.flush()
        return len(rows)

    async def list_with_title_prefix(self, prefix: str) -> list[Conversation]:
        """Активные беседы, заголовок которых начинается с prefix."""
        result = await self._session.execute(
            self._active_only(
                select(Conversation)
                .where(Conversation.title.startswith(prefix))
                .order_by(Conversation.updated_at.desc()),
            ),
        )
        return list(result.scalars().all())

    async def touch(self, conversation: Conversation) -> None:
        """Обновить updated_at беседы."""
        conversation.updated_at = datetime.now(UTC)
        await self._session.flush()


@dataclass(frozen=True, slots=True)
class GalleryAssetMeta:
    """Метаданные MediaAsset для списка галереи (без BLOB)."""

    id: uuid.UUID
    mime_type: str
    original_name: str | None
    created_at: datetime
    size_bytes: int
    has_thumb: bool


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

    async def list_images_recent(self, limit: int = 1000) -> list[MediaAsset]:
        """Изображения из БД, новые первыми (полные строки, включая BLOB)."""
        stmt = (
            select(MediaAsset)
            .where(MediaAsset.mime_type.like("image/%"))
            .order_by(MediaAsset.created_at.desc())
            .limit(max(1, int(limit)))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_gallery_metadata(self, limit: int = 1000) -> list[GalleryAssetMeta]:
        """Метаданные для галереи без чтения data/thumb_data из SQLite."""
        cap = max(1, int(limit))
        stmt = (
            select(
                MediaAsset.id,
                MediaAsset.mime_type,
                MediaAsset.original_name,
                MediaAsset.created_at,
                func.length(MediaAsset.data).label("size_bytes"),
                MediaAsset.thumb_data.isnot(None).label("has_thumb"),
            )
            .where(MediaAsset.mime_type.like("image/%"))
            .order_by(MediaAsset.created_at.desc())
            .limit(cap)
        )
        result = await self._session.execute(stmt)
        rows: list[GalleryAssetMeta] = []
        for row in result.all():
            rows.append(
                GalleryAssetMeta(
                    id=row.id,
                    mime_type=row.mime_type,
                    original_name=row.original_name,
                    created_at=row.created_at,
                    size_bytes=int(row.size_bytes or 0),
                    has_thumb=bool(row.has_thumb),
                )
            )
        return rows

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


class MediaFavoriteRepository:
    """Избранное изображений (глобально для галереи)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def make_key(source: str, media_id: str) -> str:
        return f"{source}:{media_id}"

    async def list_all(self) -> list[MediaFavorite]:
        result = await self._session.execute(
            select(MediaFavorite).order_by(MediaFavorite.created_at.desc()),
        )
        return list(result.scalars().all())

    async def favorite_map(self) -> dict[str, datetime]:
        items = await self.list_all()
        out: dict[str, datetime] = {}
        for item in items:
            out[self.make_key(item.media_source, item.media_id)] = item.created_at
        return out

    async def set_favorite(self, *, source: str, media_id: str, is_favorite: bool) -> bool:
        source_norm = source.strip().lower()
        media_norm = media_id.strip()
        if not source_norm or not media_norm:
            return False
        result = await self._session.execute(
            select(MediaFavorite)
            .where(
                MediaFavorite.media_source == source_norm,
                MediaFavorite.media_id == media_norm,
            )
            .limit(1),
        )
        item = result.scalar_one_or_none()
        if is_favorite:
            if item is not None:
                return True
            self._session.add(
                MediaFavorite(
                    media_source=source_norm,
                    media_id=media_norm,
                ),
            )
            await self._session.flush()
            return True
        if item is None:
            return False
        await self._session.delete(item)
        await self._session.flush()
        return False

    async def is_favorite(self, *, source: str, media_id: str) -> bool:
        result = await self._session.execute(
            select(MediaFavorite.id)
            .where(
                MediaFavorite.media_source == source.strip().lower(),
                MediaFavorite.media_id == media_id.strip(),
            )
            .limit(1),
        )
        return result.scalar_one_or_none() is not None

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

    async def count_uploads_for_owner(
        self,
        owner_user_id: uuid.UUID,
        *,
        since: datetime,
    ) -> int:
        """Число вложений в беседах пользователя с created_at >= since."""
        result = await self._session.execute(
            select(func.count())
            .select_from(Attachment)
            .join(Conversation, Attachment.conversation_id == Conversation.id)
            .where(
                Conversation.owner_user_id == owner_user_id,
                Attachment.created_at >= since,
            ),
        )
        return int(result.scalar() or 0)

    async def list_for_message(self, message_id: uuid.UUID) -> list[Attachment]:
        """Вложения, привязанные к сообщению."""
        result = await self._session.execute(
            select(Attachment).where(Attachment.message_id == message_id)
        )
        return list(result.scalars().all())

    async def sync_message_attachments(
        self,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
        attachment_ids: list[uuid.UUID],
    ) -> None:
        """
        Заменить набор вложений сообщения.

        Снятые с сообщения остаются в беседе (message_id=None).
        """
        current = await self.list_for_message(message_id)
        current_ids = {a.id for a in current}
        new_ids = set(attachment_ids)

        for att in current:
            if att.id not in new_ids:
                att.message_id = None

        for aid in new_ids - current_ids:
            att = await self.get_by_id(aid)
            if att is None:
                raise ValueError(f"Вложение {aid} не найдено")
            if att.conversation_id is not None and att.conversation_id != conversation_id:
                raise ValueError(f"Вложение {aid} принадлежит другой беседе")
            att.message_id = message_id
            att.conversation_id = conversation_id

        await self._session.flush()


class DocumentChunkRepository:
    """Фрагменты документов для RAG (P2.3)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def delete_for_attachment(self, attachment_id: uuid.UUID) -> int:
        result = await self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.attachment_id == attachment_id),
        )
        return int(result.rowcount or 0)

    async def create_chunks(
        self,
        *,
        attachment_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        chunks: list[tuple[int, str, list[float] | None]],
    ) -> list[DocumentChunk]:
        rows: list[DocumentChunk] = []
        for index, text, embedding in chunks:
            row = DocumentChunk(
                attachment_id=attachment_id,
                conversation_id=conversation_id,
                chunk_index=index,
                text=text,
                embedding_json=embedding,
            )
            self._session.add(row)
            rows.append(row)
        await self._session.flush()
        return rows

    async def list_for_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> list[tuple[DocumentChunk, Attachment]]:
        result = await self._session.execute(
            select(DocumentChunk, Attachment)
            .join(Attachment, DocumentChunk.attachment_id == Attachment.id)
            .where(DocumentChunk.conversation_id == conversation_id)
            .order_by(DocumentChunk.attachment_id, DocumentChunk.chunk_index),
        )
        return list(result.all())


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

    async def find_messages_containing(
        self,
        fragment: str,
        *,
        limit: int = 200,
    ) -> list[Message]:
        """Сообщения, в тексте или content_json которых встречается фрагмент."""
        if not fragment:
            return []
        pattern = f"%{fragment}%"
        result = await self._session.execute(
            select(Message)
            .where(
                or_(
                    Message.content_text.like(pattern),
                    cast(Message.content_json, String).like(pattern),
                )
            )
            .order_by(Message.created_at.desc())
            .limit(max(1, int(limit)))
        )
        return list(result.scalars().all())

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
        owner_user_id: uuid.UUID | None = None,
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
                Conversation.deleted_at.is_(None),
                Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]),
                Message.content_text.is_not(None),
                or_(*word_filters),
            )
        )
        if owner_user_id is not None:
            stmt = stmt.where(Conversation.owner_user_id == owner_user_id)
        if conversation_id is not None:
            stmt = stmt.where(Message.conversation_id == conversation_id)
        stmt = stmt.order_by(Message.created_at.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.all())

    async def get_last_message(self, conversation_id: uuid.UUID) -> Message | None:
        """Последнее сообщение беседы (любая роль)."""
        result = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def settle_stale_streaming_assistant_messages(
        self,
        conversation_id: uuid.UUID,
        *,
        keep_message_id: uuid.UUID | None = None,
    ) -> int:
        """
        Снять streaming:true со всех assistant, кроме разрешённого.

        По умолчанию streaming допустим только у последнего сообщения беседы
        (и только если это assistant). Все более ранние с streaming:false.

        Args:
            keep_message_id: Явно сохранить streaming у этого id (активный черновик).

        Returns:
            Число обновлённых сообщений.
        """
        last = await self.get_last_message(conversation_id)
        allowed_id: uuid.UUID | None = keep_message_id
        if allowed_id is None and last is not None and last.role == MessageRole.ASSISTANT:
            payload = last.content_json if isinstance(last.content_json, dict) else {}
            if payload.get("streaming"):
                allowed_id = last.id

        result = await self._session.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.ASSISTANT,
            )
            .order_by(Message.created_at.desc())
            .limit(50)
        )
        settled = 0
        for message in result.scalars():
            if allowed_id is not None and message.id == allowed_id:
                continue
            payload = message.content_json if isinstance(message.content_json, dict) else {}
            if not payload.get("streaming"):
                continue
            merged = dict(payload)
            merged["streaming"] = False
            merged["phase"] = None
            merged["active_tool"] = None
            await self.update_content(
                message,
                content_text=message.content_text or "",
                content_json=merged,
            )
            settled += 1
        return settled

    async def get_streaming_assistant_message(
        self,
        conversation_id: uuid.UUID,
    ) -> Message | None:
        """Черновик assistant с streaming — только если это последнее сообщение беседы."""
        last = await self.get_last_message(conversation_id)
        if last is None or last.role != MessageRole.ASSISTANT:
            return None
        payload = last.content_json if isinstance(last.content_json, dict) else {}
        if payload.get("streaming"):
            return last
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
