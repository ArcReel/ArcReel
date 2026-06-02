"""测试 OpenAIVideoBackend 的 size 解析：吸附 sora-2 固定 4 档枚举，比例优先。"""

from unittest.mock import MagicMock

import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.openai import _SORA_LEGAL_SIZES, OpenAIVideoBackend


def _make_backend():
    backend = OpenAIVideoBackend.__new__(OpenAIVideoBackend)
    backend._client = MagicMock()
    backend._model = "sora-2"
    backend._capabilities = set()
    return backend


async def _capture_size(backend, **req_kwargs) -> str:
    captured: dict[str, object] = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.videos.create = fake_create
    req = VideoGenerationRequest(prompt="x", duration_seconds=4, **req_kwargs)
    with pytest.raises(RuntimeError):
        await backend.generate(req)
    size = captured.get("size")
    assert isinstance(size, str)  # size 必传
    return size


@pytest.mark.asyncio
@pytest.mark.parametrize("aspect,expected", [("9:16", "720x1280"), ("16:9", "1280x720")])
async def test_standard_aspect_maps_to_exact_legal_size(tmp_path, aspect, expected):
    backend = _make_backend()
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio=aspect, resolution=None)
    assert size == expected
    assert size in _SORA_LEGAL_SIZES


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", [None, "720p", "1080p", "4K"])
async def test_resolution_does_not_break_ratio(tmp_path, resolution):
    """sora 精确 9:16 仅 720 档：任何 resolution 都不能破坏比例，始终 720x1280（清晰度让位比例）。"""
    backend = _make_backend()
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio="9:16", resolution=resolution)
    assert size == "720x1280"


@pytest.mark.asyncio
async def test_custom_resolution_value_ignored_uses_legal_size(tmp_path):
    """自定义 resolution 值（如 1080x1920，非 sora 合法档）不再被透传成非法 size，按比例吸附合法档。"""
    backend = _make_backend()
    size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio="9:16", resolution="1080x1920")
    assert size == "720x1280"
    assert size in _SORA_LEGAL_SIZES


@pytest.mark.asyncio
async def test_size_always_set_and_legal(tmp_path):
    """size 字段必传且必为合法枚举——杜绝「不传 size 让上游决定比例」。"""
    backend = _make_backend()
    for aspect in ("9:16", "16:9", "1:1", "4:3", "21:9"):
        size = await _capture_size(backend, output_path=tmp_path / "o.mp4", aspect_ratio=aspect, resolution=None)
        assert size in _SORA_LEGAL_SIZES, aspect
