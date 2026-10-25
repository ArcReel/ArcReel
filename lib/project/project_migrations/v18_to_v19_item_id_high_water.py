"""v18→v19 迁移：为每集写入条目编号用过的最大号（``docs/adr/0090``：编号只分配、不复用）。

升级前新增条目只避开剧本里现存的编号，删掉末尾条目后再新增会取回它的编号。本步按五个来源（剧本、版本历史、
产物清单、项目回收站里的删除记录、剪辑时间线的片段引用）算出每集用过的最大主序号，写进 ``project.json``
对应账本条目的 ``item_id_high_water``；此后的新增一律从它往后取号。存量编号不变，文件不改名。

只改 ``project.json``。备份由 runner 负责。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.infra.json_io import atomic_write_json
from lib.project.item_id_history import max_used_item_numbers
from lib.project.project_schema import parse_project_schema_version
from lib.script.item_ids import raise_item_id_high_water

TARGET_SCHEMA_VERSION = 19


def migrate_project_dict(project: Mapping[str, Any], used: Mapping[int, int]) -> dict[str, Any]:
    """纯函数：按 ``used``（``{集 ID: 用过的最大主序号}``）抬高各集账本条目的最大号。幂等，只升不降。"""

    migrated = dict(project)
    episodes = migrated.get("episodes")
    if not isinstance(episodes, list):
        return migrated
    entries: list[Any] = []
    for entry in episodes:
        if isinstance(entry, dict) and isinstance(episode := entry.get("episode"), int) and episode in used:
            entry = dict(entry)
            raise_item_id_high_water(entry, used[episode])
        entries.append(entry)
    migrated["episodes"] = entries
    return migrated


def migrate_v18_to_v19(project_dir: Path) -> None:
    """v18→v19 文件级迁移。单次原子写，崩溃可重试。"""

    project_file = Path(project_dir) / "project.json"
    if not project_file.is_file():
        return
    with project_metadata_lock(project_dir):
        project = json.loads(project_file.read_bytes())
        if not isinstance(project, dict):
            raise ValueError("project.json 必须是对象")
        if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
            return
        used = max_used_item_numbers(Path(project_dir))
        atomic_write_json(
            project_file, {**migrate_project_dict(project, used), "schema_version": TARGET_SCHEMA_VERSION}
        )


__all__ = ["TARGET_SCHEMA_VERSION", "migrate_project_dict", "migrate_v18_to_v19"]
