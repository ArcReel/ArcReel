"""价目表计算器按计量项类别处理价目表之外的用量（docs/adr/0100）。"""

from __future__ import annotations

import pytest

from arcreel_catalog_core import METERS, Meter, PriceRow, PriceTable
from lib.billing.metering import Priced, Unpriced, UnpricedReason, Usage, price_requests, price_usage


def _table(*meters: Meter) -> PriceTable:
    return PriceTable(currency="USD", meters=meters)


def _flat(meter: str, price: float) -> Meter:
    return Meter(meter=meter, dims=(), rows=(PriceRow(when={}, price=price),))


_PER_SECOND = _table(_flat("output_seconds", 0.5))


def test_meter_kinds_are_declared_in_the_vocabulary() -> None:
    kinds = {name: spec.kind for name, spec in METERS.items()}
    assert {kinds[name] for name in ("uncached_input_tokens", "output_tokens", "cache_read_tokens")} == {"token"}
    # 视频 token 由秒数换算而来，但按 Seedance 的计价轴算作计数类；字符数同属计数类
    assert {
        kinds[name]
        for name in (
            "output_seconds",
            "video_tokens",
            "input_video_seconds",
            "input_images",
            "requests",
            "output_images",
            "characters",
        )
    } == {"count"}


def test_count_meter_missing_from_table_is_not_charged() -> None:
    usage = Usage(quantities={"output_seconds": 8, "requests": 1, "input_images": 3, "input_video_seconds": 0})

    assert price_usage(_PER_SECOND, usage, multiplier=1.0) == Priced(4.0, "USD")


def test_token_meter_missing_from_table_is_unpriced() -> None:
    table = _table(_flat("uncached_input_tokens", 1.0), _flat("output_tokens", 2.0))
    usage = Usage(quantities={"uncached_input_tokens": 1000, "output_tokens": 1000, "cache_read_tokens": 500})

    assert price_usage(table, usage, multiplier=1.0) == Unpriced(UnpricedReason.MISSING_RATE)


def test_zero_token_usage_missing_from_table_is_not_unpriced() -> None:
    table = _table(_flat("uncached_input_tokens", 1.0))
    usage = Usage(quantities={"uncached_input_tokens": 1_000_000, "cache_read_tokens": 0})

    assert price_usage(table, usage, multiplier=1.0) == Priced(1.0, "USD")


@pytest.mark.parametrize(
    "usage",
    [Usage(), Usage(quantities={"requests": 1, "input_images": 2})],
    ids=["nothing-reported", "only-meters-outside-the-table"],
)
def test_settlement_with_no_usage_on_any_table_meter_is_unpriced(usage: Usage) -> None:
    assert price_usage(_PER_SECOND, usage, multiplier=1.0) == Unpriced(UnpricedReason.MISSING_RATE)
    assert price_requests(_PER_SECOND, (), multiplier=1.0) == Unpriced(UnpricedReason.MISSING_RATE)


def test_zero_reported_usage_is_priced_as_zero() -> None:
    """上报了 0 就是确实没有这项用量，不是落空。"""
    assert price_usage(_PER_SECOND, Usage(quantities={"output_seconds": 0}), multiplier=1.0) == Priced(0.0, "USD")


def test_estimate_without_a_quantity_for_a_table_meter_has_no_usage_estimate() -> None:
    table = _table(_flat("video_tokens", 46.0), _flat("output_seconds", 0.1))
    usage = Usage(quantities={"output_seconds": 5})

    assert price_usage(table, usage, multiplier=1.0, estimate=True) == Unpriced(UnpricedReason.NO_USAGE_ESTIMATE)


def test_zero_multiplier_records_zero_even_without_usage() -> None:
    assert price_usage(_PER_SECOND, Usage(), multiplier=0.0) == Priced(0.0, "USD")
