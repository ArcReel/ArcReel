"""v19→v20 迁移：项目模型字段里只写了内置供应商、没写模型的旧值，固化为 ``provider/model``。

升级前的运行时把这类裸供应商（含 ``"openai/"`` 这样缺模型的脏值）按模型目录里该供应商当时的默认模型补全，
目录一同步，生成用的模型就可能变。现在目录里的默认模型只作推荐，运行时不再补全（docs/adr/0099），本步按
此刻生效的推荐写下模型，升级前后生成用的模型不变，之后不再随目录变化。

与其他改写模型引用的迁移步（v16→v17、v17→v18 用冻结的映射）不同，本步读当前目录：要固化的正是升级这一刻
实例上生效的值。启动时目录的三层数据先于项目迁移装入（``server.app``），归档导入与回收站恢复时目录已就绪。
本步不查用户的停用状态（迁移没有配置库会话）：推荐模型被用户停用的，升级前报错，升级后仍会在调用时报错。

该供应商在这一媒体类型下没有推荐模型时原样保留，运行时与升级前一样报「不在模型清单中」，由用户重新选择。
自定义供应商的裸 id 原样保留：升级前它不补模型，解析直接落到下一层。只改 ``project.json``，备份由 runner 负责。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.catalog import query as catalog
from lib.config.model_setting_keys import MODEL_SETTING_MEDIA_TYPES, PROJECT_MODEL_SETTING_KEYS
from lib.config.registry import PROVIDER_REGISTRY
from lib.infra.json_io import atomic_write_json
from lib.project.project_schema import parse_project_schema_version

TARGET_SCHEMA_VERSION = 20

Recommend = Callable[[str, str], str | None]


def _bare_builtin_provider(value: object) -> str | None:
    """裸内置供应商（``"ark"``、``"ark/"``、两端带空白）→ 供应商 id；完整的 ``provider/model`` 与其他值为 None。"""
    if not isinstance(value, str):
        return None
    provider, sep, model = value.partition("/")
    if sep and provider.strip() and model.strip():
        return None
    provider = provider.strip()
    return provider if provider in PROVIDER_REGISTRY else None


def migrate_project_dict(project: Mapping[str, Any], recommend: Recommend = catalog.default_model) -> dict[str, Any]:
    """纯函数：把裸内置供应商的模型字段改写为 ``provider/推荐模型``。幂等。

    ``recommend(provider_id, media_type)`` 给出推荐模型，默认取当前目录。不改 schema_version（由文件级 migrate 提交时写入）。
    """
    migrated = dict(project)
    for key in PROJECT_MODEL_SETTING_KEYS:
        provider = _bare_builtin_provider(migrated.get(key))
        if provider is None:
            continue
        model = recommend(provider, MODEL_SETTING_MEDIA_TYPES[key])
        if model is not None:
            migrated[key] = f"{provider}/{model}"
    return migrated


def migrate_v19_to_v20(project_dir: Path) -> None:
    """v19→v20 文件级迁移。单次原子写，崩溃可重试（要么旧值要么新值，无半态）。"""

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


__all__ = ["TARGET_SCHEMA_VERSION", "migrate_project_dict", "migrate_v19_to_v20"]
