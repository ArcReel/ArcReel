"""Deleted projects, removed whole but still recoverable (``docs/adr/0107``).

Deletion/overwrite moves entire directories including project trash into data-root
``deleted_projects/<墓碑名>/``. Directory names are DB-record tombstones
(``lib.db.repositories.project_records``), used to rename records back on restoration. Retain 30 days
from deletion; restore whole directories, suffix occupied names, then run startup migrations.

Restoration/permanent deletion first rename whole directories; the first wins, the other gets absent,
preventing restoration of partly deleted projects.

This module performs actual permanent deletion and is exempt from ``scripts/audit_conventions.py`` hard-delete checks."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.db.repositories.project_records import parse_deleted_project_name
from lib.infra.api_errors import NotFoundError
from lib.infra.data_root_layout import PROJECT_FILENAME, DataRootLayout
from lib.project.project_migrations.runner import RecordedEpisodeIds, migrate_project_with_verdict
from lib.project.project_migrations.staged_swap import tree_size
from lib.project.project_trash import RETENTION
from lib.script.source_loader.migration import migrate_project_source_encoding_once

logger = logging.getLogger(__name__)

__all__ = [
    "RETENTION",
    "DeletedProject",
    "list_entries",
    "migrate_restored",
    "move_in",
    "move_out",
    "purge",
    "purge_expired",
]

_PURGING_PREFIX = ".purging-"


@dataclass(frozen=True, slots=True)
class DeletedProject:
    """One deleted project: ``id`` is tombstone, ``name`` predeletion name, ``size`` directory bytes."""

    id: str
    name: str
    title: str
    deleted_at: datetime
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "deleted_at": self.deleted_at.isoformat(),
            "expires_at": (self.deleted_at + RETENTION).isoformat(),
            "size": self.size,
        }


def move_in(layout: DataRootLayout, project_dir: Path, tombstone: str) -> Path:
    """Move whole ``project_dir`` to deleted ``tombstone``, returning new location; error if that tombstone already exists."""
    target = layout.deleted_projects_dir / tombstone
    layout.deleted_projects_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    # Propagate rename failures (e.g. open files on Windows); callers roll back records, leaving projects in place.
    project_dir.rename(target)
    return target


def move_back(layout: DataRootLayout, project_dir: Path, tombstone: str) -> None:
    """Undo :func:`move_in` after record-commit failure, moving ``tombstone`` back to ``project_dir``; no-op if never moved."""
    moved = layout.deleted_projects_dir / tombstone
    if moved.is_dir() and not (project_dir.exists() or project_dir.is_symlink()):
        moved.rename(project_dir)


def list_entries(layout: DataRootLayout) -> list[DeletedProject]:
    """All deleted projects, newest deletion first.

    Directory traversal for sizes blocks on disk; async callers offload to threads."""
    try:
        children = list(layout.deleted_projects_dir.iterdir())
    except FileNotFoundError:
        return []
    entries: list[DeletedProject] = []
    for child in children:
        parsed = parse_deleted_project_name(child.name)
        if parsed is None or child.is_symlink() or not child.is_dir():
            continue
        name, deleted_at = parsed
        entries.append(
            DeletedProject(
                id=child.name,
                name=name,
                title=_title(child) or name,
                deleted_at=deleted_at,
                size=tree_size(child),
            )
        )
    return sorted(entries, key=lambda entry: (entry.deleted_at, entry.id), reverse=True)


def move_out(layout: DataRootLayout, entry_id: str) -> Path:
    """Restore whole project directory; preserve available original names, otherwise append ``-2``, ``-3``; return restored path."""
    source, name = _require(layout, entry_id)
    candidate, suffix = name, 2
    # Case-insensitive exists() also detects case-only project collisions.
    while (layout.projects_dir / candidate).exists():
        candidate, suffix = f"{name}-{suffix}", suffix + 1
    target = layout.projects_dir / candidate
    # shortcut: tiny dedup/rename window lets POSIX rename replace a newly created empty same-name directory; serialize with project locks if concurrent same-name restore/create is reported.
    try:
        source.rename(target)
    except FileNotFoundError as exc:
        raise NotFoundError("deleted_project_not_found", id=entry_id) from exc
    return target


def migrate_restored(project_dir: Path, *, recorded_episode_ids: RecordedEpisodeIds | None = None) -> None:
    """Run startup migrations on restored projects: source encoding, then current schema.

    Like startup, migration failures do not raise. Store verdicts beside projects; generation refuses work
    and UI reports migration failure."""
    migrate_project_source_encoding_once(project_dir)
    migrate_project_with_verdict(project_dir, recorded_episode_ids=recorded_episode_ids)


def purge(layout: DataRootLayout, entry_id: str) -> None:
    """Permanently delete a deleted project."""
    source, _name = _require(layout, entry_id)
    claimed = layout.deleted_projects_dir / f"{_PURGING_PREFIX}{entry_id}"
    try:
        source.rename(claimed)
    except FileNotFoundError as exc:
        raise NotFoundError("deleted_project_not_found", id=entry_id) from exc
    shutil.rmtree(claimed)


def purge_expired(layout: DataRootLayout, *, now: datetime | None = None) -> list[str]:
    """Permanently delete expired projects, returning tombstones; also clean remnants of interrupted prior purges."""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    purged: list[str] = []
    for entry in list_entries(layout):
        if entry.deleted_at > cutoff:
            continue
        try:
            purge(layout, entry.id)
            purged.append(entry.id)
        except NotFoundError:
            continue
    for leftover in layout.deleted_projects_dir.glob(f"{_PURGING_PREFIX}*"):
        shutil.rmtree(leftover, ignore_errors=True)
    return purged


def _require(layout: DataRootLayout, entry_id: str) -> tuple[Path, str]:
    """Deleted directory and original name; only tombstone-shaped ``entry_id`` enters paths, preventing access elsewhere in data roots."""
    parsed = parse_deleted_project_name(entry_id)
    path = layout.deleted_projects_dir / entry_id
    if parsed is None or path.is_symlink() or not path.is_dir():
        raise NotFoundError("deleted_project_not_found", id=entry_id)
    return path, parsed[0]


def _title(project_dir: Path) -> str | None:
    try:
        title = json.loads((project_dir / PROJECT_FILENAME).read_text(encoding="utf-8")).get("title")
    except (OSError, ValueError, AttributeError):
        return None
    return title if isinstance(title, str) and title.strip() else None
