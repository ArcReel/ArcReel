"""读 API 格式 workflow 的原语：节点字段与连线判定。

ComfyUI 的「Export (API)」导出物是「节点 id → 节点」的对象，节点的每个 ``inputs`` 字段要么是
字面值，要么是一条指向上游的连线。两者在 JSON 里没有标记可分，只能按形状判：连线是长度 2 的
数组 ``[上游节点 id, 输出序号]``。字面值本身恰好是数组时，导出物把它包成 ``{"__value__": [...]}``，
解包后即字面值、不再按连线判。

节点绑定只能落在字面值字段上：连线字段的值在运行时由上游节点产生，往那里填值会被覆盖。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: 字面值恰好是数组时的包装键：带这层包装的字段一律按字面值看待。
VALUE_WRAPPER_KEY = "__value__"

#: 连线的形状：``[上游节点 id, 输出序号]``。
_LINK_LENGTH = 2


def has_any_node(document: Mapping[str, Any]) -> bool:
    """这份对象里是否至少有一个 ComfyUI 节点。

    节点的标志是 ``class_type``——API 格式里每个值都是节点，端点定义的顶层节（``meta`` /
    ``submit`` / ``poll`` …）一个也不是。只要一个即算：节点自身写坏了该由 workflow 的
    schema 逐个指出来，而不是让整份载荷改走另一条路。
    """
    return any(isinstance(node, Mapping) and "class_type" in node for node in document.values())


def node_inputs(node: Mapping[str, Any]) -> Mapping[str, Any]:
    """取一个节点的 ``inputs``。节点可以没有可参数化字段，此时是空表。"""
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, Mapping) else {}


def is_link(raw: object) -> bool:
    """该字段的值是否是一条连线。

    先看包装再判形状：``{"__value__": ["a", "b"]}`` 是作者刻意声明的字面数组，不是连线。
    """
    if isinstance(raw, Mapping) and set(raw) == {VALUE_WRAPPER_KEY}:
        return False
    return isinstance(raw, list) and len(raw) == _LINK_LENGTH
