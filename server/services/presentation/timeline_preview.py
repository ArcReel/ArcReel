"""剪辑视图预览用的素材层：剪辑时间线最新修订引用的每个视频单元的旁白配音与字幕条目，以及 BGM 轨引用的 BGM 文件。

旁白版本取项目默认值（TTS 配音项目带旁白），字幕取自各单元当前的呈现模型，与剪映草稿同一份切分结果。
条目时间相对单元：跟随旁白的字幕从旁白起点算起，其余按视频源素材时间。按剪辑片段摆到全局时间由前端完成。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from lib.artifacts.version_manager import VersionManager
from lib.bgm.library import read_bgm_library
from lib.edit_timeline.bgm import bgm_ids
from lib.edit_timeline.model import microseconds_to_seconds
from lib.edit_timeline.store import EditTimelineStore
from lib.final_cut.basis import current_provider_audio
from lib.jianying_draft.basis import DraftNarration, default_draft_narration, draft_unit_ids
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import resource_relative_path
from lib.speech.narration_delivery import USE_TTS
from server.services.presentation.presentation_read_model import (
    PresentationReadModelService,
    PresentationUnavailableError,
)
from server.services.presentation.timeline_units import load_episode_items, unit_rendition


class _View(BaseModel):
    model_config = ConfigDict(frozen=True)


class PreviewCue(_View):
    start: float
    duration: float
    text: str


class PreviewNarrationAudio(_View):
    """旁白配音 current 文件的项目内路径与版本号。"""

    path: str
    version: int


class PreviewUnitMedia(_View):
    """``narration_audio`` 只在该单元按带旁白版本呈现时给出；呈现模型物化不出时 ``subtitles`` 为空。

    ``provider_audio`` 是 current 视频生成时的供应商原声开关，与成片同口径：为 False 时视频文件里即使带音轨，
    预览也按静音播放；预览音量是片段原声音量乘以这个开关。
    """

    unit_id: str
    provider_audio: bool
    narration_audio: PreviewNarrationAudio | None
    subtitles_follow_narration: bool
    subtitles: tuple[PreviewCue, ...]


class PreviewBgm(_View):
    """BGM 文件的项目内路径与响度静态增益（线性倍数）；预览音量是片段音量乘以这个增益。"""

    bgm_id: str
    path: str
    gain: float


class TimelinePreviewMedia(_View):
    """``bgm`` 只含 BGM 轨引用且文件在项目里的 BGM。"""

    timeline_id: str
    revision: int
    narration: DraftNarration
    units: tuple[PreviewUnitMedia, ...]
    bgm: tuple[PreviewBgm, ...] = ()


def _preview_bgm(project_dir: Path, project: Mapping[str, Any], referenced: tuple[str, ...]) -> tuple[PreviewBgm, ...]:
    library = read_bgm_library(project)
    return tuple(
        PreviewBgm(bgm_id=bgm_id, path=track.file, gain=track.gain)
        for bgm_id in referenced
        if (track := library.get(bgm_id)) is not None and (project_dir / track.file).is_file()
    )


def _narration_audio(project_dir: Path, versions: VersionManager, unit_id: str) -> PreviewNarrationAudio | None:
    version = versions.get_current_version("audio", unit_id)
    path = resource_relative_path("audio", unit_id)
    if version <= 0 or not (project_dir / path).is_file():
        return None
    return PreviewNarrationAudio(path=path, version=version)


class TimelinePreviewService:
    def __init__(
        self,
        projects: ProjectManager,
        *,
        presentation_reader: PresentationReadModelService | None = None,
    ) -> None:
        self._projects = projects
        self._presentations = presentation_reader or PresentationReadModelService(projects)

    async def media(self, project_name: str, timeline_id: str) -> TimelinePreviewMedia:
        """按最新修订取素材层；已从脚本删除的视频单元不在结果里。"""
        document = await asyncio.to_thread(lambda: EditTimelineStore(self._projects, project_name).find(timeline_id))
        project = await asyncio.to_thread(self._projects.load_project, project_name)
        project_dir = await asyncio.to_thread(self._projects.get_project_path, project_name)
        kind, items = await asyncio.to_thread(
            load_episode_items, self._projects, project_name, project, document.episode
        )
        resource_type = "reference_videos" if kind == "video_units" else "videos"
        narration = default_draft_narration(project)
        versions = VersionManager(project_dir)
        units: list[PreviewUnitMedia] = []
        for unit_id in draft_unit_ids(document.latest.content, items):
            variant = await asyncio.to_thread(
                unit_rendition, versions, kind=kind, item=items[unit_id], unit_id=unit_id, narration=narration
            )
            audio = (
                await asyncio.to_thread(_narration_audio, project_dir, versions, unit_id)
                if variant == USE_TTS
                else None
            )
            try:
                presented = await self._presentations.materialize_unit(
                    project_name=project_name, resource_type=resource_type, resource_id=unit_id, variant=variant
                )
            except PresentationUnavailableError:
                cues: tuple[PreviewCue, ...] = ()
            else:
                cues = tuple(
                    PreviewCue(
                        start=microseconds_to_seconds(cue.start_microseconds),
                        duration=microseconds_to_seconds(cue.duration_microseconds),
                        text=cue.text,
                    )
                    for cue in presented.presentation.subtitles
                )
            provider_audio = await asyncio.to_thread(current_provider_audio, versions, resource_type, unit_id)
            units.append(
                PreviewUnitMedia(
                    unit_id=unit_id,
                    provider_audio=provider_audio,
                    narration_audio=audio,
                    subtitles_follow_narration=audio is not None,
                    subtitles=cues,
                )
            )
        bgm = await asyncio.to_thread(_preview_bgm, project_dir, project, bgm_ids(document.latest.content.bgm))
        return TimelinePreviewMedia(
            timeline_id=document.id,
            revision=document.latest.number,
            narration=narration,
            units=tuple(units),
            bgm=bgm,
        )


__all__ = [
    "PreviewBgm",
    "PreviewCue",
    "PreviewNarrationAudio",
    "PreviewUnitMedia",
    "TimelinePreviewMedia",
    "TimelinePreviewService",
]
