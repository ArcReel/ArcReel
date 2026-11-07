"""GrokVideoBackend unit tests."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoAudioMode,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from lib.backends.providers import PROVIDER_GROK
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http


@contextmanager
def _video_download(url: str, content: bytes) -> Generator[respx.Route]:
    """Outbound final-video download stream: the base layer streams real httpx bytes; respx intercepts at transport level."""
    with capture_http() as router:
        yield router.get(url).mock(return_value=httpx.Response(200, content=content))


@pytest.fixture
def video_output_path(tmp_path: Path) -> Path:
    return tmp_path / "output.mp4"


class TestGrokVideoBackend:
    @patch("lib.backends.video_backends.grok.create_grok_client")
    def test_name_and_model(self, mock_create):
        from lib.backends.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert backend.name == PROVIDER_GROK
        assert backend.model == "grok-imagine-video"

    @patch("lib.backends.video_backends.grok.create_grok_client")
    def test_capabilities(self, mock_create):
        from lib.backends.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        # The channel supports reference images without a limit; each model entry supplies its limit.
        assert backend.video_capabilities.max_reference_images is None

    @patch("lib.backends.video_backends.grok.create_grok_client")
    def test_custom_model(self, mock_create):
        from lib.backends.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-2")
        assert backend.model == "grok-imagine-video-2"

    def test_missing_api_key_raises(self):
        from lib.backends.video_backends.grok import GrokVideoBackend

        with pytest.raises(ValueError, match="xAI API Key"):
            GrokVideoBackend(api_key=None)

    async def test_text_to_video(self, video_output_path: Path):
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 5

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=video_output_path,
                    aspect_ratio="16:9",
                    duration_seconds=5,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.provider == PROVIDER_GROK
            assert result.model == "grok-imagine-video"
            assert result.duration_seconds == 5
            assert result.video_path == video_output_path

            mock_video.generate.assert_awaited_once()
            call_kwargs = mock_video.generate.call_args[1]
            assert call_kwargs["prompt"] == "A cat walking"
            assert call_kwargs["model"] == "grok-imagine-video"
            assert call_kwargs["duration"] == 5
            assert call_kwargs["aspect_ratio"] == "16:9"
            assert call_kwargs["resolution"] == "720p"
            assert "image_url" not in call_kwargs

    @pytest.mark.parametrize("generate_audio", [True, False])
    async def test_audio_intent_reaches_sdk_and_settlement(self, video_output_path: Path, generate_audio: bool):
        """Pass the audio switch unchanged as SDK generate_audio; settlement records the sent value, with disabled audio recorded as silent."""
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 5

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)
        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=video_output_path,
                    duration_seconds=5,
                    generate_audio=generate_audio,
                )
                result = await backend.generate(request)

        assert mock_video.generate.call_args[1]["generate_audio"] is generate_audio
        assert result.generate_audio is generate_audio

    async def test_default_request_keeps_audio(self, video_output_path: Path):
        """Keep audio enabled when the request declares no audio intent: send True and record audio at settlement."""
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 5

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)
        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="A cat walking", output_path=video_output_path, duration_seconds=5
                )
                result = await backend.generate(request)

        assert mock_video.generate.call_args[1]["generate_audio"] is True
        assert result.generate_audio is True

    async def test_marks_resubmit_unsafe_before_opaque_provider_call(self, video_output_path: Path):
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(side_effect=RuntimeError("service unavailable"))
        mock_client = MagicMock()
        mock_client.video = mock_video
        resubmit_unsafe = MagicMock()

        with (
            patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client),
            bounded_poll_clock(),
        ):
            backend = GrokVideoBackend(api_key="test-key")
            request = VideoGenerationRequest(
                prompt="A cat walking",
                output_path=video_output_path,
                duration_seconds=5,
                on_provider_resubmit_unsafe=resubmit_unsafe,
            )

            with pytest.raises(RuntimeError, match="service unavailable"):
                await backend.generate(request)

        resubmit_unsafe.assert_called_once_with()
        assert mock_video.generate.await_count == 1

    async def test_image_to_video(self, video_output_path: Path, tmp_path: Path):
        from lib.backends.video_backends.grok import GrokVideoBackend

        image_path = tmp_path / "start.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = 8

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="Animate this scene",
                    output_path=video_output_path,
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
            (15, 15),  # Narrow integers directly.
            ("15.0", 15),  # Parse floating-point strings through float first.
            (7.8, 8),  # Round floats half-up, matching dashscope billing.
            (4.4, 4),  # Half-up: discard less than half a second.
            (0, 5),  # Zero falls back to requested duration.
            (-10, 5),  # Negative values fall back to requested duration.
            (0.3, 5),  # Values rounded to 0 also fall back, keeping the result positive.
            ("unknown", 5),  # Unparseable values fall back to requested duration.
            (float("inf"), 5),  # Overflow falls back to requested duration.
            (1e100, 5),  # Above the safe maximum, use requested duration to avoid DB Integer overflow.
            (86400.9, 5),  # Check raw values: fractions beyond 24h cannot round back within the limit.
            (None, 5),  # Missing values fall back to requested duration.
        ],
    )
    async def test_duration_narrowed_to_int_with_fallback(self, video_output_path: Path, raw_duration, expected):
        """SDK duration is untyped: narrow parseable numbers to int for actual billed duration; otherwise use requested duration."""
        from lib.backends.video_backends.grok import GrokVideoBackend

        mock_response = MagicMock()
        mock_response.url = "https://vidgen.x.ai/test/video.mp4"
        mock_response.duration = raw_duration

        mock_video = MagicMock()
        mock_video.generate = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.video = mock_video

        with patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")

            with _video_download(mock_response.url, b"fake-video-data"):
                request = VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=video_output_path,
                    duration_seconds=5,
                    resolution="720p",
                )

                result = await backend.generate(request)

            assert result.duration_seconds == expected


def _png(path: Path) -> Path:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return path


@contextmanager
def _grok_sdk(duration: int = 5) -> Generator[AsyncMock]:
    """Replace the SDK client and yield a ``video.generate`` mock for assertions on sent arguments."""
    mock_response = MagicMock()
    mock_response.url = "https://vidgen.x.ai/test/video.mp4"
    mock_response.duration = duration
    mock_client = MagicMock()
    mock_client.video.generate = AsyncMock(return_value=mock_response)
    with (
        patch("lib.backends.video_backends.grok.create_grok_client", return_value=mock_client),
        _video_download(mock_response.url, b"fake-video-data"),
    ):
        yield mock_client.video.generate


class TestGrokVideo15Models:
    """Capability declarations and request submission for grok-imagine-video-1.5 / 1.5-lite."""

    async def test_reference_audio_rejected_before_submit(self, tmp_path: Path):
        """The request body has no reference-audio field; reject it rather than discard it and submit normally."""
        from lib.backends.video_backends.grok import GrokVideoBackend

        audio = tmp_path / "voice.mp3"
        audio.write_bytes(b"ID3")
        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-1.5")
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(
                    VideoGenerationRequest(prompt="p", output_path=tmp_path / "o.mp4", reference_audio_files=[audio])
                )
        assert exc.value.code == "video_reference_audio_unsupported"
        generate.assert_not_awaited()

    @staticmethod
    def _effective(model: str):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        return builtin_video_capabilities_for_model("grok", model)

    def test_1_5_declares_last_frame_and_reference_images(self):
        caps = self._effective("grok-imagine-video-1.5")
        assert caps.text_to_video is True
        assert caps.first_frame is True
        assert caps.last_frame is True
        # The official reference-to-video page gives 1.5 a limit of 14 (classic 7), supplied by the entry.
        assert caps.max_reference_images == 14
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.audio_track is VideoAudioMode.CONTROLLABLE

    def test_1_5_lite_declares_neither_reference_images_nor_last_frame(self):
        """Official docs describe only text-to-video and image-to-video for lite; real reference-image calls return INVALID_ARGUMENT."""
        caps = self._effective("grok-imagine-video-1.5-lite")
        assert caps.text_to_video is True
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.max_reference_images == 0
        assert caps.audio_track is VideoAudioMode.CONTROLLABLE

    def test_classic_model_keeps_rejecting_last_frame(self):
        caps = self._effective("grok-imagine-video")
        assert caps.last_frame is False
        assert caps.max_reference_images == 7

    @pytest.mark.parametrize("model", ["grok-imagine-video-1.5", "grok-imagine-video-1.5-lite"])
    async def test_1080p_reaches_sdk(self, video_output_path: Path, tmp_path: Path, model: str):
        """Pass requested 1080p unchanged for text-to-video and image-to-video, together with model name and audio switch."""
        from lib.backends.video_backends.grok import GrokVideoBackend

        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model=model)
            result = await backend.generate(
                VideoGenerationRequest(
                    prompt="A cat walking",
                    output_path=video_output_path,
                    start_image=_png(tmp_path / "start.png"),
                    duration_seconds=5,
                    resolution="1080p",
                    generate_audio=False,
                )
            )

        kwargs = generate.call_args[1]
        assert kwargs["model"] == model
        assert kwargs["resolution"] == "1080p"
        assert kwargs["generate_audio"] is False
        assert kwargs["image_url"].startswith("data:image/png;base64,")
        assert result.model == model

    async def test_1_5_sends_end_image_as_last_frame(self, video_output_path: Path, tmp_path: Path):
        from lib.backends.video_backends.grok import GrokVideoBackend

        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-1.5")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="Dolly to the window",
                    output_path=video_output_path,
                    start_image=_png(tmp_path / "start.png"),
                    end_image=_png(tmp_path / "end.png"),
                    duration_seconds=8,
                    resolution="720p",
                )
            )

        kwargs = generate.call_args[1]
        assert kwargs["image_url"].startswith("data:image/png;base64,")
        assert kwargs["last_frame_url"].startswith("data:image/png;base64,")
        assert kwargs["resolution"] == "720p"

    @pytest.mark.parametrize(
        ("field", "code"),
        [("start_image", "video_start_image_unreadable"), ("reference_images", "video_reference_images_unreadable")],
    )
    async def test_missing_start_or_reference_image_fails_before_provider_call(
        self, video_output_path: Path, tmp_path: Path, field: str, code: str
    ):
        """Do not silently skip unreadable first/reference images: that would charge normally for a video that ignores the supplied images."""
        from lib.backends.video_backends.grok import GrokVideoBackend

        missing = tmp_path / "missing.png"
        value = [missing] if field == "reference_images" else missing
        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-1.5")
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="Dolly to the window",
                        output_path=video_output_path,
                        duration_seconds=8,
                        **{field: value},
                    )
                )

        assert exc.value.code == code
        generate.assert_not_awaited()

    async def test_missing_end_image_fails_before_provider_call(self, video_output_path: Path, tmp_path: Path):
        """Do not silently skip an unreadable last frame: that would charge normally without reaching the storyboard ending image."""
        from lib.backends.video_backends.grok import GrokVideoBackend

        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-1.5")
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="Dolly to the window",
                        output_path=video_output_path,
                        end_image=tmp_path / "missing.png",
                        duration_seconds=8,
                    )
                )

        assert exc.value.code == "video_end_image_unreadable"
        generate.assert_not_awaited()

    @pytest.mark.parametrize(
        ("model", "route"),
        [
            ("grok-imagine-video-1.5", "reference_images"),
            ("grok-imagine-video-1.5", "end_image"),
            ("grok-imagine-video", "reference_images"),
        ],
    )
    async def test_reference_route_rejects_1080p_before_provider_call(
        self, video_output_path: Path, tmp_path: Path, model: str, route: str
    ):
        """Reference-to-video (reference images or first plus last frame) officially tops out at 720p: reject 1080p before the paid call without consuming the paid window."""
        from lib.backends.video_backends.grok import GrokVideoBackend

        by_reference = route == "reference_images"
        resubmit_unsafe = MagicMock()
        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model=model)
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="The model from <IMAGE_0> walks in",
                        output_path=video_output_path,
                        duration_seconds=8,
                        resolution="1080p",
                        reference_images=[_png(tmp_path / "ref.png")] if by_reference else None,
                        start_image=None if by_reference else _png(tmp_path / "start.png"),
                        end_image=None if by_reference else _png(tmp_path / "end.png"),
                        on_provider_resubmit_unsafe=resubmit_unsafe,
                    )
                )

        assert exc.value.code == "video_reference_resolution_unsupported"
        assert exc.value.params["max_resolution"] == "720p"
        generate.assert_not_awaited()
        resubmit_unsafe.assert_not_called()

    async def test_classic_rejects_first_frame_with_reference_images_before_provider_call(
        self, video_output_path: Path, tmp_path: Path
    ):
        """Classic officially rejects a first frame combined with reference images: reject before the paid call instead of sending both."""
        from lib.backends.video_backends.grok import GrokClassicVideoBackend

        resubmit_unsafe = MagicMock()
        with _grok_sdk() as generate:
            backend = GrokClassicVideoBackend(api_key="test-key", model="grok-imagine-video")
            with pytest.raises(VideoCapabilityError) as exc:
                await backend.generate(
                    VideoGenerationRequest(
                        prompt="The model from <IMAGE_1> walks in",
                        output_path=video_output_path,
                        start_image=_png(tmp_path / "start.png"),
                        reference_images=[_png(tmp_path / "ref.png")],
                        duration_seconds=8,
                        resolution="720p",
                        on_provider_resubmit_unsafe=resubmit_unsafe,
                    )
                )

        assert exc.value.code == "video_reference_images_with_frames_unsupported"
        generate.assert_not_awaited()
        resubmit_unsafe.assert_not_called()

    async def test_1_5_sends_first_frame_with_reference_images(self, video_output_path: Path, tmp_path: Path):
        """On 1.5, first frame plus reference images means reference-to-video with a fixed first frame; send both."""
        from lib.backends.video_backends.grok import GrokMixedVideoBackend

        with _grok_sdk() as generate:
            backend = GrokMixedVideoBackend(api_key="test-key", model="grok-imagine-video-1.5")
            await backend.generate(
                VideoGenerationRequest(
                    prompt="The model from <IMAGE_1> walks in",
                    output_path=video_output_path,
                    start_image=_png(tmp_path / "start.png"),
                    reference_images=[_png(tmp_path / "ref.png")],
                    duration_seconds=8,
                    resolution="720p",
                )
            )

        kwargs = generate.call_args[1]
        assert kwargs["image_url"].startswith("data:image/png;base64,")
        assert len(kwargs["reference_image_urls"]) == 1

    @pytest.mark.parametrize("model", ["grok-imagine-video-1.5", "grok-imagine-video"])
    async def test_reference_route_at_720p_reaches_sdk(self, video_output_path: Path, tmp_path: Path, model: str):
        from lib.backends.video_backends.grok import GrokVideoBackend

        with _grok_sdk() as generate:
            backend = GrokVideoBackend(api_key="test-key", model=model)
            await backend.generate(
                VideoGenerationRequest(
                    prompt="The model from <IMAGE_0> walks in",
                    output_path=video_output_path,
                    reference_images=[_png(tmp_path / "ref.png")],
                    duration_seconds=8,
                    resolution="720p",
                )
            )

        kwargs = generate.call_args[1]
        assert kwargs["resolution"] == "720p"
        assert len(kwargs["reference_image_urls"]) == 1


@pytest.mark.parametrize("endpoint", ["grok-video-classic", "grok-video-mixed"])
async def test_endpoint_request_and_metering_ignore_name(endpoint, tmp_path):
    from lib.backends.video_backends.grok import GROK_BACKENDS

    request = VideoGenerationRequest(prompt="test", output_path=tmp_path / "out.mp4", duration_seconds=5)
    for model in ("opaque-id", "grok-imagine-video"):
        with _grok_sdk() as generate:
            backend = GROK_BACKENDS[endpoint](api_key="test", model=model)
            result = await backend.generate(request)
        sent = generate.await_args.kwargs
        assert (sent["model"], sent["duration"], sent["generate_audio"]) == (model, 5, True)
        usage = backend.metered_usage(model, request, result)
        assert usage.quantities["output_seconds"] == 5
        assert usage.dimensions["audio"] is True
        assert result.video_path.read_bytes() == b"fake-video-data"


@pytest.mark.parametrize("endpoint", ["grok-video-classic", "grok-video-mixed"])
async def test_mixed_inputs_follow_endpoint_after_model_rename(endpoint, tmp_path):
    from lib.backends.video_backends.grok import GROK_BACKENDS

    image = tmp_path / "in.png"
    image.write_bytes(b"image")
    request = VideoGenerationRequest(
        prompt="test", output_path=tmp_path / "out.mp4", resolution="720p", start_image=image, reference_images=[image]
    )
    for model in ("opaque-id", "grok-imagine-video-1.5"):
        with _grok_sdk() as generate:
            backend = GROK_BACKENDS[endpoint](api_key="test", model=model)
            if endpoint == "grok-video-classic":
                with pytest.raises(VideoCapabilityError) as exc:
                    await backend.generate(request)
                assert exc.value.code == "video_reference_images_with_frames_unsupported"
                generate.assert_not_awaited()
            else:
                result = await backend.generate(request)
                sent = generate.await_args.kwargs
                assert sent["image_url"].startswith("data:image/png;base64,")
                assert len(sent["reference_image_urls"]) == 1
                assert result.video_path.read_bytes() == b"fake-video-data"
