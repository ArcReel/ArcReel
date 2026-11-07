"""Test resolution and duration passthrough in GeminiVideoBackend."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.gemini import GeminiVideoBackend


def _make_backend(model="veo-3.1-lite-generate-preview", backend_type="aistudio"):
    backend = GeminiVideoBackend.__new__(GeminiVideoBackend)
    backend._rate_limiter = None
    backend._video_model = model
    backend._backend_type = backend_type
    backend._types = MagicMock()
    backend._client = MagicMock()
    backend._client.aio.models.generate_videos = AsyncMock(side_effect=RuntimeError("stop"))
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_resolution_none_not_in_config(tmp_path):
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=8,
        resolution=None,
    )
    with pytest.raises(RuntimeError):
        await backend._create_task(req)

    cfg_call = backend._types.GenerateVideosConfig.call_args
    assert "resolution" not in cfg_call.kwargs


@pytest.mark.asyncio
async def test_resolution_string_passed_through(tmp_path):
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=8,
        resolution="1080p",
    )
    with pytest.raises(RuntimeError):
        await backend._create_task(req)

    cfg_call = backend._types.GenerateVideosConfig.call_args
    assert cfg_call.kwargs["resolution"] == "1080p"


@pytest.mark.parametrize("seconds", [3, 4, 5, 6, 7, 8, 10, 12, 15])
@pytest.mark.asyncio
async def test_duration_passthrough_str(tmp_path, seconds):
    """After removing _normalize_duration, pass duration_seconds unchanged (str) to SDK config."""
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=seconds,
    )
    with pytest.raises(RuntimeError):
        await backend._create_task(req)

    cfg_call = backend._types.GenerateVideosConfig.call_args
    assert cfg_call.kwargs["duration_seconds"] == str(seconds)


# Duration is not checked again in the backend: upstream enforces reference-image / 1080p / 4k constraints.


def _frame(tmp_path, name="frame.png"):
    p = tmp_path / name
    p.write_bytes(b"fake-png-data")
    return p


@pytest.mark.parametrize("resolution", ["1080p", "4k"])
@pytest.mark.asyncio
async def test_generate_passes_constrained_duration_through(tmp_path, resolution):
    """Pass non-8-second requests unchanged with reference images and high resolution; upstream rejects out-of-range values."""
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=4,
        resolution=resolution,
        reference_images=[_frame(tmp_path, "ref.png")],
    )
    with pytest.raises(RuntimeError):  # The SDK mock stop sentinel shows that execution reached the call.
        await backend.generate(req)

    backend._client.aio.models.generate_videos.assert_awaited_once()
    assert backend._types.GenerateVideosConfig.call_args.kwargs["duration_seconds"] == "4"
