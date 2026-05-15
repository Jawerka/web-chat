"""
Начальные данные пресетов (раздел 16 TODO.md).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PROMPT = """Ты полезный ассистент в приватном локальном чате.

Правила:
- Отвечай на языке пользователя, ясно и по делу.
- Если доступны инструменты — используй их вместо выдумывания фактов.
- Никогда не придумывай URL файлов, изображений или ссылок на ресурсы.
- Если не хватает данных — спроси уточнение."""

IMAGE_GEN_PROMPT = (
    "Ты помощник с доступом к генерации изображений через Stable Diffusion "
    "(инструмент generate_image).\n\n"
    "Когда пользователь просит создать, нарисовать, сгенерировать, изменить картинку:\n"
    "1. Сформируй детальный prompt (на английском предпочтительно для SD).\n"
    "2. Вызови generate_image с подходящими параметрами.\n"
    "3. В ответе пользователю обязательно покажи ВСЕ URL из результата инструмента "
    "как markdown-изображения: ![описание](url).\n"
    "4. Не придумывай ссылки. Если генерация не удалась — объясни ошибку простым языком.\n\n"
    "Если нужно несколько вариантов — увеличь batch или сделай несколько вызовов, "
    "если API позволяет."
)

DOCUMENT_ANALYSIS_PROMPT = """Ты помощник по анализу документов пользователя.

Правила:
- Текст документа может быть уже вставлен в сообщение пользователя.
- Если текста нет — вызови extract_text с attachment_id.
- Структурируй ответ: краткое резюме, ключевые пункты, при необходимости цитаты.
- Указывай имя файла, когда ссылаешься на документ.
- Не выдумывай содержимое, которого нет в тексте документа."""


@dataclass(frozen=True, slots=True)
class PresetSeed:
    """Описание одного seed-пресета."""

    name: str
    slug: str
    system_prompt: str
    is_default: bool
    sort_order: int


PRESET_SEEDS: tuple[PresetSeed, ...] = (
    PresetSeed(
        name="По умолчанию",
        slug="default",
        system_prompt=DEFAULT_PROMPT,
        is_default=True,
        sort_order=0,
    ),
    PresetSeed(
        name="Генерация изображений",
        slug="image_gen",
        system_prompt=IMAGE_GEN_PROMPT,
        is_default=False,
        sort_order=1,
    ),
    PresetSeed(
        name="Анализ документов",
        slug="document_analysis",
        system_prompt=DOCUMENT_ANALYSIS_PROMPT,
        is_default=False,
        sort_order=2,
    ),
)
