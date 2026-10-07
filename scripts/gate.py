#!/usr/bin/env python3
"""按域运行完整质量闸门，整机同一时刻只跑一份。

    uv run python scripts/gate.py backend frontend
    uv run python scripts/gate.py --list

完整闸门里的 pytest 与 vitest 都按整机核数并行；几个 worktree 同时跑时互相抢核，
vitest 的 jsdom 用例会先超时。本脚本用机器级文件锁把各次闸门排成队：谁拿到锁谁独占
整机跑完，排队的只打印等待信息。各域的命令只在这里定义一次，AGENTS.md 只写域名。

锁文件默认在用户目录下（同一台机器上的所有 worktree 都看得到），`ARCREEL_GATE_LOCK`
可改路径。命令任一失败即停止并以其退出码退出，与 `&&` 串联一致。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
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
        Step((*UV, "ruff", "format", ".")),
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
    lock = portalocker.Lock(LOCK_PATH, mode="a+")
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
    parser.add_argument("--list", action="store_true", help="列出各域的步骤后退出")
    args = parser.parse_args(argv)
    if args.list:
        for name, steps in DOMAINS.items():
            print(name)
            for step in steps:
                print(f"  {step.describe()}")
        return 0
    domains: list[str] = list(dict.fromkeys(args.domains))
    if not domains:
        parser.error("至少给一个域名，或用 --list 查看")
    unknown = [name for name in domains if name not in DOMAINS]
    if unknown:
        parser.error(f"未知的域：{', '.join(unknown)}；可选 {', '.join(DOMAINS)}")
    return run_domains(domains)


if __name__ == "__main__":
    sys.exit(main())
