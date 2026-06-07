"""SD WebUI interrupt и cooperative cancel в poll progress."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from app.integrations.sd_http import sd_interrupt
from app.integrations.tool_executor import ToolExecutor
from app.services.job_queue import JobCancelled


def test_sd_interrupt_posts_to_webui() -> None:
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_session.post.return_value = mock_resp

    assert sd_interrupt(mock_session, "http://sd.test:7860") is True
    mock_session.post.assert_called_once_with(
        "http://sd.test:7860/sdapi/v1/interrupt",
        timeout=10,
    )
    mock_resp.raise_for_status.assert_called_once()


def test_sd_interrupt_returns_false_on_network_error() -> None:
    mock_session = MagicMock()
    mock_session.post.side_effect = requests.ConnectionError("refused")

    assert sd_interrupt(mock_session, "http://sd.test:7860") is False


@pytest.mark.asyncio
async def test_poll_sd_progress_calls_interrupt_on_cancel() -> None:
    cancel_event = asyncio.Event()
    cancel_event.set()
    interrupted = asyncio.Event()

    async def fake_request(sd_url: str | None) -> None:
        interrupted.set()

    executor = ToolExecutor(
        cancel_event=cancel_event,
        emit_progress=AsyncMock(),
    )

    with patch.object(executor, "_request_sd_interrupt", side_effect=fake_request):
        stop = asyncio.Event()
        await executor._poll_sd_progress("img2img", stop)

    assert interrupted.is_set()


@pytest.mark.asyncio
async def test_run_img2img_streaming_returns_cancelled() -> None:
    import uuid

    executor = ToolExecutor(
        cancel_event=asyncio.Event(),
        conversation_id=uuid.uuid4(),
        emit_image=AsyncMock(),
        emit_progress=AsyncMock(),
    )

    async def fake_run_sync(*args: object, **kwargs: object) -> str:
        raise JobCancelled()

    with patch(
        "app.integrations.tool_executor.heavy_job_queue.run_sync",
        side_effect=fake_run_sync,
    ):
        result = await executor._run_img2img_streaming(
            {
                "prompt": "test",
                "init_image_bytes": MINIMAL_PNG,
                "init_source_name": "ref.png",
            },
        )

    assert result.content == "Генерация отменена"
    assert result.image_urls == []


MINIMAL_PNG = __import__("base64").b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
