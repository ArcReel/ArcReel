#!/usr/bin/env python3
"""目录归组搬迁：按 ``module_map.toml`` 移动源码与镜像测试，并改写全仓对旧路径的引用。

用法::

    uv run python scripts/regroup/migrate.py            # 执行搬迁；可重复运行，已完成的部分不再改动
    uv run python scripts/regroup/migrate.py --check    # 残留检查：仍有待搬文件或旧路径引用时退出码 1

一次运行依次完成：

1. 校验映射表：核心库顶层或应用服务层出现表外散文件、某条映射的新旧路径都不存在或同时存在时报错退出；
2. 用 ``git mv`` 移动源码（子包整体移动）与镜像测试，并给新建的包补空 ``__init__.py``。测试去向由
   ``preview.derive_test_moves`` 从映射表推导，结果累积写入 ``test_moves.tsv``，重跑时据此改写对旧测试
   路径的引用；
3. 改写 Python 导入：``from lib import <被移动模块>`` 按新父包拆分，移动后解析结果会变的相对导入改为
   绝对导入；被移动文件里 ``Path(__file__)`` 向上取的目录按新深度改写；
4. 改写全仓文本里的点分模块路径（最长前缀匹配，含字符串形式的打桩路径与测试模块路径）与斜杠源码路径
   （文档、配置、相对链接）；import-linter 分层契约的层名按容器内的相对路径改写；
5. 对改动过的 Python 文件运行 ``ruff check --select I --fix`` 与 ``ruff format``，对改动过的文档站文件
   运行 prettier。

合并了本次搬迁的分支：合并后运行一次本脚本改写自己新增的代码，再用 ``--check`` 自检。
映射表、预演工具及其生成记录、搬迁脚本自身与预演测试夹具会有意记录旧路径，不在改写与残留检查范围内。
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.regroup.preview import ModuleMap, derive_test_moves, load_map, rename_contract

TEST_MOVES_PATH = Path(__file__).with_name("test_moves.tsv")
EXCLUDED_FILES = {
    "scripts/regroup/migrate.py",
    "scripts/regroup/module_map.toml",
    "scripts/regroup/preview-report.md",
    "scripts/regroup/preview.py",
    "scripts/regroup/test_moves.tsv",
    "tests/unit/scripts/test_regroup_preview.py",
}
# 带通配符的写法不是模块路径，最长前缀匹配够不到，逐条登记
LITERAL_REWRITES = {
    "lib.*_backends": "lib.backends.*_backends",
    "lib/{text,image,video}_backends": "lib/backends/{text,image,video}_backends",
    "lib/prompt_builders*.py": "lib/prompts/prompt_builders*.py",
}
# 模块路径以外的写法：逐文件登记改写前后的原文
FILE_REWRITES = {
    # 子串提示原本靠 "lib.media" 同时命中两个 lib.media_* 模块，搬迁后按各自的新路径列出
    "scripts/audit_tests.py": [
        (
            '    "lib.media",\n',
            '    "lib.generation.media_generator",\n    "lib.artifacts.media_artifact_currency",\n',
        ),
    ],
    # 把 lib/ 插进 sys.path 后按裸模块名导入；project_manager 进了子包，改为从仓库根按完整路径导入
    "scripts/migrate_to_project_json.py": [
        (
            '# 添加 lib 目录到 Python 路径\nlib_path = Path(__file__).parent.parent / "lib"\nsys.path.insert(0, str(lib_path))\n\n'
            "from project_manager import ProjectManager\n",
            "# 添加仓库根目录到 Python 路径\nsys.path.insert(0, str(Path(__file__).parent.parent))\n\n"
            "from lib.project.project_manager import ProjectManager\n",
        ),
    ],
}
SOURCE_PACKAGE_PARENTS = ("lib", "server/services")

DOTTED = re.compile(r"(?<![\w.])(?:lib|server|tests)(?:\.\w+)+", re.ASCII)
SLASHED = re.compile(
    r"(?<![\w\-])(?:lib|server/services|tests/(?:unit|integration)/(?:lib|server/services))(?:/[\w.\-*]+)+/?",
    re.ASCII,
)
# 斜杠路径前面紧挨 ``/`` 时，只有相对链接与仓库 URL 两种前缀指向本仓库根
SLASHED_ROOT_PREFIX = re.compile(r"(?:(?:^|[^\w.\-/])\.{1,2}/|github\.com/[\w.\-]+/[\w.\-]+/(?:blob|tree)/[\w.\-]+/)$")
FILE_DEPTH = re.compile(
    r"Path\(__file__\)(?P<resolve>\.resolve\(\))?(?:(?P<chain>(?:\.parent\b)+)|\.parents\[(?P<index>\d+)\])"
)


class MigrationError(Exception):
    pass


# ---------------------------------------------------------------- 计划


def module_path(module: str) -> str:
    return module.replace(".", "/")


def path_module(path: str) -> str:
    p = PurePosixPath(path)
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@dataclass
class Plan:
    mapping: ModuleMap
    # 旧仓库相对路径 → 新路径：文件精确匹配，目录按最长前缀匹配
    files: dict[str, str] = field(default_factory=dict)
    dirs: dict[str, str] = field(default_factory=dict)
    # 测试模块的点分路径（文件或目录）→ 新点分路径
    test_modules: dict[str, str] = field(default_factory=dict)
    test_moves: dict[str, str] = field(default_factory=dict)
    # 本次需要移动的文件（源, 目标）与被搬空的旧目录
    file_moves: list[tuple[str, str]] = field(default_factory=list)
    vacated_dirs: list[str] = field(default_factory=list)

    def rename_path(self, path: str) -> str:
        if path in self.files:
            return self.files[path]
        parts = path.split("/")
        for i in range(len(parts), 0, -1):
            target = self.dirs.get("/".join(parts[:i]))
            if target is not None:
                return "/".join([target, *parts[i:]])
        return path

    def rename_module(self, name: str) -> str:
        if not name.startswith("tests."):
            return self.mapping.rename(name)
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            target = self.test_modules.get(".".join(parts[:i]))
            if target is not None:
                return ".".join([target, *parts[i:]])
        return name


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout


def repo_files() -> list[str]:
    """已跟踪与未忽略的未跟踪文件（下游分支可能有尚未提交的新文件）。"""
    out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({p for p in out.split("\0") if p and (REPO_ROOT / p).is_file()})


def _files_under(files: Iterable[str], directory: str) -> list[str]:
    prefix = directory + "/"
    return [f for f in files if f.startswith(prefix)]


def _load_test_moves() -> dict[str, str]:
    if not TEST_MOVES_PATH.exists():
        return {}
    moves: dict[str, str] = {}
    for line in TEST_MOVES_PATH.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            old, new = line.split("\t")
            moves[old] = new
    return moves


def _write_test_moves(moves: dict[str, str]) -> None:
    lines = ["# 由 migrate.py 生成：镜像测试的旧路径 → 新路径（目录以 / 结尾），勿手工编辑"]
    lines.extend(f"{old}\t{new}" for old, new in sorted(moves.items()))
    text = "\n".join(lines) + "\n"
    if not TEST_MOVES_PATH.exists() or TEST_MOVES_PATH.read_text(encoding="utf-8") != text:
        TEST_MOVES_PATH.write_text(text, encoding="utf-8")


def build_plan(mapping: ModuleMap, files: list[str]) -> Plan:
    problems: list[str] = []
    file_set = set(files)
    for f in files:
        p = PurePosixPath(f)
        if (
            p.suffix == ".py"
            and p.name != "__init__.py"
            and str(p.parent) in SOURCE_PACKAGE_PARENTS
            and path_module(f) not in mapping.moves
        ):
            problems.append(f"{f} 不在映射表 [moves] 中：先在 module_map.toml 登记它的新归属")
    for new in mapping.moves.values():
        if mapping.rename(new) != new:
            problems.append(f"新路径 {new} 仍命中映射表的旧路径，改写无法收敛")
        if new.rsplit(".", 1)[-1] != mapping.rename(new).rsplit(".", 1)[-1]:
            problems.append(f"{new} 改了模块名，本脚本只支持换目录")

    plan = Plan(mapping)
    for old, new in sorted(mapping.moves.items()):
        if old.rsplit(".", 1)[-1] != new.rsplit(".", 1)[-1]:
            problems.append(f"{old} → {new} 改了模块名，本脚本只支持换目录")
        old_dir, new_dir = module_path(old), module_path(new)
        old_files = _files_under(files, old_dir)
        if old_files or _files_under(files, new_dir):
            plan.dirs[old_dir] = new_dir
            plan.file_moves.extend((f, new_dir + f[len(old_dir) :]) for f in old_files)
            if old_files:
                plan.vacated_dirs.append(old_dir)
            continue
        old_file, new_file = old_dir + ".py", new_dir + ".py"
        plan.files[old_file] = new_file
        if old_file in file_set and new_file in file_set:
            problems.append(f"{old} 的新旧路径同时存在：{old_file}、{new_file}")
        elif old_file in file_set:
            plan.file_moves.append((old_file, new_file))
        elif new_file not in file_set:
            problems.append(f"映射表的旧路径 {old} 不存在：{old_file} 与 {new_file} 都没有")

    plan.test_moves = _load_test_moves()
    derived, unresolved = derive_test_moves(mapping)
    problems.extend(f"无法从映射表推导测试 {p} 的去向" for p in unresolved)
    for move in derived:
        plan.test_moves[move.old] = move.new
    for old, new in plan.test_moves.items():
        if old.endswith("/"):
            old_dir, new_dir = old.rstrip("/"), new.rstrip("/")
            plan.dirs[old_dir] = new_dir
            old_files = _files_under(files, old_dir)
            plan.file_moves.extend((f, new_dir + f[len(old_dir) :]) for f in old_files)
            if old_files:
                plan.vacated_dirs.append(old_dir)
            plan.test_modules[path_module(old_dir)] = path_module(new_dir)
        else:
            plan.files[old] = new
            if old in file_set:
                plan.file_moves.append((old, new))
            plan.test_modules[path_module(old)] = path_module(new)
    problems.extend(
        f"新路径 {new} 仍命中旧路径，改写无法收敛"
        for new in [*plan.files.values(), *plan.dirs.values()]
        if plan.rename_path(new) != new
    )

    seen: set[str] = set()
    for _, dst in plan.file_moves:
        if dst in seen:
            problems.append(f"多个文件都要移动到 {dst}")
        seen.add(dst)
        if dst in file_set:
            problems.append(f"移动目标 {dst} 已存在")
    if problems:
        raise MigrationError("\n".join(problems))
    return plan


# ---------------------------------------------------------------- Python 导入


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str | None:
    base = module.split(".")
    if not is_package:
        base = base[:-1]
    if level - 1 >= len(base):
        return None
    base = base[: len(base) - (level - 1)]
    return ".".join([*base, target] if target else base)


def _is_project(module: str) -> bool:
    return module.split(".")[0] in ("lib", "server", "tests")


class _Source:
    """ast 的列偏移是 UTF-8 字节偏移，这里换算成字符串下标。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.splitlines(keepends=True)
        self.starts = [0]
        for line in self.lines:
            self.starts.append(self.starts[-1] + len(line))

    def offset(self, lineno: int, col: int) -> int:
        line = self.lines[lineno - 1]
        return self.starts[lineno - 1] + len(line.encode("utf-8")[:col].decode("utf-8"))


def _alias_text(alias: ast.alias) -> str:
    return alias.name if alias.asname is None else f"{alias.name} as {alias.asname}"


def rewrite_imports(text: str, module: str, new_module: str, is_package: bool, plan: Plan) -> str:
    """改写 ``from X import ...``：被移动的子模块按新父包拆分，解析结果会变的相对导入改为绝对导入。

    ``module`` 是这段源码写下时所在的模块，用来解析相对导入；``new_module`` 是它现在的位置。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    src = _Source(text)
    rename = plan.rename_module
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        base = _resolve_relative(module, is_package, node.level, node.module) if node.level else node.module
        if not base or not _is_project(base):
            continue
        groups: dict[str, list[ast.alias]] = {}
        for alias in node.names:
            target = rename(base)
            if alias.name != "*":
                full = rename(f"{base}.{alias.name}")
                if full != f"{target}.{alias.name}":
                    target = full.rsplit(".", 1)[0]
            groups.setdefault(target, []).append(alias)
        start = src.offset(node.lineno, node.col_offset)
        end = src.offset(node.end_lineno or node.lineno, node.end_col_offset or 0)
        segment = text[start:end]
        if len(groups) == 1:
            (target,) = groups
            if node.level:
                if _resolve_relative(new_module, is_package, node.level, node.module) == target:
                    continue
            elif target == node.module:
                continue
            spec = re.match(r"from\s+(\.*[\w.]*)\s+import", segment)
            if spec is None:
                raise MigrationError(f"{module}: 无法定位导入语句的模块部分：{segment!r}")
            edits.append((start + spec.start(1), start + spec.end(1), target))
            continue
        indent = " " * node.col_offset
        statements = [
            f"from {target} import {', '.join(_alias_text(a) for a in aliases)}" for target, aliases in groups.items()
        ]
        edits.append((start, end, f"\n{indent}".join(statements)))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def rewrite_file_depth(text: str, old_path: str, new_path: str, plan: Plan) -> str:
    """被移动文件里 ``Path(__file__)`` 向上取的目录：保持指向同一个（或随之移动的）目录。"""
    old, new = PurePosixPath(old_path), PurePosixPath(new_path)

    def repl(match: re.Match[str]) -> str:
        chain = match.group("chain")
        levels = chain.count(".parent") if chain else int(match.group("index")) + 1
        target = PurePosixPath(plan.rename_path(str(old.parents[levels - 1])))
        new_parents = list(new.parents)
        if target not in new_parents:
            raise MigrationError(f"{new_path}: {match.group(0)} 移动后不再是它的上级目录")
        new_levels = new_parents.index(target) + 1
        resolve = match.group("resolve") or ""
        if match.group("chain"):
            return f"Path(__file__){resolve}" + ".parent" * new_levels
        return f"Path(__file__){resolve}.parents[{new_levels - 1}]"

    return FILE_DEPTH.sub(repl, text)


# ---------------------------------------------------------------- 文本路径


def rewrite_text(text: str, plan: Plan) -> str:
    for old, new in LITERAL_REWRITES.items():
        text = text.replace(old, new)
    text = DOTTED.sub(lambda m: plan.rename_module(m.group(0)), text)

    def slashed(match: re.Match[str]) -> str:
        raw = match.group(0)
        start = match.start()
        if start and text[start - 1] == "/" and not SLASHED_ROOT_PREFIX.search(text[max(0, start - 200) : start]):
            return raw
        if start and text[start - 1] == ".":
            return raw
        trailing = ""
        while raw and raw[-1] in "./":
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        head, dot, attribute = raw, "", ""
        if "." in raw.rsplit("/", 1)[-1] and not raw.endswith(".py"):
            # ``lib/asset_types.ASSET_SPECS``：斜杠模块路径后接属性
            stem, _, attribute = raw.rsplit("/", 1)[-1].partition(".")
            head, dot = raw.rsplit("/", 1)[0] + "/" + stem, "."
        new = plan.rename_path(head)
        if new == head and head + ".py" in plan.files:
            new = plan.files[head + ".py"].removesuffix(".py")
        return new + dot + attribute + trailing

    return SLASHED.sub(slashed, text)


def rewrite_import_linter_layers(text: str, plan: Plan) -> str:
    config = tomllib.loads(text)
    for contract in config.get("tool", {}).get("importlinter", {}).get("contracts", []):
        if "layers" not in contract:
            continue
        renamed, _ = rename_contract(contract, plan.mapping)
        for old, new in zip(contract["layers"], renamed["layers"], strict=True):
            if old != new:
                text = text.replace(f'"{old}"', f'"{new}"')
    return text


def rewrite_content(path: str, text: str, plan: Plan, origin: str) -> str:
    """一个文件的全部文本改写；``origin`` 是它在本次移动前的路径。"""
    for old, new in FILE_REWRITES.get(path, []):
        text = text.replace(old, new)
    if path.endswith(".py"):
        module, new_module = path_module(origin), path_module(path)
        is_package = path.endswith("__init__.py")
        text = rewrite_imports(text, module, new_module, is_package, plan)
        if origin != path:
            text = rewrite_file_depth(text, origin, path, plan)
    if path == "pyproject.toml":
        text = rewrite_import_linter_layers(text, plan)
    return rewrite_text(text, plan)


def _excluded(path: str) -> bool:
    return path in EXCLUDED_FILES


def _read_text(path: str) -> str | None:
    try:
        return (REPO_ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


# ---------------------------------------------------------------- 执行


def execute_moves(plan: Plan) -> None:
    tracked = set(git("ls-files", "-z", "--cached").split("\0"))
    by_dir: dict[str, list[str]] = {}
    for src, dst in plan.file_moves:
        parent = PurePosixPath(dst).parent
        (REPO_ROOT / parent).mkdir(parents=True, exist_ok=True)
        if src in tracked:
            by_dir.setdefault(str(parent), []).append(src)
        else:
            (REPO_ROOT / src).rename(REPO_ROOT / dst)
    for directory, sources in sorted(by_dir.items()):
        for i in range(0, len(sources), 200):
            git("mv", "--", *sources[i : i + 200], directory)
    for old_dir in sorted(plan.vacated_dirs, key=len, reverse=True):
        root = REPO_ROOT / old_dir
        if not root.exists():
            continue
        for cache in sorted(root.rglob("__pycache__"), reverse=True):
            shutil.rmtree(cache)
        for leftover in sorted(root.rglob(".DS_Store")):
            leftover.unlink()
        for directory in sorted(
            (p for p in [root, *root.rglob("*")] if p.is_dir()), key=lambda p: len(p.parts), reverse=True
        ):
            if not any(directory.iterdir()):
                directory.rmdir()
        if root.exists():
            print(f"警告：{old_dir} 搬空后仍有未跟踪文件，请手工确认", file=sys.stderr)


def add_package_inits(plan: Plan) -> list[str]:
    """移动目标所在的目录若缺 ``__init__.py``、且不是随子包整体移动来的，补一个空的。"""
    moved_roots = [PurePosixPath(d) for d in plan.dirs.values()]
    directories: set[PurePosixPath] = set()
    for _, dst in plan.file_moves:
        directories.update(p for p in PurePosixPath(dst).parents if len(p.parts) > 1)
    added: list[str] = []
    for d in sorted(directories):
        if any(d == root or root in d.parents for root in moved_roots):
            continue
        init = REPO_ROOT / d / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
            added.append(str(d / "__init__.py"))
    return added


def _run_formatters(py_files: list[str], website_files: list[str]) -> None:
    if py_files:
        for i in range(0, len(py_files), 400):
            chunk = py_files[i : i + 400]
            subprocess.run(
                [sys.executable, "-m", "ruff", "check", "--select", "I", "--fix", "--quiet", *chunk],
                cwd=REPO_ROOT,
                check=True,
            )
            subprocess.run([sys.executable, "-m", "ruff", "format", "--quiet", *chunk], cwd=REPO_ROOT, check=True)
    if website_files:
        website = REPO_ROOT / "website"
        prettier = website / "node_modules" / ".bin" / "prettier"
        if not prettier.exists():
            raise MigrationError("改写了文档站文件，需要先在 website/ 下运行 pnpm install 以便格式化")
        rel = [str(PurePosixPath(f).relative_to("website")) for f in website_files]
        subprocess.run([str(prettier), "--write", "--log-level", "warn", *rel], cwd=website, check=True)


def migrate(plan: Plan) -> None:
    origins = {dst: src for src, dst in plan.file_moves}
    execute_moves(plan)
    _write_test_moves(plan.test_moves)
    added = add_package_inits(plan)
    changed_py: set[str] = {f for f in [*origins, *added] if f.endswith(".py")}
    changed_web: list[str] = []
    for path in repo_files():
        if _excluded(path):
            continue
        text = _read_text(path)
        if text is None:
            continue
        new = rewrite_content(path, text, plan, origins.get(path, path))
        if new != text:
            (REPO_ROOT / path).write_text(new, encoding="utf-8")
            if path.endswith(".py"):
                changed_py.add(path)
            if path.startswith("website/"):
                changed_web.append(path)
    _run_formatters(sorted(changed_py), sorted(changed_web))
    print(
        f"移动 {len(plan.file_moves)} 个文件，新建 {len(added)} 个 __init__.py，改写 {len(changed_py)} 个 Python 文件"
    )


def residual(plan: Plan) -> list[str]:
    findings = [f"待移动：{src} → {dst}" for src, dst in plan.file_moves]
    for path in repo_files():
        if _excluded(path):
            continue
        text = _read_text(path)
        if text is None:
            continue
        new = rewrite_content(path, text, plan, path)
        if new == text:
            continue
        old_lines, new_lines = text.splitlines(), new.splitlines()
        hits = [i + 1 for i, (a, b) in enumerate(zip(old_lines, new_lines, strict=False)) if a != b]
        findings.append(f"{path}:{hits[0] if hits else 1}: 仍引用旧路径（共 {max(len(hits), 1)} 行）")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="残留检查：仍有待搬文件或旧路径引用时退出码 1")
    args = parser.parse_args(argv)
    try:
        plan = build_plan(load_map(), repo_files())
        if args.check:
            findings = residual(plan)
            for line in findings:
                print(line)
            if findings:
                print(f"残留 {len(findings)} 处；运行 uv run python scripts/regroup/migrate.py 改写", file=sys.stderr)
                return 1
            print("残留检查通过：没有待搬文件，也没有对旧路径的引用")
            return 0
        migrate(plan)
    except MigrationError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
