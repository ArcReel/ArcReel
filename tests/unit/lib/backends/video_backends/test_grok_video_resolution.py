"""Test GrokVideoBackend handling of resolution=None (regression coverage for #387)."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.grok import GrokVideoBackend


def _make_backend():
    backend = GrokVideoBackend.__new__(GrokVideoBackend)
    backend._client = MagicMock()
    backend._model = "grok-imagine-video"
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_resolution_none_not_in_generate_kwargs(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.video.generate = fake_generate
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=5,
        resolution=None,
    )
    with pytest.raises(RuntimeError):
        await backend._create_video(req)

    assert "resolution" not in captured
    # Other fields still pass through normally.
    assert captured["aspect_ratio"] == "9:16"
    assert captured["duration"] == 5


@pytest.mark.asyncio
async def test_resolution_passed_when_set(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.video.generate = fake_generate
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=5,
        resolution="720p",
    )
    with pytest.raises(RuntimeError):
        await backend._create_video(req)

    assert captured["resolution"] == "720p"


@pytest.mark.asyncio
async def test_sdk_timeout_follows_the_request_poll_timeout(tmp_path):
    """Grok polls within its SDK, but must still use the global timeout from the request snapshot; otherwise that setting would uniquely fail to apply here."""
    backend = _make_backend()
    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.video.generate = fake_generate
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=5,
        poll_timeout_seconds=7200,
    )
    with pytest.raises(RuntimeError):
        await backend._create_video(req)

    assert captured["timeout"] == timedelta(seconds=7200)
