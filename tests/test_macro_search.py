"""Ф2: поиск @alias (keyword fallback, semantic при наличии векторов)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.db.models import PromptMacro, PromptMacroCategory
from app.integrations.embedding_client import cosine_similarity
from app.services.macro_search_service import _keyword_score, search_macros
from app.services.prompt_macro_service import parse_macro_context_mode


def test_parse_macro_context_semantic() -> None:
    assert parse_macro_context_mode("semantic") == "semantic"
    assert parse_macro_context_mode("SEMANTIC") == "semantic"


def test_cosine_similarity() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_keyword_score_alias_match() -> None:
    macro = PromptMacro(
        category=PromptMacroCategory.CHARACTER,
        alias="rainbow_dash",
        label="RD",
        body="pegasus wings",
        sort_order=0,
    )
    assert _keyword_score("draw rainbow_dash", macro) >= 3.0


@pytest.mark.asyncio
async def test_search_macros_keyword(client: AsyncClient) -> None:
    await client.post(
        "/api/prompt-macros",
        json={
            "category": "character",
            "alias": "search_pony_a",
            "body": "blue pegasus with rainbow mane",
            "sort_order": 0,
        },
    )
    await client.post(
        "/api/prompt-macros",
        json={
            "category": "environment",
            "alias": "forest_scene",
            "body": "dense forest sunlight",
            "sort_order": 1,
        },
    )
    r = await client.get("/api/prompt-macros/search", params={"q": "rainbow pegasus"})
    assert r.status_code == 200
    hits = r.json()
    assert len(hits) >= 1
    assert hits[0]["alias"] == "search_pony_a"
    assert hits[0]["match"] in ("keyword", "semantic")


@pytest.mark.asyncio
async def test_search_api_requires_query(client: AsyncClient) -> None:
    r = await client.get("/api/prompt-macros/search")
    assert r.status_code == 422
