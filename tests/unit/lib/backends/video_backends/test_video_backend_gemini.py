"""GeminiVideoBackend unit tests with a mocked genai SDK."""

import asyncio
import io
import threading
import time
import urllib.error
from email.message import Message
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import errors as genai_errors

from arcreel_market_core.video_backend_contract import (
    ResumeExpiredError,
    VideoCapabilityError,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from lib.backends.gemini_shared import GEMINI_DEFAULT_VIDEO_MODEL, VERTEX_SCOPES
from tests.fakes import bounded_poll_clock, captured_provider_job_ids, captured_vertex_credentials
from tests.http_capture import PathPrefixRewrite, capture_http
from tests.unit.lib.backends.write_cancel_gate import GatedWritePath, WriteGate, cancel_during_write


class _RecordingRateLimiter:
    """Rate-limiter substitute: record the model name on each quota acquisition without waiting.

    The per-model quota contract is established by names recorded in ``acquired``, not the substitute's call target.
    """

    def __init__(self) -> None:
        self.acquired: list[str] = []

    def acquire(self, model: str) -> None:
        self.acquired.append(model)

    async def acquire_async(self, model: str) -> None:
        self.acquired.append(model)


@pytest.fixture
def mock_rate_limiter():
    return _RecordingRateLimiter()


def _stream_to_destination(content: bytes = b"aistudio-bytes", *, fail_after: bytes | None = None):
    """``files.download`` substitute: write content in chunks to writable destination, like the SDK.

    With ``fail_after``, first write those bytes, then raise to simulate an interrupted, truncated file.
    """

    def download(*, file, destination=None, config=None):
        assert hasattr(destination, "write"), "AI Studio 取件必须带 destination，不得把整段视频读进内存"
        if fail_after is not None:
            destination.write(fail_after)
            raise ConnectionError("download interrupted")
        destination.write(content)

    return download


def _far_deadline() -> float:
    return time.monotonic() + 3600


class _FakeUrlResponse(io.BytesIO):
    """``urlopen`` return substitute: readable body plus ``headers``; ``Content-Length`` may differ from body length to simulate truncation."""

    def __init__(self, body: bytes, *, content_length: int | None = None) -> None:
        super().__init__(body)
        length = len(body) if content_length is None else content_length
        self.headers = Message()
        self.headers["Content-Length"] = str(length)


@pytest.fixture
def gemini_backend(mock_rate_limiter):
    """Create an aistudio GeminiVideoBackend with a mocked genai SDK."""
    with patch("google.genai") as genai_mock, patch("google.genai.types"):
        from lib.backends.video_backends.gemini import GeminiVideoBackend

        b = GeminiVideoBackend(
            backend_type="aistudio",
            api_key="test-key",
            rate_limiter=mock_rate_limiter,
        )
        # Keep genai.Client constructor calls here; replace _client with a bare substitute for individual cases to configure.
        b._test_genai = genai_mock
        b._client = MagicMock()
        b._client.aio = MagicMock()
        # Default retrieval streams content to destination like the SDK; individual cases change side_effect to simulate failures.
        b._client.files.download.side_effect = _stream_to_destination()
        yield b


# Property tests.


class TestGeminiVideoBackendProperties:
    def test_name(self, gemini_backend):
        assert gemini_backend.name == "gemini-aistudio"
        # aistudio: pass api_key unchanged to genai.Client; omit http_options when base_url is absent.
        gemini_backend._test_genai.Client.assert_called_once_with(api_key="test-key", http_options=None)

    def test_video_capabilities_aistudio(self, gemini_backend):
        caps = gemini_backend.video_capabilities
        assert caps.last_frame is True
        # The channel supports unlimited reference images; Veo model entries supply limits (0 for Lite).
        assert caps.max_reference_images is None
        # Missing video_model selects the default video model.
        assert gemini_backend.model == GEMINI_DEFAULT_VIDEO_MODEL

    def test_vertex_backend_constructs_from_credentials_file(self, mock_rate_limiter, tmp_path):
        # Prepare a mock Vertex credential file.
        creds_file = tmp_path / "vertex_credentials.json"
        creds_file.write_text('{"project_id": "test-project"}')

        with (
            patch("google.genai") as genai_mock,
            patch("google.genai.types"),
            patch(
                "lib.backends.video_backends.gemini.resolve_vertex_credentials_path",
                return_value=creds_file,
            ),
            captured_vertex_credentials() as from_service_account_file,
        ):
            from lib.backends.video_backends.gemini import GeminiVideoBackend

            b = GeminiVideoBackend(
                backend_type="vertex",
                rate_limiter=mock_rate_limiter,
            )
            assert b.name == "gemini-vertex"
            # Load service-account credentials with Vertex scopes; construct the client in vertexai mode with the file's project_id.
            from_service_account_file.assert_called_once_with(str(creds_file), scopes=VERTEX_SCOPES)
            genai_mock.Client.assert_called_once_with(
                vertexai=True,
                project="test-project",
                location="global",
                credentials=from_service_account_file.return_value,
            )


# Generation tests.


def _make_done_operation(video_uri="gs://bucket/video.mp4"):
    """Construct a completed operation mock."""
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
    async def test_reference_audio_rejected_before_submit(self, gemini_backend, tmp_path):
        """The request body has no reference-audio field; reject it rather than discard it and submit normally."""
        gemini_backend._client.aio.models.generate_videos = AsyncMock()
        audio = tmp_path / "voice.mp3"
        audio.write_bytes(b"ID3")

        with pytest.raises(VideoCapabilityError) as exc:
            await gemini_backend.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", reference_audio_files=[audio])
            )
        assert exc.value.code == "video_reference_audio_unsupported"
        gemini_backend._client.aio.models.generate_videos.assert_not_awaited()

    async def test_aistudio_mute_rejected_before_submit(self, gemini_backend, tmp_path):
        """AI Studio has no audio switch: if a user widens capability to controllable and disables audio, reject before payment rather than produce an audible video."""
        gemini_backend._client.aio.models.generate_videos = AsyncMock()

        with pytest.raises(VideoCapabilityError) as exc:
            await gemini_backend.generate(
                VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=False)
            )
        assert exc.value.code == "video_audio_switch_not_supported"
        gemini_backend._client.aio.models.generate_videos.assert_not_awaited()

    @pytest.mark.parametrize("model", ["veo-3.1-generate-001", "veo-user-added"])
    async def test_vertex_mute_sent_and_billed_silent(self, gemini_backend, tmp_path, model):
        """Vertex sends generate_audio: estimates, results, and settlement follow actual requests for registered and user-added models."""
        from lib.backends.video_backends.gemini import GeminiVideoBackend

        gemini_backend._backend_type = "vertex"
        gemini_backend._video_model = model
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=_make_done_operation())
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", generate_audio=False)

        assert GeminiVideoBackend.metered_usage(model, request).dimensions["audio"] is False
        result = await gemini_backend.generate(request)

        assert gemini_backend._types.GenerateVideosConfig.call_args.kwargs["generate_audio"] is False
        assert result.generate_audio is False
        assert GeminiVideoBackend.metered_usage(model, request, result).dimensions["audio"] is False

    async def test_generate_text_to_video(self, gemini_backend, tmp_path):
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="a cat walking",
            output_path=output,
            duration_seconds=8,
        )

        result = await gemini_backend.generate(request)

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "gemini"
        assert result.model == gemini_backend.model
        assert result.video_uri == "gs://bucket/video.mp4"
        assert result.video_path == output
        assert result.duration_seconds == 8
        # aistudio final videos always have an audio track.
        assert result.generate_audio is True

        # Confirm the API was called.
        gemini_backend._client.aio.models.generate_videos.assert_awaited_once()
        # Stream through files.download(destination=...); final artifact is output_path, leaving no .part.
        assert output.read_bytes() == b"aistudio-bytes"
        assert not output.with_name("out.mp4.part").exists()
        gemini_backend._client.files.download.assert_called_once()
        assert gemini_backend._client.files.download.call_args.kwargs["file"] is (
            mock_op.response.generated_videos[0].video
        )

    async def test_generate_image_to_video(self, gemini_backend, tmp_path):
        output = tmp_path / "out.mp4"
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"fake-png-data")

        mock_op = _make_done_operation(video_uri=None)
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="cat moves forward",
            output_path=output,
            start_image=frame,
        )

        result = await gemini_backend.generate(request)

        assert result.provider == "gemini"
        assert result.video_path == output

    async def test_generate_polls_until_done(self, gemini_backend, tmp_path):
        """Test polling: return incomplete first, then completed."""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        done_op = _make_done_operation()

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        gemini_backend._client.aio.operations.get = AsyncMock(return_value=done_op)

        request = VideoGenerationRequest(
            prompt="a sunset",
            output_path=output,
        )

        with bounded_poll_clock():
            result = await gemini_backend.generate(request)

        assert result.provider == "gemini"
        # Poll using the operation object returned by submit.
        gemini_backend._client.aio.operations.get.assert_awaited_once_with(pending_op)

    async def test_generate_empty_result_raises(self, gemini_backend, tmp_path):
        """Raise RuntimeError when the API returns empty results."""
        output = tmp_path / "out.mp4"

        mock_op = MagicMock()
        mock_op.done = True
        mock_op.response = MagicMock()
        mock_op.response.generated_videos = []
        mock_op.error = None

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        with pytest.raises(RuntimeError, match="API 返回空结果"):
            await gemini_backend.generate(request)

    async def test_generate_error_in_operation(self, gemini_backend, tmp_path):
        """Raise RuntimeError when the operation contains an error."""
        output = tmp_path / "out.mp4"

        mock_op = MagicMock()
        mock_op.done = True
        mock_op.response = None
        mock_op.error = "Something went wrong"

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        # Include operation.error content in the message, distinguishing it from the empty-result branch.
        with pytest.raises(RuntimeError, match="视频生成失败: Something went wrong"):
            await gemini_backend.generate(request)

    async def test_rate_limiter_called(self, gemini_backend, mock_rate_limiter, tmp_path):
        """Confirm generate calls the rate limiter."""
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        await gemini_backend.generate(request)
        assert mock_rate_limiter.acquired == [gemini_backend._video_model]

    async def test_no_negative_prompt_in_config(self, gemini_backend, tmp_path):
        """negative_prompt uses the prompt-text channel; GenerateVideosConfig omits that field."""
        output = tmp_path / "out.mp4"

        mock_op = _make_done_operation()
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="test",
            output_path=output,
        )

        await gemini_backend.generate(request)

        config_call = gemini_backend._types.GenerateVideosConfig.call_args
        assert "negative_prompt" not in config_call.kwargs


class TestGeminiRetryBehavior:
    """Test separation of task-creation and polling retries."""

    async def test_poll_transient_error_retries_without_recreating_task(self, gemini_backend, tmp_path):
        """Transient polling errors retry polling instead of creating another task."""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        done_op = _make_done_operation()

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        # First poll raises ConnectionError; second returns completion.
        gemini_backend._client.aio.operations.get = AsyncMock(
            side_effect=[ConnectionError("connection reset"), done_op]
        )

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with bounded_poll_clock():
            result = await gemini_backend.generate(request)

        assert result.provider == "gemini"
        # Critical assertion: create the task only once.
        gemini_backend._client.aio.models.generate_videos.assert_awaited_once()
        # Poll twice (one failure + one success).
        assert gemini_backend._client.aio.operations.get.await_count == 2

    async def test_create_retries_on_transient_error(self, gemini_backend, tmp_path):
        """Transient task-creation errors are retried by @with_retry_async."""
        output = tmp_path / "out.mp4"

        done_op = _make_done_operation()
        # First creation raises ConnectionError; second succeeds.
        gemini_backend._client.aio.models.generate_videos = AsyncMock(
            side_effect=[ConnectionError("connection reset"), done_op]
        )

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with (
            bounded_poll_clock(),
        ):
            result = await gemini_backend.generate(request)

        assert result.provider == "gemini"
        # Create twice (one failure + one success).
        assert gemini_backend._client.aio.models.generate_videos.await_count == 2

    async def test_poll_non_retryable_error_propagates(self, gemini_backend, tmp_path):
        """Nonretryable polling errors raise immediately."""
        output = tmp_path / "out.mp4"

        pending_op = MagicMock()
        pending_op.done = False

        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=pending_op)
        gemini_backend._client.aio.operations.get = AsyncMock(side_effect=ValueError("invalid response"))

        request = VideoGenerationRequest(prompt="test", output_path=output)
        with pytest.raises(ValueError, match="invalid response"), bounded_poll_clock():
            await gemini_backend.generate(request)

        # Create only once.
        gemini_backend._client.aio.models.generate_videos.assert_awaited_once()
        # Raise after only one polling attempt.
        assert gemini_backend._client.aio.operations.get.await_count == 1


# _prepare_image_param tests.


class TestPrepareImageParam:
    def test_none_returns_none(self, gemini_backend):
        assert gemini_backend._prepare_image_param(None) is None

    def test_path_reads_file(self, gemini_backend, tmp_path):
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic

        result = gemini_backend._prepare_image_param(img_file)
        # Pass original file bytes and .jpg-derived MIME type to types.Image.
        assert result is gemini_backend._types.Image.return_value
        gemini_backend._types.Image.assert_called_once_with(image_bytes=b"\xff\xd8\xff\xe0", mime_type="image/jpeg")

    def test_pil_image(self, gemini_backend):
        from io import BytesIO

        from PIL import Image as PILImage

        img = PILImage.new("RGB", (10, 10), color="red")
        result = gemini_backend._prepare_image_param(img)
        # Encode PIL images as PNG bytes with MIME image/png.
        assert result is gemini_backend._types.Image.return_value
        image_kwargs = gemini_backend._types.Image.call_args.kwargs
        assert image_kwargs["mime_type"] == "image/png"
        assert PILImage.open(BytesIO(image_kwargs["image_bytes"])).format == "PNG"


# _download_video tests.


class TestDownloadVideo:
    async def test_vertex_inline_video_settles_through_cancellation(self, gemini_backend, tmp_path):
        gate = WriteGate()
        gemini_backend._backend_type = "vertex"
        operation = _make_done_operation()
        output = tmp_path / "video.mp4"
        request = VideoGenerationRequest(prompt="p", output_path=GatedWritePath(output).attach(gate))

        steps = await cancel_during_write(lambda: gemini_backend._collect_video(operation, request), gate)

        assert steps == ["follow-up"]
        assert output.read_bytes() == b"fake-video-bytes"
        assert list(tmp_path.iterdir()) == [output]

    def test_aistudio_download_streams_to_destination(self, gemini_backend, tmp_path):
        output = tmp_path / "video.mp4"
        ref = MagicMock()
        gemini_backend._client = SimpleNamespace(files=SimpleNamespace(download=_stream_to_destination()))

        gemini_backend._download_video(ref, output, deadline=_far_deadline())

        assert output.read_bytes() == b"aistudio-bytes"
        assert list(tmp_path.iterdir()) == [output]
        # The SDK writes final videos directly to destination, without saving from memory through video_ref.save().
        ref.save.assert_not_called()

    async def test_cancelled_download_settles_the_write_before_propagating(self, gemini_backend, tmp_path):
        # Write .part in a thread, then replace the final video; if cancellation propagates first, the thread could write after caller cleanup.
        output = tmp_path / "video.mp4"
        started = threading.Event()
        release = threading.Event()

        def blocking_download(*, file, destination, config):
            started.set()
            release.wait()
            destination.write(b"aistudio-bytes")

        gemini_backend._client = SimpleNamespace(files=SimpleNamespace(download=blocking_download))
        task = asyncio.create_task(gemini_backend._download_video_with_retry(MagicMock(), output))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            results = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=1)

        assert isinstance(results[0], asyncio.CancelledError)
        assert output.read_bytes() == b"aistudio-bytes"
        assert list(tmp_path.iterdir()) == [output]

    def test_aistudio_download_carries_sdk_io_timeout(self, gemini_backend, tmp_path):
        # The SDK has no default timeout; supply an I/O timeout through http_options on every retrieval.
        output = tmp_path / "video.mp4"

        gemini_backend._download_video(MagicMock(), output, deadline=_far_deadline())

        timeout_ms = gemini_backend._client.files.download.call_args.kwargs["config"]["http_options"]["timeout"]
        assert 0 < timeout_ms <= 120_000

    async def test_slow_download_ends_within_artifact_budget(self, gemini_backend, tmp_path):
        # Cancellation waits for the thread; asyncio.timeout cannot stop a continuously slow stream, so the thread must enforce the same budget.
        output = tmp_path / "video.mp4"

        def trickle(*, file, destination, config):
            for _ in range(200):  # Without a deadline, completion takes approximately 10s.
                destination.write(b"x")
                time.sleep(0.05)

        gemini_backend._client = SimpleNamespace(files=SimpleNamespace(download=trickle))
        started = time.monotonic()
        with (
            patch("lib.backends.video_backends.gemini.ARTIFACT_DOWNLOAD_MAX_WAIT_SECONDS", 0.3),
            pytest.raises(TimeoutError),
        ):
            await gemini_backend._download_video_with_retry(MagicMock(), output)

        assert time.monotonic() - started < 2
        assert list(tmp_path.iterdir()) == []

    def test_vertex_download_from_uri_past_deadline_does_not_connect(self, gemini_backend, tmp_path):
        gemini_backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"
        mock_ref = MagicMock()
        mock_ref.video_bytes = None
        mock_ref.uri = "https://example.com/video.mp4"

        with patch("urllib.request.urlopen") as urlopen, pytest.raises(TimeoutError):
            gemini_backend._download_video(mock_ref, output, deadline=time.monotonic() - 1)

        urlopen.assert_not_called()
        assert list(tmp_path.iterdir()) == []

    def test_aistudio_download_failure_leaves_no_partial_file(self, gemini_backend, tmp_path):
        output = tmp_path / "video.mp4"
        gemini_backend._client = SimpleNamespace(
            files=SimpleNamespace(download=_stream_to_destination(fail_after=b"partial"))
        )

        with pytest.raises(ConnectionError):
            gemini_backend._download_video(MagicMock(), output, deadline=_far_deadline())

        assert list(tmp_path.iterdir()) == []

    def test_aistudio_download_failure_keeps_existing_video(self, gemini_backend, tmp_path):
        # On regeneration, output_path already holds the previous video; interrupted download must not damage it.
        output = tmp_path / "video.mp4"
        output.write_bytes(b"previous-video")
        gemini_backend._client = SimpleNamespace(
            files=SimpleNamespace(download=_stream_to_destination(fail_after=b"partial"))
        )

        with pytest.raises(ConnectionError):
            gemini_backend._download_video(MagicMock(), output, deadline=_far_deadline())

        assert output.read_bytes() == b"previous-video"
        assert list(tmp_path.iterdir()) == [output]

    def test_vertex_download_from_bytes(self, gemini_backend, tmp_path):
        gemini_backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"

        mock_ref = MagicMock()
        mock_ref.video_bytes = b"video-data"

        gemini_backend._download_video(mock_ref, output, deadline=_far_deadline())

        assert output.read_bytes() == b"video-data"
        assert list(tmp_path.iterdir()) == [output]

    def test_vertex_download_from_bytes_failure_keeps_existing_video(self, gemini_backend, tmp_path):
        gemini_backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"
        output.write_bytes(b"previous-video")
        mock_ref = MagicMock()
        mock_ref.video_bytes = b"video-data"

        def interrupted_write(self, data):
            self.write_text("partial")
            raise OSError("disk full")

        with patch("pathlib.Path.write_bytes", interrupted_write), pytest.raises(OSError, match="disk full"):
            gemini_backend._download_video(mock_ref, output, deadline=_far_deadline())

        assert output.read_bytes() == b"previous-video"
        assert list(tmp_path.iterdir()) == [output]

    def test_vertex_download_from_bytes_failure_leaves_no_partial_file(self, gemini_backend, tmp_path):
        gemini_backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"
        mock_ref = MagicMock()
        mock_ref.video_bytes = b"video-data"

        def interrupted_write(self, data):
            self.write_text("partial")
            raise OSError("disk full")

        with patch("pathlib.Path.write_bytes", interrupted_write), pytest.raises(OSError, match="disk full"):
            gemini_backend._download_video(mock_ref, output, deadline=_far_deadline())

        assert list(tmp_path.iterdir()) == []

    def test_vertex_download_from_uri(self, gemini_backend, tmp_path):
        gemini_backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"
        mock_ref = MagicMock()
        mock_ref.video_bytes = None
        mock_ref.uri = "https://example.com/video.mp4"

        with patch("urllib.request.urlopen", return_value=_FakeUrlResponse(b"uri-video")) as urlopen:
            gemini_backend._download_video(mock_ref, output, deadline=_far_deadline())

        assert output.read_bytes() == b"uri-video"
        assert list(tmp_path.iterdir()) == [output]
        # urlopen I/O timeout terminates stalled connections.
        assert urlopen.call_args.args == ("https://example.com/video.mp4",)
        assert 0 < urlopen.call_args.kwargs["timeout"] <= 120

    @pytest.mark.parametrize("existing", [None, b"previous-video"])
    def test_vertex_download_from_uri_failure_keeps_target_untouched(self, gemini_backend, tmp_path, existing):
        gemini_backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"
        if existing is not None:
            output.write_bytes(existing)
        mock_ref = MagicMock()
        mock_ref.video_bytes = None
        mock_ref.uri = "https://example.com/video.mp4"

        # Premature connection close: body shorter than Content-Length raises truncation error.
        truncated = _FakeUrlResponse(b"partial", content_length=1024)

        with (
            patch("urllib.request.urlopen", return_value=truncated),
            pytest.raises(urllib.error.ContentTooShortError),
        ):
            gemini_backend._download_video(mock_ref, output, deadline=_far_deadline())

        expected = [] if existing is None else [output]
        assert list(tmp_path.iterdir()) == expected
        if existing is not None:
            assert output.read_bytes() == existing

    def test_vertex_no_data_raises(self, gemini_backend, tmp_path):
        gemini_backend._backend_type = "vertex"
        output = tmp_path / "video.mp4"

        mock_ref = MagicMock(spec=[])  # no attributes

        with pytest.raises(RuntimeError, match="无法获取视频数据"):
            gemini_backend._download_video(mock_ref, output, deadline=_far_deadline())


def _sdk_error(code: int, status: str, message: str) -> genai_errors.ClientError:
    """Construct 4xx response exceptions in the real SDK shape: code is the HTTP status, and body is a standard Google error envelope."""
    return genai_errors.ClientError(code, {"error": {"code": code, "message": message, "status": status}})


class TestGeminiResumeVideo:
    """resume_video: only a 404 querying an operation by job_id becomes ResumeExpiredError."""

    async def test_mid_poll_not_found_classified_as_resume_expired(self, gemini_backend, tmp_path):
        # Initial operations.get returns pending to enter the poll loop; poll_fn raises NOT_FOUND.
        pending_op = MagicMock()
        pending_op.done = False
        get_calls = {"n": 0}

        async def _fake_get(_op):
            get_calls["n"] += 1
            if get_calls["n"] == 1:
                return pending_op
            raise _sdk_error(404, "NOT_FOUND", "Operation not found")

        gemini_backend._client.aio.operations.get = AsyncMock(side_effect=_fake_get)
        # Mock GenerateVideosOperation.model_validate; returning any object suffices.
        gemini_backend._types.GenerateVideosOperation.model_validate = MagicMock(return_value=pending_op)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with bounded_poll_clock(), pytest.raises(ResumeExpiredError) as ei:
            await gemini_backend.resume_video("op-xyz", request)
        assert ei.value.job_id == "op-xyz"

    async def test_mid_poll_not_found_is_not_retried_when_message_carries_status_digits(self, gemini_backend, tmp_path):
        # SDK exception text contains the entire response, and operation names may contain "500" / "429"; base polling retries on status codes,
        # Do not retry 404 as transient merely because it contains those substrings.
        pending_op = MagicMock()
        pending_op.done = False
        get_calls = {"n": 0}

        async def _fake_get(_op):
            get_calls["n"] += 1
            if get_calls["n"] == 1:
                return pending_op
            raise _sdk_error(404, "NOT_FOUND", "Operation projects/5001/operations/4290 not found")

        gemini_backend._client.aio.operations.get = AsyncMock(side_effect=_fake_get)
        gemini_backend._types.GenerateVideosOperation.model_validate = MagicMock(return_value=pending_op)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with bounded_poll_clock(), pytest.raises(ResumeExpiredError):
            await gemini_backend.resume_video("op-digits", request)
        assert get_calls["n"] == 2

    async def test_aistudio_resume_of_mute_request_bills_audio(self, gemini_backend, tmp_path):
        """Allow resuming submitted tasks normally (cost already incurred); settle AI Studio videos as audible, matching the actual submitted request."""
        from lib.backends.video_backends.gemini import GeminiVideoBackend

        done_op = _make_done_operation()
        gemini_backend._client.aio.operations.get = AsyncMock(return_value=done_op)
        gemini_backend._types.GenerateVideosOperation.model_validate = MagicMock(return_value=done_op)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4", generate_audio=False)
        result = await gemini_backend.resume_video("op-mute", request)

        assert result.generate_audio is True
        assert GeminiVideoBackend.metered_usage(gemini_backend.model, request, result).dimensions["audio"] is True

    async def test_initial_get_not_found_classified_as_resume_expired(self, gemini_backend, tmp_path):
        gemini_backend._client.aio.operations.get = AsyncMock(
            side_effect=_sdk_error(404, "NOT_FOUND", "Operation not found")
        )
        rebuilt_op = MagicMock()
        gemini_backend._types.GenerateVideosOperation.model_validate = MagicMock(return_value=rebuilt_op)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with pytest.raises(ResumeExpiredError) as ei:
            await gemini_backend.resume_video("op-not-found", request)

        assert ei.value.provider == "gemini"
        # Reconstruct the incomplete operation from job_id, then query remotely with that object.
        gemini_backend._types.GenerateVideosOperation.model_validate.assert_called_once_with(
            {"name": "op-not-found", "done": False}
        )
        gemini_backend._client.aio.operations.get.assert_awaited_once_with(rebuilt_op)

    @pytest.mark.parametrize(
        "error",
        [
            # INVALID_ARGUMENT means invalid input (such as malformed operation), not a nonexistent operation.
            _sdk_error(400, "INVALID_ARGUMENT", "malformed operation name"),
            # An exception without SDK status codes does not imply operation reclamation merely because text contains "not found" / "expired".
            RuntimeError("operation not found"),
            RuntimeError("resource expired after 24h"),
        ],
        ids=["invalid-argument", "not-found-text", "expired-text"],
    )
    async def test_query_error_without_not_found_status_propagates(self, gemini_backend, tmp_path, error):
        gemini_backend._client.aio.operations.get = AsyncMock(side_effect=error)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with pytest.raises(type(error)) as ei:
            await gemini_backend.resume_video("op-xyz", request)

        assert ei.value is error

    async def test_completed_operation_error_mentioning_not_found_is_not_expired(self, gemini_backend, tmp_path):
        # The operation was found and completed, but generation failed; reason text does not determine operation existence.
        failed_op = MagicMock()
        failed_op.done = True
        failed_op.response = None
        failed_op.error = {"code": 5, "message": "Requested entity was not found."}
        gemini_backend._client.aio.operations.get = AsyncMock(return_value=failed_op)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with pytest.raises(RuntimeError, match="视频生成失败") as ei:
            await gemini_backend.resume_video("op-xyz", request)

        assert not isinstance(ei.value, ResumeExpiredError)

    async def test_vertex_artifact_404_propagates_as_download_failure(self, gemini_backend, tmp_path):
        # The remote task succeeded (completed operation with video URI); artifact URL returns 404 during retrieval.
        gemini_backend._backend_type = "vertex"
        video_uri = "https://storage.googleapis.com/bucket/video.mp4"
        done_op = _make_done_operation(video_uri=video_uri)
        done_op.response.generated_videos[0].video.video_bytes = None
        gemini_backend._client.aio.operations.get = AsyncMock(return_value=done_op)
        not_found = urllib.error.HTTPError(video_uri, 404, "Not Found", Message(), None)

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with (
            patch("urllib.request.urlopen", side_effect=not_found),
            pytest.raises(urllib.error.HTTPError) as ei,
        ):
            await gemini_backend.resume_video("op-xyz", request)

        assert ei.value is not_found
        assert not request.output_path.exists()

    async def test_aistudio_artifact_404_propagates_as_download_failure(self, gemini_backend, tmp_path):
        # The remote task succeeded, but SDK files.download returns 404; retrieval failure is not operation expiry.
        gemini_backend._client.aio.operations.get = AsyncMock(return_value=_make_done_operation())
        not_found = _sdk_error(404, "NOT_FOUND", "File not found")
        gemini_backend._client.files.download.side_effect = not_found

        request = VideoGenerationRequest(prompt="x", output_path=tmp_path / "out.mp4")
        with pytest.raises(genai_errors.ClientError) as ei:
            await gemini_backend.resume_video("op-xyz", request)

        assert ei.value is not_found


class TestGeminiSubmittedBaseUrlReplay:
    """AI Studio operations are queryable only on their submission host: persist the actual host and use it for resume polling/retrieval."""

    async def test_generate_persists_request_base_url(self, gemini_backend, tmp_path):
        gemini_backend._client.aio.models.generate_videos = AsyncMock(return_value=_make_done_operation())
        request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4", task_id="db-task")

        with captured_provider_job_ids() as persisted:
            await gemini_backend.generate(request)

        # No configured base_url: persist the SDK default host; later gateway configuration must not redirect resume away from the official host.
        assert persisted[0]["base_url"] == "https://generativelanguage.googleapis.com/"

    async def test_resume_polls_and_downloads_from_submitted_base_url(self, gemini_backend, tmp_path):
        # Submit to gateway A, then restore default configuration before resume: use current credentials but poll/retrieve through A.
        replay_client = MagicMock()
        replay_client.aio.operations.get = AsyncMock(return_value=_make_done_operation())
        replay_client.aio.aclose = AsyncMock()
        replay_client.files.download.side_effect = _stream_to_destination(b"from-relay-a")
        gemini_backend._client.aio.operations.get = AsyncMock(side_effect=AssertionError("不应按当下域名查询"))
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "out.mp4", submitted_base_url="https://relay-a.example.com"
        )

        with patch("google.genai") as genai_mock:
            genai_mock.Client.return_value = replay_client
            await gemini_backend.resume_video("op-a", request)

        genai_mock.Client.assert_called_once_with(
            api_key="test-key", http_options={"base_url": "https://relay-a.example.com/"}
        )
        assert request.output_path.read_bytes() == b"from-relay-a"
        gemini_backend._client.files.download.assert_not_called()
        replay_client.aio.aclose.assert_awaited_once()
        replay_client.close.assert_called_once()

    async def test_resume_on_submitted_base_url_carries_and_closes_the_injected_layer(self, tmp_path):
        from lib.backends.video_backends.gemini import GeminiVideoBackend

        layer = PathPrefixRewrite("/", "/")
        backend = GeminiVideoBackend(api_key="k", base_url="https://relay-b.example.com", transport_layer=layer)
        request = VideoGenerationRequest(
            prompt="p", output_path=tmp_path / "out.mp4", submitted_base_url="https://relay-a.example.com"
        )
        gone = {"error": {"code": 404, "message": "gone", "status": "NOT_FOUND"}}

        with capture_http() as router:
            router.route(host="relay-a.example.com").respond(404, json=gone)
            with pytest.raises(ResumeExpiredError):
                await backend.resume_video("operations/op-a", request)

        assert layer.seen
        assert all(url.startswith("https://relay-a.example.com/") for url in layer.seen)
        # Close each synchronous/asynchronous replay channel once; the SDK does not close externally supplied clients.
        assert layer.closed == 2
