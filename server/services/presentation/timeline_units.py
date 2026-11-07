"""Video units referenced by an edit timeline: retrieve script items, select the presentation variant supplying each unit’s material layer, and retrieve that layer.

Jianying drafts, final cuts and edit-view previews share these rules; all three obtain subtitles and narration from the same presentation model."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline import EditTimelineError
from lib.edit_timeline.model import EditTimelineContent
from lib.final_cut.basis import video_resource_type_for
from lib.jianying_draft.basis import DraftNarration, DraftUnitBasis, draft_unit_ids, effective_unit_variant
from lib.jianying_draft.placement import UnitCue, UnitMaterial, UnitMaterials, UnitMaterialUnavailableError
from lib.project.project_manager import ProjectManager
from lib.script.script_editor import resolve_items
from lib.speech.speech_artifact_provenance import RenditionVariant
from lib.speech.speech_composition import admit_script_unit
from server.services.presentation.presentation_read_model import (
    MaterializedPresentation,
    PresentationReadModelService,
    PresentationUnavailableError,
)


def load_episode_items(
    projects: ProjectManager, project_name: str, project: Mapping[str, Any], episode: int
) -> tuple[str, dict[str, dict[str, Any]]]:
    """An episode script’s item shape and “video-unit ID → script item” mapping."""
    script_file = next(
        (
            entry.get("script_file")
            for entry in project.get("episodes") or []
            if isinstance(entry, Mapping) and entry.get("episode") == episode
        ),
        None,
    )
    if not isinstance(script_file, str) or not script_file:
        raise EditTimelineError("episode_not_found", f"集（id={episode}）不存在或尚无脚本", episode=episode)
    script = projects.load_script_readonly(project_name, script_file)
    raw_items, id_field, kind = resolve_items(script)
    items = {
        str(item[id_field]): item for item in raw_items if isinstance(item, dict) and item.get(id_field) is not None
    }
    return kind, items


def unit_rendition(
    versions: VersionManager, *, kind: str, item: Mapping[str, Any], unit_id: str, narration: DraftNarration
) -> RenditionVariant:
    """The presentation variant actually used by a video unit for the selected narration variant; narration applies only to voiceover units with existing narration audio."""
    return effective_unit_variant(
        narration,
        admit_script_unit(kind, item).mode,
        has_narration_audio=versions.get_current_version("audio", unit_id) > 0,
    )


def _cues(presented: MaterializedPresentation) -> tuple[UnitCue, ...]:
    return tuple(
        UnitCue(start_us=cue.start_microseconds, duration_us=cue.duration_microseconds, text=cue.text)
        for cue in presented.presentation.subtitles
    )


def unit_material_and_basis(presented: MaterializedPresentation) -> tuple[UnitMaterial, DraftUnitBasis]:
    """Project the presentation model into a material layer and its fingerprint: use presentation-model evidence for typed sources, and version number plus content digest for manual uploads."""
    presentation = presented.presentation
    video = presentation.video
    narration = presentation.narration_audio
    material = UnitMaterial(
        unit_id=presentation.unit_id,
        video_path=video.media.artifact_path,
        video_version=video.media.version,
        video_duration_us=video.duration_microseconds,
        source_gain=video.gain,
        subtitles=_cues(presented),
        narration_path=narration.media.artifact_path if narration is not None else None,
        narration_duration_us=narration.duration_microseconds if narration is not None else None,
    )
    if presentation.presentation_basis is not None:
        return material, DraftUnitBasis(
            presentation.unit_id, presentation_digest=presentation.presentation_basis.digest
        )
    raw = presentation.video.media
    return material, DraftUnitBasis(presentation.unit_id, manual_upload=(raw.version, raw.content_digest))


class TimelineUnitMaterials:
    """An implementation of :class:`~lib.jianying_draft.placement.UnitMaterialSource`: materialize each unit’s current presentation model and project it into a material layer."""

    def __init__(
        self, projects: ProjectManager, *, presentation_reader: PresentationReadModelService | None = None
    ) -> None:
        self._projects = projects
        self._presentations = presentation_reader or PresentationReadModelService(projects)

    async def __call__(
        self, project_name: str, *, episode: int, content: EditTimelineContent, narration: DraftNarration
    ) -> UnitMaterials:
        project = await asyncio.to_thread(self._projects.load_project, project_name)
        project_dir = await asyncio.to_thread(self._projects.get_project_path, project_name)
        kind, items = await asyncio.to_thread(load_episode_items, self._projects, project_name, project, episode)
        resource_type = video_resource_type_for(kind)
        versions = await asyncio.to_thread(VersionManager, project_dir)
        materials: dict[str, UnitMaterial] = {}
        bases: list[DraftUnitBasis] = []
        for unit_id in draft_unit_ids(content, items):
            effective = await asyncio.to_thread(
                unit_rendition, versions, kind=kind, item=items[unit_id], unit_id=unit_id, narration=narration
            )
            try:
                presented = await self._presentations.materialize_unit(
                    project_name=project_name,
                    resource_type=resource_type,
                    resource_id=unit_id,
                    variant=effective,
                )
            except PresentationUnavailableError as exc:
                raise UnitMaterialUnavailableError(unit_id, str(exc)) from exc
            material, basis = unit_material_and_basis(presented)
            materials[unit_id] = material
            bases.append(basis)
        return UnitMaterials(materials=materials, bases=tuple(bases))


__all__ = ["TimelineUnitMaterials", "load_episode_items", "unit_material_and_basis", "unit_rendition"]
