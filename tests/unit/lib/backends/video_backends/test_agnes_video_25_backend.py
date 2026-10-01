"""Agnes 2.5 video contract tests: request variants, polling, and pre-submit validation."""

from __future__ import annotations

import base64
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
import respx

from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.video_backends.agnes import AgnesVideoBackend
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, only_request, request_json

_BASE_URL = "https://apihub.agnes-ai.com/v1"


class _AgnesRoutes(NamedTuple):
    submit: respx.Route
    query: respx.Route
    download: respx.Route


@contextmanager
def _agnes_api() -> Generator[_AgnesRoutes]:
    host = _BASE_URL.removesuffix("/v1")
    with capture_http() as router:
        yield _AgnesRoutes(
            submit=router.post(f"{_BASE_URL}/videos"),
            query=router.get(f"{host}/agnesapi"),
            download=router.get(url__regex=r"^https://cdn\.agnes/"),
        )


def _json(body: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=body)


def _queued(video_id: str = "vid-1") -> httpx.Response:
    return _json({"video_id": video_id, "status": "queued"})


def _completed(
    video_id: str = "vid-1",
    *,
    seconds: str = "4",
    url: str = "https://cdn.agnes/out.mp4",
) -> httpx.Response:
    return _json(
        {
            "video_id": video_id,
            "status": "completed",
            "seconds": seconds,
            "url": url,
        }
    )


def _request(tmp_path: Path, **overrides) -> VideoGenerationRequest:
    params: dict = {
        "prompt": "p",
        "output_path": tmp_path / "o.mp4",
        "aspect_ratio": "9:16",
        "duration_seconds": 5,
    }
    params.update(overrides)
    return VideoGenerationRequest(**params)


def _write_image(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def _data_uri(payload: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def _backend(model: str = "agnes-video-2.5") -> AgnesVideoBackend:
    return AgnesVideoBackend(api_key="sk-test", model=model, base_url=_BASE_URL)


def _assert_v25_core_body(body: dict, *, mode: str, seconds: str, size: str) -> None:
    assert body["model"] in {"agnes-video-2.5", "agnes-video-2.5-flash"}
    assert body["prompt"] == "p"
    assert body["mode"] == mode
    assert body["seconds"] == seconds
    assert body["size"] == size
    assert body["aspect_ratio"] == "9:16"
    assert "width" not in body
    assert "height" not in body
    assert "fps" not in body
    assert "num_frames" not in body


class TestV25RequestBodies:
    async def test_text_to_video_body_and_polling(self, tmp_path: Path):
        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued())
            routes.query.mock(return_value=_completed(seconds="8"))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await _backend().generate(_request(tmp_path, duration_seconds=8, resolution="1080p", seed=7))

        body = request_json(only_request(routes.submit))
        _assert_v25_core_body(body, mode="text", seconds="8", size="1080P")
        assert body["seed"] == 7
        assert "image" not in body
        assert "images" not in body
        assert "first_frame" not in body
        assert "last_frame" not in body
        assert "extra_body" not in body

        poll = only_request(routes.query)
        assert poll.url.path == "/agnesapi"
        assert dict(poll.url.params) == {"video_id": "vid-1", "model_name": "agnes-video-2.5"}
        assert result.duration_seconds == 8
        assert result.generate_audio is True

    async def test_keyframe_body_uses_data_uri_frames(self, tmp_path: Path):
        start_bytes = b"start"
        end_bytes = b"end"
        start = _write_image(tmp_path / "start.png", start_bytes)
        end = _write_image(tmp_path / "end.png", end_bytes)

        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued())
            routes.query.mock(return_value=_completed(seconds="6"))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            await _backend("agnes-video-2.5-flash").generate(
                _request(tmp_path, start_image=start, end_image=end, resolution="720p")
            )

        body = request_json(only_request(routes.submit))
        _assert_v25_core_body(body, mode="keyframe", seconds="5", size="720P")
        assert body["first_frame"] == _data_uri(start_bytes)
        assert body["last_frame"] == _data_uri(end_bytes)
        assert "image" not in body
        assert "images" not in body
        assert "extra_body" not in body

    async def test_reference_body_uses_data_uri_list(self, tmp_path: Path):
        first = _write_image(tmp_path / "r1.png", b"r1")
        second = _write_image(tmp_path / "r2.png", b"r2")

        with _agnes_api() as routes:
            routes.submit.mock(return_value=_queued())
            routes.query.mock(return_value=_completed(seconds="4"))
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            await _backend().generate(_request(tmp_path, reference_images=[first, second], duration_seconds=4))

        body = request_json(only_request(routes.submit))
        _assert_v25_core_body(body, mode="reference", seconds="4", size="720P")
        assert body["images"] == [_data_uri(b"r1"), _data_uri(b"r2")]
        assert "first_frame" not in body
        assert "last_frame" not in body
        assert "image" not in body
        assert "extra_body" not in body

    async def test_polling_ignores_internal_status_and_uses_seconds(self, tmp_path: Path):
        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_queued())
            routes.query.mock(
                side_effect=[
                    _json({"video_id": "vid-1", "status": "queued", "internal_status": "pending"}),
                    _json({"video_id": "vid-1", "status": "in_progress", "internal_status": "pending"}),
                    _completed(seconds="12"),
                ]
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await _backend().generate(_request(tmp_path))

            assert routes.query.call_count == 3
            for call in routes.query.calls:
                assert dict(call.request.url.params) == {
                    "video_id": "vid-1",
                    "model_name": "agnes-video-2.5",
                }

        assert result.duration_seconds == 12

    async def test_transient_poll_error_uses_outer_retry(self, tmp_path: Path):
        with _agnes_api() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_queued())
            routes.query.mock(
                side_effect=[
                    _json({"error": "busy"}, status_code=503),
                    _completed(),
                ]
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4"))

            result = await _backend().generate(_request(tmp_path))

        assert routes.query.call_count == 2
        assert result.duration_seconds == 4

    async def test_resume_404_bypasses_inner_retry(self, tmp_path: Path):
        with _agnes_api() as routes:
            routes.query.mock(return_value=_json({"error": "not found"}, status_code=404))

            with pytest.raises(ResumeExpiredError) as exc:
                await _backend().resume_video("vid-404", _request(tmp_path))

        assert exc.value.job_id == "vid-404"
        assert routes.query.call_count == 1


class TestV25PreSubmitValidation:
    @pytest.mark.parametrize("duration", [3, 13])
    async def test_duration_outside_4_to_12_is_rejected(self, tmp_path: Path, duration: int):
        with _agnes_api() as routes, pytest.raises(VideoCapabilityError) as exc:
            await _backend().generate(_request(tmp_path, duration_seconds=duration))

        assert exc.value.code == "video_duration_not_supported"
        assert routes.submit.call_count == 0

    @pytest.mark.parametrize("resolution", ["1080p", "2K"])
    async def test_flash_rejects_non_720p_resolution(self, tmp_path: Path, resolution: str):
        with _agnes_api() as routes, pytest.raises(VideoCapabilityError) as exc:
            await _backend("agnes-video-2.5-flash").generate(_request(tmp_path, resolution=resolution))

        assert exc.value.code == "video_resolution_not_supported"
        assert routes.submit.call_count == 0

    async def test_invalid_aspect_ratio_is_rejected(self, tmp_path: Path):
        with _agnes_api() as routes, pytest.raises(VideoCapabilityError) as exc:
            await _backend().generate(_request(tmp_path, aspect_ratio="5:4"))

        assert exc.value.code == "video_aspect_ratio_not_supported"
        assert routes.submit.call_count == 0

    @pytest.mark.parametrize(
        ("model", "count"),
        [("agnes-video-2.5-flash", 6), ("agnes-video-2.5", 9)],
    )
    async def test_reference_image_limit_is_model_specific(self, tmp_path: Path, model: str, count: int):
        refs = [_write_image(tmp_path / f"r{i}.png", str(i).encode()) for i in range(count)]

        with _agnes_api() as routes, pytest.raises(VideoCapabilityError) as exc:
            await _backend(model).generate(_request(tmp_path, reference_images=refs))

        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params["limit"] == (5 if model.endswith("flash") else 8)
        assert routes.submit.call_count == 0
