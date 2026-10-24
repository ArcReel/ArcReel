"""HiggsfieldVideoBackend 单元测试 — 能力声明、端点分派、素材上传换 URL、提交 / 轮询 / 续跑与计量。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.providers import PROVIDER_HIGGSFIELD
from lib.backends.video_backends.higgsfield import (
    DEFAULT_MODEL,
    MODEL_KLING_O3,
    MODEL_KLING_V3,
    MODEL_SEEDANCE_2_5,
    HiggsfieldVideoBackend,
)
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json

API = "https://api.higgsfield.ai"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16
MP4 = b"\x00\x00\x00\x18ftypisom" + b"0" * 32


def _backend(model: str | None = None) -> HiggsfieldVideoBackend:
    return HiggsfieldVideoBackend(access_key="id", secret_key="secret", model=model)


def _image(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(PNG)
    return p


class TestCapabilities:
    def test_default_model(self):
        backend = _backend()
        assert backend.name == PROVIDER_HIGGSFIELD
        assert backend.model == DEFAULT_MODEL == MODEL_SEEDANCE_2_5

    def test_kling_v3_has_frames_but_no_references(self):
        caps = HiggsfieldVideoBackend.video_capabilities_for_model(MODEL_KLING_V3)
        assert (caps.text_to_video, caps.first_frame, caps.last_frame, caps.max_reference_images) == (
            True,
            True,
            True,
            0,
        )
        assert caps.max_prompt_chars == 2500
        assert caps.audio_track is VideoAudioMode.CONTROLLABLE

    def test_kling_o3_has_no_text_to_video(self):
        caps = HiggsfieldVideoBackend.video_capabilities_for_model(MODEL_KLING_O3)
        assert caps.text_to_video is False
        assert caps.max_reference_images == 4

    def test_seedance_accepts_references(self):
        caps = HiggsfieldVideoBackend.video_capabilities_for_model(MODEL_SEEDANCE_2_5)
        assert caps.max_reference_images == 30
        assert caps.max_prompt_chars is None

    def test_unknown_model_falls_back(self):
        caps = HiggsfieldVideoBackend.video_capabilities_for_model("vendor/unknown")
        assert (caps.text_to_video, caps.first_frame, caps.last_frame, caps.max_reference_images) == (
            True,
            True,
            False,
            0,
        )


class TestBuildRequest:
    def _req(self, tmp_path: Path, **kwargs) -> VideoGenerationRequest:
        return VideoGenerationRequest(prompt="一只猫走过屋顶", output_path=tmp_path / "out.mp4", **kwargs)

    def test_kling_v3_tier_follows_resolution(self, tmp_path: Path):
        backend = _backend(MODEL_KLING_V3)
        endpoint, body = backend._build_request(self._req(tmp_path, resolution="1080p", aspect_ratio="9:16"))
        assert endpoint == "/kling-video/v3.0/pro/text-to-video"
        assert body == {"prompt": "一只猫走过屋顶", "duration": 5, "sound": "on", "aspect_ratio": "9:16"}
        endpoint, _ = backend._build_request(self._req(tmp_path, resolution="4k", aspect_ratio="16:9"))
        assert endpoint == "/kling-video/v3.0/4k/text-to-video"
        endpoint, _ = backend._build_request(self._req(tmp_path, aspect_ratio="1:1"))
        assert endpoint == "/kling-video/v3.0/std/text-to-video"

    def test_kling_v3_image_to_video_omits_aspect_ratio_and_mutes(self, tmp_path: Path):
        start = _image(tmp_path, "s.png")
        endpoint, body = _backend(MODEL_KLING_V3)._build_request(
            self._req(tmp_path, start_image=start, generate_audio=False, aspect_ratio="4:3")
        )
        assert endpoint == "/kling-video/v3.0/std/image-to-video"
        assert body["sound"] == "off"
        assert "aspect_ratio" not in body

    def test_kling_v3_rejects_references_and_bad_aspect_ratio(self, tmp_path: Path):
        ref = _image(tmp_path, "r.png")
        with pytest.raises(VideoCapabilityError) as exc_info:
            _backend(MODEL_KLING_V3)._build_request(self._req(tmp_path, reference_images=[ref], aspect_ratio="16:9"))
        assert exc_info.value.code == "video_reference_images_exceeded"
        with pytest.raises(VideoCapabilityError) as exc_info:
            _backend(MODEL_KLING_V3)._build_request(self._req(tmp_path, aspect_ratio="4:3"))
        assert exc_info.value.code == "video_aspect_ratio_not_supported"

    def test_kling_v3_rejects_unknown_resolution(self, tmp_path: Path):
        with pytest.raises(VideoCapabilityError) as exc_info:
            _backend(MODEL_KLING_V3)._build_request(self._req(tmp_path, resolution="480p", aspect_ratio="16:9"))
        assert exc_info.value.code == "video_resolution_not_supported"

    def test_kling_o3_dispatch(self, tmp_path: Path):
        backend = _backend(MODEL_KLING_O3)
        with pytest.raises(VideoCapabilityError) as exc_info:
            backend._build_request(self._req(tmp_path, aspect_ratio="16:9"))
        assert exc_info.value.code == "video_capability_missing_t2v"
        start = _image(tmp_path, "s.png")
        endpoint, body = backend._build_request(
            self._req(tmp_path, start_image=start, resolution="4k", aspect_ratio="16:9")
        )
        assert endpoint == "/kling-video/o3/first-last-frame"
        assert body["mode"] == "4k"
        ref = _image(tmp_path, "r.png")
        endpoint, body = backend._build_request(self._req(tmp_path, reference_images=[ref], aspect_ratio="9:16"))
        assert endpoint == "/kling-video/o3/image-reference"
        assert body["mode"] == "std"

    def test_seedance_dispatch_and_fields(self, tmp_path: Path):
        backend = _backend(MODEL_SEEDANCE_2_5)
        endpoint, body = backend._build_request(
            self._req(tmp_path, resolution="1080p", aspect_ratio="21:9", duration_seconds=12, generate_audio=False)
        )
        assert endpoint == "/bytedance/seedance-2.5/text-to-video"
        assert body == {
            "prompt": "一只猫走过屋顶",
            "duration": 12,
            "generate_audio": False,
            "resolution": "1080p",
            "aspect_ratio": "21:9",
        }
        ref = _image(tmp_path, "r.png")
        endpoint, _ = backend._build_request(self._req(tmp_path, reference_images=[ref], aspect_ratio="16:9"))
        assert endpoint == "/bytedance/seedance-2.5/reference-to-video"
        start = _image(tmp_path, "s.png")
        endpoint, body = backend._build_request(self._req(tmp_path, start_image=start))
        assert endpoint == "/bytedance/seedance-2.5/image-to-video"
        assert "aspect_ratio" not in body

    def test_common_rejections(self, tmp_path: Path):
        backend = _backend(MODEL_SEEDANCE_2_5)
        with pytest.raises(VideoCapabilityError) as exc_info:
            backend._build_request(self._req(tmp_path, reference_audio_files=[tmp_path / "a.wav"]))
        assert exc_info.value.code == "video_reference_audio_unsupported"
        end = _image(tmp_path, "e.png")
        with pytest.raises(VideoCapabilityError) as exc_info:
            backend._build_request(self._req(tmp_path, end_image=end))
        assert exc_info.value.code == "video_end_image_requires_start_image"
        with pytest.raises(FileNotFoundError):
            backend._build_request(self._req(tmp_path, start_image=tmp_path / "missing.png"))
        with pytest.raises(VideoCapabilityError) as exc_info:
            _backend(MODEL_KLING_V3)._build_request(
                VideoGenerationRequest(prompt="x" * 2501, output_path=tmp_path / "o.mp4", aspect_ratio="16:9")
            )
        assert exc_info.value.code == "video_prompt_too_long"


class TestMetering:
    def test_defaults_to_720p_and_reports_audio_and_references(self, tmp_path: Path):
        request = VideoGenerationRequest(
            prompt="x", output_path=tmp_path / "o.mp4", duration_seconds=8, reference_images=[tmp_path / "r.png"]
        )
        usage = HiggsfieldVideoBackend.metered_usage(MODEL_SEEDANCE_2_5, request)
        assert usage.quantities["output_seconds"] == 8
        assert usage.quantities["input_images"] == 1
        assert usage.dimensions["resolution"] == "720p"
        assert usage.dimensions["audio"] is True
        assert usage.dimensions["reference_to_video"] is True


def _status_routes(router, request_id: str, *, video_url: str = "https://cdn.example.com/out.mp4"):
    router.get(f"{API}/requests/{request_id}/status").mock(
        side_effect=[
            httpx.Response(200, json={"status": "queued", "request_id": request_id}),
            httpx.Response(200, json={"status": "completed", "request_id": request_id, "video": {"url": video_url}}),
        ]
    )
    router.get(video_url).mock(return_value=httpx.Response(200, content=MP4))


class TestGenerate:
    async def test_first_last_frame_uploads_then_submits_polls_downloads_and_persists(self, tmp_path: Path):
        start, end = _image(tmp_path, "s.png"), _image(tmp_path, "e.png")
        out = tmp_path / "out.mp4"
        request = VideoGenerationRequest(
            prompt="镜头缓慢推进",
            output_path=out,
            start_image=start,
            end_image=end,
            resolution="1080p",
            duration_seconds=6,
            task_id="task-1",
        )
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            router.post(f"{API}/files/generate-upload-url").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "public_url": "https://cdn.example.com/s.png",
                            "upload_url": "https://storage.example.com/s",
                        },
                    ),
                    httpx.Response(
                        200,
                        json={
                            "public_url": "https://cdn.example.com/e.png",
                            "upload_url": "https://storage.example.com/e",
                        },
                    ),
                ]
            )
            router.put(url__regex=r"https://storage\.example\.com/.*").mock(return_value=httpx.Response(200))
            submit = router.post(f"{API}/kling-video/v3.0/pro/image-to-video").mock(
                return_value=httpx.Response(200, json={"status": "queued", "request_id": "req-1"})
            )
            _status_routes(router, "req-1")

            result = await _backend(MODEL_KLING_V3).generate(request)

        assert request_json(only_request(submit)) == {
            "prompt": "镜头缓慢推进",
            "duration": 6,
            "sound": "on",
            "image_url": "https://cdn.example.com/s.png",
            "last_image_url": "https://cdn.example.com/e.png",
        }
        assert out.read_bytes()[:8] == MP4[:8]
        assert result.task_id == "req-1"
        assert result.duration_seconds == 6
        assert result.generate_audio is True
        assert persisted == [
            {"task_id": "task-1", "job_id": "req-1", "provider": PROVIDER_HIGGSFIELD, "endpoint": None, "base_url": API}
        ]

    async def test_reference_to_video_sends_image_urls(self, tmp_path: Path):
        refs = [_image(tmp_path, "a.png"), _image(tmp_path, "b.png")]
        request = VideoGenerationRequest(
            prompt="两只猫", output_path=tmp_path / "o.mp4", reference_images=refs, aspect_ratio="16:9"
        )
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            router.post(f"{API}/files/generate-upload-url").mock(
                side_effect=[
                    httpx.Response(
                        200,
                        json={
                            "public_url": f"https://cdn.example.com/{i}.png",
                            "upload_url": f"https://storage.example.com/{i}",
                        },
                    )
                    for i in ("a", "b")
                ]
            )
            router.put(url__regex=r"https://storage\.example\.com/.*").mock(return_value=httpx.Response(200))
            submit = router.post(f"{API}/bytedance/seedance-2.5/reference-to-video").mock(
                return_value=httpx.Response(200, json={"status": "queued", "request_id": "req-2"})
            )
            _status_routes(router, "req-2")
            await _backend(MODEL_SEEDANCE_2_5).generate(request)

        body = request_json(only_request(submit))
        assert body["image_urls"] == ["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"]
        assert body["aspect_ratio"] == "16:9"
        assert body["generate_audio"] is True
        # 非 worker 路径（task_id=None）不写回 job id
        assert persisted == []

    async def test_failed_terminal_status_raises(self, tmp_path: Path):
        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "o.mp4", aspect_ratio="16:9")
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids():
            router.post(f"{API}/bytedance/seedance-2.5/text-to-video").mock(
                return_value=httpx.Response(200, json={"status": "queued", "request_id": "req-3"})
            )
            router.get(f"{API}/requests/req-3/status").mock(
                return_value=httpx.Response(200, json={"status": "failed", "request_id": "req-3", "error": "boom"})
            )
            with pytest.raises(RuntimeError, match="boom"):
                await _backend(MODEL_SEEDANCE_2_5).generate(request)


class TestResume:
    async def test_resume_polls_without_resubmitting(self, tmp_path: Path):
        out = tmp_path / "o.mp4"
        request = VideoGenerationRequest(prompt="x", output_path=out, duration_seconds=5)
        with capture_http() as router, bounded_poll_clock():
            _status_routes(router, "req-9")
            result = await _backend(MODEL_SEEDANCE_2_5).resume_video("req-9", request)
        assert result.task_id == "req-9"
        assert out.exists()
        assert all(call.request.method == "GET" for call in router.calls)

    async def test_resume_uses_submitted_base_url(self, tmp_path: Path):
        request = VideoGenerationRequest(
            prompt="x", output_path=tmp_path / "o.mp4", submitted_base_url="https://relay.example.com"
        )
        with capture_http() as router, bounded_poll_clock():
            relay = router.get("https://relay.example.com/requests/req-8/status").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "request_id": "req-8",
                        "video": {"url": "https://cdn.example.com/o.mp4"},
                    },
                )
            )
            router.get("https://cdn.example.com/o.mp4").mock(return_value=httpx.Response(200, content=MP4))
            await _backend(MODEL_SEEDANCE_2_5).resume_video("req-8", request)
        assert relay.call_count == 1

    async def test_resume_404_is_expired(self, tmp_path: Path):
        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "o.mp4")
        with capture_http() as router, bounded_poll_clock():
            router.get(f"{API}/requests/gone/status").mock(return_value=httpx.Response(404, json={"detail": "no"}))
            with pytest.raises(ResumeExpiredError):
                await _backend(MODEL_SEEDANCE_2_5).resume_video("gone", request)
