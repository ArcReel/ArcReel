"""Resolve a video resource against the project's current episode scripts."""

from collections.abc import Mapping
from typing import Any

from lib.project.project_manager import ProjectManager
from lib.script.script_editor import resolve_items


def resolve_video_unit_owner(
    project_manager: ProjectManager,
    *,
    project_name: str,
    project: Mapping[str, Any],
    resource_type: str,
    resource_id: str,
) -> tuple[int, str]:
    matches: list[tuple[int, str]] = []
    entries = project.get("episodes")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        episode = entry.get("episode")
        raw_script_file = entry.get("script_file")
        if type(episode) is not int or episode <= 0 or not isinstance(raw_script_file, str):
            continue
        script_file = ProjectManager.normalize_script_filename(raw_script_file)
        try:
            script = project_manager.load_script_readonly(project_name, script_file)
        except FileNotFoundError:
            continue
        items, id_field, kind = resolve_items(script)
        expected_type = "reference_videos" if kind == "video_units" else "videos"
        if expected_type != resource_type:
            continue
        if any(isinstance(item, dict) and str(item.get(id_field)) == resource_id for item in items):
            matches.append((episode, script_file))
    if len(matches) != 1:
        raise ValueError(f"expected one owning episode for video unit: {resource_id}")
    return matches[0]
