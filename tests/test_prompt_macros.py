"""Тесты быстрых промптов (@alias)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.db.models import PromptMacroCategory
from app.services.prompt_macro_service import expand_macro_text, validate_alias


def test_validate_alias() -> None:
    assert validate_alias("@Rainbow-Dash") == "rainbow-dash"
    assert validate_alias("rainbow_dash") == "rainbow_dash"
    with pytest.raises(ValueError):
        validate_alias("bad alias!")


def test_expand_macro_text() -> None:
    body = "rainbow_dash, blue_fur, pegasus, wings"
    text = "Draw @rainbow_dash in the forest"
    out = expand_macro_text(text, {"rainbow_dash": body})
    assert body in out
    assert "@rainbow_dash" not in out


def test_expand_unknown_alias_unchanged() -> None:
    text = "Hello @unknown_tag here"
    out = expand_macro_text(text, {"rainbow_dash": "x"})
    assert out == text


def test_expand_double_at_alias() -> None:
    """@@alias — развёртка без лишнего @ перед телом макроса."""
    body = "rainbow_dash, pegasus, wings"
    text = "обнимает @@rainbow_dash"
    out = expand_macro_text(text, {"rainbow_dash": body})
    assert out == f"обнимает {body}"
    assert "@@" not in out


@pytest.mark.asyncio
async def test_prompt_macros_crud(client: AsyncClient) -> None:
    created = await client.post(
        "/api/prompt-macros",
        json={
            "category": "character",
            "alias": "test_pony",
            "label": "Test Pony",
            "body": "test pony, colorful",
        },
    )
    assert created.status_code == 201
    data = created.json()
    assert data["alias"] == "test_pony"

    listed = await client.get("/api/prompt-macros")
    assert listed.status_code == 200
    assert any(m["alias"] == "test_pony" for m in listed.json())

    macro_id = data["id"]
    patched = await client.patch(
        f"/api/prompt-macros/{macro_id}",
        json={"body": "updated body"},
    )
    assert patched.status_code == 200
    assert patched.json()["body"] == "updated body"

    deleted = await client.delete(f"/api/prompt-macros/{macro_id}")
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_duplicate_alias(client: AsyncClient) -> None:
    payload = {
        "category": PromptMacroCategory.OTHER.value,
        "alias": "dup_alias",
        "body": "one",
    }
    assert (await client.post("/api/prompt-macros", json=payload)).status_code == 201
    assert (await client.post("/api/prompt-macros", json=payload)).status_code == 409
