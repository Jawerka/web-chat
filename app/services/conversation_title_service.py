"""
Автогенерация заголовка беседы через LLM по первым сообщениям.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import DEFAULT_CONVERSATION_TITLE
from app.db.models import Message, MessageRole
from app.db.repositories import ConversationRepository, MessageRepository
from app.integrations.llm_client import LLMClient, LLMError
from app.services.conversation_title_format import strip_generic_conversation_title_prefix

logger = logging.getLogger(__name__)

_TITLE_MAX_WORDS = 7
_TITLE_MAX_CHARS = 80
_TITLE_MAX_TOKENS = 48

_TITLE_SYSTEM = (
    "Ты генерируешь короткое название чата по первому обмену реплик. "
    "Ответ — одна строка, только название на русском: от 2 до 7 слов. "
    "Без кавычек, без точки в конце, без пояснений, без перечисления слов, "
    "без фраз вроде «выберу», «вариант», «это N слов». "
    "Не начинай с общих слов «Генерация», «Генерация изображений», "
    "«Создание изображения» — сразу укажи тему: персонаж, сцена, объект, "
    "стиль или суть запроса пользователя. "
    "Не рассуждай вслух — выведи только заголовок."
)

_QUOTED_TITLE_RE = re.compile(
    r'[«“"]([^»”"]{2,120})[»”"]|\'([^\']{2,120})\'',
)
_META_PREFIX_RE = re.compile(
    r"^(?:выберу|выбрал|итог|ответ|название|вариант|подходящий\s+вариант)"
    r"[^.:]{0,80}[.:]\s*",
    re.IGNORECASE,
)
_WORD_COUNT_SUFFIX_RE = re.compile(
    r"\s*[-—–,]\s*(?:это\s+)?\d+\s+слов(?:а|ов)?\.?\s*$",
    re.IGNORECASE,
)


def _normalize_title(raw: str) -> str:
    """Извлечь короткий заголовок из ответа LLM (в т.ч. с лишними рассуждениями)."""
    text = (raw or "").strip()
    if not text:
        return ""

    quoted = _QUOTED_TITLE_RE.search(text)
    if quoted:
        text = (quoted.group(1) or quoted.group(2) or "").strip()

    text = text.split("\n")[0].strip()
    text = _META_PREFIX_RE.sub("", text)
    text = _WORD_COUNT_SUFFIX_RE.sub("", text)

    if ":" in text and len(text.split(":", 1)[-1].strip()) >= 3:
        text = text.split(":", 1)[-1].strip()

    text = text.strip().strip("\"'«»“”")
    text = re.sub(r"\s+", " ", text).strip(" .,-—–")

    words = [w for w in text.split() if w]
    if len(words) > _TITLE_MAX_WORDS:
        text = " ".join(words[:_TITLE_MAX_WORDS])

    if len(text) > _TITLE_MAX_CHARS:
        text = text[:_TITLE_MAX_CHARS].rsplit(" ", 1)[0].strip()

    return strip_generic_conversation_title_prefix(text)


def _excerpts_from_messages(messages: list[Message], max_posts: int = 3) -> list[str]:
    """Взять до max_posts текстовых фрагментов user/assistant по порядку."""
    excerpts: list[str] = []
    for msg in messages:
        if msg.role not in (MessageRole.USER, MessageRole.ASSISTANT):
            continue
        part = (msg.content_text or "").strip()
        if not part:
            continue
        role_label = "Пользователь" if msg.role == MessageRole.USER else "Ассистент"
        excerpts.append(f"{role_label}: {part[:400]}")
        if len(excerpts) >= max_posts:
            break
    return excerpts


async def _request_title_from_llm(
    llm: LLMClient,
    excerpts: list[str],
    *,
    model: str | None = None,
) -> str:
    """Запросить у LLM короткий заголовок по фрагментам переписки."""
    raw_title = await llm.complete_plain_text(
        [
            {"role": "system", "content": _TITLE_SYSTEM},
            {
                "role": "user",
                "content": (
                    "/no_think\n"
                    "Переписка:\n\n"
                    + "\n\n".join(excerpts)
                    + "\n\nНазвание чата (только текст названия):"
                ),
            },
        ],
        model=model,
        max_tokens=_TITLE_MAX_TOKENS,
        temperature=0.2,
        disable_thinking=True,
        allow_reasoning_fallback=False,
    )
    title = _normalize_title(raw_title)
    if not title or title == DEFAULT_CONVERSATION_TITLE:
        raise ValueError("LLM не предложила подходящее название")
    return title


async def generate_conversation_title(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    llm: LLMClient,
    *,
    model: str | None = None,
) -> str:
    """
    Ручная генерация заголовка по сообщениям беседы (перезаписывает текущий).

    Raises:
        ValueError: беседа не найдена или недостаточно контекста.
        LLMError: LLM недоступен.
    """
    conv_repo = ConversationRepository(session)
    msg_repo = MessageRepository(session)

    conversation = await conv_repo.get_by_id(conversation_id)
    if conversation is None:
        raise ValueError("Беседа не найдена")

    messages = await msg_repo.list_earliest_for_title(conversation_id, limit=8)
    user_posts = sum(1 for m in messages if m.role == MessageRole.USER)
    assistant_posts = sum(1 for m in messages if m.role == MessageRole.ASSISTANT)
    if user_posts < 1 or assistant_posts < 1:
        raise ValueError("Нужен хотя бы один обмен репликами (вопрос и ответ)")

    excerpts = _excerpts_from_messages(messages, max_posts=4)
    if len(excerpts) < 2:
        raise ValueError("Недостаточно текста в переписке для названия")

    title = await _request_title_from_llm(llm, excerpts, model=model)
    await conv_repo.update(conversation, title=title)
    logger.info("Заголовок беседы %s (ручной запрос): %s", conversation_id, title)
    return title


async def maybe_generate_conversation_title(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    llm: LLMClient,
    *,
    model: str | None = None,
) -> str | None:
    """
    Сгенерировать заголовок, если беседа ещё с дефолтным именем.

    Returns:
        Новый заголовок или None, если переименование не выполнялось.
    """
    conv_repo = ConversationRepository(session)
    msg_repo = MessageRepository(session)

    conversation = await conv_repo.get_by_id(conversation_id)
    if conversation is None or conversation.title != DEFAULT_CONVERSATION_TITLE:
        return None

    messages = await msg_repo.list_earliest_for_title(conversation_id, limit=6)
    user_posts = sum(1 for m in messages if m.role == MessageRole.USER)
    assistant_posts = sum(1 for m in messages if m.role == MessageRole.ASSISTANT)
    if user_posts != 1 or assistant_posts < 1:
        logger.debug(
            "Пропуск автозаголовка %s: user=%d assistant=%d (нужен первый обмен)",
            conversation_id,
            user_posts,
            assistant_posts,
        )
        return None

    excerpts = _excerpts_from_messages(messages, max_posts=3)
    if len(excerpts) < 2:
        logger.debug(
            "Пропуск автозаголовка %s: мало текста в переписке (%d фрагментов)",
            conversation_id,
            len(excerpts),
        )
        return None

    try:
        title = await _request_title_from_llm(llm, excerpts, model=model)
    except LLMError as exc:
        logger.warning("Не удалось сгенерировать заголовок беседы %s: %s", conversation_id, exc)
        return None
    except ValueError as exc:
        logger.warning("Автозаголовок беседы %s отклонён: %s", conversation_id, exc)
        return None

    await conv_repo.update(conversation, title=title)
    logger.info("Заголовок беседы %s: %s", conversation_id, title)
    return title
