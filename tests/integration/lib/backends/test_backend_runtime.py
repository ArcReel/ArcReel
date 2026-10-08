"""产物下载接随包 ffmpeg：视频在下载预算之外做 faststart。"""

from __future__ import annotations

from pathlib import Path

import httpx

from lib.backends.backend_runtime import download_video, faststart_video_artifact, stream_to_file
from lib.infra.mp4_faststart import Mp4Layout, read_mp4_layout
from tests.factories import make_test_video
from tests.http_capture import capture_http

_LIMIT = 64 * 1024 * 1024


def _moov_last_video(tmp_path: Path) -> bytes:
    source = tmp_path / "source.mp4"
    make_test_video(source)
    assert read_mp4_layout(source) == Mp4Layout(major_brand=b"isom", moov_first=False)
    return source.read_bytes()


async def test_downloaded_moov_last_video_lands_as_faststart(tmp_path: Path):
    body = _moov_last_video(tmp_path)
    output = tmp_path / "videos" / "scene_E1S01.mp4"
    output.parent.mkdir()

    with capture_http() as router:
        router.get("http://203.0.113.7/a.mp4").mock(return_value=httpx.Response(200, content=body))
        await download_video("http://203.0.113.7/a.mp4", output, label="test")

    layout = read_mp4_layout(output)
    assert layout is not None
    assert layout.moov_first
    assert sorted(output.parent.iterdir()) == [output]


async def test_stream_to_file_stores_bytes_verbatim_inside_the_download_budget(tmp_path: Path):
    """下载预算内的落盘只写原样字节：重封装若跑在预算里，预算到期会把已下载成功的产物一并作废。"""
    body = _moov_last_video(tmp_path)
    output = tmp_path / "out.mp4"

    with capture_http() as router:
        router.get("https://cdn.test/a.mp4").mock(return_value=httpx.Response(200, content=body))
        async with httpx.AsyncClient() as client:
            await stream_to_file(client, "https://cdn.test/a.mp4", output, max_bytes=_LIMIT)

    assert output.read_bytes() == body


async def test_non_video_artifact_is_left_untouched(tmp_path: Path):
    """图片等非 ISO BMFF 扩展名不进 faststart，即使内容恰好是 moov 在后的视频。"""
    body = _moov_last_video(tmp_path)
    output = tmp_path / "out.bin"
    output.write_bytes(body)

    await faststart_video_artifact(output)

    assert output.read_bytes() == body
