"""v11→v12 迁移：给存量角色补一张空衍生表，行为不变。

``derivatives`` 是角色条目内的子表（衍生名 → 相对本体的变化描述 + 资产图路径），只有
``AssetSpec.supports_derivatives`` 开启的类型才有。补空表让读侧不必区分「没有该字段」与
「有该字段但为空」两种形态。

只改 ``project.json`` 一个字段，不触碰剧本、草稿与产物清单：空衍生表不改写任何已落盘产物
的身份，也不引入新的引用。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from lib.asset_types import ASSET_SPECS, DERIVATIVES_FIELD
from lib.json_io import atomic_write_json, load_json

_TARGET_VERSION = 12


def migrate_project_dict(project: dict[str, Any]) -> dict[str, Any]:
    """纯函数：把 v11 形态的 project dict 转为 v12 形态。幂等。

    只补齐没有该字段的条目：已带衍生表的原样保留，桶、条目或衍生表被手编成非 dict 时同样
    原样保留，其结构错误由 DataValidator 另行报告——迁移覆盖脏值会把用户手写的内容抹掉，
    且让该报告永远看不到原始形状。不改 schema_version（由文件级 migrate 提交时写入）。
    """
    data = copy.deepcopy(project)
    for spec in ASSET_SPECS.values():
        if not spec.supports_derivatives:
            continue
        bucket = data.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        for entry in bucket.values():
            if isinstance(entry, dict) and DERIVATIVES_FIELD not in entry:
                entry[DERIVATIVES_FIELD] = {}
    return data


def migrate_v11_to_v12(project_dir: Path) -> None:
    """v11→v12 文件级迁移。单次原子写，崩溃可重试（要么旧值要么新值，无半态）。"""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    if not isinstance(data, dict):
        raise ValueError("project.json 必须是对象")
    # 与 runner 的版本读取同口径做 int 归一化：历史 project.json 可能存字符串版本号
    if int(data.get("schema_version") or 0) >= _TARGET_VERSION:
        return
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = _TARGET_VERSION
    atomic_write_json(pj, migrated)


__all__ = ["migrate_project_dict", "migrate_v11_to_v12"]
