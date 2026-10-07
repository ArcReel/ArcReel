#!/usr/bin/env python3
"""按域运行完整质量闸门，整机同一时刻只跑一份。

    uv run python scripts/gate.py --changed            # 按相对 origin/main 的改动选域
    uv run python scripts/gate.py backend frontend     # 直接点名域
    uv run python scripts/gate.py --list               # 各域的触发路径与步骤

完整闸门里的 pytest 与 vitest 都按整机核数并行；几个 worktree 同时跑时互相抢核，
vitest 的 jsdom 用例会先超时。本脚本用机器级文件锁把各次闸门排成队：谁拿到锁谁独占
整机跑完，排队的只打印等待信息。各域的命令与触发路径只在这里定义一次。

锁文件默认在用户目录下（同一台机器上的所有 worktree 都看得到），`ARCREEL_GATE_LOCK`
可改路径。命令任一失败即停止并以其退出码退出，与 `&&` 串联一致。
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import portalocker

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = Path(os.environ.get("ARCREEL_GATE_LOCK") or Path.home() / ".cache" / "arcreel" / "gate.lock")


@dataclass(frozen=True)
class Step:
    argv: tuple[str, ...]
    cwd: Path = ROOT

    def describe(self) -> str:
        command = " ".join(self.argv)
        return command if self.cwd == ROOT else f"{command}    # cwd={self.cwd.relative_to(ROOT)}"


UV = ("uv", "run")
# 域名 → 步骤。`-n 4 --dist loadfile` 的取值理由见 pyproject.toml 的 pytest 配置注释。
DOMAINS: dict[str, tuple[Step, ...]] = {
    "backend": (
        Step((*UV, "ruff", "check", ".")),
        Step((*UV, "ruff", "format", "--check", ".")),
        Step((*UV, "basedpyright", "--warnings")),
        Step((*UV, "lint-imports")),
        Step((*UV, "deptry", "lib", "server", "alembic", "scripts", "tests")),
        Step((*UV, "python", "-m", "pytest", "-n", "4", "--dist", "loadfile")),
    ),
    "market-core": (
        Step((*UV, "deptry", "src", "tests"), ROOT / "packages" / "arcreel-market-core"),
        Step((*UV, "python", "-m", "pytest"), ROOT / "packages" / "arcreel-market-core"),
    ),
    "tests": (Step((*UV, "python", "scripts/audit_tests.py", "--check")),),
    "conventions": (Step((*UV, "python", "scripts/audit_conventions.py", "--check")),),
    "workflows": (
        Step((*UV, "pre-commit", "run", "--all-files", "actionlint")),
        Step((*UV, "pre-commit", "run", "--all-files", "zizmor")),
    ),
    "frontend": (Step(("pnpm", "check"), ROOT / "frontend"),),
    "website": (Step(("pnpm", "check"), ROOT / "website"),),
}

# 域 → 触发路径，fnmatch 通配（`*` 也跨目录分隔符）。与 CI 的 .github/actions/domain-filter/action.yml
# 对照维护：CI 的 backend 域对应这里的 backend 与 market-core，workflow 域对应 workflows；
# CI 的 docker 域（Dockerfile、public/）没有本地闸门。tests 与 conventions 两个域是秒级的
# 结构审计，触发条件含「新增豁免注释」这类看路径判断不了的情况，按改动选域时总是跑。
TRIGGERS: dict[str, tuple[str, ...]] = {
    "backend": (
        "lib/*",
        "server/*",
        "alembic/*",
        "alembic.ini",
        "scripts/*",
        "tests/*",
        "agent_runtime_profile/*",
        "packages/*",
        "pyproject.toml",
        "uv.lock",
        ".gitignore",  # ruff 的文件发现尊重 gitignore
        # 后端契约测试直接读取的前端源文件
        "frontend/src/i18n/*/dashboard.ts",
        "frontend/src/i18n/*/events.ts",
        "frontend/src/types/workflow.ts",
        "frontend/src/data/example-templates/*",
    ),
    "market-core": ("packages/arcreel-market-core/*",),
    "workflows": (".github/*", ".pre-commit-config.yaml"),
    "frontend": ("frontend/*",),
    # website-checks 同时跑翻译 lockfile 的缺译与滞后报告，CONTRIBUTING 与两个 README 在其特例映射内
    "website": ("website/*", "CONTRIBUTING.md", "README.md", "README.en.md", ".claude/skills/translate-docs/*"),
}
ALWAYS_WHEN_CHANGED = ("tests", "conventions")


def select_domains(paths: Iterable[str]) -> list[str]:
    """按改动路径选域，顺序与 DOMAINS 一致；秒级审计域总是入选。"""
    changed = list(paths)
    hit = {
        name
        for name, patterns in TRIGGERS.items()
        if any(fnmatch.fnmatchcase(path, pattern) for path in changed for pattern in patterns)
    }
    hit.update(ALWAYS_WHEN_CHANGED)
    return [name for name in DOMAINS if name in hit]


def _vcs(root: Path, *args: str) -> str:
    # 路径按 UTF-8 解码，不随平台区域编码变；非 UTF-8 字节用 surrogateescape 保留原样。
    completed = subprocess.run(
        ("git", *args),
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
    # 持有者说明含非 ASCII 字符，读写两端都显式用 UTF-8，避开 Windows 的区域默认编码。
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
    code = subprocess.run(step.argv, cwd=step.cwd, check=False).returncode
    print(f"[gate] {'ok' if code == 0 else f'exit {code}'} in {time.monotonic() - started:.0f}s", flush=True)
    return code


def run_domains(domains: list[str]) -> int:
    steps = [step for name in domains for step in DOMAINS[name]]
    lock = _acquire_gate_lock()
    try:
        _write_holder(lock.fh, f"pid {os.getpid()} · {ROOT} · {' '.join(domains)}")
        started = time.monotonic()
        for step in steps:
            code = _run(step)
            if code != 0:
                return code
        print(f"\n[gate] {' '.join(domains)} 全部通过，用时 {time.monotonic() - started:.0f}s", flush=True)
        return 0
    finally:
        _write_holder(lock.fh, "")
        lock.release()


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
    if args.changed is not None:
        try:
            paths = changed_paths(args.changed)
        except subprocess.CalledProcessError as error:
            parser.error(f"读取相对 {args.changed} 的改动失败：{error.stderr.strip()}")
        picked = select_domains(paths)
        print(f"[gate] 相对 {args.changed} 改动 {len(paths)} 个文件，命中域：{' '.join(picked)}", flush=True)
        selected.update(picked)
    domains = [name for name in DOMAINS if name in selected]
    if not domains:
        parser.error("至少给一个域名或 --changed，或用 --list 查看")
    return run_domains(domains)


if __name__ == "__main__":
    sys.exit(main())
