"""剪辑时间线引用的视频单元：按脚本取条目，并决定每个单元按哪个呈现版本取用素材层。

剪映草稿与剪辑视图预览共用这里的口径，两者的字幕与旁白取自同一份呈现模型。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lib.artifacts.version_manager import VersionManager
from lib.edit_timeline import EditTimelineError
from lib.jianying_draft.basis import DraftNarration, effective_unit_variant
from lib.project.project_manager import ProjectManager
from lib.script.script_editor import resolve_items
from lib.speech.speech_artifact_provenance import RenditionVariant
from lib.speech.speech_composition import admit_script_unit


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


__all__ = ["load_episode_items", "unit_rendition"]
