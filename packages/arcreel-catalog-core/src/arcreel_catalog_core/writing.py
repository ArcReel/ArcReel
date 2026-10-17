"""把读入后的条目写回目录数据的 JSON 形状，与 ``parse_entry`` 互逆。

用户修改存下的条目副本用它落库：副本与目录数据同一格式，读回时过同一个实例侧校验。
"""

from __future__ import annotations

from typing import assert_never

from .model import AudioFacts, CatalogEntry, ImageFacts, Meter, ModelFacts, PriceTable, TextFacts, VideoFacts


def _facts(facts: ModelFacts) -> dict[str, object]:
    match facts:
        case TextFacts():
            raw: dict[str, object] = {"image_input": facts.image_input, "structured_output": facts.structured_output}
            if facts.max_output_tokens is not None:
                raw["max_output_tokens"] = facts.max_output_tokens
            return raw
        case ImageFacts():
            return {
                "text_to_image": facts.text_to_image,
                "image_to_image": facts.image_to_image,
                "resolutions": list(facts.resolutions),
            }
        case VideoFacts():
            constraints = facts.duration_constraints
            return {
                "durations": list(facts.durations),
                "resolutions": list(facts.resolutions),
                "duration_constraints": {
                    "by_resolution": {res: list(allowed) for res, allowed in constraints.by_resolution.items()},
                    "with_reference_images": list(constraints.with_reference_images),
                },
            }
        case AudioFacts():
            return {}
        case _:
            assert_never(facts)


def _meter(meter: Meter) -> dict[str, object]:
    raw: dict[str, object] = {
        "meter": meter.meter,
        "dims": list(meter.dims),
        "rows": [{"when": dict(row.when), "price": row.price} for row in meter.rows],
    }
    if meter.defaults:
        raw["defaults"] = dict(meter.defaults)
    if meter.free_allowance is not None:
        raw["free_allowance"] = meter.free_allowance
    if meter.cjk_double:
        raw["cjk_double"] = True
    return raw


def _price(price: PriceTable) -> dict[str, object]:
    return {"currency": price.currency, "meters": [_meter(meter) for meter in price.meters]}


def dump_entry(entry: CatalogEntry) -> dict[str, object]:
    """条目的 JSON 形状（可直接 ``json.dumps``）。缺省值的可选字段省略。"""
    raw: dict[str, object] = {
        "maker": entry.maker,
        "name": entry.name,
        "media_type": entry.media_type,
        "display_name": entry.display_name,
    }
    if entry.aliases:
        raw["aliases"] = list(entry.aliases)
    if entry.discontinued:
        raw["discontinued"] = True
    if entry.context_length is not None:
        raw["context_length"] = entry.context_length
    raw["facts"] = _facts(entry.facts)
    if entry.price is not None:
        raw["price"] = _price(entry.price)
    return raw
