#!/usr/bin/env python3
"""跨文件 clone 里、两端宿主都不是 test 函数的对 —— 即重复的局部替身 / 辅助函数。

CONTRIBUTING「共享设施」要求 fakes / factories 被 ≥2 个文件用时上提 tests/fakes.py。
用法: python3 research/jscpd-survey/cross_file_helpers.py <k>
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from map_clones import build_index, host_of

k = sys.argv[1]
dups = json.load(open(f"research/jscpd-survey/raw/k{k}/jscpd-report.json", encoding="utf-8"))["duplicates"]
idx = build_index()

rows = []
for d in dups:
    f1, f2 = d["firstFile"], d["secondFile"]
    if f1["name"] == f2["name"]:
        continue
    h1, _ = host_of(idx.get(f1["name"], []), f1["start"], f1["end"])
    h2, _ = host_of(idx.get(f2["name"], []), f2["start"], f2["end"])
    n1 = h1.name if h1 else None
    n2 = h2.name if h2 else None
    if (n1 and n1.startswith("test")) or (n2 and n2.startswith("test")):
        continue
    rows.append(
        (d["tokens"], d["lines"], f1["name"], h1.qual if h1 else "<模块级>", f2["name"], h2.qual if h2 else "<模块级>")
    )

print(f"k={k}：跨文件且两端宿主都不是 test 函数的 clone 对 = {len(rows)}")
names: collections.Counter[str] = collections.Counter()
for t, ln, fa, qa, fb, qb in rows:
    names[qa.split("::")[-1]] += 1
    names[qb.split("::")[-1]] += 1
print("\n重复出现最多的辅助/替身符号名：")
for name, c in names.most_common(20):
    print(f"  {c:>3}  {name}")
print("\n全部对（按 tokens 降序）：")
for t, ln, fa, qa, fb, qb in sorted(rows, reverse=True):
    print(f"  {t:>4}T {ln:>3}L  tests/{fa}{qa}\n              tests/{fb}{qb}")
