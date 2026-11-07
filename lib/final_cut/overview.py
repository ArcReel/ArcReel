"""Episode editing overview: current state and reminders in the production-progress editing row.

Report timeline count, latest-modified timeline issue count (the same issues returned by timeline
reads), and timelines whose final cuts lag behind edits. Any previously rendered variant lagging
counts; timelines never rendered do not count as behind.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict

from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.edit_timeline.service import EditTimelineService
from lib.final_cut.basis import FINAL_CUT_VARIANTS, FinalCutVariant, final_cut_artifact_path
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
    #: Latest modified timeline, or None if the episode has no editing timelines.
    latest: LatestTimeline | None
    #: Timelines with final cuts behind edits, ordered by creation.
    stale_final_cuts: tuple[TimelineRef, ...]


async def episode_edit_overview(projects: ProjectManager, project_name: str, episode: int) -> EpisodeEditOverview:
    timelines = EditTimelineService(projects)
    summaries = await timelines.list_timelines(project_name, episode=episode)
    if not summaries:
        return EpisodeEditOverview(episode=episode, timeline_count=0, latest=None, stale_final_cuts=())
    newest = max(summaries, key=lambda summary: summary.updated_at)
    readout = await timelines.read(project_name, newest.id)
    final_cuts = FinalCutService(projects)

    def rendered_variants() -> set[tuple[str, FinalCutVariant]]:
        project_dir = projects.get_project_path(project_name)
        return {
            (summary.id, variant)
            for summary in summaries
            for variant in FINAL_CUT_VARIANTS
            if (project_dir / final_cut_artifact_path(episode, summary.id, variant)).is_file()
        }

    rendered = await asyncio.to_thread(rendered_variants)
    stale: list[TimelineRef] = []
    for summary in summaries:
        for variant in FINAL_CUT_VARIANTS:
            # Compare rendered variants only: fileless variants are missing and do not need rebuilt bases.
            if (summary.id, variant) not in rendered:
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
