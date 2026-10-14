"""上传内容落盘：视频改名到位前接随包 ffmpeg 做 faststart；内存字节原子写入目标路径。"""

from __future__ import annotations

from pathlib import Path

from lib.infra.mp4_faststart import Mp4Layout, read_mp4_layout
from server.services.currency.upload_finalize import save_uploaded_bytes, save_uploaded_video_stream
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


async def test_uploaded_bytes_land_at_target_without_temp_residue(tmp_path: Path):
    target = tmp_path / "storyboards" / "scene_X.png"
    target.parent.mkdir()
    target.write_bytes(b"old")

    await save_uploaded_bytes(b"new-image-bytes", target)

    assert target.read_bytes() == b"new-image-bytes"
    assert sorted(target.parent.iterdir()) == [target]
