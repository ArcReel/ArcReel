"""OpenAIVideoBackend 与随包 ffmpeg：自管落盘的成片经真实重封装变成 faststart（OpenAI SDK 用替身）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from arcreel_market_core.video_backend_contract import VideoGenerationRequest
from lib.infra.mp4_faststart import Mp4Layout, read_mp4_layout
from tests.factories import make_test_video_with_audio_tail
from tests.fakes import bounded_poll_clock, captured_openai_clients


def _video(status: str) -> MagicMock:
    video = MagicMock()
    video.id = "vid_123"
    video.status = status
    video.seconds = "8"
    video.error = None
    return video


async def test_generated_video_lands_as_faststart(tmp_path: Path):
    """OpenAI 不经 download_video，写完成片后自己补 faststart：落盘的 mp4 moov 在前。"""
    source = tmp_path / "provider.mp4"
    make_test_video_with_audio_tail(source)  # mp4 封装器缺省把 moov 写在末尾
    assert read_mp4_layout(source) == Mp4Layout(major_brand=b"isom", moov_first=False)
    payload = source.read_bytes()

    client = AsyncMock()
    client.videos.create = AsyncMock(return_value=_video("queued"))
    client.videos.retrieve = AsyncMock(return_value=_video("completed"))
    client.videos.download_content = AsyncMock(return_value=MagicMock(content=payload))

    output = tmp_path / "out.mp4"
    with captured_openai_clients(client), bounded_poll_clock():
        from lib.backends.video_backends.openai import OpenAIVideoBackend

        await OpenAIVideoBackend(api_key="test-key").generate(
            VideoGenerationRequest(prompt="A cat", output_path=output, duration_seconds=8)
        )

    assert read_mp4_layout(output) == Mp4Layout(major_brand=b"isom", moov_first=True)
