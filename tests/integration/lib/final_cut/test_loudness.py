"""成片响度归一的端到端验收：混音后归一到目标响度，验收重测交付文件并记入结果。"""

from __future__ import annotations

import io
import math
import wave
from array import array
from pathlib import Path
from typing import Any

import pytest

from lib.bgm.library import SILENCE_LOUDNESS_LUFS
from lib.bgm.service import BgmLibraryService
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import InsertBgm
from lib.final_cut.basis import FinalCutVariant
from lib.final_cut.loudness import TARGET_INTEGRATED_LUFS, TARGET_TRUE_PEAK_DBTP
from lib.final_cut.service import FinalCutService
from lib.project.project_manager import ProjectManager
from tests.factories import install_current_video, make_test_clip

CREATOR = RevisionAuthor(kind="creator", user_id="u1")
VARIANT = FinalCutVariant()
PLAIN: dict[str, Any] = {"narration": VARIANT.narration, "subtitles": VARIANT.subtitles}


def _unit(unit_id: str, text: str) -> dict[str, Any]:
    return {"unit_id": unit_id, "text": text, "duration_seconds": 4}


@pytest.fixture
def render_project(tmp_path: Path) -> ProjectManager:
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
            "video_units": [
                _unit("E1U1", "@[角色A]{你好}"),
                _unit("E1U2", "{风起了}"),
                _unit("E1U3", "推门进屋"),
            ],
        },
        "episode_1.json",
    )
    return manager


CLIP_SECONDS = {"E1U1": 1.0, "E1U2": 1.5, "E1U3": 0.7}
"""与 test_service 一致的片段时长，时间线共 3.2 秒。"""


def _install_clip(render_project: ProjectManager, tmp_path: Path, unit_id: str, *, tone: bool) -> None:
    source = tmp_path / "media" / f"{unit_id}-{len(list((tmp_path / 'media').glob('*.mp4')))}.mp4"
    make_test_clip(source, size="160x90", fps=24, seconds=CLIP_SECONDS[unit_id], tone=tone)
    install_current_video(render_project.get_project_path("demo"), "reference_videos", unit_id, source)


@pytest.fixture
def media(render_project: ProjectManager, tmp_path: Path) -> None:
    _install_clip(render_project, tmp_path, "E1U1", tone=True)
    _install_clip(render_project, tmp_path, "E1U2", tone=False)
    _install_clip(render_project, tmp_path, "E1U3", tone=True)


@pytest.fixture
def continuous_media(render_project: ProjectManager, tmp_path: Path) -> None:
    for unit_id in CLIP_SECONDS:
        _install_clip(render_project, tmp_path, unit_id, tone=True)


@pytest.fixture
def silent_media(render_project: ProjectManager, tmp_path: Path) -> None:
    for unit_id in CLIP_SECONDS:
        _install_clip(render_project, tmp_path, unit_id, tone=False)


async def _create_timeline(render_project: ProjectManager) -> str:
    readout = await EditTimelineService(render_project).create_from_script(
        "demo", episode=1, name="完整版", author=CREATOR
    )
    return readout.timeline.id


@pytest.mark.usefixtures("continuous_media")
async def test_the_rendered_mix_is_normalized_to_the_target_loudness(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)

    result = await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

    acceptance = result.acceptance
    assert acceptance.integrated_lufs is not None
    assert acceptance.integrated_lufs == pytest.approx(TARGET_INTEGRATED_LUFS, abs=0.5)
    assert acceptance.true_peak_dbtp is not None
    assert acceptance.true_peak_dbtp <= TARGET_TRUE_PEAK_DBTP + 0.5
    assert acceptance.loudness_warnings == ()


@pytest.mark.usefixtures("media")
async def test_sparse_audio_is_normalized_to_the_target_with_constant_gain(
    render_project: ProjectManager,
) -> None:
    # 稀疏音轨（有声/无声交替）上二遍 loudnorm 的实测响度范围超限，
    # 归一走常数增益路径，积分响度同样落到目标。
    timeline_id = await _create_timeline(render_project)

    result = await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

    acceptance = result.acceptance
    assert acceptance.integrated_lufs is not None
    assert acceptance.integrated_lufs == pytest.approx(TARGET_INTEGRATED_LUFS, abs=0.5)
    assert acceptance.true_peak_dbtp is not None
    assert acceptance.true_peak_dbtp <= TARGET_TRUE_PEAK_DBTP + 0.5
    assert acceptance.loudness_warnings == ()


@pytest.mark.usefixtures("silent_media")
async def test_a_silent_timeline_is_kept_silent_without_loudness_warnings(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)

    result = await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

    acceptance = result.acceptance
    assert acceptance.integrated_lufs is not None
    assert acceptance.integrated_lufs <= SILENCE_LOUDNESS_LUFS
    assert acceptance.true_peak_dbtp is None
    assert acceptance.loudness_warnings == ()


def _quiet_bgm_with_loud_hits(seconds: float, sample_rate: int = 8000) -> bytes:
    """−40 dBFS 上下的正弦音，每 0.5 秒一下几个采样长、接近满幅的脉冲：积分响度很低，登记的响度增益很高。"""
    samples = array("h")
    for index in range(int(seconds * sample_rate)):
        if index % (sample_rate // 2) < 3:
            samples.append(30000 if index % 2 == 0 else -30000)
        else:
            samples.append(round(300 * math.sin(2 * math.pi * 330 * index / sample_rate)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return buffer.getvalue()


@pytest.mark.usefixtures("media")
async def test_a_high_gain_bgm_is_pulled_back_to_the_target(render_project: ProjectManager) -> None:
    track = await BgmLibraryService(render_project).upload(
        "demo", filename="hits.wav", content=_quiet_bgm_with_loud_hits(3.0)
    )
    assert track.gain_db > 6
    timeline_id = await _create_timeline(render_project)
    await EditTimelineService(render_project).edit(
        "demo",
        timeline_id,
        base_revision=1,
        summary="加 BGM",
        operations=[InsertBgm(op="insert_bgm", bgm_id=track.id, start=0.0, volume=1.0)],
        author=CREATOR,
    )

    result = await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

    acceptance = result.acceptance
    assert acceptance.integrated_lufs is not None
    # 增益会超真峰上限，走常数增益加限幅路径：脉冲触发限幅包络回落，积分响度向目标靠拢但有损；
    # 峰值必须被压回目标之内。
    assert acceptance.integrated_lufs == pytest.approx(TARGET_INTEGRATED_LUFS, abs=2.0)
    assert acceptance.true_peak_dbtp is not None
    assert acceptance.true_peak_dbtp <= TARGET_TRUE_PEAK_DBTP + 0.5
    assert acceptance.loudness_warnings == () or all("dBTP" not in warning for warning in acceptance.loudness_warnings)


@pytest.mark.usefixtures("media")
async def test_rendering_still_records_duration_acceptance_fields(render_project: ProjectManager) -> None:
    timeline_id = await _create_timeline(render_project)

    result = await FinalCutService(render_project).render("demo", timeline_id, **PLAIN)

    acceptance = result.acceptance
    assert acceptance.video_duration == pytest.approx(acceptance.expected_duration, abs=0.05)
    assert acceptance.audio_duration == pytest.approx(acceptance.video_duration, abs=0.05)
