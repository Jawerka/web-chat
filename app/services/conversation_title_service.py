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

logger = logging.getLogger(__name__)

_TITLE_MAX_WORDS = 7
_TITLE_MAX_CHARS = 80
_TITLE_MAX_TOKENS = 48

_TITLE_SYSTEM = (
    "Ты генерируешь короткое название чата по первому обмену реплик. "
    "Ответ — одна строка, только название на русском: от 2 до 7 слов. "
    "Без кавычек, без точки в конце, без пояснений, без перечисления слов, "
    "без фраз вроде «выберу», «вариант», «это N слов». "
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

    return text


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
    except LLMError as exc:
        logger.warning("Не удалось сгенерировать заголовок беседы %s: %s", conversation_id, exc)
        return None

    title = _normalize_title(raw_title)
    if not title or title == DEFAULT_CONVERSATION_TITLE:
        logger.warning(
            "Пустой или дефолтный заголовок от LLM для беседы %s (raw=%r)",
            conversation_id,
            (raw_title[:160] + "…") if len(raw_title) > 160 else raw_title,
        )
        return None

    if len(title.split()) > _TITLE_MAX_WORDS:
        logger.info(
            "Заголовок обрезан до %d слов для беседы %s",
            _TITLE_MAX_WORDS,
            conversation_id,
        )

    await conv_repo.update(conversation, title=title)
    logger.info("Заголовок беседы %s: %s", conversation_id, title)
    return title
