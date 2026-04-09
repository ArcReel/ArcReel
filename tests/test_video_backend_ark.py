"""ArkVideoBackend 单元测试 — mock Ark SDK。"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.video_backends.ark import ArkVideoBackend
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)


@pytest.fixture
def mock_ark_client():
    client = MagicMock()
    client.content_generation = MagicMock()
    client.content_generation.tasks = MagicMock()
    return client


@pytest.fixture
def backend(mock_ark_client):
    with patch("lib.video_backends.ark.create_ark_client", return_value=mock_ark_client):
        b = ArkVideoBackend(
            api_key="test-ark-key",
        )
    b._client = mock_ark_client
    return b


def _mock_httpx_stream(data: bytes = b"fake-mp4-data"):
    """Create a patched httpx mock that supports async stream context manager."""
    patcher = patch("lib.video_backends.base.httpx")
    mock_httpx = patcher.start()

    mock_stream_response = MagicMock()
    mock_stream_response.raise_for_status = MagicMock()

    async def _aiter_bytes(chunk_size=65536):
        yield data

    mock_stream_response.aiter_bytes = _aiter_bytes
    mock_stream_response.__aenter__ = AsyncMock(return_value=mock_stream_response)
    mock_stream_response.__aexit__ = AsyncMock(return_value=None)

    mock_http_client = AsyncMock()
    mock_http_client.stream = MagicMock(return_value=mock_stream_response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)
    mock_httpx.AsyncClient.return_value = mock_http_client

    return patcher


class TestArkProperties:
    def test_name(self, backend):
        assert backend.name == "ark"

    def test_capabilities(self, backend):
        caps = backend.capabilities
        assert VideoCapability.TEXT_TO_VIDEO in caps
        assert VideoCapability.IMAGE_TO_VIDEO in caps
        assert VideoCapability.GENERATE_AUDIO in caps
        assert VideoCapability.SEED_CONTROL in caps
        assert VideoCapability.FLEX_TIER in caps
        assert VideoCapability.NEGATIVE_PROMPT not in caps


class TestArkGenerate:
    async def test_text_to_video(self, backend, tmp_path):
        """文生视频：无 start_image。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-20250101-test"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = 58944
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 246840
        backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="a flower field",
                output_path=output,
                duration_seconds=5,
            )
            result = await backend.generate(request)
        finally:
            patcher.stop()

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "ark"
        assert result.model == "doubao-seedance-1-5-pro-251215"
        assert result.seed == 58944
        assert result.usage_tokens == 246840
        assert result.task_id == "cgt-20250101-test"

    async def test_image_to_video(self, backend, tmp_path):
        """图生视频：有 start_image。"""
        output = tmp_path / "out.mp4"
        frame = tmp_path / "scene_E1S01.png"
        frame.write_bytes(b"fake-png")

        create_result = MagicMock()
        create_result.id = "cgt-i2v-test"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video2.mp4"
        get_result.seed = 12345
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 200000
        backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="girl opens eyes",
                output_path=output,
                start_image=frame,
                generate_audio=True,
            )
            result = await backend.generate(request)
        finally:
            patcher.stop()

        assert result.provider == "ark"
        create_call = backend._client.content_generation.tasks.create
        call_kwargs = create_call.call_args
        content_arg = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content")
        assert len(content_arg) == 2
        assert content_arg[1]["type"] == "image_url"
        assert content_arg[1]["image_url"]["url"].startswith("data:image/")

    async def test_failed_task_raises(self, backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-fail"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "failed"
        get_result.error = "content violation"
        backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(RuntimeError, match="Ark 视频生成失败"):
            await backend.generate(request)

    async def test_with_seed_and_flex(self, backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-flex"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = 42
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 100000
        backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output,
                seed=42,
                service_tier="flex",
            )
            await backend.generate(request)
        finally:
            patcher.stop()

        create_call = backend._client.content_generation.tasks.create
        call_kwargs = create_call.call_args
        assert call_kwargs.kwargs.get("seed") == 42 or call_kwargs[1].get("seed") == 42
        assert call_kwargs.kwargs.get("service_tier") == "flex" or call_kwargs[1].get("service_tier") == "flex"

    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="Ark API Key"):
                ArkVideoBackend(api_key=None)


class TestArkRetryBehavior:
    """测试任务创建与轮询的重试分离行为。"""

    async def test_poll_transient_error_retries_without_recreating_task(self, backend, tmp_path):
        """轮询阶段瞬态错误应重试轮询，而不是重新创建任务。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-retry-test"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_success = MagicMock()
        get_success.status = "succeeded"
        get_success.content = MagicMock()
        get_success.content.video_url = "https://cdn.example.com/video.mp4"
        get_success.seed = None
        get_success.usage = None

        # 第一次轮询抛 ConnectionError，第二次成功
        backend._client.content_generation.tasks.get = MagicMock(
            side_effect=[ConnectionError("connection reset"), get_success]
        )

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(prompt="test", output_path=output)
            with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
                result = await backend.generate(request)
        finally:
            patcher.stop()

        assert result.task_id == "cgt-retry-test"
        # 关键断言：任务只创建了一次
        assert backend._client.content_generation.tasks.create.call_count == 1
        # 轮询调用了两次（一次失败 + 一次成功）
        assert backend._client.content_generation.tasks.get.call_count == 2

    async def test_create_retries_on_transient_error(self, backend, tmp_path):
        """任务创建阶段的瞬态错误应由 @with_retry_async 重试。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-create-retry"
        # 第一次创建抛 ConnectionError，第二次成功
        backend._client.content_generation.tasks.create = MagicMock(
            side_effect=[ConnectionError("connection reset"), create_result]
        )

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = None
        get_result.usage = None
        backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(prompt="test", output_path=output)
            with (
                patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock),
                patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
            ):
                result = await backend.generate(request)
        finally:
            patcher.stop()

        assert result.task_id == "cgt-create-retry"
        # 创建调用了两次（一次失败 + 一次成功）
        assert backend._client.content_generation.tasks.create.call_count == 2

    async def test_poll_non_retryable_error_propagates(self, backend, tmp_path):
        """轮询阶段不可重试的错误应直接抛出。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-no-retry"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        backend._client.content_generation.tasks.get = MagicMock(side_effect=ValueError("invalid response"))

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(ValueError, match="invalid response"):
            with patch("lib.video_backends.base.asyncio.sleep", new_callable=AsyncMock):
                await backend.generate(request)

        # 创建只调用一次，轮询只尝试一次就抛出
        assert backend._client.content_generation.tasks.create.call_count == 1
        assert backend._client.content_generation.tasks.get.call_count == 1


class TestArkModelCapabilities:
    """测试不同模型的能力映射。"""

    def test_seedance_2_no_flex_tier(self):
        with patch("lib.video_backends.ark.create_ark_client", return_value=MagicMock()):
            b = ArkVideoBackend(api_key="test", model="doubao-seedance-2-0-260128")
        caps = b.capabilities
        assert VideoCapability.FLEX_TIER not in caps
        assert VideoCapability.VIDEO_EXTEND not in caps

    def test_seedance_2_fast_no_flex_tier(self):
        with patch("lib.video_backends.ark.create_ark_client", return_value=MagicMock()):
            b = ArkVideoBackend(api_key="test", model="doubao-seedance-2-0-fast-260128")
        caps = b.capabilities
        assert VideoCapability.FLEX_TIER not in caps
        assert VideoCapability.VIDEO_EXTEND not in caps

    def test_seedance_1_5_has_flex_tier(self):
        with patch("lib.video_backends.ark.create_ark_client", return_value=MagicMock()):
            b = ArkVideoBackend(api_key="test", model="doubao-seedance-1-5-pro-251215")
        caps = b.capabilities
        assert VideoCapability.FLEX_TIER in caps
        assert VideoCapability.VIDEO_EXTEND not in caps

    def test_unknown_model_gets_default_capabilities(self):
        with patch("lib.video_backends.ark.create_ark_client", return_value=MagicMock()):
            b = ArkVideoBackend(api_key="test", model="some-future-model")
        caps = b.capabilities
        assert VideoCapability.FLEX_TIER in caps
