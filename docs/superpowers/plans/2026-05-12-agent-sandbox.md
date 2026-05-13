# Agent 沙箱化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 claude-agent-sdk 0.1.80 原生 Bash 沙箱 + provider secrets 全面下线 `os.environ`，满足 spec 中四条安全红线。

**Architecture:** 四层防线 — 父进程 env 净化（L1 + 启动 assertion）、SDK 子进程 env 注入（L2 通过 `options.env`）、Bash 沙箱（L3，Seatbelt/bwrap + `autoAllowBashIfSandboxed`）、permission rules + PreToolUse hook 双层文件围栏（L4）。

**Tech Stack:** Python 3.12 + FastAPI + claude-agent-sdk 0.1.80 + pytest + uv + ruff。

**Spec:** [`docs/superpowers/specs/2026-05-12-agent-sandbox-design.md`](../specs/2026-05-12-agent-sandbox-design.md)

---

## Phase 0 — PoC 前置调研

PoC 在所有改造之前执行，验证 spec 中 6 个技术假设。结果归档到 `docs/superpowers/specs/2026-05-12-agent-sandbox-design.poc-report.md`，作为后续 task 决策依据。

### Task 0.1: 编写 PoC 脚本

**Files:**
- Create: `scripts/dev/sandbox_poc.py`

- [ ] **Step 1: 创建 PoC 脚本框架**

```python
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
```

- [ ] **Step 2: 运行 PoC 验证语法**

Run: `uv run python -c "import ast; ast.parse(open('scripts/dev/sandbox_poc.py').read())"`
Expected: 无输出（语法正确）

- [ ] **Step 3: 不实际跑 SDK 调用（task 0.2 才跑），只 commit 脚本**

```bash
git add scripts/dev/sandbox_poc.py
git commit -m "chore(sandbox): add PoC script for SDK 0.1.80 sandbox behavior"
```

---

### Task 0.2: 执行 PoC 并生成报告

**Files:**
- Create: `docs/superpowers/specs/2026-05-12-agent-sandbox-design.poc-report.md`

- [ ] **Step 1: 跑 PoC #1（自动化）**

Run: `uv run python scripts/dev/sandbox_poc.py > /tmp/poc1.json`
Expected: stdout 含 `"passed": true` 或 `false`；记录结果

- [ ] **Step 2: 手动跑 PoC #2 — 敏感文件读拒**

启动开发服务器：
```bash
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241 &
```

打开任意项目，让 agent 跑：
- `Bash("cat /app/.env")` → 期望被 sandbox 拒（注意：此 PoC 需 spec deny rules 已部分接入；如尚未接入，记录 sandbox 默认行为）
- `Bash("cat /app/projects/.arcreel.db")` → 同上

记录 actual 输出。

- [ ] **Step 3: 手动跑 PoC #3 — autoAllow Bash 放行**

agent 跑 `Bash("ls")`、`Bash("jq --version")`、`Bash("python -c 'print(1)'")` → 期望全部放行无 prompt。

- [ ] **Step 4: 手动跑 PoC #4 — curl 任意域名**

agent 跑 `Bash("curl -s https://example.com")` → 期望返回 HTML 内容。

- [ ] **Step 5: 手动跑 PoC #5 — cwd 外写禁**

agent 跑 `Bash("echo x > /app/lib/test.py")` → 期望 sandbox 拒（Operation not permitted）。

- [ ] **Step 6: 手动跑 PoC #6 — Docker 弱沙箱**

在 Docker 容器内重复 PoC #3-#5。验证 `enableWeakerNestedSandbox=True` 下 bwrap 正常工作。

- [ ] **Step 7: 写报告文件**

Create `docs/superpowers/specs/2026-05-12-agent-sandbox-design.poc-report.md`：

```markdown
# Agent 沙箱化 PoC 验证报告

> 日期：YYYY-MM-DD
> 平台：macOS 26.x.x / Linux ubuntu-24 / Docker (Debian Trixie)
> SDK 版本：claude-agent-sdk-python 0.1.80

## 结果汇总

| # | 平台 | 期望 | 实际 | 通过 |
|---|---|---|---|---|
| 1 | macOS | options.env NOT inherited by bash | <填> | ✅/❌ |
| 1 | Linux | options.env NOT inherited by bash | <填> | ✅/❌ |
| 2 | macOS | cat .env denied | <填> | ✅/❌ |
| ... | | | | |

## 分析与决策

- PoC #1 阳性 → spec 增补 PreToolUse Bash hook 剥离 ANTHROPIC_* env（见 Task 4.3.alt）
- PoC #1 阴性 → 设计闭合，跳过 Task 4.3.alt
- 其他 PoC 异常项 → 在此说明并调整 plan

## 后续动作

- [填关联 task id]
```

- [ ] **Step 8: 提交报告**

```bash
git add docs/superpowers/specs/2026-05-12-agent-sandbox-design.poc-report.md
git commit -m "docs(sandbox): PoC validation report"
```

---

## Phase 1 — env_keys 模块 + 启动检测

### Task 1.1: 创建 `lib/config/env_keys.py` 集中常量

**Files:**
- Create: `lib/config/env_keys.py`
- Test: `tests/config/__init__.py`, `tests/config/test_env_keys.py`

- [ ] **Step 1: 创建测试目录**

```bash
mkdir -p tests/config
touch tests/config/__init__.py
```

- [ ] **Step 2: 写失败测试**

Create `tests/config/test_env_keys.py`：

```python
"""env_keys 模块集合不变量测试。"""

from __future__ import annotations

from lib.config.env_keys import (
    ANTHROPIC_ENV_KEYS,
    AUTH_ALLOWED_KEYS,
    OTHER_PROVIDER_ENV_KEYS,
    PROVIDER_SECRET_KEYS,
)


def test_provider_secret_keys_is_subset_of_all_provider_keys():
    """密钥集合必须在「其他 provider env」的并集中（防漏列）。"""
    for k in PROVIDER_SECRET_KEYS:
        if k == "ANTHROPIC_API_KEY":
            assert k in ANTHROPIC_ENV_KEYS
        else:
            assert k in OTHER_PROVIDER_ENV_KEYS, (
                f"密钥 {k} 必须出现在 OTHER_PROVIDER_ENV_KEYS 中"
            )


def test_secret_keys_are_disjoint_from_auth_whitelist():
    """密钥不能出现在 AUTH 白名单 — 否则启动断言会冲突。"""
    overlap = PROVIDER_SECRET_KEYS & AUTH_ALLOWED_KEYS
    assert not overlap, f"密钥与 AUTH 白名单冲突: {overlap}"


def test_anthropic_keys_complete():
    """ANTHROPIC_ENV_KEYS 必须覆盖 SDK 子进程读取的全部 ANTHROPIC_* + CLAUDE_CODE_*。"""
    required = {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    }
    assert required <= set(ANTHROPIC_ENV_KEYS)
```

- [ ] **Step 3: 跑测试，期望失败**

Run: `uv run pytest tests/config/test_env_keys.py -v`
Expected: FAIL，ModuleNotFoundError

- [ ] **Step 4: 实现 `lib/config/env_keys.py`**

```python
"""集中维护 provider / AUTH 相关的环境变量 key 清单。

唯一真相源 — 凡是涉及 os.environ 名单的代码都从这里 import。
"""

from __future__ import annotations

# —— SDK 子进程需要的 Anthropic env keys（通过 options.env 注入）——
ANTHROPIC_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)

# —— 其他 provider env keys（options.env 用空值覆盖兜底）——
OTHER_PROVIDER_ENV_KEYS: tuple[str, ...] = (
    "ARK_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "VIDU_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GEMINI_BASE_URL",
    "GEMINI_IMAGE_MODEL",
    "GEMINI_VIDEO_MODEL",
    "GEMINI_IMAGE_BACKEND",
    "GEMINI_VIDEO_BACKEND",
    "VERTEX_GCS_BUCKET",
    "FILE_SERVICE_BASE_URL",
    "DEFAULT_VIDEO_PROVIDER",
)

# —— 启动断言：真密钥子集，命中即 fail-fast（spec §7.2）——
PROVIDER_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ARK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "VIDU_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
)

# —— `_load_project_env` 白名单：load_dotenv 后只保留这些前缀/精确名 ——
AUTH_ALLOWED_PREFIXES: tuple[str, ...] = ("AUTH_", "ASSISTANT_", "ARCREEL_")
AUTH_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"DATABASE_URL", "LOG_LEVEL", "AI_ANIME_PROJECTS"}
)


def is_provider_env_key(name: str) -> bool:
    """判断给定 env key 是否属于 provider 相关。"""
    return name in ANTHROPIC_ENV_KEYS or name in OTHER_PROVIDER_ENV_KEYS
```

- [ ] **Step 5: 跑测试，期望通过**

Run: `uv run pytest tests/config/test_env_keys.py -v`
Expected: 3 个测试全部 PASS

- [ ] **Step 6: ruff 检查 + commit**

```bash
uv run ruff check lib/config/env_keys.py tests/config/
uv run ruff format lib/config/env_keys.py tests/config/
git add lib/config/env_keys.py tests/config/
git commit -m "feat(config): 集中维护 provider/AUTH env keys 清单"
```

---

### Task 1.2: 启动断言 `assert_no_provider_secrets_in_environ()`

**Files:**
- Modify: `server/app.py` (新增模块级函数 + import)
- Test: `tests/server/test_startup_assertions.py`

- [ ] **Step 1: 写失败测试**

Create `tests/server/test_startup_assertions.py`：

```python
"""启动断言测试 — 父进程 env 不得含 provider 密钥。"""

from __future__ import annotations

import os

import pytest

from server.app import assert_no_provider_secrets_in_environ


def _clear_secret_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "ANTHROPIC_API_KEY",
        "ARK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "VIDU_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(k, raising=False)


def test_clean_environ_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_secret_envs(monkeypatch)
    assert_no_provider_secrets_in_environ()  # no raise


@pytest.mark.parametrize(
    "leaked_key",
    [
        "ANTHROPIC_API_KEY",
        "ARK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "VIDU_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ],
)
def test_any_single_secret_triggers_raise(
    monkeypatch: pytest.MonkeyPatch, leaked_key: str
) -> None:
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv(leaked_key, "leaked-value")
    with pytest.raises(RuntimeError, match="SECURITY"):
        assert_no_provider_secrets_in_environ()


def test_empty_string_value_not_treated_as_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    """空字符串不算泄漏（os.environ.pop 后 SDK 子进程会跳过空值）。"""
    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert_no_provider_secrets_in_environ()  # 空值不 raise
```

- [ ] **Step 2: 跑测试，期望失败**

Run: `uv run pytest tests/server/test_startup_assertions.py -v`
Expected: FAIL，ImportError (无 assert_no_provider_secrets_in_environ)

- [ ] **Step 3: 在 `server/app.py` 顶部添加函数**

在 `from lib.logging_config import setup_logging` 之前加：

```python
from lib.config.env_keys import PROVIDER_SECRET_KEYS


def assert_no_provider_secrets_in_environ() -> None:
    """父进程禁止持有任何 provider 密钥。违反即 fail-fast。

    安全红线：spec §7.2。Bash 沙箱子进程通过 fork 继承父 env，
    所以父进程必须先把 provider secrets 全部下线到 DB。
    """
    leaked = sorted(k for k in PROVIDER_SECRET_KEYS if os.environ.get(k))
    if leaked:
        raise RuntimeError(
            f"SECURITY: 父进程 os.environ 含 provider 密钥: {leaked}. "
            "请到 WebUI 系统配置页填写，并从 env / .env 中移除对应条目。"
        )
```

- [ ] **Step 4: 跑测试，期望通过**

Run: `uv run pytest tests/server/test_startup_assertions.py -v`
Expected: 8 个测试 PASS

- [ ] **Step 5: commit**

```bash
uv run ruff check server/app.py tests/server/test_startup_assertions.py
uv run ruff format server/app.py tests/server/test_startup_assertions.py
git add server/app.py tests/server/test_startup_assertions.py
git commit -m "feat(server): 启动断言 assert_no_provider_secrets_in_environ"
```

---

### Task 1.3: Sandbox 工具可用性检测 `check_sandbox_available()`

**Files:**
- Modify: `server/app.py` (新增模块级函数)
- Test: `tests/server/test_startup_assertions.py` (扩展)

- [ ] **Step 1: 追加测试**

在 `tests/server/test_startup_assertions.py` 末尾追加：

```python
import platform

from server.app import check_sandbox_available


def test_sandbox_available_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None)
    check_sandbox_available()  # no raise


def test_sandbox_missing_macos_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="SANDBOX_UNAVAILABLE"):
        check_sandbox_available()


def test_sandbox_available_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    check_sandbox_available()  # no raise


def test_sandbox_missing_linux_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="bubblewrap"):
        check_sandbox_available()
```

- [ ] **Step 2: 跑测试，期望失败**

Run: `uv run pytest tests/server/test_startup_assertions.py::test_sandbox_available_macos -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 在 `server/app.py` 添加函数**

```python
import platform
import shutil


def check_sandbox_available() -> None:
    """启动期检测 sandbox 工具可用性，缺失即 fail-fast。

    spec §7.1 step [2]。沿用项目策略：硬失败，不降级。
    """
    system = platform.system()
    if system == "Darwin":
        if shutil.which("sandbox-exec") is None:
            raise RuntimeError(
                "SANDBOX_UNAVAILABLE on macOS\n"
                "  sandbox-exec: not found in PATH (should be system-installed)\n"
                "Required for ArcReel agent runtime."
            )
        return
    if system == "Linux":
        if shutil.which("bwrap") is None:
            raise RuntimeError(
                "SANDBOX_UNAVAILABLE on linux\n"
                "  bwrap: not found in PATH\n"
                "Required for ArcReel agent runtime. Install bubblewrap:\n"
                "  Ubuntu/Debian: sudo apt install bubblewrap\n"
                "  Arch:          sudo pacman -S bubblewrap"
            )
        return
    raise RuntimeError(
        f"SANDBOX_UNAVAILABLE on {system}\n"
        "Agent sandbox supports macOS / Linux only."
    )
```

- [ ] **Step 4: 跑测试，期望通过**

Run: `uv run pytest tests/server/test_startup_assertions.py -v`
Expected: 全部 PASS

- [ ] **Step 5: commit**

```bash
uv run ruff check server/app.py tests/server/test_startup_assertions.py
uv run ruff format server/app.py tests/server/test_startup_assertions.py
git add server/app.py tests/server/test_startup_assertions.py
git commit -m "feat(server): 启动期 sandbox 可用性硬检测"
```

---

### Task 1.4: Docker 环境检测 `detect_docker_environment()`

**Files:**
- Modify: `server/app.py`
- Test: `tests/server/test_startup_assertions.py` (扩展)

- [ ] **Step 1: 追加测试**

```python
from server.app import detect_docker_environment


def test_detect_docker_via_dockerenv(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake_dockerenv = tmp_path / ".dockerenv"
    fake_dockerenv.touch()
    monkeypatch.setattr("server.app._DOCKERENV_PATH", fake_dockerenv)
    monkeypatch.setattr("server.app._CGROUP_PATH", tmp_path / "nonexistent")
    assert detect_docker_environment() is True


def test_detect_docker_via_cgroup(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake_cgroup = tmp_path / "cgroup"
    fake_cgroup.write_text("12:cpu:/docker/abc123\n")
    monkeypatch.setattr("server.app._DOCKERENV_PATH", tmp_path / "nope")
    monkeypatch.setattr("server.app._CGROUP_PATH", fake_cgroup)
    assert detect_docker_environment() is True


def test_detect_no_docker(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("server.app._DOCKERENV_PATH", tmp_path / "nope")
    monkeypatch.setattr("server.app._CGROUP_PATH", tmp_path / "also_nope")
    assert detect_docker_environment() is False
```

- [ ] **Step 2: 跑测试，期望失败**

Run: `uv run pytest tests/server/test_startup_assertions.py -k docker -v`
Expected: FAIL，ImportError

- [ ] **Step 3: 在 `server/app.py` 添加函数与模块级常量**

```python
_DOCKERENV_PATH = Path("/.dockerenv")
_CGROUP_PATH = Path("/proc/1/cgroup")


def detect_docker_environment() -> bool:
    """启动期一次性检测当前是否在 Docker / Podman 容器内。

    用于决定是否启用 `SandboxSettings.enableWeakerNestedSandbox`。
    spec §5.1 / §7.1。
    """
    if _DOCKERENV_PATH.exists():
        return True
    try:
        content = _CGROUP_PATH.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "docker" in content or "podman" in content
```

- [ ] **Step 4: 跑测试，期望通过**

Run: `uv run pytest tests/server/test_startup_assertions.py -v`
Expected: 全部 PASS

- [ ] **Step 5: commit**

```bash
uv run ruff check server/app.py tests/server/test_startup_assertions.py
uv run ruff format server/app.py tests/server/test_startup_assertions.py
git add server/app.py tests/server/test_startup_assertions.py
git commit -m "feat(server): Docker/Podman 容器环境检测"
```

---

## Phase 2 — secrets 下线 + 启动断言接入

### Task 2.1: 从 lifespan 删除 `sync_anthropic_env` 调用

**Files:**
- Modify: `server/app.py:171-178`

- [ ] **Step 1: 用 grep 确认调用位置**

Run: `grep -n "sync_anthropic_env" server/app.py`
Expected: 行 173 import + 行 176 调用

- [ ] **Step 2: 删除整段代码**

打开 `server/app.py`，把这段：

```python
    # Sync Anthropic DB settings to env vars (Claude Agent SDK reads from os.environ)
    try:
        from lib.config.service import sync_anthropic_env

        async with async_session_factory() as session:
            await sync_anthropic_env(session)
    except Exception as exc:
        logger.warning("DB→env Anthropic config sync failed (non-fatal): %s", exc)
```

替换为（仅留注释占位，sandbox 设计后不再走 env）：

```python
    # NOTE: Anthropic credential 不再写入 os.environ（spec §6.3）。
    # SDK 子进程在 SessionManager._build_options() 阶段通过 options.env 注入。
```

- [ ] **Step 3: 验证启动可跑（不实际启动，跑 import 检查）**

Run: `uv run python -c "from server.app import app; print('ok')"`
Expected: `ok`

- [ ] **Step 4: commit**

```bash
uv run ruff check server/app.py
uv run ruff format server/app.py
git add server/app.py
git commit -m "refactor(server): 停止 lifespan 期间写入 ANTHROPIC_* 到 os.environ"
```

---

### Task 2.2: 删除 routers 中的 `sync_anthropic_env` 调用（4 处）

**Files:**
- Modify: `server/routers/agent_config.py:205, 232, 273` (+ import line 20)
- Modify: `server/routers/system_config.py:380` (+ import line 28)

- [ ] **Step 1: 列出每个调用点**

Run: `grep -n "sync_anthropic_env" server/routers/agent_config.py server/routers/system_config.py`
Expected: 5 行（agent_config.py 4 行含 import；system_config.py 2 行含 import）

- [ ] **Step 2: 删除 agent_config.py 中的 4 处**

打开 `server/routers/agent_config.py`：

行 20 删除：
```python
from lib.config.service import sync_anthropic_env
```

行 205 删除 `await sync_anthropic_env(session)`（保留外层逻辑）
行 232 删除 `await sync_anthropic_env(session)`
行 273 删除 `await sync_anthropic_env(session)`

如果删除后函数体空，加 `pass` 占位。

- [ ] **Step 3: 删除 system_config.py 中的 1 处**

打开 `server/routers/system_config.py`：

行 28 修改 import，从：
```python
from lib.config.service import (
    sync_anthropic_env,
    ...其他名字...
)
```
删除 `sync_anthropic_env,` 一行。

行 380 删除 `await sync_anthropic_env(session)`。

- [ ] **Step 4: 验证 import 不残留**

Run: `grep -rn "sync_anthropic_env" server/`
Expected: 无输出（只剩 lib/config/service.py 中的定义本身，将在 Task 2.3 删除）

- [ ] **Step 5: 运行 routers 相关测试**

Run: `uv run pytest tests/server/ -v 2>&1 | tail -20`
Expected: 现有测试全绿（无新增 failure）

- [ ] **Step 6: commit**

```bash
uv run ruff check server/routers/agent_config.py server/routers/system_config.py
uv run ruff format server/routers/agent_config.py server/routers/system_config.py
git add server/routers/agent_config.py server/routers/system_config.py
git commit -m "refactor(routers): 删除 credential 写入后的 sync_anthropic_env 调用"
```

---

### Task 2.3: 改造 `sync_anthropic_env` → `build_anthropic_env_dict`

**Files:**
- Modify: `lib/config/service.py:29-75`
- Test: `tests/config/test_anthropic_env_dict.py`

- [ ] **Step 1: 写测试 — 函数返回 dict 不写 environ**

Create `tests/config/test_anthropic_env_dict.py`：

```python
"""build_anthropic_env_dict 行为测试 — 只读 DB、返回 dict、不写 environ。"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from lib.config.service import build_anthropic_env_dict


@pytest.mark.asyncio
async def test_active_credential_returns_full_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    repo_mock = AsyncMock()
    cred = type(
        "Cred",
        (),
        dict(
            api_key="sk-test",
            base_url="https://api.anthropic.com",
            model="claude-opus-4-7",
            haiku_model="claude-haiku-4-5",
            sonnet_model="claude-sonnet-4-6",
            opus_model="claude-opus-4-7",
            subagent_model="claude-haiku-4-5",
        ),
    )()
    repo_mock.get_active = AsyncMock(return_value=cred)

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )

    result = await build_anthropic_env_dict(session)
    assert result["ANTHROPIC_API_KEY"] == "sk-test"
    assert result["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"
    assert result["ANTHROPIC_MODEL"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_no_active_credential_returns_empty_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    repo_mock = AsyncMock()
    repo_mock.get_active = AsyncMock(return_value=None)

    setting_repo = AsyncMock()
    setting_repo.get_all = AsyncMock(return_value={})

    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )
    monkeypatch.setattr(
        "lib.config.service.SystemSettingRepository", lambda _s: setting_repo
    )

    result = await build_anthropic_env_dict(session)
    assert result["ANTHROPIC_API_KEY"] == ""
    assert result["ANTHROPIC_BASE_URL"] == ""


@pytest.mark.asyncio
async def test_function_does_not_touch_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec §6.3 红线：build 函数不能写 os.environ。"""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    baseline = dict(os.environ)

    session = AsyncMock()
    repo_mock = AsyncMock()
    cred = type("Cred", (), dict(api_key="sk-test", base_url="x", model="y", haiku_model=None, sonnet_model=None, opus_model=None, subagent_model=None))()
    repo_mock.get_active = AsyncMock(return_value=cred)
    monkeypatch.setattr(
        "lib.db.repositories.agent_credential_repo.AgentCredentialRepository",
        lambda _s: repo_mock,
    )

    await build_anthropic_env_dict(session)
    assert dict(os.environ) == baseline, "build 函数禁止改 os.environ"
```

- [ ] **Step 2: 跑测试期望失败**

Run: `uv run pytest tests/config/test_anthropic_env_dict.py -v`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 重写 `lib/config/service.py:29-75`**

把 `sync_anthropic_env` / `_sync_from_settings` / `_apply_env_map` 整段（行 29-75）替换为：

```python
async def build_anthropic_env_dict(session: AsyncSession) -> dict[str, str]:
    """从 DB 读 active credential，返回 {ENV_KEY: value} dict，**不写 os.environ**。

    返回值由 SessionManager._build_provider_env_overrides() 注入到
    ClaudeAgentOptions.env（spec §6.2）。

    双轨期 fallback：active credential 字段为空时从 system_settings 兜底。
    """
    # 局部 import 避免循环依赖
    from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

    repo = AgentCredentialRepository(session)
    cred = await repo.get_active()

    if cred is not None:
        settings = await SystemSettingRepository(session).get_all()
        return {
            "ANTHROPIC_API_KEY": cred.api_key or "",
            "ANTHROPIC_BASE_URL": cred.base_url or "",
            "ANTHROPIC_MODEL": cred.model or settings.get("anthropic_model", "").strip(),
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": cred.haiku_model
            or settings.get("anthropic_default_haiku_model", "").strip(),
            "ANTHROPIC_DEFAULT_SONNET_MODEL": cred.sonnet_model
            or settings.get("anthropic_default_sonnet_model", "").strip(),
            "ANTHROPIC_DEFAULT_OPUS_MODEL": cred.opus_model
            or settings.get("anthropic_default_opus_model", "").strip(),
            "CLAUDE_CODE_SUBAGENT_MODEL": cred.subagent_model
            or settings.get("claude_code_subagent_model", "").strip(),
        }

    # 无 active credential — 回退 system_settings（双轨期兼容）
    settings = await SystemSettingRepository(session).get_all()
    return {
        env_key: settings.get(db_key, "").strip()
        for db_key, env_key in _ANTHROPIC_ENV_MAP.items()
    }
```

注意 `_ANTHROPIC_ENV_MAP`（dict 行 18-26）保留，因为 fallback 路径还要用。

`os` import 在文件顶部如果不再被其他代码使用，删 import（搜一下 service.py 内其他 `os.` 使用确认）。

- [ ] **Step 4: 跑测试期望通过**

Run: `uv run pytest tests/config/test_anthropic_env_dict.py -v`
Expected: 3 个测试 PASS

- [ ] **Step 5: 全模块回归**

Run: `uv run pytest tests/config/ tests/server/ -v 2>&1 | tail -30`
Expected: 全绿

- [ ] **Step 6: commit**

```bash
uv run ruff check lib/config/service.py tests/config/test_anthropic_env_dict.py
uv run ruff format lib/config/service.py tests/config/test_anthropic_env_dict.py
git add lib/config/service.py tests/config/test_anthropic_env_dict.py
git commit -m "refactor(config): sync_anthropic_env → build_anthropic_env_dict (返回 dict，不写 environ)"
```

---

### Task 2.4: `_load_project_env` 加白名单过滤

**Files:**
- Modify: `server/agent_runtime/service.py:1024-1035`
- Test: `tests/agent_runtime/test_load_project_env.py`

- [ ] **Step 1: 写测试**

Create `tests/agent_runtime/test_load_project_env.py`：

```python
"""_load_project_env 加载后白名单过滤行为。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.agent_runtime.service import AssistantService


def test_load_project_env_drops_provider_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AUTH_PASSWORD=admin\n"
        "DATABASE_URL=sqlite:///x.db\n"
        "ANTHROPIC_API_KEY=should-be-dropped\n"
        "ARK_API_KEY=also-dropped\n"
        "GEMINI_API_KEY=dropped-too\n"
        "VIDU_API_KEY=dropped\n"
        "RANDOM_VAR=also-dropped\n"
    )
    for k in ("AUTH_PASSWORD", "DATABASE_URL", "ANTHROPIC_API_KEY", "ARK_API_KEY", "GEMINI_API_KEY", "VIDU_API_KEY", "RANDOM_VAR"):
        monkeypatch.delenv(k, raising=False)

    AssistantService._load_project_env(tmp_path)

    # AUTH/DATABASE 保留
    assert os.environ.get("AUTH_PASSWORD") == "admin"
    assert os.environ.get("DATABASE_URL") == "sqlite:///x.db"

    # provider secrets 被丢
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "ARK_API_KEY" not in os.environ
    assert "GEMINI_API_KEY" not in os.environ
    assert "VIDU_API_KEY" not in os.environ

    # 非白名单 var 被丢
    assert "RANDOM_VAR" not in os.environ


def test_missing_env_file_is_noop(tmp_path: Path) -> None:
    """目录里没有 .env 时不报错。"""
    AssistantService._load_project_env(tmp_path)  # no raise
```

- [ ] **Step 2: 跑测试期望失败**

Run: `uv run pytest tests/agent_runtime/test_load_project_env.py -v`
Expected: 第一个测试 FAIL（provider keys 还在 environ）

- [ ] **Step 3: 改写 `_load_project_env`**

打开 `server/agent_runtime/service.py` 行 1024-1035，替换为：

```python
    @staticmethod
    def _load_project_env(project_root: Path) -> None:
        """Load .env file, then strip everything except AUTH/runtime allowlist.

        spec §5.3：父进程禁止持有 provider secrets。先 load_dotenv 再过滤，
        防止 .env 中遗留的旧 provider key 污染 os.environ。
        """
        env_path = project_root / ".env"
        if env_path.exists():
            try:
                from dotenv import load_dotenv

                load_dotenv(env_path, override=False)
            except ImportError:
                pass

        # —— 过滤非白名单 keys ——
        from lib.config.env_keys import (
            AUTH_ALLOWED_KEYS,
            AUTH_ALLOWED_PREFIXES,
        )

        for key in list(os.environ.keys()):
            if key in AUTH_ALLOWED_KEYS:
                continue
            if any(key.startswith(prefix) for prefix in AUTH_ALLOWED_PREFIXES):
                continue
            # OS 标配 env 不动
            if key in {"PATH", "HOME", "LANG", "LC_ALL", "USER", "SHELL", "PWD", "TMPDIR", "TERM", "TZ"}:
                continue
            # 其他一律剥掉（含 provider secrets）
            os.environ.pop(key, None)
```

注意：这条策略 **激进**（删除所有非白名单 env）。如果项目有其他业务 env（如 `OPENAI_API_KEY` 给某个非 ArcReel 代码用），会被误删。如不放心，可以改为只删 `OTHER_PROVIDER_ENV_KEYS + ANTHROPIC_ENV_KEYS` 的精确名单：

```python
# 保守版替代：只删已知 provider keys
from lib.config.env_keys import ANTHROPIC_ENV_KEYS, OTHER_PROVIDER_ENV_KEYS
for key in ANTHROPIC_ENV_KEYS + OTHER_PROVIDER_ENV_KEYS:
    os.environ.pop(key, None)
```

选保守版（精确删，不误伤），因为白名单可能漏列。把上面"激进"段替换为"保守版"。

- [ ] **Step 4: 用保守版重写**

```python
    @staticmethod
    def _load_project_env(project_root: Path) -> None:
        """Load .env file, then strip known provider env keys.

        spec §5.3：父进程禁止持有 provider secrets。先 load_dotenv 再过滤，
        防止 .env 中遗留的旧 provider key 污染 os.environ。
        """
        env_path = project_root / ".env"
        if env_path.exists():
            try:
                from dotenv import load_dotenv

                load_dotenv(env_path, override=False)
            except ImportError:
                pass

        # —— 把 dotenv 引入的 provider keys 立即移除（保守名单）——
        from lib.config.env_keys import ANTHROPIC_ENV_KEYS, OTHER_PROVIDER_ENV_KEYS

        for key in ANTHROPIC_ENV_KEYS + OTHER_PROVIDER_ENV_KEYS:
            os.environ.pop(key, None)
```

更新测试的最后一条断言（`RANDOM_VAR` 不再被强制移除 — 保守版不动非白名单）：

```python
def test_load_project_env_drops_provider_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # ... 前面同上
    AssistantService._load_project_env(tmp_path)

    assert os.environ.get("AUTH_PASSWORD") == "admin"
    assert os.environ.get("DATABASE_URL") == "sqlite:///x.db"

    # provider keys 被精确删除
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "ARK_API_KEY" not in os.environ
    assert "GEMINI_API_KEY" not in os.environ
    assert "VIDU_API_KEY" not in os.environ

    # 保守版：未列入 provider 名单的 RANDOM_VAR **保留**
    assert os.environ.get("RANDOM_VAR") == "also-dropped"
```

- [ ] **Step 5: 跑测试期望通过**

Run: `uv run pytest tests/agent_runtime/test_load_project_env.py -v`
Expected: 2 个测试 PASS

- [ ] **Step 6: commit**

```bash
uv run ruff check server/agent_runtime/service.py tests/agent_runtime/test_load_project_env.py
uv run ruff format server/agent_runtime/service.py tests/agent_runtime/test_load_project_env.py
git add server/agent_runtime/service.py tests/agent_runtime/test_load_project_env.py
git commit -m "refactor(agent): _load_project_env 加载后立即移除 provider env keys"
```

---

### Task 2.5: 拆 `SystemConfig` 中的 `os.environ` 写入

**Files:**
- Modify: `lib/system_config.py:228, 376-384`

- [ ] **Step 1: 查证当前写入点**

Run: `grep -n "os.environ" lib/system_config.py`
Expected: 228 / 376 / 378 / 384 共 4 行

- [ ] **Step 2: 改 `_baseline_env`（行 228）**

`__init__` 中删除 `_baseline_env` 字段创建：

把：
```python
        self._baseline_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
```

改为：
```python
        # spec §5.3：不再快照 env baseline（os.environ 不再持有 provider secrets）
        self._baseline_env: dict[str, str | None] = {}
```

- [ ] **Step 3: 改 `_restore_or_unset` / `_set_env`（行 376-384）**

把：
```python
    def _restore_or_unset(self, env_key: str) -> None:
        baseline_value = self._baseline_env.get(env_key)
        if baseline_value is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = baseline_value

    def _set_env(self, env_key: str, value: str) -> None:
        os.environ[env_key] = str(value)
```

改为：
```python
    def _restore_or_unset(self, env_key: str) -> None:
        """spec §5.3：不再写 os.environ；保留 noop 兼容旧调用。"""

    def _set_env(self, env_key: str, value: str) -> None:
        """spec §5.3：不再写 os.environ；保留 noop 兼容旧调用。"""
```

- [ ] **Step 4: 跑相关测试**

Run: `uv run pytest tests/ -k system_config -v 2>&1 | tail -20`
Expected: 全绿（若有失败需评估）

- [ ] **Step 5: commit**

```bash
uv run ruff check lib/system_config.py
uv run ruff format lib/system_config.py
git add lib/system_config.py
git commit -m "refactor(system_config): noop 兼容层不再写入 os.environ"
```

---

### Task 2.6: 接入启动断言到 FastAPI lifespan

**Files:**
- Modify: `server/app.py` (lifespan 顶部)

- [ ] **Step 1: 在 lifespan 顶部加 3 个启动检测**

打开 `server/app.py` 找到 `async def lifespan(app: FastAPI):` 函数体，在 `ensure_auth_password()` 之前插入：

```python
    # —— 安全红线检测（spec §7.1）——
    # 顺序：先父进程 env 净化，再 sandbox 可用性，再 docker 检测
    assert_no_provider_secrets_in_environ()
    check_sandbox_available()
    is_docker = detect_docker_environment()
    logger.info("Sandbox runtime: docker=%s", is_docker)

    # 保存到 app.state 供 SessionManager 读取（Task 4.3 使用）
    app.state.in_docker = is_docker
```

- [ ] **Step 2: 跑一次启动验证（如本地 env 干净）**

```bash
# 先确保父 env 没有真密钥
unset ANTHROPIC_API_KEY ARK_API_KEY XAI_API_KEY GEMINI_API_KEY VIDU_API_KEY GOOGLE_APPLICATION_CREDENTIALS

uv run python -c "
from server.app import app
print('lifespan import ok')
"
```
Expected: `lifespan import ok`

- [ ] **Step 3: 跑回归测试**

Run: `uv run pytest tests/server/test_startup_assertions.py -v`
Expected: 全绿

- [ ] **Step 4: commit**

```bash
uv run ruff check server/app.py
uv run ruff format server/app.py
git add server/app.py
git commit -m "feat(server): lifespan 接入 sandbox + secrets 启动检测"
```

---

## Phase 3 — backend env fallback 清理

每个 backend 的 env fallback 拆为独立 task，方便分别 review。

### Task 3.1: 创建统一测试 `test_no_env_fallback.py`

**Files:**
- Create: `tests/backends/__init__.py`, `tests/backends/test_no_env_fallback.py`

- [ ] **Step 1: 准备目录**

```bash
mkdir -p tests/backends
touch tests/backends/__init__.py
```

- [ ] **Step 2: 写测试占位 — 所有 backend 不带 api_key 应 raise**

Create `tests/backends/test_no_env_fallback.py`：

```python
"""所有 provider backend 在缺失 api_key 时必须 raise，不再走 env fallback。

spec §5.4。
"""

from __future__ import annotations

import pytest


def test_ark_shared_no_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    from lib.ark_shared import resolve_ark_api_key

    with pytest.raises(ValueError, match="Ark API Key"):
        resolve_ark_api_key(None)


def test_ark_shared_ignores_env_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """即使 env 里有 ARK_API_KEY，也不应被 fallback 读到。"""
    monkeypatch.setenv("ARK_API_KEY", "should-be-ignored")
    from lib.ark_shared import resolve_ark_api_key

    with pytest.raises(ValueError, match="Ark API Key"):
        resolve_ark_api_key(None)


def test_grok_shared_no_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    from lib.grok_shared import resolve_grok_api_key

    with pytest.raises(ValueError, match="xAI API Key"):
        resolve_grok_api_key(None)


def test_vidu_shared_no_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIDU_API_KEY", raising=False)
    from lib.vidu_shared import resolve_vidu_api_key

    with pytest.raises(ValueError, match="Vidu API Key"):
        resolve_vidu_api_key(None)
```

注意：测试用 `resolve_*_api_key` 公共 helper。如果当前模块只有内联读取（无 helper），后续 task 重构时引入此 helper。

- [ ] **Step 3: 跑测试期望失败**

Run: `uv run pytest tests/backends/test_no_env_fallback.py -v`
Expected: 多个测试 FAIL（行为不符合）

- [ ] **Step 4: 不 commit 测试，先让后续 task 改实现使其通过**

---

### Task 3.2: 清理 `lib/ark_shared.py:21`

**Files:**
- Modify: `lib/ark_shared.py`

- [ ] **Step 1: 重读原文件**

```bash
cat lib/ark_shared.py
```

- [ ] **Step 2: 改写 `resolve_ark_api_key`**

把：
```python
def resolve_ark_api_key(api_key: str | None) -> str:
    resolved = api_key or os.environ.get("ARK_API_KEY")
    if not resolved:
        raise ValueError("...")
    return resolved
```

改为：
```python
def resolve_ark_api_key(api_key: str | None) -> str:
    """spec §5.4：不再读 env fallback；缺失即 raise。"""
    if not api_key:
        raise ValueError("请到系统配置页填写 Ark API Key")
    return api_key
```

如果 `import os` 在此文件不再被其他代码使用，删 import。

- [ ] **Step 3: 跑该 backend 相关测试**

Run: `uv run pytest tests/backends/test_no_env_fallback.py::test_ark_shared_no_api_key_raises tests/backends/test_no_env_fallback.py::test_ark_shared_ignores_env_when_api_key_missing -v`
Expected: 2 个 ark 测试 PASS

- [ ] **Step 4: 跑 ark 相关老测试**

Run: `uv run pytest tests/ -k ark -v 2>&1 | tail -20`
Expected: 现有测试不退化（可能有原本依赖 env fallback 的测试要更新）

- [ ] **Step 5: commit**

```bash
uv run ruff check lib/ark_shared.py
uv run ruff format lib/ark_shared.py
git add lib/ark_shared.py tests/backends/__init__.py tests/backends/test_no_env_fallback.py
git commit -m "refactor(ark): 删除 ARK_API_KEY env fallback，缺失即 raise"
```

---

### Task 3.3: 清理 `lib/grok_shared.py`

**Files:**
- Modify: `lib/grok_shared.py`

- [ ] **Step 1: 重读原文件**

```bash
cat lib/grok_shared.py
```

定位 `XAI_API_KEY` env 读取位置（行 49 附近）。

- [ ] **Step 2: 改写**

把 `os.environ.get("XAI_API_KEY")` 这条路径删除，改为只接受参数：

```python
def resolve_grok_api_key(api_key: str | None) -> str:
    """spec §5.4：不再读 env fallback；缺失即 raise。"""
    if not api_key:
        raise ValueError("请到系统配置页填写 xAI API Key")
    return api_key
```

如果当前 grok_shared.py 的实现风格不同（如直接抛错信息含 "XAI_API_KEY 未设置"），保留同样信息但删除 env 读取。

- [ ] **Step 3: 跑测试**

Run: `uv run pytest tests/backends/test_no_env_fallback.py::test_grok_shared_no_api_key_raises -v`
Expected: PASS

- [ ] **Step 4: commit**

```bash
uv run ruff check lib/grok_shared.py
uv run ruff format lib/grok_shared.py
git add lib/grok_shared.py
git commit -m "refactor(grok): 删除 XAI_API_KEY env fallback"
```

---

### Task 3.4: 清理 `lib/vidu_shared.py`

**Files:**
- Modify: `lib/vidu_shared.py:54-59`

- [ ] **Step 1: 重读原文件相关段**

```bash
sed -n '50,80p' lib/vidu_shared.py
```

- [ ] **Step 2: 改写 `resolve_vidu_api_key`**

删除 `allow_env_fallback` 参数，删除 env 读取。

```python
def resolve_vidu_api_key(api_key: str | None) -> str:
    """spec §5.4：不再支持 env fallback；缺失即 raise。"""
    if not api_key:
        raise ValueError("请到系统配置页填写 Vidu API Key")
    return api_key
```

所有调用点（`create_vidu_client(api_key=...)`）相应清理 `allow_env_fallback` 参数。

```bash
grep -rn "allow_env_fallback" lib server
```
找到所有调用方，删除 `allow_env_fallback=...` 参数。

- [ ] **Step 3: 跑测试**

Run: `uv run pytest tests/backends/test_no_env_fallback.py::test_vidu_shared_no_api_key_raises -v`
Expected: PASS

- [ ] **Step 4: commit**

```bash
uv run ruff check lib/vidu_shared.py
uv run ruff format lib/vidu_shared.py
git add lib/vidu_shared.py
git commit -m "refactor(vidu): 删除 VIDU_API_KEY env fallback + allow_env_fallback 参数"
```

---

### Task 3.5: 清理 gemini backends env fallback

**Files:**
- Modify: `lib/image_backends/gemini.py:52, 81, 85`
- Modify: `lib/video_backends/gemini.py:54, 80, 84`
- Modify: `lib/text_backends/gemini.py:37` 附近

- [ ] **Step 1: 找到每个 env fallback 点**

```bash
grep -nE "os\.environ\.get\(\"GEMINI_" lib/image_backends/gemini.py lib/video_backends/gemini.py lib/text_backends/gemini.py
```

- [ ] **Step 2: 改 image_backends/gemini.py**

行 52：
```python
self._image_model = image_model or os.environ.get("GEMINI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL)
```
改为：
```python
self._image_model = image_model or DEFAULT_IMAGE_MODEL
```

行 81：
```python
_api_key = api_key or os.environ.get("GEMINI_API_KEY")
```
改为：
```python
if not api_key:
    raise ValueError("请到系统配置页填写 Gemini API Key")
_api_key = api_key
```

行 85：
```python
effective_base_url = normalize_base_url(base_url or os.environ.get("GEMINI_BASE_URL"))
```
改为：
```python
effective_base_url = normalize_base_url(base_url)
```

- [ ] **Step 3: 同样改 video_backends/gemini.py（行 54, 80, 84）**

模式相同。

- [ ] **Step 4: 同样改 text_backends/gemini.py**

读源码后做对应改动。

- [ ] **Step 5: 删除可能因此而 unused 的 `import os` 行**

```bash
uv run ruff check lib/image_backends/gemini.py lib/video_backends/gemini.py lib/text_backends/gemini.py
```
如报 F401，删掉对应 import。

- [ ] **Step 6: 跑 gemini 测试**

Run: `uv run pytest tests/ -k gemini -v 2>&1 | tail -30`
Expected: 现有测试通过；缺 api_key 的用例应 raise

- [ ] **Step 7: commit**

```bash
uv run ruff format lib/image_backends/gemini.py lib/video_backends/gemini.py lib/text_backends/gemini.py
git add lib/image_backends/gemini.py lib/video_backends/gemini.py lib/text_backends/gemini.py
git commit -m "refactor(gemini): 删除 GEMINI_* env fallback (image/video/text)"
```

---

## Phase 4 — Sandbox 启用核心改造

### Task 4.1: `SessionManager._build_provider_env_overrides()`

**Files:**
- Modify: `server/agent_runtime/session_manager.py` (新增方法)
- Test: `tests/agent_runtime/test_session_manager_sandbox.py`

- [ ] **Step 1: 准备测试文件**

Create `tests/agent_runtime/test_session_manager_sandbox.py`：

```python
"""SessionManager sandbox + options.env 集成测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


@pytest.fixture
def session_manager(tmp_path: Path) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "projects").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    meta_store = SessionMetaStore(data_dir)
    sm = SessionManager(project_root, data_dir, meta_store)
    sm._in_docker = False
    return sm


@pytest.mark.asyncio
async def test_provider_env_overrides_includes_anthropic_and_empties(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dict = {
        "ANTHROPIC_API_KEY": "sk-from-db",
        "ANTHROPIC_BASE_URL": "https://anthropic.example.com",
        "ANTHROPIC_MODEL": "claude-opus-4-7",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "",
        "CLAUDE_CODE_SUBAGENT_MODEL": "",
    }

    async def fake_build(_session):
        return fake_dict

    with patch("lib.config.service.build_anthropic_env_dict", side_effect=fake_build):
        env = await session_manager._build_provider_env_overrides()

    # Anthropic 注入真值
    assert env["ANTHROPIC_API_KEY"] == "sk-from-db"
    assert env["ANTHROPIC_BASE_URL"] == "https://anthropic.example.com"

    # 其他 provider 空值覆盖
    assert env["ARK_API_KEY"] == ""
    assert env["XAI_API_KEY"] == ""
    assert env["GEMINI_API_KEY"] == ""
    assert env["VIDU_API_KEY"] == ""
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == ""
```

- [ ] **Step 2: 跑测试期望失败**

Run: `uv run pytest tests/agent_runtime/test_session_manager_sandbox.py::test_provider_env_overrides_includes_anthropic_and_empties -v`
Expected: FAIL，AttributeError

- [ ] **Step 3: 在 `session_manager.py` 添加方法**

在 `SessionManager` 类内（紧邻 `_build_options` 上方）添加：

```python
    async def _build_provider_env_overrides(self) -> dict[str, str]:
        """构造 options.env 注入字典 — spec §6.2。

        - ANTHROPIC_* 从 DB active credential 取真值
        - 其他 provider env 全部空值覆盖（防御性兜底）
        """
        from lib.config.env_keys import OTHER_PROVIDER_ENV_KEYS
        from lib.config.service import build_anthropic_env_dict
        from lib.db import async_session_factory

        async with async_session_factory() as session:
            anthropic_env = await build_anthropic_env_dict(session)

        result = dict(anthropic_env)
        for key in OTHER_PROVIDER_ENV_KEYS:
            result[key] = ""
        return result
```

- [ ] **Step 4: 跑测试期望通过**

Run: `uv run pytest tests/agent_runtime/test_session_manager_sandbox.py::test_provider_env_overrides_includes_anthropic_and_empties -v`
Expected: PASS

- [ ] **Step 5: commit**

```bash
uv run ruff check server/agent_runtime/session_manager.py tests/agent_runtime/test_session_manager_sandbox.py
uv run ruff format server/agent_runtime/session_manager.py tests/agent_runtime/test_session_manager_sandbox.py
git add server/agent_runtime/session_manager.py tests/agent_runtime/test_session_manager_sandbox.py
git commit -m "feat(agent): _build_provider_env_overrides — Anthropic 真值 + 其他 provider 空值覆盖"
```

---

### Task 4.2: `DEFAULT_ALLOWED_TOOLS` 加入 Bash

**Files:**
- Modify: `server/agent_runtime/session_manager.py:291-300`

- [ ] **Step 1: 写测试**

在 `tests/agent_runtime/test_session_manager_sandbox.py` 追加：

```python
def test_default_allowed_tools_includes_bash() -> None:
    """sandbox 启用后 Bash/BashOutput/KillBash 必须在 allowed_tools 列表。"""
    assert "Bash" in SessionManager.DEFAULT_ALLOWED_TOOLS
    assert "BashOutput" in SessionManager.DEFAULT_ALLOWED_TOOLS
    assert "KillBash" in SessionManager.DEFAULT_ALLOWED_TOOLS
```

- [ ] **Step 2: 跑测试期望失败**

Run: `uv run pytest tests/agent_runtime/test_session_manager_sandbox.py::test_default_allowed_tools_includes_bash -v`
Expected: FAIL

- [ ] **Step 3: 修改常量**

打开 `server/agent_runtime/session_manager.py`：

把：
```python
    DEFAULT_ALLOWED_TOOLS = [
        "Skill",
        "Task",
        "Read",
        "Write",
        "Edit",
        "Grep",
        "Glob",
        "AskUserQuestion",
    ]
```

改为：
```python
    DEFAULT_ALLOWED_TOOLS = [
        "Skill",
        "Task",
        # —— Bash 系列（sandbox 启用 + autoAllowBashIfSandboxed=True 协同放行）——
        "Bash",
        "BashOutput",
        "KillBash",
        # —— SDK 内置工具（仍走 PreToolUse hook 文件围栏 + settings.json deny）——
        "Read",
        "Write",
        "Edit",
        "Grep",
        "Glob",
        "AskUserQuestion",
    ]
```

同时删除上方注释行：
```python
    # Bash is NOT in DEFAULT_ALLOWED_TOOLS — it is controlled by declarative
    # allow rules in settings.json (whitelist approach, default deny).
```

替换为：
```python
    # Sandbox 启用后 Bash 进入 allowed_tools；具体命令由 SDK Sandbox 自动放行
    # (autoAllowBashIfSandboxed=True)。文件访问控制走 settings.json deny rules
    # + PreToolUse hook 双重防线 (spec §4)。
```

- [ ] **Step 4: 跑测试**

Run: `uv run pytest tests/agent_runtime/test_session_manager_sandbox.py -v`
Expected: 全绿

- [ ] **Step 5: commit**

```bash
uv run ruff check server/agent_runtime/session_manager.py
uv run ruff format server/agent_runtime/session_manager.py
git add server/agent_runtime/session_manager.py
git commit -m "feat(agent): DEFAULT_ALLOWED_TOOLS 加入 Bash/BashOutput/KillBash"
```

---

### Task 4.3: `_build_options()` 注入 `sandbox=` 和 `env=`

**Files:**
- Modify: `server/agent_runtime/session_manager.py:513-582`

- [ ] **Step 1: 测试 — sandbox 字段必填**

追加测试到 `tests/agent_runtime/test_session_manager_sandbox.py`：

```python
@pytest.mark.asyncio
async def test_build_options_includes_sandbox_settings(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proj_dir = session_manager.project_root / "projects" / "test_proj"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")

    async def fake_env(_self):
        return {"ANTHROPIC_API_KEY": "sk", "ARK_API_KEY": ""}

    monkeypatch.setattr(SessionManager, "_build_provider_env_overrides", fake_env)

    opts = session_manager._build_options("test_proj")

    assert opts.sandbox is not None
    assert opts.sandbox.get("enabled") is True
    assert opts.sandbox.get("autoAllowBashIfSandboxed") is True
    # 非 Docker 默认 weakerNested=False
    assert opts.sandbox.get("enableWeakerNestedSandbox") is False
```

注：因 `_build_options` 是同步方法但 env 异步取，需要在 task 实现里把 env 注入路径调整（见 step 3）。

- [ ] **Step 2: 跑测试期望失败**

Run: `uv run pytest tests/agent_runtime/test_session_manager_sandbox.py::test_build_options_includes_sandbox_settings -v`
Expected: FAIL

- [ ] **Step 3: 改造 `_build_options`**

由于 `_build_provider_env_overrides` 是 async，但 `_build_options` 是 sync，需要把 env 注入挪到 async 入口。两个方案：

**方案 A**: 把 `_build_options` 改 async（外层调用者已是 async session_actor）
**方案 B**: 在 sync `_build_options` 中保留 env 占位，由 async 层在 connect 前 patch

选 A，最自然。

修改 `_build_options` 签名：

```python
    async def _build_options(
        self,
        project_name: str,
        resume_id: str | None = None,
        can_use_tool: Callable[[str, dict[str, Any], Any], Any] | None = None,
        locale: str = "zh",
    ) -> Any:
```

在函数体末尾把 `ClaudeAgentOptions(...)` 改为：

```python
        from claude_agent_sdk.types import SandboxSettings

        provider_env = await self._build_provider_env_overrides()

        sandbox_settings: SandboxSettings = {
            "enabled": True,
            "autoAllowBashIfSandboxed": True,
            "enableWeakerNestedSandbox": bool(getattr(self, "_in_docker", False)),
        }

        return ClaudeAgentOptions(
            cwd=str(project_cwd),
            setting_sources=self.DEFAULT_SETTING_SOURCES,
            allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
            max_turns=self.max_turns,
            system_prompt=SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=self._build_append_prompt(project_name, locale=locale),
            ),
            include_partial_messages=True,
            resume=resume_id,
            can_use_tool=can_use_tool,
            hooks=hooks,
            sandbox=sandbox_settings,
            env=provider_env,
            session_store=self._build_session_store(),
            session_store_flush=session_store_flush_mode(),
        )
```

- [ ] **Step 4: 更新所有 `_build_options(...)` 调用方为 `await self._build_options(...)`**

```bash
grep -n "_build_options(" server/agent_runtime/session_manager.py
```

把每个 `self._build_options(...)` 改为 `await self._build_options(...)`。

确认所有调用方都在 async 上下文中（应该都是）。

- [ ] **Step 5: 把 `_in_docker` 由 app.state 传到 SessionManager**

修改 `SessionManager.__init__` 增加可选字段：

```python
    def __init__(
        self,
        project_root: Path,
        data_dir: Path,
        meta_store: SessionMetaStore,
        in_docker: bool = False,
    ):
        ...
        self._in_docker = in_docker
```

修改创建 SessionManager 的位置（应在 `server/agent_runtime/service.py` 或 lifespan）：

Run: `grep -rn "SessionManager(" server/ | grep -v test`
查到调用点，传入 `in_docker=app.state.in_docker` 或类似方式。

如果是通过 `AssistantService` 构造，先在该层加 `in_docker` 字段。

- [ ] **Step 6: 跑测试期望通过**

Run: `uv run pytest tests/agent_runtime/test_session_manager_sandbox.py -v`
Expected: 全绿

- [ ] **Step 7: 跑现有 session_manager 测试做回归**

Run: `uv run pytest tests/agent_runtime/ -v 2>&1 | tail -30`
Expected: 现有测试不退化（涉及 `_build_options` 的同步调用需更新为 await）

- [ ] **Step 8: commit**

```bash
uv run ruff check server/agent_runtime/session_manager.py server/agent_runtime/service.py tests/agent_runtime/test_session_manager_sandbox.py
uv run ruff format server/agent_runtime/session_manager.py server/agent_runtime/service.py tests/agent_runtime/test_session_manager_sandbox.py
git add server/agent_runtime/session_manager.py server/agent_runtime/service.py tests/agent_runtime/test_session_manager_sandbox.py
git commit -m "feat(agent): _build_options 注入 SandboxSettings + provider env overrides"
```

---

### Task 4.4: 重写 `_is_path_allowed` 为三规则

**Files:**
- Modify: `server/agent_runtime/session_manager.py:1625-1689`
- Modify: `server/agent_runtime/session_manager.py:314-315` (常量重命名)
- Test: `tests/agent_runtime/test_path_isolation_hook.py`

- [ ] **Step 1: 写测试**

Create `tests/agent_runtime/test_path_isolation_hook.py`：

```python
"""新版 _is_path_allowed 三规则：跨项目读拒 + cwd 外写拒 + 代码扩展名拒。"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


@pytest.fixture
def sm(tmp_path: Path) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "projects").mkdir()
    (project_root / "projects" / "selfproj").mkdir()
    (project_root / "projects" / "other").mkdir()
    (project_root / "lib").mkdir()
    return SessionManager(project_root, tmp_path / "data", SessionMetaStore(tmp_path / "data"))


def test_read_cwd_internal_passes(sm: SessionManager, tmp_path: Path) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, _ = sm._is_path_allowed(str(cwd / "data.json"), "Read", cwd)
    assert allowed


def test_read_other_project_denied(sm: SessionManager) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, reason = sm._is_path_allowed(
        str(sm.project_root / "projects" / "other" / "x.json"), "Read", cwd
    )
    assert not allowed
    assert "跨项目" in reason or "项目" in reason


def test_read_lib_passes(sm: SessionManager) -> None:
    """cwd 外的非 projects 路径允许读（用于 agent 查 docs/lib 等参考资料）。"""
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, _ = sm._is_path_allowed(str(sm.project_root / "lib" / "foo.py"), "Read", cwd)
    assert allowed


def test_write_cwd_external_denied(sm: SessionManager) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, reason = sm._is_path_allowed(
        str(sm.project_root / "lib" / "foo.json"), "Write", cwd
    )
    assert not allowed
    assert "项目目录之外" in reason or "cwd" in reason or "项目" in reason


def test_write_cwd_internal_code_ext_denied(sm: SessionManager) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    for ext in (".py", ".js", ".ts", ".tsx", ".sh", ".yaml", ".yml", ".toml"):
        allowed, reason = sm._is_path_allowed(str(cwd / f"test{ext}"), "Write", cwd)
        assert not allowed, f"扩展名 {ext} 应被拒"
        assert "代码" in reason or "扩展名" in reason


def test_write_cwd_internal_data_ext_allowed(sm: SessionManager) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    for ext in (".json", ".md", ".txt", ".html", ".csv"):
        allowed, _ = sm._is_path_allowed(str(cwd / f"data{ext}"), "Write", cwd)
        assert allowed, f"扩展名 {ext} 应允许"
```

- [ ] **Step 2: 跑测试期望失败**

Run: `uv run pytest tests/agent_runtime/test_path_isolation_hook.py -v`
Expected: 多个 FAIL

- [ ] **Step 3: 重写常量**

打开 `server/agent_runtime/session_manager.py` 行 314-315：

把：
```python
    _WRITE_TOOLS = {"Write", "Edit"}
    _WRITABLE_EXTENSIONS = {".json", ".md", ".txt"}
```

改为：
```python
    _WRITE_TOOLS = {"Write", "Edit"}
    _CODE_EXTENSIONS_FORBIDDEN = {
        ".py", ".js", ".ts", ".tsx", ".sh", ".yaml", ".yml", ".toml",
    }
```

- [ ] **Step 4: 重写 `_is_path_allowed`**

把行 1625-1689 整段替换为：

```python
    def _is_path_allowed(
        self,
        file_path: str,
        tool_name: str,
        project_cwd: Path,
    ) -> tuple[bool, str | None]:
        """检查 file_path 是否允许给定工具访问。

        spec §5.1 / Level B 简化方案 — 三条普适规则：
        1. Read/Glob/Grep：projects/<other>/ 跨项目拒；cwd 外其他路径放行
        2. Write/Edit：cwd 外一律拒
        3. Write/Edit：cwd 内代码扩展名拒（agent 不写代码）

        SDK tool-results / /tmp/claude-*/tasks 例外保留（SDK 内部产物）。
        """
        try:
            p = Path(file_path)
            resolved = (project_cwd / p).resolve() if not p.is_absolute() else p.resolve()
        except (ValueError, OSError):
            return False, "访问被拒绝：无效的文件路径"

        is_write = tool_name in self._WRITE_TOOLS
        is_inside_cwd = resolved.is_relative_to(project_cwd)
        projects_root = self.project_root / "projects"

        # 规则 1: Read 类工具的跨项目隔离
        if not is_write:
            # cwd 内通过
            if is_inside_cwd:
                return True, None
            # cwd 外但在其他项目目录 → 拒
            if resolved.is_relative_to(projects_root):
                return False, (
                    f"访问被拒绝：不允许跨项目读取 ({resolved} "
                    f"不在当前项目 {project_cwd} 内)"
                )
            # SDK tool-results 例外
            encoded = self._encode_sdk_project_path(project_cwd)
            sdk_project_dir = self._CLAUDE_PROJECTS_DIR / encoded
            if resolved.is_relative_to(sdk_project_dir) and "tool-results" in resolved.parts:
                return True, None
            # SDK 后台任务输出例外
            _SDK_TMP_PREFIXES = ("/tmp/claude-", "/private/tmp/claude-")
            if str(resolved).startswith(_SDK_TMP_PREFIXES) and "tasks" in resolved.parts:
                return True, None
            # 其他 cwd 外路径放行（lib/docs/agent_runtime_profile 等参考资料）
            return True, None

        # 规则 2: 写工具 cwd 外拒
        if not is_inside_cwd:
            return False, (
                f"访问被拒绝：不允许写入当前项目目录之外的路径 ({resolved})"
            )

        # 规则 3: cwd 内写代码扩展名拒
        ext = resolved.suffix.lower()
        if ext in self._CODE_EXTENSIONS_FORBIDDEN:
            return False, (
                f"不允许在项目内创建/编辑 {ext} 类型的代码文件。"
                "Write/Edit 应用于数据文件 (.json/.md/.txt 等)；"
                "代码逻辑请通过现有 skill 脚本完成。"
            )

        return True, None
```

- [ ] **Step 5: 跑测试期望通过**

Run: `uv run pytest tests/agent_runtime/test_path_isolation_hook.py -v`
Expected: 全绿

- [ ] **Step 6: 跑现有 session_manager 测试做回归**

Run: `uv run pytest tests/agent_runtime/ -v 2>&1 | tail -30`
Expected: 现有测试不退化 — 若有依赖旧 `_WRITABLE_EXTENSIONS` 名字的，改测试

- [ ] **Step 7: commit**

```bash
uv run ruff check server/agent_runtime/session_manager.py tests/agent_runtime/test_path_isolation_hook.py
uv run ruff format server/agent_runtime/session_manager.py tests/agent_runtime/test_path_isolation_hook.py
git add server/agent_runtime/session_manager.py tests/agent_runtime/test_path_isolation_hook.py
git commit -m "refactor(agent): _is_path_allowed 重写为跨项目+cwd外+代码扩展名三规则"
```

---

### Task 4.5: 更新 `_can_use_tool` deny hint 文案

**Files:**
- Modify: `server/agent_runtime/session_manager.py:1770-1786`

- [ ] **Step 1: 定位旧文案**

Run: `grep -n "白名单" server/agent_runtime/session_manager.py | head -5`
Expected: 行 1779 附近含 "白名单仅允许..."

- [ ] **Step 2: 替换文案**

把 `_can_use_tool` 内部的 hint 构造段：

```python
                hint = (
                    f"未授权的工具调用: {tool_name}"
                    f"({json.dumps(input_data, ensure_ascii=False)[:200]})\n"
                    f"{reason_line}"
                    "当前 Bash 白名单仅允许以下命令:\n"
                    "  - python .claude/skills/<skill>/scripts/<script>.py <args>（必须用相对路径）\n"
                    "  - ffmpeg / ffprobe\n"
                    "其他 Bash 命令均不可用。"
                    "请检查命令格式是否匹配白名单规则。"
                )
```

改为：

```python
                hint = (
                    f"未授权的工具调用: {tool_name}"
                    f"({json.dumps(input_data, ensure_ascii=False)[:200]})\n"
                    f"{reason_line}"
                    "请检查工具名是否正确，以及 file_path / 命令是否触发了 "
                    "settings.json 的 deny 规则或 PreToolUse hook（跨项目/cwd 外写/代码扩展名）。"
                )
```

- [ ] **Step 3: 跑相关测试做回归**

Run: `uv run pytest tests/agent_runtime/ -v 2>&1 | tail -20`
Expected: 全绿

- [ ] **Step 4: commit**

```bash
uv run ruff format server/agent_runtime/session_manager.py
git add server/agent_runtime/session_manager.py
git commit -m "refactor(agent): can_use_tool deny hint 文案对齐沙箱化"
```

---

### Task 4.6: settings.json 瘦身

**Files:**
- Modify: `agent_runtime_profile/.claude/settings.json`

- [ ] **Step 1: 备份当前内容做对比**

```bash
cp agent_runtime_profile/.claude/settings.json /tmp/settings.before.json
```

- [ ] **Step 2: 覆写**

把 `agent_runtime_profile/.claude/settings.json` 完整内容替换为：

```json
{
  "permissions": {
    "deny": [
      "Read(//app/.env)",
      "Read(//app/.env.*)",
      "Read(//app/vertex_keys/**)",
      "Read(//app/projects/.arcreel.db)",
      "Read(//app/projects/.arcreel.db-*)",
      "Read(//app/projects/.system_config.json)",
      "Read(//app/projects/.system_config.json.bak)",
      "Read(//app/agent_runtime_profile/.claude/settings.json)",
      "Edit(//app/.env)",
      "Edit(//app/.env.*)",
      "Edit(//app/vertex_keys/**)",
      "Edit(//app/projects/.arcreel.db)",
      "Edit(//app/projects/.arcreel.db-*)",
      "Edit(//app/projects/.system_config.json)",
      "Edit(//app/projects/.system_config.json.bak)"
    ]
  }
}
```

- [ ] **Step 3: JSON 合法性校验**

Run: `uv run python -c "import json; json.load(open('agent_runtime_profile/.claude/settings.json'))"`
Expected: 无输出

- [ ] **Step 4: 启动 server 跑一次手动验证**

```bash
unset ANTHROPIC_API_KEY ARK_API_KEY XAI_API_KEY GEMINI_API_KEY VIDU_API_KEY GOOGLE_APPLICATION_CREDENTIALS
uv run uvicorn server.app:app --port 1241 &
sleep 5
curl -s http://localhost:1241/api/v1/projects | head -5
kill %1
```
Expected: server 正常启动

- [ ] **Step 5: commit**

```bash
git add agent_runtime_profile/.claude/settings.json
git commit -m "refactor(agent-profile): settings.json deny 瘦身到敏感文件 13 条 + allow 整段删除"
```

---

### Task 4.7: 更新 `agent_runtime_profile/CLAUDE.md`

**Files:**
- Modify: `agent_runtime_profile/CLAUDE.md`

- [ ] **Step 1: 定位「相对路径」相关引导**

Run: `grep -n "相对路径" agent_runtime_profile/CLAUDE.md`

- [ ] **Step 2: 删除或重写相关段**

把原段 `Bash 调用 skill 脚本时必须使用相对路径...` 等行替换为：

```markdown
- **Bash 调用**：项目目录内可自由跑 `ls / cat / jq / python / curl` 等命令（沙箱化已启用），skill 脚本路径建议用相对路径以便跨项目通用，但绝对路径同样可用。
```

注：该文件内容较长，仔细 review 改动以免误删其他段。

- [ ] **Step 3: 跑 markdown 链接 / 格式检查**

Run: `uv run python -c "import pathlib; print(pathlib.Path('agent_runtime_profile/CLAUDE.md').read_text()[:200])"`

- [ ] **Step 4: commit**

```bash
git add agent_runtime_profile/CLAUDE.md
git commit -m "docs(agent-profile): CLAUDE.md 沿沙箱化更新 Bash 调用说明"
```

---

## Phase 5 — 部署 + 文档

### Task 5.1: Dockerfile 安装 bubblewrap

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1: 找当前 apt 安装段**

Run: `grep -n "apt-get" Dockerfile`

- [ ] **Step 2: 在合适位置增加 bubblewrap**

打开 `Dockerfile` 找到运行时镜像层（一般在第二阶段 / runtime stage），在已有 `apt-get install -y ffmpeg` 类似行加上 `bubblewrap`：

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    bubblewrap \
    && rm -rf /var/lib/apt/lists/*
```

如果项目无 ffmpeg 这条，则单独加：

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    bubblewrap \
    && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 3: 本地构建验证**

```bash
docker build -t arcreel:sandbox-test .
docker run --rm arcreel:sandbox-test which bwrap
```
Expected: 输出 `/usr/bin/bwrap`

- [ ] **Step 4: commit**

```bash
git add Dockerfile
git commit -m "build(docker): 安装 bubblewrap 用于 agent sandbox"
```

---

### Task 5.2: 部署文档更新

**Files:**
- Modify: `README.md` 或 `docs/deployment.md` (按现状)

- [ ] **Step 1: 找现有部署文档**

Run: `find docs/ -name "*.md" | xargs grep -l "部署\|Deploy\|Docker" 2>/dev/null | head -5`

- [ ] **Step 2: 在合适文档加入 sandbox 段**

新增段（按文档中文风格）：

```markdown
## Agent 沙箱依赖

ArcReel 启动会进行严格的安全检查 — sandbox 工具缺失即拒绝启动。

| 环境 | 工具 | 安装 |
|---|---|---|
| macOS | `sandbox-exec` | 系统自带，无需额外安装 |
| Linux 本地开发 | `bwrap` | `sudo apt install bubblewrap` (Ubuntu/Debian) / `sudo pacman -S bubblewrap` (Arch) |
| Docker | `bwrap` | Dockerfile 已包含 |

启动失败时 server 会输出明确错误信息，按提示安装即可。

**.env 迁移说明**：sandbox 设计要求父进程 `os.environ` 不含任何 provider 密钥。
请把 `.env` 中的下列 key 移到 WebUI 系统配置页：

- `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 等 ANTHROPIC_*
- `ARK_API_KEY` / `XAI_API_KEY` / `GEMINI_API_KEY` / `VIDU_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`（vertex 凭据继续放 `vertex_keys/` 目录）

启动检测发现这些 key 仍存在于 env 时，server 会拒绝启动并提示需要清理。
```

- [ ] **Step 3: commit**

```bash
git add docs/  # 或对应文件
git commit -m "docs(deployment): 增补 agent sandbox 依赖与 .env 迁移说明"
```

---

## Phase 6 — 集成 + 验收

### Task 6.1: 集成测试 `test_sandbox_e2e.py`

**Files:**
- Create: `tests/integration/test_sandbox_e2e.py`

- [ ] **Step 1: 写测试骨架**

Create `tests/integration/test_sandbox_e2e.py`：

```python
"""Sandbox 端到端集成测试。

仅在 sandbox 工具可用的环境跑（macOS / Linux + bwrap）。
依赖：项目根有 `projects/_e2e_dummy/` 目录与合法 `project.json`。
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

pytestmark = pytest.mark.skipif(
    not (sys.platform == "darwin" and shutil.which("sandbox-exec"))
    and not (sys.platform == "linux" and shutil.which("bwrap")),
    reason="sandbox tool not available on this runner",
)


@pytest.mark.asyncio
async def test_bash_ls_in_cwd_succeeds() -> None:
    """场景：agent 在项目 cwd 跑 `ls` 应成功放行。对齐 PoC #3。"""
    pytest.skip("作为 PoC #3 手动验收项；自动化在 CI sandbox runner 就绪后引入")


@pytest.mark.asyncio
async def test_bash_cat_env_denied() -> None:
    """场景：cat /app/.env 被 sandbox deny rule 拒。对齐 PoC #2。"""
    pytest.skip("作为 PoC #2 手动验收项")


@pytest.mark.asyncio
async def test_bash_curl_external_succeeds() -> None:
    """场景：curl 任意域名应放行。对齐 PoC #4。"""
    pytest.skip("作为 PoC #4 手动验收项")


@pytest.mark.asyncio
async def test_sdk_read_other_project_denied() -> None:
    """场景：SDK Read 跨项目读取被 hook 拒。"""
    pytest.skip("hook 单元测试已覆盖；e2e 选做")


@pytest.mark.asyncio
async def test_sdk_write_code_extension_denied() -> None:
    """场景：SDK Write 写 .py 进项目被 hook 拒。"""
    pytest.skip("hook 单元测试已覆盖；e2e 选做")
```

> 说明：完整端到端测试需要真 ClaudeSDKClient + 网络 + 合法 Anthropic credential，CI 上跑成本高。spec §9.2 中的 5 条核心场景以 `pytest.skip` + 手动 PoC 验收替代。如果未来有 CI 沙箱测试 runner，再补真实测试。

- [ ] **Step 2: 跑 skipped 测试做语法验证**

Run: `uv run pytest tests/integration/test_sandbox_e2e.py -v`
Expected: 5 个测试 SKIPPED

- [ ] **Step 3: commit**

```bash
uv run ruff check tests/integration/test_sandbox_e2e.py
uv run ruff format tests/integration/test_sandbox_e2e.py
git add tests/integration/test_sandbox_e2e.py
git commit -m "test(integration): sandbox e2e skeleton (手动 PoC 验收)"
```

---

### Task 6.2: 安全红线手动验收 checklist

**Files:**
- Create: `docs/superpowers/specs/2026-05-12-agent-sandbox-design.acceptance.md`

- [ ] **Step 1: 写 checklist**

Create `docs/superpowers/specs/2026-05-12-agent-sandbox-design.acceptance.md`：

```markdown
# Agent 沙箱化 合并前验收 Checklist

## 安全红线（必过）

- [ ] Bash 子进程不可见 provider 密钥
  - 步骤：启 session → agent 跑 `Bash("env | grep -E 'ANTHROPIC|ARK|XAI|GEMINI|VIDU'")`
  - 期望：输出完全为空（含 ANTHROPIC_*；env scrub hook 会 unset 后再执行命令）
- [ ] agent 不能读 `.env`
  - agent 跑 `Bash("cat /app/.env")` → 输出含 violation
- [ ] agent 不能读 `.arcreel.db`（已知限制：暂未纳入 denyRead，issue #519 跟踪）
  - 当前状态：可读，但 db 内 provider 密钥不会进入 Bash env（双层防线之一被关闭，另一层仍生效）
- [ ] agent 不能读 `vertex_keys/`
  - agent 跑 `Bash("ls /app/vertex_keys")` → violation
- [ ] agent 不能读 `agent_runtime_profile/.claude/settings.json`
  - agent 跑 `Bash("cat /app/agent_runtime_profile/.claude/settings.json")` → violation
- [ ] agent 不能写项目目录外
  - agent 跑 `Bash("touch /app/lib/x.txt")` → violation
- [ ] 父进程 `os.environ` 不含 provider 密钥
  - 启动 server 后 `python -c "import os; print([k for k in os.environ if 'KEY' in k or 'CRED' in k])"`
  - 期望：不含 ANTHROPIC_API_KEY/ARK_API_KEY 等

## 功能验收

- [ ] agent 在项目目录内自由跑 ls / cat / jq / python -c
- [ ] 新增 skill 脚本无需改权限配置
  - 临时加一个 echo skill，调用不报权限错
- [ ] agent 可自由 curl 任意域名
- [ ] 切换 Anthropic 配置后新 session 生效
  - 旧 session 仍用旧值；新建 session 用新值

## 平台覆盖

- [ ] macOS 本地：sandbox-exec 启用，PoC 全通
- [ ] Linux 本地（含 bwrap）：bwrap 启用，PoC 全通
- [ ] Docker：enableWeakerNestedSandbox 启用，PoC 全通
```

- [ ] **Step 2: commit**

```bash
git add docs/superpowers/specs/2026-05-12-agent-sandbox-design.acceptance.md
git commit -m "docs(sandbox): 合并前验收 checklist"
```

---

### Task 6.3: PoC 脚本删除 + 收尾

**Files:**
- Delete: `scripts/dev/sandbox_poc.py`

- [ ] **Step 1: 确认 PoC 报告已归档**

Run: `ls docs/superpowers/specs/2026-05-12-agent-sandbox-design.poc-report*`
Expected: 看到 .md / .json 报告文件

- [ ] **Step 2: 删除 PoC 脚本**

```bash
git rm scripts/dev/sandbox_poc.py
```

- [ ] **Step 3: 跑完整测试套件回归**

```bash
uv run pytest 2>&1 | tail -30
```
Expected: 全绿（或仅 SKIPPED）

- [ ] **Step 4: ruff format / check 全仓**

```bash
uv run ruff check .
uv run ruff format .
git status --short
```

- [ ] **Step 5: commit 收尾**

```bash
git add -u
git commit -m "chore(sandbox): 删除 PoC 脚本，报告已归档"
```

---

## 完成检查

整个 plan 执行完后：

- [ ] `uv run pytest` 全绿
- [ ] `uv run ruff check .` 无错误
- [ ] `agent_runtime_profile/.claude/settings.json` 仅含 13 条 deny rules
- [ ] 父进程 `os.environ` 不含 provider 密钥
- [ ] 文档 `2026-05-12-agent-sandbox-design.acceptance.md` 所有条目打勾

合并到 main 前再跑一次 acceptance checklist。
