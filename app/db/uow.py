"""
Unit of Work — абстракция над сессией БД (P1.1, задел под Postgres).

Репозитории остаются в ``repositories.py``; UoW группирует их и границы транзакции.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import (
    AttachmentRepository,
    ConversationRepository,
    MediaAssetRepository,
    MessageRepository,
    PresetRepository,
    PromptMacroRepository,
)


class UnitOfWork(Protocol):
    """Контракт доступа к данным (SQLite сейчас, Postgres позже)."""

    session: AsyncSession

    @property
    def conversations(self) -> ConversationRepository: ...

    @property
    def messages(self) -> MessageRepository: ...

    @property
    def media_assets(self) -> MediaAssetRepository: ...

    @property
    def attachments(self) -> AttachmentRepository: ...

    @property
    def presets(self) -> PresetRepository: ...

    @property
    def prompt_macros(self) -> PromptMacroRepository: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class SqlAlchemyUnitOfWork:
    """Реализация UoW для SQLAlchemy AsyncSession."""

    __slots__ = (
        "_attachments",
        "_conversations",
        "_media_assets",
        "_messages",
        "_presets",
        "_prompt_macros",
        "session",
    )

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._conversations: ConversationRepository | None = None
        self._messages: MessageRepository | None = None
        self._media_assets: MediaAssetRepository | None = None
        self._attachments: AttachmentRepository | None = None
        self._presets: PresetRepository | None = None
        self._prompt_macros: PromptMacroRepository | None = None

    @property
    def conversations(self) -> ConversationRepository:
        if self._conversations is None:
            self._conversations = ConversationRepository(self.session)
        return self._conversations

    @property
    def messages(self) -> MessageRepository:
        if self._messages is None:
            self._messages = MessageRepository(self.session)
        return self._messages

    @property
    def media_assets(self) -> MediaAssetRepository:
        if self._media_assets is None:
            self._media_assets = MediaAssetRepository(self.session)
        return self._media_assets

    @property
    def attachments(self) -> AttachmentRepository:
        if self._attachments is None:
            self._attachments = AttachmentRepository(self.session)
        return self._attachments

    @property
    def presets(self) -> PresetRepository:
        if self._presets is None:
            self._presets = PresetRepository(self.session)
        return self._presets

    @property
    def prompt_macros(self) -> PromptMacroRepository:
        if self._prompt_macros is None:
            self._prompt_macros = PromptMacroRepository(self.session)
        return self._prompt_macros

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        return self

    async def __aexit__(self, *exc: object) -> None:
        if exc[0] is not None:
            await self.rollback()
        else:
            await self.commit()


async def unit_of_work(session: AsyncSession) -> AsyncGenerator[SqlAlchemyUnitOfWork, None]:
    """Dependency-стиль: UoW на одну сессию без автокоммита."""
    yield SqlAlchemyUnitOfWork(session)
