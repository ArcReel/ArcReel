"""供应商模型列表上的价格摘要：从价目表里挑出最能代表这个模型的一两个计费项，给出单价范围。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from arcreel_catalog_core import METERS, MediaType, PriceTable

#: 每种媒体类型按顺序挑选的计费项；价目表里有的取前两个，都没有时退到调用次数。
_SUMMARY_METERS: Final[Mapping[MediaType, tuple[str, ...]]] = {
    "text": ("uncached_input_tokens", "output_tokens"),
    "image": ("output_images", "image_output_tokens", "uncached_input_tokens", "output_tokens"),
    "video": ("output_seconds", "video_tokens"),
    "audio": ("characters", "audio_output_tokens", "uncached_input_tokens"),
}
_FALLBACK_METER: Final = "requests"
_MAX_ITEMS: Final = 2


@dataclass(frozen=True)
class PriceSummaryItem:
    """一个计费项的单价范围。``per`` 是单价对应的计数量（如每百万 token 为 1000000）。

    不同条件（分辨率、上下文档、服务档等）下的单价取最低与最高，两者相同时价格不分档。
    """

    meter: str
    min_price: float
    max_price: float
    per: int


@dataclass(frozen=True)
class PriceSummary:
    currency: str
    items: tuple[PriceSummaryItem, ...]


def summarize_price(price: PriceTable | None, media_type: MediaType) -> PriceSummary | None:
    """价目表的摘要；没有价目表、价目表依赖不认识的内容或挑不出计费项时为 ``None``（列表上显示未定价）。"""
    if price is None or not price.is_priceable:
        return None
    by_meter = {meter.meter: meter for meter in price.meters if meter.rows}
    keys = [key for key in _SUMMARY_METERS[media_type] if key in by_meter][:_MAX_ITEMS]
    if not keys and _FALLBACK_METER in by_meter:
        keys = [_FALLBACK_METER]
    if not keys:
        return None
    items = tuple(
        PriceSummaryItem(
            meter=key,
            min_price=min(row.price for row in by_meter[key].rows),
            max_price=max(row.price for row in by_meter[key].rows),
            per=METERS[key].quantity_per_price,
        )
        for key in keys
    )
    return PriceSummary(currency=price.currency, items=items)
