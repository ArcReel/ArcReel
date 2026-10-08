"""v17→v18 迁移：剪辑时间线修订删去 Agent 轮次字段。

消息排队后一轮里可能先后进入多条用户消息，修订记录的 ``agent_turn`` 不再有清晰含义，也没有
任何地方展示它；修订模型不接受多余字段，已落盘修订里的这个字段在此删去，其余内容原样保留。

剪辑时间线不进产物清单，删字段不改变任何登记。改写前逐个文件备份；不经过剪辑时间线存储的写入
出口，不推进项目的最近活动时间。``project.json`` 的备份由 runner 负责。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.json_io import atomic_write_json
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_schema import parse_project_schema_version

TARGET_SCHEMA_VERSION = 18
_FROM_VERSION = TARGET_SCHEMA_VERSION - 1
_REMOVED_FIELD = "agent_turn"


def _is_real_dir(path: Path) -> bool:
    return not path.is_symlink() and not path.is_junction() and path.is_dir()


def _episode_dirs(project_dir: Path) -> Iterator[Path]:
    """``edit_timelines/episode_*`` 目录。符号链接与 junction 不跟随，以免改写项目外的文件。"""

    root = Path(project_dir) / "edit_timelines"
    if not _is_real_dir(root):
        return
    yield from (directory for directory in sorted(root.glob("episode_*")) if _is_real_dir(directory))


def _timeline_names(directory: Path) -> set[str]:
    return {path.name for path in directory.glob("*.json") if not path.is_symlink() and path.is_file()}


def timeline_files(project_dir: Path) -> Iterator[Path]:
    """项目里所有剪辑时间线文件：``edit_timelines/episode_*/*.json``，不含符号链接。"""

    for directory in _episode_dirs(project_dir):
        yield from (directory / name for name in sorted(_timeline_names(directory)))


def timeline_backup_sources(project_dir: Path) -> Iterator[Path]:
    """剪辑时间线备份对应的源路径：现存的时间线文件，加上源文件已删、只剩备份的时间线。"""

    for directory in _episode_dirs(project_dir):
        names = _timeline_names(directory)
        names.update(
            path.name.partition(".bak.v")[0] for path in directory.glob("*.json.bak.v*") if not path.is_symlink()
        )
        yield from (directory / name for name in sorted(names))


def migrate_timeline_dict(document: Mapping[str, Any]) -> dict[str, Any]:
    """纯函数：删去每个修订上的 ``agent_turn``。幂等；结构不认得的值原样保留。"""

    migrated = dict(document)
    revisions = migrated.get("revisions")
    if isinstance(revisions, list):
        migrated["revisions"] = [
            {key: value for key, value in revision.items() if key != _REMOVED_FIELD}
            if isinstance(revision, dict)
            else revision
            for revision in revisions
        ]
    return migrated


def migrate_v17_to_v18(project_dir: Path) -> None:
    """v17→v18 文件级迁移。逐个文件原子写，最后提升 schema；中途崩溃可重试。"""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    with project_metadata_lock(project_dir):
        project = json.loads(project_file.read_bytes())
        if not isinstance(project, dict):
            raise ValueError("project.json 必须是对象")
        if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
            return
        for path in timeline_files(project_dir):
            try:
                document = json.loads(path.read_bytes())
            except ValueError:
                # 本就读不出的文件保持原样，读取时照常按无法解析处理。
                continue
            if not isinstance(document, dict):
                continue
            migrated = migrate_timeline_dict(document)
            if migrated != document:
                ensure_versioned_backup(path, _FROM_VERSION)
                atomic_write_json(path, migrated)
        atomic_write_json(project_file, {**project, "schema_version": TARGET_SCHEMA_VERSION})


__all__ = [
    "TARGET_SCHEMA_VERSION",
    "migrate_timeline_dict",
    "migrate_v17_to_v18",
    "timeline_backup_sources",
    "timeline_files",
]
