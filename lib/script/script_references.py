"""剧本 / 草稿载荷里名称引用的落点遍历：改写与扫描共用同一份「引用写在哪些位置」。

引用名（资产名，或角色的 ``本体名/衍生名``）在剧本载荷里有五种落点：各骨架的引用数组
（``characters_in_*`` / ``scenes`` / ``props`` / ``products_in_shot``）、``speaker`` 字段、
单元正文里的 ``@[名称]`` 记号、分镜画面描述里的 ``@[名称]`` 记号，以及脚本规划「新增资产」清单
里并入项与衍生项的 ``target``。级联改名与合并要把它们一次改齐，「这条资产被脚本引用了吗」要把
它们一次数清——两者只在「读到的名字怎么处理」上不同，落点集合必须是同一份：分成两套遍历时，
新增一种落点只被其中一侧认识，改名会漏改或引用状态会漏报。

分镜画面描述里的记号只指认该分镜引用字段里已声明的资产、不派生参考图（见
:mod:`lib.script.storyboard_mentions`），所以改名要改它，「引用了吗」不数它。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any, Literal

from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, asset_name_comparison_key
from lib.references.reference_catalog import derivative_reference
from lib.script.reference_video.text_parser import mention_names
from lib.script.script_models import NEW_ASSETS_FIELD

#: 承载单元正文的列表字段。参考生视频的 mention 落在 ``video_units[].text``（草稿里是
#: ``units[].text``），ad 分镜的 shot 还带引用数组与 ``video_prompt.dialogue``。
_TEXT_ITEM_FIELDS: frozenset[str] = frozenset({"shots", "units", "video_units"})

#: 全部资产类型的引用数组字段。扫描不限类型（要数的是「这个名字出现了吗」），改写按类型
#: 取子集（改角色名不该动 ``scenes`` 数组）。
_ALL_REFERENCE_LIST_FIELDS: frozenset[str] = frozenset(
    field for spec in ASSET_SPECS.values() for field in spec.reference_list_fields
)

#: 引用落点的五种形态：``name`` 是引用数组里的一个完整引用名，``speaker`` 是承载角色本体名的
#: 说话人字段，``text`` 是含 ``@[名称]`` 记号的单元正文，``scene`` 是含 ``@[名称]`` 记号的分镜画面
#: 描述（``image_prompt.scene``，或字符串形态的 ``image_prompt``），``target`` 是新增资产清单里
#: 并入项归入的资产名或衍生项的本体角色名——与说话人一样只写资产表的条目名，不写 ``本体名/衍生名``。
SiteKind = Literal["name", "speaker", "text", "scene", "target"]


def _dict_writer(node: dict[str, Any], key: str) -> Callable[[str], None]:
    def write(value: str) -> None:
        node[key] = value

    return write


def _list_writer(items: list[Any], index: int) -> Callable[[str], None]:
    def write(value: str) -> None:
        items[index] = value

    return write


def _new_asset_target_sites(
    items: list[Any], list_fields: frozenset[str]
) -> Iterator[tuple[SiteKind, str, Callable[[str], None]]]:
    """新增资产清单里的 ``target`` 落点；只产出指向的资产类型落在 ``list_fields`` 覆盖范围内的那些。

    并入项的 ``target`` 指向同类资产，衍生项的指向本体角色；其余处理决定的 ``target`` 留空，不是
    落点。``list_fields`` 即调用方关心的资产类型：改角色名时只给角色的引用数组字段，并入场景的
    那一项就不该被当成落点。
    """
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("target"), str):
            continue
        decision = item.get("decision")
        target_type = item.get("type") if decision == "merge" else "character" if decision == "derivative" else None
        spec = ASSET_SPECS.get(target_type) if isinstance(target_type, str) else None
        if spec is not None and not list_fields.isdisjoint(spec.reference_list_fields):
            yield "target", item["target"], _dict_writer(item, "target")


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
            if key == NEW_ASSETS_FIELD and isinstance(value, list):
                yield from _new_asset_target_sites(value, list_fields)
                continue
            if key == "speaker" and with_speaker and isinstance(value, str):
                yield "speaker", value, _dict_writer(payload, key)
                continue
            if key == "image_prompt":
                if isinstance(value, str):
                    yield "scene", value, _dict_writer(payload, key)
                elif isinstance(value, dict) and isinstance(value.get("scene"), str):
                    yield "scene", value["scene"], _dict_writer(value, "scene")
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
    :class:`lib.references.reference_catalog.ReferenceCatalog` 回答。
    """
    names: set[str] = set()
    for kind, value, _write in iter_reference_sites(payload, _ALL_REFERENCE_LIST_FIELDS, with_speaker=True):
        if kind == "text":
            names.update(mention_names(value))
        elif kind != "scene":
            names.add(asset_name_comparison_key(value))
    return names


def iter_reference_lists(payload: object, list_fields: frozenset[str]) -> Iterator[list[Any]]:
    """遍历载荷里的引用数组本身，供需要增删元素的调用方（如合并后去重）使用。

    与 :func:`iter_reference_sites` 认同一组数组：键在 ``list_fields`` 中、值是列表即是。
    产出的列表可就地增删元素，遍历不会再进入已产出的列表。
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in list_fields and isinstance(value, list):
                yield value
                for item in value:
                    if not isinstance(item, str):
                        yield from iter_reference_lists(item, list_fields)
                continue
            yield from iter_reference_lists(value, list_fields)
    elif isinstance(payload, list):
        for item in payload:
            yield from iter_reference_lists(item, list_fields)


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
    "iter_reference_lists",
    "iter_reference_sites",
    "payload_reference_names",
]
