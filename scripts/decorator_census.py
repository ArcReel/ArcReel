#!/usr/bin/env python3
"""Count functions and function-line shares skipped by mutmut 3.7.0 decorators for mutation-batch admission.

mutmut 3.7.0 generates no mutants for entire decorated `FunctionDef` / `AsyncFunctionDef` subtrees
(except a single `@staticmethod` / `@classmethod`), skips decorated `ClassDef` entirely, and skips
nested functions inside skipped nodes. This is unconfigurable design; files with over half their
function lines skipped produce few mutants and are poor batch candidates.

Count unique line numbers from first decorator through function end; nested-function lines count once.
Count nested functions separately only if their parent is not skipped; otherwise they are already
included in the skipped parent. Directory summaries use `<根>/<一级子目录>` keys; root files use `<根>`.

No third-party dependencies. See `--help` and `docs/testing/mutmut-runbook.md` for module selection.
"""

from __future__ import annotations

import argparse
import ast
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

EXEMPT_DECORATORS = frozenset({"staticmethod", "classmethod"})


@dataclass
class Tally:
    """File/directory counts: functions, skipped functions, function lines, and skipped function lines."""

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
    """Whether a function itself is decorator-skipped under mutmut rules, excluding inherited skipping."""
    if not node.decorator_list:
        return False
    if len(node.decorator_list) == 1:
        decorator = node.decorator_list[0]
        if isinstance(decorator, ast.Name) and decorator.id in EXEMPT_DECORATORS:
            return False
    return True


def function_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> range:
    """Function line range, from the first decorator through its final line."""
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
    """Return None for syntax-invalid files and exclude them from statistics."""
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
    """Return (directory totals, files), both keyed and sorted by POSIX path strings."""
    per_dir: dict[str, Tally] = {}
    per_file: dict[str, Tally] = {}
    for path in sorted(root.rglob("*.py")):
        tally = tally_file(path)
        if tally is None:
            continue
        per_file[path.as_posix()] = tally
        per_dir.setdefault(group_key(root, path), Tally()).add(tally)
    return dict(sorted(per_dir.items())), per_file


def display_width(text: str) -> int:
    """Terminal display width: East Asian wide characters, including Chinese, occupy 2 columns; others 1."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def pad(text: str, width: int, *, align_right: bool = False) -> str:
    """Pad by display width so Chinese headers/totals align with ASCII-path rows."""
    fill = " " * max(width - display_width(text), 0)
    return fill + text if align_right else text + fill


def render_rows(rows: Iterable[tuple[str, Tally]], label: str) -> list[str]:
    header_cells = [
        pad("函数", 6, align_right=True),
        pad("跳过", 6, align_right=True),
        pad("跳过%", 7, align_right=True),
        pad("函数行", 8, align_right=True),
        pad("跳过行", 8, align_right=True),
        pad("跳过行%", 8, align_right=True),
    ]
    lines = [" ".join([pad(label, 60), *header_cells])]
    lines.extend(
        f"{pad(name, 60)} {t.functions:6} {t.skipped_functions:6} {t.skipped_function_ratio * 100:6.1f}% "
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
