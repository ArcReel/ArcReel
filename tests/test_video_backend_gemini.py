"""GeminiVideoBackend unit tests — mock genai SDK."""

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
    """Create a GeminiVideoBackend in aistudio mode (mock genai SDK)."""
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


@pytest.fixture
def content_api_backend(mock_rate_limiter):
    """Create a GeminiVideoBackend with use_content_api=True (mock genai SDK)."""
    with patch("google.genai"), patch("google.genai.types"):
        from lib.video_backends.gemini import GeminiVideoBackend

        b = GeminiVideoBackend(
            backend_type="aistudio",
            api_key="test-key",
            rate_limiter=mock_rate_limiter,
            base_url="https://custom-provider.example.com/",
            use_content_api=True,
        )
        b._client = MagicMock()
        b._client.aio = MagicMock()
        yield b


# ── Property tests ────────────────────────────────────────


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
        # Prepare mock vertex credentials file
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


# ── Generation tests ──────────────────────────────────────


def _make_done_operation(video_uri="gs://bucket/video.mp4"):
    """Build a completed operation mock."""
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
            negative_prompt="no music",
        )

        result = await backend.generate(request)

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "gemini"
        assert result.video_uri == "gs://bucket/video.mp4"
        assert result.video_path == output
        assert result.duration_seconds == 8

        # Confirm the API was called
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
        """Test polling logic: first returns not-done, then returns done."""
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

        # patch asyncio.sleep to avoid actual waiting
        with patch("lib.video_backends.gemini.asyncio.sleep", new_callable=AsyncMock):
            result = await backend.generate(request)

        assert result.provider == "gemini"

    async def test_generate_empty_result_raises(self, backend, tmp_path):
        """Should raise RuntimeError when API returns an empty result."""
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

        with pytest.raises(RuntimeError, match="API returned empty result"):
            await backend.generate(request)

    async def test_generate_error_in_operation(self, backend, tmp_path):
        """Should raise RuntimeError when operation contains an error."""
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

        with pytest.raises(RuntimeError, match="Video generation failed"):
            await backend.generate(request)

    async def test_rate_limiter_called(self, backend, mock_rate_limiter, tmp_path):
        """Confirm that generate calls the rate limiter."""
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        await backend.generate(request)
        mock_rate_limiter.acquire_async.assert_called_once_with(backend._video_model)

    async def test_default_negative_prompt(self, backend, tmp_path):
        """Uses the default value when negative_prompt is not specified."""
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
            negative_prompt=None,
        )

        await backend.generate(request)

        # Verify GenerateVideosConfig was called with the default negative_prompt
        config_call = backend._types.GenerateVideosConfig.call_args
        assert "music" in config_call.kwargs.get("negative_prompt", "")


class TestGeminiRetryBehavior:
    """Test retry separation behavior between task creation and polling."""

    async def test_poll_transient_error_retries_without_recreating_task(self, backend, tmp_path):
        """Transient errors during polling should retry the poll, not recreate the task."""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        done_op = _make_done_operation()

        backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        # First poll raises ConnectionError, second returns done
        backend._client.aio.operations.get = AsyncMock(side_effect=[ConnectionError("connection reset"), done_op])

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with patch("lib.video_backends.gemini.asyncio.sleep", new_callable=AsyncMock):
            result = await backend.generate(request)

        assert result.provider == "gemini"
        # Key assertion: task was created only once
        backend._client.aio.models.generate_videos.assert_awaited_once()
        # Poll was called twice (one failure + one success)
        assert backend._client.aio.operations.get.await_count == 2

    async def test_create_retries_on_transient_error(self, backend, tmp_path):
        """Transient errors during task creation should be retried by @with_retry_async."""
        output = tmp_path / "out.mp4"

        done_op = _make_done_operation()
        # First creation raises ConnectionError, second succeeds
        backend._client.aio.models.generate_videos = AsyncMock(
            side_effect=[ConnectionError("connection reset"), done_op]
        )

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with (
            patch("lib.video_backends.gemini.asyncio.sleep", new_callable=AsyncMock),
            patch("lib.retry.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await backend.generate(request)

        assert result.provider == "gemini"
        # Creation was called twice (one failure + one success)
        assert backend._client.aio.models.generate_videos.await_count == 2

    async def test_poll_non_retryable_error_propagates(self, backend, tmp_path):
        """Non-retryable errors during polling should propagate immediately."""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        backend._client.aio.operations.get = AsyncMock(side_effect=ValueError("invalid response"))

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(ValueError, match="invalid response"):
            with patch("lib.video_backends.gemini.asyncio.sleep", new_callable=AsyncMock):
                await backend.generate(request)

        # Creation called only once
        backend._client.aio.models.generate_videos.assert_awaited_once()
        # Polling attempted only once before raising
        assert backend._client.aio.operations.get.await_count == 1


# ── _prepare_image_param tests ───────────────────────────


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


# ── _download_video tests ─────────────────────────────────


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

        with pytest.raises(RuntimeError, match="video data could not be retrieved"):
            backend._download_video(mock_ref, output)


# ── Content API (custom provider) tests ──────────────────


def _make_content_api_response(video_bytes=b"fake-video-data", mime_type="video/mp4"):
    """Build a mock response returned by generate_content (with video inline_data)."""
    mock_blob = MagicMock()
    mock_blob.data = video_bytes
    mock_blob.mime_type = mime_type

    mock_part = MagicMock()
    mock_part.inline_data = mock_blob

    mock_content = MagicMock()
    mock_content.parts = [mock_part]

    mock_candidate = MagicMock()
    mock_candidate.content = mock_content

    mock_response = MagicMock()
    mock_response.candidates = [mock_candidate]
    return mock_response


class TestContentApiGenerate:
    """When use_content_api=True, should use generate_content instead of generate_videos."""

    async def test_generate_calls_generate_content(self, content_api_backend, tmp_path):
        output = tmp_path / "out.mp4"

        mock_resp = _make_content_api_response()
        content_api_backend._client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        request = VideoGenerationRequest(prompt="a cat walking", output_path=output)
        result = await content_api_backend.generate(request)

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "gemini"
        assert result.video_path == output
        assert output.read_bytes() == b"fake-video-data"

        # Confirm generate_content was called instead of generate_videos
        content_api_backend._client.aio.models.generate_content.assert_awaited_once()

    async def test_generate_does_not_call_generate_videos(self, content_api_backend, tmp_path):
        """Should not call generate_videos when use_content_api=True."""
        output = tmp_path / "out.mp4"

        mock_resp = _make_content_api_response()
        content_api_backend._client.aio.models.generate_content = AsyncMock(return_value=mock_resp)
        content_api_backend._client.aio.models.generate_videos = AsyncMock()

        request = VideoGenerationRequest(prompt="test", output_path=output)
        await content_api_backend.generate(request)

        content_api_backend._client.aio.models.generate_videos.assert_not_awaited()

    async def test_generate_with_start_image(self, content_api_backend, tmp_path):
        output = tmp_path / "out.mp4"

        # Create a valid PNG image file
        from PIL import Image as PILImage

        img = PILImage.new("RGB", (10, 10), color="red")
        frame = tmp_path / "frame.png"
        img.save(frame)

        mock_resp = _make_content_api_response()
        content_api_backend._client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        request = VideoGenerationRequest(prompt="cat moves", output_path=output, start_image=frame)
        result = await content_api_backend.generate(request)

        assert result.video_path == output
        # contents should contain PIL.Image + prompt (str)
        call_kwargs = content_api_backend._client.aio.models.generate_content.call_args.kwargs
        contents = call_kwargs["contents"]
        assert len(contents) == 2
        assert isinstance(contents[0], PILImage.Image)
        assert isinstance(contents[1], str)

    async def test_generate_empty_response_raises(self, content_api_backend, tmp_path):
        """Should raise RuntimeError when API returns no candidates."""
        output = tmp_path / "out.mp4"

        mock_response = MagicMock()
        mock_response.candidates = []
        content_api_backend._client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(RuntimeError, match="API did not return video data"):
            await content_api_backend.generate(request)

    async def test_rate_limiter_called(self, content_api_backend, mock_rate_limiter, tmp_path):
        output = tmp_path / "out.mp4"

        mock_resp = _make_content_api_response()
        content_api_backend._client.aio.models.generate_content = AsyncMock(return_value=mock_resp)

        request = VideoGenerationRequest(prompt="test", output_path=output)
        await content_api_backend.generate(request)

        mock_rate_limiter.acquire_async.assert_called_once_with(content_api_backend._video_model)
