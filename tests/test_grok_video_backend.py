"""GrokVideoBackend 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.providers import PROVIDER_GROK
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
)


@pytest.fixture
def output_path(tmp_path: Path) -> Path:
    return tmp_path / "output.mp4"


class TestGrokVideoBackend:
    @patch("lib.video_backends.grok.create_grok_client")
    def test_name_and_model(self, mock_create):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert backend.name == PROVIDER_GROK
        assert backend.model == "grok-imagine-video"

    @patch("lib.video_backends.grok.create_grok_client")
    def test_capabilities(self, mock_create):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
        assert VideoCapability.GENERATE_AUDIO not in backend.capabilities
        assert VideoCapability.NEGATIVE_PROMPT not in backend.capabilities
        assert VideoCapability.SEED_CONTROL not in backend.capabilities

    @patch("lib.video_backends.grok.create_grok_client")
    def test_custom_model(self, mock_create):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-2")
        assert backend.model == "grok-imagine-video-2"

    def test_missing_api_key_raises(self):
        from lib.video_backends.grok import GrokVideoBackend

        with pytest.raises(ValueError, match="xAI API Key"):
            GrokVideoBackend(api_key=None)

    async def test_text_to_video(self, output_path: Path):
        from lib.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 5

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            mock_http_response = AsyncMock()
            mock_http_response.status_code = 200
            mock_http_response.raise_for_status = MagicMock()
            mock_http_response.aiter_bytes = lambda chunk_size=None: _async_iter([b"fake-video-data"])

            mock_http_client = AsyncMock()
            mock_http_client.stream = _async_context_manager(mock_http_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=False)

            with patch("lib.video_backends.base.httpx.AsyncClient", return_value=mock_http_client):
                request = VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=output_path,
                    aspect_ratio="16:9",
                    duration_seconds=5,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.provider == PROVIDER_GROK
            assert result.model == "grok-imagine-video"
            assert result.duration_seconds == 5
            assert result.video_path == output_path

            mock_video.generate.assert_awaited_once()
            call_kwargs = mock_video.generate.call_args[1]
            assert call_kwargs["prompt"] == "A cat walking"
            assert call_kwargs["model"] == "grok-imagine-video"
            assert call_kwargs["duration"] == 5
            assert call_kwargs["aspect_ratio"] == "16:9"
            assert call_kwargs["resolution"] == "720p"
            assert "image_url" not in call_kwargs

    async def test_image_to_video(self, output_path: Path, tmp_path: Path):
        from lib.video_backends.grok import GrokVideoBackend

        image_path = tmp_path / "start.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 8

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            mock_http_response = AsyncMock()
            mock_http_response.status_code = 200
            mock_http_response.raise_for_status = MagicMock()
            mock_http_response.aiter_bytes = lambda chunk_size=None: _async_iter([b"fake-video-data"])

            mock_http_client = AsyncMock()
            mock_http_client.stream = _async_context_manager(mock_http_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=False)

            with patch("lib.video_backends.base.httpx.AsyncClient", return_value=mock_http_client):
                request = VideoGenerationRequest(
                    prompt="Animate this scene",
                    output_path=output_path,
                    start_image=image_path,
                    duration_seconds=8,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.duration_seconds == 8

            call_kwargs = mock_video.generate.call_args[1]
            assert "image_url" in call_kwargs
            assert call_kwargs["image_url"].startswith("data:image/png;base64,")

    @pytest.mark.parametrize(
        ("raw_duration", "expected"),
        [
            (15, 15),  # 整数直接收窄
            ("15.0", 15),  # 浮点字符串先经 float 解析
            (7.8, 8),  # 浮点 half-up 取整，与 dashscope 计费口径一致
            (4.4, 4),  # half-up：不足半秒舍去
            (0, 5),  # 零值回落请求时长
            (-10, 5),  # 负值回落请求时长
            (0.3, 5),  # 取整到 0 同样回落，保持结果恒为正
            ("unknown", 5),  # 不可解析回落请求时长
            (float("inf"), 5),  # 溢出回落请求时长
            (1e100, 5),  # 超出合理上限回落请求时长，防 DB Integer 列溢出
            (86400.9, 5),  # 上限基于取整前原始值：小数已超 24h 不得因取整落回上限内
            (None, 5),  # 缺失回落请求时长
        ],
    )
    async def test_duration_narrowed_to_int_with_fallback(self, output_path: Path, raw_duration, expected):
        """SDK 回报的 duration 未类型化：可解析数值收窄为 int 作为实际计费时长，否则回落请求时长。"""
        from lib.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = raw_duration

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            mock_http_response = AsyncMock()
            mock_http_response.status_code = 200
            mock_http_response.raise_for_status = MagicMock()
            mock_http_response.aiter_bytes = lambda chunk_size=None: _async_iter([b"fake-video-data"])

            mock_http_client = AsyncMock()
            mock_http_client.stream = _async_context_manager(mock_http_response)
            mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
            mock_http_client.__aexit__ = AsyncMock(return_value=False)

            with patch("lib.video_backends.base.httpx.AsyncClient", return_value=mock_http_client):
                request = VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=output_path,
                    duration_seconds=5,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.duration_seconds == expected


async def _async_iter(items):
    for item in items:
        yield item


def _async_context_manager(mock_response):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _stream(*args, **kwargs):
        yield mock_response

    return _stream
