"""项目级资产类型规格（character / scene / prop / product）的单一事实源。

升级自原 BUCKET_KEY / SHEET_KEY 常量字典：用 AssetSpec dataclass 描述每类资产
完整属性（bucket / sheet 字段 / 子目录 / 中文标签 / 额外字符串字段 / 额外列表字段），
供 ProjectManager 统一资产 API 与 server/routers/_asset_router_factory 共享。

旧常量 ASSET_TYPES / BUCKET_KEY / SHEET_KEY 保留为 ASSET_SPECS 的派生，现有 18 处
引用零修改。

面向用户的显示名不落在 spec 里：``localize_asset_type`` 以注入的 translate 把类型标识
映射到 ``lib/i18n`` 的 ``asset_type_*`` key，本模块因而不反向依赖 i18n。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssetSpec:
    """单一资产类型的所有结构性属性。

    ``extra_string_fields`` 是 schema 维度——validator 据此校验「这些字段若存在须为
    string」、`_build_asset_entry` 据此初始化默认空串、REST PATCH 据此扩展可更新字段集；
    ``extra_list_fields`` 是 schema 维度的列表变体——字段若存在须为「字符串列表」，
    `_build_asset_entry` 初始化默认空列表，REST PATCH 同样据此扩展；
    ``agent_editable_extra_fields`` 是权限维度——`upsert_assets`（agent 走的入口）的字段
    白名单来自这里，**不复用 schema 维度**。两者解耦的原因：``reference_image`` /
    ``reference_images`` 是用户上传或系统生成的文件路径，是 schema 维度字段但不是
    ``agent_editable_extra_fields``（agent 不该覆写用户上传的路径，更新走专用 API，
    与 sheet_field 同性质）。

    ``in_global_library`` 控制该类型是否进入跨项目全局资产库（assets 表）：库的
    单图列模型只兼容「一资产一图」的类型，多图列表型资产（product）暂不进入。

    ``label_zh`` 服务 logger 与 agent 侧字符串（两者按 i18n 规范豁免翻译）；
    面向用户的资产类型显示名走 ``lib/i18n`` 的 ``asset_type_*`` key，不复用此字段。
    """

    asset_type: str
    bucket_key: str
    sheet_field: str
    subdir: str
    label_zh: str
    extra_string_fields: tuple[str, ...] = ()
    extra_list_fields: tuple[str, ...] = ()
    agent_editable_extra_fields: tuple[str, ...] = ()
    in_global_library: bool = True


ASSET_SPECS: dict[str, AssetSpec] = {
    "character": AssetSpec(
        asset_type="character",
        bucket_key="characters",
        sheet_field="character_sheet",
        subdir="characters",
        label_zh="角色",
        extra_string_fields=("voice_style", "reference_image", "reference_audio", "voice_notice_dismissed_at"),
        # voice_style 是 LLM 生成的角色配音风格，agent 可改；reference_image / reference_audio
        # 是用户上传的文件路径（系统级），不进 agent 白名单——更新分别走
        # update_character_reference_image / update_character_reference_audio。
        # voice_notice_dismissed_at 记录用户已确认到的 voice_updated_at 版本，由前端「关闭」
        # 动作通过本通用 PATCH 写入；agent 不该也无需感知这个 UI 状态，不进白名单。
        # 与之比较的 voice_updated_at 不在此列——只由系统在 reference_audio 实际变更时机械戳写
        # （update_character_reference_audio 与全局资产库导入），不开放任意 PATCH 覆写
        # （否则该比较可被客户端绕过）。
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
    "product": AssetSpec(
        asset_type="product",
        bucket_key="products",
        sheet_field="product_sheet",
        subdir="products",
        label_zh="产品",
        # brand 是用户填写的品牌要素自由文本；reference_images 是用户上传的多张产品
        # 原图路径（系统级，保真验收锚点），selling_points 是卖点列表（agent 起草、
        # 用户可改）。
        extra_string_fields=("brand",),
        extra_list_fields=("reference_images", "selling_points"),
        # selling_points 允许 agent 起草/修改；reference_images 是上传路径（与
        # reference_image 同性质），不进 agent 白名单，更新走专用上传 API。
        agent_editable_extra_fields=("selling_points",),
        # 全局资产库是单图列模型，多图列表型的 product 暂不进入（跨项目复用为后续工作）。
        in_global_library=False,
    ),
}


ASSET_TYPES: frozenset[str] = frozenset(ASSET_SPECS.keys())

BUCKET_KEY: dict[str, str] = {t: s.bucket_key for t, s in ASSET_SPECS.items()}

SHEET_KEY: dict[str, str] = {t: s.sheet_field for t, s in ASSET_SPECS.items()}

GLOBAL_LIBRARY_ASSET_TYPES: frozenset[str] = frozenset(t for t, s in ASSET_SPECS.items() if s.in_global_library)

ILLEGAL_ASSET_NAME_CHARS: tuple[str, ...] = ("/", "\\", "\0", ":", "*", "?", '"', "<", ">", "|")

WINDOWS_RESERVED_BASENAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)


def localize_asset_type(value: str, translate: Callable[..., str]) -> str:
    """把资产类型内部标识（如 ``"product"``）替换为当前语言显示名。

    未登记的类型值（不在 ``ASSET_SPECS`` 中）原样透传，不做语义映射。
    """
    if value not in ASSET_SPECS:
        return value
    return translate(f"asset_type_{value}")


def normalize_asset_name(name: str) -> str:
    """把资产名归一到比对坐标系（Unicode NFC）——资产名判等/判成员的坐标系定义点。

    同一个名字有两种等价编码：NFC（合成形式，网页表单与 project.json 登记侧的主形态）与
    NFD（分解形式，macOS 文件名系统与部分输入法产出）。两者屏幕显示完全相同、字节不同，
    ``==`` 与 ``in`` 判不相等；组合附加符高发的语种（如产品三语中的 vi）尤其容易同时出现
    两种形式。逐字比对因此必须先落到同一形式，否则用户对着两个肉眼一致的名字无从排查。

    约束：资产名判等和成员测试前必须归一化。文本名称调用本函数，资产表 key 调用
    :func:`normalize_asset_bucket`。归一放在读取与解析的入口、不逐个比对点补；不得直接比较
    未归一化的名称。

    归一只做编码形式收敛，不改字、不改长度语义，对纯 ASCII 名是恒等变换。
    """
    return unicodedata.normalize("NFC", name)


def normalize_asset_bucket(bucket: object) -> dict[str, Any]:
    """把资产桶读成 key 已归一到比对坐标系的字典；非 dict 的畸形值按空桶处理。

    资产名的比对总是「文本里的名字 × 资产表的 key」，两侧都要在同一坐标系里才判得准。
    读取处归一一次即可覆盖存量数据——落盘的 key 可能是任一形式，而调用点只该关心比对结果。

    同名不同形式的 key 归一后会合并（后写入的胜出）：它们本就指同一个资产名，资产表不应
    同时存在两条，合并即修复而非丢数据。
    """
    if not isinstance(bucket, dict):
        return {}
    return {normalize_asset_name(str(name)): item for name, item in bucket.items()}  # pyright: ignore[reportUnknownVariableType]


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
