"""Read-only file views: list project directories with roles and open individual files.

Request paths are project-relative POSIX paths without following symlinks. Omit links from listings;
any linked component behaves as absent. Infrastructure (:func:`is_infrastructure_path`) is neither listed nor openable.

Remote-version working files are caches (``docs/adr/0116``, ``docs/adr/0117``). Cleaned caches remain
listed from history as ``remote_only``, using registered object sizes without downloading. Reading
bytes/deleting first retrieves through :func:`local_file`."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
from collections.abc import AsyncGenerator, Iterable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import VersionManager
from lib.infra.api_errors import ConflictError
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.material.formal_objects import formal_object_sizes, iter_version_records
from lib.material.storage import CONTENT_VERSION_ID, ObjectLocator
from lib.material.work_files import local_work_files, remote_work_file_digests, remote_work_files
from lib.project import project_trash
from lib.project.reference_index import FileReference, FileRole, ReferenceIndex, is_infrastructure_path

__all__ = [
    "FileViewEntry",
    "RemoteFile",
    "absent_remote_files",
    "iter_file_chunks",
    "list_directory",
    "list_file_view",
    "local_file",
    "move_file_to_trash",
    "open_project_file",
]

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


@dataclass(frozen=True, slots=True)
class FileViewEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified_at: str
    reference: FileReference | None = None
    #: Remote working file with cleaned cache; retrieve object-storage content on read.
    remote_only: bool = False

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "name": self.name,
            "path": self.path,
            "type": "directory" if self.is_dir else "file",
            "size": self.size,
            "modified_at": self.modified_at,
        }
        if self.reference is not None:
            data["role"] = self.reference.role.value
            data["owner"] = None if self.reference.owner is None else self.reference.owner.to_dict()
            if self.reference.current:
                data["is_current"] = True
        if self.remote_only:
            data["remote_only"] = True
        return data


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """Remote-version working file with cleaned local cache: present in history, absent on local disk."""

    path: str
    stored: ObjectLocator
    #: History creation time (UTC ISO 8601).
    modified_at: str
    #: Registered object size, 0 if unqueried/unregistered.
    size: int = 0


def _parts(rel: str) -> tuple[str, ...]:
    """Validate requested relative paths; empty means project root. Invalid paths count as absent."""
    if not rel:
        return ()
    if "\\" in rel or "\x00" in rel or rel.startswith("/"):
        raise FileNotFoundError(rel)
    parts = tuple(rel.split("/"))
    if any(part in {"", ".", ".."} for part in parts) or is_infrastructure_path(rel):
        raise FileNotFoundError(rel)
    return parts


def _resolve(project_dir: Path, parts: tuple[str, ...]) -> Path:
    """Reject symlinks component by component, then confirm project containment through path-safety helpers."""
    lexical = project_dir
    for part in parts:
        lexical /= part
        if lexical.is_symlink():
            raise FileNotFoundError(part)
    try:
        return safe_join(project_dir, *parts, allow_base=True, must_exist=True)
    except PathTraversalError as exc:
        raise FileNotFoundError("/".join(parts)) from exc


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _visible_size(directory: Path, rel: str) -> int:
    """Visible descendant bytes, excluding symlinks/infrastructure; skip entries moved during scans."""
    try:
        with os.scandir(directory) as scan:
            entries = list(scan)
    except FileNotFoundError:
        return 0
    total = 0
    for entry in entries:
        child = f"{rel}/{entry.name}" if rel else entry.name
        if entry.is_symlink() or is_infrastructure_path(child):
            continue
        if entry.is_dir(follow_symlinks=False):
            total += _visible_size(Path(entry.path), child)
        elif entry.is_file(follow_symlinks=False):
            with contextlib.suppress(FileNotFoundError):
                total += entry.stat(follow_symlinks=False).st_size
    return total


def _record_time(value: object, fallback: float) -> str:
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value)
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    return _iso(fallback)


def _read_object(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def absent_remote_files(project_dir: Path, rel: str = "") -> list[RemoteFile]:
    """Remote working files with cleaned caches at any depth under ``rel``, with unqueried sizes of 0.

    Read only history/``project.json``, without locks/object storage. Atomic replacement guarantees complete
    versions; :func:`list_directory` deduplicates files concurrently retrieved during listing against disk entries."""
    parts = _parts(rel)
    prefix = "".join(f"{part}/" for part in parts)

    def _wanted(path: str) -> bool:
        if not path.startswith(prefix):
            return False
        try:
            _parts(path)
        except FileNotFoundError:  # Dirty history paths/infrastructure.
            return False
        return not os.path.lexists(project_dir / path)

    wanted = {path for path in remote_work_file_digests(project_dir) if _wanted(path)}
    if not wanted:
        return []
    versions_file = project_dir / "versions" / "versions.json"
    versions = _read_object(versions_file)
    project = _read_object(project_dir / "project.json")
    try:
        fallback = versions_file.stat().st_mtime
    except OSError:
        fallback = 0.0
    created = {
        record.get(CONTENT_VERSION_ID): record.get("created_at") for _rt, _rid, record in iter_version_records(versions)
    }
    found: dict[str, RemoteFile] = {}
    for item in remote_work_files(versions, project if isinstance(project, dict) else {}):
        if item.path in wanted and item.path not in found:
            modified_at = _record_time(created.get(item.content_version_id), fallback)
            found[item.path] = RemoteFile(item.path, item.stored, modified_at)
    return sorted(found.values(), key=lambda item: item.path)


def list_directory(
    project_dir: Path,
    rel: str = "",
    *,
    index: ReferenceIndex | None = None,
    remote: Iterable[RemoteFile] = (),
) -> list[FileViewEntry]:
    """List direct children, directories first and each group name-sorted. Files have roles; directories have visible-content totals.

    ``remote`` contains cleaned remote working files (:func:`absent_remote_files` plus sizes). Immediate
    children appear ``remote_only``; deeper files contribute to child-directory sizes. Existing disk entries win same-name conflicts."""
    parts = _parts(rel)
    directory = _resolve(project_dir, parts)
    if not directory.is_dir():
        raise NotADirectoryError(rel)
    index = index or ReferenceIndex.build(project_dir)
    prefix = "".join(f"{part}/" for part in parts)
    remote_here: dict[str, RemoteFile] = {}
    remote_below: dict[str, int] = {}
    for item in remote:
        if not item.path.startswith(prefix):
            continue
        head, nested, _rest = item.path.removeprefix(prefix).partition("/")
        if nested:
            remote_below[head] = remote_below.get(head, 0) + item.size
        elif head:
            remote_here[head] = item
    found: list[FileViewEntry] = []
    on_disk: set[str] = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            on_disk.add(entry.name)
            child = "/".join((*parts, entry.name))
            if entry.is_symlink() or is_infrastructure_path(child):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:  # Generation/trash moved it during scanning.
                continue
            if entry.is_dir(follow_symlinks=False):
                size = _visible_size(Path(entry.path), child) + remote_below.get(entry.name, 0)
                found.append(FileViewEntry(entry.name, child, True, size, _iso(info.st_mtime)))
            elif stat.S_ISREG(info.st_mode):
                found.append(
                    FileViewEntry(
                        entry.name, child, False, info.st_size, _iso(info.st_mtime), index.reference_of(child)
                    )
                )
    for name, item in remote_here.items():
        if name not in on_disk:
            reference = index.reference_of(item.path)
            found.append(
                FileViewEntry(name, item.path, False, item.size, item.modified_at, reference, remote_only=True)
            )
    return sorted(found, key=lambda item: (not item.is_dir, item.name))


async def list_file_view(
    project_dir: Path, rel: str = "", *, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> list[FileViewEntry]:
    """:func:`list_directory` plus cleaned remote working files; query registered object sizes without object-storage access."""
    remote = await asyncio.to_thread(absent_remote_files, project_dir, rel)
    if remote:
        sizes = await formal_object_sizes({item.stored for item in remote}, session_factory=session_factory)
        remote = [replace(item, size=sizes.get(item.stored, 0)) for item in remote]
    return await asyncio.to_thread(list_directory, project_dir, rel, remote=remote)


def _fetch_needed(project_dir: Path, rel: str, *, deleting: bool) -> bool:
    """``rel`` is a valid remote working file, protected even with local cache; ``deleting`` additionally requires deletable history."""
    try:
        parts = _parts(rel)
        if not parts:
            return False
        _resolve(project_dir, parts[:-1])  # Do not follow parent-directory links either.
    except FileNotFoundError:
        return False
    if (project_dir / rel).is_symlink() or rel not in remote_work_file_digests(project_dir):
        return False
    if not deleting:
        return True
    # Do not download files whose deletion is refused (current snapshots/referenced formal files).
    reference = ReferenceIndex.build(project_dir).reference_of(rel)
    return reference.role is FileRole.HISTORY and not reference.current


@asynccontextmanager
async def local_file(
    project_dir: Path,
    rel: str,
    *,
    deleting: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[None]:
    """One file for byte reads (``deleting`` false) or trash moves (``deleting`` true), with usage registration preventing cleanup deletion.

    Retrieve/verify cleaned remote caches through shared readers. For deletion, fetch only deletable
    history, putting actual content into records so restoration needs no network. Pass other paths
    through unchanged for caller handling.

    Raises:
        MaterialConnectionUnavailableError, MaterialFetchFailedError: retrieval failed."""
    fetch = await asyncio.to_thread(_fetch_needed, project_dir, rel, deleting=deleting)
    async with local_work_files(project_dir, [rel] if fetch else [], session_factory=session_factory):
        yield


def open_project_file(project_dir: Path, rel: str) -> tuple[BinaryIO, os.stat_result]:
    """Open regular project files without following symlinks, returning handle/stat; callers close handles."""
    parts = _parts(rel)
    if not parts:
        raise FileNotFoundError(rel)
    if os.open in os.supports_dir_fd and _O_NOFOLLOW:
        fd = _open_nofollow_posix(project_dir, parts)
    else:
        fd = _open_checked(project_dir, parts)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FileNotFoundError(rel)
        return os.fdopen(fd, "rb"), info
    except BaseException:
        os.close(fd)
        raise


def _open_nofollow_posix(project_dir: Path, parts: tuple[str, ...]) -> int:
    """Open directories/files component-wise with ``O_NOFOLLOW``, eliminating symlink substitution between checking/opening."""
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW
    try:
        directory_fd = os.open(project_dir, flags)
    except OSError as exc:
        raise FileNotFoundError("/".join(parts)) from exc
    try:
        for part in parts[:-1]:
            child = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child
        return os.open(parts[-1], os.O_RDONLY | _O_NONBLOCK | _O_NOFOLLOW, dir_fd=directory_fd)
    except OSError as exc:
        raise FileNotFoundError("/".join(parts)) from exc
    finally:
        os.close(directory_fd)


def _open_checked(project_dir: Path, parts: tuple[str, ...]) -> int:
    """Platforms without ``dir_fd`` / ``O_NOFOLLOW``: preflight each component, then compare opened-file identity."""
    path = _resolve(project_dir, parts)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    opened, current = os.fstat(fd), os.lstat(path)
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        os.close(fd)
        raise FileNotFoundError("/".join(parts))
    return fd


def move_file_to_trash(project_dir: Path, rel: str) -> project_trash.TrashRecord:
    """Move one unreferenced file/history version into its own deletion record.

    Rebuild references under project locks before judging: history deletion uses version management
    (reject current versions), reject all other roles. Episode views handle source files outside whole source."""
    parts = _parts(rel)
    with project_metadata_lock(project_dir):
        path = _resolve(project_dir, parts) if parts else project_dir
        if not path.is_file():
            raise FileNotFoundError(rel)
        reference = ReferenceIndex.build(project_dir).reference_of(rel)
        if reference.role is FileRole.HISTORY:
            return VersionManager(project_dir).delete_history_version(rel)
        if reference.role is not FileRole.UNREFERENCED:
            raise ConflictError("file_view_delete_not_allowed", path=rel)
        if reference.owner is not None and reference.owner.kind == "source":
            raise ConflictError("file_view_delete_source_in_episodes", path=rel)
        return project_trash.record_deletion(project_dir, {"kind": "file", "path": rel}, [rel])


def iter_file_chunks(handle: BinaryIO, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Read chunks and close handles on completion; streaming responses iterate in worker threads."""
    with handle:
        while chunk := handle.read(chunk_size):
            yield chunk
