"""v20→v21：升级旁白交付依据，将退役的隔离草稿移入项目回收站。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.sqlite_history import SQLiteHistory
from lib.episode.episode_paths import (
    DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    EPISODE_DRAFT_DIR_PATTERN,
    NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
)
from lib.infra.json_io import atomic_write_json
from lib.infra.project_write_journal import HISTORY_UNDO_RELATIVE_PATH, is_history_database_name
from lib.project.project_migration_report import MigrationAdjustment, extend_migration_report
from lib.project.project_migrations.audio_delivery import upgrade_audio_delivery
from lib.project.project_migrations.backups import (
    allocate_kept_copy_path,
    ensure_versioned_backup,
    is_kept_copy_for,
    set_aside_as_kept_copy,
)
from lib.project.project_migrations.v9_to_v10_script_plan_naming import DRAFT_FILE_RENAMES
from lib.project.project_schema import SQLITE_HISTORY_SCHEMA_VERSION, parse_project_schema_version
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


_CONFLICT_MARKER = ".history-conflict-v20.pending.json"
_HISTORY_MARKER = ".history-v20.pending.json"


def _assert_no_history_symlinks(*paths: Path) -> None:
    if any(path.is_symlink() for path in paths):
        raise ValueError("unexpected version history symlink")


def _preserve_conflicting_database(history: SQLiteHistory, adjustments: list[MigrationAdjustment] | None) -> None:
    path = history.path
    marker = history.project / _CONFLICT_MARKER
    members = [p for p in path.parent.glob("*") if is_history_database_name(p.name)]
    undo = history.project / HISTORY_UNDO_RELATIVE_PATH
    if undo.exists():
        members.append(undo)
    if marker.is_symlink():
        raise ValueError("unexpected conflict marker symlink")
    if marker.exists():
        name = json.loads(marker.read_bytes())
        if not isinstance(name, str) or not is_kept_copy_for(name, path.name, 20):
            raise ValueError("invalid history conflict marker")
        kept = path.with_name(name)
    elif members:
        kept = allocate_kept_copy_path(path, 20)
        atomic_write_json(marker, kept.name)
    else:
        return
    if kept.is_symlink():
        raise ValueError("unexpected conflict directory symlink")
    kept.mkdir(exist_ok=True)
    for member in members:
        destination = kept / member.name
        if destination.exists():
            raise FileExistsError(destination)
        member.rename(destination)
    if adjustments is not None:
        item = MigrationAdjustment(
            code="history_conflict_preserved",
            file=path.relative_to(history.project).as_posix(),
            backup=(kept / path.name if (kept / path.name).is_file() else kept).relative_to(history.project).as_posix(),
        )
        if item not in adjustments:
            adjustments.append(item)


def prepare_legacy_history(project_dir: Path, version: int, adjustments: list[MigrationAdjustment]) -> None:
    history = SQLiteHistory(project_dir)
    source = history.path.with_name("versions.json")
    with project_metadata_lock(project_dir, journal=False):
        if (project_dir / _HISTORY_MARKER).exists():
            return
        _assert_no_history_symlinks(history.path.parent, history.path, source)
        if version == 20:
            ensure_versioned_backup(project_dir / "project.json", version)
        if source.exists():
            ensure_versioned_backup(source, version)
            if not isinstance(json.loads(source.read_bytes()), dict):
                raise ValueError("invalid version history: versions.json")
        _preserve_conflicting_database(history, adjustments)


def migrate_v20_to_v21(
    project_dir: Path, *, record: list[MigrationAdjustment] | None = None, restoring_snapshot: bool = False
) -> None:
    project_file = project_dir / "project.json"
    with project_metadata_lock(project_dir, journal=False):
        project = json.loads(project_file.read_bytes())
        if parse_project_schema_version(project) >= SQLITE_HISTORY_SCHEMA_VERSION:
            return
        history = SQLiteHistory(project_dir)
        source = history.path.with_name("versions.json")
        pending = project_dir / _HISTORY_MARKER
        _assert_no_history_symlinks(source, pending, history.path.parent, history.path)
        ensure_versioned_backup(project_file, 20)
        ensure_versioned_backup(source, 20)
        if pending.exists():
            checkpoint = json.loads(pending.read_bytes())
            project = checkpoint["project"]
            if record is not None:
                for raw in checkpoint["adjusted"]:
                    saved = MigrationAdjustment.model_validate(raw)
                    if saved not in record:
                        record.append(saved)
        else:
            if source.exists():
                history.read_exchange(source)
            _preserve_conflicting_database(history, record)
        if not restoring_snapshot and not pending.exists():
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
        if not pending.exists():
            upgrade_audio_delivery(project_dir, project)
            atomic_write_json(pending, {"project": project, "adjusted": [item.model_dump() for item in record or ()]})
        if not history.is_enabled():
            records = history.read_exchange(source) if source.exists() else {}
            history.create(records)
        source.unlink(missing_ok=True)  # hard-delete: infra -- versioned backup preserves the legacy history
        atomic_write_json(project_file, {**project, "schema_version": SQLITE_HISTORY_SCHEMA_VERSION})


def activate_current_history(project_dir: Path) -> bool:
    history = SQLiteHistory(project_dir)
    source = history.path.with_name("versions.json")
    with project_metadata_lock(project_dir, journal=False):
        _assert_no_history_symlinks(history.path.parent, history.path, source)
        if not source.exists():
            if not history.is_enabled():
                raise ValueError("missing v21 version history: versions/history.sqlite3")
            return False
        ensure_versioned_backup(source, SQLITE_HISTORY_SCHEMA_VERSION)
        if not history.is_enabled():
            history.create(history.read_exchange(source))
        else:
            try:
                duplicate = history.read_exchange(source) == history.snapshot()
            except ValueError:
                duplicate = False
            if not duplicate:
                kept = set_aside_as_kept_copy(source, SQLITE_HISTORY_SCHEMA_VERSION)
                try:
                    extend_migration_report(
                        project_dir,
                        adjusted=[
                            MigrationAdjustment(
                                code="history_conflict_preserved",
                                file=source.relative_to(project_dir).as_posix(),
                                backup=kept.relative_to(project_dir).as_posix(),
                            )
                        ],
                    )
                except Exception:
                    kept.replace(source)
                    raise
                return True
        source.unlink()  # hard-delete: infra -- versioned backup preserves the exchange history
        return True


def finish_history_upgrade(project_dir: Path) -> None:
    pending = project_dir / _HISTORY_MARKER
    if not pending.exists():
        return
    with project_metadata_lock(project_dir, journal=False):
        if not pending.exists():
            return
        if pending.is_symlink():
            raise ValueError("unexpected history marker symlink")
        checkpoint = json.loads(pending.read_bytes())
        extend_migration_report(
            project_dir, adjusted=[MigrationAdjustment.model_validate(raw) for raw in checkpoint["adjusted"]]
        )
        conflict = project_dir / _CONFLICT_MARKER
        conflict.unlink(missing_ok=True)  # hard-delete: infra -- completed conflict recovery marker
        pending.unlink()  # hard-delete: infra -- completed migration marker
