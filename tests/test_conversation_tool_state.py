"""Тесты anti-loop tools (P1.4)."""

from __future__ import annotations

import asyncio

import pytest

from app.services.agent_orchestrator import ToolLoopExceeded, TurnCancelled
from app.services.conversation_tool_state import ConversationToolState, tool_call_signature


def test_tool_call_signature_stable() -> None:
    a = tool_call_signature("img2img", {"prompt": "cat", "denoise": 0.5})
    b = tool_call_signature("img2img", {"denoise": 0.5, "prompt": "cat"})
    assert a == b


def test_img2img_asset_url_matches_attachment_id() -> None:
    """UUID в /media/asset/… и attachment_id — один и тот же init для anti-loop."""
    uid = "fbe2b769-1687-41b1-adab-35a8ecb073a1"
    a = tool_call_signature(
        "img2img",
        {
            "prompt": "x",
            "init_image_url": f"/media/asset/{uid}.png",
        },
    )
    b = tool_call_signature(
        "img2img",
        {
            "prompt": "x",
            "attachment_id": uid,
        },
    )
    assert a == b


def test_duplicate_args_raises() -> None:
    state = ConversationToolState(max_same_tool_per_turn=5)
    args = {"prompt": "same"}
    state.before_tool("img2img", args, cancel_event=asyncio.Event())
    with pytest.raises(ToolLoopExceeded, match="Повторный вызов"):
        state.before_tool("img2img", args, cancel_event=asyncio.Event())


def test_max_same_sd_tool_raises() -> None:
    state = ConversationToolState(max_same_tool_per_turn=3)
    ev = asyncio.Event()
    state.before_tool("img2img", {"n": 1}, cancel_event=ev)
    state.before_tool("img2img", {"n": 2}, cancel_event=ev)
    state.before_tool("img2img", {"n": 3}, cancel_event=ev)
    with pytest.raises(ToolLoopExceeded, match="Слишком много вызовов img2img"):
        state.before_tool("img2img", {"n": 4}, cancel_event=ev)


def test_cancel_before_tool() -> None:
    state = ConversationToolState()
    ev = asyncio.Event()
    ev.set()
    with pytest.raises(TurnCancelled):
        state.before_tool("generate_image", {"prompt": "x"}, cancel_event=ev)
