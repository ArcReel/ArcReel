"""Plan and run tests related to repository changes.

The selector is intentionally conservative: if a Python production change cannot
be connected to at least one test through the static import graph or an explicit
contract mapping, the backend plan escalates to the full non-E2E suite.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOTS = ("lib", "server", "alembic", "scripts", "tests")
BACKEND_PREFIXES = ("lib/", "server/", "alembic/", "scripts/", "tests/", "agent_runtime_profile/")
BACKEND_ROOT_FILES = {"alembic.ini", "pyproject.toml", "uv.lock"}
BACKEND_FULL_FILES = {"alembic.ini", "pyproject.toml", "uv.lock", "tests/fakes.py", "tests/factories.py"}
FRONTEND_FULL_FILES = {
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/vitest.config.ts",
    "frontend/vite.config.ts",
    "frontend/tsconfig.json",
    "frontend/eslint.config.js",
}
FRONTEND_RUNTIME_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css", ".json"}

PROFILE_CONTRACT_TESTS = {
    "tests/test_agent_profile_lint.py",
    "tests/test_frontend_skill_i18n.py",
    "tests/test_profile_manifest.py",
    "tests/test_skill_script_path_guards.py",
    "tests/prompt_rules/test_subagent_md_sync.py",
    "tests/prompt_rules/test_video_workflow_prompt.py",
}
I18N_CONTRACT_TESTS = {
    "tests/test_frontend_mcp_tool_i18n.py",
    "tests/test_frontend_skill_i18n.py",
    "tests/test_frontend_task_type_i18n.py",
    "tests/test_i18n_consistency.py",
}
EXPLICIT_FILE_CONTRACTS = {
    "frontend/src/types/workflow.ts": {"tests/test_workflow_action_types.py"},
    "public/skill.md.template": {"tests/test_public_contract.py"},
    "scripts/test_changed.py": {"tests/scripts/test_test_changed.py"},
}

Mode = Literal["none", "related", "full"]


@dataclass
class DomainPlan:
    mode: Mode = "none"
    tests: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class TestPlan:
    base: str
    changed: list[str]
    backend: DomainPlan = field(default_factory=DomainPlan)
    frontend: DomainPlan = field(default_factory=DomainPlan)
    website: DomainPlan = field(default_factory=DomainPlan)


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def collect_changed_paths(root: Path, base: str) -> list[str]:
    """Return branch, staged, unstaged, and untracked changes relative to base."""

    merge_base = _git(root, "merge-base", "HEAD", base)
    outputs = [
        _git(root, "diff", "--name-only", "--diff-filter=ACMRD", merge_base, "HEAD"),
        _git(root, "diff", "--name-only", "--diff-filter=ACMRD"),
        _git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMRD"),
        _git(root, "ls-files", "--others", "--exclude-standard"),
    ]
    return sorted({line for output in outputs for line in output.splitlines() if line})


def _module_name(path: PurePosixPath) -> str | None:
    if path.suffix != ".py" or not path.parts or path.parts[0] not in PYTHON_ROOTS:
        return None
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def _resolve_imports(path: Path, module: str, known: set[str]) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()

    resolved: set[str] = set()
    is_package = path.name == "__init__.py"
    package_parts = module.split(".") if is_package else module.split(".")[:-1]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _add_known_import(alias.name, known, resolved)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = max(0, len(package_parts) - node.level + 1)
                prefix = package_parts[:keep]
                if node.module:
                    prefix.extend(node.module.split("."))
                base = ".".join(prefix)
            else:
                base = node.module or ""
            if base:
                _add_known_import(base, known, resolved)
            for alias in node.names:
                if alias.name != "*" and base:
                    _add_known_import(f"{base}.{alias.name}", known, resolved)
    return resolved


def _add_known_import(name: str, known: set[str], destination: set[str]) -> None:
    candidate = name
    while candidate:
        if candidate in known:
            destination.add(candidate)
            return
        candidate = candidate.rpartition(".")[0]


def _python_inventory(root: Path) -> tuple[dict[str, str], dict[str, set[str]]]:
    modules: dict[str, str] = {}
    for top in PYTHON_ROOTS:
        base = root / top
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            rel = PurePosixPath(path.relative_to(root).as_posix())
            module = _module_name(rel)
            if module:
                modules[module] = rel.as_posix()

    known = set(modules)
    reverse: dict[str, set[str]] = defaultdict(set)
    for module, relative in modules.items():
        for dependency in _resolve_imports(root / relative, module, known):
            reverse[dependency].add(module)
    return modules, reverse


def _dependent_tests(changed_modules: set[str], modules: dict[str, str], reverse: dict[str, set[str]]) -> set[str]:
    queue = deque(changed_modules)
    visited = set(changed_modules)
    selected: set[str] = set()
    while queue:
        current = queue.popleft()
        relative = modules.get(current)
        if relative and relative.startswith("tests/") and Path(relative).name.startswith("test_"):
            selected.add(relative)
        for dependent in reverse.get(current, ()):
            if dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)
    return selected


def _companion_tests(changed: PurePosixPath, all_tests: set[str]) -> set[str]:
    stem = changed.stem
    if stem == "__init__" or len(stem) < 5:
        return set()
    return {test for test in all_tests if stem in PurePosixPath(test).stem}


def _existing(root: Path, candidates: set[str]) -> set[str]:
    return {candidate for candidate in candidates if (root / candidate).is_file()}


def build_plan(root: Path, changed_paths: list[str], base: str) -> TestPlan:
    changed = sorted({PurePosixPath(path).as_posix() for path in changed_paths})
    plan = TestPlan(base=base, changed=changed)
    modules, reverse = _python_inventory(root)
    all_tests = {path for path in modules.values() if path.startswith("tests/") and Path(path).name.startswith("test_")}

    backend_changed = {
        path
        for path in changed
        if path in BACKEND_ROOT_FILES or path.startswith(BACKEND_PREFIXES) or path in EXPLICIT_FILE_CONTRACTS
    }
    frontend_changed = {path for path in changed if path.startswith("frontend/")}
    website_changed = {
        path
        for path in changed
        if path.startswith("website/")
        or path in {"CONTRIBUTING.md", "README.md", "README.en.md"}
        or path.startswith(".claude/skills/translate-docs/")
    }

    backend_tests: set[str] = set()
    production_python: set[str] = set()
    full_backend_reasons: list[str] = []

    for raw in backend_changed:
        path = PurePosixPath(raw)
        if raw in BACKEND_FULL_FILES or (raw.startswith("tests/") and path.name == "conftest.py"):
            full_backend_reasons.append(f"共享测试或依赖配置变化：{raw}")
            continue
        if raw.startswith("agent_runtime_profile/"):
            backend_tests.update(_existing(root, PROFILE_CONTRACT_TESTS))
            continue
        backend_tests.update(_existing(root, EXPLICIT_FILE_CONTRACTS.get(raw, set())))
        if raw.startswith("tests/") and path.suffix == ".py" and path.name.startswith("test_"):
            if (root / raw).exists():
                backend_tests.add(raw)
            continue
        module = _module_name(path)
        if module and not raw.startswith("tests/"):
            if not (root / raw).exists():
                full_backend_reasons.append(f"生产模块被删除，静态图无法证明影响边界：{raw}")
            else:
                production_python.add(module)
                backend_tests.update(_companion_tests(path, all_tests))
            continue
        if raw.startswith(("lib/", "server/", "alembic/", "scripts/")) and raw not in EXPLICIT_FILE_CONTRACTS:
            full_backend_reasons.append(f"未知 backend 非 Python 运行期文件：{raw}")

    if any(path.startswith("frontend/src/i18n/") for path in frontend_changed):
        backend_tests.update(_existing(root, I18N_CONTRACT_TESTS))
        backend_changed.add("frontend/src/i18n/")
    if "frontend/src/types/workflow.ts" in frontend_changed:
        backend_tests.update(_existing(root, EXPLICIT_FILE_CONTRACTS["frontend/src/types/workflow.ts"]))
        backend_changed.add("frontend/src/types/workflow.ts")

    if production_python:
        dependent = _dependent_tests(production_python, modules, reverse)
        backend_tests.update(dependent)
        for module in sorted(production_python):
            per_module = _dependent_tests({module}, modules, reverse)
            relative = modules.get(module, module)
            if not per_module and not _companion_tests(PurePosixPath(relative), all_tests):
                full_backend_reasons.append(f"生产模块没有可证明相关的测试：{relative}")

    if full_backend_reasons:
        plan.backend.mode = "full"
        plan.backend.reasons = sorted(set(full_backend_reasons))
    elif backend_changed:
        plan.backend.mode = "related"
        plan.backend.tests = sorted(backend_tests)
        plan.backend.reasons = [f"{len(plan.backend.tests)} 个 pytest 文件与改动相关"]

    frontend_full_reasons: list[str] = []
    frontend_tests: set[str] = set()
    frontend_sources: set[str] = set()
    for raw in frontend_changed:
        path = PurePosixPath(raw)
        relative = PurePosixPath(*path.parts[1:]).as_posix()
        if raw in FRONTEND_FULL_FILES or raw.startswith(("frontend/src/test/", "frontend/src/__mocks__/")):
            frontend_full_reasons.append(f"前端测试基础设施变化：{raw}")
        elif not (root / raw).exists():
            frontend_full_reasons.append(f"前端文件被删除：{raw}")
        elif ".test." in path.name:
            frontend_tests.add(relative)
        elif raw.startswith("frontend/src/") and path.suffix in FRONTEND_RUNTIME_SUFFIXES:
            frontend_sources.add(relative)
        elif raw.startswith("frontend/"):
            frontend_full_reasons.append(f"未知前端运行期文件：{raw}")

    if frontend_full_reasons:
        plan.frontend.mode = "full"
        plan.frontend.reasons = sorted(set(frontend_full_reasons))
    elif frontend_changed:
        plan.frontend.mode = "related"
        plan.frontend.tests = sorted(frontend_tests)
        plan.frontend.sources = sorted(frontend_sources)
        plan.frontend.reasons = [
            f"{len(plan.frontend.tests)} 个直接测试文件，{len(plan.frontend.sources)} 个 Vitest related 源文件"
        ]

    if website_changed:
        script_changes = [path for path in website_changed if path.startswith("website/scripts/")]
        translation_changes = [path for path in website_changed if path.startswith(".claude/skills/translate-docs/")]
        if script_changes or translation_changes:
            plan.website.mode = "related"
            plan.website.reasons = ["文档工具变化，执行文档脚本单元测试"]
        else:
            plan.website.mode = "none"
            plan.website.reasons = ["仅文档内容变化；运行期测试留到 website 域完成闸门"]

    return plan


def _print_plan(plan: TestPlan, *, verbose: bool = False) -> None:
    print(f"base: {plan.base}")
    print(f"changed: {len(plan.changed)} file(s)")
    for path in plan.changed:
        print(f"  - {path}")
    for name in ("backend", "frontend", "website"):
        domain: DomainPlan = getattr(plan, name)
        print(f"{name}: {domain.mode}")
        for reason in domain.reasons:
            print(f"  reason: {reason}")
        tests = domain.tests if verbose else domain.tests[:25]
        sources = domain.sources if verbose else domain.sources[:25]
        for test in tests:
            print(f"  test: {test}")
        if len(domain.tests) > len(tests):
            print(f"  ... {len(domain.tests) - len(tests)} more test file(s); use --verbose to list all")
        for source in sources:
            print(f"  source: {source}")
        if len(domain.sources) > len(sources):
            print(f"  ... {len(domain.sources) - len(sources)} more source file(s); use --verbose to list all")


def _run(command: list[str], cwd: Path) -> int:
    print(f"\n$ {' '.join(command)}", flush=True)
    return subprocess.run(command, cwd=cwd, check=False).returncode


def frontend_commands(plan: TestPlan, frontend_root: Path) -> list[list[str]]:
    if plan.frontend.mode == "full":
        return [["pnpm", "test"]]
    if plan.frontend.mode != "related":
        return []

    commands: list[list[str]] = []
    if plan.frontend.tests:
        commands.append(["pnpm", "exec", "vitest", "run", "--passWithNoTests", *plan.frontend.tests])
    if plan.frontend.sources:
        absolute_sources = [str((frontend_root / source).resolve()) for source in plan.frontend.sources]
        commands.append(["pnpm", "exec", "vitest", "related", "--run", "--passWithNoTests", *absolute_sources])
    return commands


def run_plan(plan: TestPlan, root: Path) -> int:
    if plan.backend.mode == "full":
        code = _run([sys.executable, "-m", "pytest", "-m", "not e2e"], root)
        if code:
            return code
    elif plan.backend.mode == "related" and plan.backend.tests:
        code = _run([sys.executable, "-m", "pytest", *plan.backend.tests], root)
        if code:
            return code

    frontend_root = root / "frontend"
    for command in frontend_commands(plan, frontend_root):
        code = _run(command, frontend_root)
        if code:
            return code

    if plan.website.mode == "related":
        commands = [
            ["node", "--test", ".claude/skills/translate-docs/scripts/translation-lock.test.mjs"],
            ["node", "--test", "website/scripts/update-docs-inventory.test.mjs"],
        ]
        for command in commands:
            code = _run(command, root)
            if code:
                return code
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main", help="Git ref used as the change baseline (default: main)")
    parser.add_argument("--run", action="store_true", help="Execute the plan; default is preview only")
    parser.add_argument("--json", action="store_true", help="Print the plan as JSON")
    parser.add_argument("--verbose", action="store_true", help="List every selected test and source file")
    parser.add_argument(
        "--changed",
        action="append",
        default=[],
        metavar="PATH",
        help="Use an explicit changed path (repeatable); intended for diagnostics and tests",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    changed = sorted(set(args.changed)) if args.changed else collect_changed_paths(REPO_ROOT, args.base)
    plan = build_plan(REPO_ROOT, changed, args.base)
    if args.json:
        print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
    else:
        _print_plan(plan, verbose=args.verbose)
    return run_plan(plan, REPO_ROOT) if args.run else 0


if __name__ == "__main__":
    raise SystemExit(main())
