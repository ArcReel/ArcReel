"""v16→v17 迁移：项目文本 backend 里已退役的模型 ID 改为现行 registry 键。

``project.json`` 的默认文本 backend 与两个档位字段若精确引用已退役的模型（映射见
``lib.config.retired_model_ids``），改写为现行 ID；其他 preview 模型、不匹配的值与非字符串
脏值原样保留。

只改 ``project.json``，不触碰剧本、草稿与产物清单：产物依据不含文本 backend，改写不改变任何
已登记产物的时效。备份由 runner 负责。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.config.retired_model_ids import (
    TEXT_BACKEND_SETTING_KEYS,
    migrate_retired_text_model_reference,
)
from lib.infra.json_io import atomic_write_json
from lib.project.project_schema import parse_project_schema_version

TARGET_SCHEMA_VERSION = 17


def migrate_project_dict(project: Mapping[str, Any]) -> dict[str, Any]:
    """纯函数：把项目文本 backend 中精确的退役模型引用迁到现行 registry 键。幂等。

    不改 schema_version（由文件级 migrate 提交时写入）。
    """

    migrated = dict(project)
    for key in TEXT_BACKEND_SETTING_KEYS:
        value = migrated.get(key)
        if isinstance(value, str):
            migrated[key] = migrate_retired_text_model_reference(value)
    return migrated


def migrate_v16_to_v17(project_dir: Path) -> None:
    """v16→v17 文件级迁移。单次原子写，崩溃可重试（要么旧值要么新值，无半态）。"""

    project_file = Path(project_dir) / "project.json"
    if not project_file.is_file():
        return
    with project_metadata_lock(project_dir):
        project = json.loads(project_file.read_bytes())
        if not isinstance(project, dict):
            raise ValueError("project.json 必须是对象")
        if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
            return
        atomic_write_json(project_file, {**migrate_project_dict(project), "schema_version": TARGET_SCHEMA_VERSION})


__all__ = ["TARGET_SCHEMA_VERSION", "migrate_project_dict", "migrate_v16_to_v17"]
