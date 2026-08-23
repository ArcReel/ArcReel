"""v9→v10: collapse the structured Unified Video Style into one prompt paragraph."""

from __future__ import annotations

import copy
from pathlib import Path

from lib.json_io import atomic_write_json, load_json
from lib.video_style import migrate_legacy_video_style

_TARGET_VERSION = 10


def migrate_v9_to_v10(project_dir: Path) -> None:
    """Preserve every legacy dimension and provenance field in the v10 prompt shape."""

    project_file = Path(project_dir) / "project.json"
    if not project_file.is_file():
        return
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json must contain an object")
    if int(project.get("schema_version") or 0) >= _TARGET_VERSION:
        return

    migrated = copy.deepcopy(project)
    raw_style = project.get("video_style")
    if raw_style is not None:
        if not isinstance(raw_style, dict):
            raise ValueError("project.video_style must contain an object or null")
        style = migrate_legacy_video_style(raw_style, project.get("source_language"))
        migrated["video_style"] = style.model_dump(mode="json")

    migrated["schema_version"] = _TARGET_VERSION
    atomic_write_json(project_file, migrated)


__all__ = ["migrate_v9_to_v10"]
