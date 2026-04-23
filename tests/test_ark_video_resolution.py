"""测试 ArkVideoBackend 对 resolution=None 的处理。"""

from unittest.mock import MagicMock

import pytest

from lib.video_backends.ark import ArkVideoBackend
from lib.video_backends.base import VideoGenerationRequest


def _make_backend():
    backend = ArkVideoBackend.__new__(ArkVideoBackend)
    backend._client = MagicMock()
    backend._model = "doubao-seedance-1-5-pro-251215"
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_resolution_none_not_in_create_params(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.content_generation.tasks.create = fake_create

    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=5,
        resolution=None,
    )
    with pytest.raises(RuntimeError):
        await backend._create_task(req)

    assert "resolution" not in captured
    # 其他关键字段仍然透传
    assert captured["ratio"] == "9:16"
    assert captured["duration"] == 5


@pytest.mark.asyncio
async def test_resolution_passed_when_set(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.content_generation.tasks.create = fake_create

    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16",
        duration_seconds=5,
        resolution="720p",
    )
    with pytest.raises(RuntimeError):
        await backend._create_task(req)

    assert captured["resolution"] == "720p"
