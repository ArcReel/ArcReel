"""Project trash: sole exit for every project-local deletion (``docs/adr/0107``).

Public operations: :func:`record_deletion`, :func:`list_records`, :func:`restore`, :func:`purge`,
:func:`empty`, and :func:`purge_expired`. Records live in ``<项目>/.arcreel/trash/<记录 ID>/``:
``record.json`` stores object descriptions, files, schema, deletion time, initiator, and byte size.
Deleted files retain relative paths under ``files/``; structural deletions additionally copy predeletion
:func:`structure_files` under ``snapshot/`` for :mod:`lib.project.trash_restore`. Degraded restoration
leaves unrestorable structures as ``download_only`` records. Private directories are absent from
archives/validation/file views and move with whole deleted projects.

Actual permanent project-data deletion happens here, exempt from ``scripts/audit_conventions.py`` hard-delete checks."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal

from lib.agent.agent_memory_paths import ARCREEL_DIRNAME
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_ledger import SOURCE_TEXT_SUFFIXES
from lib.infra.api_errors import ConflictError, NotFoundError
from lib.infra.json_io import atomic_write_json
from lib.project.project_change_hints import (
    emit_project_change_batch,
    emit_project_change_hint,
    get_project_change_source,
)
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.project.resource_paths import resource_of_path, resource_relative_path

__all__ = [
    "FILES_DIRNAME",
    "RETENTION",
    "SNAPSHOT_DIRNAME",
    "TRASH_DIRNAME",
    "TRASH_ENTITY_TYPE",
    "RestoreResult",
    "RestoredFile",
    "TrashRecord",
    "empty",
    "is_structure_file",
    "keep_for_download",
    "land_files",
    "list_records",
    "open_record_file",
    "purge",
    "purge_expired",
    "record_deletion",
    "restore",
    "structure_files",
    "trash_dir",
    "withdraw",
]

TRASH_DIRNAME = "trash"
#: Retain records 30 days from deletion.
RETENTION = timedelta(days=30)
#: Record-format version, independent of project ``schema_version``.
RECORD_FORMAT = 1

_RECORD_FILE = "record.json"
FILES_DIRNAME = "files"
SNAPSHOT_DIRNAME = "snapshot"
_RECORD_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")

Origin = Literal["user", "agent"]

#: Trash project-change entries are refresh signals like terminal tasks/accounting settlement, never notifications/focus.
TRASH_ENTITY_TYPE = "trash"


def trash_dir(project_dir: Path) -> Path:
    return project_dir / ARCREEL_DIRNAME / TRASH_DIRNAME


@dataclass(frozen=True, slots=True)
class TrashRecord:
    """One deletion record: ``object`` includes at least ``kind``; ``files`` holds predeletion project-relative paths."""

    id: str
    object: Mapping[str, Any]
    files: tuple[str, ...]
    schema_version: int
    deleted_at: datetime
    origin: Origin
    size: int
    has_snapshot: bool = False
    #: Remaining structures after degradation are download-only, never restorable.
    download_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": dict(self.object),
            "files": list(self.files),
            "schema_version": self.schema_version,
            "deleted_at": self.deleted_at.isoformat(),
            "origin": self.origin,
            "size": self.size,
            "has_snapshot": self.has_snapshot,
            "download_only": self.download_only,
        }


@dataclass(frozen=True, slots=True)
class RestoredFile:
    """One restored file location: ``as_history`` means a historical version of ``resource``."""

    original: str
    path: str
    as_history: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"original": self.original, "path": self.path, "as_history": self.as_history}


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """One restoration result. ``object`` is the restored description, differing after rename/reidentification.

    ``degraded`` means files-only restoration instead of original structures; ``download_only`` contains
    structure files retained in records for download."""

    object: Mapping[str, Any]
    restored: list[RestoredFile]
    degraded: bool = False
    download_only: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "object": dict(self.object),
            "restored": [item.to_dict() for item in self.restored],
            "degraded": self.degraded,
            "download_only": list(self.download_only),
        }


# ---------------------------------------------------------------- Records

#: Individual snapshot files: ``project.json``, manifests, history.
_STRUCTURE_FILES = ("project.json", ".arcreel_artifacts.json", "versions/versions.json")
#: Snapshot directories/extensions: scripts, planning drafts, timelines, source text.
_STRUCTURE_TREES: Mapping[str, frozenset[str]] = {
    "scripts": frozenset({".json"}),
    "drafts": frozenset({".json", ".md", ".txt"}),
    "edit_timelines": frozenset({".json"}),
    "source": frozenset(SOURCE_TEXT_SUFFIXES),
}


def is_structure_file(rel: str) -> bool:
    """Whether project-relative POSIX ``rel`` is structural, matching :func:`structure_files`."""
    if rel in _STRUCTURE_FILES:
        return True
    top, *rest = rel.split("/")
    suffixes = _STRUCTURE_TREES.get(top)
    return (
        suffixes is not None
        and bool(rest)
        and not any(part.startswith(".") for part in rest)
        and PurePosixPath(rel).suffix.lower() in suffixes
    )


def structure_files(project_dir: Path) -> list[str]:
    """Current structural files as project-relative POSIX paths, for snapshots excluding other media.

    Regular files only, without symlinks or ``.``-prefixed locks/hidden files."""
    found = [rel for rel in _STRUCTURE_FILES if _is_plain_file(project_dir / rel)]
    for top, suffixes in _STRUCTURE_TREES.items():
        root = project_dir / top
        if root.is_symlink() or not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if not name.startswith(".") and path.suffix.lower() in suffixes and _is_plain_file(path):
                    found.append(path.relative_to(project_dir).as_posix())
    return found


def record_deletion(
    project_dir: Path,
    obj: Mapping[str, Any],
    files: Sequence[str],
    *,
    snapshot: bool = False,
    sources: Mapping[str, Path] | None = None,
    before: Mapping[str, bytes] | None = None,
) -> TrashRecord:
    """Move ``files`` (regular project-relative POSIX files) into a new deletion record.

    Callers hold project locks and established deletability in the same critical section. Initiators use
    current change sources. ``snapshot`` first copies predeletion structures for identity-based extraction,
    without duplicating deleted files. ``sources`` locates files not yet at recorded paths (e.g. generated
    staging output), retaining original paths in records. For structures already changed there, such as
    ``project.json`` on episode deletion, ``before`` supplies pre-edit bytes. Structural-only objects need no
    files. Any move failure restores moved files without partial records; after creation, failed commit
    compensates through :func:`withdraw`."""
    if "kind" not in obj or not (files or snapshot):
        raise ValueError("删除记录需要对象类型与至少一个被删文件")
    record_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    record_dir = trash_dir(project_dir) / record_id
    stored = record_dir / FILES_DIRNAME
    moved: list[tuple[Path, Path]] = []
    try:
        size = 0
        stored.mkdir(
            parents=True
        )  # Structural-only deletions also create this directory for restoration materialization.
        if snapshot:
            deleted = set(files)
            # Last deregistration removes manifests, but predeletion structures must still enter snapshots.
            for rel in sorted(set(structure_files(project_dir)).union(before or ())):
                if rel not in deleted and is_structure_file(rel):
                    target = record_dir / SNAPSHOT_DIRNAME / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if before is not None and rel in before:
                        target.write_bytes(before[rel])
                    else:
                        shutil.copy2(project_dir / rel, target)
                    size += target.stat().st_size
        for rel in files:
            source = (sources or {}).get(rel) or project_dir / _checked_rel(rel)
            if source.is_symlink() or not source.is_file():
                raise FileNotFoundError(rel)
            target = stored / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            size += source.stat().st_size
            os.replace(source, target)
            moved.append((target, source))
        record = TrashRecord(
            id=record_id,
            object=dict(obj),
            files=tuple(files),
            schema_version=CURRENT_PROJECT_SCHEMA_VERSION,
            deleted_at=datetime.now(UTC),
            origin="user" if get_project_change_source() == "webui" else "agent",
            size=size,
            has_snapshot=snapshot,
        )
        # Write record.json last; directories without it are not records and are skipped.
        atomic_write_json(record_dir / _RECORD_FILE, {"format": RECORD_FORMAT, **record.to_dict()})
    except BaseException:
        for target, source in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, source)
        shutil.rmtree(record_dir, ignore_errors=True)
        raise
    _notify(project_dir)
    return record


def withdraw(project_dir: Path, record_id: str) -> None:
    """Withdraw just-created records in the same critical section, returning files and removing records; compensates failed commits under caller-held project locks."""
    record = require_record(project_dir, record_id)
    record_dir = trash_dir(project_dir) / record.id
    for rel in record.files:
        target = project_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(record_dir / FILES_DIRNAME / rel, target)
    shutil.rmtree(record_dir)
    _notify(project_dir)


def list_records(project_dir: Path) -> list[TrashRecord]:
    """All records, newest first; skip unreadable records."""
    root = trash_dir(project_dir)
    if not root.is_dir():
        return []
    records = [record for entry in root.iterdir() if (record := _load(entry)) is not None]
    return sorted(records, key=lambda record: record.deleted_at, reverse=True)


# ---------------------------------------------------------------- Restoration and cleanup


def restore(project_dir: Path, record_id: str) -> RestoreResult:
    """Restore records, consuming on success; failure preserves projects/records unchanged.

    Structural snapshots use :mod:`lib.project.trash_restore`, degrading to files-only when objects cannot
    restore original structure. Files-only records use :func:`land_files`; download-only records cannot restore."""
    record = require_record(project_dir, record_id)
    if record.download_only:
        raise ConflictError("trash_restore_download_only", id=record.id)
    if record.has_snapshot:
        # Pipeline landing rules depend on domain modules; top-level imports would cycle.
        from lib.project.trash_restore import restore_structural

        result = restore_structural(project_dir, record)
        _notify(project_dir)
        return result
    with project_metadata_lock(project_dir):
        record = require_record(project_dir, record_id)
        record_dir = trash_dir(project_dir) / record.id
        restored = land_files(
            project_dir,
            [(record_dir / FILES_DIRNAME / rel, rel) for rel in record.files],
            record.object,
            then=lambda: shutil.rmtree(record_dir),
        )
    _notify(project_dir)
    return RestoreResult(record.object, restored)


def land_files(
    project_dir: Path, files: Sequence[tuple[Path, str]], obj: Mapping[str, Any], *, then: Callable[[], None]
) -> list[RestoredFile]:
    """Restore ``(待加回的文件, 项目内相对路径)`` individually, then ``then`` after all succeed; callers hold project locks.

    Use free original paths. Occupied paths belonging to current resources restore as history; other
    collisions use new filenames. Never overwrite existing files. Any failure moves additions back,
    returning projects to pre-restoration state."""
    for _source, rel in files:
        check_restore_target(project_dir, rel)
    moved: list[tuple[Path, Path]] = []
    with VersionManager(project_dir).history_transaction():
        try:
            restored = [_land_file(project_dir, source, rel, obj, moved) for source, rel in files]
            then()
        except BaseException:
            for source, target in reversed(moved):
                os.replace(target, source)
            raise
    return restored


def keep_for_download(project_dir: Path, record_id: str, files: Sequence[str]) -> None:
    """After degraded restoration, retain only ``files`` (record structures) as download-only; callers hold project locks."""
    record = require_record(project_dir, record_id)
    record_dir = trash_dir(project_dir) / record.id
    stored = record_dir / FILES_DIRNAME
    kept = replace(
        record,
        files=tuple(files),
        size=sum((stored / rel).stat().st_size for rel in files),
        has_snapshot=False,
        download_only=True,
    )
    # Update record.json first, so interrupted cleanup still lists only remaining files.
    atomic_write_json(record_dir / _RECORD_FILE, {"format": RECORD_FORMAT, **kept.to_dict()})
    shutil.rmtree(record_dir / SNAPSHOT_DIRNAME, ignore_errors=True)
    for rel in record.files:
        if rel not in kept.files:
            (stored / rel).unlink(missing_ok=True)


def open_record_file(project_dir: Path, record_id: str, rel: str) -> tuple[BinaryIO, os.stat_result]:
    """Open deleted-record files for download without symlinks; callers close handles."""
    # File views depend on this module; top-level imports would cycle.
    from lib.project.file_view import open_project_file

    record = require_record(project_dir, record_id)
    if rel not in record.files:
        raise NotFoundError("file_not_found", path=rel)
    try:
        return open_project_file(trash_dir(project_dir) / record.id / FILES_DIRNAME, rel)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise NotFoundError("file_not_found", path=rel) from exc


def consume(project_dir: Path, record_id: str) -> None:
    """Remove records after successful pipeline landing under project locks; concurrent consumption/cleanup raises ``trash_record_not_found``."""
    shutil.rmtree(trash_dir(project_dir) / require_record(project_dir, record_id).id)


def purge(project_dir: Path, record_id: str) -> None:
    """Permanently delete a record."""
    with project_metadata_lock(project_dir):
        record = require_record(project_dir, record_id)
        shutil.rmtree(trash_dir(project_dir) / record.id)
    _notify(project_dir)


def empty(project_dir: Path) -> int:
    """Empty project trash and return removed-record count."""
    with project_metadata_lock(project_dir):
        records = list_records(project_dir)
        shutil.rmtree(trash_dir(project_dir), ignore_errors=True)
    _notify(project_dir)
    return len(records)


def purge_expired(project_dir: Path, *, now: datetime | None = None) -> int:
    """Remove records older than retention and return count."""
    if not trash_dir(project_dir).is_dir():
        return 0
    cutoff = (now or datetime.now(UTC)) - RETENTION
    with project_metadata_lock(project_dir):
        expired = [record for record in list_records(project_dir) if record.deleted_at <= cutoff]
        for record in expired:
            shutil.rmtree(trash_dir(project_dir) / record.id)
    if expired:
        _notify(project_dir)
    return len(expired)


# ---------------------------------------------------------------- Internal


def _notify(project_dir: Path) -> None:
    """Broadcast trash changes after sending same-source hints for deleted/restored project files.

    This batch describes trash only; event-service sweeps/polls discover file-related changes (drafts,
    registrations), attributing source from preread hints. Hints must precede batches, or Web deletions
    would appear as external changes in sweeps."""
    emit_project_change_hint(project_dir.name)
    change = {"entity_type": TRASH_ENTITY_TYPE, "action": "updated", "entity_id": "trash", "label": "trash"}
    emit_project_change_batch(project_dir.name, [{**change, "focus": None, "important": False}])


def _checked_rel(rel: str) -> str:
    path = PurePosixPath(rel)
    if not rel or "\\" in rel or path.is_absolute() or any(part in {"", ".", ".."} for part in rel.split("/")):
        raise ValueError(f"非法的项目内路径: {rel!r}")
    return rel


def _load(record_dir: Path) -> TrashRecord | None:
    if not _RECORD_ID_RE.match(record_dir.name) or record_dir.is_symlink():
        return None
    try:
        data = json.loads((record_dir / _RECORD_FILE).read_text(encoding="utf-8"))
        return TrashRecord(
            id=record_dir.name,
            object=data["object"],
            files=tuple(_checked_rel(rel) for rel in data["files"]),
            schema_version=int(data["schema_version"]),
            deleted_at=datetime.fromisoformat(data["deleted_at"]),
            origin=data["origin"],
            size=int(data["size"]),
            has_snapshot=bool(data.get("has_snapshot", False)),
            download_only=bool(data.get("download_only", False)),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def require_record(project_dir: Path, record_id: str) -> TrashRecord:
    """Read records by ID; absent raises ``trash_record_not_found``."""
    record = _load(trash_dir(project_dir) / record_id) if _RECORD_ID_RE.match(record_id) else None
    if record is None:
        raise NotFoundError("trash_record_not_found", id=record_id)
    return record


def _is_plain_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def check_restore_target(project_dir: Path, rel: str) -> None:
    """Landing ``rel`` parent directories must not be symlinks; otherwise raise ``trash_restore_target_blocked``."""
    target = project_dir / rel
    if any(parent.is_symlink() for parent in target.parents if parent.is_relative_to(project_dir)):
        # Parent directories replaced with symlinks after deletion must not redirect restoration writes.
        raise ConflictError("trash_restore_target_blocked", path=rel)


def _land_file(
    project_dir: Path, stored: Path, rel: str, obj: Mapping[str, Any], moved: list[tuple[Path, Path]]
) -> RestoredFile:
    """Restore one file, appending ``(记录内位置, 落点)`` to ``moved`` for rollback."""
    target = project_dir / rel
    if obj.get("kind") == "version" and obj.get("resource_type") in VersionManager.RESOURCE_TYPES:
        # Reregister deleted history: returning snapshots alone would leave them absent from version records.
        resource_type, resource_id = str(obj["resource_type"]), str(obj["resource_id"])
        path = VersionManager(project_dir).add_history_version(
            resource_type,
            resource_id,
            stored,
            current_file=project_dir / resource_relative_path(resource_type, resource_id),
            prompt=str(obj.get("prompt") or ""),
            metadata=obj["metadata"] if isinstance(obj.get("metadata"), dict) else None,
        )
        return RestoredFile(rel, path, as_history=True)
    if not (target.exists() or target.is_symlink()):
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stored, target)
        moved.append((stored, target))
        return RestoredFile(rel, rel)
    resource = resource_of_path(rel)
    if resource is not None and target.is_file() and not target.is_symlink():
        path = VersionManager(project_dir).add_history_version(*resource, stored, current_file=target)
        return RestoredFile(rel, path, as_history=True)
    path = PurePosixPath(rel)
    for n in range(1, 1000):
        suffix = f" ({n}){path.suffix}"
        stem = path.stem
        if obj.get("kind") == "memory":
            # Memory storage depends on trash; read landing filename constraints lazily to avoid import cycles.
            from lib.agent.agent_memory_store import FILENAME_MAX_LENGTH

            stem = stem[: FILENAME_MAX_LENGTH - len(suffix)]
        candidate = path.with_name(f"{stem}{suffix}").as_posix()
        if not ((project_dir / candidate).exists() or (project_dir / candidate).is_symlink()):
            os.replace(stored, project_dir / candidate)
            moved.append((stored, project_dir / candidate))
            return RestoredFile(rel, candidate)
    raise FileExistsError(rel)
