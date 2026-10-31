"""成片联系表：对成片文件 best-effort 抽帧拼表；失败不阻断出片。"""

from __future__ import annotations

from pathlib import Path

from lib.final_cut.sheet import build_final_cut_sheet
from tests.factories import make_signal_clip


async def test_signal_clip_yields_a_sheet_file(tmp_path: Path):
    video = tmp_path / "final_cut.mp4"
    make_signal_clip(video)
    sheet_file = tmp_path / "final_cut.without_narration.no_subtitles_sheet.jpg"

    assert await build_final_cut_sheet(video, sheet_file, label="final_cut.no_subtitles", version=1) is True

    data = sheet_file.read_bytes()
    assert data.startswith(b"\xff\xd8")
    assert len(data) > 1024


async def test_unreadable_video_leaves_no_sheet(tmp_path: Path):
    sheet_file = tmp_path / "final_cut_sheet.jpg"

    assert await build_final_cut_sheet(tmp_path / "missing.mp4", sheet_file, label="final_cut", version=1) is False
    assert not sheet_file.exists()
