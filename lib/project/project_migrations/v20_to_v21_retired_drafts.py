"""v20→v21：将退役的隔离草稿原样移入项目回收站。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from lib.artifacts.formal_write import project_metadata_lock
from lib.episode.episode_paths import (
    DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    EPISODE_DRAFT_DIR_PATTERN,
    NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
)
from lib.infra.json_io import atomic_write_json
from lib.project.project_migration_report import MigrationAdjustment
from lib.project.project_migrations.v9_to_v10_script_plan_naming import DRAFT_FILE_RENAMES
from lib.project.project_schema import parse_project_schema_version
from lib.project.project_trash import record_deletion, trash_dir

_RETIRED_NAMES = (
    DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
    *(old for old in DRAFT_FILE_RENAMES if old.endswith(".invalid.json")),
)


def _retired_drafts(project_dir: Path) -> Iterator[tuple[int, str]]:
    drafts = project_dir / "drafts"
    if drafts.is_symlink() or not drafts.is_dir():
        return
    for directory in sorted(drafts.iterdir()):
        match = EPISODE_DRAFT_DIR_PATTERN.fullmatch(directory.name)
        if not match or directory.is_symlink() or not directory.is_dir():
            continue
        for name in _RETIRED_NAMES:
            path = directory / name
            if not path.is_symlink() and path.is_file():
                yield int(match[1]), path.relative_to(project_dir).as_posix()


def migrate_v20_to_v21(
    project_dir: Path, *, record: list[MigrationAdjustment] | None = None, restoring_snapshot: bool = False
) -> None:
    project_file = project_dir / "project.json"
    with project_metadata_lock(project_dir):
        project = json.loads(project_file.read_bytes())
        if parse_project_schema_version(project) >= 21:
            return
        if not restoring_snapshot:
            for episode, rel in _retired_drafts(project_dir):
                destination = trash_dir(project_dir)
                if destination.parent.is_symlink() or destination.is_symlink():
                    raise ValueError("项目回收站路径不能是符号链接")
                record_deletion(
                    project_dir,
                    {"kind": "planning_draft", "episode": episode, "path": rel},
                    [rel],
                    origin="migration",
                    notify=False,
                )
                if record is not None:
                    record.append(MigrationAdjustment(code="retired_draft_trashed", episode=episode, file=rel))
        atomic_write_json(project_file, {**project, "schema_version": 21})
