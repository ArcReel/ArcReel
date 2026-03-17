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
    with patch("lib.video_backends.seedance.Ark", return_value=mock_ark_client):
        b = SeedanceVideoBackend(
            api_key="test-ark-key",
            file_service_base_url="https://example.com",
        )
    b._client = mock_ark_client
    return b


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

        # Mock create -> task_id
        create_result = MagicMock()
        create_result.id = "cgt-20250101-test"
        backend._client.content_generation.tasks.create = MagicMock(
            return_value=create_result
        )

        # Mock get -> succeeded immediately
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

        # Mock video download
        with patch("lib.video_backends.seedance.httpx") as mock_httpx:
            mock_http_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.content = b"fake-mp4-data"
            mock_response.raise_for_status = MagicMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=None)
            mock_httpx.AsyncClient.return_value = mock_http_client

            request = VideoGenerationRequest(
                prompt="a flower field",
                output_path=output,
                duration_seconds=5,
            )

            result = await backend.generate(request)

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "seedance"
        assert result.model == "doubao-seedance-1-5-pro-251215"
        assert result.seed == 58944
        assert result.usage_tokens == 246840
        assert result.task_id == "cgt-20250101-test"

    async def test_image_to_video(self, backend, tmp_path):
        """图生视频：有 start_image。"""
        output = tmp_path / "out.mp4"
        frame = tmp_path / "frame.png"
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

        with patch("lib.video_backends.seedance.httpx") as mock_httpx:
            mock_http_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.content = b"fake-mp4-data"
            mock_response.raise_for_status = MagicMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=None)
            mock_httpx.AsyncClient.return_value = mock_http_client

            request = VideoGenerationRequest(
                prompt="girl opens eyes",
                output_path=output,
                start_image=frame,
                generate_audio=True,
            )

            result = await backend.generate(request)

        assert result.provider == "seedance"
        # Verify create was called with image_url content
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

        with patch("lib.video_backends.seedance.httpx") as mock_httpx:
            mock_http_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.content = b"fake-mp4-data"
            mock_response.raise_for_status = MagicMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=None)
            mock_httpx.AsyncClient.return_value = mock_http_client

            request = VideoGenerationRequest(
                prompt="test",
                output_path=output,
                seed=42,
                service_tier="flex",
            )

            result = await backend.generate(request)

        # Verify seed and service_tier were passed
        create_call = backend._client.content_generation.tasks.create
        call_kwargs = create_call.call_args
        assert call_kwargs.kwargs.get("seed") == 42 or call_kwargs[1].get("seed") == 42
        assert (
            call_kwargs.kwargs.get("service_tier") == "flex"
            or call_kwargs[1].get("service_tier") == "flex"
        )

    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("lib.video_backends.seedance.Ark"):
                with pytest.raises(ValueError, match="ARK_API_KEY"):
                    SeedanceVideoBackend(api_key=None)

    def test_missing_file_service_url_raises(self, backend):
        backend._file_service_base_url = ""
        with pytest.raises(ValueError, match="FILE_SERVICE_BASE_URL"):
            backend._get_image_url(Path("/tmp/test.png"))
