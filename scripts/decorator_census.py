#!/usr/bin/env python3
"""统计源码目录里被 mutmut 3.7.0 装饰器规则跳过的函数与函数行占比，供挑选变异测试批次时做准入核对。

mutmut 3.7.0 对带装饰器的 `FunctionDef` / `AsyncFunctionDef` 整棵子树不生成 mutant（仅单个
`@staticmethod` / `@classmethod` 豁免），带装饰器的 `ClassDef` 整体跳过，嵌套在被跳过节点里的函数一并跳过。
这是 mutmut 的设计决定，不可配置；跳过行占比过半的文件在 mutmut 下几乎不产出 mutant，不值得进批次。

函数行数按「装饰器首行到函数末行」的唯一行号计，嵌套函数的行只算一次；嵌套函数只在外层未被跳过时单独计数，外层跳过则整体已计入外层。
按目录汇总时以 `<根>/<一级子目录>` 为键，直接放在根目录下的文件归 `<根>`。

零第三方依赖。用法见 `--help`，选模块规则见 `docs/testing/mutmut-runbook.md`。
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

EXEMPT_DECORATORS = frozenset({"staticmethod", "classmethod"})


@dataclass
class Tally:
    """一个文件或目录的统计：函数数、被跳过的函数数、函数行数、被跳过的函数行数。"""

    functions: int = 0
    skipped_functions: int = 0
    lines: int = 0
    skipped_lines: int = 0

    def add(self, other: Tally) -> None:
        self.functions += other.functions
        self.skipped_functions += other.skipped_functions
        self.lines += other.lines
        self.skipped_lines += other.skipped_lines

    @property
    def skipped_function_ratio(self) -> float:
        return self.skipped_functions / self.functions if self.functions else 0.0

    @property
    def skipped_line_ratio(self) -> float:
        return self.skipped_lines / self.lines if self.lines else 0.0


def is_skipped_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """按 mutmut 规则判断函数自身是否因装饰器被跳过（不含从外层继承的跳过）。"""
    if not node.decorator_list:
        return False
    if len(node.decorator_list) == 1:
        decorator = node.decorator_list[0]
        if isinstance(decorator, ast.Name) and decorator.id in EXEMPT_DECORATORS:
            return False
    return True


def function_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> range:
    """函数占用的行号区间，从装饰器首行到函数末行。"""
    start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    end = node.end_lineno if node.end_lineno is not None else node.lineno
    return range(start, end + 1)


def tally_tree(tree: ast.AST) -> Tally:
    tally = Tally()
    all_lines: set[int] = set()
    skipped_lines: set[int] = set()

    def visit(node: ast.AST, inherited_skip: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, inherited_skip or bool(child.decorator_list))
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                skipped = inherited_skip or is_skipped_function(child)
                lines = function_lines(child)
                tally.functions += 1
                all_lines.update(lines)
                if skipped:
                    tally.skipped_functions += 1
                    skipped_lines.update(lines)
                else:
                    visit(child, False)
            else:
                visit(child, inherited_skip)

    visit(tree, False)
    tally.lines = len(all_lines)
    tally.skipped_lines = len(skipped_lines)
    return tally


def tally_file(path: Path) -> Tally | None:
    """解析失败（语法错误）的文件返回 None，不计入统计。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    return tally_tree(tree)


def group_key(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    if len(relative.parts) <= 1:
        return root.as_posix()
    return (root / relative.parts[0]).as_posix()


def census(root: Path) -> tuple[dict[str, Tally], dict[str, Tally]]:
    """返回 (按目录汇总, 按文件)，两者都以 posix 路径字符串为键、按路径排序。"""
    per_dir: dict[str, Tally] = {}
    per_file: dict[str, Tally] = {}
    for path in sorted(root.rglob("*.py")):
        tally = tally_file(path)
        if tally is None:
            continue
        per_file[path.as_posix()] = tally
        per_dir.setdefault(group_key(root, path), Tally()).add(tally)
    return dict(sorted(per_dir.items())), per_file


def render_rows(rows: Iterable[tuple[str, Tally]], label: str) -> list[str]:
    lines = [f"{label:60} {'函数':>6} {'跳过':>6} {'跳过%':>7} {'函数行':>8} {'跳过行':>8} {'跳过行%':>8}"]
    lines.extend(
        f"{name:60} {t.functions:6} {t.skipped_functions:6} {t.skipped_function_ratio * 100:6.1f}% "
        f"{t.lines:8} {t.skipped_lines:8} {t.skipped_line_ratio * 100:7.1f}%"
        for name, t in rows
    )
    return lines


def render(root: Path, *, per_file: bool, min_skip: float) -> list[str]:
    per_dir, per_file_tallies = census(root)
    lines = [f"== {root.as_posix()}/ =="]
    if per_file:
        selected = [(name, t) for name, t in per_file_tallies.items() if t.skipped_line_ratio * 100 >= min_skip]
        selected.sort(key=lambda item: item[1].skipped_line_ratio, reverse=True)
        lines.extend(render_rows(selected, "文件"))
        return lines
    total = Tally()
    for tally in per_dir.values():
        total.add(tally)
    lines.extend(render_rows(per_dir.items(), "目录"))
    lines.extend(render_rows([("合计", total)], "")[1:])
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("roots", nargs="*", default=["lib", "server"], help="要统计的源码根目录，默认 lib 与 server")
    parser.add_argument("--per-file", action="store_true", help="按文件列出并按跳过行占比降序，而不是按目录汇总")
    parser.add_argument(
        "--min-skip",
        type=float,
        default=0.0,
        metavar="PCT",
        help="配合 --per-file：只列出跳过行占比不低于 PCT%% 的文件（准入核对用 50）",
    )
    args = parser.parse_args(argv)
    for raw in args.roots:
        root = Path(raw)
        if not root.is_dir():
            print(f"不是目录：{raw}", file=sys.stderr)
            return 2
        print("\n".join(render(root, per_file=args.per_file, min_skip=args.min_skip)))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
