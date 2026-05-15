"""Тесты извлечения текста из документов (этап 6)."""

from __future__ import annotations

from pathlib import Path

from app.integrations.document_extractor import extract_text_from_file, truncate_text


def test_truncate_text_adds_suffix() -> None:
    """Обрезка добавляет суффикс с полным размером."""
    long_text = "а" * 100
    result = truncate_text(long_text, 50)
    assert len(result) > 50
    assert "обрезано" in result
    assert "100 символов" in result


def test_truncate_text_unchanged_when_short() -> None:
    assert truncate_text("короткий", 100) == "короткий"


def test_extract_txt(tmp_path: Path) -> None:
    """TXT извлекается полностью."""
    path = tmp_path / "note.txt"
    path.write_text("Привет, документ!", encoding="utf-8")
    text = extract_text_from_file(path, "text/plain")
    assert "Привет" in text


def test_extract_pdf(tmp_path: Path) -> None:
    """PDF через PyMuPDF."""
    import fitz

    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Текст в PDF для теста")
    doc.save(path)
    doc.close()

    text = extract_text_from_file(path, "application/pdf")
    assert "PDF" in text or "теста" in text
