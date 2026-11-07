"""Provider-model list price summaries: select one/two representative price-table measurements and show unit-price ranges."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from arcreel_catalog_core import METERS, MediaType, PriceTable

#: Ordered measurements per media: first two present in tables, otherwise API-call counts.
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
    """Unit-price range for one measurement. ``per`` is its quantity basis, such as 1000000 per million tokens.

    Take min/max across resolutions/context/service tiers; equal values mean untiered pricing.
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
    """Price-table summary; ``None`` means unpriced when absent, dependent on unknown content, or without selectable measurements."""
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
