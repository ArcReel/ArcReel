from decimal import Decimal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from arcreel_catalog_core import Meter, PriceRow, PriceTable
from lib.billing.metering import Priced, Unpriced, UnpricedReason, Usage, price_requests, token_usage

pytestmark = pytest.mark.invariant

_METERS = ("uncached_input_tokens", "cache_read_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens")
type Split = tuple[int, int, int, int]
_COUNT = st.integers(0, 8)


def _reference(
    requests: list[Split], *, threshold: int, base_cents: int, free: int, quarters: int, price_reads: bool
) -> Decimal | None:
    multiplier = Decimal(quarters) / 4
    total = Decimal(0)
    for split in requests:
        if quarters and not price_reads and split[1]:
            return None
        tier_premium = 7 if sum(split) > threshold else 0
        for index, quantity in enumerate(split):
            cents_per_million = base_cents + 3 * index + tier_premium
            unit_rate = Decimal(cents_per_million) / 100 / 1_000_000
            total += max(0, quantity - free) * unit_rate * multiplier
    return total


def _request(split: Split) -> Usage:
    quantities = token_usage(
        input_tokens=sum(split),
        output_tokens=None,
        cache_read_tokens=split[1],
        cache_write_5m_tokens=split[2],
        cache_write_1h_tokens=split[3],
    )
    assert tuple(quantities.get(meter, 0) for meter in _METERS) == split
    return Usage(quantities=quantities)


@given(
    requests=st.lists(st.tuples(_COUNT, _COUNT, _COUNT, _COUNT), min_size=1, max_size=4),
    boundary_offset=st.integers(-1, 1),
    base_cents=st.integers(0, 100),
    free=st.integers(0, 5),
    quarters=st.integers(0, 8),
    price_reads=st.booleans(),
    currency=st.sampled_from(("USD", "CNY")),
)
@example(
    requests=[(0, 1, 0, 0)], boundary_offset=0, base_cents=0, free=0, quarters=1, price_reads=False, currency="USD"
)
@example(
    requests=[(0, 0, 0, 1)], boundary_offset=0, base_cents=0, free=0, quarters=1, price_reads=False, currency="USD"
)
def test_request_prices_match_decimal_invoice(
    requests: list[Split],
    boundary_offset: int,
    base_cents: int,
    free: int,
    quarters: int,
    price_reads: bool,
    currency: str,
) -> None:
    threshold = max(1, sum(requests[0]) + boundary_offset)
    meters = tuple(
        Meter(
            meter=name,
            dims=("context_tier",),
            free_allowance=free,
            rows=tuple(
                PriceRow(when={"context_tier": bound}, price=(base_cents + index * 3 + premium) / 100)
                for bound, premium in ((0, 0), (threshold, 7))
            ),
        )
        for index, name in enumerate(_METERS)
        if price_reads or name != "cache_read_tokens"
    )
    table = PriceTable(currency=currency, meters=meters)
    for batch in [[request] for request in requests] + [requests]:
        expected = _reference(
            batch, threshold=threshold, base_cents=base_cents, free=free, quarters=quarters, price_reads=price_reads
        )
        actual = price_requests(table, [_request(split) for split in batch], multiplier=quarters / 4)
        if expected is None:
            assert actual == Unpriced(UnpricedReason.MISSING_RATE)
        else:
            assert isinstance(actual, Priced)
            assert actual.currency == currency
            assert actual.amount == pytest.approx(float(expected), rel=1e-14, abs=0)
