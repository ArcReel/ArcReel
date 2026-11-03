"""视频调用通道按名字族认定模型的请求形态（docs/adr/0104、0105）。

视频的输入能力（文生视频、首帧、尾帧、参考图与参考音频及其数量）不按名字认定：调用通道只声明它实现了什么、
一次能承载多少，模型接受什么记在模型条目上，两侧共同约束（:mod:`lib.backends.video_capabilities`）。

仍按名字族认定的只剩请求形态，各自等待移进调用端点：按代际区分的请求结构与计量（方舟 Seedance 各代、Agnes 2.5
与 v2.0 契约、Vidu 端点白名单与时长校正）、音轨形态、提示词长度上限、首帧比例约束，以及随请求档位或输入组合变化
的限制（可灵旧型号只在 pro 档接受尾帧、Grok 经典版拒收首帧与参考图并存）。拿来比对的名字取模型所对应条目的官方
模型名与模型别名，对应不上条目时取 API 模型名（docs/adr/0105）。条目由调用方给出时（自定义供应商与内置供应商下
用户添加的模型可以绑定条目）直接用它，否则按 API 模型名现查。

内置供应商清单里的 API 模型名就是所引条目的官方模型名或模型别名，按名字现查得到的正是清单所引的条目；
自定义供应商的模型按同一规则现查。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Self

from lib.catalog import query as catalog

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry


def capability_names(model: str, entry: CatalogEntry | None = None) -> tuple[str, ...]:
    """按名字族认定请求形态时拿来比对的名字：对应条目的官方模型名与模型别名，对应不上条目时为 API 模型名。

    ``entry`` 是调用方已确定的对应条目（条目绑定优先，见 ``docs/adr/0105``）；不给时按 API 模型名现查。
    """
    if entry is None:
        entry = catalog.find_entry(model, media_type="video")
    if entry is None:
        return (model,)
    return (entry.name, *entry.aliases)


def match_by_names[T](model: str, match: Callable[[str], T | None], *, entry: CatalogEntry | None = None) -> T | None:
    """依次用 :func:`capability_names` 的名字调 ``match``，返回第一个非 ``None`` 的结果；都认不得时为 ``None``。"""
    for name in capability_names(model, entry):
        found = match(name)
        if found is not None:
            return found
    return None


class EntryBoundCapabilities:
    """调用通道实例按模型所对应的条目认定请求形态的公共部分（docs/adr/0104、0105）。

    装配方经 :meth:`with_catalog_entry` 交给实例这个模型对应的条目（条目绑定优先，否则按名字现查）；没有交时
    ``_catalog_entry`` 为 ``None``，按 API 模型名现查。条目只决定按哪个模型认定请求形态，请求里仍发 API 模型名。
    """

    _catalog_entry: CatalogEntry | None = None

    @property
    def catalog_entry(self) -> CatalogEntry | None:
        """装配时交给实例的条目；结算计量与能力、请求构造按它认定同一个模型（见 :func:`catalog_entry_of`）。"""
        return self._catalog_entry

    def with_catalog_entry(self, entry: CatalogEntry | None) -> Self:
        """返回按 ``entry`` 认定请求形态的副本；客户端、凭证与模型等其余状态与本实例共用，不就地改写。"""
        clone = copy.copy(self)
        clone._catalog_entry = entry
        clone._on_catalog_entry_changed()
        return clone

    def _on_catalog_entry_changed(self) -> None:
        """构造时按模型名算好的派生状态在这里按新条目重算；没有这类状态时不用覆盖。"""


def catalog_entry_of(backend: object) -> CatalogEntry | None:
    """调用通道实例认定模型所按的条目：装配时交给它的条目；不按条目认定的通道（声明式、ComfyUI）为 ``None``。

    结算计量按这个条目读名字族判定（如方舟的视频 token 与服务档、可灵的有声约束），与实例的能力和请求构造
    同一个来源；提交前预估由调用方按同一规则取条目（内置取 ``provider_model_entry``，自定义取条目绑定）。
    """
    return getattr(backend, "catalog_entry", None)
