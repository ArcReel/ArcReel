"""OpenAI 视频后端 resolution 参数映射测试。"""

from __future__ import annotations

import pytest

from lib.video_backends.openai import _resolve_size


class TestResolveSizeFunction:
    """直接测试 _resolve_size 辅助函数。"""

    def test_sora2_720p_9_16(self):
        assert _resolve_size("720p", "9:16") == "720x1280"

    def test_sora2_720p_16_9(self):
        assert _resolve_size("720p", "16:9") == "1280x720"

    def test_sora2pro_1080p_9_16(self):
        assert _resolve_size("1080p", "9:16") == "1080x1920"

    def test_sora2pro_1080p_16_9(self):
        assert _resolve_size("1080p", "16:9") == "1920x1080"

    def test_1024p_9_16(self):
        assert _resolve_size("1024p", "9:16") == "1024x1792"

    def test_1024p_16_9(self):
        assert _resolve_size("1024p", "16:9") == "1792x1024"

    def test_default_fallback_unknown_resolution(self):
        """未知 resolution 应回退到默认值 720x1280。"""
        assert _resolve_size("2160p", "9:16") == "720x1280"

    def test_default_fallback_unknown_aspect_ratio(self):
        """未知 aspect_ratio 应回退到默认值 720x1280。"""
        assert _resolve_size("720p", "4:3") == "720x1280"

    def test_default_fallback_both_unknown(self):
        """resolution 和 aspect_ratio 均未知时回退到默认值。"""
        assert _resolve_size("unknown", "unknown") == "720x1280"


class TestGenerateUsesResolution:
    """验证 generate() 实际传递给 API 的 size 参数会根据 resolution 变化。"""

    async def _run_generate(self, tmp_path, resolution, aspect_ratio, mock_client):
        from lib.video_backends.base import VideoGenerationRequest
        from lib.video_backends.openai import OpenAIVideoBackend

        backend = OpenAIVideoBackend(api_key="test-key")
        output_path = tmp_path / "output.mp4"
        request = VideoGenerationRequest(
            prompt="test",
            output_path=output_path,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration_seconds=4,
        )
        await backend.generate(request)
        return mock_client.videos.create_and_poll.call_args[1]["size"]

    async def test_generate_passes_1080p_9_16(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_video = MagicMock()
        mock_video.id = "vid_1"
        mock_video.status = "completed"
        mock_video.seconds = "4"
        mock_video.error = None

        mock_content = MagicMock()
        mock_content.content = b"data"

        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=mock_video)
        mock_client.videos.download_content = AsyncMock(return_value=mock_content)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            size = await self._run_generate(tmp_path, "1080p", "9:16", mock_client)

        assert size == "1080x1920"

    async def test_generate_passes_1080p_16_9(self, tmp_path):
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_video = MagicMock()
        mock_video.id = "vid_2"
        mock_video.status = "completed"
        mock_video.seconds = "4"
        mock_video.error = None

        mock_content = MagicMock()
        mock_content.content = b"data"

        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=mock_video)
        mock_client.videos.download_content = AsyncMock(return_value=mock_content)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            size = await self._run_generate(tmp_path, "1080p", "16:9", mock_client)

        assert size == "1920x1080"

    async def test_generate_default_fallback(self, tmp_path):
        """不支持的 resolution + aspect_ratio 组合应回退到 720x1280。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_video = MagicMock()
        mock_video.id = "vid_3"
        mock_video.status = "completed"
        mock_video.seconds = "4"
        mock_video.error = None

        mock_content = MagicMock()
        mock_content.content = b"data"

        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=mock_video)
        mock_client.videos.download_content = AsyncMock(return_value=mock_content)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            size = await self._run_generate(tmp_path, "4K", "1:1", mock_client)

        assert size == "720x1280"
