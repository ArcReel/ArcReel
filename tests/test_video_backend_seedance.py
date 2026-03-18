"""SeedanceVideoBackend 单元测试 — mock Ark SDK。"""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.video_backends.seedance import SeedanceVideoBackend


@pytest.fixture
def mock_ark_client():
    client = MagicMock()
    client.content_generation = MagicMock()
    client.content_generation.tasks = MagicMock()
    return client


@pytest.fixture
def backend(mock_ark_client):
    with patch("volcenginesdkarkruntime.Ark", return_value=mock_ark_client):
        b = SeedanceVideoBackend(
            api_key="test-ark-key",
            file_service_base_url="https://example.com",
        )
    b._client = mock_ark_client
    return b


def _mock_httpx_stream(data: bytes = b"fake-mp4-data"):
    """Create a patched httpx mock that supports async stream context manager."""
    patcher = patch("lib.video_backends.seedance.httpx")
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


class TestSeedanceProperties:
    def test_name(self, backend):
        assert backend.name == "seedance"

    def test_capabilities(self, backend):
        caps = backend.capabilities
        assert VideoCapability.TEXT_TO_VIDEO in caps
        assert VideoCapability.IMAGE_TO_VIDEO in caps
        assert VideoCapability.GENERATE_AUDIO in caps
        assert VideoCapability.SEED_CONTROL in caps
        assert VideoCapability.FLEX_TIER in caps
        assert VideoCapability.NEGATIVE_PROMPT not in caps


class TestSeedanceGenerate:
    async def test_text_to_video(self, backend, tmp_path):
        """文生视频：无 start_image。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-20250101-test"
        backend._client.content_generation.tasks.create = MagicMock(
            return_value=create_result
        )

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = 58944
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 246840
        backend._client.content_generation.tasks.get = MagicMock(
            return_value=get_result
        )

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
        assert result.provider == "seedance"
        assert result.model == "doubao-seedance-1-5-pro-251215"
        assert result.seed == 58944
        assert result.usage_tokens == 246840
        assert result.task_id == "cgt-20250101-test"

    async def test_image_to_video(self, backend, tmp_path):
        """图生视频：有 start_image。"""
        output = tmp_path / "out.mp4"
        # 模拟真实项目路径结构: .../projects/<name>/storyboards/<file>.png
        project_dir = tmp_path / "projects" / "demo" / "storyboards"
        project_dir.mkdir(parents=True)
        frame = project_dir / "scene_E1S01.png"
        frame.write_bytes(b"fake-png")

        create_result = MagicMock()
        create_result.id = "cgt-i2v-test"
        backend._client.content_generation.tasks.create = MagicMock(
            return_value=create_result
        )

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video2.mp4"
        get_result.seed = 12345
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 200000
        backend._client.content_generation.tasks.get = MagicMock(
            return_value=get_result
        )

        patcher = _mock_httpx_stream()
        try:
            request = VideoGenerationRequest(
                prompt="girl opens eyes",
                output_path=output,
                start_image=frame,
                generate_audio=True,
                project_name="demo",
            )
            result = await backend.generate(request)
        finally:
            patcher.stop()

        assert result.provider == "seedance"
        create_call = backend._client.content_generation.tasks.create
        call_kwargs = create_call.call_args
        content_arg = call_kwargs.kwargs.get("content") or call_kwargs[1].get(
            "content"
        )
        assert len(content_arg) == 2
        assert content_arg[1]["type"] == "image_url"

    async def test_failed_task_raises(self, backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-fail"
        backend._client.content_generation.tasks.create = MagicMock(
            return_value=create_result
        )

        get_result = MagicMock()
        get_result.status = "failed"
        get_result.error = "content violation"
        backend._client.content_generation.tasks.get = MagicMock(
            return_value=get_result
        )

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(RuntimeError, match="Seedance 视频生成失败"):
            await backend.generate(request)

    async def test_with_seed_and_flex(self, backend, tmp_path):
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-flex"
        backend._client.content_generation.tasks.create = MagicMock(
            return_value=create_result
        )

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/video.mp4"
        get_result.seed = 42
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 100000
        backend._client.content_generation.tasks.get = MagicMock(
            return_value=get_result
        )

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
        assert (
            call_kwargs.kwargs.get("service_tier") == "flex"
            or call_kwargs[1].get("service_tier") == "flex"
        )

    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("volcenginesdkarkruntime.Ark"):
                with pytest.raises(ValueError, match="ARK_API_KEY"):
                    SeedanceVideoBackend(api_key=None)

    def test_missing_file_service_url_raises(self, backend):
        backend._file_service_base_url = ""
        with pytest.raises(ValueError, match="FILE_SERVICE_BASE_URL"):
            backend._get_image_url(Path("/tmp/test.png"), "demo")
