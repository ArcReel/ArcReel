"""Material layers for edit-view previews: narration audio and subtitle entries for every unit referenced by the latest timeline revision, plus files referenced by the BGM track.

Use the project’s default narration variant (with narration for TTS projects). Subtitles come from each unit’s current presentation model,
sharing segmentation with Jianying drafts. Entry times are unit-relative: narration-following subtitles use the narration start, others use
video-source time. The frontend places them on global time according to edit clips."""

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
    """Project-relative path and version number of the current narration-audio file."""

    path: str
    version: int


class PreviewUnitMedia(_View):
    """Include ``narration_audio`` only when this unit uses the narrated presentation variant; ``subtitles`` is empty if the presentation model cannot materialize.

    ``provider_audio`` is the provider-audio switch used when generating the current video, matching final-cut rules: False mutes
    the preview even if the file contains an audio track. Preview volume is the clip’s original-audio volume multiplied by this switch."""

    unit_id: str
    provider_audio: bool
    narration_audio: PreviewNarrationAudio | None
    subtitles_follow_narration: bool
    subtitles: tuple[PreviewCue, ...]


class PreviewBgm(_View):
    """Project-relative BGM path and static loudness gain (linear multiplier); preview volume is clip volume multiplied by this gain."""

    bgm_id: str
    path: str
    gain: float


class TimelinePreviewMedia(_View):
    """``bgm`` includes only BGM referenced by the BGM track whose files exist in the project."""

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
        """Retrieve material layers for the latest revision; exclude video units deleted from the script."""
        document = await asyncio.to_thread(lambda: EditTimelineStore(self._projects, project_name).find(timeline_id))
        project = await asyncio.to_thread(self._projects.load_project, project_name)
        project_dir = await asyncio.to_thread(self._projects.get_project_path, project_name)
        kind, items = await asyncio.to_thread(
            load_episode_items, self._projects, project_name, project, document.episode
        )
        resource_type = "reference_videos" if kind == "video_units" else "videos"
        narration = default_draft_narration(project)
        versions = await asyncio.to_thread(VersionManager, project_dir)
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
