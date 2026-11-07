"""OpenAIVideoBackend unit tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import InternalServerError
from openai.types.video_create_error import VideoCreateError

from arcreel_market_core.video_backend_contract import VideoCapabilityError, VideoGenerationRequest
from lib.backends.providers import PROVIDER_OPENAI
from tests.fakes import (
    blocking_file_read_gate,
    bounded_poll_clock,
    captured_openai_clients,
    captured_provider_job_ids,
)
from tests.http_capture import capture_http, only_request
from tests.unit.lib.backends.write_cancel_gate import GatedWritePath, WriteGate, cancel_during_write


def _make_mock_video(status="completed", seconds="8", video_id="vid_123"):
    """Construct a mock Video response."""
    video = MagicMock()
    video.id = video_id
    video.status = status
    video.seconds = seconds
    video.error = None
    return video


def _make_mock_content(data: bytes = b"fake-video-data"):
    """Construct a mock download_content response."""
    content = MagicMock()
    content.content = data
    return content


def _stub_client_completed(client: AsyncMock, *, seconds="8", video_id="vid_123", data=b"fake-video-data"):
    """Common stub: create→queued, retrieve→completed, download_content→data."""
    client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued", seconds=seconds, video_id=video_id))
    client.videos.retrieve = AsyncMock(
        return_value=_make_mock_video(status="completed", seconds=seconds, video_id=video_id)
    )
    client.videos.download_content = AsyncMock(return_value=_make_mock_content(data))


class TestOpenAIVideoBackend:
    def test_name_and_model(self):
        with captured_openai_clients():
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            assert backend.name == PROVIDER_OPENAI
            assert backend.model == "sora-2"

    def test_custom_model(self):
        with captured_openai_clients():
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2-pro")
            assert backend.model == "sora-2-pro"

    def test_capabilities(self):
        with captured_openai_clients():
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            assert backend.video_capabilities.max_reference_images == 1

    @pytest.mark.parametrize(
        ("field", "code"),
        [("end_image", "video_last_frame_unsupported"), ("reference_audio_files", "video_reference_audio_unsupported")],
    )
    async def test_unsendable_input_rejected_before_submit(self, tmp_path: Path, field: str, code: str):
        """The Sora request body has no last-frame/reference-audio fields; reject them rather than discard them and submit normally."""
        mock_client = AsyncMock()
        _stub_client_completed(mock_client)
        source = tmp_path / "input.bin"
        source.write_bytes(b"x")
        value = [source] if field == "reference_audio_files" else source

        with captured_openai_clients(mock_client):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", **{field: value})
                )
        assert exc.value.code == code
        mock_client.videos.create.assert_not_awaited()

    @pytest.mark.parametrize(
        ("kind", "code"),
        [
            ("missing_start", "video_start_image_unreadable"),
            ("missing_ref", "video_reference_images_unreadable"),
            ("two_inputs", "video_reference_images_exceeded"),
        ],
    )
    async def test_unusable_image_inputs_rejected_before_submit(self, tmp_path: Path, kind: str, code: str):
        """Do not silently skip missing images or discard images beyond the single slot: normal submission would charge for a video ignoring supplied images."""
        mock_client = AsyncMock()
        _stub_client_completed(mock_client)
        real = tmp_path / "real.png"
        real.write_bytes(b"\x89PNG")
        extra = tmp_path / "extra.png"
        extra.write_bytes(b"\x89PNG")
        missing = tmp_path / "missing.png"
        inputs = {
            "missing_start": {"start_image": missing},
            "missing_ref": {"reference_images": [missing]},
            "two_inputs": {"start_image": real, "reference_images": [extra]},
        }[kind]

        with captured_openai_clients(mock_client):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", **inputs))
        assert exc.value.code == code
        mock_client.videos.create.assert_not_awaited()

    async def test_text_to_video(self, tmp_path: Path):
        video_data = b"mp4-video-content"
        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds="8", data=video_data)

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
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
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
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

    async def test_start_image_encoding_keeps_event_loop_running(self, tmp_path: Path, monkeypatch):
        """Offload first-frame byte reads for multipart upload to a thread; otherwise the read blocks the event loop."""
        start_image = tmp_path / "start.png"
        start_image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds="4")

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            request = VideoGenerationRequest(
                prompt="Animate this",
                output_path=tmp_path / "output.mp4",
                start_image=start_image,
                duration_seconds=4,
            )
            with blocking_file_read_gate(monkeypatch, start_image) as gate:
                task = asyncio.create_task(backend.generate(request))
                await gate.wait_until_read_started()
                gate.release()
                await task
                gate.assert_read_was_offloaded()

        ref = mock_client.videos.create.call_args[1]["input_reference"]
        assert ref[0] == "start.png"
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
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="Bad content",
                output_path=output_path,
            )
            with pytest.raises(RuntimeError, match="Sora 视频生成失败"):
                await backend.generate(request)

        # Failure should raise during polling, without entering download.
        mock_client.videos.download_content.assert_not_called()

    async def test_duration_passthrough(self, tmp_path: Path):
        """Pass every duration value unchanged to the SDK without _map_duration rewriting it."""
        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds="6")

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")

            for seconds in [3, 4, 5, 6, 7, 8, 10, 12, 15, 20]:
                output_path = tmp_path / f"output_{seconds}.mp4"
                request = VideoGenerationRequest(
                    prompt="test",
                    output_path=output_path,
                    duration_seconds=seconds,
                )
                await backend.generate(request)
                call_kwargs = mock_client.videos.create.call_args[1]
                assert call_kwargs["seconds"] == str(seconds), f"duration={seconds}"

    async def test_video_seconds_none_fallback(self, tmp_path: Path):
        """When the API returns video.seconds=None, fall back to requested duration."""
        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds=None)

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=6,
            )
            result = await backend.generate(request)

        # Request 6 seconds, pass it through, and retain requested value 6 on fallback.
        assert result.duration_seconds == 6

    async def test_size_mapping(self, tmp_path: Path):
        mock_client = AsyncMock()
        _stub_client_completed(mock_client, seconds="4")

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")

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
        """Retry content download separately after a 502 download failure instead of creating another task."""
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
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=8,
            )
            result = await backend.generate(request)

        assert result.video_path == output_path
        assert output_path.read_bytes() == b"video-data"
        # Call create only once; download failure must not create another task.
        assert mock_client.videos.create.call_count == 1
        # Call download_content 3 times (2 failures + 1 success).
        assert mock_client.videos.download_content.call_count == 3

    async def test_content_download_all_retries_exhausted(self, tmp_path: Path):
        """Raise after exhausting all content-download retries without regenerating the video."""
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
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=8,
            )
            # Exhausting the shared artifact-download budget raises RuntimeError carrying the last original error.
            with pytest.raises(RuntimeError, match="Failed to resolve Vertex video URL"):
                await backend.generate(request)

        # Create only 1 task even when download retries are exhausted.
        assert mock_client.videos.create.call_count == 1

    async def test_content_download_non_retryable_error_fails_immediately(self, tmp_path: Path):
        """Nonretryable download errors (such as 4xx) fail immediately without wasting backoff time."""
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

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=8,
            )
            with pytest.raises(AuthenticationError):
                await backend.generate(request)

        # Nonretryable error: raise after 1 download call without backoff retries.
        assert mock_client.videos.download_content.call_count == 1
        assert not output_path.exists()

    async def test_polls_until_completed_for_nonstandard_status(self, tmp_path: Path):
        """Keep polling until completed when an OpenAI-compatible gateway returns nonstandard status values (such as NOT_START / running).

        The SDK built-in poll recognizes only 4 standard states; nonstandard states make it exit early and download an unready task
        (400 Task is not completed yet), so the backend must not rely on it.
        """
        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        # Simulate nonstandard states: NOT_START → running → in_progress → completed.
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
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=8,
            )
            result = await backend.generate(request)

        # Poll 4 times (3 incomplete + 1 complete) before obtaining the result.
        assert mock_client.videos.retrieve.call_count == 4
        # Download only once, after completion.
        assert mock_client.videos.download_content.call_count == 1
        assert result.video_path == output_path

    async def test_first_retrieve_completed_skips_polling_sleep(self, tmp_path: Path):
        """If the first retrieve returns completed, query only once and download without waiting poll_interval.

        _poll_until_complete always uses poll_with_retry; its loop queries before waiting. A terminal state
        on the first query returns immediately without sleeping. The recorded query count establishes that order.
        """
        retrieved: list[str] = []

        async def _retrieve(video_id: str):
            retrieved.append(video_id)
            return _make_mock_video(status="completed", seconds="8")

        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = _retrieve
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(b"v"))

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="test",
                output_path=output_path,
                duration_seconds=8,
            )
            await backend.generate(request)

        # First query is completed: poll_with_retry returns after one retrieve without another query.
        assert retrieved == ["vid_123"]
        assert output_path.read_bytes() == b"v"

    async def test_first_retrieve_failed_raises_before_sleep(self, tmp_path: Path):
        """A first retrieve returning failed raises immediately: poll_with_retry checks is_failed on the first query and raises without sleeping."""
        err = MagicMock()
        err.message = "moderation rejected"
        failed = _make_mock_video(status="failed")
        failed.error = err

        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = AsyncMock(return_value=failed)
        mock_client.videos.download_content = AsyncMock()

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="bad",
                output_path=output_path,
                duration_seconds=8,
            )
            with pytest.raises(RuntimeError, match="Sora 视频生成失败"):
                await backend.generate(request)

        assert mock_client.videos.retrieve.call_count == 1
        mock_client.videos.download_content.assert_not_called()
        assert not output_path.exists()

    async def test_polls_failed_status_raises_without_download(self, tmp_path: Path):
        """status='failed' during polling raises immediately without downloading."""
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
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "output.mp4"
            request = VideoGenerationRequest(
                prompt="bad",
                output_path=output_path,
                duration_seconds=8,
            )
            with pytest.raises(RuntimeError, match="Sora 视频生成失败"):
                await backend.generate(request)

        mock_client.videos.download_content.assert_not_called()

    async def test_resume_video_polls_existing_job(self, tmp_path: Path):
        """resume_video only polls and downloads; it does not call videos.create (ADR 0007)."""
        video_data = b"resumed-content"
        mock_client = AsyncMock()
        # Do not stub create; fail if it is called.
        mock_client.videos.create = AsyncMock(side_effect=AssertionError("resume 不应调 create"))
        mock_client.videos.retrieve = AsyncMock(return_value=_make_mock_video(status="completed", seconds="8"))
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(video_data))

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "out.mp4"
            request = VideoGenerationRequest(
                prompt="resumed", output_path=output_path, aspect_ratio="9:16", duration_seconds=8
            )
            result = await backend.resume_video("vid_existing", request)

        mock_client.videos.create.assert_not_called()
        mock_client.videos.retrieve.assert_called_with("vid_existing")
        assert result.video_path == output_path
        assert output_path.read_bytes() == video_data

    async def test_download_write_settles_through_cancellation(self, tmp_path: Path):
        """Final-video bytes are available: cancellation during disk writing settles the write and is suppressed; subsequent faststart and artifact commit proceed normally."""
        mock_client = AsyncMock()
        mock_client.videos.retrieve = AsyncMock(return_value=_make_mock_video(status="completed", seconds="8"))
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(b"resumed-content"))
        gate = WriteGate()
        output_path = GatedWritePath(tmp_path / "out.mp4").attach(gate)
        written = tmp_path / "out.mp4"

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            request = VideoGenerationRequest(
                prompt="resumed", output_path=output_path, aspect_ratio="9:16", duration_seconds=8
            )
            steps = await cancel_during_write(lambda: backend.resume_video("vid_existing", request), gate)

        assert steps == ["follow-up"]
        assert written.read_bytes() == b"resumed-content"

    async def test_poll_recognizes_expired_status(self, tmp_path: Path):
        """retrieve returning status='expired' raises ResumeExpiredError instead of waiting out max_wait."""
        from arcreel_market_core.video_backend_contract import ResumeExpiredError

        mock_client = AsyncMock()
        expired_video = _make_mock_video(status="expired", video_id="vid_exp")
        mock_client.videos.retrieve = AsyncMock(return_value=expired_video)

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            request = VideoGenerationRequest(
                prompt="x", output_path=tmp_path / "out.mp4", aspect_ratio="9:16", duration_seconds=8
            )
            with pytest.raises(ResumeExpiredError) as ei:
                await backend.resume_video("vid_exp", request)
            assert ei.value.job_id == "vid_exp"
            assert ei.value.provider == PROVIDER_OPENAI

    async def test_is_openai_not_found_no_loose_string_match(self):
        """Do not fall back to "not found" / "expired" substrings, which can misclassify business strings; recognize only structured 404."""
        from lib.backends.video_backends.openai import _is_openai_not_found

        assert _is_openai_not_found(RuntimeError("file not found in storage")) is False
        assert _is_openai_not_found(RuntimeError("session expired but task is fine")) is False
        # Still recognize status_code=404.
        exc = RuntimeError("any")
        exc.status_code = 404
        assert _is_openai_not_found(exc) is True

    async def test_resume_video_not_found_raises_resume_expired(self, tmp_path: Path):
        """A nonexistent/expired job raises ResumeExpiredError (the [resume_expired] path)."""
        from openai import NotFoundError

        from arcreel_market_core.video_backend_contract import ResumeExpiredError

        mock_client = AsyncMock()
        not_found = NotFoundError(
            message="video not found", response=MagicMock(status_code=404), body={"error": "not_found"}
        )
        mock_client.videos.retrieve = AsyncMock(side_effect=not_found)

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            request = VideoGenerationRequest(
                prompt="x", output_path=tmp_path / "out.mp4", aspect_ratio="9:16", duration_seconds=8
            )
            with pytest.raises(ResumeExpiredError) as ei:
                await backend.resume_video("vid_expired", request)
            assert ei.value.job_id == "vid_expired"
            assert ei.value.provider == PROVIDER_OPENAI

    async def test_generate_expired_status_raises_runtime_error_not_resume_expired(self, tmp_path: Path):
        """status='expired' on the generate path raises RuntimeError, not ResumeExpiredError.

        Fresh submission must not carry [resume_expired] semantics; those apply only to resuming after worker restart.
        """
        from arcreel_market_core.video_backend_contract import ResumeExpiredError

        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued", video_id="vid_new"))
        mock_client.videos.retrieve = AsyncMock(return_value=_make_mock_video(status="expired", video_id="vid_new"))

        with (
            captured_openai_clients(mock_client),
            bounded_poll_clock(),
        ):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            request = VideoGenerationRequest(
                prompt="x", output_path=tmp_path / "out.mp4", aspect_ratio="9:16", duration_seconds=8
            )
            with pytest.raises(RuntimeError) as ei:
                await backend.generate(request)
            assert "expired" in str(ei.value).lower()
            assert not isinstance(ei.value, ResumeExpiredError), "generate 路径不应抛 ResumeExpiredError"


class TestProxyStatusSynonyms:
    """OpenAI-compatible proxy gateways (NewAPI family) forward underlying vendor status strings for non-Sora models.

    If terminal-state detection recognizes only Sora-documented literals, ready tasks keep polling until max_wait expires:
    users see failure even though the provider has a completed, billed video.
    """

    @pytest.mark.parametrize("proxy_status", ["succeeded", "success", "SUCCEEDED", "  succeeded  "])
    async def test_success_synonyms_finish_polling_and_download(self, tmp_path: Path, proxy_status: str):
        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = AsyncMock(return_value=_make_mock_video(status=proxy_status))
        mock_client.videos.download_content = AsyncMock(return_value=_make_mock_content(b"v"))

        with bounded_poll_clock(), captured_openai_clients(mock_client):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            output_path = tmp_path / "out.mp4"
            result = await backend.generate(
                VideoGenerationRequest(prompt="p", output_path=output_path, duration_seconds=8)
            )

        assert mock_client.videos.retrieve.call_count == 1
        mock_client.videos.download_content.assert_awaited_once()
        assert result.video_path == output_path
        assert output_path.read_bytes() == b"v"

    @pytest.mark.parametrize("proxy_status", ["error", "fail", "FAILED", "canceled"])
    async def test_failure_synonyms_raise_immediately(self, tmp_path: Path, proxy_status: str):
        err = MagicMock()
        err.message = "upstream rejected"
        failed = _make_mock_video(status=proxy_status)
        failed.error = err

        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = AsyncMock(return_value=failed)
        mock_client.videos.download_content = AsyncMock()

        with bounded_poll_clock(), captured_openai_clients(mock_client):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            with pytest.raises(RuntimeError, match="Sora 视频生成失败"):
                await backend.generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=8)
                )

        assert mock_client.videos.retrieve.call_count == 1
        mock_client.videos.download_content.assert_not_awaited()

    async def test_uppercase_expired_still_splits_generate_and_resume(self, tmp_path: Path):
        """Uppercase EXPIRED also counts as expiry: generate raises RuntimeError, resume raises ResumeExpiredError."""
        from arcreel_market_core.video_backend_contract import ResumeExpiredError

        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued", video_id="vid_new"))
        mock_client.videos.retrieve = AsyncMock(return_value=_make_mock_video(status="EXPIRED", video_id="vid_new"))

        with bounded_poll_clock(), captured_openai_clients(mock_client):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            request = VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=8)

            with pytest.raises(RuntimeError) as ei:
                await backend.generate(request)
            assert not isinstance(ei.value, ResumeExpiredError)

            with pytest.raises(ResumeExpiredError) as resume_ei:
                await backend.resume_video("vid_new", request)
            assert resume_ei.value.job_id == "vid_new"


class TestFailureMessage:
    """Store the failure reason as readable text in task.error_message; users read that sentence in the task panel."""

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            # Native SDK shape: a model with code / message; use only message.
            (VideoCreateError(code="moderation_blocked", message="content policy"), "content policy"),
            # Bare dict / string forwarded by proxy gateways.
            ({"code": 500, "message": "upstream down"}, "upstream down"),
            ({"code": "billing_hard_limit_reached"}, "billing_hard_limit_reached"),
            # Numeric error codes are reasons too; do not discard them for not being strings.
            ({"code": 500}, "500"),
            ("boom", "boom"),
            # A gateway supplying status but no error cannot explain the reason, but must not write "None".
            (None, "unknown"),
            # Unknown shape: report that the reason is unknown instead of exposing the object repr.
            ({"detail": "internal"}, "unknown"),
            (SimpleNamespace(), "unknown"),
        ],
    )
    async def test_provider_reason_reaches_error_message(self, tmp_path: Path, error, expected: str):
        failed = _make_mock_video(status="failed")
        failed.error = error

        mock_client = AsyncMock()
        mock_client.videos.create = AsyncMock(return_value=_make_mock_video(status="queued"))
        mock_client.videos.retrieve = AsyncMock(return_value=failed)
        mock_client.videos.download_content = AsyncMock()

        with bounded_poll_clock(), captured_openai_clients(mock_client):
            from lib.backends.video_backends.openai import OpenAIVideoBackend

            backend = OpenAIVideoBackend(api_key="test-key", model="sora-2")
            with pytest.raises(RuntimeError) as ei:
                await backend.generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=8)
                )

        # Exact assertion: persist error text unchanged in task.error_message, without internal-type repr.
        assert str(ei.value) == f"Sora 视频生成失败: {expected}"


_RELAY_A = "https://relay-a.example.com/v1"
_RELAY_B = "https://relay-b.example.com/v1"


def _video_json(video_id: str, status: str) -> dict:
    return {
        "id": video_id,
        "object": "video",
        "model": "sora-2",
        "status": status,
        "progress": 100 if status == "completed" else 0,
        "seconds": "8",
        "size": "720x1280",
        "created_at": 1,
    }


class TestSubmittedBaseUrlReplay:
    """OpenAI video protocol under a custom provider: persist the actual request host at submission and use it for polling/retrieval on resume.

    Real SDK client; respx intercepts the transport.
    """

    async def test_generate_persists_request_base_url(self, tmp_path: Path):
        from lib.backends.video_backends.openai import OpenAIVideoBackend

        with capture_http() as router, bounded_poll_clock(), captured_provider_job_ids() as persisted:
            router.post(f"{_RELAY_A}/videos").mock(
                return_value=httpx.Response(200, json=_video_json("vid-a", "queued"))
            )
            router.get(f"{_RELAY_A}/videos/vid-a").mock(
                return_value=httpx.Response(200, json=_video_json("vid-a", "completed"))
            )
            router.get(f"{_RELAY_A}/videos/vid-a/content").mock(return_value=httpx.Response(200, content=b"mp4"))

            await OpenAIVideoBackend(api_key="k", model="sora-2", base_url=f"{_RELAY_A}/").generate(
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=8, task_id="db-task-a"
                )
            )

        assert [(r["job_id"], r["base_url"]) for r in persisted] == [("vid-a", _RELAY_A)]

    async def test_resume_polls_and_downloads_from_submitted_base_url(self, tmp_path: Path):
        # Submit to host A, then configure B before resume: task/video exist only on A; use current credentials.
        from lib.backends.video_backends.openai import OpenAIVideoBackend

        with capture_http() as router, bounded_poll_clock():
            poll = router.get(f"{_RELAY_A}/videos/vid-a").mock(
                return_value=httpx.Response(200, json=_video_json("vid-a", "completed"))
            )
            content = router.get(f"{_RELAY_A}/videos/vid-a/content").mock(
                return_value=httpx.Response(200, content=b"resumed")
            )
            current = router.get(url__regex=r"^https://relay-b\.example\.com/").mock(
                return_value=httpx.Response(404, json={"error": {"message": "not found"}})
            )

            result = await OpenAIVideoBackend(api_key="k", model="sora-2", base_url=_RELAY_B).resume_video(
                "vid-a",
                VideoGenerationRequest(
                    prompt="p", output_path=tmp_path / "out.mp4", duration_seconds=8, submitted_base_url=_RELAY_A
                ),
            )

            assert only_request(poll).headers["Authorization"] == "Bearer k"
            assert only_request(content).headers["Authorization"] == "Bearer k"
            assert current.call_count == 0

        assert result.task_id == "vid-a"
        assert (tmp_path / "out.mp4").read_bytes() == b"resumed"
