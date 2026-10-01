"""带旁白与烧入字幕的成片：素材层取自呈现模型，用随包 ffmpeg 真实渲染后检查画面与声音。

共用项目的视频是没有音轨的黑屏、旁白配音是正弦音，所以画面里有亮像素即烧入了字幕，音频有声即混入了旁白。
"""

from __future__ import annotations

import subprocess
from array import array
from pathlib import Path

from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.final_cut.service import FinalCutService
from lib.infra.ffmpeg import ffmpeg_executable
from lib.project.project_manager import ProjectManager
from server.services.presentation.timeline_units import TimelineUnitMaterials
from tests.integration.server.services.presentation.timeline_render_support import edited_timeline, setup_project

_WIDTH, _HEIGHT = 1080, 1920
_SAMPLE_RATE = 8000


def _service(pm: ProjectManager) -> FinalCutService:
    return FinalCutService(pm, unit_materials=TimelineUnitMaterials(pm))


def _decode(path: Path, *args: str) -> bytes:
    return subprocess.run(
        [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-i", str(path), *args, "-"],
        capture_output=True,
        check=True,
    ).stdout


def _bright_pixels(path: Path, at: float) -> tuple[int, int]:
    """``at`` 秒那一帧上半幅、下半幅的亮像素数。"""
    frame = _decode(path, "-ss", f"{at}", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray")
    assert len(frame) == _WIDTH * _HEIGHT
    half = _WIDTH * _HEIGHT // 2
    return sum(value > 200 for value in frame[:half]), sum(value > 200 for value in frame[half:])


def _peak(path: Path, start: float, end: float) -> int:
    """音频在 ``[start, end)`` 秒内的峰值（16 位采样）。"""
    samples = array("h", _decode(path, "-vn", "-ac", "1", "-ar", str(_SAMPLE_RATE), "-f", "s16le"))
    window = samples[round(start * _SAMPLE_RATE) : round(end * _SAMPLE_RATE)]
    return max((abs(sample) for sample in window), default=0)


async def test_burned_and_clean_final_cuts_are_separate_artifacts_and_only_one_shows_subtitles(
    tmp_path: Path,
) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)
    service = _service(pm)

    burned = await service.render("demo", timeline_id, narration="without_narration", subtitles="burned_subtitles")
    clean = await service.render("demo", timeline_id, narration="without_narration", subtitles="no_subtitles")

    assert burned.artifact_path == f"renders/episode_1/{timeline_id}/final_cut.without_narration.burned_subtitles.mp4"
    assert clean.artifact_path == f"renders/episode_1/{timeline_id}/final_cut.without_narration.no_subtitles.mp4"
    for rendered in (burned, clean):
        assert (rendered.version, rendered.acceptance.expected_duration) == (1, 3.0)
        status = await service.status("demo", timeline_id, narration=rendered.narration, subtitles=rendered.subtitles)
        assert status.status is ArtifactStatus.CURRENT
    # 不带旁白版本的字幕按源素材时间显示：0.5 秒处是 S01 的「旁白一句」，烧在画面下方。
    upper, lower = _bright_pixels(project_path / burned.artifact_path, 0.5)
    assert (upper, lower > 500) == (0, True)
    assert _bright_pixels(project_path / clean.artifact_path, 0.5) == (0, 0)


async def test_narrated_final_cut_mixes_each_narration_from_its_carrying_clip_and_cuts_it_at_the_end(
    tmp_path: Path,
) -> None:
    pm, project_path = setup_project(tmp_path)
    timeline_id = await edited_timeline(pm)
    service = _service(pm)

    narrated = await service.render("demo", timeline_id, narration="with_narration", subtitles="no_subtitles")
    silent = await service.render("demo", timeline_id, narration="without_narration", subtitles="no_subtitles")

    # S02 的 2 秒旁白从 1.5 秒起、超出 3 秒的时间线末尾：如实渲染，随成片截止，时长仍与剪辑时间线一致。
    assert [(issue.code, issue.params.get("cause")) for issue in narrated.warnings] == [
        ("narration_overrun", "timeline_end")
    ]
    assert narrated.acceptance.audio_duration == narrated.acceptance.expected_duration == 3.0
    narrated_path = project_path / narrated.artifact_path
    # S01 的 1.2 秒旁白从 0 秒起，S02 的旁白从 c2 的起点 1.5 秒起，中间留空。
    assert _peak(narrated_path, 0.1, 1.1) > 3000
    assert _peak(narrated_path, 1.27, 1.43) < 300
    assert _peak(narrated_path, 1.6, 2.9) > 3000
    assert _peak(project_path / silent.artifact_path, 0.0, 3.0) < 300
