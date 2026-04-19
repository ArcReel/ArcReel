"""项目级资产类型规格（character / scene / prop）的单一事实源。

升级自原 BUCKET_KEY / SHEET_KEY 常量字典：用 AssetSpec dataclass 描述每类资产
完整属性（bucket / sheet 字段 / 子目录 / 中文标签 / 额外字符串字段），供 ProjectManager
统一资产 API 与 server/routers/_asset_router_factory 共享。

旧常量 ASSET_TYPES / BUCKET_KEY / SHEET_KEY 保留为 ASSET_SPECS 的派生，现有 18 处
引用零修改。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetSpec:
    """单一资产类型的所有结构性属性。"""

    asset_type: str
    bucket_key: str
    sheet_field: str
    subdir: str
    label_zh: str
    extra_string_fields: tuple[str, ...] = ()


ASSET_SPECS: dict[str, AssetSpec] = {
    "character": AssetSpec(
        asset_type="character",
        bucket_key="characters",
        sheet_field="character_sheet",
        subdir="characters",
        label_zh="角色",
        extra_string_fields=("voice_style", "reference_image"),
    ),
    "scene": AssetSpec(
        asset_type="scene",
        bucket_key="scenes",
        sheet_field="scene_sheet",
        subdir="scenes",
        label_zh="场景",
        extra_string_fields=(),
    ),
    "prop": AssetSpec(
        asset_type="prop",
        bucket_key="props",
        sheet_field="prop_sheet",
        subdir="props",
        label_zh="道具",
        extra_string_fields=(),
    ),
}


ASSET_TYPES: frozenset[str] = frozenset(ASSET_SPECS.keys())

BUCKET_KEY: dict[str, str] = {t: s.bucket_key for t, s in ASSET_SPECS.items()}

SHEET_KEY: dict[str, str] = {t: s.sheet_field for t, s in ASSET_SPECS.items()}
