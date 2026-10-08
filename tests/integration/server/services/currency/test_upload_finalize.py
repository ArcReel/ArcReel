"""用户上传视频落盘接随包 ffmpeg：改名到位前做 faststart。"""

from __future__ import annotations

from pathlib import Path

from lib.infra.mp4_faststart import Mp4Layout, read_mp4_layout
from server.services.currency.upload_finalize import save_uploaded_video_stream
from tests.factories import make_test_video


async def test_uploaded_moov_last_video_lands_as_faststart(tmp_path: Path):
    source = tmp_path / "source.mp4"
    make_test_video(source)
    assert read_mp4_layout(source) == Mp4Layout(major_brand=b"isom", moov_first=False)
    target = tmp_path / "videos" / "scene_X.mp4"

    with source.open("rb") as upload:
        await save_uploaded_video_stream(upload, target, max_bytes=64 * 1024 * 1024)

    layout = read_mp4_layout(target)
    assert layout is not None
    assert layout.moov_first
    assert sorted(target.parent.iterdir()) == [target]
