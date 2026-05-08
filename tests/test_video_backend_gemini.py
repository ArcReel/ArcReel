"""GeminiVideoBackend 单元测试 — mock genai SDK。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)


@pytest.fixture
def mock_rate_limiter():
    rl = MagicMock()
    rl.acquire = MagicMock()
    rl.acquire_async = AsyncMock()
    return rl


@pytest.fixture
def backend(mock_rate_limiter):
    """创建 aistudio 模式的 GeminiVideoBackend（mock genai SDK）。"""
    with patch("google.genai"), patch("google.genai.types"):
        from lib.video_backends.gemini import GeminiVideoBackend

        b = GeminiVideoBackend(
            backend_type="aistudio",
            api_key="test-key",
            rate_limiter=mock_rate_limiter,
        )
        b._client = MagicMock()
        b._client.aio = MagicMock()
        yield b


# ── 属性测试 ──────────────────────────────────────────────


class TestGeminiVideoBackendProperties:
    def test_name(self, backend):
        assert backend.name == "gemini-aistudio"

    def test_capabilities_aistudio(self, backend):
        caps = backend.capabilities
        assert VideoCapability.TEXT_TO_VIDEO in caps
        assert VideoCapability.IMAGE_TO_VIDEO in caps
        assert VideoCapability.NEGATIVE_PROMPT in caps
        assert VideoCapability.VIDEO_EXTEND in caps
        assert VideoCapability.GENERATE_AUDIO not in caps

    def test_capabilities_vertex(self, mock_rate_limiter, tmp_path):
        # 准备 mock vertex 凭证文件
        creds_file = tmp_path / "vertex_credentials.json"
        creds_file.write_text('{"project_id": "test-project"}')

        with (
            patch("google.genai"),
            patch("google.genai.types"),
            patch(
                "lib.video_backends.gemini.resolve_vertex_credentials_path",
                return_value=creds_file,
            ),
            patch("google.oauth2.service_account.Credentials.from_service_account_file"),
        ):
            from lib.video_backends.gemini import GeminiVideoBackend

            b = GeminiVideoBackend(
                backend_type="vertex",
                rate_limiter=mock_rate_limiter,
            )
            assert VideoCapability.GENERATE_AUDIO in b.capabilities


# ── 生成测试 ──────────────────────────────────────────────


def _make_done_operation(video_uri="gs://bucket/video.mp4"):
    """构造一个已完成的 operation mock。"""
    mock_video = MagicMock()
    mock_video.uri = video_uri
    mock_video.video_bytes = b"fake-video-bytes"

    mock_generated = MagicMock()
    mock_generated.video = mock_video

    mock_response = MagicMock()
    mock_response.generated_videos = [mock_generated]

    mock_op = MagicMock()
    mock_op.done = True
    mock_op.response = mock_response
    mock_op.error = None
    return mock_op


class TestGeminiVideoBackendGenerate:
    async def test_generate_text_to_video(self, backend, tmp_path):
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="a cat walking",
            output_path=output,
            duration_seconds=8,
        )

        result = await backend.generate(request)

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "gemini"
        assert result.video_uri == "gs://bucket/video.mp4"
        assert result.video_path == output
        assert result.duration_seconds == 8

        # 确认调用了 API
        backend._client.aio.models.generate_videos.assert_awaited_once()

    async def test_generate_image_to_video(self, backend, tmp_path):
        output = tmp_path / "out.mp4"
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"fake-png-data")

        mock_op = _make_done_operation(video_uri=None)
        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="cat moves forward",
            output_path=output,
            start_image=frame,
        )

        result = await backend.generate(request)

        assert result.provider == "gemini"
        assert result.video_path == output

    async def test_generate_polls_until_done(self, backend, tmp_path):
        """测试轮询逻辑：先返回未完成，再返回已完成。"""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        done_op = _make_done_operation()

        backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        backend._client.aio.operations.get = AsyncMock(return_value=done_op)

        request = VideoGenerationRequest(
            prompt="a sunset",
            output_path=output,
        )

        # patch asyncio.sleep 以避免实际等待
        with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
            result = await backend.generate(request)

        assert result.provider == "gemini"

    async def test_generate_empty_result_raises(self, backend, tmp_path):
        """API 返回空结果时应抛出 RuntimeError。"""
        output = tmp_path / "out.mp4"

        mock_op = MagicMock()
        mock_op.done = True
        mock_op.response = MagicMock()
        mock_op.response.generated_videos = []
        mock_op.error = None

        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        with pytest.raises(RuntimeError, match="API 返回空结果"):
            await backend.generate(request)

    async def test_generate_error_in_operation(self, backend, tmp_path):
        """operation 包含 error 时应抛出 RuntimeError。"""
        output = tmp_path / "out.mp4"

        mock_op = MagicMock()
        mock_op.done = True
        mock_op.response = None
        mock_op.error = "Something went wrong"

        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        with pytest.raises(RuntimeError, match="视频生成失败"):
            await backend.generate(request)

    async def test_rate_limiter_called(self, backend, mock_rate_limiter, tmp_path):
        """确认 generate 会调用限流器。"""
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        await backend.generate(request)
        mock_rate_limiter.acquire_async.assert_called_once_with(backend._video_model)

    async def test_no_negative_prompt_in_config(self, backend, tmp_path):
        """negative_prompt 改走 prompt 文本通道，GenerateVideosConfig 不再带该字段。"""
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        await backend.generate(request)

        config_call = backend._types.GenerateVideosConfig.call_args
        assert "negative_prompt" not in config_call.kwargs


class TestGeminiRetryBehavior:
    """测试任务创建与轮询的重试分离行为。"""

    async def test_poll_transient_error_retries_without_recreating_task(self, backend, tmp_path):
        """轮询阶段瞬态错误应重试轮询，而不是重新创建任务。"""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        done_op = _make_done_operation()

        backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        # 第一次轮询抛 ConnectionError，第二次返回完成
        backend._client.aio.operations.get = AsyncMock(side_effect=[ConnectionError("connection reset"), done_op])

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
            result = await backend.generate(request)

        assert result.provider == "gemini"
        # 关键断言：任务只创建了一次
        backend._client.aio.models.generate_videos.assert_awaited_once()
        # 轮询调用了两次（一次失败 + 一次成功）
        assert backend._client.aio.operations.get.await_count == 2

    async def test_create_retries_on_transient_error(self, backend, tmp_path):
        """任务创建阶段的瞬态错误应由 @with_retry_async 重试。"""
        output = tmp_path / "out.mp4"

        done_op = _make_done_operation()
        # 第一次创建抛 ConnectionError，第二次成功
        backend._client.aio.models.generate_videos = AsyncMock(
            side_effect=[ConnectionError("connection reset"), done_op]
        )

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with (
            patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
            patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await backend.generate(request)

        assert result.provider == "gemini"
        # 创建调用了两次（一次失败 + 一次成功）
        assert backend._client.aio.models.generate_videos.await_count == 2

    async def test_poll_non_retryable_error_propagates(self, backend, tmp_path):
        """轮询阶段不可重试的错误应直接抛出。"""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        backend._client.aio.operations.get = AsyncMock(side_effect=ValueError("invalid response"))

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(ValueError, match="invalid response"):
            with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
                await backend.generate(request)

        # 创建只调用一次
        backend._client.aio.models.generate_videos.assert_awaited_once()
        # 轮询只尝试一次就抛出
        assert backend._client.aio.operations.get.await_count == 1


# ── _prepare_image_param 测试 ─────────────────────────────


class TestPrepareImageParam:
    def test_none_returns_none(self, backend):
        assert backend._prepare_image_param(None) is None

    def test_path_reads_file(self, backend, tmp_path):
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic

        result = backend._prepare_image_param(img_file)
        assert result is not None

    def test_pil_image(self, backend):
        from PIL import Image as PILImage

        img = PILImage.new("RGB", (10, 10), color="red")
        result = backend._prepare_image_param(img)
        assert result is not None


# ── _download_video 测试 ──────────────────────────────────


class TestDownloadVideo:
    def test_aistudio_download(self, backend, tmp_path):
        output = tmp_path / "video.mp4"
        mock_ref = MagicMock()

        backend._download_video(mock_ref, output)

        backend._client.files.download.assert_called_once_with(file=mock_ref)
        mock_ref.save.assert_called_once_with(str(output))

    def test_vertex_download_from_bytes(self, backend, tmp_path):
        backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"

        mock_ref = MagicMock()
        mock_ref.video_bytes = b"video-data"

        backend._download_video(mock_ref, output)

        assert output.read_bytes() == b"video-data"

    def test_vertex_no_data_raises(self, backend, tmp_path):
        backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"

        mock_ref = MagicMock(spec=[])  # no attributes

        with pytest.raises(RuntimeError, match="无法获取视频数据"):
            backend._download_video(mock_ref, output)
