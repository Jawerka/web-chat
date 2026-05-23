"""P1.5: magic bytes, Pillow decode, лимиты документов."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.integrations.upload_validation import (
    UploadBytesValidationError,
    validate_document_bytes,
    validate_image_bytes,
)

from tests.helpers import minimal_valid_png_bytes


def test_validate_image_rejects_mime_mismatch() -> None:
    with pytest.raises(UploadBytesValidationError, match="не распознан|не совпадает"):
        validate_image_bytes(b"MZ\x90\x00", "image/png")


def test_validate_image_accepts_minimal_png() -> None:
    validate_image_bytes(minimal_valid_png_bytes(), "image/png")


def test_validate_document_pdf_magic() -> None:
    validate_document_bytes(b"%PDF-1.4\n", "application/pdf")
    with pytest.raises(UploadBytesValidationError):
        validate_document_bytes(b"not a pdf", "application/pdf")


@pytest.mark.asyncio
async def test_upload_rejects_png_mime_with_exe_body(client: AsyncClient) -> None:
    """Заявленный image/png при неверной сигнатуре → 415."""
    response = await client.post(
        "/api/upload",
        files=[("files", ("fake.png", b"MZ\x90\x00", "image/png"))],
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_upload_rejects_pdf_mime_without_header(client: AsyncClient) -> None:
    response = await client.post(
        "/api/upload",
        files=[("files", ("fake.pdf", b"hello", "application/pdf"))],
    )
    assert response.status_code == 415
