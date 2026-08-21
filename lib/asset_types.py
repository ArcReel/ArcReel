"""项目级资产类型规格（character / scene / prop / product）的单一事实源。

``AssetSpec`` 描述每类资产的 bucket、sheet 字段、子目录、显示标签、引用字段与扩展字段，
供 ProjectManager 统一资产 API、名称空间校验和 server/routers/_asset_router_factory 共享。
兼容常量 ASSET_TYPES / BUCKET_KEY / SHEET_KEY 均从 ASSET_SPECS 派生。

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

    ``original_image_fields`` 列出该类型登记用户原图的字段（单值或列表均可）。参考生视频
    执行期按「有资产图用资产图，否则用全部原图」展开参考图，该顺序即字段声明顺序；未登记
    原图字段的类型（场景 / 道具）没有资产图时就不产生参考图候选。

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
    namespace_priority: int
    reference_list_fields: tuple[str, ...]
    original_image_fields: tuple[str, ...] = ()
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
        namespace_priority=0,
        reference_list_fields=("characters_in_segment", "characters_in_scene", "characters_in_shot"),
        original_image_fields=("reference_image",),
        extra_string_fields=(
            "voice_style",
            "voice_id",
            "reference_image",
            "reference_audio",
            "voice_notice_dismissed_at",
        ),
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
        namespace_priority=1,
        reference_list_fields=("scenes",),
        extra_string_fields=(),
        agent_editable_extra_fields=(),
    ),
    "prop": AssetSpec(
        asset_type="prop",
        bucket_key="props",
        sheet_field="prop_sheet",
        subdir="props",
        label_zh="道具",
        namespace_priority=2,
        reference_list_fields=("props",),
        extra_string_fields=(),
        agent_editable_extra_fields=(),
    ),
    "product": AssetSpec(
        asset_type="product",
        bucket_key="products",
        sheet_field="product_sheet",
        subdir="products",
        label_zh="商品",
        namespace_priority=3,
        reference_list_fields=("products_in_shot",),
        original_image_fields=("reference_images",),
        # brand 是用户填写的品牌要素自由文本；reference_images 是用户上传的多张产品
        # 原图路径（系统级，保真验收锚点），selling_points 是卖点列表（agent 起草、
        # 用户可改）。
        extra_string_fields=("brand",),
        extra_list_fields=("reference_images", "selling_points"),
        # selling_points 允许 agent 起草/修改；reference_images 是上传路径（与
        # reference_image 同性质），不进 agent 白名单，更新走专用上传 API。
        agent_editable_extra_fields=("selling_points",),
        # 全局资产库是单图列模型，多图列表型的 product 不进入。
        in_global_library=False,
    ),
}


ASSET_TYPES: frozenset[str] = frozenset(ASSET_SPECS.keys())

BUCKET_KEY: dict[str, str] = {t: s.bucket_key for t, s in ASSET_SPECS.items()}

SHEET_KEY: dict[str, str] = {t: s.sheet_field for t, s in ASSET_SPECS.items()}

GLOBAL_LIBRARY_ASSET_TYPES: frozenset[str] = frozenset(t for t, s in ASSET_SPECS.items() if s.in_global_library)

# 项目资产只保存全局资产的逻辑关联，不复制全局资产内容。matched 字段由资产提取流程
# 机械写入，global 字段仅由用户确认链接的 API 写入；二者都不开放通用 PATCH。
MATCHED_GLOBAL_ASSET_ID_FIELD = "matched_global_asset_id"
GLOBAL_ASSET_ID_FIELD = "global_asset_id"
PROJECT_ASSET_LINK_FIELDS: tuple[str, ...] = (
    MATCHED_GLOBAL_ASSET_ID_FIELD,
    GLOBAL_ASSET_ID_FIELD,
)


@dataclass(frozen=True)
class ProjectAssetNameMatch:
    """项目共享资产名称空间中的一个已有条目。"""

    asset_type: str
    bucket_key: str
    name: str


class ProjectAssetNameConflictError(ValueError):
    """项目内的资产名与另一资产冲突。"""

    def __init__(self, name: str, existing: ProjectAssetNameMatch, requested_asset_type: str | None = None):
        self.name = name
        self.existing = existing
        self.requested_asset_type = requested_asset_type
        requested = (
            f"新增/改名后的{ASSET_SPECS[requested_asset_type].label_zh}"
            if requested_asset_type in ASSET_SPECS
            else "待写入资产"
        )
        super().__init__(
            f"{requested}名称 {name!r} 与项目内已有{ASSET_SPECS[existing.asset_type].label_zh} "
            f"{existing.name!r} 冲突（按 strip + Unicode NFC、大小写敏感判定）"
        )


ILLEGAL_ASSET_NAME_CHARS: tuple[str, ...] = ("/", "\\", "\0", ":", "*", "?", '"', "<", ">", "|", "]")

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


def asset_name_comparison_key(name: str) -> str:
    """项目级资产名判等键：去除两端空白后收敛到 Unicode NFC。

    这一坐标系仅用于名称空间判等，不做 case-fold；项目资产名大小写敏感。
    """
    return normalize_asset_name(name.strip())


def find_project_asset_name(
    project: object,
    name: str,
    *,
    exclude_asset_type: str | None = None,
    exclude_name: str | None = None,
) -> ProjectAssetNameMatch | None:
    """在 ``ASSET_SPECS`` 驱动的四类项目资产中查找等价名称。

    ``exclude_*`` 只排除一个确切落盘条目，供 rename 检查目标名时忽略自身。
    畸形 bucket 按空处理，其结构错误由 DataValidator 另行报告。
    """
    if not isinstance(project, dict):
        return None
    target = asset_name_comparison_key(name)
    for spec in sorted(ASSET_SPECS.values(), key=lambda item: item.namespace_priority):
        asset_type = spec.asset_type
        bucket = project.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        for raw_name in bucket:
            if not isinstance(raw_name, str):
                continue
            if asset_type == exclude_asset_type and raw_name == exclude_name:
                continue
            if asset_name_comparison_key(raw_name) == target:
                return ProjectAssetNameMatch(asset_type=asset_type, bucket_key=spec.bucket_key, name=raw_name)
    return None


def ensure_project_asset_name_available(
    project: object,
    name: str,
    *,
    requested_asset_type: str | None = None,
    exclude_asset_type: str | None = None,
    exclude_name: str | None = None,
) -> None:
    """断言项目共享名称空间中 *name* 可用，否则给出结构化冲突诊断。"""
    existing = find_project_asset_name(
        project,
        name,
        exclude_asset_type=exclude_asset_type,
        exclude_name=exclude_name,
    )
    if existing is not None:
        raise ProjectAssetNameConflictError(name, existing, requested_asset_type)


def project_asset_name_conflicts(project: object) -> list[tuple[ProjectAssetNameMatch, ProjectAssetNameMatch]]:
    """返回项目共享名称空间里的所有重名对，顺序稳定可复现。"""
    if not isinstance(project, dict):
        return []
    first_by_key: dict[str, ProjectAssetNameMatch] = {}
    conflicts: list[tuple[ProjectAssetNameMatch, ProjectAssetNameMatch]] = []
    specs = sorted(ASSET_SPECS.values(), key=lambda spec: spec.namespace_priority)
    for spec in specs:
        bucket = project.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        for raw_name in bucket:
            if not isinstance(raw_name, str):
                continue
            current = ProjectAssetNameMatch(spec.asset_type, spec.bucket_key, raw_name)
            key = asset_name_comparison_key(raw_name)
            first = first_by_key.setdefault(key, current)
            if first is not current:
                conflicts.append((first, current))
    return conflicts


def ensure_project_asset_namespace(project: object) -> None:
    """断言项目四类资产全局唯一，报告第一个稳定冲突。"""
    conflicts = project_asset_name_conflicts(project)
    if conflicts:
        first, current = conflicts[0]
        raise ProjectAssetNameConflictError(current.name, first, current.asset_type)


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


def resolve_asset_key(bucket: object, name: str) -> str | None:
    """在资产桶中按比对坐标系（NFC）解析 *name* 对应的真实落盘 key；未命中返回 None。

    写入侧已统一落 NFC（:func:`validate_asset_name`），但存量数据的 key 可能是任一
    编码形式且无需迁移：按 key 就地更新/删除/冲突判定时不能拿归一后的名字直接下标，
    须先解析出真实 key 再操作，否则会对同一个视觉名字新建第二条资产或漏判冲突。

    同名多形式的存量 key 之间后写入的胜出，与 :func:`normalize_asset_bucket` 的合并
    方向一致。非 dict 的畸形桶按空桶处理。
    """
    if not isinstance(bucket, dict):
        return None
    target = normalize_asset_name(name)
    found: str | None = None
    for key in bucket:
        if isinstance(key, str) and normalize_asset_name(key) == target:
            found = key
    return found


def rekey_equivalent_entries[T](bucket: dict[str, T], old_name: str, new_name: str) -> T | None:
    """把桶中与 *old_name* 视觉等价的全部 key 一并改成 *new_name*，返回改名后该 key 的值。

    改名要收编整簇等价 key（NFC / NFD 并存）：只改 :func:`resolve_asset_key` 选中的那一条，
    另一条等价 key 会顶着旧名带着失效的路径字段留在桶里，之后还可能被重建的同名资产接上。
    合并方向与读侧一致——后写入的值胜出，即读侧解析到的那一条。桶里没有等价 key 时返回
    ``None`` 且不改动桶。

    原地重建而非 pop + 赋值，保留条目在桶中的原位置：JSON 里资产的排列顺序是用户可见的。
    """
    if resolve_asset_key(bucket, old_name) is None:
        return None
    target = normalize_asset_name(old_name)
    rekeyed = {(new_name if normalize_asset_name(key) == target else key): value for key, value in bucket.items()}
    bucket.clear()
    bucket.update(rekeyed)
    return rekeyed[new_name]


def validate_asset_name(name: object) -> str:
    """校验并规范化（strip + NFC）资产名，非法时抛 ValueError，合法时返回规范化后的名字。

    这是登记路径的规范化闸口：所有新登记/新写入的资产名经此落到 NFC 坐标系
    （见 :func:`normalize_asset_name`），NFD 输入不再以另一种编码形式落盘产生
    视觉同名的重复资产。

    资产名全链路被当作单段路径组件使用：文件名（``characters/{name}.png``、
    ``versions/{type}/{name}_v{n}_{ts}.png``）与 REST 路由的单段路径参数。含路径
    分隔符、控制字符或 ``..`` 的名字会产生嵌套路径与无法匹配的 URL；Windows 还会
    拒绝 ``: * ? " < > |``、尾随点与保留设备名（CON / COM1 等，按首个点段判定，
    ``CON.backup`` 同样保留）。项目目录须可跨平台迁移，这些约束在所有平台统一执行，
    并在创建入口拒绝。

    ``]`` 一并拒绝：正文里的引用写作 ``@[名称]``，解析以首个 ``]`` 收尾
    （见 :func:`lib.reference_video.text_parser._iter_mentions`），名字含 ``]`` 时写出的
    引用会在中途截断。``[`` 不致坏——名为 ``[甲`` 时 ``@[[甲]`` 仍解析回完整的 ``[甲``——不拦。
    """
    if not isinstance(name, str):
        raise ValueError(f"资产名称必须是字符串，当前为 {type(name).__name__}")
    cleaned = normalize_asset_name(name.strip())
    if not cleaned:
        raise ValueError("资产名称不能为空或仅含空白字符")
    if (
        ".." in cleaned
        or any(c in cleaned for c in ILLEGAL_ASSET_NAME_CHARS)
        or any(ord(c) < 32 or ord(c) == 127 for c in cleaned)
    ):
        raise ValueError(
            f"资产名称 {cleaned!r} 含非法字符：不允许路径分隔符（/ \\）、"
            f'Windows 保留字符（: * ? " < > |）、引用定界符（]）、控制字符或 ..'
        )
    if cleaned.endswith("."):
        raise ValueError(f"资产名称 {cleaned!r} 不能以点结尾（Windows 文件名约束）")
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED_BASENAMES:
        raise ValueError(f"资产名称 {cleaned!r} 是 Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）")
    return cleaned
