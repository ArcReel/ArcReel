#!/usr/bin/env python3
"""检查 #2266 判据找到的三组严格重复，在各 min-tokens 档的 jscpd 报告里能否被覆盖。

判定用「宽松重叠」：只要某个 clone 对的一端片段与 A 的函数区间有交集、另一端与 B 有交集，
就算命中——比「片段的最内层宿主恰为 A/B」宽松得多，避免因 jscpd 贪心外扩越过函数边界而误判为漏检。

用法: python3 research/jscpd-survey/check_pairs.py 10 25 50 75 100 150 200
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

TESTS_ROOT = Path("tests")

# #2266 的三组存量（路径去掉 tests/ 前缀以对齐 jscpd 报告里的 name）
PAIRS = [
    (
        "integration/lib/config/test_config_resolver.py",
        "TestVideoGenerateAudio.test_global_true",
        "TestVideoGenerateAudio.test_project_none_skips_override",
    ),
    (
        "unit/lib/test_script_structure_validator.py",
        "TestValidScripts.test_valid_drama",
        "TestModeDetection.test_drama_detected_by_scenes",
    ),
    (
        "unit/server/test_auth.py",
        "TestCheckCredentials.test_check_credentials_valid",
        "TestPasswordHash.test_check_credentials_with_hash",
    ),
]


def span(rel: str, dotted: str) -> tuple[int, int]:
    """返回 dotted 路径（Class.func 或 func）的函数体行区间。"""
    tree = ast.parse((TESTS_ROOT / rel).read_text(encoding="utf-8"))
    parts = dotted.split(".")
    node: ast.AST = tree
    for part in parts:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and child.name == part:
                node = child
                break
        else:
            raise KeyError(f"{rel}::{dotted} 找不到 {part}")
    assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    return node.body[0].lineno, node.end_lineno or node.lineno


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def main() -> None:
    spans = [(f, span(f, a), span(f, b), a, b) for f, a, b in PAIRS]
    for _f, sa, sb, a, b in spans:
        print(f"  {a} {sa}  ≡  {b} {sb}")
    print()
    for k in sys.argv[1:]:
        rp = Path(f"research/jscpd-survey/raw/k{k}/jscpd-report.json")
        if not rp.exists():
            print(f"k={k}: 无报告")
            continue
        dups = json.load(open(rp, encoding="utf-8"))["duplicates"]
        marks = []
        for f, sa, sb, _a, _b in spans:
            hit = False
            for d in dups:
                f1, f2 = d["firstFile"], d["secondFile"]
                if f1["name"] != f or f2["name"] != f:
                    continue
                s1, s2 = (f1["start"], f1["end"]), (f2["start"], f2["end"])
                if (overlaps(s1, sa) and overlaps(s2, sb)) or (overlaps(s1, sb) and overlaps(s2, sa)):
                    hit = True
                    break
            marks.append("命中" if hit else "未命中")
        print(f"k={k:>3}  " + "  ".join(marks))


if __name__ == "__main__":
    main()
