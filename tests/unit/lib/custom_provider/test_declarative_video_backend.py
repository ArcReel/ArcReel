from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from lib.custom_provider.declarative_backend import (
    DeclarativeRuntimeError,
    DeclarativeVideoBackend,
    extract_duration,
    extract_provider_state,
)
from lib.custom_provider.endpoint_definition import validate_definition
from lib.db.repositories.usage_repo import MAX_BILLED_DURATION_SECONDS
from lib.generation_worker import _encode_task_failure_message
from lib.video_backends.base import (
    VIDEO_POLL_MAX_CONSECUTIVE_FAILURES,
    ProviderJobStatus,
    ProviderRejectedError,
    ProviderResponseStage,
    ResumeExpiredError,
    VideoGenerationRequest,
)
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

    async def test_artifact_on_the_fixed_submit_host_gets_credentials(self, tmp_path: Path):
        """信任集来自渲染出的请求源：提交写死绝对地址时，提交主机上的产物照样附凭证下载。"""
        definition = _definition()
        definition["submit"] = {**definition["submit"], "url": "https://submit.test/v1/video/create"}
        definition["poll"] = {**definition["poll"], "url": "https://poll.test/v1/video/fetch/{{ task_id }}"}
        with capture_http() as router, bounded_poll_clock():
            router.post("https://submit.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://poll.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200, json={"status": "completed", "video_url": "https://submit.test/files/job-42.mp4"}
                )
            )
            download = router.get("https://submit.test/files/job-42.mp4").mock(
                return_value=httpx.Response(200, content=b"video")
            )

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert download.calls.last.request.headers["Authorization"] == "Bearer secret"

    async def test_an_unused_configured_base_origin_is_not_trusted_for_artifacts(self, tmp_path: Path):
        """配置了却从未被请求的 base_url 源不进信任集：产物落在那个源上按裸请求下载。"""
        definition = _definition()
        definition["submit"] = {**definition["submit"], "url": "https://submit.test/v1/video/create"}
        definition["poll"] = {**definition["poll"], "url": "https://submit.test/v1/video/fetch/{{ task_id }}"}
        with capture_http() as router, bounded_poll_clock():
            router.post("https://submit.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://submit.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200, json={"status": "completed", "video_url": "https://unused.test/files/job-42.mp4"}
                )
            )
            download = router.get("https://unused.test/files/job-42.mp4").mock(
                return_value=httpx.Response(200, content=b"video")
            )

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://unused.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert "Authorization" not in download.calls.last.request.headers

    async def test_redirect_to_another_origin_drops_custom_auth_headers(self, tmp_path: Path):
        """同源产物地址跳到 CDN 时，自定义头名的凭证同样不许跟过去。

        httpx 跨源只摘 Authorization，而 auth 节可以用任意头名。
        """
        definition = _definition()
        definition["auth"] = {"headers": {"X-API-Key": "{{ api_key }}"}}
        assert validate_definition(definition).valid

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
            origin = router.get("https://relay.test/files/job-42.mp4").mock(
                return_value=httpx.Response(302, headers={"location": "https://cdn.example/signed/job-42.mp4"})
            )
            cdn = router.get("https://cdn.example/signed/job-42.mp4").mock(
                return_value=httpx.Response(200, content=b"video")
            )

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert origin.calls.last.request.headers["X-API-Key"] == "secret"
        assert "X-API-Key" not in cdn.calls.last.request.headers

    async def test_submit_redirect_to_another_origin_drops_custom_auth_headers(self, tmp_path: Path):
        """提交 / 轮询 / 二次取件同样带着 auth 节，跨源跳转时凭证不许跟过去。"""
        definition = _definition()
        definition["auth"] = {"headers": {"X-API-Key": "{{ api_key }}"}}
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock():
            submit = router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(307, headers={"location": "https://elsewhere.test/v1/video/create"})
            )
            moved = router.post("https://elsewhere.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert submit.calls.last.request.headers["X-API-Key"] == "secret"
        assert "X-API-Key" not in moved.calls.last.request.headers

    async def test_submit_302_does_not_replay_the_creation_payload(self, tmp_path: Path):
        """302 后按 GET 跟随、丢掉请求体：原样重放创建请求会在跳转目标上再建一个付费任务。"""
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(302, headers={"location": "https://relay.test/v1/video/created"})
            )
            replayed = router.post("https://relay.test/v1/video/created")
            followed = router.get("https://relay.test/v1/video/created").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=_definition(),
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert replayed.call_count == 0
        assert followed.call_count == 1
        assert followed.calls.last.request.read() == b""

    async def test_poll_on_another_host_keeps_credentials_but_scopes_them_there(self, tmp_path: Path):
        """定义可以把轮询端点写在与 base_url 不同的主机上：凭证按该请求自己的源判作用域。"""
        definition = _definition()
        definition["auth"] = {"headers": {"X-API-Key": "{{ api_key }}"}}
        definition["poll"]["url"] = "https://status.test/v1/video/fetch/{{ task_id }}"
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            poll = router.get("https://status.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(302, headers={"location": "https://elsewhere.test/fetch/job-42"})
            )
            moved = router.get("https://elsewhere.test/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        # 定义指名的主机拿得到凭证；服务端把它引去的第三方主机拿不到。
        assert poll.calls.last.request.headers["X-API-Key"] == "secret"
        assert "X-API-Key" not in moved.calls.last.request.headers

    async def test_same_origin_redirect_keeps_query_credentials(self, tmp_path: Path):
        """按 query 传的凭证在同源续跳上要补回：Location 会整体替换查询串。

        一次 `/fetch/job-42` → `/fetch/job-42/` 的规范化跳转就足以丢掉 api_key。
        """
        definition = _definition()
        definition["auth"] = {"query": {"key": "{{ api_key }}"}}
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(302, headers={"location": "/v1/video/fetch/job-42/"})
            )
            followed = router.get("https://relay.test/v1/video/fetch/job-42/").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert followed.calls.last.request.url.params["key"] == "secret"

    async def test_same_origin_redirect_keeps_the_location_query_too(self, tmp_path: Path):
        """凭证要合并进 Location 自带的查询串，而不是整串替换掉它。"""
        definition = _definition()
        definition["auth"] = {"query": {"key": "{{ api_key }}"}}

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(302, headers={"location": "/v1/video/fetch/job-42/?region=us"})
            )
            followed = router.get("https://relay.test/v1/video/fetch/job-42/").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        params = followed.calls.last.request.url.params
        assert params["key"] == "secret"
        assert params["region"] == "us"

    async def test_artifact_on_the_poll_host_gets_credentials(self, tmp_path: Path):
        """定义可以把端点写在别的主机上，产物地址往往就出自那台主机。

        只按 base_url 判同源会让这类下载裸请求、认证失败。
        """
        definition = _definition()
        definition["poll"]["url"] = "https://status.test/v1/video/fetch/{{ task_id }}"
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://status.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://status.test/files/job-42.mp4"},
                )
            )
            download = router.get("https://status.test/files/job-42.mp4").mock(
                return_value=httpx.Response(200, content=b"video")
            )

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert download.calls.last.request.headers["Authorization"] == "Bearer secret"

    async def test_redirect_target_does_not_become_a_trusted_artifact_origin(self, tmp_path: Path):
        """轮询被跨源重定向后，终点主机不算「访问过的可信源」。

        凭证在那一跳早已被卸掉；把终点当可信源，等于让产物下载把 key 送给一个从没验证过
        的第三方主机。
        """
        definition = _definition()
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(302, headers={"location": "https://evil.test/fetch/job-42"})
            )
            router.get("https://evil.test/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://evil.test/files/job-42.mp4"},
                )
            )
            download = router.get("https://evil.test/files/job-42.mp4").mock(
                return_value=httpx.Response(200, content=b"video")
            )

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert "Authorization" not in download.calls.last.request.headers

    async def test_cross_origin_redirect_drops_query_credentials(self, tmp_path: Path):
        definition = _definition()
        definition["auth"] = {"query": {"key": "{{ api_key }}"}}

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(302, headers={"location": "https://elsewhere.test/fetch/job-42"})
            )
            moved = router.get("https://elsewhere.test/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert "key" not in moved.calls.last.request.url.params

    async def test_same_origin_redirect_keeps_credentials(self, tmp_path: Path):
        definition = _definition()
        definition["auth"] = {"headers": {"X-API-Key": "{{ api_key }}"}}

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
            router.get("https://relay.test/files/job-42.mp4").mock(
                return_value=httpx.Response(302, headers={"location": "/files/job-42-v2.mp4"})
            )
            final = router.get("https://relay.test/files/job-42-v2.mp4").mock(
                return_value=httpx.Response(200, content=b"video")
            )

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert final.calls.last.request.headers["X-API-Key"] == "secret"

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
        recorded: list[tuple[str, object]] = []

        async def record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

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

        assert recorded == [("submit", {"error": "bad prompt"})]

    async def test_rejected_submit_carries_the_provider_reason(self, tmp_path: Path):
        """声明式后端复用 submit_post，上游 4xx 的拒因摘要同样挂在异常上。"""
        with capture_http() as router:
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(
                    400, json={"error": {"code": "PromptRejected", "message": "prompt violates the policy"}}
                )
            )
            with pytest.raises(ProviderRejectedError) as excinfo:
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=_definition(),
                    provider="custom-1",
                ).generate(_request(tmp_path))

        assert excinfo.value.provider_reason == "PromptRejected: prompt violates the policy"
        stored = _encode_task_failure_message(excinfo.value)
        assert json.loads(stored.split("] ", 1)[1]) == {
            "provider_reason": "PromptRejected: prompt violates the policy",
            "status": 400,
        }

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

    async def test_submitted_base_url_is_replayed_on_resume(self, tmp_path: Path):
        """用户在途改了供应商域名：续跑仍按提交时的域名轮询，不把旧任务误判成过期。"""
        with capture_http() as router:
            old_host = router.get("https://old.relay.test/v1/video/fetch/job-old").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://old.relay.test/files/job-old.mp4"},
                )
            )
            new_host = router.get("https://new.relay.test/v1/video/fetch/job-old")
            download = router.get("https://old.relay.test/files/job-old.mp4").mock(
                return_value=httpx.Response(200, content=b"resumed")
            )

            result = await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://new.relay.test",
                model="video-x",
                definition=_definition(),
                provider="custom-1",
            ).resume_video("job-old", _request(tmp_path, submitted_base_url="https://old.relay.test"))

        assert old_host.call_count == 1
        assert new_host.call_count == 0
        assert result.video_path.read_bytes() == b"resumed"
        # 同源判定跟着回放的域名走，凭证照常附上。
        assert download.calls.last.request.headers["Authorization"] == "Bearer secret"

    async def test_submit_persists_the_base_url_it_used(self, tmp_path: Path):
        from tests.fakes import captured_provider_job_ids

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"},
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"v"))

            await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=_definition(),
                provider="custom-1",
            ).generate(_request(tmp_path, task_id="task-1"))

        assert persisted[-1]["base_url"] == "https://relay.test"

    async def test_unreadable_input_asset_fails_with_the_stable_code(self, tmp_path: Path):
        """素材在任务准备与执行之间消失时也要落稳定错误码，而不是没有译文的裸 OSError 文本。"""
        with capture_http() as router:
            submit = router.post("https://relay.test/v1/video/create")

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=_definition(),
                    provider="custom-1",
                ).generate(_request(tmp_path, start_image=tmp_path / "vanished.png"))

        assert caught.value.code == "declarative_template_render_failed"
        assert submit.call_count == 0

    async def test_template_failure_keeps_its_detail_locale_neutral(self, tmp_path: Path):
        definition = _definition()
        definition["enum_maps"] = {"duration": {"10": 10}}

        with capture_http() as router:
            submit = router.post("https://relay.test/v1/video/create")
            with pytest.raises(DeclarativeRuntimeError) as caught:
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=definition,
                    provider="custom-1",
                ).generate(_request(tmp_path))

        assert caught.value.params["detail"] == {
            "key": "val_ce_enum_map_value_missing",
            "params": {"name": "duration", "value": "5"},
        }
        assert str(caught.value) == "enum_maps.duration 缺少 '5' 的映射"
        assert submit.call_count == 0

    async def test_non_json_success_response_is_recorded(self, tmp_path: Path):
        """2xx 却不是 JSON（网关 HTML 错误页、被截断的响应）时，原文也要留痕。"""
        recorded: list[tuple[str, object]] = []

        async def _record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, text="<html>gateway timeout</html>")
            )

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=_definition(),
                    provider="custom-1",
                ).generate(_request(tmp_path, on_provider_response=_record))

        assert caught.value.code == "declarative_response_extract_failed"
        assert recorded[-1] == ("submit", "<html>gateway timeout</html>")

    async def test_jsonpath_evaluation_failure_keeps_its_detail_locale_neutral(self, tmp_path: Path):
        definition = _definition()
        definition["submit"]["extract"]["task_id"] = ["$[?@.a == 1e400]"]

        with capture_http() as router:
            router.post("https://relay.test/v1/video/create").mock(return_value=httpx.Response(200, json={"a": 1}))

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=definition,
                    provider="custom-1",
                ).generate(_request(tmp_path))

        assert caught.value.code == "declarative_response_extract_failed"
        assert caught.value.params["detail"] == {
            "key": "val_ce_jsonpath_evaluation_failed",
            "params": {"path_expression": "$[?@.a == 1e400]"},
        }

    async def test_missing_required_input_fails_before_submitting(self, tmp_path: Path):
        """声明为必需的素材缺席时不许发请求：模板会把该键整个删掉，供应商照样建任务照常计费。"""
        definition = _definition()
        definition["inputs"]["first_frame"]["required"] = True
        assert validate_definition(definition).valid

        with capture_http() as router:
            submit = router.post("https://relay.test/v1/video/create")

            with pytest.raises(DeclarativeRuntimeError) as caught:
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=definition,
                    provider="custom-1",
                ).generate(_request(tmp_path))

        assert caught.value.code == "declarative_template_render_failed"
        assert "first_frame" in caught.value.params["detail"]
        assert submit.call_count == 0

    async def test_resume_does_not_require_submit_assets(self, tmp_path: Path):
        """续跑的请求本就不带素材（校验器也禁止 poll/result 模板引用 inputs）。

        在续跑上查必需项，会把每一笔「必需图输入」端点的已付费任务判死在第一次轮询之前。
        """
        definition = _definition()
        definition["inputs"]["first_frame"]["required"] = True

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
                definition=definition,
                provider="custom-1",
            ).resume_video("job-old", _request(tmp_path))

        assert submit.call_count == 0
        assert result.video_path.read_bytes() == b"resumed"

    async def test_numeric_provider_task_id_is_accepted(self, tmp_path: Path):
        """accept=scalar 的定义可以命中数字，格式口径是按字符串化交给下游。"""
        definition = _definition()
        definition["submit"]["extract"]["task_id"] = {"paths": ["$.task_id"], "accept": "scalar"}
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": 12345})
            )
            poll = router.get("https://relay.test/v1/video/fetch/12345").mock(
                return_value=httpx.Response(
                    200,
                    json={"status": "completed", "video_url": "https://relay.test/files/12345.mp4"},
                )
            )
            router.get("https://relay.test/files/12345.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            result = await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        assert result.task_id == "12345"
        assert poll.call_count == 1

    async def test_failed_status_without_error_still_fails_immediately(self, tmp_path: Path):
        """供应商判负却没给理由时也必须立刻终态，不是一路轮询到超时。"""
        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            poll = router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json={"status": "failed"})
            )

            with pytest.raises(RuntimeError, match="provider reported failure"):
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=_definition(),
                    provider="custom-1",
                ).generate(_request(tmp_path))

        assert poll.call_count == 1

    async def test_result_request_404_on_resume_is_retried_not_expired(self, tmp_path: Path):
        """只有轮询端点的 404 是「远端任务没了」。

        result 端点的 404 说的是产物还没就绪；续跑期把它判成过期会永久丢掉一条已经成功的
        付费任务，它该留给取件预算重试。
        """
        definition = _definition()
        definition["poll"]["extract"] = {"status": ["$.status"], "result_id": ["$.result_id"]}
        definition["result"] = {
            "method": "GET",
            "url": "{{ base_url }}/v1/video/result/{{ result_id }}",
            "extract": {"video_url": ["$.video_url"]},
        }
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock():
            router.get("https://relay.test/v1/video/fetch/job-old").mock(
                return_value=httpx.Response(200, json={"status": "completed", "result_id": "r-9"})
            )
            result = router.get("https://relay.test/v1/video/result/r-9").mock(
                side_effect=[
                    httpx.Response(404, json={"error": "not ready"}),
                    httpx.Response(200, json={"video_url": "https://relay.test/files/job-old.mp4"}),
                ]
            )
            router.get("https://relay.test/files/job-old.mp4").mock(
                return_value=httpx.Response(200, content=b"resumed")
            )

            outcome = await DeclarativeVideoBackend(
                api_key="secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).resume_video("job-old", _request(tmp_path))

        assert result.call_count == 2
        assert outcome.video_path.read_bytes() == b"resumed"

    async def test_poll_404_on_resume_still_expires(self, tmp_path: Path):
        definition = _definition()
        definition["poll"]["extract"] = {"status": ["$.status"], "result_id": ["$.result_id"]}
        definition["result"] = {
            "method": "GET",
            "url": "{{ base_url }}/v1/video/result/{{ result_id }}",
            "extract": {"video_url": ["$.video_url"]},
        }

        with capture_http() as router:
            poll = router.get("https://relay.test/v1/video/fetch/job-old").mock(
                return_value=httpx.Response(404, json={"error": "gone"})
            )

            with pytest.raises(ResumeExpiredError):
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=definition,
                    provider="custom-1",
                ).resume_video("job-old", _request(tmp_path))

        assert poll.call_count == 1

    async def test_query_credentials_are_not_in_the_failure_text(self, tmp_path: Path):
        """auth 节按 query 传凭证时，HTTP 错误的消息会落进 task.error_message 与日志。"""
        definition = _definition()
        definition["auth"] = {"query": {"key": "{{ api_key }}"}}
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(400, json={"error": "bad request"})
            )

            with pytest.raises(httpx.HTTPStatusError) as caught:
                await DeclarativeVideoBackend(
                    api_key="super-secret-key",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=definition,
                    provider="custom-1",
                ).generate(_request(tmp_path))

        assert "super-secret-key" not in str(caught.value)
        assert caught.value.response.status_code == 400

    async def test_result_request_failure_body_is_recorded(self, tmp_path: Path):
        definition = _definition()
        definition["poll"]["extract"] = {"status": ["$.status"], "result_id": ["$.result_id"]}
        definition["result"] = {
            "method": "GET",
            "url": "{{ base_url }}/v1/video/result/{{ result_id }}",
            "extract": {"video_url": ["$.video_url"]},
        }
        assert validate_definition(definition).valid
        recorded: list[tuple[str, object]] = []

        async def _record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

        with capture_http() as router, bounded_poll_clock():
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(200, json={"status": "completed", "result_id": "r-9"})
            )
            result = router.get("https://relay.test/v1/video/result/r-9").mock(
                return_value=httpx.Response(500, json={"error": "result exploded"})
            )

            # 任务已成功、钱已花：二次取件的瞬态失败共用产物取件预算，耗尽后落可恢复的
            # artifact_download_failed，而不是就地废掉一条已付费的成片。
            with pytest.raises(DeclarativeRuntimeError) as caught:
                await DeclarativeVideoBackend(
                    api_key="secret",
                    base_url="https://relay.test",
                    model="video-x",
                    definition=definition,
                    provider="custom-1",
                ).generate(_request(tmp_path, on_provider_response=_record))

        assert caught.value.code == "artifact_download_failed"
        assert result.call_count == VIDEO_POLL_MAX_CONSECUTIVE_FAILURES
        assert recorded[-1] == ("result", {"error": "result exploded"})

    async def test_request_log_drops_auth_query_credentials(self, tmp_path: Path, caplog):
        """请求日志按键名遮蔽，遮不到拼进 URL 查询串里的 ``auth.query`` 凭证。"""
        definition = _definition()
        definition["auth"] = {"query": {"key": "{{ api_key }}"}}
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock(), caplog.at_level("INFO"):
            router.post(url__regex=r"^https://relay\.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get(url__regex=r"^https://relay\.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200, json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"}
                )
            )
            router.get(url__regex=r"^https://relay\.test/files/job-42\.mp4").mock(
                return_value=httpx.Response(200, content=b"video")
            )

            await DeclarativeVideoBackend(
                api_key="sk-super-secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        # 断言范围限本模块写入的日志；httpx 自己的 `HTTP Request:` 行不在其内。
        logged = "\n".join(
            record.getMessage() for record in caplog.records if record.name == "lib.custom_provider.declarative_backend"
        )
        assert "声明式视频请求" in logged
        assert "sk-super-secret" not in logged

    async def test_request_log_summarizes_raw_base64_assets(self, tmp_path: Path, caplog):
        """``encoding: "base64"`` 渲染出的是裸 base64 串，没有 data URI 前缀可识别；日志按形状摘要。"""
        definition = _definition()
        definition["inputs"] = {"first_frame": {"source": "start_image", "encoding": "base64"}}
        assert validate_definition(definition).valid
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"\x89PNG" + b"x" * 1024)
        encoded_prefix = base64.b64encode(frame.read_bytes()).decode("ascii")[:64]

        with capture_http() as router, bounded_poll_clock(), caplog.at_level("INFO"):
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200, json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"}
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="sk-super-secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path, start_image=frame))

        logged = "\n".join(
            record.getMessage() for record in caplog.records if record.name == "lib.custom_provider.declarative_backend"
        )
        assert "声明式视频请求" in logged
        assert encoded_prefix not in logged

    async def test_request_log_summarizes_assets_shorter_than_the_shape_threshold(self, tmp_path: Path, caplog):
        """小到编不出摘要阈值长度的素材（如 1×1 PNG）照样是用户媒体：按来源摘，不按体积。"""
        definition = _definition()
        definition["inputs"] = {"first_frame": {"source": "start_image", "encoding": "base64"}}
        assert validate_definition(definition).valid
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"\x89PNG tiny")
        encoded = base64.b64encode(frame.read_bytes()).decode("ascii")

        with capture_http() as router, bounded_poll_clock(), caplog.at_level("INFO"):
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200, json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"}
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="sk-super-secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path, start_image=frame))

        logged = "\n".join(
            record.getMessage() for record in caplog.records if record.name == "lib.custom_provider.declarative_backend"
        )
        assert len(encoded) < 256
        assert encoded not in logged

    async def test_request_log_summarizes_assets_embedded_in_mixed_text(self, tmp_path: Path, caplog):
        """定义可以把素材拼进 ``"prefix{{ inputs.first_frame }}"``：既不等值也过不了纯 base64 兜底。"""
        definition = _definition()
        definition["inputs"] = {"first_frame": {"source": "start_image", "encoding": "base64"}}
        definition["submit"]["body"]["image"] = "prefix:{{ inputs.first_frame }}"
        assert validate_definition(definition).valid
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"\x89PNG tiny")
        encoded = base64.b64encode(frame.read_bytes()).decode("ascii")

        with capture_http() as router, bounded_poll_clock(), caplog.at_level("INFO"):
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200, json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"}
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="sk-super-secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path, start_image=frame))

        logged = "\n".join(
            record.getMessage() for record in caplog.records if record.name == "lib.custom_provider.declarative_backend"
        )
        assert "prefix:" in logged
        assert encoded not in logged

    async def test_request_log_masks_arbitrary_auth_header_names(self, tmp_path: Path, caplog):
        """通用敏感词表认不出 ``Ocp-Apim-Subscription-Key`` 这类名字；按定义 ``auth.headers`` 的键名遮。"""
        definition = _definition()
        definition["auth"] = {"headers": {"Ocp-Apim-Subscription-Key": "{{ api_key }}"}}
        assert validate_definition(definition).valid

        with capture_http() as router, bounded_poll_clock(), caplog.at_level("INFO"):
            router.post("https://relay.test/v1/video/create").mock(
                return_value=httpx.Response(200, json={"task_id": "job-42"})
            )
            router.get("https://relay.test/v1/video/fetch/job-42").mock(
                return_value=httpx.Response(
                    200, json={"status": "completed", "video_url": "https://relay.test/files/job-42.mp4"}
                )
            )
            router.get("https://relay.test/files/job-42.mp4").mock(return_value=httpx.Response(200, content=b"video"))

            await DeclarativeVideoBackend(
                api_key="sk-super-secret",
                base_url="https://relay.test",
                model="video-x",
                definition=definition,
                provider="custom-1",
            ).generate(_request(tmp_path))

        logged = "\n".join(
            record.getMessage() for record in caplog.records if record.name == "lib.custom_provider.declarative_backend"
        )
        assert "声明式视频请求" in logged
        assert "sk-super-secret" not in logged


class TestExtractDuration:
    """``extract_duration`` 直接决定计费时长：越界与不可解析一律回 ``None`` 走缺省计价。"""

    SPEC: ClassVar[dict[str, object]] = {"paths": ["$.usage.duration"], "accept": "scalar"}

    @pytest.mark.parametrize(
        "raw",
        [0, -3, MAX_BILLED_DURATION_SECONDS + 1, "not-a-number"],
        ids=["zero", "negative", "over-max", "non-numeric"],
    )
    def test_out_of_range_or_unparsable_values_fall_back_to_none(self, raw):
        assert extract_duration(self.SPEC, {"usage": {"duration": raw}}) is None

    def test_half_up_rounding_can_push_the_value_out_of_range(self):
        boundary = MAX_BILLED_DURATION_SECONDS + 0.5
        assert extract_duration(self.SPEC, {"usage": {"duration": boundary}}) is None

    def test_boundary_values_inside_the_range_are_kept(self):
        assert extract_duration(self.SPEC, {"usage": {"duration": 1}}) == 1
        assert extract_duration(self.SPEC, {"usage": {"duration": MAX_BILLED_DURATION_SECONDS}}) == (
            MAX_BILLED_DURATION_SECONDS
        )

    def test_jsonpath_evaluation_failure_reaches_the_shared_runtime_diagnostic(self):
        extract = {
            "usage": {
                "duration_seconds": {"paths": ["$[?@.a == 1e400]"], "accept": "scalar"},
            }
        }

        with pytest.raises(DeclarativeRuntimeError) as caught:
            extract_provider_state({"a": 1}, extract, status=ProviderJobStatus.SUCCEEDED)

        assert caught.value.code == "declarative_response_extract_failed"
        assert caught.value.params == {
            "detail": {
                "key": "val_ce_jsonpath_evaluation_failed",
                "params": {"path_expression": "$[?@.a == 1e400]"},
            }
        }
