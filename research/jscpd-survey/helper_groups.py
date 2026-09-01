#!/usr/bin/env python3
"""把 cross_file_helpers.py 的输出聚成连通族。

用法: python3 research/jscpd-survey/helper_groups.py raw/cross-file-helpers-k50.txt
"""

from __future__ import annotations

import collections
import re
import sys

txt = open(sys.argv[1], encoding="utf-8").read()
pairs = re.findall(r"^\s+\d+T\s+\d+L\s+(tests/\S+)\n\s+(tests/\S+)$", txt, re.M)
parent: dict[str, str] = {}


def find(x: str) -> str:
    parent.setdefault(x, x)
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def union(a: str, b: str) -> None:
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb


for a, b in pairs:
    union(a, b)

g: dict[str, list[str]] = collections.defaultdict(list)
for key in list(parent):
    g[find(key)].append(key)
groups = sorted(g.values(), key=lambda v: -len(v))
files = {m.split("::")[0] for m in parent}
print(
    f"跨文件重复辅助/替身：{len(pairs)} 对 → {len(groups)} 个连通族，涉及 {len(parent)} 个符号、{len(files)} 个测试文件"
)
print("\n全部族：")
for v in groups:
    print(f"  [{len(v)}]")
    for m in sorted(v):
        print(f"      {m}")
