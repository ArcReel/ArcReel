"""文件视图的只读访问：按目录列出项目文件并标角色，按路径打开单个文件。

路径来自请求，一律按项目内相对 POSIX 路径解析，不跟随符号链接：符号链接条目不列出，
路径上任一段是符号链接时与文件不存在同样处理。基础设施文件（见 :func:`is_infrastructure_path`）
既不列出也不能打开。

远端内容版本的工作文件只是缓存（见 ``docs/adr/0116``、``docs/adr/0117``）：缓存已清理时，列出照样按版本记录给出它
（标 ``remote_only``，大小取对象登记的字节数），不触发下载；要读字节或删除时经 :func:`local_file` 先从对象存储取回。
"""

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
    #: 远端内容版本的工作文件，本地缓存已清理：内容在对象存储里，读取时取回。
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
    """缓存已清理的远端内容版本工作文件：版本记录里有它，本地盘上没有。"""

    path: str
    stored: ObjectLocator
    #: 版本记录的创建时间（UTC ISO 8601）。
    modified_at: str
    #: 对象登记的字节数；未查询或没有登记时为 0。
    size: int = 0


def _parts(rel: str) -> tuple[str, ...]:
    """校验请求里的相对路径；空串表示项目根。非法路径按不存在处理。"""
    if not rel:
        return ()
    if "\\" in rel or "\x00" in rel or rel.startswith("/"):
        raise FileNotFoundError(rel)
    parts = tuple(rel.split("/"))
    if any(part in {"", ".", ".."} for part in parts) or is_infrastructure_path(rel):
        raise FileNotFoundError(rel)
    return parts


def _resolve(project_dir: Path, parts: tuple[str, ...]) -> Path:
    """逐段拒绝符号链接后，再经路径安全工具确认落在项目内。"""
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
    """目录下可见文件的总字节数；不进符号链接，不计基础设施文件。扫描途中被移走的条目不计。"""
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
    """``rel`` 目录之下（任意深度）缓存已清理的远端内容版本工作文件，大小未查询（为 0）。

    只读版本历史与 ``project.json``，不取锁、不访问对象存储：两个文件都经原子替换写入，读到的是完整的一版，
    列出期间被并发取回的文件由 :func:`list_directory` 按盘上条目去重。
    """
    parts = _parts(rel)
    prefix = "".join(f"{part}/" for part in parts)

    def _wanted(path: str) -> bool:
        if not path.startswith(prefix):
            return False
        try:
            _parts(path)
        except FileNotFoundError:  # 版本记录里的脏路径、基础设施路径
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
    """列出目录的直接子项：目录在前，各自按名称排序。文件带角色，目录带可见内容的总大小。

    ``remote`` 是缓存已清理的远端内容版本工作文件（:func:`absent_remote_files` 加上大小）：直接位于本目录的
    列为 ``remote_only`` 文件，更深的计入所在子目录的大小。盘上已有同名条目时以盘上为准。
    """
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
            except FileNotFoundError:  # 生成任务或回收站操作在扫描途中移走了它
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
    """:func:`list_directory` 加上缓存已清理的远端内容版本工作文件；大小查对象登记，不访问对象存储。"""
    remote = await asyncio.to_thread(absent_remote_files, project_dir, rel)
    if remote:
        sizes = await formal_object_sizes({item.stored for item in remote}, session_factory=session_factory)
        remote = [replace(item, size=sizes.get(item.stored, 0)) for item in remote]
    return await asyncio.to_thread(list_directory, project_dir, rel, remote=remote)


def _fetch_needed(project_dir: Path, rel: str, *, deleting: bool) -> bool:
    """``rel`` 是合法路径、指向缓存已清理的远端内容版本工作文件；``deleting`` 时还须是可删除的历史版本。"""
    try:
        parts = _parts(rel)
        if not parts:
            return False
        _resolve(project_dir, parts[:-1])  # 父目录同样不跟随符号链接
    except FileNotFoundError:
        return False
    if os.path.lexists(project_dir / rel) or rel not in remote_work_file_digests(project_dir):
        return False
    if not deleting:
        return True
    # 删除会被拒绝的文件（当前版本快照、被引用的正式文件）不为此下载
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
    """要读字节（``deleting`` 为假）或移进回收站（``deleting`` 为真）的单个文件，期间登记使用、清理不会删除它。

    它是缓存已清理的远端内容版本工作文件时，先经共用读取入口从对象存储取回并校验摘要；删除时只为可删除的历史
    版本取回，删除记录因此带着内容本身，恢复不依赖网络。其余路径原样放行，由调用方照原有逻辑处理。

    Raises:
        MaterialConnectionUnavailableError, MaterialFetchFailedError: 取回失败。
    """
    fetch = await asyncio.to_thread(_fetch_needed, project_dir, rel, deleting=deleting)
    async with local_work_files(project_dir, [rel] if fetch else [], session_factory=session_factory):
        yield


def open_project_file(project_dir: Path, rel: str) -> tuple[BinaryIO, os.stat_result]:
    """以不跟随符号链接的方式打开项目内的普通文件，返回句柄与它的 stat。调用方负责关闭句柄。"""
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
    """逐级用 ``O_NOFOLLOW`` 打开目录与文件，校验与打开之间不留被换成符号链接的窗口。"""
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
    """没有 ``dir_fd`` / ``O_NOFOLLOW`` 的平台：先逐段预检，打开后再比对文件身份。"""
    path = _resolve(project_dir, parts)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    opened, current = os.fstat(fd), os.lstat(path)
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        os.close(fd)
        raise FileNotFoundError("/".join(parts))
    return fd


def move_file_to_trash(project_dir: Path, rel: str) -> project_trash.TrashRecord:
    """把一个未引用文件或历史版本移进项目回收站，成为一条只含它的删除记录。

    在项目锁内重建引用索引再判定：历史版本经版本管理删除（当前版本拒绝），其余角色一律拒绝；未纳入整本源文的源文件交给分集视图处理。
    """
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
    """逐块读出并在结束时关闭句柄；供流式响应在工作线程里迭代。"""
    with handle:
        while chunk := handle.read(chunk_size):
            yield chunk
