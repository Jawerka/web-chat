"""P0.4: канонические turn_phase в content_json."""

from __future__ import annotations

from app.services.turn_status import (
    CANCELLED,
    COMPLETED,
    FAILED,
    STREAMING,
    TOOL_RUNNING,
    patch_completed,
    patch_interrupted,
    status_code_to_turn_phase,
)


def test_status_code_to_turn_phase() -> None:
    assert status_code_to_turn_phase("cancelled") == CANCELLED
    assert status_code_to_turn_phase("llm_error") == FAILED
    assert status_code_to_turn_phase("completed") == COMPLETED


def test_patch_completed_clears_streaming() -> None:
    out = patch_completed({"streaming": True, "phase": "text", "images": []})
    assert out["turn_phase"] == COMPLETED
    assert out["streaming"] is None
    assert out["phase"] is None


def test_patch_interrupted_sets_turn_phase() -> None:
    out = patch_interrupted(
        {"streaming": True, "images": ["/x"]},
        status_code="cancelled",
        status_message="stop",
    )
    assert out["turn_phase"] == CANCELLED
    assert out["turn_status"] == "cancelled"
    assert out["streaming"] is None
