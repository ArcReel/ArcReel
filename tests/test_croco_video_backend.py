"""Croco H3 视频请求合同测试。"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lib.config.registry import PROVIDER_REGISTRY
from lib.video_backends.base import VideoCapabilityError, VideoGenerationRequest
from lib.video_backends.croco import CrocoVideoBackend, _resolve_quality

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("aspect_ratio", "resolution", "expected"),
    [
        ("16:9", "480p", "preview"),
        ("16:9", "0.7M", "base_0_7mp"),
        ("16:9", "720p", "base_768p"),
        ("9:16", "480p", "portrait_preview"),
        ("9:16", "0.7M", "portrait_0_7mp"),
        ("9:16", "720p", "portrait_768p"),
        ("4:3", "480p", "standard_480p"),
        ("4:3", "0.7M", "standard_0_7mp"),
        ("4:3", "720p", "standard_768p"),
        ("3:4", "480p", "standard_portrait_480p"),
        ("3:4", "0.7M", "standard_portrait_0_7mp"),
        ("3:4", "720p", "standard_portrait_768p"),
    ],
)
def test_quality_combines_resolution_and_aspect_ratio(aspect_ratio: str, resolution: str, expected: str):
    assert _resolve_quality(resolution, aspect_ratio) == expected


def test_auto_resolution_uses_middle_tier_without_losing_portrait_orientation():
    assert _resolve_quality(None, "9:16") == "portrait_0_7mp"


@pytest.mark.parametrize(
    ("resolution", "aspect_ratio"),
    [("1080p", "16:9"), ("720p", "1:1")],
)
def test_unsupported_output_profile_fails_loud(resolution: str, aspect_ratio: str):
    with pytest.raises(VideoCapabilityError) as exc_info:
        _resolve_quality(resolution, aspect_ratio)

    assert exc_info.value.code == "video_output_profile_unsupported"
    assert exc_info.value.params["resolution"] == resolution
    assert exc_info.value.params["aspect_ratio"] == aspect_ratio


def test_registry_exposes_croco_h3_user_facing_resolution_tiers():
    model = PROVIDER_REGISTRY["croco"].models["minimax-h3"]
    assert model.resolutions == ["480p", "0.7M", "720p"]


async def test_generate_sends_resolved_quality_to_unified_job(tmp_path: Path):
    backend = CrocoVideoBackend(api_key="test-token")
    backend._client.submit_job = AsyncMock(return_value={"job_id": "job-1"})
    backend._client.wait_until_terminal = AsyncMock(return_value={"status": "succeeded"})
    backend._client.list_outputs = AsyncMock(
        return_value={
            "items": [
                {
                    "output_id": "video",
                    "delivery_state": "ready",
                    "content_url": "https://example.test/video.mp4",
                }
            ]
        }
    )
    backend._client.download_output = AsyncMock()

    await backend.generate(
        VideoGenerationRequest(
            prompt="A subject turns toward camera",
            output_path=tmp_path / "result.mp4",
            aspect_ratio="9:16",
            resolution="720p",
            duration_seconds=6,
        )
    )

    call = backend._client.submit_job.await_args.kwargs
    assert call["model_id"] == "minimax-h3"
    assert call["operation"] == "video.generate"
    assert call["contract_version"] == "1"
    assert call["parameters"] == {
        "mode": "t2v",
        "prompt": "A subject turns toward camera",
        "quality": "portrait_768p",
        "duration_seconds": 6,
    }
