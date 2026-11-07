"""KlingModelPathVideoBackend tests (respx intercepts transport; no real HTTP).

Cover the new Kling API request shape (model name in the path, settings, and contents[]), submission, polling, retrieval, metering,
job_id persistence and resume, rejection of legacy job_id values, and built-in/custom provider assembly.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.providers import PROVIDER_KLING
from lib.backends.video_backends.kling_model_path import KlingModelPathVideoBackend
from lib.backends.video_backends.registry import (
    create_backend,
    effective_generate_audio_for_model,
    metered_usage_for_model,
    video_capabilities_for_model,
)
from lib.custom_provider.factory import create_custom_backend
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json

_BASE = "https://api-beijing.klingai.com"
_DOWNLOAD_URL = "https://cdn.test/final.mp4"
_ENDPOINT = "kling-video-model-path"

# Minimal response samples: fields come from task creation and task-ID lookup on the official Kling 4.0 text-to-video page (docs/api-docs/endpoints/kling-video-model-path.md).


def _submitted(task_id: str = "893605946402811985") -> httpx.Response:
    return httpx.Response(
        200, json={"code": 0, "message": "SUCCEED", "request_id": "r", "data": {"id": task_id, "status": "submitted"}}
    )


def _task(status: str, *, task_id: str = "893605946402811985", message: str = "", **extra) -> httpx.Response:
    task: dict = {"id": task_id, "status": status, "message": message, **extra}
    return httpx.Response(200, json={"code": 0, "message": "SUCCEED", "request_id": "r", "data": [task]})


def _succeeded(*, output_duration: str | None = None, task_id: str = "893605946402811985") -> httpx.Response:
    extra: dict = {
        "outputs": [
            {"type": "video", "id": "v1", "url": _DOWNLOAD_URL, "watermark_url": "https://cdn.test/wm.mp4"},
        ]
    }
    if output_duration is not None:
        extra["usage"] = {"input_duration": "0", "output_duration": output_duration}
    return _task("succeeded", task_id=task_id, **extra)


def _backend(model: str = "kling-4.0", **kwargs) -> KlingModelPathVideoBackend:
    return KlingModelPathVideoBackend(api_key="static-key", model=model, **kwargs)


def _request(tmp_path: Path, **overrides) -> VideoGenerationRequest:
    kwargs: dict = {
        "prompt": "a girl on the train",
        "output_path": tmp_path / "out.mp4",
        "duration_seconds": 12,
        "aspect_ratio": "21:9",
        "resolution": "1080p",
    }
    kwargs.update(overrides)
    return VideoGenerationRequest(**kwargs)


def _frame(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n" + name.encode())
    return path


class TestConstructionAndCapabilities:
    def test_default_base_url_has_no_version_segment(self):
        assert _backend()._base_url == _BASE

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            ("https://api-beijing.klingai.com/v1", _BASE),
            ("https://relay.test/v1/", "https://relay.test"),
            ("relay.test", "https://relay.test"),
            ("https://relay.test/kling", "https://relay.test/kling"),
        ],
    )
    def test_configured_base_url_drops_trailing_version(self, configured, expected):
        assert _backend(base_url=configured)._base_url == expected

    def test_access_key_secret_key_is_rejected(self):
        # Official: Access Key / Secret Key apply only to the legacy API.
        with pytest.raises(ValueError, match="只支持 API Key"):
            KlingModelPathVideoBackend(auth_mode="jwt", access_key="ak", secret_key="sk")

    def test_missing_api_key_is_rejected(self):
        with pytest.raises(ValueError, match="API Key"):
            KlingModelPathVideoBackend(api_key=None)

    def test_registered_capabilities(self):
        caps = video_capabilities_for_model(_ENDPOINT, "kling-4.0")
        assert caps.text_to_video
        assert caps.first_frame
        assert caps.last_frame
        assert caps.max_reference_images == 0
        assert caps.audio_track is VideoAudioMode.ALWAYS_ON
        assert effective_generate_audio_for_model(_ENDPOINT, "kling-3.0-turbo") is True

    def test_registry_builds_this_backend(self):
        backend = create_backend(_ENDPOINT, auth_mode="bearer", api_key="k", model="kling-4.0-flash")
        assert isinstance(backend, KlingModelPathVideoBackend)
        assert backend.name == PROVIDER_KLING
        assert backend.model == "kling-4.0-flash"


class TestPayload:
    def test_text_to_video_uses_top_level_prompt_and_settings(self, tmp_path):
        path, payload = _backend()._build_payload(_request(tmp_path))
        assert path == "text-to-video/kling-4.0"
        assert payload == {
            "prompt": "a girl on the train",
            "settings": {"resolution": "1080p", "duration": 12, "aspect_ratio": "21:9"},
        }

    def test_unspecified_resolution_sends_720p(self, tmp_path):
        _path, payload = _backend()._build_payload(_request(tmp_path, resolution=None))
        assert payload["settings"]["resolution"] == "720p"

    def test_image_to_video_puts_prompt_and_frames_in_contents(self, tmp_path):
        first, last = _frame(tmp_path, "first.png"), _frame(tmp_path, "last.png")
        path, payload = _backend()._build_payload(_request(tmp_path, start_image=first, end_image=last))
        assert path == "image-to-video/kling-4.0"
        assert [c["type"] for c in payload["contents"]] == ["prompt", "first_frame", "last_frame"]
        assert payload["contents"][0]["text"] == "a girl on the train"
        assert payload["contents"][1]["url"]
        assert payload["contents"][1]["url"] != payload["contents"][2]["url"]
        # Image-to-video follows the first-frame aspect ratio; the body has no aspect_ratio.
        assert payload["settings"] == {"resolution": "1080p", "duration": 12}

    def test_last_frame_narrowed_by_entry_is_refused(self, tmp_path):
        first, last = _frame(tmp_path, "first.png"), _frame(tmp_path, "last.png")
        caps = replace(KlingModelPathVideoBackend.video_capabilities_for_model("kling-4.0-flash"), last_frame=False)
        request = _request(tmp_path, start_image=first, end_image=last, video_capabilities=caps)
        with pytest.raises(VideoCapabilityError) as caught:
            _backend("kling-4.0-flash")._build_payload(request)
        assert caught.value.code == "video_last_frame_unsupported"

    @pytest.mark.parametrize(
        ("overrides", "code"),
        [
            ({"generate_audio": False}, "video_audio_switch_not_supported"),
            ({"reference_audio_files": [Path("voice.wav")]}, "video_reference_audio_unsupported"),
        ],
    )
    def test_inputs_the_request_cannot_carry_are_refused(self, tmp_path, overrides, code):
        with pytest.raises(VideoCapabilityError) as caught:
            _backend()._build_payload(_request(tmp_path, **overrides))
        assert caught.value.code == code

    def test_end_frame_without_first_frame_is_refused(self, tmp_path):
        with pytest.raises(VideoCapabilityError) as caught:
            _backend()._build_payload(_request(tmp_path, end_image=_frame(tmp_path, "end.png")))
        assert caught.value.code == "video_end_image_requires_start_image"

    def test_reference_images_are_refused(self, tmp_path):
        request = _request(tmp_path, reference_images=[_frame(tmp_path, "ref.png")])
        with pytest.raises(VideoCapabilityError) as caught:
            _backend()._build_payload(request)
        assert caught.value.code == "video_reference_images_unsupported"

    def test_safe_log_view_omits_prompt_and_frames(self, tmp_path):
        first = _frame(tmp_path, "first.png")
        backend = _backend()
        path, payload = backend._build_payload(_request(tmp_path, start_image=first))
        view = backend._safe_log_view(path, payload)
        assert view["frames"] == "first_frame"
        assert view["prompt_len"] == len("a girl on the train")
        assert all(isinstance(v, (str, int)) for v in view.values() if v is not None)
        assert "contents" not in view
        assert "prompt" not in view


class TestGenerate:
    async def test_submit_poll_download_and_settle_on_billed_duration(self, tmp_path):
        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            submit = router.post(f"{_BASE}/text-to-video/kling-4.0").mock(return_value=_submitted("task-9"))
            poll = router.get(f"{_BASE}/tasks").mock(
                side_effect=[_task("processing", task_id="task-9"), _succeeded(task_id="task-9", output_duration="12")]
            )
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"mp4-bytes"))

            request = _request(tmp_path, task_id="local-1")
            result = await _backend().generate(request)

            sent = only_request(submit)
            assert sent.headers["Authorization"] == "Bearer static-key"
            assert request_json(sent)["settings"]["duration"] == 12
            assert poll.call_count == 2
            assert dict(poll.calls[0].request.url.params) == {"task_ids": "task-9"}

        assert result.video_path.read_bytes() == b"mp4-bytes"
        assert result.task_id == "task-9"
        assert result.video_uri == _DOWNLOAD_URL
        assert result.duration_seconds == 12
        assert result.generate_audio is True
        assert [(r["job_id"], r["base_url"]) for r in persisted] == [("tasks:task-9", _BASE)]

    async def test_image_to_video_route(self, tmp_path):
        with capture_http() as router:
            submit = router.post(f"{_BASE}/image-to-video/kling-3.0-turbo").mock(return_value=_submitted())
            router.get(f"{_BASE}/tasks").mock(return_value=_succeeded())
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b""))

            request = _request(tmp_path, start_image=_frame(tmp_path, "first.png"))
            result = await _backend("kling-3.0-turbo").generate(request)

            body = request_json(only_request(submit))
            assert [c["type"] for c in body["contents"]] == ["prompt", "first_frame"]

        # kling-3.0-turbo reports no usage; settlement uses submitted duration.
        assert result.duration_seconds == 12

    async def test_failed_task_raises_with_upstream_message(self, tmp_path):
        with capture_http() as router, bounded_poll_clock():
            router.post(f"{_BASE}/text-to-video/kling-4.0").mock(return_value=_submitted())
            router.get(f"{_BASE}/tasks").mock(return_value=_task("failed", message="content rejected"))
            download = router.get(_DOWNLOAD_URL)

            with pytest.raises(RuntimeError, match="content rejected"):
                await _backend().generate(_request(tmp_path))

        assert download.call_count == 0

    async def test_submit_error_envelope_raises(self, tmp_path):
        with capture_http() as router:
            router.post(f"{_BASE}/text-to-video/kling-4.0").mock(
                return_value=httpx.Response(200, json={"code": 1201, "message": "invalid duration", "data": None})
            )
            with pytest.raises(RuntimeError, match="invalid duration"):
                await _backend().generate(_request(tmp_path))

    async def test_refused_request_never_reaches_upstream(self, tmp_path):
        with capture_http() as router:
            submit = router.post(url__regex=rf"^{_BASE}/")
            with pytest.raises(VideoCapabilityError):
                await _backend().generate(_request(tmp_path, generate_audio=False))
        assert submit.call_count == 0


class TestMetering:
    def test_estimate_uses_sent_resolution_and_audio(self, tmp_path):
        usage = metered_usage_for_model(_ENDPOINT, "kling-4.0", _request(tmp_path))
        assert usage.quantities["output_seconds"] == 12
        assert usage.quantities["input_video_seconds"] == 0
        assert usage.dimensions["resolution"] == "1080p"
        assert usage.dimensions["audio"] is True
        assert usage.dimensions["input_video"] is False

    def test_estimate_without_resolution_meters_720p(self, tmp_path):
        usage = KlingModelPathVideoBackend.metered_usage("kling-4.0-flash", _request(tmp_path, resolution=None))
        assert usage.dimensions["resolution"] == "720p"

    async def test_settlement_reads_billed_duration(self, tmp_path):
        with capture_http() as router:
            router.post(f"{_BASE}/text-to-video/kling-4.0").mock(return_value=_submitted())
            router.get(f"{_BASE}/tasks").mock(return_value=_succeeded(output_duration="10"))
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b""))
            request = _request(tmp_path)
            result = await _backend().generate(request)

        usage = KlingModelPathVideoBackend.metered_usage("kling-4.0", request, result)
        assert usage.quantities["output_seconds"] == 10
        assert usage.dimensions["audio"] is True


class TestResume:
    async def test_resume_polls_without_resubmit(self, tmp_path):
        with capture_http() as router:
            submit = router.post(url__regex=rf"^{_BASE}/(text|image)-to-video/")
            poll = router.get(f"{_BASE}/tasks").mock(return_value=_succeeded(task_id="task-r"))
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b"resumed"))

            result = await _backend().resume_video("tasks:task-r", _request(tmp_path))

            assert submit.call_count == 0
            assert dict(only_request(poll).url.params) == {"task_ids": "task-r"}

        assert result.task_id == "task-r"
        assert result.generate_audio is True
        assert result.video_path.read_bytes() == b"resumed"

    @pytest.mark.parametrize("job_id", ["text2video:task-1:1", "image2video:task-1", "bare-task-id", "tasks:"])
    async def test_job_id_from_another_protocol_is_refused_before_any_request(self, tmp_path, job_id):
        with capture_http() as router:
            sent = router.route()
            with pytest.raises(ValueError, match="不是由本调用端点提交的"):
                await _backend().resume_video(job_id, _request(tmp_path))
        assert sent.call_count == 0

    async def test_resume_404_is_expired(self, tmp_path):
        with capture_http() as router, bounded_poll_clock():
            poll = router.get(f"{_BASE}/tasks").mock(
                return_value=httpx.Response(404, json={"code": 1203, "message": "task not found"})
            )
            with pytest.raises(ResumeExpiredError) as caught:
                await _backend().resume_video("tasks:task-gone", _request(tmp_path))

        assert poll.call_count == 1
        assert caught.value.job_id == "tasks:task-gone"
        assert caught.value.provider == PROVIDER_KLING

    async def test_resume_replays_submitted_base_url(self, tmp_path):
        with capture_http() as router:
            submitted = router.get("https://relay-a.test/tasks").mock(return_value=_succeeded(task_id="task-r"))
            current = router.get(url__regex=r"^https://relay-b\.test/")
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b""))

            backend = _backend(base_url="https://relay-b.test")
            await backend.resume_video("tasks:task-r", _request(tmp_path, submitted_base_url="https://relay-a.test"))

            assert only_request(submitted).headers["Authorization"] == "Bearer static-key"
            assert current.call_count == 0


def _custom_provider(base_url: str) -> MagicMock:
    provider = MagicMock()
    provider.base_url = base_url
    provider.api_key = "sk-relay"
    provider.provider_id = "custom-42"
    provider.id = 42
    return provider


class TestCustomProvider:
    async def test_relay_base_url_with_v1_is_stripped(self, tmp_path):
        backend = create_custom_backend(
            provider=_custom_provider("https://relay.test/v1"), model_id="kling-4.0", endpoint=_ENDPOINT
        )
        with capture_http() as router:
            submit = router.post("https://relay.test/text-to-video/kling-4.0").mock(return_value=_submitted())
            router.get("https://relay.test/tasks").mock(return_value=_succeeded())
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b""))

            await backend.generate(_request(tmp_path))

            assert only_request(submit).headers["Authorization"] == "Bearer sk-relay"

    async def test_path_overrides_rewrite_submit_and_poll(self, tmp_path):
        backend = create_custom_backend(
            provider=_custom_provider("https://relay.test/kling"),
            model_id="kling-4.0",
            endpoint=_ENDPOINT,
            path_overrides={"submit_text2video": "/v2/t2v/{{ model }}", "poll": "/v2/tasks"},
        )
        with capture_http() as router:
            submit = router.post("https://relay.test/kling/v2/t2v/kling-4.0").mock(return_value=_submitted("t-1"))
            poll = router.get("https://relay.test/kling/v2/tasks").mock(return_value=_succeeded(task_id="t-1"))
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b""))

            await backend.generate(_request(tmp_path))

            assert json.loads(only_request(submit).content)["prompt"] == "a girl on the train"
            # Cover preserving original task_ids when the override path has no query.
            assert dict(only_request(poll).url.params) == {"task_ids": "t-1"}
