"""测试 OpenAIVideoBackend resolution=None 时不传 size。"""

from unittest.mock import MagicMock

import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.openai import OpenAIVideoBackend


def _make_backend():
    backend = OpenAIVideoBackend.__new__(OpenAIVideoBackend)
    backend._client = MagicMock()
    backend._model = "sora-2"
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_resolution_none_omits_size(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.videos.create_and_poll = fake_create

    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=4,
        resolution=None,
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    assert "size" not in captured


@pytest.mark.asyncio
async def test_resolution_token_maps_to_size(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.videos.create_and_poll = fake_create

    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=4,
        resolution="720p",
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    assert captured["size"] == "720x1280"


@pytest.mark.asyncio
async def test_unknown_resolution_passthrough(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.videos.create_and_poll = fake_create

    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=4,
        resolution="1080x1920",  # 非标准 token / 直接原生值
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    # 未知 resolution 透传作为 size
    assert captured["size"] == "1080x1920"
