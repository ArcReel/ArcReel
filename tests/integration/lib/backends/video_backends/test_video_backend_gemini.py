"""GeminiVideoBackend with bundled ffmpeg: self-managed downloads become faststart through real remuxing, with substituted genai SDK."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.infra.mp4_faststart import Mp4Layout, read_mp4_layout
from tests.factories import make_test_video_with_audio_tail


def _done_operation() -> MagicMock:
    video = MagicMock()
    video.uri = "gs://bucket/video.mp4"
    generated = MagicMock()
    generated.video = video
    operation = MagicMock()
    operation.done = True
    operation.error = None
    operation.response.generated_videos = [generated]
    return operation


async def test_generated_video_lands_as_faststart(tmp_path: Path):
    """Gemini bypasses download_video, adding faststart after final renames; saved mp4 has leading moov."""
    source = tmp_path / "provider.mp4"
    make_test_video_with_audio_tail(source)  # mp4 muxers default to trailing moov.
    assert read_mp4_layout(source) == Mp4Layout(major_brand=b"isom", moov_first=False)
    payload = source.read_bytes()

    def download(*, file, destination=None, config=None):
        destination.write(payload)

    with patch("google.genai"), patch("google.genai.types"):
        from lib.backends.video_backends.gemini import GeminiVideoBackend

        backend = GeminiVideoBackend(backend_type="aistudio", api_key="test-key")
    backend._client = MagicMock()
    backend._client.aio.models.generate_videos = AsyncMock(return_value=_done_operation())
    backend._client.files.download.side_effect = download

    output = tmp_path / "out.mp4"
    await backend.generate(VideoGenerationRequest(prompt="a cat", output_path=output, duration_seconds=8))

    assert read_mp4_layout(output) == Mp4Layout(major_brand=b"isom", moov_first=True)
