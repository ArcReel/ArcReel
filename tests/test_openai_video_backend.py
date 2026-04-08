"""Unit tests for OpenAIVideoBackend."""

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
    """Build a mock Video response."""
    video = MagicMock()
    video.id = video_id
    video.status = status
    video.seconds = seconds
    video.error = None
    return video


def _make_mock_content(data: bytes = b"fake-video-data"):
    """Build a mock download_content response."""
    content = MagicMock()
    content.content = data
    return content


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
        mock_client.videos.create_and_poll = AsyncMock(return_value=_make_mock_video(seconds="8"))
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(video_data))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
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

        call_kwargs = mock_client.videos.create_and_poll.call_args[1]
        assert call_kwargs["prompt"] == "A cat walking in the park"
        assert call_kwargs["model"] == "sora-2"
        assert call_kwargs["seconds"] == "8"
        assert call_kwargs["size"] == "720x1280"  # 720p 9:16
        assert "input_reference" not in call_kwargs

    async def test_image_to_video(self, tmp_path: Path):
        start_image = tmp_path / "start.png"
        start_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=_make_mock_video(seconds="4"))
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(b"video"))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
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
        call_kwargs = mock_client.videos.create_and_poll.call_args[1]
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
        mock_client.videos.create_and_poll = AsyncMock(return_value=failed_video)

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="Bad content",
                output_path=output_path,
            )
            with pytest.raises(RuntimeError, match="Sora video generation failed"):
                await backend.generate(request)

    async def test_duration_mapping(self, tmp_path: Path):
        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=_make_mock_video(seconds="4"))
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(b"v"))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
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
                call_kwargs = mock_client.videos.create_and_poll.call_args[1]
                assert call_kwargs["seconds"] == expected, f"duration={seconds}"

    async def test_video_seconds_none_fallback(self, tmp_path: Path):
        """When the API returns video.seconds=None, should fall back to the requested duration."""
        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=_make_mock_video(seconds=None))
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(b"v"))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
            from lib.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=5,
            )
            result = await backend.generate(request)

        # Request 5s → _map_duration → "8", fallback should return 8
        assert result.duration_seconds == 8

    async def test_size_mapping(self, tmp_path: Path):
        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=_make_mock_video(seconds="4"))
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(b"v"))

        with patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client):
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
                call_kwargs = mock_client.videos.create_and_poll.call_args[1]
                assert call_kwargs["size"] == expected_size, f"aspect={aspect}"

    async def test_content_download_retry_does_not_regenerate(self, tmp_path: Path):
        """After a 502 download failure, should retry the download alone, not re-invoke create_and_poll."""
        error = InternalServerError(
            message="Failed to resolve Vertex video URL",
            response=MagicMock(status_code=502, headers={}),
            body=None,
        )
        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=_make_mock_video(seconds="8"))
        mock_client.videos.download_content = AsyncMock(side_effect=[error, error, _make_mock_content(b"video-data")])

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
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
        # create_and_poll called only once, not re-invoked on download failure
        assert mock_client.videos.create_and_poll.call_count == 1
        # download_content called 3 times (2 failures + 1 success)
        assert mock_client.videos.download_content.call_count == 3

    async def test_content_download_all_retries_exhausted(self, tmp_path: Path):
        """Should raise an exception when all download retries are exhausted, without re-generating the video."""
        error = InternalServerError(
            message="Failed to resolve Vertex video URL",
            response=MagicMock(status_code=502, headers={}),
            body=None,
        )
        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=_make_mock_video(seconds="8"))
        mock_client.videos.download_content = AsyncMock(side_effect=error)

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
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

        # Even when download retries are exhausted, video is generated only once
        assert mock_client.videos.create_and_poll.call_count == 1

    async def test_content_download_non_retryable_error_fails_immediately(self, tmp_path: Path):
        """Non-retryable download errors (e.g. 4xx) should fail immediately without wasting backoff time."""
        from openai import AuthenticationError

        error = AuthenticationError(
            message="Invalid API key",
            response=MagicMock(status_code=401, headers={}),
            body=None,
        )
        mock_client = AsyncMock()
        mock_client.videos.create_and_poll = AsyncMock(return_value=_make_mock_video(seconds="8"))
        mock_client.videos.download_content = AsyncMock(side_effect=error)
        mock_sleep = AsyncMock()

        with (
            patch("lib.openai_shared.AsyncOpenAI", return_value=mock_client),
            patch("lib.retry.asyncio.sleep", mock_sleep),
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

        # Non-retryable error: only 1 download call, no sleep
        assert mock_client.videos.download_content.call_count == 1
        mock_sleep.assert_not_called()
