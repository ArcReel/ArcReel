"""一次性 PoC：验证 SDK 0.1.80 Sandbox 行为假设。

执行：uv run python scripts/dev/sandbox_poc.py
输出：JSON 报告到 stdout + 文件 docs/superpowers/specs/2026-05-12-agent-sandbox-design.poc-report.json

PoC 完成后此脚本应从代码库删除。
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import SandboxSettings


@dataclass
class PoCResult:
    name: str
    platform: str
    expected: str
    actual: str
    passed: bool
    notes: str = ""


@dataclass
class PoCReport:
    platform: str
    sandbox_tool: str
    results: list[PoCResult] = field(default_factory=list)


def detect_sandbox_tool() -> str:
    if platform.system() == "Darwin":
        return "sandbox-exec" if shutil.which("sandbox-exec") else "missing"
    if shutil.which("bwrap"):
        return "bwrap"
    return "missing"


def in_docker() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        cg = Path("/proc/1/cgroup").read_text()
        return "docker" in cg or "podman" in cg
    except OSError:
        return False


async def run_agent_command(command: str, env_overrides: dict[str, str]) -> str:
    """跑一次 sandboxed bash 命令，返回原始输出文本。"""
    cwd = Path(__file__).resolve().parents[2] / "projects" / "_poc_dummy"
    cwd.mkdir(parents=True, exist_ok=True)

    options = ClaudeAgentOptions(
        cwd=str(cwd),
        allowed_tools=["Bash"],
        sandbox=SandboxSettings(
            enabled=True,
            autoAllowBashIfSandboxed=True,
            enableWeakerNestedSandbox=in_docker(),
        ),
        env=env_overrides,
        max_turns=2,
    )
    output_chunks: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"Run this bash command and return raw output: {command}")
        async for msg in client.receive_response():
            output_chunks.append(repr(msg))
    return "\n".join(output_chunks)


async def main() -> None:
    report = PoCReport(platform=platform.system(), sandbox_tool=detect_sandbox_tool())
    poc_token = "POC_TOKEN_DO_NOT_USE_IN_PROD"

    # PoC #1: options.env 是否透传到 Bash 子进程
    try:
        output = await run_agent_command(
            command=f"env | grep {poc_token} || echo NOT_FOUND",
            env_overrides={"ANTHROPIC_API_KEY": poc_token},
        )
        leaked = poc_token in output and "NOT_FOUND" not in output
        report.results.append(
            PoCResult(
                name="PoC#1 options.env leaks to bash subprocess",
                platform=report.platform,
                expected="NOT_FOUND (env should NOT be inherited)",
                actual="LEAKED" if leaked else "isolated",
                passed=not leaked,
                notes="If leaked: spec needs PreToolUse Bash hook to strip ANTHROPIC_*",
            )
        )
    except Exception as exc:  # noqa: BLE001
        report.results.append(
            PoCResult(
                name="PoC#1 options.env leaks to bash subprocess",
                platform=report.platform,
                expected="run",
                actual=f"error: {exc}",
                passed=False,
            )
        )

    # PoC #2: sensitive file read denied (uses settings.json deny rules)
    # PoC #3: sandbox + autoAllow lets ls / jq / python -c through
    # PoC #4: curl to external domain works
    # PoC #5: write to /app/lib/test.py is denied (sandbox cwd-only + Edit deny)
    # PoC #6: enableWeakerNestedSandbox in Docker

    # 上述 #2-#6 需要在真实环境中手动运行，本脚本仅产出 #1 自动化结果。
    # 在执行 task 0.2 时手动跑剩余项并填入 report。

    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
