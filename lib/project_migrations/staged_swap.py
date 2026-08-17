"""v5→v6 目录交换的命名约定、崩溃窗口认领与磁盘预检。

v6 迁移在同父目录 staging 树上改写，全部成功后用两次 ``os.replace`` 把项目目录换成 staging 树：
先把原目录改名为 rollback 目录，再把 staging 树改名为项目目录。两次改名之间进程被硬杀时，
项目目录不存在、rollback 目录留在磁盘上——恢复责任归启动期扫描（``reclaim_interrupted_swaps``），
迁移器进程内的 ``finally`` 只覆盖它还活着的场景。
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_ROLLBACK_INFIX = ".v6-rollback-"
_STAGING_INFIX = ".v6-"

# staging 树是原项目目录的整树副本，交换窗口内父目录上同时存在原树与副本。
# 预留 10% 余量（下限 32 MiB）覆盖 copytree 期间的元数据开销与并发写入。
_HEADROOM_RATIO = 10
_MIN_HEADROOM_BYTES = 32 * 1024 * 1024


class MigrationDiskSpaceError(RuntimeError):
    """迁移前磁盘容量预检不通过。原项目目录未被触碰。"""


def new_rollback_dir(project_dir: Path) -> Path:
    """为一次交换生成唯一的 rollback 目录路径（不创建）。"""
    return project_dir.parent / f".{project_dir.name}{_ROLLBACK_INFIX}{uuid.uuid4().hex}"


def staging_dir_prefix(project_name: str) -> str:
    """``tempfile.mkdtemp`` 用的 staging 目录前缀。"""
    return f".{project_name}{_STAGING_INFIX}"


def _project_name_from_swap_dir(dir_name: str, infix: str) -> str | None:
    """从交换中间目录名反解项目名；不匹配命名约定或名字不安全时返回 None。"""
    if not dir_name.startswith("."):
        return None
    head, separator, suffix = dir_name.rpartition(infix)
    if not separator or not suffix or not all(char in "0123456789abcdef" for char in suffix):
        return None
    name = head[1:]
    if not name or name in {".", ".."} or "/" in name or "\\" in name or os.sep in name:
        return None
    return name


def rollback_project_name(dir_name: str) -> str | None:
    """rollback 目录名 → 项目名；非 rollback 目录返回 None。"""
    return _project_name_from_swap_dir(dir_name, _ROLLBACK_INFIX)


def staging_project_name(dir_name: str) -> str | None:
    """staging 目录名 → 项目名；rollback 目录与非 staging 目录返回 None。"""
    if rollback_project_name(dir_name) is not None:
        return None
    return _project_name_from_swap_dir(dir_name, _STAGING_INFIX)


def _swap_dirs(projects_root: Path) -> list[Path]:
    try:
        children = sorted(projects_root.iterdir())
    except OSError:
        return []
    return [child for child in children if child.is_dir() and not child.is_symlink()]


def reclaim_interrupted_swaps(projects_root: Path) -> list[str]:
    """认领交换窗口内崩溃遗留的 rollback 目录，返回被改回的项目名。

    只在项目目录缺失时改回：项目目录存在说明交换已完成（或已被上一轮认领），此时 rollback
    目录是等待清理的备份，不是恢复源。"""
    if not projects_root.is_dir():
        return []
    candidates: list[tuple[float, Path, str]] = []
    for child in _swap_dirs(projects_root):
        name = rollback_project_name(child.name)
        if name is None:
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, child, name))

    reclaimed: list[str] = []
    # 同一项目有多个 rollback 目录时取最新的——它才是最后一次交换的原树。
    for _mtime, rollback, name in sorted(candidates, key=lambda item: item[0], reverse=True):
        project_dir = projects_root / name
        if project_dir.exists():
            continue
        try:
            os.replace(rollback, project_dir)
        except OSError:
            logger.exception("迁移交换窗口遗留目录改回失败：%s", rollback)
            continue
        logger.warning("迁移交换窗口内中断，已从 %s 改回项目目录：%s", rollback.name, name)
        reclaimed.append(name)
    return reclaimed


def cleanup_completed_swap_dirs(projects_root: Path, cutoff: float) -> None:
    """删除交换已完成、且 mtime 早于 cutoff 的 rollback / staging 遗留目录。

    项目目录存在即视为交换已完成或已被认领，此时中间目录只占磁盘。年龄闸口既沿用备份清理
    策略，也保证正在运行的迁移的 staging 树不会被误删。"""
    if not projects_root.is_dir():
        return
    for child in _swap_dirs(projects_root):
        name = rollback_project_name(child.name) or staging_project_name(child.name)
        if name is None:
            continue
        if not (projects_root / name).is_dir():
            continue
        try:
            if child.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(child, ignore_errors=True)
        if child.exists():
            logger.warning("无法删除迁移中间目录：%s", child)


def _tree_size(root: Path) -> int:
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                    continue
                total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def ensure_disk_headroom(project_dir: Path) -> None:
    """交换前预检：父目录可用空间须容得下整树副本，不足则失败且不动原目录。"""
    try:
        free = shutil.disk_usage(project_dir.parent).free
    except OSError:
        logger.warning("无法读取 %s 的磁盘可用空间，跳过迁移前容量预检", project_dir.parent, exc_info=True)
        return
    size = _tree_size(project_dir)
    required = size + max(size // _HEADROOM_RATIO, _MIN_HEADROOM_BYTES)
    if free >= required:
        return
    megabyte = 1024 * 1024
    raise MigrationDiskSpaceError(
        f"磁盘空间不足，无法迁移项目 {project_dir.name}："
        f"迁移需在 {project_dir.parent} 复制整个项目目录，"
        f"至少需要 {required // megabyte} MiB，当前可用 {free // megabyte} MiB。"
        "请清理磁盘后重启，项目目录未被改动。"
    )


__all__ = [
    "MigrationDiskSpaceError",
    "cleanup_completed_swap_dirs",
    "ensure_disk_headroom",
    "new_rollback_dir",
    "reclaim_interrupted_swaps",
    "rollback_project_name",
    "staging_dir_prefix",
    "staging_project_name",
]
