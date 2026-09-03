"""剧本 / 草稿载荷里名称引用的落点遍历：改写与扫描共用同一份「引用写在哪些位置」。

引用名（资产名，或角色的 ``本体名/衍生名``）在剧本载荷里有三种落点：各骨架的引用数组
（``characters_in_*`` / ``scenes`` / ``props`` / ``products_in_shot``）、``speaker`` 字段，
以及单元正文里的 ``@[名称]`` 记号。级联改名要把它们一次改齐，「这条资产被脚本引用了吗」
要把它们一次数清——两者只在「读到的名字怎么处理」上不同，落点集合必须是同一份：分成两套
遍历时，新增一种落点只被其中一侧认识，改名会漏改或引用状态会漏报。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any, Literal

from lib.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, asset_name_comparison_key
from lib.reference_catalog import derivative_reference
from lib.reference_video.text_parser import mention_names

#: 承载单元正文的列表字段。参考生视频的 mention 落在 ``video_units[].text``（草稿里是
#: ``units[].text``），ad 分镜的 shot 还带引用数组与 ``video_prompt.dialogue``。
_TEXT_ITEM_FIELDS: frozenset[str] = frozenset({"shots", "units", "video_units"})

#: 全部资产类型的引用数组字段。扫描不限类型（要数的是「这个名字出现了吗」），改写按类型
#: 取子集（改角色名不该动 ``scenes`` 数组）。
_ALL_REFERENCE_LIST_FIELDS: frozenset[str] = frozenset(
    field for spec in ASSET_SPECS.values() for field in spec.reference_list_fields
)

#: 引用落点的两种形态：``name`` 是一个完整的引用名，``text`` 是含 ``@[名称]`` 记号的正文。
SiteKind = Literal["name", "text"]


def _dict_writer(node: dict[str, Any], key: str) -> Callable[[str], None]:
    def write(value: str) -> None:
        node[key] = value

    return write


def _list_writer(items: list[Any], index: int) -> Callable[[str], None]:
    def write(value: str) -> None:
        items[index] = value

    return write


def iter_reference_sites(
    payload: object,
    list_fields: frozenset[str],
    *,
    with_speaker: bool,
) -> Iterator[tuple[SiteKind, str, Callable[[str], None]]]:
    """遍历载荷里承载名称引用的位置，产出 ``(类别, 当前值, 写回)``。

    只识别骨架结构、不校验语义：结构校验由写盘统一入口的「不更坏」守卫兜底。写回只替换已
    存在的键 / 下标，不增删元素，故遍历期间就地改写是安全的。

    ``with_speaker`` 控制 ``speaker`` 字段是否算落点：它只承载角色本体名（衍生共享本体的
    声音，见 ``docs/adr/0072``），改场景 / 道具 / 商品名时不该扫它。
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in list_fields and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, str):
                        yield "name", item, _list_writer(value, index)
                    else:
                        yield from iter_reference_sites(item, list_fields, with_speaker=with_speaker)
                continue
            if key == "speaker" and with_speaker and isinstance(value, str):
                yield "name", value, _dict_writer(payload, key)
                continue
            if key in _TEXT_ITEM_FIELDS and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        yield "text", item["text"], _dict_writer(item, "text")
                    yield from iter_reference_sites(item, list_fields, with_speaker=with_speaker)
                continue
            yield from iter_reference_sites(value, list_fields, with_speaker=with_speaker)
    elif isinstance(payload, list):
        for item in payload:
            yield from iter_reference_sites(item, list_fields, with_speaker=with_speaker)


def payload_reference_names(payload: object) -> set[str]:
    """载荷里写下的全部引用名，已在比对坐标系中；跨类型合并、不分归属。

    问的是「这个名字写在剧本里了吗」，故不限资产类型也不判登记——归属与登记由
    :class:`lib.reference_catalog.ReferenceCatalog` 回答。
    """
    names: set[str] = set()
    for kind, value, _write in iter_reference_sites(payload, _ALL_REFERENCE_LIST_FIELDS, with_speaker=True):
        if kind == "text":
            names.update(mention_names(value))
        else:
            names.add(asset_name_comparison_key(value))
    return names


def annotate_derivative_references(project: object, payloads: Iterable[object]) -> dict[str, Any]:
    """返回给每个衍生条目补上 ``referenced`` 的项目载荷副本：脚本里有没有写 ``本体名/衍生名``。

    读时计算、不落盘：衍生表的落盘字段只有 ``description`` 与资产图路径（见 ``docs/adr/0072``），
    本字段只随读取接口返回，供浮层呈现「这套外观用上了没有」。故不就地改写入参——写盘路径与
    读取路径共用同一份载荷时，就地改写会把这个派生字段带进 project.json。

    判定与级联改名同一份落点集合（:func:`iter_reference_sites`）：作者写在哪里算引用，改名就
    改哪里，两者不会各说各话。
    """
    payload: dict[str, Any] = dict(project) if isinstance(project, dict) else {}
    referenced: set[str] = set()
    for item in payloads:
        referenced |= payload_reference_names(item)
    for spec in ASSET_SPECS.values():
        if not spec.supports_derivatives:
            continue
        bucket = payload.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        payload[spec.bucket_key] = {
            name: _annotated_entry(asset, asset_name_comparison_key(str(name)), referenced)
            for name, asset in bucket.items()
        }
    return payload


def _annotated_entry(asset: object, base_name: str, referenced: set[str]) -> object:
    """资产条目的副本，其衍生表每条带上 ``referenced``；没有衍生表时原样返回。"""
    if not isinstance(asset, dict):
        return asset
    table = asset.get(DERIVATIVES_FIELD)
    if not isinstance(table, dict):
        return asset
    return {
        **asset,
        DERIVATIVES_FIELD: {
            derivative_name: (
                {
                    **derivative,
                    "referenced": derivative_reference(base_name, asset_name_comparison_key(str(derivative_name)))
                    in referenced,
                }
                if isinstance(derivative, dict)
                else derivative
            )
            for derivative_name, derivative in table.items()
        },
    }


__all__ = [
    "SiteKind",
    "annotate_derivative_references",
    "iter_reference_sites",
    "payload_reference_names",
]
