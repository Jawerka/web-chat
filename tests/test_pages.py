"""Тесты HTML-страниц."""

from httpx import AsyncClient


async def test_chat_page(client: AsyncClient) -> None:
    """GET / отдаёт главную страницу чата."""
    response = await client.get("/")
    assert response.status_code == 200
    assert "web-chat" in response.text
    assert "/static/js/chat.js" in response.text


async def test_public_config(client: AsyncClient) -> None:
    """GET /api/config — публичные лимиты."""
    response = await client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "max_upload_mb" in data
    assert "public_base_url" in data
