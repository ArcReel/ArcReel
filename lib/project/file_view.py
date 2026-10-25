"""文件视图的只读访问：按目录列出项目文件并标角色，按路径打开单个文件。

路径来自请求，一律按项目内相对 POSIX 路径解析，不跟随符号链接：符号链接条目不列出，
路径上任一段是符号链接时与文件不存在同样处理。基础设施文件（见 :func:`is_infrastructure_path`）
既不列出也不能打开。
"""

from __future__ import annotations

import contextlib
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import VersionManager
from lib.infra.api_errors import ConflictError
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.project import project_trash
from lib.project.reference_index import FileReference, FileRole, ReferenceIndex, is_infrastructure_path

__all__ = ["FileViewEntry", "iter_file_chunks", "list_directory", "move_file_to_trash", "open_project_file"]

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
        return data


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


def list_directory(project_dir: Path, rel: str = "", *, index: ReferenceIndex | None = None) -> list[FileViewEntry]:
    """列出目录的直接子项：目录在前，各自按名称排序。文件带角色，目录带可见内容的总大小。"""
    parts = _parts(rel)
    directory = _resolve(project_dir, parts)
    if not directory.is_dir():
        raise NotADirectoryError(rel)
    index = index or ReferenceIndex.build(project_dir)
    found: list[FileViewEntry] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            child = "/".join((*parts, entry.name))
            if entry.is_symlink() or is_infrastructure_path(child):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:  # 生成任务或回收站操作在扫描途中移走了它
                continue
            if entry.is_dir(follow_symlinks=False):
                size = _visible_size(Path(entry.path), child)
                found.append(FileViewEntry(entry.name, child, True, size, _iso(info.st_mtime)))
            elif stat.S_ISREG(info.st_mode):
                found.append(
                    FileViewEntry(
                        entry.name, child, False, info.st_size, _iso(info.st_mtime), index.reference_of(child)
                    )
                )
    return sorted(found, key=lambda item: (not item.is_dir, item.name))


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
