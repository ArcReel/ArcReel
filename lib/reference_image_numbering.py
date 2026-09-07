"""参考图的「图N」编号：类型声明行与正文 ``@[名称]`` 的替换。

分镜图与宫格图的参考图对图像模型只是一个图数组，模型能理解的指认方式是「图N」。编排层在
最终参考图列表确定后，由本模块机械渲染两样东西：一行不带资产名的类型声明（YAML 键
``Reference_Images``），以及正文里 ``@[名称]`` 到对应「图N」的替换。编号即列表位置（从 1
起）；压缩层与执行时的临时复制都不改数量与顺序，故只在此处编号一次，对全部图像后端同一口径。

输入是与实际随请求发出的参考图严格等长同序的 :class:`ReferenceImageSlot` 序列
（:class:`lib.visual_artifact_provenance.VisualReference` 满足该协议）。参考图列表仍由
条目的引用字段决定，mention 只指认、不派生参考图。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from lib.asset_types import asset_name_comparison_key
from lib.reference_video.text_parser import render_mentions

#: 类型声明行的 YAML 键，插在 ``Style`` 与 ``Scene`` 之间。
REFERENCE_IMAGES_KEY = "Reference_Images"

#: 上一分镜图在参考图证据里的角色名。
PREVIOUS_STORYBOARD_ROLE = "previous_storyboard"

#: 上一分镜图的约束句，随类型声明行发给模型。
PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION = "只参考构图与色调"

#: 按资产类型的类型说明；商品条目并入保真要求。
_TYPE_DESCRIPTIONS: dict[str, str] = {
    "product": "商品参考图，画面中的商品须与之完全一致",
    "character": "角色参考图",
    "scene": "场景参考图",
    "prop": "道具参考图",
}
_PREVIOUS_STORYBOARD_DESCRIPTION = f"上一分镜图，{PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION}"
_EXTRA_DESCRIPTION = "补充参考图"


class ReferenceImageSlot(Protocol):
    """一张实际随请求发出的参考图：角色名与逻辑身份，不含路径。"""

    @property
    def role(self) -> str: ...

    @property
    def logical_type(self) -> str | None: ...

    @property
    def logical_id(self) -> str | None: ...


def _image_label(position: int) -> str:
    return f"图{position + 1}"


def _describe(slot: ReferenceImageSlot) -> str:
    if slot.role == PREVIOUS_STORYBOARD_ROLE:
        return _PREVIOUS_STORYBOARD_DESCRIPTION
    if slot.logical_type in _TYPE_DESCRIPTIONS:
        return _TYPE_DESCRIPTIONS[slot.logical_type]
    return _EXTRA_DESCRIPTION


def reference_images_declaration(references: Sequence[ReferenceImageSlot]) -> str:
    """``Reference_Images`` 行的值：按类型分组的编号声明；无参考图时为空串。

    只用全角标点、不含 ASCII 空格：PyYAML 会在超过行宽的空格处折行，声明行不能给它折行点。
    """
    groups: dict[str, list[str]] = {}
    for position, slot in enumerate(references):
        groups.setdefault(_describe(slot), []).append(_image_label(position))
    if not groups:
        return ""
    return "；".join(f"{'、'.join(labels)}为{description}" for description, labels in groups.items()) + "。"


def mention_replacements(references: Sequence[ReferenceImageSlot]) -> dict[str, str]:
    """引用名（比对坐标系）→ 该资产首张参考图的「图N」。上一分镜图与补充参考图没有可指认的名字。"""
    replacements: dict[str, str] = {}
    for position, slot in enumerate(references):
        if slot.role == PREVIOUS_STORYBOARD_ROLE or slot.logical_id is None:
            continue
        replacements.setdefault(asset_name_comparison_key(slot.logical_id), _image_label(position))
    return replacements


def render_reference_mentions(text: str, references: Sequence[ReferenceImageSlot]) -> str:
    """把正文的 ``@[名称]`` 换成对应「图N」；对不上参考图的 mention 渲染为裸名。"""
    replacements = mention_replacements(references)
    return render_mentions(text, lambda name: replacements.get(name, name))


__all__ = [
    "PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION",
    "PREVIOUS_STORYBOARD_ROLE",
    "REFERENCE_IMAGES_KEY",
    "ReferenceImageSlot",
    "mention_replacements",
    "reference_images_declaration",
    "render_reference_mentions",
]
