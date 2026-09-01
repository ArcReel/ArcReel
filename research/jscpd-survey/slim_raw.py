#!/usr/bin/env python3
"""瘦身原始数据：从 jscpd 报告里去掉 `fragment` 正文，从 mapped-*.json 里只留 families。

行号 / token 数 / 统计全部保留，本目录所有分析脚本（除 sample_partial.py 需要 fragment，
其输出已存为 raw/sample-partial-k50.txt）都能照常复跑。

用法: python3 research/jscpd-survey/slim_raw.py
"""

from __future__ import annotations

import json
from pathlib import Path

raw = Path("research/jscpd-survey/raw")

for report in sorted(raw.glob("*/jscpd-report.json")):
    data = json.load(open(report, encoding="utf-8"))
    for d in data["duplicates"]:
        d.pop("fragment", None)
    before = report.stat().st_size
    with open(report, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{report}: {before // 1024}K -> {report.stat().st_size // 1024}K")

for mapped in sorted(raw.glob("mapped-*.json")):
    data = json.load(open(mapped, encoding="utf-8"))
    before = mapped.stat().st_size
    with open(mapped, "w", encoding="utf-8") as fh:
        json.dump({"families": data["families"]}, fh, ensure_ascii=False, indent=1)
    print(f"{mapped}: {before // 1024}K -> {mapped.stat().st_size // 1024}K")
