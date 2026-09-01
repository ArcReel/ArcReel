#!/usr/bin/env python3
"""按文件汇总 jscpd 重复行，并列出最大的 clone 块。

用法: python3 research/jscpd-survey/top_files.py <k> [topN]
"""

from __future__ import annotations

import collections
import json
import sys

k = sys.argv[1]
top = int(sys.argv[2]) if len(sys.argv) > 2 else 15
d = json.load(open(f"research/jscpd-survey/raw/k{k}/jscpd-report.json", encoding="utf-8"))
dups = d["duplicates"]

# 每个文件被 clone 覆盖的行集合（去重，避免重叠片段重复计数）
covered: dict[str, set[int]] = collections.defaultdict(set)
pairs: collections.Counter[str] = collections.Counter()
for x in dups:
    for side in ("firstFile", "secondFile"):
        f = x[side]
        covered[f["name"]].update(range(f["start"], f["end"] + 1))
        pairs[f["name"]] += 1

print(f"=== k={k}：按「被 clone 覆盖的去重行数」排前 {top} 的文件 ===")
for name, lines in sorted(covered.items(), key=lambda kv: -len(kv[1]))[:top]:
    print(f"  {len(lines):>5} 行  {pairs[name]:>4} 对  tests/{name}")

print(f"\n=== k={k}：最大的 clone 块（按 tokens）前 {top} ===")
for x in sorted(dups, key=lambda y: -y["tokens"])[:top]:
    f1, f2 = x["firstFile"], x["secondFile"]
    tag = "同文件" if f1["name"] == f2["name"] else "跨文件"
    print(
        f"  {x['tokens']:>4}T {x['lines']:>3}L {tag}  {f1['name']}[{f1['start']}:{f1['end']}] <-> {f2['name']}[{f2['start']}:{f2['end']}]"
    )
