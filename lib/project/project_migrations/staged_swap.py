"""v5→v6 swap naming, intermediate-directory cleanup, and disk preflight.

v6 transforms sibling staging trees, then swaps via two ``os.replace`` calls: original → rollback,
staging → project. In-process ``finally`` reverses failed swaps. Force-kill between renames is abnormal,
without automatic recovery (ADR 0022). Cleanup only guarantees preserving rollback while projects
are absent, leaving manual restoration possible."""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_ROLLBACK_INFIX = ".v6-rollback-"
_STAGING_INFIX = ".v6-"
# Both directory suffixes use uuid4().hex, letting parsers identify hidden directories belonging to this convention.
_SUFFIX_CHARS = "0123456789abcdef"
_SUFFIX_LENGTH = (
    32  # uuid4().hex has fixed length; character-set-only checks misidentify arbitrary hex-ending user directories.
)

# Staging copies whole projects; originals/copies coexist under the parent during swapping.
# Reserve 10% headroom, minimum 32 MiB, for copytree metadata and concurrent writes.
_HEADROOM_PERCENT = 10
_MIN_HEADROOM_BYTES = 32 * 1024 * 1024


class MigrationDiskSpaceError(OSError):
    """Premigration disk preflight failed without changing project directories.

    Inherit ``OSError`` to share actual ENOSPC handling without import-side failure branches."""


def new_rollback_dir(project_dir: Path) -> Path:
    """Generate a unique rollback path for one swap without creating it."""
    return project_dir.parent / f".{project_dir.name}{_ROLLBACK_INFIX}{uuid.uuid4().hex}"


def new_staging_dir(project_dir: Path) -> Path:
    """Create/return this swap's staging directory.

    Names match rollback hexadecimal suffixes for shared project-name parsing. Exclusive ``mkdir``
    replaces ``tempfile.mkdtemp``: UUIDs already ensure uniqueness; collisions are errors."""
    staging = project_dir.parent / f".{project_dir.name}{_STAGING_INFIX}{uuid.uuid4().hex}"
    staging.mkdir(mode=0o700)
    return staging


def _project_name_from_swap_dir(dir_name: str, infix: str) -> str | None:
    """Decode project names from swap intermediates; None for mismatched conventions or unsafe names."""
    if not dir_name.startswith("."):
        return None
    head, separator, suffix = dir_name.rpartition(infix)
    if not separator or len(suffix) != _SUFFIX_LENGTH or not all(char in _SUFFIX_CHARS for char in suffix):
        return None
    name = head[1:]
    if not name or name in {".", ".."} or "/" in name or "\\" in name or os.sep in name:
        return None
    return name


def rollback_project_name(dir_name: str) -> str | None:
    """Rollback directory → project name; None for nonrollback paths."""
    return _project_name_from_swap_dir(dir_name, _ROLLBACK_INFIX)


def staging_project_name(dir_name: str) -> str | None:
    """Staging directory → project name; None for rollback/nonstaging paths."""
    if rollback_project_name(dir_name) is not None:
        return None
    return _project_name_from_swap_dir(dir_name, _STAGING_INFIX)


def _candidate_dirs(projects_dir: Path) -> list[Path]:
    """Real, nonsymlink projects_dir children eligible for name decoding.

    Valid names start with ``.``. Prefix-filter first, short-circuiting noncandidate ``is_dir``/``is_symlink``
    system calls. Startup always scans the entire root, magnifying two stats across many projects or slow
    filesystems (network mounts, Docker shared folders)."""
    try:
        children = sorted(projects_dir.iterdir())
    except OSError:
        return []
    return [child for child in children if child.name.startswith(".") and child.is_dir() and not child.is_symlink()]


def cleanup_completed_swap_dirs(projects_dir: Path, cutoff: float) -> None:
    """Delete completed-swap rollback/staging remnants with mtime before cutoff.

    Existing project directories mean completed swaps; intermediates only occupy disk. If projects are
    absent, rollback is the only project data and must never be deleted. Age gates share backup policy
    and prevent deleting running migrations' staging trees."""
    if not projects_dir.is_dir():
        return
    for child in _candidate_dirs(projects_dir):
        name = rollback_project_name(child.name) or staging_project_name(child.name)
        if name is None:
            continue
        if not (projects_dir / name).is_dir():
            continue
        try:
            if child.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(
            child, ignore_errors=True
        )  # hard-delete: infra -- Rollback/staging directories after completed migrations.
        if child.exists():
            logger.warning("无法删除迁移中间目录：%s", child)


def tree_size(root: Path) -> int:
    """File bytes in a tree without following symlinks; skip unreadable entries."""
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
    """Premigration check: parent free space must fit a complete tree copy; otherwise fail without writing any bytes."""
    try:
        free = shutil.disk_usage(project_dir.parent).free
    except OSError:
        logger.warning("无法读取 %s 的磁盘可用空间，跳过迁移前容量预检", project_dir.parent, exc_info=True)
        return
    size = tree_size(project_dir)
    required = size + max(size * _HEADROOM_PERCENT // 100, _MIN_HEADROOM_BYTES)
    if free >= required:
        return
    megabyte = 1024 * 1024
    raise MigrationDiskSpaceError(
        f"磁盘空间不足，无法迁移项目 {project_dir.name}："
        f"迁移需在 {project_dir.parent} 复制整个项目目录，"
        f"至少需要 {required // megabyte} MiB，当前可用 {free // megabyte} MiB。"
        "请清理磁盘后重试，项目目录未被改动。"
    )


__all__ = [
    "MigrationDiskSpaceError",
    "cleanup_completed_swap_dirs",
    "ensure_disk_headroom",
    "new_rollback_dir",
    "new_staging_dir",
    "rollback_project_name",
    "staging_project_name",
    "tree_size",
]
