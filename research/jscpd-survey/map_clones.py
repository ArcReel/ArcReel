#!/usr/bin/env python3
"""把 jscpd 的 clone 片段映射回测试函数，产出可判定的候选清单。

用法: python3 research/jscpd-survey/map_clones.py <report.json> [--json 输出.json]

术语：
  片段(fragment)  jscpd 报的一段行区间
  宿主(host)      包含该片段的最内层 test 函数（没有则为 None，例如 module 级辅助/fixture）
  覆盖率(cover)   片段覆盖宿主函数体行数的比例
  整体近似重复对  两端片段各自覆盖宿主 >= COVER 且宿主互不相同
"""

from __future__ import annotations

import ast
import collections
import json
import sys
from pathlib import Path

COVER = float(sys.argv[sys.argv.index("--cover") + 1]) if "--cover" in sys.argv else 0.8
TESTS_ROOT = Path("tests")


class Fn:
    __slots__ = ("body_lines", "cls", "end", "name", "param", "qual", "start")

    def __init__(self, qual: str, name: str, cls: str | None, start: int, end: int, param: bool, body_lines: int):
        self.qual = qual
        self.name = name
        self.cls = cls
        self.start = start
        self.end = end
        self.param = param
        self.body_lines = body_lines


def index_file(path: Path) -> list[Fn]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    out: list[Fn] = []

    def walk(node: ast.AST, prefix: str, cls: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}::{child.name}", child.name)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                param = any("parametrize" in ast.unparse(d) for d in child.decorator_list)
                body = child.body
                start = body[0].lineno if body else child.lineno
                end = child.end_lineno or child.lineno
                out.append(Fn(f"{prefix}::{child.name}", child.name, cls, start, end, param, max(1, end - start + 1)))

    walk(tree, "", None)
    return out


def build_index() -> dict[str, list[Fn]]:
    idx: dict[str, list[Fn]] = {}
    for p in sorted(TESTS_ROOT.rglob("*.py")):
        rel = str(p.relative_to(TESTS_ROOT))
        idx[rel] = index_file(p)
    return idx


def host_of(fns: list[Fn], start: int, end: int) -> tuple[Fn | None, float]:
    """返回包含片段的最内层函数与片段对该函数体的覆盖率。"""
    best: Fn | None = None
    for fn in fns:
        if fn.start <= end and start <= fn.end:  # 有交集
            overlap = min(end, fn.end) - max(start, fn.start) + 1
            if overlap <= 0:
                continue
            # 取行数最小（最内层）且片段主体落在其中的
            if best is None or fn.body_lines < best.body_lines:
                best = fn
    if best is None:
        return None, 0.0
    overlap = min(end, best.end) - max(start, best.start) + 1
    return best, overlap / best.body_lines


def main() -> None:
    report = json.load(open(sys.argv[1], encoding="utf-8"))
    idx = build_index()
    rows = []
    for d in report["duplicates"]:
        f1, f2 = d["firstFile"], d["secondFile"]
        h1, c1 = host_of(idx.get(f1["name"], []), f1["start"], f1["end"])
        h2, c2 = host_of(idx.get(f2["name"], []), f2["start"], f2["end"])
        rows.append(
            {
                "tokens": d["tokens"],
                "lines": d["lines"],
                "same_file": f1["name"] == f2["name"],
                "a_file": f1["name"],
                "a_span": [f1["start"], f1["end"]],
                "a_host": (f1["name"] + h1.qual) if h1 else None,
                "a_is_test": bool(h1 and h1.name.startswith("test")),
                "a_cover": round(c1, 3),
                "a_param": bool(h1 and h1.param),
                "b_file": f2["name"],
                "b_span": [f2["start"], f2["end"]],
                "b_host": (f2["name"] + h2.qual) if h2 else None,
                "b_is_test": bool(h2 and h2.name.startswith("test")),
                "b_cover": round(c2, 3),
                "b_param": bool(h2 and h2.param),
            }
        )

    n = len(rows)
    same = [r for r in rows if r["same_file"]]
    both_test = [r for r in rows if r["a_is_test"] and r["b_is_test"]]
    # 整体近似重复对：两端都是 test 函数、宿主不同、两端覆盖率都 >= COVER
    whole = [r for r in both_test if r["a_host"] != r["b_host"] and r["a_cover"] >= COVER and r["b_cover"] >= COVER]
    whole_same = [r for r in whole if r["same_file"]]

    files = {r["a_file"] for r in rows} | {r["b_file"] for r in rows}
    print(f"clone 对 {n}；同文件 {len(same)} ({len(same) / n:.1%})；涉及文件 {len(files)}")
    print(f"两端宿主都是 test 函数的对: {len(both_test)} ({len(both_test) / n:.1%})")
    print(f"  其中片段落在 fixture / 辅助函数 / 类体 / 模块级的对: {n - len(both_test)}")
    print(f"整体近似重复对（两端 cover>={COVER}、宿主不同）: {len(whole)}，其中同文件 {len(whole_same)}")

    # 族：对同文件整体近似重复对做并查集
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

    for r in whole_same:
        union(r["a_host"], r["b_host"])
    fam: dict[str, list[str]] = collections.defaultdict(list)
    for k in list(parent):
        fam[find(k)].append(k)
    fams = sorted(fam.values(), key=lambda v: -len(v))
    print(f"同文件整体近似重复族: {len(fams)}，涉及测试函数 {sum(len(v) for v in fams)}")
    print("\n最大的族：")
    for v in fams[:20]:
        f = v[0].split("::")[0]
        print(f"  [{len(v):>2}] {f}")
        for name in sorted(v)[:12]:
            print(f"        {name.split('::', 1)[1]}")
        if len(v) > 12:
            print(f"        ... 另 {len(v) - 12} 个")

    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(
                {"rows": rows, "families": [sorted(v) for v in fams]},
                fh,
                ensure_ascii=False,
                indent=1,
            )
        print(f"\n写出 {out}")


if __name__ == "__main__":
    main()
