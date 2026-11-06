"""视频调用通道使用的条目绑定与名字匹配。

请求结构、计量和输入路径由所选端点决定；模型事实从装配时确定的条目读取。
DashScope 的提示词上限仍按条目官方名与别名识别；对应不上条目时取 API 模型名。
条目由调用方给出时直接用它，否则按 API 模型名现查（docs/adr/0105）。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Self

from lib.catalog import query as catalog

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry


def capability_names(model: str, entry: CatalogEntry | None = None) -> tuple[str, ...]:
    """按名字匹配提示词上限时拿来比对的名字：对应条目的官方模型名与模型别名，对应不上条目时为 API 模型名。

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
    """调用通道实例持有模型所对应条目的公共部分（docs/adr/0104、0105）。

    装配方经 :meth:`with_catalog_entry` 交给实例这个模型对应的条目（条目绑定优先，否则按名字现查）；没有交时
    ``_catalog_entry`` 为 ``None``，按 API 模型名现查。条目用于读取模型事实与匹配提示词上限，请求里仍发 API 模型名。
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

    结算计量与请求构造读取同一个条目的模型事实；提交前预估由调用方按同一规则取条目（内置取 ``provider_model_entry``，自定义取条目绑定）。
    """
    return getattr(backend, "catalog_entry", None)
