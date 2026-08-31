"""测试 GeminiVideoBackend 对 resolution 的处理与 duration 联动约束。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.config.registry import ModelInfo
from lib.video_backends.base import VideoCapabilityError, VideoGenerationRequest
from lib.video_backends.gemini import GeminiVideoBackend


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


# 统一走 generate()：校验在重试装饰器之外，只有从这个入口进才覆盖真实调用顺序。


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
        await backend.generate(req)

    assert exc.value.code == "video_reference_images_duration_unsupported"
    assert exc.value.params["duration"] == seconds
    assert exc.value.params["supported"] == "8s"
    backend._client.aio.models.generate_videos.assert_not_awaited()


@pytest.mark.parametrize("seconds", [4, 6])
@pytest.mark.parametrize("resolution", ["1080p", "1080P", "4k", "4K"])
@pytest.mark.asyncio
async def test_high_resolution_rejects_non_8s(tmp_path, resolution, seconds):
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=seconds,
        resolution=resolution,
    )
    with pytest.raises(VideoCapabilityError) as exc:
        await backend.generate(req)

    assert exc.value.code == "video_resolution_duration_unsupported"
    assert exc.value.params["resolution"] == resolution.upper()
    assert exc.value.params["duration"] == seconds
    backend._client.aio.models.generate_videos.assert_not_awaited()


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
        await backend.generate(req)

    backend._client.aio.models.generate_videos.assert_awaited_once()
    assert backend._types.GenerateVideosConfig.call_args.kwargs["duration_seconds"] == str(seconds)


@pytest.mark.parametrize(
    ("model", "backend_type"),
    [
        ("veo-3.1-generate-preview", "aistudio"),
        ("veo-3.1-generate-001", "vertex"),
        # registry 未登记的型号（中转站 / 自定义供应商包装）：约束读不到声明，落兜底常量。
        ("veo-3.1-via-relay", "aistudio"),
    ],
)
@pytest.mark.parametrize("resolution", ["1080p", "4k"])
@pytest.mark.asyncio
async def test_constraints_follow_registry_declaration(tmp_path, model, backend_type, resolution):
    """已登记型号按 registry 声明拒绝，未登记型号回落兜底常量——两条路径都不放行 4 秒。"""
    backend = _make_backend(model=model, backend_type=backend_type)
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=4,
        resolution=resolution,
    )
    with pytest.raises(VideoCapabilityError) as exc:
        await backend.generate(req)

    assert exc.value.code == "video_resolution_duration_unsupported"
    assert exc.value.params["supported"] == "8s"
    backend._client.aio.models.generate_videos.assert_not_awaited()


@pytest.mark.asyncio
async def test_reference_images_constraint_for_unregistered_model(tmp_path):
    """未登记型号的参考图路径同样落兜底，不因查不到声明而放行。"""
    backend = _make_backend(model="veo-3.1-via-relay")
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=6,
        reference_images=[_frame(tmp_path, "ref.png")],
    )
    with pytest.raises(VideoCapabilityError) as exc:
        await backend.generate(req)

    assert exc.value.code == "video_reference_images_duration_unsupported"
    assert exc.value.params["supported"] == "8s"


@pytest.mark.asyncio
async def test_reference_images_empty_declaration_means_unconstrained(tmp_path, monkeypatch):
    """已登记但把 reference_image_durations 声明为空 = 该路径不额外约束，不落兜底。

    空声明与「未登记」是两种语义（前端读同一份声明也按不约束处理），兜底只对未登记生效。
    """
    info = ModelInfo(
        display_name="Veo test",
        media_type="video",
        capabilities=["text_to_video"],
        supported_durations=[4, 6, 8],
        reference_image_durations=[],
    )
    monkeypatch.setattr("lib.video_backends.gemini.model_info_for", lambda *_: info)

    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=4,
        reference_images=[_frame(tmp_path, "ref.png")],
    )
    with pytest.raises(RuntimeError):  # 走到 SDK 调用即说明未被约束拦截
        await backend.generate(req)


@pytest.mark.parametrize("resolution", ["1080p", "4k"])
@pytest.mark.asyncio
async def test_8s_passes_through(tmp_path, resolution):
    backend = _make_backend()
    req = VideoGenerationRequest(
        prompt="x",
        output_path=tmp_path / "o.mp4",
        duration_seconds=8,
        resolution=resolution,
        reference_images=[_frame(tmp_path, "ref.png")],
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    backend._client.aio.models.generate_videos.assert_awaited_once()
