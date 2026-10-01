"""一集的剪辑概况：剪辑时间线条数、最近修改那条的问题数，以及成片落后于剪辑时间线的那几条。"""

from __future__ import annotations

from pathlib import Path

from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.edit_timeline.operations import SetReason
from lib.final_cut.overview import episode_edit_overview
from lib.final_cut.service import FinalCutService
from lib.project.project_manager import ProjectManager
from tests.factories import install_current_video, make_test_clip

CREATOR = RevisionAuthor(kind="creator", user_id="u1")


def _install(timeline_project: ProjectManager, tmp_path: Path, unit_id: str) -> None:
    source = tmp_path / "media" / f"{unit_id}.mp4"
    make_test_clip(source, size="160x90", fps=30, seconds=0.5, tone=True)
    install_current_video(timeline_project.get_project_path("demo"), "reference_videos", unit_id, source)


async def _create(timeline_project: ProjectManager, name: str) -> str:
    readout = await EditTimelineService(timeline_project).create_from_script(
        "demo", episode=1, name=name, author=CREATOR
    )
    return readout.timeline.id


async def _touch(timeline_project: ProjectManager, timeline_id: str, base_revision: int) -> None:
    await EditTimelineService(timeline_project).edit(
        "demo",
        timeline_id,
        base_revision=base_revision,
        summary="补充理由",
        operations=[SetReason(op="set_reason", clip="c1", reason="保留开场")],
        author=CREATOR,
    )


async def test_an_episode_without_edit_timelines_has_an_empty_overview(timeline_project: ProjectManager) -> None:
    overview = await episode_edit_overview(timeline_project, "demo", 1)

    assert overview.timeline_count == 0
    assert overview.latest is None
    assert overview.stale_final_cuts == ()


async def test_latest_is_the_most_recently_modified_timeline_with_its_issue_count(
    tmp_path: Path, timeline_project: ProjectManager
) -> None:
    _install(timeline_project, tmp_path, "E1U1")
    first = await _create(timeline_project, "完整版")
    await _create(timeline_project, "快节奏版")
    await _touch(timeline_project, first, base_revision=1)

    overview = await episode_edit_overview(timeline_project, "demo", 1)

    assert overview.timeline_count == 2
    assert overview.latest is not None
    assert (overview.latest.id, overview.latest.name) == (first, "完整版")
    # E1U2 还没有视频：它的片段报一条缺视频。
    assert overview.latest.issue_count == 1


async def test_only_final_cuts_behind_their_timeline_are_listed(
    tmp_path: Path, timeline_project: ProjectManager
) -> None:
    _install(timeline_project, tmp_path, "E1U1")
    _install(timeline_project, tmp_path, "E1U2")
    rendered = await _create(timeline_project, "完整版")
    never_rendered = await _create(timeline_project, "快节奏版")
    await FinalCutService(timeline_project).render("demo", rendered)

    current = await episode_edit_overview(timeline_project, "demo", 1)
    assert current.stale_final_cuts == ()

    await _touch(timeline_project, rendered, base_revision=1)
    await _touch(timeline_project, never_rendered, base_revision=1)

    behind = await episode_edit_overview(timeline_project, "demo", 1)
    # 从未出片的那条不算落后。
    assert [(item.id, item.name) for item in behind.stale_final_cuts] == [(rendered, "完整版")]
