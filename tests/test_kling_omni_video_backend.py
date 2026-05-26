"""KlingOmniVideoBackend 单元测试。"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.providers import PROVIDER_KLING
from lib.video_backends.base import VideoCapability, VideoGenerationRequest
from lib.video_backends.kling_omni_types import (
    KlingOmniElementInput,
    KlingOmniImageInput,
    KlingOmniRequestOptions,
    KlingOmniShot,
    KlingOmniShotType,
    KlingOmniSoundMode,
    KlingOmniVideoInput,
    KlingOmniVideoReferType,
)


def _make_response(status_code: int, json_body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _fake_download_factory(payload: bytes = b"mp4-bytes"):
    async def _fake(url: str, output_path: Path, *, timeout: int = 120) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)

    return _fake


class TestKlingOmniVideoBackend:
    def test_name_model_and_capabilities(self):
        from lib.video_backends.kling_omni import KlingOmniVideoBackend

        backend = KlingOmniVideoBackend(api_key="sk-test")
        assert backend.name == PROVIDER_KLING
        assert backend.model == "kling-video-o1"
        assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
        assert VideoCapability.GENERATE_AUDIO in backend.capabilities
        assert backend.video_capabilities.reference_images is True
        assert backend.video_capabilities.max_reference_images == 7

    def test_build_payload_from_generic_reference_images(self, tmp_path: Path):
        image = tmp_path / "hero.png"
        image.write_bytes(b"\x89PNG\r\nhero")

        from lib.video_backends.kling_omni import KlingOmniVideoBackend

        backend = KlingOmniVideoBackend(api_key="sk-test")
        payload = backend._build_payload(
            VideoGenerationRequest(
                prompt="让<<<image_1>>>向前走",
                output_path=tmp_path / "out.mp4",
                reference_images=[image],
                aspect_ratio="16:9",
                duration_seconds=5,
                generate_audio=True,
            )
        )

        assert payload["model_name"] == "kling-video-o1"
        assert payload["prompt"] == "让<<<image_1>>>向前走"
        assert payload["duration"] == "5"
        assert payload["aspect_ratio"] == "16:9"
        assert payload["sound"] == "on"
        assert payload["image_list"] == [{"image_url": base64.b64encode(image.read_bytes()).decode("ascii")}]
        assert payload["watermark_info"] == {"enabled": False}

    def test_build_payload_rewrites_legacy_image_tokens(self, tmp_path: Path):
        image = tmp_path / "hero.png"
        image.write_bytes(b"hero")

        from lib.video_backends.kling_omni import KlingOmniVideoBackend

        backend = KlingOmniVideoBackend(api_key="sk-test")
        payload = backend._build_payload(
            VideoGenerationRequest(
                prompt="[图1] 在 [图2] 里向前走",
                output_path=tmp_path / "out.mp4",
                reference_images=[image, image],
            )
        )

        assert payload["prompt"] == "<<<image_1>>> 在 <<<image_2>>> 里向前走"

    def test_build_payload_with_omni_multishot_and_elements(self, tmp_path: Path):
        style = tmp_path / "style.png"
        style.write_bytes(b"style")

        from lib.video_backends.kling_omni import KlingOmniVideoBackend

        backend = KlingOmniVideoBackend(api_key="sk-test", model="kling-v3-omni")
        payload = backend._build_payload(
            VideoGenerationRequest(
                prompt="这个字段在 customize 模式下应被忽略",
                output_path=tmp_path / "out.mp4",
                aspect_ratio="16:9",
                duration_seconds=5,
                kling_omni=KlingOmniRequestOptions(
                    images=(
                        KlingOmniImageInput(image_path=style),
                        KlingOmniImageInput(image_url="https://example.com/scene.png"),
                    ),
                    elements=(KlingOmniElementInput(element_id=11), KlingOmniElementInput(element_id=22)),
                    multi_shot=True,
                    shot_type=KlingOmniShotType.CUSTOMIZE,
                    shots=(
                        KlingOmniShot(index=1, prompt="<<<image_1>>>走进画面", duration_seconds=2),
                        KlingOmniShot(index=2, prompt="<<<element_1>>>与<<<element_2>>>对视", duration_seconds=3),
                    ),
                    sound=KlingOmniSoundMode.ON,
                ),
            )
        )

        assert payload["model_name"] == "kling-v3-omni"
        assert payload["multi_shot"] is True
        assert payload["shot_type"] == "customize"
        assert payload["prompt"] == ""
        assert payload["multi_prompt"] == [
            {"index": 1, "prompt": "<<<image_1>>>走进画面", "duration": "2"},
            {"index": 2, "prompt": "<<<element_1>>>与<<<element_2>>>对视", "duration": "3"},
        ]
        assert payload["element_list"] == [{"element_id": 11}, {"element_id": 22}]
        assert payload["aspect_ratio"] == "16:9"
        assert payload["duration"] == "5"
        assert payload["sound"] == "on"
        assert payload["image_list"][1] == {"image_url": "https://example.com/scene.png"}

    def test_video_edit_omits_aspect_ratio_and_duration_and_forces_sound_off(self, tmp_path: Path):
        from lib.video_backends.kling_omni import KlingOmniVideoBackend

        backend = KlingOmniVideoBackend(api_key="sk-test")
        payload = backend._build_payload(
            VideoGenerationRequest(
                prompt="给<<<video_1>>>中的女孩戴上<<<image_1>>>里的王冠",
                output_path=tmp_path / "out.mp4",
                aspect_ratio="1:1",
                duration_seconds=7,
                kling_omni=KlingOmniRequestOptions(
                    images=(KlingOmniImageInput(image_url="https://example.com/crown.png"),),
                    videos=(
                        KlingOmniVideoInput(
                            video_url="https://example.com/base.mov",
                            refer_type=KlingOmniVideoReferType.BASE,
                            keep_original_sound=True,
                        ),
                    ),
                    sound=KlingOmniSoundMode.ON,
                ),
            )
        )

        assert "aspect_ratio" not in payload
        assert "duration" not in payload
        assert payload["sound"] == "off"
        assert payload["video_list"] == [
            {
                "video_url": "https://example.com/base.mov",
                "refer_type": "base",
                "keep_original_sound": "yes",
            }
        ]

    def test_local_video_path_is_rejected(self, tmp_path: Path):
        video = tmp_path / "ref.mov"
        video.write_bytes(b"mov")

        from lib.video_backends.kling_omni import KlingOmniVideoBackend

        backend = KlingOmniVideoBackend(api_key="sk-test")
        with pytest.raises(ValueError, match="仅支持 video_url"):
            backend._build_payload(
                VideoGenerationRequest(
                    prompt="参考<<<video_1>>>生成下一个镜头",
                    output_path=tmp_path / "out.mp4",
                    kling_omni=KlingOmniRequestOptions(
                        videos=(KlingOmniVideoInput(video_path=video),),
                    ),
                )
            )

    async def test_generate_happy_path(self, tmp_path: Path):
        create_resp = _make_response(
            200,
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "task_id": "task-42",
                    "task_status": "submitted",
                },
            },
        )
        poll_resp = _make_response(
            200,
            {
                "code": 0,
                "message": "ok",
                "data": {
                    "task_id": "task-42",
                    "task_status": "succeed",
                    "task_result": {
                        "videos": [
                            {
                                "url": "https://cdn.example.com/out.mp4",
                                "duration": "5",
                            }
                        ]
                    },
                },
            },
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_download = AsyncMock(side_effect=_fake_download_factory(b"video-bytes"))

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.kling_omni._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.kling_omni.download_video", fake_download),
        ):
            from lib.video_backends.kling_omni import KlingOmniVideoBackend

            backend = KlingOmniVideoBackend(api_key="sk-test")
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="视频中的人跳舞",
                    output_path=tmp_path / "out.mp4",
                    aspect_ratio="1:1",
                    duration_seconds=5,
                )
            )

        assert result.provider == PROVIDER_KLING
        assert result.model == "kling-video-o1"
        assert result.task_id == "task-42"
        assert result.duration_seconds == 5
        assert (tmp_path / "out.mp4").read_bytes() == b"video-bytes"

        post_call = mock_client.post.call_args
        assert post_call.args[0].endswith("/v1/videos/omni-video")
        assert post_call.kwargs["json"]["prompt"] == "视频中的人跳舞"
        assert post_call.kwargs["json"]["aspect_ratio"] == "1:1"
        assert post_call.kwargs["json"]["duration"] == "5"
        assert post_call.kwargs["headers"]["Authorization"] == "Bearer sk-test"