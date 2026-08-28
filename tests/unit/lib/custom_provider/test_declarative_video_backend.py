from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from lib.custom_provider.declarative_backend import DeclarativeRuntimeError, DeclarativeVideoBackend
from lib.custom_provider.endpoint_definition import validate_definition
from lib.video_backends.base import ResumeExpiredError, VideoGenerationRequest
from tests.factories import custom_endpoint_definition
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, request_json


def _definition() -> dict:
    definition = custom_endpoint_definition()
    definition["poll"]["extract"]["usage"] = {"duration_seconds": {"paths": ["$.usage.duration"], "accept": "scalar"}}
    assert validate_definition(definition).valid
    return definition


def _request(tmp_path: Path, **overrides) -> VideoGenerationRequest:
    values = {
        "prompt": "paper boat on a river",
        "output_path": tmp_path / "out.mp4",
        "aspect_ratio": "16:9",
        "duration_seconds": 5,
        "resolution": "720p",
    }
    values.update(overrides)
    return VideoGenerationRequest(**values)


class TestDeclarativeVideoBackend:
    async def test_definition_drives_submit_poll_download_and_usage(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            poll = router.get("https://relay.test/v1/video/fetch/job-42").mock(
                side_effect=[
                    httpx.Response(200, json={"status": "processing"}),
                    httpx.Response(
                        200,
                        json={
                            "status": "completed",
                            "video_url": "https://relay.test/files/job-42.mp4",
                            "usage": {"duration": 7.5},
                        },
                    ),
                ]
            )
            download = router.get("https://relay.test/files/job-42.mp4").mock(
                return_value=httpx.Response(200, content=b"video")
            )

            result = await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=_definition(),
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert result.video_path.read_bytes() == b"video"
        assert result.video_uri == "https://relay.test/files/job-42.mp4"
        assert result.task_id == "job-42"
        assert result.duration_seconds == 8
        assert poll.call_count == 2
        assert request_json(submit.calls.last.request) == {
            "model": "video-x",
            "prompt": "paper boat on a river",
            "duration": 5,
        }
        assert submit.calls.last.request.headers["Authorization"] == "Bearer secret"
        assert download.calls.last.request.headers["Authorization"] == "Bearer secret"

    async def test_cross_origin_artifact_is_downloaded_without_credentials(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://cdn.example/signed/job-42.mp4?sig=abc"},
                )
            )
            download = router.get("https://cdn.example/signed/job-42.mp4").mock(
                return_value=httpx.Response(200, content=b"video")
            )

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=_definition(),
                provider="custom-1",
            ).generate(_request(tmp_path))

        request = download.calls.last.request
        assert "Authorization" not in request.headers
        # 签名 URL 的 query 是签名的一部分，附带 auth 节的 query 凭证会直接破坏它。
        assert request.url.query == b"sig=abc"

    async def test_succeeded_without_video_prefers_extracted_error(self, tmp_path: Path):
        with capture_http() as router:
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json={"status": "completed", "error": "moderated"})
            )

            with pytest.raises(DeclarativeRuntimeError, match="moderated") as caught:
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=_definition(),
                    provider="custom-1",
                ).generate(_request(tmp_path))

        assert caught.value.code == "declarative_response_extract_failed"

    async def test_resume_404_expires_without_submit(self, tmp_path: Path):
        with capture_http() as router:
            submit = router.post("https://relay.test/v1/video/create")
            poll = router.get("https://relay.test/v1/video/fetch/job-old").mock(
                return_value=httpx.Response(404, json={"error": "gone"})
            )

            with pytest.raises(ResumeExpiredError):
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=_definition(),
                    provider="custom-1",
                ).resume_video("job-old", _request(tmp_path))

        assert submit.call_count == 0
        assert poll.call_count == 1

    async def test_download_retries_403_and_404_without_resubmitting(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                )
            )
            download = router.get("https://relay.test/files/job-42.mp4").mock(
                side_effect=[
                    httpx.Response(403, json={"error": "not ready"}),
                    httpx.Response(404, json={"error": "propagating"}),
                    httpx.Response(200, content=b"video"),
                ]
            )

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=_definition(),
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert submit.call_count == 1
        assert download.call_count == 3

    async def test_failed_submit_body_is_recorded(self, tmp_path: Path):
        recorded: list[object] = []

        async def record(body: object) -> None:
            recorded.append(body)

        with capture_http() as router:
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(400, json={"error": "bad prompt"})
            )
            with pytest.raises(httpx.HTTPStatusError):
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=_definition(),
                    provider="custom-1",
                ).generate(_request(tmp_path, on_provider_response=record))

        assert recorded == [{"error": "bad prompt"}]

    async def test_download_exhausts_shared_ten_failure_budget(self, tmp_path: Path):
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                )
            )
            download = router.get("https://relay.test/files/job-42.mp4").mock(
                return_value=httpx.Response(403, json={"error": "not ready"})
            )

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=_definition(),
                    provider="custom-1",
                ).generate(_request(tmp_path))

        assert caught.value.code == "artifact_download_failed"
        assert download.call_count == 10

    async def test_resume_success_polls_and_downloads_without_submit(self, tmp_path: Path):
        with capture_http() as router:
            submit = router.post("https://relay.test/v1/video/create")
            router.get("https://relay.test/v1/video/fetch/job-old").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-old.mp4"},
                )
            )
            router.get("https://relay.test/files/job-old.mp4").mock(
                return_value=httpx.Response(200, content=b"resumed")
            )

            result = await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=_definition(),
                provider="custom-1",
            ).resume_video("job-old", _request(tmp_path))

        assert submit.call_count == 0
        assert result.video_path.read_bytes() == b"resumed"
