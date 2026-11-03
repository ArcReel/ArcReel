"""视频调用通道按名字族认定模型的通道能力（docs/adr/0104）。

通道事实（输入模式、参考图与参考音频上限、提示词长度上限、音轨形态）由各调用通道按模型名声明。拿来比对的
名字取模型所对应条目的官方模型名与模型别名，对应不上条目时取 API 模型名（docs/adr/0105）。条目由调用方给出时
（自定义供应商与内置供应商下用户添加的模型可以绑定条目）直接用它，否则按 API 模型名现查。各通道按自己的
名字族规则逐个试这些名字，第一个认得的名字决定能力；一个都认不得时统一回落为
:data:`UNRECOGNIZED_MODEL_CAPABILITIES`：只支持文生视频与首帧，不开尾帧，参考图与参考音频上限为 0。

内置供应商清单里的 API 模型名就是所引条目的官方模型名或模型别名，按名字现查得到的正是清单所引的条目；
自定义供应商的模型按同一规则现查。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING, Self

from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities
from lib.catalog import query as catalog

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry

#: 调用通道认不得的模型统一取这组输入模式与上限。音轨形态、提示词长度上限这类与输入模式无关的通道事实
#: 不在统一之列：调用通道对认不得的模型照样按自己的请求构造声明（如不下发音轨开关时声明恒无声），
#: 用 ``dataclasses.replace`` 在这份基础上改。
UNRECOGNIZED_MODEL_CAPABILITIES = VideoCapabilities(
    text_to_video=True,
    first_frame=True,
    last_frame=False,
    max_reference_images=0,
    reference_audio_mode=ReferenceAudioMode.NONE,
    max_reference_audio_count=0,
)


def capability_names(model: str, entry: CatalogEntry | None = None) -> tuple[str, ...]:
    """按名字族认定通道能力时拿来比对的名字：对应条目的官方模型名与模型别名，对应不上条目时为 API 模型名。

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


def capabilities_by_names(
    model: str,
    match: Callable[[str], VideoCapabilities | None],
    *,
    fallback: VideoCapabilities = UNRECOGNIZED_MODEL_CAPABILITIES,
    entry: CatalogEntry | None = None,
) -> VideoCapabilities:
    """调用通道对模型声明的视频能力：按名字族认得时取 ``match`` 的结果，认不得时取 ``fallback``。

    ``fallback`` 只在 :data:`UNRECOGNIZED_MODEL_CAPABILITIES` 的基础上改输入模式以外的通道事实。
    """
    found = match_by_names(model, match, entry=entry)
    return found if found is not None else fallback


class EntryBoundCapabilities:
    """调用通道实例按模型所对应的条目认定通道能力的公共部分（docs/adr/0104、0105）。

    装配方经 :meth:`with_catalog_entry` 交给实例这个模型对应的条目（条目绑定优先，否则按名字现查）；没有交时
    ``_catalog_entry`` 为 ``None``，按 API 模型名现查。条目只决定按哪个模型认定能力，请求里仍发 API 模型名。
    """

    _catalog_entry: CatalogEntry | None = None

    def with_catalog_entry(self, entry: CatalogEntry | None) -> Self:
        """返回按 ``entry`` 认定通道能力的副本；客户端、凭证与模型等其余状态与本实例共用，不就地改写。"""
        clone = copy.copy(self)
        clone._catalog_entry = entry
        clone._on_catalog_entry_changed()
        return clone

    def _on_catalog_entry_changed(self) -> None:
        """构造时按模型名算好的派生状态在这里按新条目重算；没有这类状态时不用覆盖。"""
