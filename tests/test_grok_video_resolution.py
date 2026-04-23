"""测试 GrokVideoBackend 对 resolution=None 的处理（对照 #387 回归）。"""

from unittest.mock import MagicMock

import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.grok import GrokVideoBackend


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
    # 其他字段仍正常透传
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
