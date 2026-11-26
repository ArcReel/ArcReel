#!/usr/bin/env python3
"""按改动快检，或运行受影响域的完整闸门。

    uv run python scripts/gate.py --changed            # 开发快检
    uv run python scripts/gate.py --changed --full     # push 前完整闸门
    uv run python scripts/gate.py backend frontend     # 点名的域总是完整运行
    uv run python scripts/gate.py --list               # 触发路径与完整步骤

全量 pytest / Vitest 步骤跨 worktree 排队。ARCREEL_GATE_LOCK 可覆盖机器锁路径。
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Literal

import portalocker

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = Path(os.environ.get("ARCREEL_GATE_LOCK") or Path.home() / ".cache" / "arcreel" / "gate.lock")
_PYTEST_NO_TESTS_COLLECTED = 5


@dataclass(frozen=True)
class Step:
    argv: tuple[str, ...]
    cwd: Path = ROOT
    heavy: bool = False
    junit_runner: Literal["pytest", "vitest"] | None = None
    testmon_sources: tuple[str, ...] = ()

    def describe(self) -> str:
        command = " ".join(self.argv)
        return command if self.cwd == ROOT else f"{command}    # cwd={self.cwd.relative_to(ROOT)}"


UV = ("uv", "run")
PYTEST = (*UV, "python", "-m", "pytest")
BACKEND_SUITE = Step((*PYTEST, "-n", "4", "--dist", "loadfile", "--max-worker-restart=0"), heavy=True)
FRONT = ROOT / "frontend"
FRONTEND_SUITE = Step(("pnpm", "test"), FRONT, heavy=True)
FULL_SUITES: dict[str, Step] = {
    "backend": BACKEND_SUITE,
    "frontend": FRONTEND_SUITE,
}

DOMAINS: dict[str, tuple[Step, ...]] = {
    "backend": (
        Step((*UV, "ruff", "check", ".")),
        Step((*UV, "ruff", "format", "--check", ".")),
        Step((*UV, "basedpyright", "--warnings")),
        Step((*UV, "lint-imports")),
        Step((*UV, "deptry", "lib", "server", "alembic", "scripts", "tests")),
        Step((*UV, "python", "-m", "scripts.audit_capability_codes")),
        BACKEND_SUITE,
    ),
    "market-core": (
        Step((*UV, "deptry", "src", "tests"), ROOT / "packages" / "arcreel-market-core"),
        Step((*UV, "python", "-m", "pytest"), ROOT / "packages" / "arcreel-market-core"),
    ),
    "catalog-core": (
        Step((*UV, "deptry", "src", "tests"), ROOT / "packages" / "arcreel-catalog-core"),
        Step((*UV, "python", "-m", "pytest"), ROOT / "packages" / "arcreel-catalog-core"),
    ),
    "tests": (Step((*UV, "python", "scripts/audit_tests.py", "--check")),),
    "conventions": (
        Step((*UV, "python", "scripts/audit_conventions.py", "--check")),
        Step((*UV, "python", "-m", "scripts.audit_comments")),
    ),
    "workflows": (
        Step((*UV, "pre-commit", "run", "--all-files", "actionlint")),
        Step((*UV, "pre-commit", "run", "--all-files", "zizmor")),
    ),
    "frontend": (Step(("pnpm", "check:static"), FRONT), FRONTEND_SUITE),
    "website": (Step(("pnpm", "check"), ROOT / "website"),),
    "e2e-live": (
        Step(("pnpm", "exec", "vite", "build"), FRONT),
        Step(("env", "E2E_PREBUILT=1", "pnpm", "e2e:live"), FRONT),
    ),
    "e2e-freshness": (Step(("pnpm", "e2e:record:freshness"), FRONT),),
    "e2e-record": (Step(("pnpm", "e2e:record:check"), ROOT / "frontend"),),
}

# 域 → 触发路径，fnmatch 通配（`*` 也跨目录分隔符）。与 CI 的 .github/actions/domain-filter/action.yml
# 对照维护：CI 的 backend 域对应这里的 backend、market-core 与 catalog-core，workflow 域对应 workflows；
BACKEND_TRIGGERS = (
    "lib/*",
    "server/*",
    "alembic/*",
    "alembic.ini",
    "conftest.py",
    "scripts/*",
    "tests/*",
    "agent_runtime_profile/*",
    "packages/*",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
    "frontend/src/i18n/*/dashboard.ts",
    "frontend/src/i18n/*/events.ts",
    "frontend/src/types/workflow.ts",
    "frontend/src/data/example-templates/*",
    "frontend/src/test/contracts/*",
)
TRIGGERS: dict[str, tuple[str, ...]] = {
    "backend": BACKEND_TRIGGERS,
    "market-core": ("packages/arcreel-market-core/*",),
    "catalog-core": ("packages/arcreel-catalog-core/*",),
    "workflows": (".github/*", ".pre-commit-config.yaml"),
    "frontend": ("frontend/*",),
    "website": ("website/*", "CONTRIBUTING.md", "README.md", "README.en.md"),
    "e2e-live": (
        "frontend/e2e/live/*",
        "frontend/playwright.config.ts",
        "frontend/e2e/support/viewports.ts",
        "frontend/package.json",
        "frontend/pnpm-lock.yaml",
    ),
    "e2e-freshness": (*BACKEND_TRIGGERS, "frontend/e2e/fixtures/recorded/*"),
    "e2e-record": (
        "scripts/e2e_record_backend.py",
        "tests/fixtures/catalog.json",
        "lib/app_version.py",
        "lib/catalog/lifecycle.py",
        "lib/catalog/query.py",
        "frontend/pnpm-lock.yaml",
        "pyproject.toml",
        "uv.lock",
        "frontend/e2e/record-fixtures.ts",
        "frontend/e2e/support/recorded.ts",
        "frontend/e2e/fixtures/comfyui.ts",
        "frontend/package.json",
    ),
}
ALWAYS_WHEN_CHANGED = ("tests", "conventions")


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _unknown(path: str) -> bool:
    if path.endswith(".md") or path.startswith("docs/"):
        return False
    return not any(_matches(path, patterns) for patterns in TRIGGERS.values())


def select_domains(paths: Iterable[str]) -> list[str]:
    """按改动路径选域，顺序与 DOMAINS 一致；秒级审计域总是入选。"""
    changed = list(paths)
    hit = {
        name
        for name, patterns in TRIGGERS.items()
        if any(fnmatch.fnmatchcase(path, pattern) for path in changed for pattern in patterns)
    }
    if any(_unknown(path) for path in changed):
        hit.update(("backend", "frontend", "e2e-freshness"))
    if "e2e-record" in hit:
        hit.add("e2e-freshness")
    hit.update(ALWAYS_WHEN_CHANGED)
    return [name for name in DOMAINS if name in hit]


def _vcs(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),  # noqa: S607 -- 开发者环境的 git 本就经 PATH 查找
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=True,
    )
    return completed.stdout


def changed_paths(base: str, root: Path = ROOT) -> list[str]:
    """相对 base 与 HEAD 公共祖先的全部改动：已提交、未提交与未跟踪的都算。

    关掉重命名识别，移动的文件新旧路径都列出来：旧路径所在域可能因此少了文件而失败。
    按 NUL 分隔读取，含非 ASCII 字符的路径才是原文而不是加引号的转义串。
    """
    merge_base = _vcs(root, "merge-base", base, "HEAD").strip()
    tracked = _vcs(root, "diff", "--name-only", "--no-renames", "-z", merge_base).split("\0")
    untracked = _vcs(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return sorted({*tracked, *untracked} - {""})


def _holder_note() -> str:
    try:
        note = LOCK_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return f"（当前持有者：{note}）" if note else ""


def _write_holder(handle: IO[str] | None, note: str) -> None:
    """锁文件的内容就是持有者说明，排队方据此打印是谁在跑。"""
    if handle is None:
        return
    handle.seek(0)
    handle.truncate()
    handle.write(note)
    handle.flush()


def _acquire_gate_lock() -> portalocker.Lock:
    """拿到机器级锁后返回；别人持有时每秒重试，只在开始排队时打印一次。"""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(LOCK_PATH, mode="a", encoding="utf-8")
    waited_from = time.monotonic()
    announced = False
    while True:
        try:
            lock.acquire(timeout=0, fail_when_locked=True)
        except portalocker.AlreadyLocked:
            if not announced:
                print(f"[gate] 另一份闸门正在运行，排队等待 {LOCK_PATH} {_holder_note()}", flush=True)
                announced = True
            time.sleep(1.0)
            continue
        if announced:
            print(f"[gate] 等待 {time.monotonic() - waited_from:.0f}s 后拿到锁", flush=True)
        return lock


def _run(step: Step) -> int:
    started = time.monotonic()
    print(f"\n$ {step.describe()}", flush=True)
    env = {**os.environ, "TESTMON_DATAFILE": str(ROOT / ".testmondata")} if "--testmon" in step.argv else None
    code = subprocess.run(step.argv, cwd=step.cwd, env=env, check=False).returncode
    print(f"[gate] {'ok' if code == 0 else f'exit {code}'} in {time.monotonic() - started:.0f}s", flush=True)
    return code


def _execute(step: Step) -> int:
    if not step.heavy:
        return _run(step)
    lock = _acquire_gate_lock()
    try:
        _write_holder(lock.fh, f"pid {os.getpid()} · {ROOT} · {step.describe()}")
        return _run(step)
    finally:
        _write_holder(lock.fh, "")
        lock.release()


BACKEND_SHARED = (
    "*/__init__.py",
    "*/__main__.py",
    "lib/db/*",
    "lib/i18n/*",
    "lib/config/*",
    "lib/project/project_migrations/*",
    "lib/project/data_validator.py",
    "lib/artifacts/artifact_manifest.py",
    "server/app.py",
    "server/shutdown.py",
    "server/i18n.py",
    "server/remote_mcp.py",
)
FRONTEND_SHARED = (
    "frontend/src/i18n/*",
    "frontend/src/branding.*",
    "frontend/src/test/*",
    "frontend/src/__mocks__/*",
    "frontend/src/main.tsx",
    *BACKEND_TRIGGERS,
)


def select_tests(name: str, paths: list[str], root: Path = ROOT) -> list[Step]:
    """为已命中的前端或后端域选择定向测试，遇到共享边界返回完整套件。"""
    suite = FULL_SUITES[name]
    sources = []
    tests = []
    for path in paths:
        if _unknown(path):
            return [suite]
        if not _matches(path, TRIGGERS[name]):
            continue
        if not (root / path).is_file():
            return [suite]
        if name == "backend":
            if _matches(path, ("tests/*/test_*.py",)):
                tests.append(path)
            elif _matches(path, ("server/*.py", "lib/*.py")) and not _matches(path, BACKEND_SHARED):
                sources.append(path)
            else:
                return [suite]
        elif not _matches(path, ("frontend/src/*.ts", "frontend/src/*.tsx")) or _matches(path, FRONTEND_SHARED):
            return [suite]
        elif _matches(path, ("*.test.ts", "*.test.tsx")):
            tests.append(path.removeprefix("frontend/"))
        else:
            sources.append(path.removeprefix("frontend/"))
    steps = []
    if name == "backend":
        if sources and not (root / ".testmondata").is_file():
            return [
                replace(
                    suite,
                    argv=(*suite.argv, "--testmon", "--testmon-noselect", "--blocking-guard-expect-all-exemptions"),
                )
            ]
        if sources:
            steps.append(Step((*PYTEST, "--testmon"), junit_runner="pytest", testmon_sources=tuple(sources)))
        if tests:
            steps.append(Step((*PYTEST, *tests), junit_runner="pytest"))
    else:
        vitest = ("pnpm", "exec", "vitest")
        if sources:
            steps.append(Step((*vitest, "related", "--run", "--maxWorkers=1", *sources), FRONT, junit_runner="vitest"))
        if tests:
            steps.append(Step((*vitest, "run", "--maxWorkers=1", *tests), FRONT, junit_runner="vitest"))
    return steps or [suite]


def _source_fshas(path: Path) -> set[str]:
    """计算与 testmon 兼容的文件指纹（覆盖 git blob 与 read_source_sha 两种格式）。"""
    raw = path.read_bytes()
    git_blob = hashlib.sha1(b"blob %d\0" % len(raw) + raw, usedforsecurity=False).hexdigest()
    normalized = raw.replace(b"\f", b" ").replace(b"\r\n", b"\n")
    text = normalized.decode("utf-8", "replace")
    testmon_blob = hashlib.sha1(b"blob %d\0" % len(text) + normalized, usedforsecurity=False).hexdigest()
    return {git_blob, testmon_blob}


def _testmon_covers_current_sources(sources: Iterable[str], root: Path = ROOT) -> bool:
    """确认改动源码均已在 .testmondata 中以当前内容通过覆盖且库中无失败用例。"""
    source_list = list(sources)
    db_path = root / ".testmondata"
    if not source_list or not db_path.is_file():
        return False
    try:
        with contextlib.closing(sqlite3.connect(db_path)) as con:
            if con.execute("SELECT 1 FROM test_execution WHERE failed != 0 LIMIT 1").fetchone():
                return False
            rows = con.execute(
                """
                SELECT DISTINCT f.filename, f.fsha
                FROM file_fp f
                JOIN test_execution_file_fp tef ON tef.fingerprint_id = f.id
                JOIN test_execution te ON te.id = tef.test_execution_id
                WHERE te.failed = 0 AND f.fsha IS NOT NULL
                """
            ).fetchall()
    except sqlite3.Error:
        return False
    recorded: dict[str, set[str]] = {}
    for filename, fsha in rows:
        recorded.setdefault(Path(filename).as_posix(), set()).add(fsha)
    for source in source_list:
        try:
            expected = _source_fshas(root / source)
        except OSError:
            return False
        if not (recorded.get(source, set()) & expected):
            return False
    return True


def _run_selected(step: Step) -> tuple[int, bool]:
    """运行定向测试并返回 (退出码, 是否实际执行了至少一条用例)。"""
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "selected.xml"
        options = (
            ("--passWithNoTests", "--reporter=default", "--reporter=junit", f"--outputFile.junit={report}")
            if step.junit_runner == "vitest"
            else (f"--junitxml={report}",)
        )
        code = _execute(replace(step, argv=(*step.argv, *options)))
        if code:
            return code, False
        ran_tests = report.is_file() and ET.parse(report).find(".//testcase") is not None
        return 0, ran_tests


def _run_quick_tests(name: str, paths: list[str], root: Path = ROOT) -> int:
    for step in select_tests(name, paths, root):
        if step.heavy:
            return _execute(step)
        code, ran_tests = _run_selected(step)
        if code == 0 and ran_tests:
            continue
        if code == 0 and step.testmon_sources and _testmon_covers_current_sources(step.testmon_sources, root):
            print(f"[gate] {name} 增量缓存已覆盖当前改动，无需重跑", flush=True)
            continue
        if code in {0, _PYTEST_NO_TESTS_COLLECTED}:
            print(f"[gate] {name} 零命中，运行完整测试", flush=True)
            return _execute(FULL_SUITES[name])
        return code
    return 0


def run_domains(domains: list[str], paths: list[str], complete: set[str], root: Path = ROOT) -> int:
    started = time.monotonic()
    for name in domains:
        steps = DOMAINS[name]
        quick = name in FULL_SUITES and name not in complete
        for step in (step for step in steps if not (quick and step.heavy)):
            if code := _execute(step):
                return code
        if quick and (code := _run_quick_tests(name, paths, root)):
            return code
    print(f"[gate] {' '.join(domains)} 全部通过，用时 {time.monotonic() - started:.0f}s", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domains", nargs="*", metavar="DOMAIN", help="|".join(DOMAINS))
    parser.add_argument(
        "--changed",
        nargs="?",
        const="origin/main",
        metavar="BASE",
        help="按相对 BASE（默认 origin/main）的改动选域，可与点名的域合并",
    )
    parser.add_argument("--list", action="store_true", help="列出各域的触发路径与步骤后退出")
    parser.add_argument("--full", action="store_true", help="完整运行所有命中域")
    args = parser.parse_args(argv)
    if args.list:
        for name, steps in DOMAINS.items():
            triggers = TRIGGERS.get(name)
            print(f"{name}    # {' '.join(triggers) if triggers else '按改动选域时总是跑'}")
            for step in steps:
                print(f"  {step.describe()}")
        return 0
    unknown = [name for name in args.domains if name not in DOMAINS]
    if unknown:
        parser.error(f"未知的域：{', '.join(unknown)}；可选 {', '.join(DOMAINS)}")
    selected: set[str] = set(args.domains)
    paths: list[str] = []
    if args.changed is None and not args.domains:
        args.changed = "origin/main"
    if args.changed is not None:
        try:
            paths = changed_paths(args.changed)
        except subprocess.CalledProcessError as error:
            parser.error(f"读取相对 {args.changed} 的改动失败：{error.stderr.strip()}")
        picked = select_domains(paths)
        print(f"[gate] 相对 {args.changed} 改动 {len(paths)} 个文件，命中域：{' '.join(picked)}", flush=True)
        selected.update(picked)
    domains = [name for name in DOMAINS if name in selected]
    return run_domains(domains, paths, selected if args.full else set(args.domains))


if __name__ == "__main__":
    sys.exit(main())
