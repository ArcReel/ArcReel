"""v19→v20 迁移：项目模型字段里只写了内置供应商、没写模型的旧值，固化为 ``provider/model``。

升级前的运行时把这类裸供应商（含 ``"openai/"`` 这样缺模型的脏值）按模型目录里该供应商的默认模型补全，
默认模型已下线、被用户停用或被最低应用版本挡住时报错。现在目录里的默认模型只作推荐，运行时不再补全
（docs/adr/0099），本步按升级前的版本会补全的模型写下来，升级前后生成用的模型不变，之后不再随目录变化。

补全用哪个模型由调用方给出（:data:`BareProviderModels`），迁移本身不读目录也不查库：

- 启动期迁移、回收站与已删除项目的恢复、迁移重试：读升级时记下的升级前推荐
  （``lib.config.pre_upgrade_recommendations``，启动时经 :func:`install_bare_provider_models` 装入）。
- 归档导入：读导入这一刻的推荐（当前目录、当前版本、去掉停用的），由导入入口经参数传入。归档不带它在原实例上
  补全的模型，按目标实例此刻的推荐补全。

给不出模型的（升级前就会报错的：没有推荐、推荐已下线或被停用；以及还原不了升级前推荐的）原样保留，运行时报
「没有选定模型」，由用户重新选择，不会变成可以执行的模型。自定义供应商的裸 id 原样保留：升级前它不补模型，
解析直接落到下一层。还没有装入补全来源时本步报错，项目留在 v19，下次启动重试。只改 ``project.json``，备份由
runner 负责。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import project_metadata_lock
from lib.config.model_setting_keys import MODEL_SETTING_MEDIA_TYPES, PROJECT_MODEL_SETTING_KEYS
from lib.config.registry import PROVIDER_REGISTRY
from lib.infra.json_io import atomic_write_json
from lib.project.project_schema import parse_project_schema_version

TARGET_SCHEMA_VERSION = 20

#: ``(provider_id, media_type)`` → 补全用的模型键；给不出时为 ``None``，该字段原样保留。
BareProviderModels = Callable[[str, str], str | None]

# 启动时装入的补全来源（升级前的推荐）。单元素列表当信箱。
_installed: list[BareProviderModels | None] = [None]


def install_bare_provider_models(models: BareProviderModels | None) -> None:
    """装入没有显式传入补全来源时使用的那一份（启动时装入升级前的推荐）。"""
    _installed[0] = models


def installed_bare_provider_models() -> BareProviderModels | None:
    """启动时装入的补全来源；还没有装入时为 ``None``。"""
    return _installed[0]


def _bare_builtin_provider(value: object) -> str | None:
    """裸内置供应商（``"ark"``、``"ark/"``、两端带空白）→ 供应商 id；完整的 ``provider/model`` 与其他值为 None。"""
    if not isinstance(value, str):
        return None
    provider, sep, model = value.partition("/")
    if sep and provider.strip() and model.strip():
        return None
    provider = provider.strip()
    return provider if provider in PROVIDER_REGISTRY else None


def migrate_project_dict(project: Mapping[str, Any], models: BareProviderModels) -> dict[str, Any]:
    """纯函数：把裸内置供应商的模型字段改写为 ``provider/model``，模型由 ``models`` 给出。幂等。

    不改 schema_version（由文件级 migrate 提交时写入）。
    """
    migrated = dict(project)
    for key in PROJECT_MODEL_SETTING_KEYS:
        provider = _bare_builtin_provider(migrated.get(key))
        if provider is None:
            continue
        model = models(provider, MODEL_SETTING_MEDIA_TYPES[key])
        if model is not None:
            migrated[key] = f"{provider}/{model}"
    return migrated


def migrate_v19_to_v20(project_dir: Path, *, bare_provider_models: BareProviderModels | None = None) -> None:
    """v19→v20 文件级迁移。单次原子写，崩溃可重试（要么旧值要么新值，无半态）。"""

    project_file = Path(project_dir) / "project.json"
    if not project_file.is_file():
        return
    models = bare_provider_models or _installed[0]
    if models is None:
        raise RuntimeError("还没有装入补全裸供应商的推荐模型，v19→v20 迁移留到下次启动")
    with project_metadata_lock(project_dir):
        project = json.loads(project_file.read_bytes())
        if not isinstance(project, dict):
            raise ValueError("project.json 必须是对象")
        if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
            return
        atomic_write_json(
            project_file, {**migrate_project_dict(project, models), "schema_version": TARGET_SCHEMA_VERSION}
        )


__all__ = [
    "TARGET_SCHEMA_VERSION",
    "BareProviderModels",
    "install_bare_provider_models",
    "installed_bare_provider_models",
    "migrate_project_dict",
    "migrate_v19_to_v20",
]
