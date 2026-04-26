"""OpenAIVideoBackend 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import InternalServerError

from lib.providers import PROVIDER_OPENAI
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
)


def _make_mock_video(status="completed", seconds="8", video_id="vid_123"):
    """构造 mock Video 响应。"""
    video = MagicMock()
    video.id = video_id
    video.status = status
    video.seconds = seconds
    video.error = None
    return video


def _make_mock_content(data: bytes = b"fake-video-data"):
    """构造 mock download_content 响应。"""
    content = MagicMock()
    content.content = data
    return content


def _stub_client_completed(client: AsyncMock, *, seconds="8", video_id="vid_123", data=b"fake-video-data"):
    """常用 stub：create→queued、retrieve→completed、download_content→data。"""
    client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued", seconds=seconds, video_id=video_id))
    client.videos.retrieve = AsyncMock(
        return_value=_make_mock_video(status="completed", seconds=seconds, video_id=video_id)
    )
    client.videos.download_content = AsyncMock(return_value=_make_mock_content(data))


class TestOpenAIVideoBackend:
    def test_name_and_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "sora-2"

    def test_custom_model(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2-pro")
            assert backend.model == "sora-2-pro"

    def test_capabilities(self):
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
            assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
            assert VideoCapability.GENERATE_AUDIO not in backend.capabilities
            assert VideoCapability.NEGATIVE_PROMPT not in backend.capabilities
            assert VideoCapability.SEED_CONTROL not in backend.capabilities

    async def test_text_to_video(self, tmp_path: Path):
        video_data = b"mp4-video-content"
        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds="8", data=video_data)

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="A cat walking in the park",
                output_path=output_path,
                aspect_ratio="9:16",
                resolution="720p",
                duration_seconds=8,
            )
            result = await backend.generate(request)

        assert result.provider == PROVIDER_OPENAI
        assert result.model == "sora-2"
        assert result.duration_seconds == 8
        assert result.video_path == output_path
        assert result.task_id == "vid_123"
        assert output_path.read_bytes() == video_data

        call_kwargs = mock_client.videos.create.call_args[1]
        assert call_kwargs["prompt"] == "A cat walking in the park"
        assert call_kwargs["model"] == "sora-2"
        assert call_kwargs["seconds"] == "8"
        assert call_kwargs["size"] == "720x1280"  # 720p 9:16
        assert "input_reference" not in call_kwargs

    async def test_image_to_video(self, tmp_path: Path):
        start_image = tmp_path / "start.png"
        start_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds="4")

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="Animate this",
                output_path=output_path,
                start_image=start_image,
                duration_seconds=4,
            )
            result = await backend.generate(request)

        assert result.duration_seconds == 4
        call_kwargs = mock_client.videos.create.call_args[1]
        ref = call_kwargs["input_reference"]
        assert isinstance(ref, tuple)
        assert ref[0] == "start.png"
        assert isinstance(ref[1], bytes)
        assert ref[2] == "image/png"

    async def test_failed_video_raises(self, tmp_path: Path):
        error = MagicMock()
        error.message = "Content policy violation"
        failed_video = _make_mock_video(status="failed")
        failed_video.error = error

        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = AsyncMock(return_value=failed_video)
        mock_client.videos.download_content = AsyncMock()

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="Bad content",
                output_path=output_path,
            )
            with pytest.raises(RuntimeError, match="Sora 视频生成失败"):
                await backend.generate(request)

        # 失败应该在轮询阶段抛出，不会进入下载
        mock_client.videos.download_content.assert_not_called()

    async def test_duration_mapping(self, tmp_path: Path):
        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds="4")

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")

            for seconds, expected in [(3, "4"), (4, "4"), (5, "8"), (8, "8"), (10, "12"), (15, "12")]:
                output_path = tmp_path / f"output_{seconds}.mp4"
                request = VideoGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    duration_seconds=seconds,
                )
                await backend.generate(request)
                call_kwargs = mock_client.videos.create.call_args[1]
                assert call_kwargs["seconds"] == expected, f"duration={seconds}"

    async def test_video_seconds_none_fallback(self, tmp_path: Path):
        """当 API 返回 video.seconds=None 时，应回退到请求的 duration。"""
        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds=None)

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=5,
            )
            result = await backend.generate(request)

        # 请求 5 秒 → _map_duration → "8"，回退应返回 8
        assert result.duration_seconds == 8

    async def test_size_mapping(self, tmp_path: Path):
        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds="4")

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")

            for aspect, expected_size in [("9:16", "720x1280"), ("16:9", "1280x720")]:
                output_path = tmp_path / f"output_{aspect.replace(':', '_')}.mp4"
                request = VideoGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    aspect_ratio=aspect,
                    resolution="720p",
                )
                await backend.generate(request)
                call_kwargs = mock_client.videos.create.call_args[1]
                assert call_kwargs["size"] == expected_size, f"aspect={aspect}"

    async def test_content_download_retry_does_not_regenerate(self, tmp_path: Path):
        """内容下载 502 失败后应单独重试下载，而非重新创建任务。"""
        error = InternalServerError(
            message="Failed to resolve Vertex video URL",
            response=MagicMock(status_code=502, headers={}),
            body=None,
        )
        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = AsyncMock(return_value=_make_mock_video(status="completed", seconds="8"))
        mock_client.videos.download_content = AsyncMock(side_effect=[error, error, _make_mock_content(b"video-data")])

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=8,
            )
            result = await backend.generate(request)

        assert result.video_path == output_path
        assert output_path.read_bytes() == b"video-data"
        # create 只调用 1 次，不因下载失败重新创建任务
        assert mock_client.videos.create.call_count == 1
        # download_content 调用 3 次（2 次失败 + 1 次成功）
        assert mock_client.videos.download_content.call_count == 3

    async def test_content_download_all_retries_exhausted(self, tmp_path: Path):
        """内容下载全部重试耗尽后应抛出异常，且不重新生成视频。"""
        error = InternalServerError(
            message="Failed to resolve Vertex video URL",
            response=MagicMock(status_code=502, headers={}),
            body=None,
        )
        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = AsyncMock(return_value=_make_mock_video(status="completed", seconds="8"))
        mock_client.videos.download_content = AsyncMock(side_effect=error)

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=8,
            )
            with pytest.raises(InternalServerError):
                await backend.generate(request)

        # 即使下载重试耗尽，也只创建 1 次任务
        assert mock_client.videos.create.call_count == 1

    async def test_content_download_non_retryable_error_fails_immediately(self, tmp_path: Path):
        """不可重试的下载错误（如 4xx）应立即失败，不浪费退避时间。"""
        from openai import AuthenticationError

        error = AuthenticationError(
            message="Invalid API key",
            response=MagicMock(status_code=401, headers={}),
            body=None,
        )
        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = AsyncMock(return_value=_make_mock_video(status="completed", seconds="8"))
        mock_client.videos.download_content = AsyncMock(side_effect=error)
        mock_sleep = AsyncMock()

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.retry.asyncio.sleep", mock_sleep),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=8,
            )
            with pytest.raises(AuthenticationError):
                await backend.generate(request)

        # 不可重试错误：只调用 1 次下载，无 retry sleep
        assert mock_client.videos.download_content.call_count == 1
        mock_sleep.assert_not_called()

    async def test_polls_until_completed_for_nonstandard_status(self, tmp_path: Path):
        """OpenAI 兼容网关返回非标 status（如 NOT_START / running）时，必须继续轮询直到 completed。

        回归 issue：grok-imagine 走自定义供应商时，retrieve 返回 NOT_START，但 SDK 内置 poll
        仅识别 4 种标准状态，会提前退出导致下载未就绪任务（400 Task is not completed yet）。
        """
        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        # 模拟非标状态序列：NOT_START → running → in_progress → completed
        mock_client.videos.retrieve = AsyncMock(
            side_effect=[
                _make_mock_video(status="NOT_START"),
                _make_mock_video(status="running"),
                _make_mock_video(status="in_progress"),
                _make_mock_video(status="completed", seconds="8"),
            ]
        )
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(b"v"))

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=8,
            )
            result = await backend.generate(request)

        # 必须轮询 4 次（3 次非完成 + 1 次完成）才得到结果
        assert mock_client.videos.retrieve.call_count == 4
        # 中间至少 sleep 了 3 次（每次轮询前都 sleep）
        assert mock_sleep.await_count >= 3
        # 下载只在完成后调用一次
        assert mock_client.videos.download_content.call_count == 1
        assert result.video_path == output_path

    async def test_polls_failed_status_raises_without_download(self, tmp_path: Path):
        """轮询期间出现 status='failed' 应直接抛错，不进入下载。"""
        err = MagicMock()
        err.message = "moderation rejected"
        failed = _make_mock_video(status="failed")
        failed.error = err

        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = AsyncMock(
            side_effect=[
                _make_mock_video(status="in_progress"),
                failed,
            ]
        )
        mock_client.videos.download_content = AsyncMock()

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="bad",
                output_path=output_path,
                duration_seconds=8,
            )
            with pytest.raises(RuntimeError, match="Sora 视频生成失败"):
                await backend.generate(request)

        mock_client.videos.download_content.assert_not_called()
