"""角色衍生资产图的身份、落盘形状与生成准入（见 ``docs/adr/0072``）。

衍生挂在本体资产条目内的 ``DERIVATIVES_FIELD`` 表下，共享本体的名字与身份，自身只持有
一段相对本体的外观变化描述与一张资产图。那张资产图是对**本体资产图**的一次图片编辑
（``docs/adr/0050`` 语义）：变化描述即编辑指令，固定守卫要求保持三视图版式与其余外观不变；
本体没有资产图时不能生成。

本模块是「衍生资产图叫什么、落在哪、由什么输入生成」的唯一事实源，供衍生路由、执行层、
产物规划（时新性判定）与版本管理共用同一套坐标，避免各处自行拼 ``本体名/衍生名``。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from lib.api_errors import BadRequestError, NotFoundError
from lib.artifact_manifest import ArtifactBasis, ArtifactKey
from lib.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    AssetSpec,
    normalize_asset_name,
    resolve_asset_key,
    validate_asset_name,
)
from lib.resource_paths import (
    CHARACTER_DERIVATIVE_RESOURCE_TYPE,
    resource_relative_path,
    version_snapshot_dir,
)
from lib.visual_artifact_provenance import VisualReference, build_asset_sheet_visual_basis

#: 引用与产物 id 里分隔本体与衍生的字符。``/`` 已是资产名禁用字符，拼接无歧义。
DERIVATIVE_ID_SEPARATOR = "/"

#: 衍生资产图在队列与图片编辑 API 里的类型名：既是队列 task_type，也是 ``image_edit``
#: 请求体里的 ``resource_type``。两处必然同名（``TaskSpec`` 按同一张表判 id 段数），
#: 故只此一份；落盘与版本管理用的资源类型是另一个概念，见
#: ``resource_paths.CHARACTER_DERIVATIVE_RESOURCE_TYPE``。
DERIVATIVE_TASK_TYPE = "character_derivative"

#: 衍生资产图沿用哪一类资产的 spec。机制按 ``AssetSpec.supports_derivatives`` 通用建模，
#: 当前只有 character 开启，落盘与产物键因此固定在这一类上。
DERIVATIVE_ASSET_TYPE = "character"

#: 本体资产图作为编辑输入时，在产物依据里的角色与形态。规划侧与执行侧共用同一对取值，
#: 两侧不一致会让每一张衍生图恒判过期。
DERIVATIVE_SOURCE_ROLE = "derivative_source"
DERIVATIVE_SOURCE_KIND = "sheet"


def derivative_artifact_id(owner_name: str, derivative_name: str) -> str:
    """把本体名与衍生名拼成衍生资产图的产物 / 版本 / 路径 id。"""
    return f"{owner_name}{DERIVATIVE_ID_SEPARATOR}{derivative_name}"


def is_derivative_artifact_id(artifact_id: str) -> bool:
    """该 id 是否寻址一个衍生（而非本体）。"""
    return DERIVATIVE_ID_SEPARATOR in artifact_id


def split_derivative_artifact_id(artifact_id: str) -> tuple[str, str]:
    """把衍生资产图 id 拆回 (本体名, 衍生名)；两段都非空才算合法。"""
    owner, separator, derivative = artifact_id.partition(DERIVATIVE_ID_SEPARATOR)
    if not separator or not owner or not derivative or DERIVATIVE_ID_SEPARATOR in derivative:
        raise ValueError(f"不是合法的衍生资产图 id: {artifact_id!r}")
    return owner, derivative


def derivative_sheet_relative_path(owner_name: str, derivative_name: str) -> str:
    """衍生资产图在项目内的相对路径：``characters/derivatives/{本体}/{衍生}.png``。"""
    return resource_relative_path(
        CHARACTER_DERIVATIVE_RESOURCE_TYPE,
        derivative_artifact_id(owner_name, derivative_name),
    )


def derivative_sheet_dir(owner_name: str) -> str:
    """本体名下全部衍生资产图所在的项目内相对目录。"""
    return f"{PurePosixPath(derivative_sheet_relative_path(owner_name, '_')).parent}"


def derivative_version_dir(owner_name: str) -> str:
    """本体名下全部衍生资产图版本快照所在的项目内相对目录。"""
    return f"{version_snapshot_dir(CHARACTER_DERIVATIVE_RESOURCE_TYPE)}/{owner_name}"


def derivative_artifact_key(owner_name: str, derivative_name: str) -> ArtifactKey:
    """衍生资产图的产物清单键：``asset_sheet("character", "本体/衍生")``。"""
    return ArtifactKey.asset_sheet(
        DERIVATIVE_ASSET_TYPE,
        derivative_artifact_id(owner_name, derivative_name),
    )


def derivative_table(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    """读出本体条目里的衍生表；缺失或畸形按空表处理（结构错误另由校验层报告）。"""
    if not isinstance(entry, Mapping):
        return {}
    table = entry.get(DERIVATIVES_FIELD)
    return dict(table) if isinstance(table, Mapping) else {}


def ensure_derivative_table(entry: dict[str, Any]) -> dict[str, Any]:
    """取本体条目里可写的衍生表；缺失或畸形时就地补空表，让写入落在可预期的形状上。

    与只读的 :func:`derivative_table` 分工：那个返回副本供读取，这个返回条目内的原表供写入。
    """

    table = entry.get(DERIVATIVES_FIELD)
    if not isinstance(table, dict):
        table = {}
        entry[DERIVATIVES_FIELD] = table
    return table


def normalize_agent_derivatives(raw: object, *, spec: AssetSpec, field_path: str) -> dict[str, dict[str, Any]]:
    """把 Agent 写入路径收到的衍生表归一成落盘形状，非法输入 fail-loud。

    Agent 只写得动变化描述：资产图由生成流水线回写，故每条一律以空 sheet 字段构造，
    合并进已有衍生时由 :func:`merge_agent_derivatives` 保留原路径。名字沿用资产名校验，
    规范化（strip + NFC）后等价的两个键会静默互相覆盖，因此整表拒绝而非取其一。

    *field_path* 是错误消息里指向该表的字段路径前缀，由调用方按自己的入参形状拼出。
    """

    if not isinstance(raw, Mapping):
        raise ValueError(f"{field_path}.{DERIVATIVES_FIELD} 必须是对象（衍生名 → {{description}}）")
    normalized: dict[str, dict[str, Any]] = {}
    raw_keys: dict[str, object] = {}
    for raw_name, attrs in raw.items():
        try:
            name = validate_asset_name(raw_name)
        except ValueError as exc:
            raise ValueError(f"{field_path}.{DERIVATIVES_FIELD}: {exc}") from None
        if name in normalized:
            raise ValueError(
                f"{field_path}.{DERIVATIVES_FIELD} 含规范化后冲突的衍生名 {name!r}："
                f"原始键 {raw_keys[name]!r} 与 {raw_name!r} 在规范化（strip + NFC）后等价"
            )
        if not isinstance(attrs, Mapping):
            raise ValueError(f"{field_path}.{DERIVATIVES_FIELD}[{name!r}] 必须是对象")
        extra = sorted(str(key) for key in attrs if key != "description")
        if extra:
            raise ValueError(f"{field_path}.{DERIVATIVES_FIELD}[{name!r}] 含不可写字段: {extra}")
        description = attrs.get("description")
        if not isinstance(description, str):
            raise ValueError(f"{field_path}.{DERIVATIVES_FIELD}[{name!r}].description 必须是字符串")
        normalized[name] = {"description": description, spec.sheet_field: ""}
        raw_keys[name] = raw_name
    return normalized


def merge_agent_derivatives(entry: dict[str, Any], normalized: Mapping[str, dict[str, Any]]) -> None:
    """把归一化后的衍生表并入本体条目：同名改描述、新名整条加入，未提及的原样留下。

    整表替换会连带抹掉未提及衍生的资产图，故按名合并；已有条目的存量 key 可能是 NFD，
    按 :func:`resolve_asset_key` 就地更新而非按 NFC 名新建第二条。
    """

    table = ensure_derivative_table(entry)
    for name, derivative in normalized.items():
        key = resolve_asset_key(table, name)
        current = table.get(key) if key is not None else None
        if key is not None and isinstance(current, dict):
            current["description"] = derivative["description"]
        else:
            table[key if key is not None else name] = dict(derivative)


@dataclass(frozen=True, slots=True)
class DerivativeSheetTarget:
    """一个衍生资产图的写入坐标，两段名都是落盘真名（NFC/NFD 已解析）。"""

    owner_key: str
    derivative_key: str

    @property
    def artifact_id(self) -> str:
        return derivative_artifact_id(self.owner_key, self.derivative_key)

    @property
    def sheet_path(self) -> str:
        return derivative_sheet_relative_path(self.owner_key, self.derivative_key)


@dataclass(frozen=True, slots=True)
class DerivativeSheetSource:
    """一次衍生资产图生成的全部输入，按落盘真名解析。"""

    target: DerivativeSheetTarget
    description: str
    owner_sheet_path: str

    @property
    def owner_key(self) -> str:
        return self.target.owner_key

    @property
    def derivative_key(self) -> str:
        return self.target.derivative_key


def resolve_derivative_target(
    project: Mapping[str, Any],
    owner_name: str,
    derivative_name: str,
) -> DerivativeSheetTarget:
    """把一对可能是任一编码形式的名字解析成落盘真名坐标。

    本体不存在抛 ``KeyError``（与本体资产的寻址口径一致），衍生不存在抛 404。
    """
    spec = ASSET_SPECS[DERIVATIVE_ASSET_TYPE]
    bucket = project.get(spec.bucket_key)
    owner_key = resolve_asset_key(bucket, owner_name)
    entry = bucket.get(owner_key) if isinstance(bucket, Mapping) and owner_key is not None else None
    if owner_key is None or not isinstance(entry, Mapping):
        raise KeyError(f"{spec.label_zh} '{owner_name}' 不存在")
    table = derivative_table(entry)
    derivative_key = resolve_asset_key(table, derivative_name)
    if derivative_key is None or not isinstance(table.get(derivative_key), Mapping):
        raise NotFoundError("asset_derivative_not_found", name=derivative_name)
    return DerivativeSheetTarget(owner_key=owner_key, derivative_key=derivative_key)


def resolve_derivative_sheet_source(
    project: Mapping[str, Any],
    owner_name: str,
    derivative_name: str,
) -> DerivativeSheetSource:
    """解析衍生资产图生成的输入，并在此拒绝一切不可生成的形态。

    本体不存在抛 ``KeyError``（与本体资产的寻址口径一致），衍生不存在抛 404；本体没有
    资产图、或衍生还没写变化描述时抛 400——两者都是用户可自行修复的前置条件，说明
    分开给，指向的动作不同。
    """
    spec = ASSET_SPECS[DERIVATIVE_ASSET_TYPE]
    target = resolve_derivative_target(project, owner_name, derivative_name)
    bucket = project[spec.bucket_key]
    entry = bucket[target.owner_key]
    derivative = derivative_table(entry)[target.derivative_key]

    description = derivative.get("description")
    if not isinstance(description, str) or not description.strip():
        raise BadRequestError("derivative_description_required", name=target.derivative_key)

    owner_sheet = entry.get(spec.sheet_field)
    if not isinstance(owner_sheet, str) or not owner_sheet:
        raise BadRequestError("derivative_owner_sheet_missing", name=target.owner_key)

    return DerivativeSheetSource(
        target=target,
        description=description.strip(),
        owner_sheet_path=owner_sheet,
    )


def derivative_source_reference(owner_name: str, owner_sheet_file: Path) -> VisualReference:
    """把本体资产图包成衍生生成的唯一图像输入证据。"""
    return VisualReference(
        path=owner_sheet_file,
        role=DERIVATIVE_SOURCE_ROLE,
        logical_type=DERIVATIVE_ASSET_TYPE,
        logical_id=normalize_asset_name(owner_name),
        kind=DERIVATIVE_SOURCE_KIND,
    )


def build_derivative_sheet_basis(
    *,
    owner_name: str,
    derivative_name: str,
    description: str,
    aspect_ratio: str,
    source: VisualReference,
) -> ArtifactBasis:
    """衍生资产图的规范依据：变化描述 + 本体资产图内容，不含项目画风。

    画风由本体资产图自身承载——衍生只是对它的一次编辑，编辑指令里不再注入项目 style
    （与图片编辑同口径）。规划侧与执行侧都经此构造，因此本体资产图一旦重生成、或变化
    描述被改写，衍生的登记依据即与规范状态不符，判为过期。
    """
    return build_asset_sheet_visual_basis(
        asset_type=DERIVATIVE_ASSET_TYPE,
        asset_id=derivative_artifact_id(owner_name, derivative_name),
        description=description,
        style="",
        style_description="",
        aspect_ratio=aspect_ratio,
        references=(source,),
    )
