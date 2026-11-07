"""Restoration pipeline: sole path for structural-snapshot deletion records (``docs/adr/0107``).

1. Materialize snapshots/deleted files into a temporary project under project trash.
2. Run encoding/schema migrations, as in archive import (no-op for same schemas). Do not run archive
   tree repair/validation/activation, which assume complete projects and falsely fail temporary projects missing other media.
3. Extract objects by identity, not original paths, using type-specific landing rules.
4. Acquire declared domain locks, then project locks; recheck record existence, add objects, and register
   restored artifacts. Landing failure removes newly created files and retains records unchanged.

Migration failure/unextractable objects degrade unless records contain no deleted files, in which case
retain them. ``OSError`` I/O failures except missing files do not degrade: disk/permissions can be fixed
and retried; propagate errors with records intact. Degradation restores deleted nonstructure files at
postmigration paths (or failure-time paths if migration failed), using files-only rules: original paths
if free, otherwise history/new names. Never restore deleted structure files; retain them as download-only.

Temporary migration project state (e.g. episode high water) is discarded; landing restores extracted
objects only. Register new types in :func:`_rules` with extractors returning :class:`Restoration`."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.api_errors import BadRequestError, ConflictError
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.json_io import atomic_write_json
from lib.project import project_trash
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_trash import RestoredFile, RestoreResult, TrashRecord
from lib.script.source_loader.migration import migrate_project_source_encoding

__all__ = ["Landing", "Restoration", "RestoreBlocked", "TakeRule", "restore_structural"]

logger = logging.getLogger(__name__)

#: Migrations may edit text in place; copy these extensions, hardlink media to avoid copying large files.
_COPIED_SUFFIXES = frozenset({".json", ".md", ".txt"})
#: Migration backups (``<名>.bak.v<N>-<ts>``) and set-aside ``.kept.`` copies are not deleted files.
_MIGRATION_COPY_RE = re.compile(r"\.(?:bak|kept)\.v\d+-\d+")


class RestoreBlocked(ConflictError):
    """Objects temporarily unrestorable (e.g. trashed parent episodes); extractor raises without degradation, retaining records unchanged."""


class Landing:
    """Files/directories newly created by one restoration; :meth:`rollback` removes them to restore prior state, never overwriting existing files."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.restored: list[RestoredFile] = []
        self._created: list[Path] = []

    def free(self, rel: str) -> bool:
        target = self.project_dir / rel
        return not (target.exists() or target.is_symlink())

    def place(self, staged: Path, rel: str, *, original: str) -> None:
        """Move temporary ``staged`` to current-project ``rel``; ``rel`` must be free."""
        target = self._target(rel)
        os.replace(staged, target)
        self._done(target, rel, original)

    def write_json(self, rel: str, data: Any, *, original: str) -> None:
        target = self._target(rel)
        atomic_write_json(target, data)
        self._done(target, rel, original)

    def rollback(self) -> None:
        for path in reversed(self._created):
            if path.is_dir() and not path.is_symlink():
                # Remove created directories too, avoiding identity conflicts on retry; retain directories others populated meanwhile.
                with suppress(OSError):
                    path.rmdir()  # hard-delete: rollback -- Only newly created directories still empty.
            else:
                # hard-delete: rollback -- Remove this attempt's new files after landing failure, preserving record originals.
                path.unlink(missing_ok=True)

    def _target(self, rel: str) -> Path:
        project_trash.check_restore_target(self.project_dir, rel)
        target = self.project_dir / rel
        if not self.free(rel):
            raise FileExistsError(rel)
        missing = [parent for parent in target.parents if not parent.exists()]
        target.parent.mkdir(parents=True, exist_ok=True)
        self._created.extend(reversed(missing))
        return target

    def _done(self, target: Path, rel: str, original: str) -> None:
        self._created.append(target)
        self.restored.append(RestoredFile(original, rel))


@dataclass(frozen=True, slots=True)
class Restoration:
    """One object extracted from temporary projects, awaiting landing.

    Run ``land`` within ``locks`` and project locks, writing through :class:`Landing`, registering artifacts
    last, and returning restored object descriptions after rename/reidentification. Exceptions remove
    new files. Run ``after`` outside locks after successful landing (domain refresh signals, etc.)."""

    land: Callable[[Landing], Mapping[str, Any] | None]
    locks: Callable[[], AbstractContextManager[object]] = nullcontext
    after: Callable[[], None] = field(default=lambda: None)


#: ``(当前项目目录, 迁移后的临时项目目录, 对象描述) -> Restoration``; absent objects raise ``trash_restore_failed``.
TakeRule = Callable[[Path, Path, Mapping[str, Any]], Restoration]


def _rules() -> Mapping[str, TakeRule]:
    # Extract/landing rules need Landing / Restoration, so top-level imports would cycle.
    from lib.edit_timeline.trash import EDIT_TIMELINE_KIND, take_edit_timeline
    from lib.episode.episode_trash import EPISODE_KIND, take_episode
    from lib.episode.source_trash import SOURCE_FILE_KIND, take_source_file
    from lib.project import media_trash
    from lib.project.asset_trash import ASSET_DERIVATIVE_KIND, ASSET_KIND, take_asset, take_asset_derivative
    from lib.project.script_entry_trash import SCRIPT_ENTRY_KIND, take_script_entry
    from lib.script.planning_draft_trash import PLANNING_DRAFT_KIND, take_planning_draft

    return {
        EDIT_TIMELINE_KIND: take_edit_timeline,
        EPISODE_KIND: take_episode,
        media_trash.GRID_KIND: media_trash.take_grid,
        media_trash.END_FRAME_KIND: media_trash.take_end_frame,
        media_trash.REFERENCE_AUDIO_KIND: media_trash.take_reference_audio,
        SCRIPT_ENTRY_KIND: take_script_entry,
        SOURCE_FILE_KIND: take_source_file,
        PLANNING_DRAFT_KIND: take_planning_draft,
        ASSET_KIND: take_asset,
        ASSET_DERIVATIVE_KIND: take_asset_derivative,
    }


def restore_structural(project_dir: Path, record: TrashRecord) -> RestoreResult:
    """Restore one structural record into ``project_dir``; success consumes it, degradation retains structures only. Callers hold no locks."""
    rule = _rules().get(str(record.object.get("kind")))
    if rule is None:
        raise BadRequestError("trash_restore_unsupported")
    record_dir = project_trash.trash_dir(project_dir) / record.id
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=record_dir.parent) as tmp:
        # Temporary projects follow data-root layout so landing rules can use ProjectManager domain flows (e.g. asset rename cascades).
        staged = DataRootLayout(Path(tmp)).projects_dir / project_dir.name
        with project_metadata_lock(project_dir):
            # Prevent purge/other restoration consuming records during materialization.
            _require_unchanged(project_dir, record)
            _stage(record_dir, staged)
        try:
            migrate_project_source_encoding(staged)
            migrate_project_dir(staged)
            restoration = rule(project_dir, staged, record.object)
        except RestoreBlocked:
            raise
        except Exception as exc:
            if isinstance(exc, OSError) and not isinstance(exc, FileNotFoundError):
                # Fixed I/O faults permit structural retries; degradation would consume records/lose structures. Missing files are permanent, so degrade normally.
                raise
            if not record.files:
                # Structure-only objects add nothing on degradation; consumption would lose them. Retain records for later retries.
                logger.warning("删除记录 %s 无法按原结构恢复，记录保留", record.id, exc_info=True)
                raise ConflictError("trash_restore_unsupported") from exc
            # Log failure causes; creators see degradation results.
            logger.warning("删除记录 %s 无法按原结构恢复，降级为只恢复文件", record.id, exc_info=True)
            return _restore_files_only(project_dir, record, staged)
        landing = Landing(project_dir)
        with restoration.locks(), project_metadata_lock(project_dir):
            # Migration released locks; concurrent restore/expiry cleanup may have consumed records, so landing would create recordless objects.
            _require_unchanged(project_dir, record)
            try:
                landed = restoration.land(landing)
                project_trash.consume(project_dir, record.id)
            except BaseException:
                landing.rollback()
                raise
    restoration.after()
    return RestoreResult(landed or record.object, landing.restored)


def _restore_files_only(project_dir: Path, record: TrashRecord, staged: Path) -> RestoreResult:
    """Degrade by restoring deleted nonstructure files from temporary projects at their current paths; keep record structures download-only."""
    files = [(staged / rel, rel) for rel in _non_structure_files(staged)]
    kept = tuple(rel for rel in record.files if project_trash.is_structure_file(rel))

    def settle() -> None:
        if kept:
            project_trash.keep_for_download(project_dir, record.id, kept)
        else:
            project_trash.consume(project_dir, record.id)

    with project_metadata_lock(project_dir):
        _require_unchanged(project_dir, record)
        restored = project_trash.land_files(project_dir, files, record.object, then=settle)
    return RestoreResult(record.object, restored, degraded=True, download_only=kept)


def _require_unchanged(project_dir: Path, record: TrashRecord) -> None:
    """Under caller-held project locks, require records still exist unchanged since read; absent raises ``trash_record_not_found``."""
    if project_trash.require_record(project_dir, record.id) != record:
        # Only concurrent degraded restoration rewrites records into download-only form.
        raise ConflictError("trash_restore_download_only", id=record.id)


def _non_structure_files(staged: Path) -> list[str]:
    """Temporary-project nonstructure files: snapshots contain structures only, so these are deleted files, possibly relocated by migrations.

    Exclude private areas, hidden root migration records, backups, and symlinks."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(staged):
        top = Path(dirpath) == staged
        dirnames[:] = sorted(
            name for name in dirnames if not (top and name.startswith(".")) and not _MIGRATION_COPY_RE.search(name)
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(staged).as_posix()
            if (
                (top and name.startswith("."))
                or _MIGRATION_COPY_RE.search(name)
                or path.is_symlink()
                or not path.is_file()
                or project_trash.is_structure_file(rel)
            ):
                continue
            found.append(rel)
    return found


def _stage(record_dir: Path, staged: Path) -> None:
    snapshot = record_dir / project_trash.SNAPSHOT_DIRNAME
    if snapshot.is_dir():
        shutil.copytree(snapshot, staged, symlinks=True, dirs_exist_ok=True)
    shutil.copytree(
        record_dir / project_trash.FILES_DIRNAME, staged, symlinks=True, dirs_exist_ok=True, copy_function=_link_or_copy
    )


def _link_or_copy(source: str, target: str) -> None:
    if Path(source).suffix.lower() not in _COPIED_SUFFIXES:
        try:
            os.link(source, target)
            return
        except OSError:
            pass  # Filesystem lacks hardlinks; fall back to copying.
    shutil.copy2(source, target)
