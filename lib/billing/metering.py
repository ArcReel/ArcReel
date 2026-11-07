"""Price-table calculator and usage contract (docs/adr/0100).

Channels report :class:`Usage`: nonoverlapping measurement quantities and selection dimensions. Uncached input + cache reads + two cache-write tiers
sum to total input; channels normalize vendor semantics before reporting. Characters are exceptional: channels report raw text only;
calculators count according to entry price-table CJK-double properties.

:func:`price_usage` sums measurements: cost = multiplier × Σ max(usage − free allowance, 0) × unit price(dimensions), without pricing-shape,
media, or provider dispatch. Missing rates return :class:`Unpriced` with reasons, never other models/tiers.

For reported usage absent from tables, distinguish ``arcreel_catalog_core.MeterKind``: token measurements split one total,
so missing prices remain unpriced rather than zero; count measurements (seconds/images/calls) are independent billing axes,
so missing table items mean vendors do not charge that axis. Channels therefore report all actual count usage without knowing billing items.

Calls may contain several provider requests, including billed but discarded fallback attempts. :func:`price_requests` assigns context tiers
and prices each separately using its own input totals, then sums costs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from arcreel_catalog_core import CJK_DOUBLE_METER, METERS, Meter, PriceTable

#: Input measurements contributing context totals: per-request input includes cache reads/writes.
_INPUT_TOKEN_METERS = frozenset(
    {
        "uncached_input_tokens",
        "cache_read_tokens",
        "cache_write_5m_tokens",
        "cache_write_1h_tokens",
        "audio_input_tokens",
        "uncached_image_input_tokens",
        "image_cache_read_tokens",
    }
)

_CONTEXT_TIER = "context_tier"

# CJK double-counting covers Unified Ideographs including extensions and Compatibility Ideographs.
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]")


class UnpricedReason(StrEnum):
    """Unpriced reasons stored unchanged in ``api_calls.unpriced_reason``."""

    #: Provider models without entry matches.
    NO_ENTRY = "no_entry"
    #: Entries without price tables.
    NO_PRICE_TABLE = "no_price_table"
    #: Missing rates: absent request-dimension combinations without defaults, reported token measurements absent from tables, or settlement
    #: without any table-measurement usage.
    MISSING_RATE = "missing_rate"
    #: Tables depend on unknown currencies/measurements/dimensions.
    UNRECOGNIZED_TERMS = "unrecognized_terms"
    #: Presubmission estimates only: table measurement quantities cannot be derived from request facts or estimators.
    NO_USAGE_ESTIMATE = "no_usage_estimate"


@dataclass(frozen=True)
class Usage:
    """Reported usage for one provider call.

    ``quantities`` contains nonoverlapping measurements in their units (tokens/images/seconds);
    ``dimensions`` contains actually sent request-field values, omitting unsent dimensions; ``text`` is raw sent text
    for character pricing. Channels do not report context tiers; calculators derive them from total input tokens.
    """

    quantities: Mapping[str, float] = field(default_factory=dict)
    dimensions: Mapping[str, object] = field(default_factory=dict)
    text: str | None = None


def token_usage(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None = None,
    cache_write_5m_tokens: int | None = None,
    cache_write_1h_tokens: int | None = None,
    output_meter: str = "output_tokens",
) -> dict[str, float]:
    """Split total input and three cache portions into nonoverlapping token measurements.

    ``input_tokens`` includes cache reads/writes; uncached = total − reads − both writes. Omit unreported measurements.
    """
    caches = {
        "cache_read_tokens": cache_read_tokens or 0,
        "cache_write_5m_tokens": cache_write_5m_tokens or 0,
        "cache_write_1h_tokens": cache_write_1h_tokens or 0,
    }
    quantities: dict[str, float] = {meter: value for meter, value in caches.items() if value}
    if input_tokens is not None:
        quantities["uncached_input_tokens"] = max(input_tokens - sum(caches.values()), 0)
    if output_tokens is not None:
        quantities[output_meter] = output_tokens
    return quantities


def count_characters(text: str, *, cjk_double: bool) -> int:
    """Character quantities: ``cjk_double`` counts each CJK ideograph as 2, all others as 1."""
    if not cjk_double:
        return len(text)
    return len(text) + len(_HAN.findall(text))


@dataclass(frozen=True)
class Priced:
    amount: float
    currency: str


@dataclass(frozen=True)
class Unpriced:
    reason: UnpricedReason


Cost = Priced | Unpriced


def _quantity(meter: Meter, usage: Usage) -> float | None:
    """Measurement quantities; ``None`` when unreported."""
    if meter.meter == CJK_DOUBLE_METER and usage.text is not None:
        return count_characters(usage.text, cjk_double=meter.cjk_double)
    return usage.quantities.get(meter.meter)


def _context_tier(table: PriceTable, usage: Usage) -> int | None:
    """Whole-request context tiers: table values are exclusive lower bounds; select highest below total, ``None`` if untiered."""
    bounds = sorted(
        {bound for meter in table.meters for row in meter.rows if isinstance(bound := row.when.get(_CONTEXT_TIER), int)}
    )
    if not bounds:
        return None
    total = sum(quantity for meter, quantity in usage.quantities.items() if meter in _INPUT_TOKEN_METERS)
    reached = [bound for bound in bounds if total > bound]
    return reached[-1] if reached else bounds[0]


def _same(expected: object, actual: object) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.casefold() == actual.casefold()
    return type(expected) is type(actual) and expected == actual


def _rate(meter: Meter, dimensions: Mapping[str, object]) -> float | None:
    """Rates by request dimensions, using measurement defaults for missing dimensions; ``None`` without matching rows."""
    values = {dim: dimensions.get(dim, meter.defaults.get(dim)) for dim in meter.dims}
    for row in meter.rows:
        if all(values.get(dim) is not None and _same(expected, values[dim]) for dim, expected in row.when.items()):
            return row.price
    return None


def _unpriced_meter(table: PriceTable, usage: Usage) -> bool:
    """Whether reported token measurements lack table entries: missing rates cannot mean zero cost.

    Count measurements absent from tables are uncharged, not missing-rate failures. Unknown measurements behave as tokens.
    """
    priced = {meter.meter for meter in table.meters}
    return any(
        quantity > 0 and meter not in priced and _is_token_meter(meter) for meter, quantity in usage.quantities.items()
    )


def _is_token_meter(meter: str) -> bool:
    spec = METERS.get(meter)
    return spec is None or spec.kind == "token"


def price_usage(
    table: PriceTable | None,
    usage: Usage,
    *,
    multiplier: float,
    entry_found: bool = True,
    estimate: bool = False,
) -> Cost:
    """Calculate one provider request's reference cost, shared by settlement/estimates.

    ``multiplier`` is provider × provider-model factors; zero directly records zero without lookup. False ``entry_found`` means
    unmatched models. Zero/unreported table measurements skip lookup: absent settlement reports mean absent usage,
    but if none of a table's measurements are available, cost is unknown/unpriced, never silently zero. True ``estimate``
    uses request facts/estimators, requiring every item or marking unpriced. Positive reported token measurements missing
    from tables are unpriced (:attr:`UnpricedReason.MISSING_RATE`); missing count items are uncharged.
    """
    if multiplier == 0:
        return Priced(0.0, table.currency if table is not None and table.is_priceable else "USD")
    if not entry_found:
        return Unpriced(UnpricedReason.NO_ENTRY)
    if table is None:
        return Unpriced(UnpricedReason.NO_PRICE_TABLE)
    if not table.is_priceable:
        return Unpriced(UnpricedReason.UNRECOGNIZED_TERMS)
    if _unpriced_meter(table, usage):
        return Unpriced(UnpricedReason.MISSING_RATE)
    if not estimate and table.meters and all(_quantity(meter, usage) is None for meter in table.meters):
        return Unpriced(UnpricedReason.MISSING_RATE)

    dimensions = dict(usage.dimensions)
    tier = _context_tier(table, usage)
    if tier is not None:
        dimensions[_CONTEXT_TIER] = tier

    total = 0.0
    for meter in table.meters:
        quantity = _quantity(meter, usage)
        if quantity is None and estimate:
            return Unpriced(UnpricedReason.NO_USAGE_ESTIMATE)
        billable = (quantity or 0) - (meter.free_allowance or 0)
        if billable <= 0:
            continue
        rate = _rate(meter, dimensions)
        if rate is None:
            return Unpriced(UnpricedReason.MISSING_RATE)
        total += billable / METERS[meter.meter].quantity_per_price * rate
    return Priced(total * multiplier, table.currency)


def price_requests(
    table: PriceTable | None,
    requests: Sequence[Usage],
    *,
    multiplier: float,
    entry_found: bool = True,
    estimate: bool = False,
) -> Cost:
    """Sum provider-request costs per call, individually context-tiered/priced; any unpriced request makes the whole call unpriced.

    Parameters match :func:`price_usage`.
    """
    costs = [
        price_usage(table, usage, multiplier=multiplier, entry_found=entry_found, estimate=estimate)
        for usage in requests
    ] or [price_usage(table, Usage(), multiplier=multiplier, entry_found=entry_found, estimate=estimate)]
    for cost in costs:
        if isinstance(cost, Unpriced):
            return cost
    priced = [cost for cost in costs if isinstance(cost, Priced)]
    return Priced(sum(cost.amount for cost in priced), priced[0].currency)
