"""P2.3: RAG по документам беседы."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.fixture
def rag_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(settings, "rag_auto_inject", False)
    monkeypatch.setattr(settings, "embedding_model", "test-embed")
    monkeypatch.setattr(settings, "rag_chunk_chars", 100)
    monkeypatch.setattr(settings, "rag_chunk_overlap", 20)


def test_split_text_into_chunks() -> None:
    from app.services.document_rag_service import split_text_into_chunks

    text = "a" * 500
    chunks = split_text_into_chunks(text, chunk_size=200, overlap=30)
    assert len(chunks) >= 2
    assert all(len(c) <= 200 for c in chunks)


@pytest.mark.asyncio
async def test_index_and_search_documents(
    client: AsyncClient,
    rag_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import session as db_session
    from app.db.repositories import AttachmentRepository
    from app.services.document_rag_service import index_attachment_rag, search_conversation_documents
    from tests.helpers import api_create_conversation

    conv = await api_create_conversation(client, "[pytest] document rag")
    conv_id = uuid.UUID(str(conv["id"]))
    att_id = uuid.uuid4()

    async with db_session.async_session_factory() as session:
        repo = AttachmentRepository(session)
        await repo.create(
            attachment_id=att_id,
            original_name="notes.txt",
            mime_type="text/plain",
            size_bytes=100,
            storage_path="uploads/test/notes.txt",
            conversation_id=conv_id,
        )
        att = await repo.get_by_id(att_id)
        assert att is not None
        await repo.update_extracted_text(
            att,
            "Alpha document about neural networks. " * 5
            + "Beta section on databases and SQL queries.",
        )
        await session.commit()

    vec_a = [1.0, 0.0, 0.0]
    vec_b = [0.0, 1.0, 0.0]
    vec_q = [0.9, 0.1, 0.0]
    call_count = 0

    async def fake_embed(
        self,
        text: str,
        *,
        model: str | None = None,
    ) -> list[float] | None:
        nonlocal call_count
        call_count += 1
        if "neural" in text.lower() or "network" in text.lower():
            return vec_a
        if "database" in text.lower() or "sql" in text.lower():
            return vec_b
        return vec_q

    from app.integrations.embedding_client import EmbeddingClient

    monkeypatch.setattr(EmbeddingClient, "embed_text", fake_embed)

    async with db_session.async_session_factory() as session:
        stats = await index_attachment_rag(session, att_id)
        await session.commit()
        assert stats["chunks"] >= 2

        hits = await search_conversation_documents(
            session,
            conv_id,
            "neural networks",
            limit=3,
        )
        assert hits
        assert hits[0]["file_name"] == "notes.txt"

    search_res = await client.get(
        f"/api/conversations/{conv_id}/document-search",
        params={"q": "neural"},
    )
    assert search_res.status_code == 200
    body = search_res.json()
    assert body
    assert body[0]["file_name"] == "notes.txt"

    index_res = await client.post(f"/api/attachments/{att_id}/index-rag")
    assert index_res.status_code == 200
    assert index_res.json()["chunks"] >= 1
