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
    """单一资产类型的所有结构性属性。

    ``extra_string_fields`` 是 schema 维度——validator 据此校验「这些字段若存在须为
    string」、`_build_asset_entry` 据此初始化默认空串、REST PATCH 据此扩展可更新字段集；
    ``agent_editable_extra_fields`` 是权限维度——`upsert_assets`（agent 走的入口）的字段
    白名单来自这里，**不复用 schema 维度**。两者解耦的原因：``reference_image`` 是用户
    上传或系统生成的文件路径，是 ``extra_string_fields``（schema 层 string）但不是
    ``agent_editable_extra_fields``（agent 不该覆写用户上传的路径，更新走专用 API
    ``update_character_reference_image``，与 sheet_field 同性质）。
    """

    asset_type: str
    bucket_key: str
    sheet_field: str
    subdir: str
    label_zh: str
    extra_string_fields: tuple[str, ...] = ()
    agent_editable_extra_fields: tuple[str, ...] = ()


ASSET_SPECS: dict[str, AssetSpec] = {
    "character": AssetSpec(
        asset_type="character",
        bucket_key="characters",
        sheet_field="character_sheet",
        subdir="characters",
        label_zh="角色",
        extra_string_fields=("voice_style", "reference_image"),
        # voice_style 是 LLM 生成的角色配音风格，agent 可改；reference_image 是用户上传
        # 的文件路径（系统级），不进 agent 白名单——更新走 update_character_reference_image。
        agent_editable_extra_fields=("voice_style",),
    ),
    "scene": AssetSpec(
        asset_type="scene",
        bucket_key="scenes",
        sheet_field="scene_sheet",
        subdir="scenes",
        label_zh="场景",
        extra_string_fields=(),
        agent_editable_extra_fields=(),
    ),
    "prop": AssetSpec(
        asset_type="prop",
        bucket_key="props",
        sheet_field="prop_sheet",
        subdir="props",
        label_zh="道具",
        extra_string_fields=(),
        agent_editable_extra_fields=(),
    ),
}


ASSET_TYPES: frozenset[str] = frozenset(ASSET_SPECS.keys())

BUCKET_KEY: dict[str, str] = {t: s.bucket_key for t, s in ASSET_SPECS.items()}

SHEET_KEY: dict[str, str] = {t: s.sheet_field for t, s in ASSET_SPECS.items()}

ILLEGAL_ASSET_NAME_CHARS: tuple[str, ...] = ("/", "\\", "\0", ":", "*", "?", '"', "<", ">", "|")

WINDOWS_RESERVED_BASENAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)


def validate_asset_name(name: object) -> str:
    """校验并规范化（strip）资产名，非法时抛 ValueError，合法时返回 strip 后的名字。

    资产名全链路被当作单段路径组件使用：文件名（``characters/{name}.png``、
    ``versions/{type}/{name}_v{n}_{ts}.png``）与 REST 路由的单段路径参数。含路径
    分隔符、控制字符或 ``..`` 的名字会产生嵌套路径与无法匹配的 URL；Windows 还会
    拒绝 ``: * ? " < > |``、尾随点与保留设备名（CON / COM1 等，按首个点段判定，
    ``CON.backup`` 同样保留）。项目目录须可跨平台迁移，这些约束在所有平台统一执行，
    并在创建入口拒绝。
    """
    if not isinstance(name, str):
        raise ValueError(f"资产名称必须是字符串，当前为 {type(name).__name__}")
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("资产名称不能为空或仅含空白字符")
    if (
        ".." in cleaned
        or any(c in cleaned for c in ILLEGAL_ASSET_NAME_CHARS)
        or any(ord(c) < 32 or ord(c) == 127 for c in cleaned)
    ):
        raise ValueError(
            f'资产名称 {cleaned!r} 含非法字符：不允许路径分隔符（/ \\）、Windows 保留字符（: * ? " < > |）、控制字符或 ..'
        )
    if cleaned.endswith("."):
        raise ValueError(f"资产名称 {cleaned!r} 不能以点结尾（Windows 文件名约束）")
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_BASENAMES:
        raise ValueError(f"资产名称 {cleaned!r} 是 Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）")
    return cleaned
