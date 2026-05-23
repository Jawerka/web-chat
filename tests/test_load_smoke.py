"""P1.7: лёгкая параллельная нагрузка без deadlock."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from tests.helpers import api_create_conversation


@pytest.mark.asyncio
async def test_parallel_conversation_creates(
    client: AsyncClient,
    test_conv_title: str,
) -> None:
    """8 параллельных POST /api/conversations — все 201."""

    async def create_one(index: int) -> str:
        title = f"{test_conv_title} load-{index}"
        data = await api_create_conversation(client, title)
        return data["id"]

    ids = await asyncio.gather(*[create_one(i) for i in range(8)])
    assert len(ids) == 8
    assert len(set(ids)) == 8
