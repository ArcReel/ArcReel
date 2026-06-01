"""NewAPIVideoBackend 单元测试（mock httpx）。"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.providers import PROVIDER_NEWAPI
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
)


def _make_response(status_code: int, json_body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _make_http_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    """构造 httpx.HTTPStatusError，用于模拟 raise_for_status() 抛出的 5xx。"""
    request = httpx.Request("POST", "https://x/v1/video/generations")
    response = httpx.Response(status_code, request=request, text=message)
    return httpx.HTTPStatusError(f"Server error '{status_code}'", request=request, response=response)


def _fake_download_factory(payload: bytes = b"mp4-bytes"):
    """返回一个模拟 `download_video` 的异步函数，写入 payload 到 output_path。"""

    async def _fake(url: str, output_path: Path, *, timeout: int = 120) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)

    return _fake


class TestNewAPIVideoBackend:
    def test_name_and_model(self):
        from lib.video_backends.newapi import NewAPIVideoBackend

        backend = NewAPIVideoBackend(api_key="sk-test", base_url="https://example.com/v1", model="kling-v1")
        assert backend.name == PROVIDER_NEWAPI
        assert backend.model == "kling-v1"

    def test_capabilities(self):
        from lib.video_backends.newapi import NewAPIVideoBackend

        backend = NewAPIVideoBackend(api_key="sk-test", base_url="https://x/v1", model="m")
        assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
        assert backend.video_capabilities.reference_images is False
        assert backend.video_capabilities.max_reference_images == 0

    async def test_text_to_video_happy_path(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "task-42", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "task-42",
                "status": "completed",
                "url": "https://cdn.example.com/out.mp4",
                "format": "mp4",
                "metadata": {"duration": 5, "fps": 24, "width": 720, "height": 1280, "seed": 0},
            },
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_download = AsyncMock(side_effect=_fake_download_factory(b"mp4-bytes"))

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.newapi.download_video", fake_download),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="sk-test", base_url="https://example.com/v1", model="kling-v1")
            request = VideoGenerationRequest(
                prompt="A cat running",
                output_path=tmp_path / "out.mp4",
                aspect_ratio="9:16",
                resolution="720p",
                duration_seconds=5,
            )
            result = await backend.generate(request)

        assert result.video_path == tmp_path / "out.mp4"
        assert result.video_path.read_bytes() == b"mp4-bytes"
        assert result.provider == PROVIDER_NEWAPI
        assert result.model == "kling-v1"
        assert result.duration_seconds == 5
        assert result.task_id == "task-42"

        post_call = mock_client.post.call_args
        assert post_call.args[0].endswith("/video/generations")
        assert post_call.kwargs["json"]["model"] == "kling-v1"
        assert post_call.kwargs["json"]["prompt"] == "A cat running"
        assert post_call.kwargs["json"]["width"] == 720
        assert post_call.kwargs["json"]["height"] == 1280
        assert post_call.kwargs["json"]["duration"] == 5
        assert post_call.kwargs["json"]["n"] == 1
        assert "image" not in post_call.kwargs["json"]
        assert post_call.kwargs["headers"]["Authorization"] == "Bearer sk-test"

        # 下载走 base.download_video，URL 正确且不带 auth（base.download_video 不接 headers 参数）
        fake_download.assert_called_once()
        download_call = fake_download.call_args
        assert download_call.args[0] == "https://cdn.example.com/out.mp4"
        assert download_call.args[1] == tmp_path / "out.mp4"

    async def test_image_to_video_encodes_base64(self, tmp_path: Path):
        img_bytes = b"\x89PNG\r\nfake"
        img_path = tmp_path / "start.png"
        img_path.write_bytes(img_bytes)

        create_resp = _make_response(200, {"task_id": "t1", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "t1",
                "status": "completed",
                "url": "https://cdn/x.mp4",
                "metadata": {"duration": 5},
            },
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.newapi.download_video", fake_download),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="kling-v1")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    start_image=img_path,
                    resolution="720p",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        sent_image = mock_client.post.call_args.kwargs["json"]["image"]
        expected = "data:image/png;base64," + base64.b64encode(img_bytes).decode()
        assert sent_image == expected

    async def test_start_image_missing_is_ignored(self, tmp_path: Path, caplog):
        """start_image 文件不存在时应 warning 并走纯文生路径。"""
        create_resp = _make_response(200, {"task_id": "t-missing", "status": "queued"})
        poll_resp = _make_response(
            200,
            {"task_id": "t-missing", "status": "completed", "url": "https://cdn/v.mp4", "metadata": {"duration": 5}},
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.newapi.download_video", fake_download),
            caplog.at_level("WARNING", logger="lib.video_backends.newapi"),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    start_image=tmp_path / "does_not_exist.png",
                    resolution="720p",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        assert "image" not in mock_client.post.call_args.kwargs["json"]
        assert any("start_image 文件不存在" in rec.message for rec in caplog.records)

    async def test_failed_status_raises(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "t2", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "t2",
                "status": "failed",
                "error": {"code": 500, "message": "upstream down"},
            },
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_download = AsyncMock()

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.newapi.download_video", fake_download),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            with pytest.raises(RuntimeError, match="upstream down"):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        resolution="720p",
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    )
                )

        fake_download.assert_not_called()

    async def test_polls_through_in_progress(self, tmp_path: Path):
        create_resp = _make_response(200, {"task_id": "t3", "status": "queued"})
        in_progress = _make_response(200, {"task_id": "t3", "status": "in_progress"})
        completed = _make_response(
            200,
            {
                "task_id": "t3",
                "status": "completed",
                "url": "https://cdn/v.mp4",
                "metadata": {"duration": 5},
            },
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(side_effect=[in_progress, in_progress, completed])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.newapi.download_video", fake_download),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    resolution="720p",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        assert result.task_id == "t3"
        # 3 次 poll（in_progress → in_progress → completed），下载不经过 mock_client
        assert mock_client.get.call_count == 3
        fake_download.assert_called_once()

    async def test_polling_timeout_raises(self, tmp_path: Path):
        """轮询超时应抛 TimeoutError 且不触发下载。"""
        create_resp = _make_response(200, {"task_id": "t-timeout", "status": "queued"})
        in_progress = _make_response(200, {"task_id": "t-timeout", "status": "in_progress"})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=in_progress)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_download = AsyncMock()

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.newapi._MIN_POLL_TIMEOUT_SECONDS", 0.01),
            patch("lib.video_backends.newapi._POLL_TIMEOUT_PER_SECOND", 0),
            patch("lib.video_backends.newapi.download_video", fake_download),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            with pytest.raises(TimeoutError, match="NewAPI"):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        resolution="720p",
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    )
                )

        fake_download.assert_not_called()

    async def test_zero_duration_from_api_is_preserved(self, tmp_path: Path):
        """回归: API 返回 duration=0 时不应被 falsy 回退到请求值（is None 判空）。"""
        create_resp = _make_response(200, {"task_id": "t-zero", "status": "queued"})
        poll_resp = _make_response(
            200,
            {
                "task_id": "t-zero",
                "status": "completed",
                "url": "https://cdn/v.mp4",
                "metadata": {"duration": 0},
            },
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.newapi.download_video", fake_download),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    resolution="720p",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        # API 明确返回 0，应如实保留，不是回退到 request.duration_seconds=5
        assert result.duration_seconds == 0

    async def test_create_retries_on_5xx(self, tmp_path: Path):
        """5xx HTTPStatusError 应通过 should_retry_submit 的 status_code 闸门重试。"""
        failing_resp = MagicMock()
        failing_resp.status_code = 503
        failing_resp.raise_for_status = MagicMock(side_effect=_make_http_error(503, "upstream busy"))

        create_resp = _make_response(200, {"task_id": "t-retry", "status": "queued"})
        poll_resp = _make_response(
            200,
            {"task_id": "t-retry", "status": "completed", "url": "https://cdn/v.mp4", "metadata": {"duration": 5}},
        )

        mock_client = AsyncMock()
        # 前两次创建任务 503，第三次成功
        mock_client.post = AsyncMock(side_effect=[failing_resp, failing_resp, create_resp])
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_download = AsyncMock(side_effect=_fake_download_factory(b"v"))

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
            # 压缩重试退避时间到 0，避免测试变慢
            patch("lib.video_backends.newapi.DEFAULT_BACKOFF_SECONDS", (0, 0, 0)),
            patch("lib.retry._compute_wait", lambda attempt, backoff: 0.0),
            patch("lib.video_backends.newapi.download_video", fake_download),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.mp4",
                    resolution="720p",
                    aspect_ratio="9:16",
                    duration_seconds=5,
                )
            )

        assert result.task_id == "t-retry"
        assert mock_client.post.call_count == 3

    async def test_create_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        """创建任务遇确定性 4xx（400）应一次失败，不重试。"""
        bad_resp = _make_response(400, {"error": "bad request"})
        bad_resp.raise_for_status = MagicMock(side_effect=_make_http_error(400, "bad request"))

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=bad_resp)
        mock_client.get = AsyncMock(side_effect=AssertionError("4xx 应在创建阶段失败，不该轮询"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.retry._compute_wait", lambda attempt, backoff: 0.0),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            with pytest.raises(httpx.HTTPStatusError):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                    )
                )

        assert mock_client.post.call_count == 1, "确定性 4xx 不该被 retry"

    async def test_poll_non_retryable_4xx_fails_fast(self, tmp_path: Path):
        """轮询遇确定性 4xx（401，如 token 失效）应一次失败，不重试到 max_wait 超时。"""
        create_resp = _make_response(200, {"task_id": "t-401", "status": "queued"})
        unauthorized_resp = _make_response(401, {"error": "unauthorized"})
        unauthorized_resp.raise_for_status = MagicMock(side_effect=_make_http_error(401, "unauthorized"))

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=unauthorized_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            with pytest.raises(httpx.HTTPStatusError):
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "o.mp4", aspect_ratio="9:16", duration_seconds=5
                    )
                )

        assert mock_client.get.call_count == 1, "轮询确定性 4xx 应一击失败，不重试到超时"

    async def test_resume_video_polls_existing_job(self, tmp_path: Path):
        """resume_video 仅 poll + 下载,不 POST create (ADR 0007)。"""
        poll_resp = _make_response(
            200,
            {
                "task_id": "task-resume",
                "status": "completed",
                "url": "https://cdn/resumed.mp4",
                "metadata": {"duration": 5},
            },
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=AssertionError("resume 不应 POST create"))
        mock_client.get = AsyncMock(return_value=poll_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        fake_download = AsyncMock(side_effect=_fake_download_factory(b"resumed"))

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
            patch("lib.video_backends.newapi.download_video", fake_download),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            result = await backend.resume_video(
                "task-resume",
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "out.mp4", aspect_ratio="9:16", duration_seconds=5
                ),
            )

        mock_client.post.assert_not_called()
        # 应该 GET 到 .../video/generations/task-resume
        assert mock_client.get.call_args.args[0].endswith("/task-resume")
        assert result.task_id == "task-resume"
        assert (tmp_path / "out.mp4").read_bytes() == b"resumed"

    async def test_poll_recognizes_expired_status(self, tmp_path: Path):
        """fix #647 #5：poll 返回 status='expired' → 抛 ResumeExpiredError。"""
        from lib.video_backends.base import ResumeExpiredError

        expired_resp = _make_response(200, {"task_id": "task-x", "status": "expired"})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=expired_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            with pytest.raises(ResumeExpiredError) as ei:
                await backend.resume_video(
                    "task-x",
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "out.mp4", aspect_ratio="9:16", duration_seconds=5
                    ),
                )
            assert ei.value.job_id == "task-x"
            assert ei.value.provider == PROVIDER_NEWAPI

    async def test_resume_404_raises_resume_expired_without_retry(self, tmp_path: Path):
        """resume 路径下 GET 返 404 应立即转 ResumeExpiredError，不被 retryable 框架重试到超时。"""
        from lib.video_backends.base import ResumeExpiredError

        # 构造 404 response 让 raise_for_status 真抛 HTTPStatusError（_make_response 默认 mock 空，需手工设）
        not_found_resp = _make_response(404, {"error": "task not found"})
        not_found_resp.raise_for_status = MagicMock(side_effect=_make_http_error(404, "task not found"))
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=not_found_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            with pytest.raises(ResumeExpiredError) as ei:
                await backend.resume_video(
                    "task-404",
                    VideoGenerationRequest(
                        prompt="p", output_path=tmp_path / "out.mp4", aspect_ratio="9:16", duration_seconds=5
                    ),
                )
            assert ei.value.job_id == "task-404"
            assert ei.value.provider == PROVIDER_NEWAPI
            # 不应被 retry 框架重试多次（应仅 1 次 GET 调用立即抛错）
            assert mock_client.get.call_count == 1, "404 应一击转 ResumeExpiredError，不该被 retry"

    async def test_generate_expired_status_raises_runtime_error_not_resume_expired(self, tmp_path: Path):
        """generate 路径下 status='expired' 抛 RuntimeError，不带 [resume_expired] 语义。"""
        from lib.video_backends.base import ResumeExpiredError

        create_resp = _make_response(200, {"task_id": "task-new"})
        expired_resp = _make_response(200, {"task_id": "task-new", "status": "expired"})
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=create_resp)
        mock_client.get = AsyncMock(return_value=expired_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("lib.video_backends.newapi._POLL_INTERVAL_SECONDS", 0.0),
        ):
            from lib.video_backends.newapi import NewAPIVideoBackend

            backend = NewAPIVideoBackend(api_key="k", base_url="https://x/v1", model="m")
            with pytest.raises(RuntimeError) as ei:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "out.mp4",
                        aspect_ratio="9:16",
                        duration_seconds=5,
                    ),
                )
            assert "expired" in str(ei.value).lower()
            assert not isinstance(ei.value, ResumeExpiredError), "generate 路径不应抛 ResumeExpiredError"
