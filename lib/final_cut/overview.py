"""一集的剪辑概况：制作进度面板「剪辑」行陈述的现状与提醒。

剪辑时间线条数、最近修改那条的问题数（与剪辑时间线读取结果的 issues 同一份），以及成片已落后于
剪辑时间线的那几条：渲染过的任一版本落后就算。从未出片的剪辑时间线不算落后。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.edit_timeline.service import EditTimelineService
from lib.final_cut.basis import FINAL_CUT_VARIANTS, final_cut_artifact_path
from lib.final_cut.service import FinalCutService
from lib.project.project_manager import ProjectManager


class TimelineRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str


class LatestTimeline(TimelineRef):
    updated_at: str
    issue_count: int


class EpisodeEditOverview(BaseModel):
    model_config = ConfigDict(frozen=True)

    episode: int
    timeline_count: int
    #: 最近修改的那条；该集还没有剪辑时间线时为 None。
    latest: LatestTimeline | None
    #: 成片已落后于剪辑时间线的那几条，按创建顺序。
    stale_final_cuts: tuple[TimelineRef, ...]


async def episode_edit_overview(projects: ProjectManager, project_name: str, episode: int) -> EpisodeEditOverview:
    timelines = EditTimelineService(projects)
    summaries = await timelines.list_timelines(project_name, episode=episode)
    if not summaries:
        return EpisodeEditOverview(episode=episode, timeline_count=0, latest=None, stale_final_cuts=())
    newest = max(summaries, key=lambda summary: summary.updated_at)
    readout = await timelines.read(project_name, newest.id)
    final_cuts = FinalCutService(projects)
    project_dir = projects.get_project_path(project_name)
    stale: list[TimelineRef] = []
    for summary in summaries:
        for variant in FINAL_CUT_VARIANTS:
            # 只比对渲染过的版本：没有文件的版本读 missing，不必为它重建依据。
            if not (project_dir / final_cut_artifact_path(episode, summary.id, variant)).is_file():
                continue
            status = await final_cuts.status(
                project_name, summary.id, narration=variant.narration, subtitles=variant.subtitles
            )
            if status.status is ArtifactStatus.STALE:
                stale.append(TimelineRef(id=summary.id, name=summary.name))
                break
    return EpisodeEditOverview(
        episode=episode,
        timeline_count=len(summaries),
        latest=LatestTimeline(
            id=newest.id, name=newest.name, updated_at=newest.updated_at, issue_count=len(readout.issues)
        ),
        stale_final_cuts=tuple(stale),
    )


__all__ = ["EpisodeEditOverview", "LatestTimeline", "TimelineRef", "episode_edit_overview"]
