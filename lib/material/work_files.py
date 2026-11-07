"""Local remote-version work files: fetch on demand, verify, coordinate concurrency, protect use, and clean unused caches (``docs/adr/0116``).

Remote versions have two local work-file types, matching local-version locations so existing
readers need not distinguish storage:

- Version snapshot location (recorded ``file``);
- Selected resource’s formal file, e.g. ``characters/Alice.png``.

Both are caches only; durable content lives remotely. After cleanup, readers use
:func:`ensure_work_file` / :func:`ensure_content_version_file` to fetch it. Verify against recorded
digests; concurrent same-file requests download once. Before installing under version locks,
recheck unchanged records and continued file absence, never overwriting concurrent new formal files.

Register active files through :func:`hold_work_file`; :func:`cleanup_work_cache` skips them and
recently used files. Delete only confirmed-remote, digest-matching work files, never formal
objects in object storage.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections import Counter
from collections.abc import AsyncGenerator, Generator, Iterable, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import BadGatewayError, ConflictError
from lib.infra.content_digest import sha256_file
from lib.infra.data_root_layout import list_project_dirs
from lib.material.formal_objects import iter_version_records, stored_object_keys
from lib.material.storage import (
    CONTENT_DIGEST,
    CONTENT_VERSION_ID,
    STORAGE,
    ContentVersion,
    LocalMaterialStorage,
    ObjectLocator,
)
from lib.object_storage.client import ObjectStorageClient, ObjectStorageError
from lib.object_storage.connection import config_from_connection
from lib.project.asset_types import ASSET_SPECS
from lib.project.resource_paths import resource_relative_path

logger = logging.getLogger(__name__)

#: Minimum work-file idle time before periodic deletion.
DEFAULT_MIN_IDLE = timedelta(hours=24)


class MaterialFetchFailedError(BadGatewayError):
    """Remote content could not be fetched or mismatches the recorded digest."""

    def __init__(self, reason: str) -> None:
        super().__init__("material_remote_fetch_failed", reason=reason)


class MaterialConnectionUnavailableError(ConflictError):
    """Recorded connection is absent or points to another bucket; never reinterpret old locators through new connections."""

    def __init__(self) -> None:
        super().__init__("material_object_connection_unavailable")


# ---- Use protection ---------------------------------------------------------------------

_LEASE_GUARD = threading.Lock()
_LEASES: Counter[str] = Counter()
_LAST_USED: dict[str, float] = {}


def _lease_key(path: Path) -> str:
    """Key use registrations by real paths: ``get_project_path`` returns resolved paths, while cleanup enumerates original project-directory paths.
    Symlinked data directories make them differ; realpath comparison aligns them."""
    return os.path.realpath(path)


@contextmanager
def hold_work_file(path: Path) -> Generator[Path]:
    """Register ``path`` as in use so cleanup cannot delete it; nesting is supported, thread-safe."""
    key = _lease_key(path)
    with _LEASE_GUARD:
        _LEASES[key] += 1
    try:
        yield path
    finally:
        with _LEASE_GUARD:
            _LEASES[key] -= 1
            if _LEASES[key] <= 0:
                del _LEASES[key]
            _LAST_USED[key] = time.time()


def _in_use_or_recent(path: Path, stat_result: os.stat_result, idle_before: float) -> bool:
    """Caller holds the use-registration lock."""
    key = _lease_key(path)
    if _LEASES.get(key):
        return True
    last_used = _LAST_USED.get(key, 0.0)
    return max(stat_result.st_mtime, last_used) > idle_before


# ---- Work-file/version-record correspondence ------------------------------------------------


@dataclass(frozen=True)
class _WorkFile:
    path: str
    resource_type: str
    resource_id: str
    content_version_id: str
    digest: str
    stored: ObjectLocator
    #: Formal file, the selected-version work copy; otherwise version snapshot location.
    formal: bool


def _load_project_json(project_path: Path) -> dict[str, Any]:
    try:
        data = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_ASSET_SHEET_FIELDS = {spec.bucket_key: spec.sheet_field for spec in ASSET_SPECS.values()}


def _formal_path(project: dict[str, Any], resource_type: str, resource_id: str) -> str | None:
    """Project-relative formal resource path: assets use project.json sheet fields (possibly .jpg), others canonical paths."""
    sheet_field = _ASSET_SHEET_FIELDS.get(resource_type)
    if sheet_field is not None:
        bucket = project.get(resource_type)
        entry = bucket.get(resource_id) if isinstance(bucket, dict) else None
        value = entry.get(sheet_field) if isinstance(entry, dict) else None
        return value if isinstance(value, str) and value else None
    try:
        return resource_relative_path(resource_type, resource_id)
    except ValueError:
        return None


def _work_files(data: object, project: dict[str, Any]) -> list[_WorkFile]:
    """Work files for every remote historical version; read ``data`` under version locks."""
    result: list[_WorkFile] = []
    current_versions: dict[tuple[str, str], object] = {}
    if isinstance(data, dict):
        for resource_type, bucket in data.items():
            for resource_id, history in bucket.items() if isinstance(bucket, dict) else ():
                if isinstance(history, dict):
                    current_versions[(resource_type, resource_id)] = history.get("current_version")
    for resource_type, resource_id, record in iter_version_records(data):
        stored = ObjectLocator.from_record(record.get(STORAGE))
        content_version_id, digest, file = (
            record.get(CONTENT_VERSION_ID),
            record.get(CONTENT_DIGEST),
            record.get("file"),
        )
        if stored is None or not isinstance(content_version_id, str) or not isinstance(digest, str):
            continue
        if isinstance(file, str):
            result.append(_WorkFile(file, resource_type, resource_id, content_version_id, digest, stored, False))
        if current_versions.get((resource_type, resource_id)) == record.get("version"):
            formal = _formal_path(project, resource_type, resource_id)
            if formal is not None:
                result.append(_WorkFile(formal, resource_type, resource_id, content_version_id, digest, stored, True))
    return result


_INDEX_GUARD = threading.Lock()
_INDEX_CACHE: dict[str, tuple[tuple[object, ...], dict[str, str]]] = {}


def _file_version(path: Path) -> tuple[object, ...] | None:
    try:
        stat_result = path.stat()
    except OSError:
        return None
    return (stat_result.st_dev, stat_result.st_ino, stat_result.st_size, stat_result.st_mtime_ns)


def remote_work_file_digests(project_path: Path) -> Mapping[str, str]:
    """All project remote-version work files, mapping project-relative paths to digests, regardless of local presence.

    No version locks: history/project.json use atomic replacements, so each read gets a complete
    version; reparse when either stat identity changes. Readers distinguish missing caches from
    missing formal materials, while their own precommit stability checks still guard conclusions.
    """
    project_path = Path(project_path)
    versions_file = project_path / "versions" / "versions.json"
    version = (_file_version(versions_file), _file_version(project_path / "project.json"))
    if version[0] is None:
        return {}
    key = os.path.abspath(project_path)
    with _INDEX_GUARD:
        cached = _INDEX_CACHE.get(key)
    if cached is not None and cached[0] == version:
        return cached[1]
    try:
        raw = versions_file.read_bytes()
    except OSError:
        return {}
    index: dict[str, str] = {}
    if b'"storage"' in raw:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        for item in _work_files(data, _load_project_json(project_path)):
            index[item.path] = item.digest
    with _INDEX_GUARD:
        _INDEX_CACHE[key] = (version, index)
    return index


def absent_work_file_digest(project_path: Path, relative_path: str) -> str | None:
    """Return recorded digests when ``relative_path`` is a remote-version work file with absent local cache.

    Return ``None`` if local (read/verify normally) or not a remote-version work file.
    """
    digest = remote_work_file_digests(project_path).get(PurePosixPath(relative_path).as_posix())
    if digest is None or (Path(project_path) / relative_path).exists():
        return None
    return digest


def _owning_project(path: Path) -> Path | None:
    for parent in path.parents:
        if (parent / "project.json").is_file():
            return parent
    return None


def _absent_digest_at(path: Path) -> str | None:
    absolute = Path(os.path.abspath(path))
    project_path = _owning_project(absolute)
    if project_path is None:
        return None
    return absent_work_file_digest(project_path, absolute.relative_to(project_path).as_posix())


def material_file_present(path: Path) -> bool:
    """Image presence: a regular local file, or a cleaned remote-version cache whose formal content remains remotely stored.

    Presence only; callers needing bytes first fetch through :func:`local_work_files`.
    """
    return path.is_file() or _absent_digest_at(path) is not None


def material_file_digest(path: Path) -> str:
    """Image digest: hash local bytes with normal verification, or use recorded digests for cleaned remote-version work files,
    never treating missing caches as missing formal materials.

    Raises:
        FileNotFoundError: Neither local nor a remote-version work file.
    """
    try:
        return sha256_file(path)
    except FileNotFoundError:
        digest = _absent_digest_at(path)
        if digest is None:
            raise
        return digest


def _locked_work_files(project_path: Path) -> list[_WorkFile]:
    from lib.artifacts.version_manager import VersionManager

    project = _load_project_json(project_path)
    with VersionManager(project_path).locked_history() as data:
        return _work_files(data, project)


#: One remote-version work file (snapshot or selected formal file) for readers ensuring files in batches.
RemoteWorkFile = _WorkFile


def remote_work_files(versions: object, project: dict[str, Any]) -> list[RemoteWorkFile]:
    """All remote-version work files in ``versions`` (versions.json) and ``project`` (project.json).

    Pure function, no disk reads/locks. Use :func:`locked_remote_work_files` for active project reads.
    """
    return _work_files(versions, project)


def locked_remote_work_files(project_path: Path) -> list[RemoteWorkFile]:
    """Read all remote-version work files under version locks; async callers run synchronous disk operations in worker threads."""
    return _locked_work_files(Path(project_path))


def _same_work_file(project_path: Path, work_file: _WorkFile, data: object) -> bool:
    """``data`` is version-lock-read history; work files still belong to the same content version (formal files remain at selected-version locations)."""
    return work_file in _work_files(data, _load_project_json(project_path))


# ---- Fetch ------------------------------------------------------------------------------

_FETCH_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def _fetch_lock(path: Path) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), os.path.abspath(path))
    lock = _FETCH_LOCKS.get(key)
    if lock is None:
        lock = _FETCH_LOCKS[key] = asyncio.Lock()
    return lock


def _default_session_factory() -> async_sessionmaker[AsyncSession]:
    from lib.db import async_session_factory

    return async_session_factory


async def _download(stored: ObjectLocator, session_factory: async_sessionmaker[AsyncSession]) -> bytes:
    async with session_factory() as session:
        connection = await ObjectStorageConnectionRepository(session).get()
    if connection is None or connection.id != stored.connection_id or connection.bucket != stored.bucket:
        raise MaterialConnectionUnavailableError()
    try:
        return await ObjectStorageClient(config_from_connection(connection)).get_bytes(stored.key)
    except ObjectStorageError as exc:
        raise MaterialFetchFailedError(exc.kind) from exc


async def fetch_object_content(
    stored: ObjectLocator, *, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> bytes:
    """Fetch formal-object content by recorded connection/bucket without digest verification, for nonproject holders (global libraries) installing work files.

    Raises:
        MaterialConnectionUnavailableError: Recorded connection absent or changed buckets.
        MaterialFetchFailedError: Download failed.
    """
    return await _download(stored, session_factory or _default_session_factory())


def in_use_or_recent(path: Path, stat_result: os.stat_result, idle_before: float) -> bool:
    """Work file is currently registered in use, or written/used after ``idle_before``; cleanup must skip it."""
    with _LEASE_GUARD:
        return _in_use_or_recent(path, stat_result, idle_before)


def evict_idle_work_file(path: Path, stat_result: os.stat_result, idle_before: float) -> tuple[int, int, int]:
    """After caller confirms remote storage/digest matching, atomically recheck use registrations and delete local caches."""
    with _LEASE_GUARD:
        if _in_use_or_recent(path, stat_result, idle_before):
            return 0, 0, 1
        path.unlink()  # hard-delete: derived -- Confirmed-remote image work files, refetchable from object storage
    return 1, stat_result.st_size, 0


def _install(project_path: Path, work_file: _WorkFile, data: bytes) -> None:
    """After content verification, install under version locks; abandon changed records/present files without overwriting."""
    from lib.artifacts.version_manager import VersionManager

    if hashlib.sha256(data).hexdigest() != work_file.digest:
        raise MaterialFetchFailedError("content_mismatch")
    target = project_path / work_file.path
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=f"{target.suffix}.fetch", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        with VersionManager(project_path).locked_history() as history:
            if not target.exists() and _same_work_file(project_path, work_file, history):
                os.replace(tmp, target)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)  # hard-delete: temp -- Temporary work-file download


async def _fetch(project_path: Path, work_file: _WorkFile, session_factory: async_sessionmaker[AsyncSession]) -> None:
    target = project_path / work_file.path
    async with _fetch_lock(target):
        if await asyncio.to_thread(target.is_file):
            return
        data = await _download(work_file.stored, session_factory)
        await asyncio.to_thread(_install, project_path, work_file, data)


async def ensure_work_file(
    project_path: Path, relative_path: str, *, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> bool:
    """Fetch ``relative_path`` from object storage if it is a remote-version work file missing locally.

    Return whether fetched (``False`` if already present or not remote-version work files; callers
    continue existing behavior).

    Raises:
        MaterialConnectionUnavailableError: Recorded connection absent or changed buckets.
        MaterialFetchFailedError: Download failed or digest mismatched.
    """
    project_path = Path(project_path)
    if await asyncio.to_thread((project_path / relative_path).is_file):
        return False
    candidates = await asyncio.to_thread(_locked_work_files, project_path)
    work_file = next((item for item in candidates if item.path == relative_path), None)
    if work_file is None:
        return False
    await _fetch(project_path, work_file, session_factory or _default_session_factory())
    return True


async def ensure_content_version_file(
    project_path: Path,
    content_version: ContentVersion,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> Path:
    """Return verified local content-version snapshot files, fetching missing remote-version work files first.

    Raises:
        MaterialConnectionUnavailableError, MaterialFetchFailedError: Remote fetch failed.
        FileNotFoundError, VersionSnapshotCorruptedError: As :meth:`LocalMaterialStorage.open`.
    """
    project_path = Path(project_path)
    if content_version.stored is not None:
        await ensure_work_file(project_path, content_version.locator.path, session_factory=session_factory)
    return await asyncio.to_thread(LocalMaterialStorage(project_path).open, content_version)


_HELD_FETCH_CONCURRENCY = 4


@asynccontextmanager
async def held_work_files(
    project_path: Path,
    relative_paths: Iterable[str],
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[None]:
    """Ensure work files in batches, registering use for this context so cleanup cannot delete them.

    Register before fetch, preventing cleanup between fetch/read. Register ``relative_paths``
    outside remote-version work files normally without fetching. Any fetch failure raises without
    entering the context.

    Raises:
        MaterialConnectionUnavailableError, MaterialFetchFailedError: As :func:`ensure_work_file`.
    """
    project_path = Path(project_path)
    paths = sorted(set(relative_paths))
    session_factory = session_factory or _default_session_factory()
    gate = asyncio.Semaphore(_HELD_FETCH_CONCURRENCY)

    async def _ensure(relative_path: str) -> None:
        async with gate:
            await ensure_work_file(project_path, relative_path, session_factory=session_factory)

    with contextlib.ExitStack() as stack:
        for relative_path in paths:
            stack.enter_context(hold_work_file(project_path / relative_path))
        fetches = [asyncio.create_task(_ensure(relative_path)) for relative_path in paths]
        try:
            await asyncio.gather(*fetches)
        except BaseException:
            # Propagate the first failure unchanged (callers return its status), cancel others; already installed work files are only caches.
            for fetch in fetches:
                fetch.cancel()
            await asyncio.gather(*fetches, return_exceptions=True)
            raise
        yield


def _project_relative_paths(project_path: Path, paths: Iterable[Path | str]) -> tuple[Path, list[str]]:
    """Resolved project directory and contained paths converted to project-relative realpaths, deduplicated in order; exclude external paths."""
    root = os.path.realpath(project_path)
    relative: list[str] = []
    for path in paths:
        absolute = os.path.realpath(project_path / path)
        if absolute == root or os.path.commonpath([root, absolute]) != root:
            continue
        rel = Path(absolute).relative_to(root).as_posix()
        if rel not in relative:
            relative.append(rel)
    return Path(root), relative


@asynccontextmanager
async def local_work_files(
    project_path: Path,
    paths: Iterable[Path | str],
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[None]:
    """Project files whose bytes will be read: register use and fetch/digest-verify cleaned remote-version caches first.

    ``paths`` accepts project-relative paths or absolute paths under project directories. Nonremote
    work-file paths pass unchanged for existing caller reads. Register use before fetch so cleanup
    cannot intervene before reading.

    Raises:
        MaterialConnectionUnavailableError, MaterialFetchFailedError: Remote fetch failed.
    """
    project_path = Path(project_path)
    _real_root, relative = await asyncio.to_thread(_project_relative_paths, project_path, paths)
    async with held_work_files(project_path, relative, session_factory=session_factory):
        yield


# ---- Cleanup ----------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheCleanupResult:
    removed: int
    freed_bytes: int
    kept_in_use: int


def _evict(project_path: Path, work_file: _WorkFile, idle_before: float) -> tuple[int, int, int]:
    """Delete one cleanable work file; return (deleted count, freed bytes, retained-in-use count)."""
    from lib.artifacts.version_manager import VersionManager

    target = project_path / work_file.path
    with VersionManager(project_path).locked_history() as data:
        try:
            stat_result = target.stat()
        except OSError:
            return 0, 0, 0
        if in_use_or_recent(target, stat_result, idle_before):
            return 0, 0, 1
        if not _same_work_file(project_path, work_file, data) or sha256_file(target) != work_file.digest:
            return 0, 0, 0
        return evict_idle_work_file(target, stat_result, idle_before)


async def cleanup_work_cache(
    projects_dir: Path,
    *,
    min_idle: timedelta = DEFAULT_MIN_IDLE,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> CacheCleanupResult:
    """Delete remote-version work files idle beyond ``min_idle`` across projects.

    Delete only confirmed-remote (``stored``), locally digest-matching files without current use
    registrations; local content versions, formal objects, and relay copies remain unaffected.
    """
    session_factory = session_factory or _default_session_factory()
    idle_before = time.time() - min_idle.total_seconds()
    removed = freed = kept = 0
    for project_path in await asyncio.to_thread(list_project_dirs, Path(projects_dir)):
        work_files = await asyncio.to_thread(_locked_work_files, project_path)
        if not work_files:
            continue
        async with session_factory() as session:
            stored = await stored_object_keys(session, {item.stored for item in work_files})
        for work_file in work_files:
            if work_file.stored not in stored:
                continue
            count, size, in_use = await asyncio.to_thread(_evict, project_path, work_file, idle_before)
            removed += count
            freed += size
            kept += in_use
    if removed:
        logger.info("清理远端素材工作文件 %d 个，释放 %d 字节", removed, freed)
    return CacheCleanupResult(removed=removed, freed_bytes=freed, kept_in_use=kept)


__all__ = [
    "DEFAULT_MIN_IDLE",
    "CacheCleanupResult",
    "MaterialConnectionUnavailableError",
    "MaterialFetchFailedError",
    "RemoteWorkFile",
    "absent_work_file_digest",
    "cleanup_work_cache",
    "ensure_content_version_file",
    "ensure_work_file",
    "evict_idle_work_file",
    "fetch_object_content",
    "held_work_files",
    "hold_work_file",
    "in_use_or_recent",
    "local_work_files",
    "locked_remote_work_files",
    "material_file_digest",
    "material_file_present",
    "remote_work_file_digests",
    "remote_work_files",
]
