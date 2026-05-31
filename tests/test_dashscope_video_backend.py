"""DashScopeVideoBackend 单元测试（mock httpx，异步两步式）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from lib.providers import PROVIDER_DASHSCOPE
from lib.video_backends.base import (
    ResumeExpiredError,
    VideoCapability,
    VideoGenerationRequest,
)


def _resp(json_body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


def _http_error(status_code: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://x/api/v1/tasks/t")
    response = httpx.Response(status_code, request=request, text=message)
    return httpx.HTTPStatusError(f"error {status_code}", request=request, response=response)


def _submit(task_id: str = "t-1") -> dict:
    return {"output": {"task_id": task_id, "task_status": "PENDING"}}


def _succeeded(url: str = "https://x/o.mp4", duration: int = 5) -> dict:
    return {
        "output": {"task_status": "SUCCEEDED", "video_url": url},
        "usage": {"duration": duration, "input_video_duration": 0, "output_video_duration": duration},
    }


def _client(*, post=None, get=None) -> AsyncMock:
    c = AsyncMock()
    if post is not None:
        c.post = post
    if get is not None:
        c.get = get
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=None)
    return c


def _patches(client: AsyncMock, download: AsyncMock):
    return (
        patch("httpx.AsyncClient", return_value=client),
        patch("lib.video_backends.dashscope.download_video", download),
        patch("lib.video_backends.dashscope.DASHSCOPE_POLL_INTERVAL_SECONDS", 0.0),
    )


def _ref(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\nfake")
    return p


class TestCapabilities:
    def test_name_and_model(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
        assert b.name == PROVIDER_DASHSCOPE
        assert b.model == "happyhorse-1.0-i2v"

    def test_happyhorse_r2v_caps(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
        vc = b.video_capabilities
        assert vc.reference_images is True
        assert vc.max_reference_images == 9
        assert vc.first_frame is False

    def test_wan_r2v_caps(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
        vc = b.video_capabilities
        assert vc.max_reference_images == 5
        assert vc.first_frame is True

    def test_t2v_caps(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
        assert VideoCapability.TEXT_TO_VIDEO in b.capabilities
        assert b.video_capabilities.first_frame is False
        assert b.video_capabilities.reference_images is False

    def test_i2v_caps(self):
        from lib.video_backends.dashscope import DashScopeVideoBackend

        b = DashScopeVideoBackend(api_key="sk", model="wan2.7-i2v")
        assert VideoCapability.IMAGE_TO_VIDEO in b.capabilities
        assert b.video_capabilities.first_frame is True


class TestReferenceToVideo:
    async def test_r2v_happy_path(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("t-r2v")))
        get = AsyncMock(return_value=_resp(_succeeded(duration=8)))
        client = _client(post=post, get=get)
        download = AsyncMock()
        ref1, ref2 = _ref(tmp_path, "a.png"), _ref(tmp_path, "b.png")
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            result = await b.generate(
                VideoGenerationRequest(
                    prompt="[Image 1] dances",
                    output_path=tmp_path / "o.mp4",
                    reference_images=[ref1, ref2],
                    resolution="720p",
                    aspect_ratio="16:9",
                    duration_seconds=5,
                )
            )

        body = post.call_args.kwargs["json"]
        assert body["model"] == "happyhorse-1.0-r2v"
        media = body["input"]["media"]
        assert len(media) == 2
        assert all(m["type"] == "reference_image" for m in media)
        assert media[0]["url"].startswith("data:image/png;base64,")
        # resolution 大写、watermark 关、ratio 透传
        assert body["parameters"]["resolution"] == "720P"
        assert body["parameters"]["watermark"] is False
        assert body["parameters"]["ratio"] == "16:9"
        # submit 端点 + async 头
        assert post.call_args.args[0].endswith("/api/v1/services/aigc/video-generation/video-synthesis")
        assert post.call_args.kwargs["headers"]["X-DashScope-Async"] == "enable"
        # 计费时长取 usage.duration（非请求值 5）
        assert result.duration_seconds == 8
        assert result.provider == PROVIDER_DASHSCOPE
        assert result.task_id == "t-r2v"
        download.assert_called_once()

    async def test_r2v_ref_limit_happyhorse_9(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        refs = [_ref(tmp_path, f"r{i}.png") for i in range(12)]
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", reference_images=refs, resolution="720p"
                )
            )
        assert len(post.call_args.kwargs["json"]["input"]["media"]) == 9

    async def test_r2v_ref_limit_wan_5(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        refs = [_ref(tmp_path, f"r{i}.png") for i in range(8)]
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", reference_images=refs, resolution="1080p"
                )
            )
        assert len(post.call_args.kwargs["json"]["input"]["media"]) == 5

    async def test_r2v_all_refs_missing_raises(self, tmp_path: Path):
        # r2v 参考图全部缺失/不可读（含空串）须 fail-loud，不静默退化为无参考生成
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(RuntimeError, match="至少一张参考图"):
                await b.generate(
                    VideoGenerationRequest(
                        prompt="p",
                        output_path=tmp_path / "o.mp4",
                        reference_images=[str(tmp_path / "nope.png"), ""],
                        resolution="720p",
                    )
                )
        # 提交请求根本不应发出
        post.assert_not_called()

    async def test_r2v_no_refs_provided_raises(self, tmp_path: Path):
        # r2v 模型但调用方完全未提供参考图（None/空），也须 fail-loud，不提交无 media 的 r2v 请求
        post = AsyncMock(return_value=_resp(_submit()))
        client = _client(post=post, get=AsyncMock())
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-r2v")
            with pytest.raises(RuntimeError, match="至少一张参考图"):
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
        post.assert_not_called()


class TestFirstFrameAndTextOnly:
    async def test_i2v_first_frame(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        start = _ref(tmp_path, "start.png")
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-i2v")
            await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", start_image=start, resolution="720p")
            )
        media = post.call_args.kwargs["json"]["input"]["media"]
        assert media == [{"type": "first_frame", "url": media[0]["url"]}]
        assert media[0]["url"].startswith("data:image/png;base64,")

    async def test_t2v_no_media(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-t2v")
            await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="1080p"))
        assert "media" not in post.call_args.kwargs["json"]["input"]
        assert post.call_args.kwargs["json"]["parameters"]["resolution"] == "1080P"


class TestPollingAndFailures:
    async def test_polls_through_running(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("t3")))
        get = AsyncMock(
            side_effect=[
                _resp({"output": {"task_status": "RUNNING"}}),
                _resp({"output": {"task_status": "RUNNING"}}),
                _resp(_succeeded()),
            ]
        )
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            result = await b.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p")
            )
        assert get.call_count == 3
        assert result.task_id == "t3"

    async def test_failed_raises(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit()))
        get = AsyncMock(return_value=_resp({"output": {"task_status": "FAILED", "code": "X", "message": "boom"}}))
        client = _client(post=post, get=get)
        download = AsyncMock()
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-t2v")
            with pytest.raises(RuntimeError, match="boom"):
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
        download.assert_not_called()

    async def test_generate_unknown_raises_runtime(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("t-new")))
        get = AsyncMock(return_value=_resp({"output": {"task_status": "UNKNOWN"}}))
        client = _client(post=post, get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-t2v")
            with pytest.raises(RuntimeError) as ei:
                await b.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"))
            assert "expired" in str(ei.value).lower()
            assert not isinstance(ei.value, ResumeExpiredError)


class TestResume:
    async def test_resume_polls_without_post(self, tmp_path: Path):
        post = AsyncMock(side_effect=AssertionError("resume 不应 POST"))
        get = AsyncMock(return_value=_resp(_succeeded(url="https://x/r.mp4")))
        client = _client(post=post, get=get)
        download = AsyncMock()
        p1, p2, p3 = _patches(client, download)
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            result = await b.resume_video(
                "t-resume",
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"),
            )
        post.assert_not_called()
        assert get.call_args.args[0].endswith("/tasks/t-resume")
        assert result.task_id == "t-resume"

    async def test_resume_unknown_raises_resume_expired(self, tmp_path: Path):
        get = AsyncMock(return_value=_resp({"output": {"task_status": "UNKNOWN"}}))
        client = _client(get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(ResumeExpiredError) as ei:
                await b.resume_video(
                    "t-exp",
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"),
                )
            assert ei.value.job_id == "t-exp"
            assert ei.value.provider == PROVIDER_DASHSCOPE

    async def test_resume_404_raises_without_retry(self, tmp_path: Path):
        not_found = _resp({"error": "nope"}, status_code=404)
        not_found.raise_for_status = MagicMock(side_effect=_http_error(404, "not found"))
        get = AsyncMock(return_value=not_found)
        client = _client(get=get)
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3:
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="wan2.7-r2v")
            with pytest.raises(ResumeExpiredError):
                await b.resume_video(
                    "t-404",
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", resolution="720p"),
                )
            assert get.call_count == 1


class TestPersist:
    async def test_persist_called_with_task_id(self, tmp_path: Path):
        post = AsyncMock(return_value=_resp(_submit("job-9")))
        get = AsyncMock(return_value=_resp(_succeeded()))
        client = _client(post=post, get=get)
        persist = AsyncMock()
        p1, p2, p3 = _patches(client, AsyncMock())
        with p1, p2, p3, patch("lib.video_backends.dashscope.persist_provider_job_id", persist):
            from lib.video_backends.dashscope import DashScopeVideoBackend

            b = DashScopeVideoBackend(api_key="sk", model="happyhorse-1.0-i2v")
            await b.generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "o.mp4", resolution="720p", task_id="db-task-1"
                )
            )
        persist.assert_called_once()
        assert persist.call_args.args[0] == "db-task-1"
        assert persist.call_args.args[1] == "job-9"
        assert persist.call_args.kwargs["provider"] == PROVIDER_DASHSCOPE
