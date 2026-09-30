"""剪辑时间线服务命令：在真实临时项目目录上经公开命令验证机械新建、列出与读取。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline import (
    EditTimelineError,
    EditTimelineService,
    RevisionAuthor,
)
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import resource_relative_path
from tests.factories import make_test_video, wav_bytes

CREATOR = RevisionAuthor(kind="creator", user_id="u1")


def _unit(unit_id: str, text: str) -> dict[str, Any]:
    return {"unit_id": unit_id, "text": text, "duration_seconds": 4}


def _script(*units: dict[str, Any]) -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": list(units),
    }


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path / "projects"))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.upsert_assets("demo", "characters", {"角色A": {"description": "主角"}})
    manager.update_project("demo", lambda project: project.update({"generation_mode": "reference_video"}))
    manager.save_script(
        "demo",
        _script(
            _unit("E1U1", "@[角色A]{你好}"),
            _unit("E1U2", "{风起了}"),
            _unit("E1U3", "推门进屋"),
        ),
        "episode_1.json",
    )
    return manager


def _install_media(pm: ProjectManager, tmp_path: Path, resource_type: str, unit_id: str, source: Path) -> None:
    """登记一个 current 版本，并把它放到正式路径上。"""
    project_path = pm.get_project_path("demo")
    VersionManager(project_path).add_version(resource_type, unit_id, "prompt", source_file=source)
    target = project_path / resource_relative_path(resource_type, unit_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _install_video(pm: ProjectManager, tmp_path: Path, unit_id: str, seconds: float) -> None:
    source = tmp_path / "media" / f"{unit_id}.mp4"
    make_test_video(source, duration_sec=seconds)
    _install_media(pm, tmp_path, "reference_videos", unit_id, source)


def _install_narration(pm: ProjectManager, tmp_path: Path, unit_id: str, seconds: float) -> None:
    source = tmp_path / "media" / f"{unit_id}.wav"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(wav_bytes(seconds))
    _install_media(pm, tmp_path, "audio", unit_id, source)


@pytest.fixture
def service(pm: ProjectManager) -> EditTimelineService:
    return EditTimelineService(pm)


async def test_mechanical_timeline_orders_clips_by_script_with_voice_defaults(
    pm: ProjectManager, tmp_path: Path, service: EditTimelineService
) -> None:
    _install_video(pm, tmp_path, "E1U1", 1.0)
    _install_video(pm, tmp_path, "E1U2", 1.5)
    _install_video(pm, tmp_path, "E1U3", 0.5)
    _install_narration(pm, tmp_path, "E1U2", 0.8)

    created = await service.create_from_script("demo", episode=1, name="完整版", author=CREATOR)
    readout = await service.read("demo", created.timeline.id)

    assert readout.revision == 1
    assert readout.timeline.name == "完整版"
    assert [(clip.id, clip.unit_id) for clip in readout.clips] == [("c1", "E1U1"), ("c2", "E1U2"), ("c3", "E1U3")]
    assert [(clip.start, clip.duration) for clip in readout.clips] == [(0.0, 1.0), (1.0, 1.5), (2.5, 0.5)]
    assert readout.duration == 3.0
    # 台词与无人声单位保持原音量，画外音单位压低
    assert [clip.source_volume for clip in readout.clips] == [1.0, 0.3, 1.0]
    assert all(clip.transition_to_next is None for clip in readout.clips)
    assert all(clip.trim is None for clip in readout.clips)
    narration = readout.clips[1].narration
    assert narration is not None
    assert (narration.start, narration.end) == (1.0, 1.8)
    assert readout.clips[0].narration is None
    assert readout.clips[2].narration is None
    assert readout.bgm == ()
    assert readout.issues == ()


def _issues(readout, code: str) -> list[tuple[tuple[str, ...], str | None, str]]:
    return [(issue.clip_ids, issue.unit_id, issue.severity.value) for issue in readout.issues if issue.code == code]


async def test_script_changes_surface_as_deleted_and_unused_units(
    pm: ProjectManager, tmp_path: Path, service: EditTimelineService
) -> None:
    for unit_id in ("E1U1", "E1U2", "E1U3"):
        _install_video(pm, tmp_path, unit_id, 1.0)
    created = await service.create_from_script("demo", episode=1, name="初剪", author=CREATOR)

    pm.save_script(
        "demo",
        _script(_unit("E1U1", "@[角色A]{你好}"), _unit("E1U3", "推门进屋"), _unit("E1U4", "关门")),
        "episode_1.json",
    )
    readout = await service.read("demo", created.timeline.id)

    # 剪辑时间线不随脚本变化：片段保持原样，已删除单元的片段渲染时跳过
    assert [clip.unit_id for clip in readout.clips] == ["E1U1", "E1U2", "E1U3"]
    assert [(clip.status, clip.start, clip.duration) for clip in readout.clips] == [
        ("ready", 0.0, 1.0),
        ("unit_deleted", 1.0, 0.0),
        ("ready", 1.0, 1.0),
    ]
    assert readout.duration == 2.0
    assert _issues(readout, "unit_deleted") == [(("c2",), "E1U2", "info")]
    assert _issues(readout, "unit_unused") == [((), "E1U4", "info")]
    assert _issues(readout, "video_missing") == []


async def test_unit_without_usable_video_blocks_and_keeps_scripted_length(
    pm: ProjectManager, tmp_path: Path, service: EditTimelineService
) -> None:
    _install_video(pm, tmp_path, "E1U1", 1.0)
    _install_video(pm, tmp_path, "E1U3", 1.0)

    created = await service.create_from_script("demo", episode=1, name="初剪", author=CREATOR)

    assert _issues(created, "video_missing") == [(("c2",), "E1U2", "blocking")]
    missing = created.clips[1]
    assert (missing.status, missing.video_version, missing.duration) == ("video_missing", None, 4.0)
    assert created.clips[2].start == 5.0


async def test_duplicate_name_within_episode_is_rejected(pm: ProjectManager, service: EditTimelineService) -> None:
    await service.create_from_script("demo", episode=1, name="快节奏版", author=CREATOR)

    with pytest.raises(EditTimelineError) as excinfo:
        await service.create_from_script("demo", episode=1, name="  快节奏版 ", author=CREATOR)

    assert excinfo.value.code == "timeline_name_conflict"
    assert [summary.name for summary in await service.list_timelines("demo", episode=1)] == ["快节奏版"]


async def test_list_reports_each_timeline_with_latest_revision(
    pm: ProjectManager, service: EditTimelineService
) -> None:
    pm.save_script("demo", {**_script(_unit("E2U1", "关门")), "episode": 2}, "episode_2.json")
    first = await service.create_from_script("demo", episode=1, name="完整版", author=CREATOR)
    second = await service.create_from_script(
        "demo", episode=1, name="快节奏版", author=RevisionAuthor(kind="arcreel_agent"), agent_turn="user-1"
    )
    other = await service.create_from_script("demo", episode=2, name="完整版", author=CREATOR)

    listed = await service.list_timelines("demo")
    assert [(item.id, item.episode, item.name, item.revision, item.clip_count) for item in listed] == [
        (first.timeline.id, 1, "完整版", 1, 3),
        (second.timeline.id, 1, "快节奏版", 1, 3),
        (other.timeline.id, 2, "完整版", 1, 1),
    ]
    assert [item.id for item in await service.list_timelines("demo", episode=2)] == [other.timeline.id]


async def test_creating_for_a_missing_episode_is_rejected(service: EditTimelineService) -> None:
    with pytest.raises(EditTimelineError) as excinfo:
        await service.create_from_script("demo", episode=9, name="完整版", author=CREATOR)

    assert excinfo.value.code == "episode_not_found"


@pytest.mark.parametrize("timeline_id", ["tl-0000abcd", "../project"])
async def test_reading_an_unknown_timeline_is_rejected(service: EditTimelineService, timeline_id: str) -> None:
    with pytest.raises(EditTimelineError) as excinfo:
        await service.read("demo", timeline_id)

    assert excinfo.value.code == "timeline_not_found"


async def test_media_durations_accumulate_before_rounding_to_milliseconds(
    pm: ProjectManager, tmp_path: Path, service: EditTimelineService
) -> None:
    # 每段 31 帧 / 30fps，单段显示为 1.033 秒，三段合计应为 3.100 秒。
    for unit_id in ("E1U1", "E1U2", "E1U3"):
        _install_video(pm, tmp_path, unit_id, 1.03)
    created = await service.create_from_script("demo", episode=1, name="帧时长", author=CREATOR)
    readout = await service.read("demo", created.timeline.id)

    assert [clip.duration for clip in readout.clips] == [1.033, 1.033, 1.033]
    assert [clip.start for clip in readout.clips] == [0.0, 1.033, 2.067]
    assert readout.duration == 3.1
