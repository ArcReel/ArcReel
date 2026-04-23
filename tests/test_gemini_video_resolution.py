"""测试 GeminiVideoBackend 对 resolution=None 的处理。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.gemini import GeminiVideoBackend


def _make_backend():
    backend = GeminiVideoBackend.__new__(GeminiVideoBackend)
    backend._rate_limiter = None
    backend._video_model = "veo-3.1-lite-generate-preview"
    backend._backend_type = "aistudio"
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
