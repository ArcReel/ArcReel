"""已删除项目：被整体删除、仍可恢复的项目（``docs/adr/0107``）。

项目删除与覆盖导入把整个项目目录（连同它的项目回收站）移到数据根的 ``deleted_projects/<墓碑名>/``。
目录名就是数据库里改挂记录的墓碑名（``lib.db.repositories.project_records``），恢复时据此把记录改回。
按删除时间保留 30 天；恢复时整体移回项目目录，原名被占用则加后缀，之后走启动时同一条迁移链。

恢复与彻底删除都先把目录整体改名，谁先改名谁生效，另一方得到「不存在」，不会移回删了一半的项目。

这里是已删除项目真正被删掉的地方，``scripts/audit_conventions.py`` 的硬删除检查豁免本模块。
"""

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
    """一个已删除项目。``id`` 是它的墓碑名，``name`` 是删除前的项目名，``size`` 是目录占用的字节数。"""

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
    """把 ``project_dir`` 整体移成已删除项目 ``tombstone``，返回它的新位置；同名的已删除项目已存在时报错。"""
    target = layout.deleted_projects_dir / tombstone
    layout.deleted_projects_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    # 改名失败（如 Windows 上项目里仍有文件被打开）时原样抛出，调用方回滚记录，项目留在原处。
    project_dir.rename(target)
    return target


def move_back(layout: DataRootLayout, project_dir: Path, tombstone: str) -> None:
    """撤销 :func:`move_in`：记录没能提交时把已删除项目 ``tombstone`` 移回 ``project_dir``；没移走过时什么都不做。"""
    moved = layout.deleted_projects_dir / tombstone
    if moved.is_dir() and not (project_dir.exists() or project_dir.is_symlink()):
        moved.rename(project_dir)


def list_entries(layout: DataRootLayout) -> list[DeletedProject]:
    """全部已删除项目，最近删除的在前。

    逐个遍历项目目录统计占用空间，是阻塞的磁盘 I/O：异步调用方放进线程执行。
    """
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
    """把已删除项目整体移回项目目录，返回恢复后的项目目录：原名空闲则沿用，被占用则加 ``-2``、``-3`` 后缀。"""
    source, name = _require(layout, entry_id)
    candidate, suffix = name, 2
    # 大小写不敏感的盘上 exists() 同样认出只差大小写的现有项目。
    while (layout.projects_dir / candidate).exists():
        candidate, suffix = f"{name}-{suffix}", suffix + 1
    target = layout.projects_dir / candidate
    # shortcut: 查重与改名之间有极短窗口，POSIX 的 rename 会顶替同一瞬间新建出的空项目目录；出现同名并发恢复/新建的报告时改用项目锁串行。
    try:
        source.rename(target)
    except FileNotFoundError as exc:
        raise NotFoundError("deleted_project_not_found", id=entry_id) from exc
    return target


def migrate_restored(project_dir: Path, *, recorded_episode_ids: RecordedEpisodeIds | None = None) -> None:
    """对恢复回来的项目走一遍启动时的迁移链：源文编码迁移，再升级到当前 schema。

    与启动时一样，迁移失败不抛出：裁决落在项目旁，生成入口据此拒绝工作，界面提示迁移失败。
    """
    migrate_project_source_encoding_once(project_dir)
    migrate_project_with_verdict(project_dir, recorded_episode_ids=recorded_episode_ids)


def purge(layout: DataRootLayout, entry_id: str) -> None:
    """彻底删除一个已删除项目。"""
    source, _name = _require(layout, entry_id)
    claimed = layout.deleted_projects_dir / f"{_PURGING_PREFIX}{entry_id}"
    try:
        source.rename(claimed)
    except FileNotFoundError as exc:
        raise NotFoundError("deleted_project_not_found", id=entry_id) from exc
    shutil.rmtree(claimed)


def purge_expired(layout: DataRootLayout, *, now: datetime | None = None) -> int:
    """彻底删除超过保留期的已删除项目，返回删除的个数；顺带清掉上次中断的彻底删除留下的目录。"""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    purged = 0
    for entry in list_entries(layout):
        if entry.deleted_at > cutoff:
            continue
        try:
            purge(layout, entry.id)
            purged += 1
        except NotFoundError:
            continue
    for leftover in layout.deleted_projects_dir.glob(f"{_PURGING_PREFIX}*"):
        shutil.rmtree(leftover, ignore_errors=True)
    return purged


def _require(layout: DataRootLayout, entry_id: str) -> tuple[Path, str]:
    """已删除项目的目录与删除前的项目名；``entry_id`` 只有是墓碑名才拼进路径，拼不出数据根里的其他位置。"""
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
