"""价目表计算器与用量契约（docs/adr/0100）。

调用通道按用量契约上报 :class:`Usage`：互不重叠的计量项用量与选择维度取值，未缓存输入、缓存读与两档缓存写之和
等于总输入；各家 usage 的语义差异由调用通道在上报前处理。字符数是例外：调用通道只上报原始文本，字符数由计算器
按条目价目表上的「汉字计 2」属性统计。

:func:`price_usage` 按价目表的计量项求和：费用 = 倍率 × Σ max(用量 − 免费额度, 0) × 单价(选择维度)，不按定价
形状、媒体类型或供应商分派。查不到单价时返回 :class:`Unpriced` 并带原因，不回落到别的模型或别的档；上报了用量、
价目表却没有这个计量项，同样算查不到单价，不按 0 计。

一次调用可能含多次供应商请求（降级链上被丢弃的请求也已计费）：:func:`price_requests` 让每次请求按自己的输入
总量落上下文档、单独计价，再把费用相加。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from arcreel_catalog_core import CJK_DOUBLE_METER, METERS, Meter, PriceTable

#: 计入上下文档落档总量的输入类计量项：单次请求的输入 token 总量含缓存读写。
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

# 「汉字计 2」中的汉字：CJK 统一表意文字（含扩展区）与兼容表意文字。
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]")


class UnpricedReason(StrEnum):
    """未定价的原因，原样写入 ``api_calls.unpriced_reason``。"""

    #: 供应商模型对应不上模型条目。
    NO_ENTRY = "no_entry"
    #: 条目没有价目表。
    NO_PRICE_TABLE = "no_price_table"
    #: 查不到单价：单价表缺少请求的维度组合且没有默认值，或上报了用量的计量项不在价目表里。
    MISSING_RATE = "missing_rate"
    #: 价目表依赖当前代码不认识的币种、计量项或选择维度。
    UNRECOGNIZED_TERMS = "unrecognized_terms"
    #: 只用于提交前预估：价目表里有计量项的用量既不能从请求事实得出，也没有估算函数。
    NO_USAGE_ESTIMATE = "no_usage_estimate"


@dataclass(frozen=True)
class Usage:
    """一次供应商调用上报的用量。

    ``quantities`` 按计量项给出用量（按计量项的计数单位，如 token 数、张数、秒数），各项互不重叠；
    ``dimensions`` 是选择维度的取值，取自真正发给供应商的请求字段，没发送的维度不写；``text`` 是按字符计价时
    发送的原始文本。上下文档不由调用通道上报，由计算器按输入 token 总量落档。
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
    """按总输入与三项缓存拆出互不重叠的 token 计量项。

    ``input_tokens`` 是总输入（含缓存读写），未缓存输入 = 总输入 − 缓存读 − 两档缓存写；没有上报的项不写。
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
    """字符数计量项的用量：``cjk_double`` 时每个汉字计 2 个字符，其余字符计 1 个。"""
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
    """计量项的用量；用量没有上报时为 ``None``。"""
    if meter.meter == CJK_DOUBLE_METER and usage.text is not None:
        return count_characters(usage.text, cjk_double=meter.cjk_double)
    return usage.quantities.get(meter.meter)


def _context_tier(table: PriceTable, usage: Usage) -> int | None:
    """整个请求落入的上下文档：价目表各取值为档的下界（不含），取总量大于它的最大一档；不分档时为 ``None``。"""
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
    """计量项在请求维度下的单价；请求缺少的维度取计量项的默认值，仍对不上任何一行时返回 ``None``。"""
    values = {dim: dimensions.get(dim, meter.defaults.get(dim)) for dim in meter.dims}
    for row in meter.rows:
        if all(values.get(dim) is not None and _same(expected, values[dim]) for dim, expected in row.when.items()):
            return row.price
    return None


def _unpriced_meter(table: PriceTable, usage: Usage) -> bool:
    """是否上报了价目表没有的计量项：这部分用量查不到单价，不能按 0 计。"""
    priced = {meter.meter for meter in table.meters}
    return any(quantity > 0 and meter not in priced for meter, quantity in usage.quantities.items())


def price_usage(
    table: PriceTable | None,
    usage: Usage,
    *,
    multiplier: float,
    entry_found: bool = True,
    estimate: bool = False,
) -> Cost:
    """按价目表计算一次供应商请求的参考费用；结算与提交前预估共用。

    ``multiplier`` 是供应商倍率与供应商模型倍率之积，为 0 时直接记 0、不查价；``entry_found`` 为 False 表示
    供应商模型对应不上模型条目。价目表里用量为 0 或没有上报的计量项不参与查价：结算时供应商没回报就是没有这项
    用量；``estimate`` 为真时用量来自请求事实与估算函数，缺一项就无从估算，记为未定价。上报的计量项用量大于 0、
    价目表却没有这个计量项时记为未定价（:attr:`UnpricedReason.MISSING_RATE`）。
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
    """一次调用所含各次供应商请求的费用之和：每次请求单独落上下文档、单独计价，任一次未定价则整笔未定价。

    参数含义同 :func:`price_usage`。
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
