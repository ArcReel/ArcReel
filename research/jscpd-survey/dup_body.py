#!/usr/bin/env python3
"""纯静态对照组：找 tests/ 里函数体规范化后完全相同的测试（真·重复），不需要覆盖数据。

规范化 = `ast.unparse(函数体)`，忽略函数名、装饰器、docstring。
"""

from __future__ import annotations

import ast
import collections
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "tests")
groups: dict[str, list[str]] = collections.defaultdict(list)
parametrized: set[str] = set()
total = 0


def has_parametrize(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any("parametrize" in ast.unparse(d) for d in fn.decorator_list)


for path in sorted(root.rglob("test_*.py")):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        continue

    def walk(node: ast.AST, prefix: str) -> None:
        global total
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}::{child.name}")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if not child.name.startswith("test"):
                    continue
                body = child.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    body = body[1:]
                if not body:
                    continue
                key = "\n".join(ast.unparse(s) for s in body)
                name = f"{prefix}::{child.name}"
                groups[key].append(name)
                if has_parametrize(child):
                    parametrized.add(name)
                total += 1

    walk(tree, str(path))

dups = {k: v for k, v in groups.items() if len(v) > 1}


def same_file(v: list[str]) -> bool:
    return len({n.split("::")[0] for n in v}) == 1


def any_param(v: list[str]) -> bool:
    return any(n in parametrized for n in v)


strict = {k: v for k, v in dups.items() if same_file(v) and not any_param(v)}
print(f"测试函数 {total}，函数体规范化后完全相同的组 {len(dups)}，涉及函数 {sum(len(v) for v in dups.values())}")
print(f"  跨文件组（局部辅助函数同名，多为假阳）: {sum(1 for v in dups.values() if not same_file(v))}")
print(
    f"  含 parametrize 的组（差异在装饰器里，属可合并而非可删）: {sum(1 for v in dups.values() if same_file(v) and any_param(v))}"
)
print(f"  严格组（同文件 + 双方都无 parametrize）: {len(strict)}，涉及函数 {sum(len(v) for v in strict.values())}")
for k, v in sorted(strict.items(), key=lambda kv: -len(kv[1])):
    print(f"\n-- 严格同体 {len(v)} 个 --")
    for name in v:
        print(f"   {name}")
    print("   body:", k.replace("\n", " ⏎ ")[:200])
