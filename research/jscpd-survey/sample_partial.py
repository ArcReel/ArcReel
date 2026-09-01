#!/usr/bin/env python3
"""从「非整体近似重复」的 clone 对里随机抽样，打印片段原文，用于判定重复段的性质。

用法: python3 research/jscpd-survey/sample_partial.py <k> <n> <seed>
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from map_clones import build_index, host_of

k, n, seed = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
dups = json.load(open(f"research/jscpd-survey/raw/k{k}/jscpd-report.json", encoding="utf-8"))["duplicates"]
idx = build_index()

partial = []
for d in dups:
    f1, f2 = d["firstFile"], d["secondFile"]
    h1, c1 = host_of(idx.get(f1["name"], []), f1["start"], f1["end"])
    h2, c2 = host_of(idx.get(f2["name"], []), f2["start"], f2["end"])
    if not (h1 and h2 and c1 >= 0.8 and c2 >= 0.8 and h1.qual != h2.qual):
        partial.append((d, h1, c1, h2, c2))

random.seed(seed)
print(f"非整体近似重复对 {len(partial)} / {len(dups)}，抽 {n}（seed={seed}）\n")
for d, h1, c1, h2, c2 in random.sample(partial, n):
    f1, f2 = d["firstFile"], d["secondFile"]
    n1 = h1.qual if h1 else "<模块级/类体>"
    n2 = h2.qual if h2 else "<模块级/类体>"
    print(f"--- {d['tokens']}T {d['lines']}L")
    print(f"    A tests/{f1['name']}[{f1['start']}:{f1['end']}] 宿主{n1} cover={c1:.2f}")
    print(f"    B tests/{f2['name']}[{f2['start']}:{f2['end']}] 宿主{n2} cover={c2:.2f}")
    for line in d.get("fragment", "<raw 已瘦身，fragment 正文见 raw/sample-partial-k50.txt>").splitlines()[:14]:
        print(f"      | {line}")
    print()
