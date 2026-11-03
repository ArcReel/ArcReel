"""远端内容版本的本地工作文件：按需取回、内容校验、并发协调、使用保护与未使用缓存清理（见 ``docs/adr/0116``）。

远端内容版本在本地有两类工作文件，位置与本地版本一致，既有读取方因此不用区分存储位置：

- 版本快照位置（记录的 ``file``）；
- 资源被选中时的正式文件（如 ``characters/Alice.png``）。

两者都只是缓存：持久内容在对象存储里，清理后读取方经 :func:`ensure_work_file` 或
:func:`ensure_content_version_file` 取回。取回时按版本记录的摘要校验，同一文件的并发取回只下载一次；写入在版本锁内
复核记录仍未改变、文件仍不在，才安装到位，不会盖住并发提交写下的新正式文件。

使用中的工作文件经 :func:`hold_work_file` 登记，清理（:func:`cleanup_work_cache`）跳过它们，也跳过最近使用过的文件。
清理只删除远端已确认保存、且本地内容与记录摘要一致的工作文件，从不删除对象存储里的正式对象。
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

#: 周期清理时工作文件至少闲置多久才删除。
DEFAULT_MIN_IDLE = timedelta(hours=24)


class MaterialFetchFailedError(BadGatewayError):
    """没能从对象存储取回远端内容版本，或取回的内容与记录的摘要不符。"""

    def __init__(self, reason: str) -> None:
        super().__init__("material_remote_fetch_failed", reason=reason)


class MaterialConnectionUnavailableError(ConflictError):
    """内容版本记下的对象存储连接已不存在或已指向另一个桶；不按新连接重新解释旧定位。"""

    def __init__(self) -> None:
        super().__init__("material_object_connection_unavailable")


# ---- 使用保护 ---------------------------------------------------------------------------------

_LEASE_GUARD = threading.Lock()
_LEASES: Counter[str] = Counter()
_LAST_USED: dict[str, float] = {}


def _lease_key(path: Path) -> str:
    """使用登记按真实路径记：登记方经 ``get_project_path`` 拿到的是真实路径，清理方枚举的是项目目录下的原路径，
    数据目录经过符号链接时两种写法不同，按真实路径比对才对得上。"""
    return os.path.realpath(path)


@contextmanager
def hold_work_file(path: Path) -> Generator[Path]:
    """登记 ``path`` 正在使用，期间清理不会删除它。可嵌套，线程安全。"""
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
    """调用方持有使用登记锁。"""
    key = _lease_key(path)
    if _LEASES.get(key):
        return True
    last_used = _LAST_USED.get(key, 0.0)
    return max(stat_result.st_mtime, last_used) > idle_before


# ---- 工作文件与版本记录的对应 -------------------------------------------------------------------


@dataclass(frozen=True)
class _WorkFile:
    path: str
    resource_type: str
    resource_id: str
    content_version_id: str
    digest: str
    stored: ObjectLocator
    #: 正式文件（选中版本的工作副本）；否则是版本快照位置。
    formal: bool


def _load_project_json(project_path: Path) -> dict[str, Any]:
    try:
        data = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_ASSET_SHEET_FIELDS = {spec.bucket_key: spec.sheet_field for spec in ASSET_SPECS.values()}


def _formal_path(project: dict[str, Any], resource_type: str, resource_id: str) -> str | None:
    """资源正式文件的项目内路径：资产以 project.json 的资产图字段为准（可能是 .jpg），其余取规范路径。"""
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
    """版本历史中所有远端内容版本的工作文件。``data`` 须在版本锁内读取。"""
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
    """项目中远端内容版本的全部工作文件（项目内相对路径 → 记录的内容摘要），不论本地是否在场。

    不取版本锁：版本历史与 project.json 都经原子替换写入，各自读到的是完整的一版；两者任一被替换
    （stat 标识变化）就重新解析。读取方以此把缓存缺失与正式素材缺失分开，结论在提交前仍由各自的
    稳定性复核把关。
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
    """``relative_path`` 是远端内容版本的工作文件、而本地缓存不在时，返回记录的内容摘要。

    文件在本地（应照常读取并校验内容）或它不是远端内容版本的工作文件时返回 ``None``。
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
    """图片文件是否在场：本地有普通文件，或它是缓存已清理的远端内容版本工作文件（正式素材仍在对象存储）。

    只回答在场；要读字节的调用方先经 :func:`local_work_files` 取回。
    """
    return path.is_file() or _absent_digest_at(path) is not None


def material_file_digest(path: Path) -> str:
    """图片文件的内容摘要：文件在本地时按字节计算（照常校验）；它是缓存已清理的远端内容版本工作文件时
    取版本记录里的摘要，不因缓存缺失而视为正式素材缺失。

    Raises:
        FileNotFoundError: 文件不在本地，且不是远端内容版本的工作文件。
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


#: 远端内容版本的一个工作文件（版本快照位置或选中时的正式文件），供需要成批确保工作文件在场的读取方使用。
RemoteWorkFile = _WorkFile


def remote_work_files(versions: object, project: dict[str, Any]) -> list[RemoteWorkFile]:
    """``versions``（versions.json 内容）与 ``project``（project.json 内容）里所有远端内容版本的工作文件。

    纯函数：不读盘、不取锁。读取正在使用的项目时用 :func:`locked_remote_work_files`。
    """
    return _work_files(versions, project)


def locked_remote_work_files(project_path: Path) -> list[RemoteWorkFile]:
    """在版本锁内读取项目所有远端内容版本的工作文件。同步磁盘操作，异步调用方在工作线程里调用。"""
    return _locked_work_files(Path(project_path))


def _same_work_file(project_path: Path, work_file: _WorkFile, data: object) -> bool:
    """``data`` 是版本锁内读取的版本历史：工作文件仍对应同一个内容版本（正式文件仍是它被选中时的位置）。"""
    return work_file in _work_files(data, _load_project_json(project_path))


# ---- 取回 -------------------------------------------------------------------------------------

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
    """按记下的连接与桶取回正式对象的内容，不校验摘要；供项目之外的持有方（全局资产库）安装自己的工作文件。

    Raises:
        MaterialConnectionUnavailableError: 记录的连接已不存在或已换桶。
        MaterialFetchFailedError: 下载失败。
    """
    return await _download(stored, session_factory or _default_session_factory())


def in_use_or_recent(path: Path, stat_result: os.stat_result, idle_before: float) -> bool:
    """工作文件此刻有使用登记，或在 ``idle_before`` 之后写入或用过；清理须跳过它。"""
    with _LEASE_GUARD:
        return _in_use_or_recent(path, stat_result, idle_before)


def evict_idle_work_file(path: Path, stat_result: os.stat_result, idle_before: float) -> tuple[int, int, int]:
    """调用方确认远端已存储且摘要一致后，原子复核使用登记并删除本地缓存。"""
    with _LEASE_GUARD:
        if _in_use_or_recent(path, stat_result, idle_before):
            return 0, 0, 1
        path.unlink()  # hard-delete: derived -- 远端已确认保存的图片工作文件，可从对象存储取回
    return 1, stat_result.st_size, 0


def _install(project_path: Path, work_file: _WorkFile, data: bytes) -> None:
    """校验内容后在版本锁内安装工作文件；记录已变或文件已在时放弃，不覆盖。"""
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
            tmp.unlink(missing_ok=True)  # hard-delete: temp -- 取回工作文件的临时文件


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
    """``relative_path`` 是远端内容版本的工作文件且不在本地时，从对象存储取回。

    返回是否做了取回（文件已在、或不是远端内容版本的工作文件时返回 ``False``，调用方照原有逻辑处理）。

    Raises:
        MaterialConnectionUnavailableError: 记录的连接已不存在或已换桶。
        MaterialFetchFailedError: 下载失败或内容与摘要不符。
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
    """返回内容版本校验过的本地文件（版本快照位置）；远端内容版本的工作文件不在时先取回。

    Raises:
        MaterialConnectionUnavailableError, MaterialFetchFailedError: 远端取回失败。
        FileNotFoundError, VersionSnapshotCorruptedError: 同 :meth:`LocalMaterialStorage.open`。
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
    """成批确保工作文件在场，并在这段上下文里登记使用，清理不会删除它们。

    先登记再取回，取回与读取之间不会被清理插队。``relative_paths`` 里不是远端内容版本工作文件的路径照常登记、
    不取回。任何一个取回失败都抛出，不进入上下文。

    Raises:
        MaterialConnectionUnavailableError, MaterialFetchFailedError: 同 :func:`ensure_work_file`。
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
            # 第一个失败原样抛出（调用方按它的状态码返回），其余取回随之取消；已安装的工作文件只是缓存。
            for fetch in fetches:
                fetch.cancel()
            await asyncio.gather(*fetches, return_exceptions=True)
            raise
        yield


def _project_relative_paths(project_path: Path, paths: Iterable[Path | str]) -> tuple[Path, list[str]]:
    """项目目录的真实路径，以及目录下的路径按真实路径换成的项目内相对路径（去重保序，目录外的不在其列）。"""
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
    """要实际读取字节的一批项目文件：期间登记使用，缓存已清理的远端内容版本工作文件先取回并校验摘要。

    ``paths`` 是项目内相对路径或项目目录下的绝对路径；不是远端内容版本工作文件的路径原样放行，
    由调用方照原有逻辑读取。先登记使用再取回，取回后到读取前不会被清理。

    Raises:
        MaterialConnectionUnavailableError, MaterialFetchFailedError: 远端取回失败。
    """
    project_path = Path(project_path)
    _real_root, relative = await asyncio.to_thread(_project_relative_paths, project_path, paths)
    async with held_work_files(project_path, relative, session_factory=session_factory):
        yield


# ---- 清理 -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheCleanupResult:
    removed: int
    freed_bytes: int
    kept_in_use: int


def _evict(project_path: Path, work_file: _WorkFile, idle_before: float) -> tuple[int, int, int]:
    """删除一个可清理的工作文件，返回 (删除数, 释放字节, 因使用中保留数)。"""
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
    """删除各项目中闲置超过 ``min_idle`` 的远端内容版本工作文件。

    只删除远端已确认保存（``stored``）、本地内容与摘要一致、此刻没有使用登记的文件；本地内容版本、
    正式对象与中转副本都不受影响。
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
