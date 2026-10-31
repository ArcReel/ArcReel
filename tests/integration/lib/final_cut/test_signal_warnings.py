"""成片内容信号告警进渲染结果：长黑场段记黑场告警（联系表照常生成），干净素材没有告警。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.final_cut.basis import FinalCutVariant
from lib.final_cut.content_signals import FinalCutSignalWarning, SignalWarningKind
from lib.final_cut.service import FinalCutRender, FinalCutService
from lib.project.project_manager import ProjectManager
from tests.factories import install_current_video, make_test_clip, run_bundled_ffmpeg

CREATOR = RevisionAuthor(kind="creator", user_id="u1")
VARIANT = FinalCutVariant()
PLAIN: dict[str, object] = {"narration": VARIANT.narration, "subtitles": VARIANT.subtitles}
"""现场合成的素材没有呈现模型，这里只渲染不带旁白、不烧入字幕的版本。"""


def make_black_middle_clip(path: Path, *, seconds: float = 7.0, black: float = 3.0) -> None:
    """用随包 ffmpeg 合成 25 fps、320x180、无音轨的素材：前后两段运动画面夹一段 ``black`` 秒的纯黑（共 ``seconds`` 秒）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    moving = (seconds - black) / 2
    run_bundled_ffmpeg(
        "-filter_complex",
        f"testsrc=size=320x180:rate=25:duration={moving}[before];"
        f"color=black:size=320x180:rate=25:duration={black}[black];"
        f"testsrc2=size=320x180:rate=25:duration={moving}[after];"
        "[before][black][after]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    )


@pytest.fixture
def signal_project(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path / "projects"))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.upsert_assets("demo", "characters", {"角色A": {"description": "主角"}})
    manager.update_project(
        "demo", lambda project: project.update({"generation_mode": "reference_video", "aspect_ratio": "9:16"})
    )
    manager.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "summary": "摘要",
            "novel": {"title": "小说", "chapter": "第一章"},
            "video_units": [{"unit_id": "E1U1", "text": "@[角色A]{你好}", "duration_seconds": 7}],
        },
        "episode_1.json",
    )
    return manager


async def _create_timeline(project: ProjectManager) -> str:
    readout = await EditTimelineService(project).create_from_script("demo", episode=1, name="完整版", author=CREATOR)
    return readout.timeline.id


def _signal_warnings(result: FinalCutRender) -> list[FinalCutSignalWarning]:
    return [warning for warning in result.warnings if isinstance(warning, FinalCutSignalWarning)]


async def test_long_black_span_yields_a_signal_warning(tmp_path: Path, signal_project: ProjectManager) -> None:
    source = tmp_path / "media" / "E1U1.mp4"
    make_black_middle_clip(source)
    install_current_video(signal_project.get_project_path("demo"), "reference_videos", "E1U1", source)
    timeline_id = await _create_timeline(signal_project)

    result = await FinalCutService(signal_project).render("demo", timeline_id, **PLAIN)

    warnings = _signal_warnings(result)
    assert [warning.kind for warning in warnings] == [SignalWarningKind.BLACK]
    assert warnings[0].start == pytest.approx(2.0, abs=0.15)
    assert warnings[0].end == pytest.approx(5.0, abs=0.15)
    # 画面信号不阻断联系表。
    assert result.sheet_path is not None


async def test_clean_render_has_no_signal_warnings(tmp_path: Path, signal_project: ProjectManager) -> None:
    source = tmp_path / "media" / "E1U1.mp4"
    make_test_clip(source, size="320x180", fps=25, seconds=4.0, tone=False)
    install_current_video(signal_project.get_project_path("demo"), "reference_videos", "E1U1", source)
    timeline_id = await _create_timeline(signal_project)

    result = await FinalCutService(signal_project).render("demo", timeline_id, **PLAIN)

    assert _signal_warnings(result) == []
    assert result.sheet_path is not None
