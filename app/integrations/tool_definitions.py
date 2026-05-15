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
]
