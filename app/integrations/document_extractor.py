"""
Извлечение текста из документов и изображений (OCR опционально).

PDF — PyMuPDF; DOCX — python-docx; TXT/CSV — utf-8 с fallback.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

_TESSERACT_AVAILABLE: bool | None = None


def truncate_text(text: str, max_chars: int) -> str:
    """
    Обрезать текст с суффиксом о полном размере.

    Args:
        text: Исходный текст.
        max_chars: Максимум символов в результате.

    Returns:
        Обрезанный или исходный текст.
    """
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n… (обрезано, всего {len(text)} символов)"


def _read_text_file(path: Path) -> str:
    """Прочитать TXT/CSV с utf-8 и fallback кодировками."""
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_pdf(path: Path) -> str:
    import fitz

    max_pages = settings.max_pdf_pages
    parts: list[str] = []
    with fitz.open(path) as doc:
        if doc.page_count > max_pages:
            raise ValueError(
                f"PDF содержит {doc.page_count} страниц, лимит {max_pages}",
            )
        for page in doc:
            parts.append(page.get_text())
    return "\n".join(parts).strip()


def _extract_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()


def _extract_image_ocr(path: Path) -> str:
    """OCR через pytesseract, если установлен tesseract в системе."""
    global _TESSERACT_AVAILABLE
    if _TESSERACT_AVAILABLE is False:
        return ""
    try:
        import pytesseract
        from PIL import Image

        _TESSERACT_AVAILABLE = True
        with Image.open(path) as img:
            return pytesseract.image_to_string(img, lang="rus+eng").strip()
    except ImportError:
        _TESSERACT_AVAILABLE = False
        logger.debug("pytesseract не установлен — OCR пропущен")
        return ""
    except OSError as exc:
        _TESSERACT_AVAILABLE = False
        logger.warning("OCR недоступен: %s", exc)
        return ""


def extract_text_from_file(path: Path, mime_type: str) -> str:
    """
    Извлечь текст из файла по MIME-типу (синхронно, для asyncio.to_thread).

    Raises:
        ValueError: Неподдерживаемый тип или пустой результат.
    """
    if not path.is_file():
        raise ValueError(f"Файл не найден: {path}")

    mime = mime_type.lower()
    text = ""

    if mime == "application/pdf":
        text = _extract_pdf(path)
    elif mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        text = _extract_docx(path)
    elif mime in ("text/plain", "text/csv"):
        text = _read_text_file(path)
    elif mime.startswith("image/"):
        text = _extract_image_ocr(path)
        if not text:
            raise ValueError(
                "Для изображения текст не извлечён (OCR недоступен или пустой результат)"
            )
    else:
        raise ValueError(f"Извлечение текста не поддерживается для MIME: {mime_type}")

    text = text.strip()
    if not text:
        raise ValueError("Документ не содержит извлекаемого текста")
    return text
