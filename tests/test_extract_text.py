"""Интеграционные тесты extract_text и prepare_for_llm (этап 6)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile

from app.db import session as db_session
from app.db.session import dispose_database, init_db
from tests.safety import assert_not_using_production_database, safe_configure_database
from app.integrations.tool_executor import ToolExecutor
from app.services.attachment_service import AttachmentService


@pytest.mark.asyncio
async def test_pdf_extract_and_prepare_for_llm(tmp_path: Path) -> None:
    """Загрузка PDF → extract_text → кэш в БД, длина <= max_chars."""
    import fitz

    await dispose_database()
    safe_configure_database(f"sqlite+aiosqlite:///{tmp_path}/extract.sqlite")
    await init_db()
    assert_not_using_production_database()

    buf = io.BytesIO()
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Содержимое тестового PDF документа.")
    doc.save(buf)
    doc.close()
    pdf_bytes = buf.getvalue()

    upload_file = UploadFile(
        filename="report.pdf",
        file=io.BytesIO(pdf_bytes),
        headers={"content-type": "application/pdf"},
    )

    async with db_session.async_session_factory() as session:
        service = AttachmentService(session)
        attachment = await service.register_upload(upload_file)
        await session.commit()

        executor = ToolExecutor(session=session)
        result = await executor.run(
            "extract_text",
            {"attachment_id": str(attachment.id), "max_chars": 500},
        )
        await session.commit()

    assert "Ошибка" not in result.content
    assert len(result.content) > 0
    assert len(result.content) <= 500 + 80

    async with db_session.async_session_factory() as session:
        service = AttachmentService(session)
        prepared = await service.prepare_for_llm([attachment.id])

    assert len(prepared) == 1
    assert prepared[0].extracted_text is not None
    assert "документа" in prepared[0].extracted_text or "PDF" in prepared[0].extracted_text
