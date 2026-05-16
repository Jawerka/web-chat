"""
JSON-схемы инструментов для OpenAI-compatible API.

Имена функций должны совпадать с MCP tools и обработчиками ToolExecutor.
"""

from __future__ import annotations

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Сгенерировать изображение по текстовому описанию через Stable Diffusion (txt2img). "
                "Только для новой картинки с нуля. Пресет беседы: «Генерация с нуля (txt2img)». "
                "После вызова картинки автоматически появятся в чате; в тексте ответа "
                "пользователю URL и markdown-картинки не нужны. "
                "Для нескольких картинок (до 10) укажи count за один вызов."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Детальное описание изображения",
                    },
                    "negative_prompt": {"type": "string", "default": ""},
                    "width": {"type": "integer", "default": 1024},
                    "height": {"type": "integer", "default": 1024},
                    "steps": {"type": "integer", "default": 22},
                    "cfg_scale": {"type": "number", "default": 5.0},
                    "sampler_name": {"type": "string", "default": "Euler a"},
                    "scheduler": {"type": "string", "default": ""},
                    "seed": {"type": "integer", "default": -1},
                    "restore_faces": {"type": "boolean", "default": False},
                    "tiling": {"type": "boolean", "default": False},
                    "description": {"type": "string", "default": ""},
                    "count": {
                        "type": "integer",
                        "default": 1,
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Сколько изображений сгенерировать за один вызов (1–10)",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_text",
            "description": (
                "Извлечь текст из файла, загруженного пользователем "
                "(PDF, DOCX, TXT, изображение с OCR)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "attachment_id": {
                        "type": "string",
                        "description": "UUID вложения",
                    },
                    "max_chars": {"type": "integer", "default": 50000},
                },
                "required": ["attachment_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "img2img",
            "description": (
                "Перерисовать или доработать существующее изображение (img2img, SD WebUI). "
                "Пресет беседы: «Перерисовка (img2img)». "
                "Обязателен исходник: init_image_url из истории (строка URL: …/media/asset/…) "
                "или attachment_id вложения в текущем сообщении. "
                "Не использовать для картинки с нуля — для этого другой пресет и generate_image. "
                "width/height: 0 = размер исходника. "
                "denoising_strength: 0.20–0.36 мелкие правки; 0.37–0.48 косметика; "
                "0.49–0.62 средние; 0.63–0.74 сильные; 0.75–0.92 почти новая картинка (по умолчанию 0.54)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Что изменить (теги Danbooru-style на английском)",
                    },
                    "init_image_url": {
                        "type": "string",
                        "description": "URL исходника из чата или имя файла в generated/",
                    },
                    "attachment_id": {
                        "type": "string",
                        "description": "UUID вложения-картинки в текущем сообщении",
                    },
                    "negative_prompt": {"type": "string", "default": ""},
                    "width": {
                        "type": "integer",
                        "default": 0,
                        "description": "0 = как у исходника; иначе 512–2048, кратно 8",
                    },
                    "height": {
                        "type": "integer",
                        "default": 0,
                        "description": "0 = как у исходника; иначе 512–2048, кратно 8",
                    },
                    "steps": {"type": "integer", "default": 22},
                    "cfg_scale": {"type": "number", "default": 5.0},
                    "sampler_name": {"type": "string", "default": "Euler a"},
                    "scheduler": {"type": "string", "default": ""},
                    "seed": {"type": "integer", "default": -1},
                    "denoising_strength": {
                        "type": "number",
                        "default": 0.54,
                        "minimum": 0.2,
                        "maximum": 0.92,
                    },
                    "resize_mode": {
                        "type": "integer",
                        "default": 0,
                        "description": "0 just resize, 1 crop, 2 fill, 3 latent upscale",
                    },
                    "restore_faces": {"type": "boolean", "default": False},
                    "tiling": {"type": "boolean", "default": False},
                    "description": {"type": "string", "default": ""},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upscale_images",
            "description": (
                "Увеличить разрешение изображений через SD extras. "
                "file_urls — только локальные /media/generated/… или имена файлов."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Список URL или имён файлов",
                    },
                    "resize_mode": {"type": "integer", "default": 0},
                    "upscaling_resize": {"type": "integer", "default": 2},
                    "upscaling_resize_w": {"type": "integer", "default": 512},
                    "upscaling_resize_h": {"type": "integer", "default": 512},
                    "upscaler_1": {"type": "string", "default": "R-ESRGAN 4x+"},
                    "upscaler_2": {"type": "string", "default": "None"},
                },
                "required": ["file_urls"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_gallery",
            "description": (
                "Список последних сгенерированных изображений на сервере "
                "(имена, URL, размер). Для обзора истории генераций."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
            },
        },
    },
]

# Какие tools отдавать LLM в зависимости от slug пресета беседы.
PRESET_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    "image_gen": ("generate_image", "upscale_images", "get_gallery"),
    "img2img": ("img2img", "upscale_images"),
    "document_analysis": ("extract_text",),
}

_TOOL_BY_NAME: dict[str, dict] = {
    t["function"]["name"]: t for t in TOOL_DEFINITIONS
}


def tools_for_preset_slug(slug: str | None) -> list[dict]:
    """
    Подмножество TOOL_DEFINITIONS для пресета.

    None / default — все инструменты. image_gen и img2img — раздельные наборы.
    """
    if not slug or slug not in PRESET_TOOL_NAMES:
        return list(TOOL_DEFINITIONS)
    return [_TOOL_BY_NAME[name] for name in PRESET_TOOL_NAMES[slug] if name in _TOOL_BY_NAME]
