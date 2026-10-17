#!/usr/bin/env python3
"""生成价格编辑器用的词表（前端 TS 文件）。

计量项与选择维度是由代码定义的封闭集合（``arcreel_catalog_core.vocabulary``）。前端的价格编辑器要知道每个计量项
适用哪些媒体类型、属于哪一类、单价对应多少计数量，以及每个选择维度适用哪些媒体类型，这些都从集合生成，
前端不再手写第二份。集合改动后重新运行本脚本；``tests/unit/scripts/test_generate_price_vocabulary.py`` 校验
生成的文件没有过期。

用法：
    uv run python scripts/generate_price_vocabulary.py
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from arcreel_catalog_core import (
    CJK_DOUBLE_METER,
    CURRENCIES,
    DIMENSION_MEDIA_TYPES,
    DIMENSIONS,
    MEDIA_TYPES,
    METERS,
    SERVICE_TIERS,
)

OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "frontend/src/components/pages/settings/price-editor/priceVocabulary.generated.ts"
)


def _list(values: Iterable[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def render() -> str:
    lines = [
        "// 由 scripts/generate_price_vocabulary.py 从 arcreel_catalog_core.vocabulary 生成，不要手改。",
        "// 计量项与选择维度的封闭集合以代码为准，集合改动后重新运行脚本。",
        "",
        f"export const PRICE_CURRENCIES = {_list(CURRENCIES)} as const;",
        f"export const PRICE_SERVICE_TIERS = {_list(SERVICE_TIERS)} as const;",
        f"export const CJK_DOUBLE_METER = {json.dumps(CJK_DOUBLE_METER)};",
        "",
        "/** 计量项：`kind` 为 token 的是同一份 token 总量拆出的各部分；`per` 是单价对应的计数量。 */",
        "export const METERS = {",
    ]
    for key, spec in METERS.items():
        media = _list(m for m in MEDIA_TYPES if m in spec.media_types)
        lines.append(
            f'  {key}: {{ kind: "{spec.kind}", per: {spec.quantity_per_price}, mediaTypes: {media} }},',
        )
    lines += ["} as const;", "", "/** 选择维度：`kind` 是取值形态。 */", "export const DIMENSIONS = {"]
    for key, kind in DIMENSIONS.items():
        media = _list(m for m in MEDIA_TYPES if m in DIMENSION_MEDIA_TYPES[key])
        lines.append(f'  {key}: {{ kind: "{kind}", mediaTypes: {media} }},')
    lines += ["} as const;", ""]
    return "\n".join(lines)


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render(), encoding="utf-8")


if __name__ == "__main__":
    main()
