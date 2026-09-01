#!/usr/bin/env python3
"""把 map_clones 产出的族逐个打印源码，供人工逐条判定。

用法: python3 research/jscpd-survey/dump_families.py <mapped.json> [起始序号] [条数]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS_ROOT = Path("tests")

data = json.load(open(sys.argv[1], encoding="utf-8"))
fams = data["families"]
start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
count = int(sys.argv[3]) if len(sys.argv) > 3 else len(fams)

# 建立 host -> 行区间 的索引（从 rows 反推不可靠，直接重新用 ast）
sys.path.insert(0, str(Path(__file__).parent))
from map_clones import build_index

idx = build_index()
span: dict[str, tuple[int, int]] = {}
for rel, fns in idx.items():
    for fn in fns:
        span[rel + fn.qual] = (fn.start, fn.end)

for i, fam in enumerate(fams):
    if i < start or i >= start + count:
        continue
    rel = fam[0].split("::")[0]
    src = (TESTS_ROOT / rel).read_text(encoding="utf-8").splitlines()
    print(f"\n########## 族 #{i}  [{len(fam)}]  tests/{rel}")
    for host in sorted(fam):
        s, e = span[host]
        print(f"--- {host.split('::', 1)[1]}  (L{s}-{e})")
        # 往上取到 def / 装饰器
        top = s - 1
        while top > 0 and (src[top - 1].lstrip().startswith(("@", "def ", "async def ")) or src[top - 1].strip() == ""):
            if src[top - 1].strip() == "":
                break
            top -= 1
        for ln in range(top, min(e, top + 60)):
            print(f"{ln + 1:>5}| {src[ln]}")
        if e - top > 60:
            print(f"      ... (共 {e - top} 行)")
