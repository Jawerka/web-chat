"""P3.7: ShutdownInProgress в ToolExecutor."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.integrations.tool_executor import ToolExecutor
from app.services.job_queue import ShutdownInProgress


@pytest.mark.asyncio
async def test_sd_tool_returns_message_on_shutdown() -> None:
    executor = ToolExecutor()

    with patch(
        "app.integrations.tool_executor.heavy_job_queue.run_sync",
        side_effect=ShutdownInProgress(),
    ):
        result = await executor.run(
            "generate_image",
            {"prompt": "test", "negative_prompt": ""},
        )

    assert "завершает работу" in result.content
    assert result.image_urls == []
