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
                "Сгенерировать изображение по текстовому описанию через Stable Diffusion. "
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
                "Доработать существующее изображение (img2img). "
                "init_image_url — URL из чата (/media/asset/… или /media/generated/…) "
                "или имя файла. denoising_strength: 0.20–0.36 мелкие правки/апскейл-логика, "
                "0.37–0.48 косметика, 0.49–0.62 средние изменения, 0.63–0.74 сильные, "
                "0.75–0.92 почти новая картинка. По умолчанию 0.52."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Что изменить/добавить"},
                    "init_image_url": {
                        "type": "string",
                        "description": "URL или имя исходного изображения",
                    },
                    "negative_prompt": {"type": "string", "default": ""},
                    "width": {"type": "integer", "default": 1024},
                    "height": {"type": "integer", "default": 1024},
                    "steps": {"type": "integer", "default": 22},
                    "cfg_scale": {"type": "number", "default": 5.0},
                    "sampler_name": {"type": "string", "default": "Euler a"},
                    "scheduler": {"type": "string", "default": ""},
                    "seed": {"type": "integer", "default": -1},
                    "denoising_strength": {
                        "type": "number",
                        "default": 0.52,
                        "minimum": 0.2,
                        "maximum": 0.92,
                    },
                    "resize_mode": {
                        "type": "integer",
                        "default": 0,
                        "description": "0–3, режим ресайза init",
                    },
                    "restore_faces": {"type": "boolean", "default": False},
                    "tiling": {"type": "boolean", "default": False},
                    "description": {"type": "string", "default": ""},
                },
                "required": ["prompt", "init_image_url"],
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
