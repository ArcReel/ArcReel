"""v16→v17 迁移：项目文本 backend 里已退役的模型 ID 改为现行 registry 键。

``project.json`` 的默认文本 backend 与两个档位字段若精确引用已退役的模型（映射是本模块内冻结的
副本），改写为现行 ID；其他 preview 模型、不匹配的值与非字符串脏值原样保留。

只改 ``project.json``，不触碰剧本、草稿与产物清单：产物依据不含文本 backend，改写不改变任何
已登记产物的时效。备份由 runner 负责。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.config.model_setting_keys import TEXT_BACKEND_SETTING_KEYS
from lib.infra.json_io import atomic_write_json
from lib.project.project_schema import parse_project_schema_version

TARGET_SCHEMA_VERSION = 17

#: 冻结的退役模型映射：移除设置启动改写时从共享映射逐字复制，此后不再增删，历史项目的升级结果因此可以复现。
#: 运行期不再按写死的映射改写引用，之后的退役只通过模型目录条目的生命周期表达（docs/adr/0106）。
_RETIRED_MODEL_REFERENCES: dict[str, str] = {
    # 文本
    "gemini-aistudio/gemini-3.1-flash-lite-preview": "gemini-aistudio/gemini-3.1-flash-lite",
    "gemini-vertex/gemini-3.1-flash-lite-preview": "gemini-vertex/gemini-3.1-flash-lite",
    # 方舟 Seed 2.0 Lite/Mini 的 260215 版本与 xAI Grok 4.1 Fast 停服，改用同档在役型号
    "ark/doubao-seed-2-0-lite-260215": "ark/doubao-seed-2-1-lite-260915",
    "ark/doubao-seed-2-0-mini-260215": "ark/doubao-seed-2-0-mini-260428",
    "grok/grok-4-1-fast-reasoning": "grok/grok-4.3",
    "grok/grok-4-1-fast-non-reasoning": "grok/grok-4.3",
    # 图片：Gemini 图片模型正式发布，AI Studio 与 Vertex 都改用不带 -preview 的名字
    "gemini-aistudio/gemini-3-pro-image-preview": "gemini-aistudio/gemini-3-pro-image",
    "gemini-aistudio/gemini-3.1-flash-image-preview": "gemini-aistudio/gemini-3.1-flash-image",
    "gemini-vertex/gemini-3-pro-image-preview": "gemini-vertex/gemini-3-pro-image",
    "gemini-vertex/gemini-3.1-flash-image-preview": "gemini-vertex/gemini-3.1-flash-image",
}


def migrate_project_dict(project: Mapping[str, Any]) -> dict[str, Any]:
    """纯函数：把项目文本 backend 中精确的退役模型引用迁到现行 registry 键。幂等。

    不改 schema_version（由文件级 migrate 提交时写入）。
    """

    migrated = dict(project)
    for key in TEXT_BACKEND_SETTING_KEYS:
        value = migrated.get(key)
        if isinstance(value, str):
            migrated[key] = _RETIRED_MODEL_REFERENCES.get(value, value)
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
