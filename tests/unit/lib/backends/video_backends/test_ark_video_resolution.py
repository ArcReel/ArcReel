"""Ark video resolution=None handling."""

from unittest.mock import MagicMock

import pytest

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.backends.video_backends.ark import Seedance2VideoBackend


def _make_backend():
    backend = Seedance2VideoBackend.__new__(Seedance2VideoBackend)
    backend._client = MagicMock()
    backend._model = "doubao-seedance-1-5-pro-251215"
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
    # Other critical fields still forward.
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

    # resolution/ratio remain orthogonal when sent together; resolution never rewrites ratios.
    assert captured["resolution"] == "720p"
    assert captured["ratio"] == "9:16"
