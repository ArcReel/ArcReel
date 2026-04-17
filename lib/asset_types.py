"""全局资产类型常量——后端共享来源。

全局资产库当前支持的三类资源：character / scene / prop。
该模块提供类型集合、与 project.json bucket key 的映射，以及 bucket 项内
sheet 字段名的映射，避免在路由与工具函数中重复定义同一份表。
"""

from __future__ import annotations

ASSET_TYPES: frozenset[str] = frozenset({"character", "scene", "prop"})

# 资产类型 → project.json 顶层 bucket key
BUCKET_KEY: dict[str, str] = {
    "character": "characters",
    "scene": "scenes",
    "prop": "props",
}

# 资产类型 → bucket 项内的 sheet 字段名
SHEET_KEY: dict[str, str] = {
    "character": "character_sheet",
    "scene": "scene_sheet",
    "prop": "prop_sheet",
}
