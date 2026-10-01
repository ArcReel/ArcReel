"""剪辑时间线引用的视频单元：按脚本取条目，决定每个单元按哪个呈现版本取用素材层，并取出素材层。

剪映草稿、成片与剪辑视图预览共用这里的口径，三者的字幕与旁白取自同一份呈现模型。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline import EditTimelineError
from lib.edit_timeline.model import EditTimelineContent
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
    """一集脚本的条目形态与「视频单元 ID → 脚本条目」。"""
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
    """视频单元在所选旁白版本下实际取用的呈现版本；带旁白只作用于已有旁白配音的画外音单元。"""
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
    """呈现模型投影成素材层与素材层指纹：有类型化来源的取呈现模型依据，手动上传的取版本号与内容摘要。"""
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
    """:class:`~lib.jianying_draft.placement.UnitMaterialSource` 的实现：物化各单元当前的呈现模型并投影成素材层。"""

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
        resource_type = "reference_videos" if kind == "video_units" else "videos"
        versions = VersionManager(project_dir)
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
