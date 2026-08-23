"""NewAPIVideoBackend 单元测试（respx 捕获出站请求，假表压缩轮询等待）。"""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
import respx

from lib.providers import PROVIDER_NEWAPI
from lib.video_backends.base import AmbiguousSubmitError, ResumeExpiredError, VideoGenerationRequest
from lib.video_backends.newapi import NewAPIVideoBackend
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, only_request, request_json

_BASE_URL = "https://x/v1"
_GENERATIONS_PATH = "/video/generations"


class _NewAPIRoutes(NamedTuple):
    """NewAPI 的三条出站流量：建任务、任务轮询、成片下载。"""

    submit: respx.Route
    poll: respx.Route
    download: respx.Route


@contextmanager
def _newapi(*, base_url: str = _BASE_URL) -> Iterator[_NewAPIRoutes]:
    with capture_http() as router:
        yield _NewAPIRoutes(
            submit=router.post(f"{base_url}{_GENERATIONS_PATH}"),
            poll=router.get(url__regex=rf"^{re.escape(base_url + _GENERATIONS_PATH)}/[^/]+$"),
            download=router.get(url__regex=r"^https://cdn"),
        )


def _json(body: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=body)


def _queued(task_id: str = "t-proxy") -> httpx.Response:
    return _json({"task_id": task_id, "status": "queued"})


def _done(task_id: str = "t-proxy", url: str = "https://cdn/v.mp4", **extra) -> httpx.Response:
    body = {"task_id": task_id, "status": "completed", "url": url, "metadata": {"duration": 5}}
    body.update(extra)
    return _json(body)


def _request(tmp_path: Path, **overrides) -> VideoGenerationRequest:
    params: dict = {
        "prompt": "p",
        "output_path": tmp_path / "o.mp4",
        "aspect_ratio": "9:16",
        "duration_seconds": 5,
    }
    params.update(overrides)
    return VideoGenerationRequest(**params)


def _backend(base_url: str = _BASE_URL, model: str = "m") -> NewAPIVideoBackend:
    return NewAPIVideoBackend(api_key="k", base_url=base_url, model=model)


class TestNewAPIVideoBackend:
    def test_name_and_model(self):
        backend = NewAPIVideoBackend(api_key="sk-test", base_url="https://example.com/v1", model="kling-v1")
        assert backend.name == PROVIDER_NEWAPI
        assert backend.model == "kling-v1"

    def test_capabilities(self):
        assert _backend().video_capabilities.max_reference_images == 0

    async def test_text_to_video_happy_path(self, tmp_path: Path):
        with _newapi(base_url="https://example.com/v1") as routes:
            routes.submit.mock(return_value=_queued("task-42"))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "task-42",
                        "status": "completed",
                        "url": "https://cdn.example.com/out.mp4",
                        "format": "mp4",
                        "metadata": {"duration": 5, "fps": 24, "width": 720, "height": 1280, "seed": 0},
                    }
                )
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"mp4-bytes"))

            backend = NewAPIVideoBackend(api_key="sk-test", base_url="https://example.com/v1", model="kling-v1")
            result = await backend.generate(
                _request(
                    tmp_path,
                    prompt="A cat running",
                    output_path=tmp_path / "out.mp4",
                    resolution="720p",
                )
            )

        assert result.video_path == tmp_path / "out.mp4"
        assert result.video_path.read_bytes() == b"mp4-bytes"
        assert result.provider == PROVIDER_NEWAPI
        assert result.model == "kling-v1"
        assert result.duration_seconds == 5
        assert result.task_id == "task-42"

        submitted = only_request(routes.submit)
        assert submitted.url.path.endswith(_GENERATIONS_PATH)
        body = request_json(submitted)
        assert body["model"] == "kling-v1"
        assert body["prompt"] == "A cat running"
        assert body["width"] == 720
        assert body["height"] == 1280
        assert body["duration"] == 5
        assert body["n"] == 1
        assert "image" not in body
        assert submitted.headers["Authorization"] == "Bearer sk-test"

        # 下载走 base.download_video，URL 正确且不带鉴权头
        downloaded = only_request(routes.download)
        assert str(downloaded.url) == "https://cdn.example.com/out.mp4"
        assert "Authorization" not in downloaded.headers

    async def test_image_to_video_encodes_base64(self, tmp_path: Path):
        img_bytes = b"\x89PNG\r\nfake"
        img_path = tmp_path / "start.png"
        img_path.write_bytes(img_bytes)

        with _newapi() as routes:
            routes.submit.mock(return_value=_queued("t1"))
            routes.poll.mock(return_value=_done("t1", "https://cdn/x.mp4"))

            await _backend(model="kling-v1").generate(_request(tmp_path, start_image=img_path, resolution="720p"))

            sent_image = request_json(only_request(routes.submit))["image"]

        assert sent_image == "data:image/png;base64," + base64.b64encode(img_bytes).decode()

    async def test_start_image_missing_is_ignored(self, tmp_path: Path, caplog):
        """start_image 文件不存在时应 warning 并走纯文生路径。"""
        with (
            _newapi() as routes,
            caplog.at_level("WARNING", logger="lib.video_backends.newapi"),
        ):
            routes.submit.mock(return_value=_queued("t-missing"))
            routes.poll.mock(return_value=_done("t-missing"))

            await _backend().generate(
                _request(tmp_path, start_image=tmp_path / "does_not_exist.png", resolution="720p")
            )

            body = request_json(only_request(routes.submit))

        assert "image" not in body
        assert any("start_image 文件不存在" in rec.message for rec in caplog.records)

    async def test_failed_status_raises(self, tmp_path: Path):
        with _newapi() as routes:
            routes.submit.mock(return_value=_queued("t2"))
            routes.poll.mock(
                return_value=_json(
                    {"task_id": "t2", "status": "failed", "error": {"code": 500, "message": "upstream down"}}
                )
            )

            with pytest.raises(RuntimeError, match="upstream down"):
                await _backend().generate(_request(tmp_path, resolution="720p"))

            assert routes.download.call_count == 0

    async def test_polls_through_in_progress(self, tmp_path: Path):
        in_progress = _json({"task_id": "t3", "status": "in_progress"})

        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_queued("t3"))
            routes.poll.mock(side_effect=[in_progress, in_progress, _done("t3")])
            routes.download.mock(return_value=httpx.Response(200, content=b"v"))

            result = await _backend().generate(_request(tmp_path, resolution="720p"))

            assert routes.poll.call_count == 3
            assert routes.download.call_count == 1

        assert result.task_id == "t3"

    async def test_polling_timeout_raises(self, tmp_path: Path):
        """轮询超时应抛 TimeoutError 且不触发下载。"""
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_queued("t-timeout"))
            routes.poll.mock(return_value=_json({"task_id": "t-timeout", "status": "in_progress"}))

            with pytest.raises(TimeoutError, match="NewAPI"):
                await _backend().generate(_request(tmp_path, resolution="720p"))

            assert routes.poll.call_count > 1
            assert routes.download.call_count == 0

    async def test_zero_duration_from_api_is_preserved(self, tmp_path: Path):
        """回归: API 返回 duration=0 时不应被 falsy 回退到请求值（is None 判空）。"""
        with _newapi() as routes:
            routes.submit.mock(return_value=_queued("t-zero"))
            routes.poll.mock(return_value=_done("t-zero", metadata={"duration": 0}))

            result = await _backend().generate(_request(tmp_path, resolution="720p"))

        # API 明确返回 0，应如实保留，不是回退到 request.duration_seconds=5
        assert result.duration_seconds == 0

    async def test_create_retries_on_5xx(self, tmp_path: Path):
        """5xx HTTPStatusError 应通过 should_retry_submit 的 status_code 闸门重试。"""
        busy = httpx.Response(503, text="upstream busy")

        with _newapi() as routes, bounded_poll_clock():
            # 前两次创建任务 503，第三次成功
            routes.submit.mock(side_effect=[busy, busy, _queued("t-retry")])
            routes.poll.mock(return_value=_done("t-retry"))

            result = await _backend().generate(_request(tmp_path, resolution="720p"))

            assert routes.submit.call_count == 3

        assert result.task_id == "t-retry"

    async def test_create_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        """创建任务遇确定性 4xx（400）应一次失败，不重试。"""
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"error": "bad request"}, status_code=400))

            with pytest.raises(httpx.HTTPStatusError):
                await _backend().generate(_request(tmp_path))

            assert routes.submit.call_count == 1, "确定性 4xx 不该被 retry"
            assert routes.poll.call_count == 0, "4xx 应在创建阶段失败，不该轮询"

    async def test_create_read_timeout_fails_fast_with_manual_retry_hint(self, tmp_path: Path):
        """create 阶段 ReadTimeout（请求可能已送达）→ 不重试、单次失败、错误信息含手动重试提示。"""
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(side_effect=httpx.ReadTimeout("read timed out"))

            with pytest.raises(AmbiguousSubmitError, match="手动重试"):
                await _backend().generate(_request(tmp_path))

            assert routes.submit.call_count == 1, "歧义态不该被 retry"
            assert routes.poll.call_count == 0

    async def test_create_connect_error_retries(self, tmp_path: Path):
        """create 阶段 ConnectError（请求确定未送达）→ 重试，第三次成功。"""
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(
                side_effect=[httpx.ConnectError("refused"), httpx.ConnectError("refused"), _queued("t-conn")]
            )
            routes.poll.mock(return_value=_done("t-conn"))

            result = await _backend().generate(_request(tmp_path))

            assert routes.submit.call_count == 3, "ConnectError 请求确定未送达，应重试"

        assert result.task_id == "t-conn"

    async def test_poll_read_timeout_retries(self, tmp_path: Path):
        """poll 阶段 ReadTimeout（幂等 GET）→ 重试，不回归。"""
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_queued("t-pr"))
            routes.poll.mock(side_effect=[httpx.ReadTimeout("read timed out"), _done("t-pr")])

            result = await _backend().generate(_request(tmp_path))

            assert routes.poll.call_count == 2, "poll 网络超时应重试"

        assert result.task_id == "t-pr"

    async def test_poll_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        """轮询遇确定性 4xx（401，如 token 失效）应一次失败，不重试到 max_wait 超时。"""
        with _newapi() as routes:
            routes.submit.mock(return_value=_queued("t-401"))
            routes.poll.mock(return_value=_json({"error": "unauthorized"}, status_code=401))

            with pytest.raises(httpx.HTTPStatusError):
                await _backend().generate(_request(tmp_path))

            assert routes.poll.call_count == 1, "轮询确定性 4xx 应一击失败，不重试到超时"

    async def test_resume_video_polls_existing_job(self, tmp_path: Path):
        """resume_video 仅 poll + 下载,不 POST create (ADR 0007)。"""
        with _newapi() as routes:
            routes.poll.mock(return_value=_done("task-resume", "https://cdn/resumed.mp4"))
            routes.download.mock(return_value=httpx.Response(200, content=b"resumed"))

            result = await _backend().resume_video("task-resume", _request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert routes.submit.call_count == 0  # resume 不 POST create
            assert only_request(routes.poll).url.path.endswith("/task-resume")

        assert result.task_id == "task-resume"
        assert (tmp_path / "out.mp4").read_bytes() == b"resumed"

    async def test_poll_recognizes_expired_status(self, tmp_path: Path):
        """poll 返回 status='expired' → 抛 ResumeExpiredError。"""
        with _newapi() as routes:
            routes.poll.mock(return_value=_json({"task_id": "task-x", "status": "expired"}))

            with pytest.raises(ResumeExpiredError) as ei:
                await _backend().resume_video("task-x", _request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert ei.value.job_id == "task-x"
            assert ei.value.provider == PROVIDER_NEWAPI

    async def test_resume_404_raises_resume_expired_without_retry(self, tmp_path: Path):
        """resume 路径下 GET 返 404 应立即转 ResumeExpiredError，不被 retryable 框架重试到超时。"""
        with _newapi() as routes:
            routes.poll.mock(return_value=_json({"error": "task not found"}, status_code=404))

            with pytest.raises(ResumeExpiredError) as ei:
                await _backend().resume_video("task-404", _request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert ei.value.job_id == "task-404"
            assert ei.value.provider == PROVIDER_NEWAPI
            # 不应被 retry 框架重试多次（应仅 1 次 GET 调用立即抛错）
            assert routes.poll.call_count == 1, "404 应一击转 ResumeExpiredError，不该被 retry"

    async def test_generate_expired_status_raises_runtime_error_not_resume_expired(self, tmp_path: Path):
        """generate 路径下 status='expired' 抛 RuntimeError，不带 [resume_expired] 语义。"""
        with _newapi() as routes:
            routes.submit.mock(return_value=_json({"task_id": "task-new"}))
            routes.poll.mock(return_value=_json({"task_id": "task-new", "status": "expired"}))

            with pytest.raises(RuntimeError) as ei:
                await _backend().generate(_request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert "expired" in str(ei.value).lower()
            assert not isinstance(ei.value, ResumeExpiredError), "generate 路径不应抛 ResumeExpiredError"


class TestProxyStatusSynonyms:
    """NewAPI 端点跨厂商分发，状态串随底层厂商透传，终态判定不能只认单一字面量。"""

    @pytest.mark.parametrize("proxy_status", ["succeeded", "success", "SUCCEEDED", "  succeeded  "])
    async def test_success_synonyms_finish_polling_and_download(self, tmp_path: Path, proxy_status: str):
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"task_id": "t-proxy"}))
            routes.poll.mock(
                return_value=_json({"task_id": "t-proxy", "status": proxy_status, "url": "https://cdn/v.mp4"})
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"ok"))

            result = await _backend().generate(_request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert routes.poll.call_count == 1
            assert routes.download.call_count == 1

        assert result.video_path == tmp_path / "out.mp4"
        assert result.video_path.read_bytes() == b"ok"

    @pytest.mark.parametrize("proxy_status", ["error", "fail", "FAILED", "canceled"])
    async def test_failure_synonyms_raise_immediately(self, tmp_path: Path, proxy_status: str):
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"task_id": "t-proxy"}))
            routes.poll.mock(
                return_value=_json(
                    {"task_id": "t-proxy", "status": proxy_status, "error": {"message": "upstream down"}}
                )
            )

            with pytest.raises(RuntimeError, match="upstream down"):
                await _backend().generate(_request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert routes.poll.call_count == 1
            assert routes.download.call_count == 0

    async def test_uppercase_expired_still_splits_generate_and_resume(self, tmp_path: Path):
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"task_id": "t-proxy"}))
            routes.poll.mock(return_value=_json({"task_id": "t-proxy", "status": "EXPIRED"}))

            request = _request(tmp_path, output_path=tmp_path / "out.mp4")

            with pytest.raises(RuntimeError) as ei:
                await _backend().generate(request)
            assert not isinstance(ei.value, ResumeExpiredError)

            with pytest.raises(ResumeExpiredError) as resume_ei:
                await _backend().resume_video("t-proxy", request)
            assert resume_ei.value.job_id == "t-proxy"


class TestWrappedResponseShape:
    """回包为 ``{"code": ..., "data": {...}}`` 包装体时同样能取到状态与视频地址；
    扁平形状保持最高优先级、行为不变。"""

    async def test_wrapped_status_and_result_url(self, tmp_path: Path):
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"task_id": "t-proxy"}))
            routes.poll.mock(
                return_value=_json(
                    {
                        "code": "success",
                        "data": {
                            "task_id": "t-proxy",
                            "status": "SUCCESS",
                            "result_url": "https://cdn/wrapped.mp4",
                        },
                    }
                )
            )
            routes.download.mock(return_value=httpx.Response(200, content=b"wrapped"))

            result = await _backend().generate(_request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert str(only_request(routes.download).url) == "https://cdn/wrapped.mp4"

        assert result.duration_seconds == 5
        assert (tmp_path / "out.mp4").read_bytes() == b"wrapped"

    async def test_flat_url_wins_over_wrapped(self, tmp_path: Path):
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"task_id": "t-proxy"}))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "t-proxy",
                        "status": "completed",
                        "url": "https://cdn/flat.mp4",
                        "data": {"result_url": "https://cdn/wrapped.mp4"},
                    }
                )
            )

            await _backend().generate(_request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert str(only_request(routes.download).url) == "https://cdn/flat.mp4"

    async def test_flat_result_url_wins_over_wrapped(self, tmp_path: Path):
        """混合形状下扁平字段整体优先于包装体，不因字段名不同而让位。"""
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"task_id": "t-proxy"}))
            routes.poll.mock(
                return_value=_json(
                    {
                        "task_id": "t-proxy",
                        "status": "completed",
                        "result_url": "https://cdn/flat.mp4",
                        "data": {"url": "https://cdn/wrapped.mp4"},
                    }
                )
            )

            await _backend().generate(_request(tmp_path, output_path=tmp_path / "out.mp4"))

            assert str(only_request(routes.download).url) == "https://cdn/flat.mp4"

    async def test_wrapped_metadata_feeds_duration_and_seed(self, tmp_path: Path):
        """包装体里的 metadata 与状态、视频地址同源，同样要取到——实际时长是计费依据。"""
        with _newapi() as routes, bounded_poll_clock():
            routes.submit.mock(return_value=_json({"task_id": "t-proxy"}))
            routes.poll.mock(
                return_value=_json(
                    {
                        "code": "success",
                        "data": {
                            "task_id": "t-proxy",
                            "status": "SUCCESS",
                            "result_url": "https://cdn/wrapped.mp4",
                            "metadata": {"duration": 8, "seed": 4242},
                        },
                    }
                )
            )

            result = await _backend().generate(_request(tmp_path, output_path=tmp_path / "out.mp4"))

        assert result.duration_seconds == 8
        assert result.seed == 4242
