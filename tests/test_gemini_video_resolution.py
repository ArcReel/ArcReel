"""测试 GeminiVideoBackend 对 resolution 的处理与 duration 联动约束。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.retry import _should_retry
from lib.video_backends.base import VideoCapabilityError, VideoGenerationRequest
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


@pytest.mark.parametrize("seconds", [3, 4, 5, 6, 7, 8, 10, 12, 15])
@pytest.mark.asyncio
async def test_duration_passthrough_str(tmp_path, seconds):
    """删除 _normalize_duration 后，duration_seconds 应原值（str）透传到 SDK config。"""
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


# ── duration 联动约束：参考图 / 1080p·4k 强制 8 秒 ──────────────────


def _frame(tmp_path, name="frame.png"):
    p = tmp_path / name
    p.write_bytes(b"fake-png-data")
    return p


@pytest.mark.parametrize("seconds", [4, 6])
@pytest.mark.asyncio
async def test_reference_images_reject_non_8s(tmp_path, seconds):
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=seconds,
        reference_images=[_frame(tmp_path, "ref.png")],
    )
    with pytest.raises(VideoCapabilityError) as exc:
        await backend._create_task(req)

    assert exc.value.code == "video_reference_images_duration_unsupported"
    assert exc.value.params["duration"] == seconds
    assert exc.value.params["supported"] == "8s"
    backend._client.aio.models.generate_videos.assert_not_awaited()


@pytest.mark.parametrize("resolution", ["1080p", "1080P", "4k", "4K"])
@pytest.mark.asyncio
async def test_high_resolution_rejects_non_8s(tmp_path, resolution):
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=6,
        resolution=resolution,
    )
    with pytest.raises(VideoCapabilityError) as exc:
        await backend._create_task(req)

    assert exc.value.code == "video_resolution_duration_unsupported"
    assert exc.value.params["resolution"] == resolution.upper()
    backend._client.aio.models.generate_videos.assert_not_awaited()


@pytest.mark.asyncio
async def test_capability_error_is_not_retried():
    """约束违规必然复现，重试只会重复失败——确认它不落入瞬态重试。"""
    assert not _should_retry(VideoCapabilityError("video_resolution_duration_unsupported"), ())
    assert not _should_retry(VideoCapabilityError("video_reference_images_duration_unsupported"), ())


@pytest.mark.parametrize("seconds", [4, 6])
@pytest.mark.asyncio
async def test_start_and_last_frame_keep_short_durations(tmp_path, seconds):
    """image / lastFrame 路径不受约束，720p 下 4/6 秒照常下发。"""
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=seconds,
        resolution="720p",
        start_image=_frame(tmp_path, "start.png"),
        end_image=_frame(tmp_path, "end.png"),
    )
    with pytest.raises(RuntimeError):  # SDK mock 的 stop 哨兵，说明已走到调用点
        await backend._create_task(req)

    backend._client.aio.models.generate_videos.assert_awaited_once()
    assert backend._types.GenerateVideosConfig.call_args.kwargs["duration_seconds"] == str(seconds)


@pytest.mark.parametrize(
    "extra",
    [
        {"resolution": "1080p"},
        {"resolution": "4k"},
    ],
)
@pytest.mark.asyncio
async def test_8s_passes_through(tmp_path, extra):
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=8,
        reference_images=[_frame(tmp_path, "ref.png")],
        **extra,
    )
    with pytest.raises(RuntimeError):
        await backend._create_task(req)

    backend._client.aio.models.generate_videos.assert_awaited_once()
