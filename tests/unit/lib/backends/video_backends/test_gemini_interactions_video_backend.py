"""Gemini Interactions API 视频调用通道：请求形状、轮询终态、取件、计量与续跑。

SDK 保持真实，出站 HTTP 由 respx 拦截，断言落在序列化后的请求与落盘的成片上。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.video_backends.gemini_interactions import (
    GeminiInteractionsVideoBackend,
    VertexInteractionsVideoBackend,
)
from tests.fakes import MP4_BYTES, bounded_poll_clock, captured_provider_job_ids, captured_vertex_credentials
from tests.http_capture import capture_http, request_json

MODEL = "gemini-omni-1.1-flash"
AISTUDIO_INTERACTIONS = "https://generativelanguage.googleapis.com/v1beta/interactions"
VERTEX_INTERACTIONS = "https://aiplatform.googleapis.com/v1beta1/projects/proj-1/locations/global/interactions"


class _NoopRateLimiter:
    async def acquire_async(self, model: str) -> None:
        return None


def _aistudio(**kwargs: Any) -> GeminiInteractionsVideoBackend:
    return GeminiInteractionsVideoBackend(
        api_key="k", rate_limiter=_NoopRateLimiter(), video_model=kwargs.pop("video_model", MODEL), **kwargs
    )


def _running(interaction_id: str = "int-1") -> dict[str, Any]:
    return {"id": interaction_id, "status": "in_progress"}


def _completed(interaction_id: str = "int-1", *, video: bytes = MP4_BYTES, tokens: int = 46336) -> dict[str, Any]:
    return {
        "id": interaction_id,
        "status": "completed",
        "steps": [
            {"type": "user_input", "content": [{"type": "text", "text": "p"}]},
            {
                "type": "model_output",
                "content": [{"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(video).decode()}],
            },
        ],
        "usage": {
            "total_output_tokens": tokens,
            "output_tokens_by_modality": [{"modality": "video", "tokens": tokens}],
        },
    }


def _request_fields(tmp_path: Path, inputs: dict[str, Any]) -> dict[str, Any]:
    """把参数里的文件名换成 tmp_path 下的文件，文件内容是去掉扩展名的文件名；其余参数原样保留。"""

    def file_of(name: str) -> Path:
        path = tmp_path / name
        path.write_bytes(Path(name).stem.encode())
        return path

    fields: dict[str, Any] = {}
    for key, value in inputs.items():
        if isinstance(value, list):
            fields[key] = [file_of(name) for name in value]
        elif isinstance(value, str):
            fields[key] = file_of(value)
        else:
            fields[key] = value
    return fields


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode()


class TestSubmit:
    @pytest.mark.parametrize("model", [MODEL, "gemini-omni-1.2-flash"])
    @pytest.mark.parametrize(
        ("inputs", "task", "images"),
        [
            pytest.param({}, "text_to_video", [], id="文生视频"),
            pytest.param(
                {"reference_images": [f"{i}.png" for i in range(6)]},
                "reference_to_video",
                [str(i).encode() for i in range(6)],
                id="六张参考图透传",
            ),
            pytest.param({"start_image": "first.png"}, "image_to_video", [b"first"], id="首帧"),
            pytest.param(
                {"start_image": "first.png", "end_image": "last.png"},
                "image_to_video",
                [b"first", b"last"],
                id="首尾帧按先首后尾排列",
            ),
            pytest.param(
                {"reference_images": ["a.png", "b.png"]}, "reference_to_video", [b"a", b"b"], id="参考图按顺序发出"
            ),
        ],
    )
    async def test_request_body_carries_task_images_and_video_format(
        self, tmp_path: Path, inputs: dict[str, Any], task: str, images: list[bytes], model: str
    ):
        fields = _request_fields(tmp_path, inputs)
        request = VideoGenerationRequest(
            prompt="一只猫走过屋顶",
            output_path=tmp_path / "out.mp4",
            aspect_ratio="16:9",
            duration_seconds=8,
            resolution="1080p",
            **fields,
        )

        with capture_http() as router, bounded_poll_clock():
            create = router.post(AISTUDIO_INTERACTIONS).respond(200, json=_completed())
            await _aistudio(video_model=model).generate(request)

        body = request_json(create.calls.last.request)
        assert body["model"] == model
        assert body["background"] is True
        assert body["generation_config"] == {"video_config": {"task": task}}
        assert body["response_format"] == {
            "type": "video",
            "aspect_ratio": "16:9",
            "duration": "8s",
            "resolution": "1080p",
        }
        [step] = body["input"]
        assert step["content"] == [
            *({"type": "image", "data": _b64(image), "mime_type": "image/png"} for image in images),
            {"type": "text", "text": "一只猫走过屋顶"},
        ]

    async def test_resolution_is_left_to_the_service_default_when_not_chosen(self, tmp_path: Path):
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=5)

        with capture_http() as router:
            create = router.post(AISTUDIO_INTERACTIONS).respond(200, json=_completed())
            await _aistudio().generate(request)

        assert "resolution" not in request_json(create.calls.last.request)["response_format"]

    @pytest.mark.parametrize(
        ("inputs", "code"),
        [
            pytest.param({"generate_audio": False}, "video_audio_switch_not_supported", id="静音请求"),
            pytest.param({"reference_audio_files": ["voice.wav"]}, "video_reference_audio_unsupported", id="参考音频"),
            pytest.param(
                {"start_image": "first.png", "reference_images": ["a.png"]},
                "video_reference_images_with_frames_unsupported",
                id="首帧与参考图混用",
            ),
            pytest.param({"end_image": "last.png"}, "video_end_image_requires_start_image", id="单独的尾帧"),
        ],
    )
    async def test_inputs_the_body_cannot_express_are_rejected_before_submit(
        self, tmp_path: Path, inputs: dict[str, Any], code: str
    ):
        fields = _request_fields(tmp_path, inputs)
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4", **fields)

        with capture_http() as router, pytest.raises(VideoCapabilityError) as raised:
            await _aistudio().generate(request)

        assert raised.value.code == code
        assert router.calls.call_count == 0


class TestPollAndCollect:
    async def test_polls_until_completed_then_writes_the_inline_video(self, tmp_path: Path):
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=8, task_id="db-task"
        )

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            router.post(AISTUDIO_INTERACTIONS).respond(200, json=_running())
            router.get(f"{AISTUDIO_INTERACTIONS}/int-1").mock(
                side_effect=[httpx.Response(200, json=_running()), httpx.Response(200, json=_completed(tokens=46336))]
            )
            result = await _aistudio().generate(request)

        assert request.output_path.read_bytes() == MP4_BYTES
        assert result.usage_tokens == 46336
        assert result.generate_audio is True
        assert result.duration_seconds == 8
        assert [(r["job_id"], r["base_url"]) for r in persisted] == [
            ("int-1", "https://generativelanguage.googleapis.com/")
        ]

    @pytest.mark.parametrize("status", ["failed", "cancelled", "incomplete"])
    async def test_a_terminal_status_other_than_completed_fails_without_an_artifact(self, tmp_path: Path, status: str):
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4")
        failed = {"id": "int-1", "status": status, "errors": [{"code": "safety", "message": "blocked by policy"}]}

        with capture_http() as router, bounded_poll_clock():
            router.post(AISTUDIO_INTERACTIONS).respond(200, json=_running())
            router.get(f"{AISTUDIO_INTERACTIONS}/int-1").respond(200, json=failed)
            with pytest.raises(RuntimeError, match="blocked by policy") as raised:
                await _aistudio().generate(request)

        assert status in str(raised.value)
        assert not request.output_path.exists()

    async def test_a_completed_interaction_without_video_data_fails(self, tmp_path: Path):
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4")
        empty = {"id": "int-1", "status": "completed", "steps": []}

        with capture_http() as router:
            router.post(AISTUDIO_INTERACTIONS).respond(200, json=empty)
            with pytest.raises(RuntimeError, match="未返回视频数据"):
                await _aistudio().generate(request)

        assert not request.output_path.exists()

    async def test_a_transient_poll_error_is_retried(self, tmp_path: Path):
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4")

        with capture_http() as router, bounded_poll_clock():
            router.post(AISTUDIO_INTERACTIONS).respond(200, json=_running())
            router.get(f"{AISTUDIO_INTERACTIONS}/int-1").mock(
                side_effect=[httpx.ConnectError("reset"), httpx.Response(200, json=_completed())]
            )
            await _aistudio().generate(request)

        assert request.output_path.read_bytes() == MP4_BYTES


class TestResume:
    async def test_resume_polls_the_interaction_without_resubmitting(self, tmp_path: Path):
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4")

        with capture_http() as router, bounded_poll_clock():
            create = router.post(AISTUDIO_INTERACTIONS).respond(500)
            router.get(f"{AISTUDIO_INTERACTIONS}/int-9").mock(
                side_effect=[httpx.Response(200, json=_running("int-9")), httpx.Response(200, json=_completed("int-9"))]
            )
            result = await _aistudio().resume_video("int-9", request)

        assert create.call_count == 0
        assert request.output_path.read_bytes() == MP4_BYTES
        assert result.usage_tokens == 46336

    async def test_a_missing_interaction_is_reported_as_expired(self, tmp_path: Path):
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4")
        gone = {"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}}

        with capture_http() as router:
            router.get(f"{AISTUDIO_INTERACTIONS}/int-9").respond(404, json=gone)
            with pytest.raises(ResumeExpiredError) as raised:
                await _aistudio().resume_video("int-9", request)

        assert raised.value.job_id == "int-9"

    async def test_resume_queries_the_domain_the_interaction_was_submitted_to(self, tmp_path: Path):
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "out.mp4", submitted_base_url="https://relay-a.example.com"
        )

        with capture_http() as router:
            relay = router.get("https://relay-a.example.com/v1beta/interactions/int-9").respond(
                200, json=_completed("int-9")
            )
            await _aistudio(base_url="https://relay-b.example.com").resume_video("int-9", request)

        assert relay.call_count == 1
        assert request.output_path.read_bytes() == MP4_BYTES


class TestVertex:
    @pytest.fixture
    def credentials_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "vertex.json"
        path.write_text(json.dumps({"project_id": "proj-1"}))
        return path

    async def test_vertex_submits_to_the_project_interactions_path_with_a_refreshed_token(
        self, tmp_path: Path, credentials_file: Path
    ):
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4")

        with captured_vertex_credentials() as load:
            credentials = load.return_value
            credentials.token, credentials.valid, credentials.expired = None, False, True
            credentials.quota_project_id = None

            def refresh(_request: object) -> None:
                credentials.token, credentials.valid, credentials.expired = "fresh-token", True, False

            credentials.refresh.side_effect = refresh
            backend = VertexInteractionsVideoBackend(
                credentials_path=str(credentials_file), rate_limiter=_NoopRateLimiter(), video_model=MODEL
            )
            with capture_http() as router:
                create = router.post(VERTEX_INTERACTIONS).respond(200, json=_completed())
                result = await backend.generate(request)

        assert create.calls.last.request.headers["authorization"] == "Bearer fresh-token"
        assert request.output_path.read_bytes() == MP4_BYTES
        assert result.generate_audio is True


class TestDeclarations:
    @pytest.mark.parametrize("backend_class", [GeminiInteractionsVideoBackend, VertexInteractionsVideoBackend])
    def test_both_credentials_declare_an_always_on_audio_track(self, backend_class: type):
        caps = backend_class.video_capabilities_for_model(MODEL)

        assert caps.audio_track is VideoAudioMode.ALWAYS_ON
        assert (caps.text_to_video, caps.first_frame, caps.last_frame) == (True, True, True)
        # 参考图张数是模型事实，通道不限
        assert caps.max_reference_images is None

    def test_frames_and_audio_track_follow_both_the_entry_and_the_channel(self):
        from lib.backends.video_capabilities import effective_video_capabilities, entry_video_facts
        from lib.catalog import query as catalog

        entry = catalog.find_entry(MODEL, media_type="video")
        assert entry is not None
        caps = effective_video_capabilities(
            GeminiInteractionsVideoBackend.video_capabilities_for_model(MODEL, entry), entry_video_facts(entry)
        )

        assert (caps.text_to_video, caps.first_frame, caps.last_frame) == (True, True, True)
        assert caps.audio_track is VideoAudioMode.ALWAYS_ON
        assert caps.max_reference_audio_count == 0

    @pytest.mark.parametrize(
        ("resolution", "tokens"),
        [("360p", 1931 * 8), ("720p", 5792 * 8), (None, 5792 * 8), ("1080p", 8688 * 8), ("4k", 17376 * 8)],
    )
    def test_estimate_converts_seconds_to_video_tokens_by_resolution(self, resolution: str | None, tokens: int):
        request = VideoGenerationRequest(
            prompt="p", output_path=Path("out.mp4"), duration_seconds=8, resolution=resolution
        )

        usage = GeminiInteractionsVideoBackend.metered_usage(MODEL, request)

        assert usage.quantities["video_tokens"] == tokens
        assert usage.quantities["output_seconds"] == 8
        assert usage.dimensions["audio"] is True

    def test_settlement_takes_the_reported_video_tokens(self):
        request = VideoGenerationRequest(prompt="p", output_path=Path("out.mp4"), duration_seconds=8)
        result = VideoGenerationResult(
            video_path=Path("out.mp4"), provider="gemini", model=MODEL, duration_seconds=8, usage_tokens=40000
        )

        usage = GeminiInteractionsVideoBackend.metered_usage(MODEL, request, result)

        assert usage.quantities["video_tokens"] == 40000
