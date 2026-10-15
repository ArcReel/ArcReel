"""v17→v18 迁移：剪辑时间线修订删去 Agent 轮次字段；补录原先因生成缺口被跳过的产物。

消息排队后一轮里可能先后进入多条用户消息，修订记录的 ``agent_turn`` 不再有清晰含义，也没有
任何地方展示它；修订模型不接受多余字段，已落盘修订里的这个字段在此删去，其余内容原样保留。
剪辑时间线不进产物清单，删字段不改变任何登记。改写前逐个文件备份；不经过剪辑时间线存储的写入
出口，不推进项目的最近活动时间。``project.json`` 的备份由 runner 负责。

v18 起生成缺口只拦生成、依据照常投影（``docs/adr/0098``）。同一步按改前改后两次规划补录此前
因缺口没登记的产物，已登记条目不动，见 :mod:`lib.project.project_migrations.input_gap_backfill`。

同一步把 ``project.json`` 各模型字段里已改名的模型改写为后继模型（映射见
``lib.config.retired_model_ids``）；已下架而没有后继的引用原样保留，运行时报错由用户重选。产物依据
不含模型字段，改写不改变任何已登记产物的时效。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.config.retired_model_ids import PROJECT_MODEL_SETTING_KEYS, migrate_retired_model_reference
from lib.infra.json_io import atomic_write_json
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_migrations.input_gap_backfill import backfill_input_gap_artifacts
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


def migrate_project_model_references(project: Mapping[str, Any]) -> dict[str, Any]:
    """纯函数：把 ``project.json`` 模型字段里精确的退役模型引用改写为后继模型。幂等；非字符串脏值原样保留。

    ``model_settings`` 以 ``provider/model`` 为键（如图片模型的分辨率），退役模型的那一项随之换到后继模型的键下，
    否则改名后按后继模型查不到、静默回到缺省分辨率。后继模型的键已有设置时以它为准，旧键原样留下。
    """

    migrated = dict(project)
    for key in PROJECT_MODEL_SETTING_KEYS:
        value = migrated.get(key)
        if isinstance(value, str):
            migrated[key] = migrate_retired_model_reference(value)
    model_settings = migrated.get("model_settings")
    if isinstance(model_settings, dict):
        renamed: dict[str, Any] = {}
        for key, entry in model_settings.items():
            successor = migrate_retired_model_reference(key) if isinstance(key, str) else key
            if successor != key and successor not in model_settings:
                renamed[successor] = entry
            else:
                renamed[key] = entry
        migrated["model_settings"] = renamed
    return migrated


def migrate_v17_to_v18(project_dir: Path) -> ArtifactBackfillOutcome | None:
    """v17→v18 文件级迁移。逐个文件原子写，最后提升 schema；中途崩溃可重试。"""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return None
    with project_metadata_lock(project_dir):
        project = json.loads(project_file.read_bytes())
        if not isinstance(project, dict):
            raise ValueError("project.json 必须是对象")
        if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
            return None
        outcome = backfill_input_gap_artifacts(project_dir, project)
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
        atomic_write_json(
            project_file, {**migrate_project_model_references(project), "schema_version": TARGET_SCHEMA_VERSION}
        )
    return outcome


__all__ = [
    "TARGET_SCHEMA_VERSION",
    "migrate_project_model_references",
    "migrate_timeline_dict",
    "migrate_v17_to_v18",
    "timeline_backup_sources",
    "timeline_files",
]
