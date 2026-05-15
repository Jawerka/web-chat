"""Тесты загрузки файлов и раздачи media (этап 3)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.integrations.media_utils import safe_filename


def test_safe_filename_blocks_traversal() -> None:
    """Path traversal обрезается до безопасного имени."""
    assert safe_filename("../../../etc/passwd") == "passwd"
    assert safe_filename("../../image.png") == "image.png"


@pytest.mark.asyncio
async def test_upload_png_and_pdf(client: AsyncClient) -> None:
    """POST /api/upload принимает PNG и PDF, preview только для изображения."""
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
        b"\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"

    response = await client.post(
        "/api/upload",
        files=[
            ("files", ("test.png", png_bytes, "image/png")),
            ("files", ("doc.pdf", pdf_bytes, "application/pdf")),
        ],
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["attachments"]) == 2

    by_name = {a["original_name"]: a for a in data["attachments"]}
    assert by_name["test.png"]["preview_url"] is not None
    assert by_name["doc.pdf"]["preview_url"] is None

    png_id = by_name["test.png"]["id"]
    media = await client.get(f"/media/uploads/{png_id}/test.png")
    assert media.status_code == 200
    assert media.headers["content-type"].startswith("image/")


@pytest.mark.asyncio
async def test_upload_rejects_bad_mime(client: AsyncClient) -> None:
    """Неподдерживаемый MIME → 415."""
    response = await client.post(
        "/api/upload",
        files=[("files", ("evil.exe", b"MZ", "application/x-msdownload"))],
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_upload_too_many_files(client: AsyncClient) -> None:
    """Превышение max_files_per_message → 400."""
    tiny = ("f.txt", b"x", "text/plain")
    files = [("files", tiny) for _ in range(11)]
    response = await client.post("/api/upload", files=files)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_media_blocks_traversal(client: AsyncClient) -> None:
    """Небезопасное имя в URL media → 400."""
    fake_id = uuid.uuid4()
    response = await client.get(f"/media/uploads/{fake_id}/../../../etc/passwd")
    assert response.status_code in (400, 404)
