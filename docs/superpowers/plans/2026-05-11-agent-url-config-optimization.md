# Agent URL 配置优化与预设供应商目录 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Anthropic 兼容子路径识别盲区，引入预设供应商目录（cc-switch 风格）+ 多套凭证 active 切换 + 真实 messages probe 连接测试，让用户开箱即用主流国内代理网关。

**Architecture:** 三层：(1) 纯函数模块 `derive_anthropic_endpoints` + `probe_anthropic_messages` 处理 URL 派生与连通性体检；(2) `agent_anthropic_credentials` 新表 + Repository 管理多套凭证；(3) FastAPI `/api/v1/agent/*` 路由 + 前端 cc-switch 风格 UI。

**Tech Stack:** Python (FastAPI / SQLAlchemy 2 async / Pydantic / pytest / httpx) · TypeScript (React 19 / wouter / zustand / Tailwind / @lobehub/icons / vitest) · Alembic 数据迁移

**Spec:** `docs/superpowers/specs/2026-05-11-agent-url-config-optimization-design.md`

---

## 文件结构

### 新建（后端）

| 路径 | 责任 |
|------|------|
| `lib/agent_provider_catalog.py` | 预设供应商 hardcoded 目录 (`PRESET_PROVIDERS`, `list_presets`, `get_preset`, `CUSTOM_SENTINEL_ID`) |
| `lib/config/anthropic_url.py` | `AnthropicEndpoints` dataclass + `derive_anthropic_endpoints` 纯函数 |
| `lib/config/anthropic_probe.py` | `probe_messages` / `probe_discovery` / `classify_diagnosis` / `run_test` 协调函数 |
| `lib/db/models/agent_credential.py` | `AgentAnthropicCredential` ORM (含每用户 1 条 active 唯一约束) |
| `lib/db/repositories/agent_credential_repo.py` | `AgentCredentialRepository`：CRUD + `set_active` + `get_active` |
| `alembic/versions/<rev>_add_agent_anthropic_credentials.py` | 建表 + 数据迁移（旧 `system_settings.anthropic_*` → 1 条 `__custom__` active） |
| `server/routers/agent_config.py` | `/api/v1/agent/*` 路由：preset-providers / credentials CRUD / activate / test |
| `tests/test_anthropic_url.py` | `derive_anthropic_endpoints` 单元测试 |
| `tests/test_anthropic_probe.py` | `probe_messages` / `classify_diagnosis` / `run_test` 测试（mock httpx） |
| `tests/test_agent_provider_catalog.py` | catalog 完整性 + icon_key 与前端 ICON_LOADERS 一致性 |
| `tests/test_agent_credential_repo.py` | Repository 行为 + 一致性约束 |
| `tests/test_agent_config_router.py` | 路由鉴权 / 校验 / 边界 |
| `tests/test_discover_anthropic_path_fix.py` | 回归：带 `/anthropic` 后缀时 discovery 走根 |

### 新建（前端）

| 路径 | 责任 |
|------|------|
| `frontend/src/types/agent-credential.ts` | `PresetProvider` / `AgentCredential` / `TestConnectionResponse` 等 TS 类型 |
| `frontend/src/components/agent/PresetIcon.tsx` | 按 `iconKey` 动态 import lobehub 子组件，失败 fallback monogram |
| `frontend/src/components/agent/CredentialList.tsx` | 凭证卡片网格 + activate/test/edit/delete 操作 |
| `frontend/src/components/agent/AddCredentialModal.tsx` | cc-switch 风格预设选择 + 表单 + tab |
| `frontend/src/components/agent/TestResultPanel.tsx` | 诊断结果折叠面板 + Apply Fix 按钮 |
| `frontend/src/components/agent/__tests__/PresetIcon.test.tsx` | 加载失败 fallback 验证 |
| `frontend/src/components/agent/__tests__/CredentialList.test.tsx` | 操作触发 API + active 不可删 |
| `frontend/src/components/agent/__tests__/AddCredentialModal.test.tsx` | 选预设 → URL 自动填；自定义 → 可编辑；submit payload 正确 |

### 修改

| 路径 | 改动概要 |
|------|---------|
| `lib/db/models/__init__.py` | export `AgentAnthropicCredential` |
| `lib/config/service.py` | `sync_anthropic_env` 改签名 `(session)` 为新主路径；旧 dict 签名抽到内部 `_sync_from_settings` |
| `lib/custom_provider/discovery.py` | `_discover_anthropic` 用 `derive_anthropic_endpoints` |
| `server/routers/custom_providers.py` | `/discover-anthropic` 凭据 fallback 改读 active credential |
| `server/routers/system_config.py` | 移除「PATCH 后同步 env」（改由 `/agent/credentials/{id}/activate` 触发）；保留旧 anthropic_* setting 写路径作兼容 |
| `server/app.py` | lifespan 改用新 `sync_anthropic_env(session)`；注册新路由 |
| `frontend/src/api.ts` | 加 `listPresetProviders` / `listAgentCredentials` / `createAgentCredential` / `updateAgentCredential` / `deleteAgentCredential` / `activateAgentCredential` / `testAgentCredential` / `testAgentConnectionDraft` |
| `frontend/src/components/pages/AgentConfigTab.tsx` | Section 1 (API Credentials) 整段替换 |
| `frontend/src/components/pages/AgentConfigTab.test.tsx` | 适配新结构 |
| `frontend/src/i18n/zh/dashboard.ts` | 新增 keys |
| `frontend/src/i18n/en/dashboard.ts` | 新增 keys |
| `frontend/src/i18n/vi/dashboard.ts` | 新增 keys |

---

## 实施阶段总览

1. **Phase 1**：纯函数模块 — URL 派生 + Probe（无 DB / 无 HTTP，可独立 TDD）
2. **Phase 2**：预设 Catalog（hardcoded，无依赖）
3. **Phase 3**：DB Schema + Repository
4. **Phase 4**：`sync_anthropic_env` 重构 + lifespan 切换
5. **Phase 5**：FastAPI 路由
6. **Phase 6**：`_discover_anthropic` 修复
7. **Phase 7**：前端类型 + API 客户端 + 底层组件
8. **Phase 8**：前端 modal + 列表
9. **Phase 9**：AgentConfigTab 整合 + i18n
10. **Phase 10**：手工 / E2E 验证

---

## Phase 1：URL 派生 + Probe

### Task 1：`derive_anthropic_endpoints` 纯函数

**Files:**
- Create: `lib/config/anthropic_url.py`
- Test: `tests/test_anthropic_url.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_anthropic_url.py
"""derive_anthropic_endpoints 单元测试。"""

import pytest

from lib.config.anthropic_url import AnthropicEndpoints, derive_anthropic_endpoints


@pytest.mark.parametrize(
    ("user_url", "expected"),
    [
        # 官方根
        (
            "https://api.anthropic.com",
            AnthropicEndpoints("https://api.anthropic.com", "https://api.anthropic.com", False),
        ),
        # /anthropic 子路径 (DeepSeek/Kimi/MiniMax/Hunyuan/MiMo)
        (
            "https://api.deepseek.com/anthropic",
            AnthropicEndpoints("https://api.deepseek.com/anthropic", "https://api.deepseek.com", True),
        ),
        # /api/anthropic (z.ai)
        (
            "https://api.z.ai/api/anthropic",
            AnthropicEndpoints("https://api.z.ai/api/anthropic", "https://api.z.ai/api", True),
        ),
        # /apps/anthropic (DashScope)
        (
            "https://dashscope.aliyuncs.com/apps/anthropic",
            AnthropicEndpoints(
                "https://dashscope.aliyuncs.com/apps/anthropic",
                "https://dashscope.aliyuncs.com",
                True,
            ),
        ),
        # /coding/anthropic (LKEAP)
        (
            "https://api.lkeap.cloud.tencent.com/coding/anthropic",
            AnthropicEndpoints(
                "https://api.lkeap.cloud.tencent.com/coding/anthropic",
                "https://api.lkeap.cloud.tencent.com",
                True,
            ),
        ),
        # /api/coding (火山方舟 Coding Plan)
        (
            "https://ark.cn-beijing.volces.com/api/coding",
            AnthropicEndpoints(
                "https://ark.cn-beijing.volces.com/api/coding",
                "https://ark.cn-beijing.volces.com",
                True,
            ),
        ),
        # 用户误带 /v1
        (
            "https://api.deepseek.com/anthropic/v1",
            AnthropicEndpoints("https://api.deepseek.com/anthropic", "https://api.deepseek.com", True),
        ),
        # 用户误带 /v1/messages
        (
            "https://api.deepseek.com/anthropic/v1/messages",
            AnthropicEndpoints("https://api.deepseek.com/anthropic", "https://api.deepseek.com", True),
        ),
        # 末尾多斜杠
        (
            "https://api.deepseek.com/anthropic/",
            AnthropicEndpoints("https://api.deepseek.com/anthropic", "https://api.deepseek.com", True),
        ),
        # 未识别子路径 → 不剥
        (
            "https://example.com/v2/proxy",
            AnthropicEndpoints("https://example.com/v2/proxy", "https://example.com/v2/proxy", False),
        ),
        # 纯根域，未带 /anthropic
        (
            "https://api.deepseek.com",
            AnthropicEndpoints("https://api.deepseek.com", "https://api.deepseek.com", False),
        ),
    ],
)
def test_derive_endpoints(user_url: str, expected: AnthropicEndpoints) -> None:
    assert derive_anthropic_endpoints(user_url) == expected


def test_empty_url_raises() -> None:
    with pytest.raises(ValueError):
        derive_anthropic_endpoints("")


def test_whitespace_stripped() -> None:
    ep = derive_anthropic_endpoints("  https://api.deepseek.com/anthropic  ")
    assert ep.messages_root == "https://api.deepseek.com/anthropic"
```

- [ ] **Step 2: 运行测试确认失败**

```
uv run python -m pytest tests/test_anthropic_url.py -v
```
Expected: ImportError / ModuleNotFoundError on `lib.config.anthropic_url`.

- [ ] **Step 3: 实现 `lib/config/anthropic_url.py`**

```python
"""Anthropic base_url 派生：把用户填的 URL 拆为 messages_root + discovery_root。

各国内代理网关把 Claude 兼容协议挂在不同的子路径下：
- /anthropic              DeepSeek、Kimi、MiniMax、腾讯 Hunyuan、小米 MiMo
- /api/anthropic          GLM (z.ai)
- /apps/anthropic         阿里百炼 (DashScope)
- /plan/anthropic         腾讯 LKEAP Token Plan
- /coding/anthropic       腾讯 LKEAP Coding Plan
- /api/coding             火山方舟 Coding Plan

而模型发现 /v1/models 总是在「子路径之前的根」下。
本模块负责一次性派生这两个 root，下游 SDK / 模型发现各取所需。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 已知的 Claude 兼容子路径 — 从精确到宽松，防止 "/anthropic" 提前匹配 "/api/anthropic"
_KNOWN_ANTHROPIC_SUFFIX = re.compile(
    r"/(?:api/anthropic|apps/anthropic|plan/anthropic|coding/anthropic|api/coding|anthropic)/?$"
)
# 用户误带的版本路径，先剥
_TRAILING_VERSION = re.compile(r"/v\d+\w*(?:/messages)?/?$")


@dataclass(frozen=True)
class AnthropicEndpoints:
    """从用户填的 base_url 派生出的两个端点根。"""

    messages_root: str
    """Claude SDK 拼 /v1/messages 用 (含 anthropic 子路径)。"""

    discovery_root: str
    """模型发现拼 /v1/models 用 (剥掉 anthropic 子路径)。"""

    has_explicit_suffix: bool
    """用户输入是否已经显式带了已知 anthropic 子路径。"""


def derive_anthropic_endpoints(user_url: str) -> AnthropicEndpoints:
    """派生 Anthropic 兼容端点。

    Steps:
        1) 去首尾空白、剥末尾斜杠
        2) 剥末尾的 /v\\d+ 或 /v\\d+/messages（用户误带）
        3) 用 _KNOWN_ANTHROPIC_SUFFIX 匹配子路径：
           匹配 → messages_root = 原值, discovery_root = 剥掉子路径
           不匹配 → messages_root == discovery_root == 原值
    """
    if not user_url or not user_url.strip():
        raise ValueError("user_url is empty")
    cleaned = user_url.strip().rstrip("/")
    cleaned = _TRAILING_VERSION.sub("", cleaned).rstrip("/")
    match = _KNOWN_ANTHROPIC_SUFFIX.search(cleaned)
    if match:
        messages_root = cleaned[: match.end()].rstrip("/")
        discovery_root = cleaned[: match.start()].rstrip("/")
        return AnthropicEndpoints(messages_root, discovery_root, has_explicit_suffix=True)
    return AnthropicEndpoints(cleaned, cleaned, has_explicit_suffix=False)
```

- [ ] **Step 4: 运行测试确认通过**

```
uv run python -m pytest tests/test_anthropic_url.py -v
```
Expected: 所有 case PASS。

- [ ] **Step 5: lint / format**

```
uv run ruff check lib/config/anthropic_url.py tests/test_anthropic_url.py
uv run ruff format lib/config/anthropic_url.py tests/test_anthropic_url.py
```

- [ ] **Step 6: 提交**

```
git add lib/config/anthropic_url.py tests/test_anthropic_url.py
git commit -m "feat(config): derive_anthropic_endpoints 识别已知子路径"
```


### Task 2：`probe_messages` HTTP 体检（mock httpx）

**Files:**
- Create: `lib/config/anthropic_probe.py`
- Test: `tests/test_anthropic_probe.py`

- [ ] **Step 1: 写失败测试 — 成功路径**

```python
# tests/test_anthropic_probe.py
"""Anthropic probe 单元测试 (mock httpx，不打真实网络)。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lib.config.anthropic_probe import (
    DiagnosisCode,
    ProbeResult,
    classify_probe_failure,
    probe_messages,
)


@pytest.mark.asyncio
async def test_probe_messages_success() -> None:
    fake_response = httpx.Response(
        200,
        json={"id": "msg_1", "type": "message", "content": [{"type": "text", "text": "ok"}]},
    )
    with patch(
        "lib.config.anthropic_probe._post",
        AsyncMock(return_value=fake_response),
    ) as mocked:
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="sk-test",
            model="claude-3-5-sonnet-20241022",
        )
    assert result.success is True
    assert result.status_code == 200
    assert result.error is None
    mocked.assert_awaited_once()
    called_url = mocked.await_args.kwargs["url"]
    assert called_url == "https://api.example.com/v1/messages"
```

- [ ] **Step 2: 写失败测试 — 失败 / 协议不匹配 / 超时**

接前文文件追加：

```python
@pytest.mark.asyncio
async def test_probe_messages_401_marks_failure() -> None:
    fake = httpx.Response(401, json={"error": {"type": "authentication_error"}})
    with patch("lib.config.anthropic_probe._post", AsyncMock(return_value=fake)):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="bad",
            model="claude-3-5-sonnet-20241022",
        )
    assert result.success is False
    assert result.status_code == 401
    assert "authentication_error" in (result.error or "")


@pytest.mark.asyncio
async def test_probe_messages_200_but_not_anthropic_marks_failure() -> None:
    """OpenAI 兼容协议响应：200 但缺 type=message 应判失败。"""
    fake = httpx.Response(
        200,
        json={"id": "chatcmpl-1", "object": "chat.completion", "choices": []},
    )
    with patch("lib.config.anthropic_probe._post", AsyncMock(return_value=fake)):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="sk",
            model="x",
        )
    assert result.success is False
    assert result.status_code == 200
    assert "non-anthropic" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_probe_messages_timeout() -> None:
    with patch(
        "lib.config.anthropic_probe._post",
        AsyncMock(side_effect=httpx.TimeoutException("timeout")),
    ):
        result = await probe_messages(
            messages_root="https://api.example.com",
            api_key="sk",
            model="x",
            timeout_s=0.5,
        )
    assert result.success is False
    assert result.status_code is None
    assert "timeout" in (result.error or "").lower()


def test_classify_probe_failure_auth() -> None:
    p = ProbeResult(success=False, status_code=401, latency_ms=10, error="…")
    assert classify_probe_failure(p) == DiagnosisCode.AUTH_FAILED


def test_classify_probe_failure_404_with_model() -> None:
    p = ProbeResult(success=False, status_code=404, latency_ms=10, error="model_not_found")
    assert classify_probe_failure(p) == DiagnosisCode.MODEL_NOT_FOUND


def test_classify_probe_failure_429() -> None:
    p = ProbeResult(success=False, status_code=429, latency_ms=10, error="rate")
    assert classify_probe_failure(p) == DiagnosisCode.RATE_LIMITED


def test_classify_probe_failure_network() -> None:
    p = ProbeResult(success=False, status_code=None, latency_ms=10, error="timeout")
    assert classify_probe_failure(p) == DiagnosisCode.NETWORK


def test_classify_probe_failure_openai_compat() -> None:
    p = ProbeResult(success=False, status_code=200, latency_ms=10, error="non-anthropic JSON")
    assert classify_probe_failure(p) == DiagnosisCode.OPENAI_COMPAT_ONLY
```

- [ ] **Step 3: 运行测试确认失败**

```
uv run python -m pytest tests/test_anthropic_probe.py -v
```
Expected: ImportError on `lib.config.anthropic_probe`.

- [ ] **Step 4: 实现 `lib/config/anthropic_probe.py` — 数据类型 + 工具**

```python
"""Anthropic 兼容端点的真实连通性体检 + 诊断分类。

本模块只用 httpx 直调，不通过 Claude SDK，避免子进程副作用。
日志严格只打 URL 与 status，不打 body / headers / api_key。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

from lib.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

_ERR_TRUNCATE = 200


class DiagnosisCode(str, Enum):
    MISSING_ANTHROPIC_SUFFIX = "missing_anthropic_suffix"
    OPENAI_COMPAT_ONLY = "openai_compat_only"
    AUTH_FAILED = "auth_failed"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    success: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None  # 截断到 200 字符


async def _post(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: float,
) -> httpx.Response:
    """间接层：测试时 patch 这一个。"""
    client = get_http_client()
    return await client.post(url, headers=headers, json=payload, timeout=timeout_s)


def _truncate(s: str | None) -> str | None:
    if s is None:
        return None
    return s if len(s) <= _ERR_TRUNCATE else s[:_ERR_TRUNCATE] + "…"
```

- [ ] **Step 5: 实现 `probe_messages`**

接续追加到 `lib/config/anthropic_probe.py`：

```python
async def probe_messages(
    *,
    messages_root: str,
    api_key: str,
    model: str,
    timeout_s: float = 10.0,
) -> ProbeResult:
    """POST {messages_root}/v1/messages 发最小请求 (max_tokens=1)。

    判定:
    - 2xx 且响应 JSON 含 type=message → success
    - 2xx 但响应不像 anthropic JSON → 判失败 (OPENAI_COMPAT_ONLY)
    - 非 2xx → 失败
    - 网络异常/超时 → 失败 (status_code=None)
    """
    url = f"{messages_root.rstrip('/')}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    started = time.perf_counter()
    try:
        resp = await _post(url=url, headers=headers, payload=payload, timeout_s=timeout_s)
    except httpx.TimeoutException as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_messages timeout url=%s elapsed_ms=%d", url, elapsed)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=f"timeout: {exc!s}")
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.info("probe_messages network err url=%s elapsed_ms=%d", url, elapsed)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=_truncate(str(exc)))

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("probe_messages url=%s status=%d elapsed_ms=%d", url, resp.status_code, elapsed)

    if resp.status_code >= 400:
        # 不打 body 全文，只截前 200 字符以便 UI 给用户看
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error=_truncate(resp.text),
        )

    # 2xx：检查是否真的是 anthropic JSON
    try:
        data = resp.json()
    except ValueError:
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error="non-anthropic response: not JSON",
        )
    if not isinstance(data, dict) or data.get("type") != "message":
        return ProbeResult(
            success=False,
            status_code=resp.status_code,
            latency_ms=elapsed,
            error="non-anthropic JSON: missing type=message",
        )
    return ProbeResult(success=True, status_code=resp.status_code, latency_ms=elapsed, error=None)


def classify_probe_failure(result: ProbeResult) -> DiagnosisCode:
    """把失败 ProbeResult 映射到 DiagnosisCode。"""
    if result.success:
        return DiagnosisCode.UNKNOWN  # caller misuse
    err = (result.error or "").lower()
    code = result.status_code
    if code in (401, 403):
        return DiagnosisCode.AUTH_FAILED
    if code == 429:
        return DiagnosisCode.RATE_LIMITED
    if code == 404 and ("model" in err or "model_not_found" in err):
        return DiagnosisCode.MODEL_NOT_FOUND
    if code is not None and 200 <= code < 300:
        # 2xx 但 probe 判失败 = 协议不匹配
        return DiagnosisCode.OPENAI_COMPAT_ONLY
    if code is None:
        return DiagnosisCode.NETWORK
    return DiagnosisCode.UNKNOWN
```

- [ ] **Step 6: 运行测试确认通过**

```
uv run python -m pytest tests/test_anthropic_probe.py -v
```
Expected: 全 PASS。

- [ ] **Step 7: lint / format / 提交**

```
uv run ruff check lib/config/anthropic_probe.py tests/test_anthropic_probe.py
uv run ruff format lib/config/anthropic_probe.py tests/test_anthropic_probe.py
git add lib/config/anthropic_probe.py tests/test_anthropic_probe.py
git commit -m "feat(config): probe_messages + classify_probe_failure"
```


### Task 3：`probe_discovery` + `run_test` 协调函数

**Files:**
- Modify: `lib/config/anthropic_probe.py`
- Modify: `tests/test_anthropic_probe.py`

- [ ] **Step 1: 写失败测试 — discovery probe**

把以下追加到 `tests/test_anthropic_probe.py`：

```python
from lib.config.anthropic_probe import probe_discovery, run_test, SuggestionAction
from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID  # 见 Phase 2 Task 4


@pytest.mark.asyncio
async def test_probe_discovery_none_root_returns_none() -> None:
    assert await probe_discovery(discovery_root=None, api_key="sk") is None


@pytest.mark.asyncio
async def test_probe_discovery_success() -> None:
    fake = httpx.Response(200, json={"data": [{"id": "m"}]})
    with patch(
        "lib.config.anthropic_probe._get",
        AsyncMock(return_value=fake),
    ) as mocked:
        result = await probe_discovery(discovery_root="https://api.example.com", api_key="sk")
    assert result is not None
    assert result.success is True
    assert result.status_code == 200
    called_url = mocked.await_args.kwargs["url"]
    assert called_url == "https://api.example.com/v1/models"
```

- [ ] **Step 2: 写失败测试 — run_test 自定义模式自愈**

继续追加：

```python
@pytest.mark.asyncio
async def test_run_test_custom_mode_self_heals_with_anthropic_suffix() -> None:
    """用户填 https://api.deepseek.com，messages probe 失败 (404)；
    自动重试 https://api.deepseek.com/anthropic 成功 → suggestion 给出修复值。
    """
    seq = [
        # 第一次：原 URL → 404
        httpx.Response(404, text="not found"),
        # 第二次：补 /anthropic → 200 anthropic JSON
        httpx.Response(200, json={"id": "msg_1", "type": "message", "content": []}),
        # discovery probe → 200 (随便)
        httpx.Response(200, json={"data": []}),
    ]
    call_log: list[str] = []

    async def fake_post(*, url, **_kw):
        call_log.append(url)
        return seq.pop(0)

    async def fake_get(*, url, **_kw):
        call_log.append(url)
        return seq.pop(0)

    with (
        patch("lib.config.anthropic_probe._post", AsyncMock(side_effect=fake_post)),
        patch("lib.config.anthropic_probe._get", AsyncMock(side_effect=fake_get)),
    ):
        resp = await run_test(
            preset_id=CUSTOM_SENTINEL_ID,
            base_url="https://api.deepseek.com",
            api_key="sk",
            model=None,
        )

    assert resp.overall == "ok"
    assert resp.diagnosis == DiagnosisCode.MISSING_ANTHROPIC_SUFFIX
    assert resp.suggestion is not None
    assert resp.suggestion.kind == "replace_base_url"
    assert resp.suggestion.suggested_value == "https://api.deepseek.com/anthropic"
    # 第一次和第二次都打的是 messages 端点
    assert call_log[0] == "https://api.deepseek.com/v1/messages"
    assert call_log[1] == "https://api.deepseek.com/anthropic/v1/messages"


@pytest.mark.asyncio
async def test_run_test_preset_skips_self_heal() -> None:
    """preset_id != __custom__ 时不做自愈尝试。"""
    seq = [httpx.Response(404, text="not found")]

    async def fake_post(*, url, **_kw):
        return seq.pop(0)

    async def fake_get(**_kw):
        return httpx.Response(200, json={"data": []})

    with (
        patch("lib.config.anthropic_probe._post", AsyncMock(side_effect=fake_post)),
        patch("lib.config.anthropic_probe._get", AsyncMock(side_effect=fake_get)),
    ):
        resp = await run_test(
            preset_id="anthropic-official",
            base_url=None,
            api_key="sk",
            model=None,
        )
    assert resp.overall == "fail"
    assert resp.suggestion is None
```

- [ ] **Step 3: 实现 `_get` 间接层**

把以下追加到 `lib/config/anthropic_probe.py`：

```python
async def _get(*, url: str, headers: dict[str, str], timeout_s: float) -> httpx.Response:
    """间接层：测试时 patch 这一个。"""
    client = get_http_client()
    return await client.get(url, headers=headers, timeout=timeout_s)
```

- [ ] **Step 4: 实现 `probe_discovery`**

```python
async def probe_discovery(
    *,
    discovery_root: str | None,
    api_key: str,
    timeout_s: float = 5.0,
) -> ProbeResult | None:
    """GET {discovery_root}/v1/models 体检模型发现端点 (warn 级，仅供参考)。"""
    if not discovery_root:
        return None
    url = f"{discovery_root.rstrip('/')}/v1/models"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    started = time.perf_counter()
    try:
        resp = await _get(url=url, headers=headers, timeout_s=timeout_s)
    except httpx.TimeoutException as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=f"timeout: {exc!s}")
    except httpx.HTTPError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        return ProbeResult(success=False, status_code=None, latency_ms=elapsed, error=_truncate(str(exc)))

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info("probe_discovery url=%s status=%d", url, resp.status_code)
    success = 200 <= resp.status_code < 300
    return ProbeResult(
        success=success,
        status_code=resp.status_code,
        latency_ms=elapsed,
        error=None if success else _truncate(resp.text),
    )
```

- [ ] **Step 5: 实现 `SuggestionAction` + `TestConnectionResponse` + `run_test`**

```python
from dataclasses import dataclass, field
from typing import Literal

from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID, get_preset
from lib.config.anthropic_url import AnthropicEndpoints, derive_anthropic_endpoints

_DEFAULT_TEST_MODEL = "claude-3-5-sonnet-20241022"
_RETRYABLE_STATUS_FOR_SELF_HEAL = (404, 405, 502)


@dataclass(frozen=True)
class SuggestionAction:
    kind: Literal["replace_base_url", "check_api_key", "run_discovery", "see_docs"]
    suggested_value: str | None = None


@dataclass(frozen=True)
class TestConnectionResponse:
    overall: Literal["ok", "warn", "fail"]
    messages_probe: ProbeResult
    discovery_probe: ProbeResult | None
    diagnosis: DiagnosisCode | None
    suggestion: SuggestionAction | None
    derived_messages_root: str
    derived_discovery_root: str


async def run_test(
    *,
    preset_id: str | None,
    base_url: str | None,
    api_key: str,
    model: str | None,
) -> TestConnectionResponse:
    """完整端到端测试：派生 → probe messages → 自定义模式自愈 → probe discovery → 诊断。"""
    # 1. 派生 endpoints
    if preset_id and preset_id != CUSTOM_SENTINEL_ID:
        preset = get_preset(preset_id)
        if preset is None:
            raise ValueError(f"unknown preset: {preset_id!r}")
        ep = AnthropicEndpoints(
            messages_root=preset.messages_url,
            discovery_root=preset.discovery_url or "",
            has_explicit_suffix=True,
        )
        effective_model = model or preset.default_model
    else:
        if not base_url:
            raise ValueError("base_url required for __custom__ mode")
        ep = derive_anthropic_endpoints(base_url)
        effective_model = model or _DEFAULT_TEST_MODEL

    # 2. messages probe
    msg = await probe_messages(messages_root=ep.messages_root, api_key=api_key, model=effective_model)

    # 3. 自定义模式 + 失败 + 没显式 anthropic 后缀 → 尝试自愈
    suggestion: SuggestionAction | None = None
    diagnosis: DiagnosisCode | None = None
    final_messages_root = ep.messages_root
    if (
        not msg.success
        and (preset_id is None or preset_id == CUSTOM_SENTINEL_ID)
        and not ep.has_explicit_suffix
        and msg.status_code in _RETRYABLE_STATUS_FOR_SELF_HEAL
    ):
        retry_root = ep.messages_root.rstrip("/") + "/anthropic"
        retry = await probe_messages(messages_root=retry_root, api_key=api_key, model=effective_model)
        if retry.success:
            msg = retry
            final_messages_root = retry_root
            suggestion = SuggestionAction(kind="replace_base_url", suggested_value=retry_root)
            diagnosis = DiagnosisCode.MISSING_ANTHROPIC_SUFFIX

    # 4. discovery probe (warn 级)
    disc = await probe_discovery(
        discovery_root=ep.discovery_root or None,
        api_key=api_key,
    )

    # 5. 诊断 + 总评
    if msg.success:
        overall = "ok" if (disc is None or disc.success) else "warn"
    else:
        overall = "fail"
        diagnosis = classify_probe_failure(msg)

    return TestConnectionResponse(
        overall=overall,
        messages_probe=msg,
        discovery_probe=disc,
        diagnosis=diagnosis,
        suggestion=suggestion,
        derived_messages_root=final_messages_root,
        derived_discovery_root=ep.discovery_root,
    )
```

- [ ] **Step 6: 运行测试确认通过**

```
uv run python -m pytest tests/test_anthropic_probe.py -v
```
Expected: 全 PASS。注意此 step 依赖 Phase 2 Task 4 已建立的 `lib/agent_provider_catalog.py`，若 catalog 还未建则先 `touch lib/agent_provider_catalog.py` 并放入临时存根 `CUSTOM_SENTINEL_ID = "__custom__"`，等 Phase 2 完整建立后取消存根。

- [ ] **Step 7: lint / format / 提交**

```
uv run ruff check lib/config/anthropic_probe.py tests/test_anthropic_probe.py
uv run ruff format lib/config/anthropic_probe.py tests/test_anthropic_probe.py
git add lib/config/anthropic_probe.py tests/test_anthropic_probe.py
git commit -m "feat(config): probe_discovery + run_test 协调 + 自愈逻辑"
```


---

## Phase 2：预设 Catalog

### Task 4：`PresetProvider` 数据类型 + `CUSTOM_SENTINEL_ID` 占位

**Files:**
- Create: `lib/agent_provider_catalog.py`
- Test: `tests/test_agent_provider_catalog.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_provider_catalog.py
"""预设供应商目录单元测试。"""

from lib.agent_provider_catalog import (
    CUSTOM_SENTINEL_ID,
    PRESET_PROVIDERS,
    PresetProvider,
    get_preset,
    list_presets,
)


def test_custom_sentinel_value() -> None:
    assert CUSTOM_SENTINEL_ID == "__custom__"


def test_anthropic_official_present() -> None:
    p = get_preset("anthropic-official")
    assert p is not None
    assert p.messages_url == "https://api.anthropic.com"
    assert p.discovery_url == "https://api.anthropic.com"
    assert p.icon_key == "Anthropic"
    assert p.is_recommended


def test_get_preset_unknown_returns_none() -> None:
    assert get_preset("does-not-exist") is None


def test_list_presets_recommended_first() -> None:
    presets = list_presets()
    # 第一个必须是推荐项
    assert presets[0].is_recommended


def test_no_duplicate_ids() -> None:
    ids = [p.id for p in list_presets()]
    assert len(ids) == len(set(ids))


def test_messages_url_https_only() -> None:
    for p in list_presets():
        assert p.messages_url.startswith("https://"), f"{p.id} messages_url not https"
        if p.discovery_url is not None:
            assert p.discovery_url.startswith("https://"), f"{p.id} discovery_url not https"


def test_first_batch_required_presets() -> None:
    """第一批 catalog 必须覆盖 spec §1.2 表格中的网关。"""
    required = {
        "anthropic-official",
        "deepseek",
        "kimi",
        "glm",
        "minimax-intl",
        "minimax-cn",
        "hunyuan",
        "lkeap",
        "ark-coding",
        "bailian",
        "xiaomi-mimo",
    }
    actual = {p.id for p in list_presets()}
    missing = required - actual
    assert not missing, f"缺失预设: {missing}"


def test_preset_dataclass_is_frozen() -> None:
    p = get_preset("anthropic-official")
    assert p is not None
    import dataclasses

    assert dataclasses.is_dataclass(p)
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        p.display_name = "x"  # type: ignore[misc]
```

- [ ] **Step 2: 运行测试确认失败**

```
uv run python -m pytest tests/test_agent_provider_catalog.py -v
```
Expected: ImportError on `lib.agent_provider_catalog`.

- [ ] **Step 3: 实现 `lib/agent_provider_catalog.py` — dataclass + sentinel + 第一批 entries**

```python
"""预设 Anthropic 兼容供应商目录 (cc-switch 风格)。

每条 PresetProvider 提供「开箱即用」的 messages_url + discovery_url + 推荐模型，
让用户在 UI 上选 chip 即填好 URL。新增 entries 在此文件添加；前端 ICON_LOADERS
通过 icon_key 与 @lobehub/icons 对齐。
"""

from __future__ import annotations

from dataclasses import dataclass

CUSTOM_SENTINEL_ID = "__custom__"


@dataclass(frozen=True)
class PresetProvider:
    id: str
    display_name: str
    icon_key: str  # @lobehub/icons 子组件名 (如 "DeepSeek")
    messages_url: str
    discovery_url: str | None
    default_model: str
    suggested_models: tuple[str, ...]
    docs_url: str | None
    api_key_url: str | None  # 「获取 API Key」链接
    notes_i18n_key: str | None
    api_key_pattern: str | None  # 前端轻量校验
    is_recommended: bool


PRESET_PROVIDERS: dict[str, PresetProvider] = {
    "anthropic-official": PresetProvider(
        id="anthropic-official",
        display_name="Anthropic Official",
        icon_key="Anthropic",
        messages_url="https://api.anthropic.com",
        discovery_url="https://api.anthropic.com",
        default_model="claude-3-5-sonnet-20241022",
        suggested_models=(
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-7-sonnet-latest",
        ),
        docs_url="https://docs.anthropic.com",
        api_key_url="https://console.anthropic.com/settings/keys",
        notes_i18n_key=None,
        api_key_pattern=r"^sk-ant-[A-Za-z0-9_-]+$",
        is_recommended=True,
    ),
    "deepseek": PresetProvider(
        id="deepseek",
        display_name="DeepSeek",
        icon_key="DeepSeek",
        messages_url="https://api.deepseek.com/anthropic",
        discovery_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        suggested_models=("deepseek-chat", "deepseek-reasoner"),
        docs_url="https://api-docs.deepseek.com/",
        api_key_url="https://platform.deepseek.com/api_keys",
        notes_i18n_key="preset_notes_deepseek",
        api_key_pattern=r"^sk-[A-Za-z0-9]+$",
        is_recommended=True,
    ),
    "kimi": PresetProvider(
        id="kimi",
        display_name="Kimi (Moonshot)",
        icon_key="Moonshot",
        messages_url="https://api.moonshot.cn/anthropic",
        discovery_url="https://api.moonshot.cn",
        default_model="moonshot-v1-32k",
        suggested_models=("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
        docs_url="https://platform.moonshot.cn/docs",
        api_key_url="https://platform.moonshot.cn/console/api-keys",
        notes_i18n_key=None,
        api_key_pattern=r"^sk-[A-Za-z0-9]+$",
        is_recommended=True,
    ),
    "glm": PresetProvider(
        id="glm",
        display_name="Zhipu GLM",
        icon_key="Zhipu",
        messages_url="https://open.bigmodel.cn/api/anthropic",
        discovery_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-plus",
        suggested_models=("glm-4-plus", "glm-4-air", "glm-4-flash"),
        docs_url="https://open.bigmodel.cn/dev/api",
        api_key_url="https://open.bigmodel.cn/usercenter/apikeys",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=True,
    ),
    "minimax-intl": PresetProvider(
        id="minimax-intl",
        display_name="MiniMax (Global)",
        icon_key="Minimax",
        messages_url="https://api.minimax.io/anthropic",
        discovery_url="https://api.minimax.io",
        default_model="MiniMax-M1",
        suggested_models=("MiniMax-M1",),
        docs_url="https://www.minimax.io/platform/document",
        api_key_url="https://www.minimax.io/user-center/basic-information/interface-key",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "minimax-cn": PresetProvider(
        id="minimax-cn",
        display_name="MiniMax (中国)",
        icon_key="Minimax",
        messages_url="https://api.minimaxi.com/anthropic",
        discovery_url="https://api.minimaxi.com",
        default_model="MiniMax-M1",
        suggested_models=("MiniMax-M1",),
        docs_url="https://platform.minimaxi.com/document",
        api_key_url="https://platform.minimaxi.com/user-center/basic-information/interface-key",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "hunyuan": PresetProvider(
        id="hunyuan",
        display_name="Tencent Hunyuan",
        icon_key="Hunyuan",
        messages_url="https://api.hunyuan.cloud.tencent.com/anthropic",
        discovery_url="https://api.hunyuan.cloud.tencent.com",
        default_model="hunyuan-turbo",
        suggested_models=("hunyuan-turbo", "hunyuan-pro", "hunyuan-lite"),
        docs_url="https://cloud.tencent.com/document/product/1729",
        api_key_url="https://console.cloud.tencent.com/hunyuan/api-key",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "lkeap": PresetProvider(
        id="lkeap",
        display_name="Tencent LKEAP (Coding)",
        icon_key="TencentCloud",
        messages_url="https://api.lkeap.cloud.tencent.com/coding/anthropic",
        discovery_url="https://api.lkeap.cloud.tencent.com",
        default_model="deepseek-v3",
        suggested_models=("deepseek-v3", "deepseek-r1"),
        docs_url="https://cloud.tencent.com/document/product/1772",
        api_key_url="https://console.cloud.tencent.com/lkeap/api",
        notes_i18n_key=None,
        api_key_pattern=None,
        is_recommended=False,
    ),
    "ark-coding": PresetProvider(
        id="ark-coding",
        display_name="火山方舟 (Coding)",
        icon_key="Volcengine",
        messages_url="https://ark.cn-beijing.volces.com/api/coding",
        discovery_url="https://ark.cn-beijing.volces.com",
        default_model="doubao-seed-1.6",
        suggested_models=("doubao-seed-1.6", "doubao-1.5-pro-32k"),
        docs_url="https://www.volcengine.com/docs/82379",
        api_key_url="https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
        notes_i18n_key="preset_notes_ark_coding",
        api_key_pattern=None,
        is_recommended=False,
    ),
    "bailian": PresetProvider(
        id="bailian",
        display_name="阿里百炼 (DashScope)",
        icon_key="Qwen",
        messages_url="https://dashscope.aliyuncs.com/apps/anthropic",
        discovery_url=None,  # 无公开 list 端点
        default_model="qwen-max",
        suggested_models=("qwen-max", "qwen-plus", "qwen-turbo"),
        docs_url="https://help.aliyun.com/zh/dashscope/",
        api_key_url="https://bailian.console.aliyun.com/?apiKey=1",
        notes_i18n_key="preset_notes_bailian",
        api_key_pattern=None,
        is_recommended=False,
    ),
    "xiaomi-mimo": PresetProvider(
        id="xiaomi-mimo",
        display_name="Xiaomi MiMo",
        icon_key="XiaomiMiMo",
        messages_url="https://api.xiaomimimo.com/anthropic",
        discovery_url=None,  # 未公开 /v1/models
        default_model="mimo-v2-pro",
        suggested_models=("mimo-v2-pro", "mimo-v2-flash"),
        docs_url="https://www.xiaomi.com/mimo",
        api_key_url=None,
        notes_i18n_key="preset_notes_xiaomi_mimo",
        api_key_pattern=None,
        is_recommended=False,
    ),
}


# 显示顺序：推荐项优先；同推荐内按字母序
PRESET_ORDER: tuple[str, ...] = tuple(
    sorted(PRESET_PROVIDERS.keys(), key=lambda k: (not PRESET_PROVIDERS[k].is_recommended, k))
)


def get_preset(preset_id: str) -> PresetProvider | None:
    return PRESET_PROVIDERS.get(preset_id)


def list_presets() -> list[PresetProvider]:
    return [PRESET_PROVIDERS[k] for k in PRESET_ORDER]
```

- [ ] **Step 4: 运行测试确认通过**

```
uv run python -m pytest tests/test_agent_provider_catalog.py -v
```
Expected: 全 PASS。

- [ ] **Step 5: lint / format / 提交**

```
uv run ruff check lib/agent_provider_catalog.py tests/test_agent_provider_catalog.py
uv run ruff format lib/agent_provider_catalog.py tests/test_agent_provider_catalog.py
git add lib/agent_provider_catalog.py tests/test_agent_provider_catalog.py
git commit -m "feat(catalog): 预设 Anthropic 供应商目录第一批"
```


---

## Phase 3：DB Schema + Repository

### Task 5：`AgentAnthropicCredential` ORM 模型

**Files:**
- Create: `lib/db/models/agent_credential.py`
- Modify: `lib/db/models/__init__.py`

- [ ] **Step 1: 创建 ORM 模型**

```python
# lib/db/models/agent_credential.py
"""Agent Anthropic 凭证 ORM。

每个 user 至多一条 is_active=True，由 partial unique index 保证 (与
ProviderCredential 同模式)。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, DEFAULT_USER_ID, TimestampMixin


class AgentAnthropicCredential(TimestampMixin, Base):
    """用户保存的多套 Anthropic 凭证；可在 UI 上一键切换 active。"""

    __tablename__ = "agent_anthropic_credentials"
    __table_args__ = (
        Index("ix_agent_credential_user", "user_id"),
        # 每个 user 至多一条 is_active=True
        Index(
            "uq_agent_credential_one_active_per_user",
            "user_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default=DEFAULT_USER_ID)
    preset_id: Mapped[str] = mapped_column(String(64), nullable=False)  # "deepseek" | "__custom__" | ...
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)  # 明文，读出 API mask_secret 脱敏
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    haiku_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sonnet_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    opus_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subagent_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

- [ ] **Step 2: 更新 `lib/db/models/__init__.py` export**

```python
# 在 from lib.db.models.custom_provider 行下加
from lib.db.models.agent_credential import AgentAnthropicCredential
# 在 __all__ 末尾加 "AgentAnthropicCredential"
```

具体编辑（用 Edit）：
- 找 `from lib.db.models.custom_provider import CustomProvider, CustomProviderModel`，在其下追加 `from lib.db.models.agent_credential import AgentAnthropicCredential`。
- 在 `__all__` 列表的 `"Asset",` 之后加 `"AgentAnthropicCredential",`。

- [ ] **Step 3: lint / format / 提交（暂不跑迁移）**

```
uv run ruff check lib/db/models/agent_credential.py lib/db/models/__init__.py
uv run ruff format lib/db/models/agent_credential.py lib/db/models/__init__.py
git add lib/db/models/agent_credential.py lib/db/models/__init__.py
git commit -m "feat(db): AgentAnthropicCredential ORM"
```

### Task 6：Alembic migration（建表 + 数据迁移）

**Files:**
- Create: `alembic/versions/<rev>_add_agent_anthropic_credentials.py`

- [ ] **Step 1: 自动生成迁移骨架**

```
uv run alembic revision -m "add agent anthropic credentials"
```
记下生成的 `<rev>` 文件路径。

- [ ] **Step 2: 编辑迁移内容**

把生成文件全部覆写为以下内容（替换 `<REV>` 与 `<DOWN_REV>`，不要改顶部 alembic 自动生成的 revision 行）：

```python
"""add agent anthropic credentials

Revision ID: <REV>
Revises: <DOWN_REV>
Create Date: ...

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# 顶部 revision 行保持 alembic 生成的原值，不要替换
revision: str = "<REV>"
down_revision: str | Sequence[str] | None = "<DOWN_REV>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_USER_ID = "default"
_LEGACY_KEYS = (
    "anthropic_api_key",
    "anthropic_base_url",
    "anthropic_model",
    "anthropic_default_haiku_model",
    "anthropic_default_sonnet_model",
    "anthropic_default_opus_model",
    "claude_code_subagent_model",
)


def upgrade() -> None:
    op.create_table(
        "agent_anthropic_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False, server_default=DEFAULT_USER_ID),
        sa.Column("preset_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("haiku_model", sa.String(length=128), nullable=True),
        sa.Column("sonnet_model", sa.String(length=128), nullable=True),
        sa.Column("opus_model", sa.String(length=128), nullable=True),
        sa.Column("subagent_model", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.create_index("ix_agent_credential_user", ["user_id"], unique=False)
        batch_op.create_index(
            "uq_agent_credential_one_active_per_user",
            ["user_id"],
            unique=True,
            sqlite_where=sa.text("is_active = 1"),
            postgresql_where=sa.text("is_active"),
        )

    # ── 数据迁移：旧 system_settings 中 anthropic_* → 一条 __custom__ active 记录 ──
    bind = op.get_bind()
    try:
        rows = bind.execute(
            sa.text(
                "SELECT key, value FROM system_settings WHERE key IN :keys"
            ).bindparams(sa.bindparam("keys", expanding=True)),
            {"keys": list(_LEGACY_KEYS)},
        ).fetchall()
        settings = {r.key: r.value for r in rows if r.value}

        if settings.get("anthropic_api_key"):
            now = datetime.now(UTC)
            bind.execute(
                sa.text("""
                    INSERT INTO agent_anthropic_credentials
                      (user_id, preset_id, display_name, base_url, api_key,
                       model, haiku_model, sonnet_model, opus_model, subagent_model,
                       is_active, created_at, updated_at)
                    VALUES (:user_id, '__custom__', 'Migrated', :base_url, :api_key,
                            :model, :haiku, :sonnet, :opus, :subagent,
                            1, :now, :now)
                """),
                {
                    "user_id": DEFAULT_USER_ID,
                    "base_url": settings.get("anthropic_base_url", ""),
                    "api_key": settings["anthropic_api_key"],
                    "model": settings.get("anthropic_model"),
                    "haiku": settings.get("anthropic_default_haiku_model"),
                    "sonnet": settings.get("anthropic_default_sonnet_model"),
                    "opus": settings.get("anthropic_default_opus_model"),
                    "subagent": settings.get("claude_code_subagent_model"),
                    "now": now,
                },
            )
    except Exception as exc:  # noqa: BLE001
        # 数据迁移失败不阻塞 schema 升级；用户可在 UI 里手动建
        import logging

        logging.getLogger(__name__).warning(
            "agent_anthropic_credentials data migration skipped: %s", exc
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_anthropic_credentials", schema=None) as batch_op:
        batch_op.drop_index("uq_agent_credential_one_active_per_user")
        batch_op.drop_index("ix_agent_credential_user")
    op.drop_table("agent_anthropic_credentials")
```

- [ ] **Step 3: 跑一次 upgrade 验证**

```
uv run alembic upgrade head
```
Expected: `Running upgrade <DOWN_REV> -> <REV>, add agent anthropic credentials`，无报错。

- [ ] **Step 4: 跑 downgrade + 再 upgrade，确认幂等**

```
uv run alembic downgrade -1
uv run alembic upgrade head
```
Expected: 两次都成功。

- [ ] **Step 5: lint + 提交**

```
uv run ruff check alembic/versions/<rev>_add_agent_anthropic_credentials.py
uv run ruff format alembic/versions/<rev>_add_agent_anthropic_credentials.py
git add alembic/versions/<rev>_add_agent_anthropic_credentials.py
git commit -m "feat(db): alembic 建表 + 旧 anthropic settings 数据迁移"
```


### Task 7：`AgentCredentialRepository` + tests

**Files:**
- Create: `lib/db/repositories/agent_credential_repo.py`
- Test: `tests/test_agent_credential_repo.py`

- [ ] **Step 1: 写失败测试 — 基础 CRUD**

```python
# tests/test_agent_credential_repo.py
"""AgentCredentialRepository 单元测试。"""

from __future__ import annotations

import pytest

from lib.db.repositories.agent_credential_repo import AgentCredentialRepository


@pytest.mark.asyncio
async def test_create_and_get(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    cred = await repo.create(
        preset_id="deepseek",
        display_name="My DeepSeek",
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        model="deepseek-chat",
    )
    await async_session.flush()
    fetched = await repo.get(cred.id)
    assert fetched is not None
    assert fetched.preset_id == "deepseek"
    assert fetched.api_key == "sk-test"
    assert fetched.is_active is False


@pytest.mark.asyncio
async def test_list_orders_by_id(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="deepseek", display_name="A", base_url="u", api_key="k1")
    b = await repo.create(preset_id="kimi", display_name="B", base_url="u", api_key="k2")
    await async_session.flush()
    items = await repo.list_for_user()
    ids = [c.id for c in items]
    assert ids == [a.id, b.id]
```

- [ ] **Step 2: 写失败测试 — set_active 互斥 + delete 阻塞**

```python
@pytest.mark.asyncio
async def test_set_active_makes_others_inactive(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="x", display_name="A", base_url="u", api_key="k1")
    b = await repo.create(preset_id="y", display_name="B", base_url="u", api_key="k2")
    await async_session.flush()
    await repo.set_active(a.id)
    await async_session.flush()
    active = await repo.get_active()
    assert active is not None and active.id == a.id

    await repo.set_active(b.id)
    await async_session.flush()
    active = await repo.get_active()
    assert active is not None and active.id == b.id

    a_after = await repo.get(a.id)
    assert a_after is not None and a_after.is_active is False


@pytest.mark.asyncio
async def test_delete_active_raises(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="x", display_name="A", base_url="u", api_key="k")
    await async_session.flush()
    await repo.set_active(a.id)
    await async_session.flush()
    with pytest.raises(ValueError, match="active"):
        await repo.delete(a.id)


@pytest.mark.asyncio
async def test_delete_inactive_works(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="x", display_name="A", base_url="u", api_key="k")
    await async_session.flush()
    await repo.delete(a.id)
    await async_session.flush()
    assert await repo.get(a.id) is None


@pytest.mark.asyncio
async def test_set_active_unknown_id_raises(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    with pytest.raises(ValueError, match="not found"):
        await repo.set_active(9999)


@pytest.mark.asyncio
async def test_update_partial(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    a = await repo.create(preset_id="x", display_name="A", base_url="u", api_key="k")
    await async_session.flush()
    updated = await repo.update(a.id, display_name="A2", model="m1")
    assert updated is not None
    assert updated.display_name == "A2"
    assert updated.model == "m1"
    assert updated.api_key == "k"  # 未传不动


@pytest.mark.asyncio
async def test_get_active_when_none(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    assert await repo.get_active() is None
```

- [ ] **Step 3: 检查或新增 `async_session` fixture**

```
grep -n "async_session" tests/conftest.py
```

如果该 fixture 已存在则跳过，否则参考 `tests/test_agent_credential_repo.py` 的需求在 `tests/conftest.py` 加：

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.db.base import Base


@pytest_asyncio.fixture
async def async_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
```

- [ ] **Step 4: 运行测试确认失败**

```
uv run python -m pytest tests/test_agent_credential_repo.py -v
```
Expected: ImportError on `lib.db.repositories.agent_credential_repo`.

- [ ] **Step 5: 实现 `lib/db/repositories/agent_credential_repo.py`**

```python
"""Agent Anthropic 凭证 Repository。"""

from __future__ import annotations

from sqlalchemy import delete, select, update

from lib.db.base import DEFAULT_USER_ID
from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.repositories.base import BaseRepository


class AgentCredentialRepository(BaseRepository):
    """凭证 CRUD + active 互斥切换。

    NOTE: 调用方需在合适的边界 commit。本类只 flush，不 commit。
    """

    async def create(
        self,
        *,
        preset_id: str,
        display_name: str,
        base_url: str,
        api_key: str,
        model: str | None = None,
        haiku_model: str | None = None,
        sonnet_model: str | None = None,
        opus_model: str | None = None,
        subagent_model: str | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> AgentAnthropicCredential:
        cred = AgentAnthropicCredential(
            user_id=user_id,
            preset_id=preset_id,
            display_name=display_name,
            base_url=base_url,
            api_key=api_key,
            model=model,
            haiku_model=haiku_model,
            sonnet_model=sonnet_model,
            opus_model=opus_model,
            subagent_model=subagent_model,
            is_active=False,
        )
        self.session.add(cred)
        await self.session.flush()
        return cred

    async def get(self, cred_id: int) -> AgentAnthropicCredential | None:
        stmt = select(AgentAnthropicCredential).where(AgentAnthropicCredential.id == cred_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: str = DEFAULT_USER_ID) -> list[AgentAnthropicCredential]:
        stmt = (
            select(AgentAnthropicCredential)
            .where(AgentAnthropicCredential.user_id == user_id)
            .order_by(AgentAnthropicCredential.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_active(self, user_id: str = DEFAULT_USER_ID) -> AgentAnthropicCredential | None:
        stmt = select(AgentAnthropicCredential).where(
            AgentAnthropicCredential.user_id == user_id,
            AgentAnthropicCredential.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, cred_id: int, **kwargs) -> AgentAnthropicCredential | None:
        cred = await self.get(cred_id)
        if cred is None:
            return None
        for k, v in kwargs.items():
            setattr(cred, k, v)
        await self.session.flush()
        return cred

    async def set_active(self, cred_id: int, user_id: str = DEFAULT_USER_ID) -> None:
        """互斥切 active：先把同 user 全置 False，再把目标置 True。

        Raises:
            ValueError: cred_id 不存在或不属于该 user
        """
        cred = await self.get(cred_id)
        if cred is None or cred.user_id != user_id:
            raise ValueError(f"credential id={cred_id} not found")
        # SQLite 的 partial unique index 在同事务内中间态可能违反，所以先全清再设
        await self.session.execute(
            update(AgentAnthropicCredential)
            .where(
                AgentAnthropicCredential.user_id == user_id,
                AgentAnthropicCredential.is_active.is_(True),
            )
            .values(is_active=False)
        )
        await self.session.flush()
        cred.is_active = True
        await self.session.flush()

    async def delete(self, cred_id: int) -> None:
        """删除非 active 凭证。删 active 抛 ValueError。"""
        cred = await self.get(cred_id)
        if cred is None:
            return
        if cred.is_active:
            raise ValueError("cannot delete active credential; activate another first")
        await self.session.execute(
            delete(AgentAnthropicCredential).where(AgentAnthropicCredential.id == cred_id)
        )
        await self.session.flush()
```

- [ ] **Step 6: 运行测试确认通过**

```
uv run python -m pytest tests/test_agent_credential_repo.py -v
```
Expected: 全 PASS。

- [ ] **Step 7: lint / format / 提交**

```
uv run ruff check lib/db/repositories/agent_credential_repo.py tests/test_agent_credential_repo.py
uv run ruff format lib/db/repositories/agent_credential_repo.py tests/test_agent_credential_repo.py
git add lib/db/repositories/agent_credential_repo.py tests/test_agent_credential_repo.py tests/conftest.py
git commit -m "feat(db): AgentCredentialRepository CRUD + active 互斥"
```


---

## Phase 4：`sync_anthropic_env` 重构 + lifespan 切换

### Task 8：`sync_anthropic_env` 改签名为 `(session)`

**Files:**
- Modify: `lib/config/service.py`
- Modify: `server/app.py`
- Modify: `server/routers/system_config.py`
- Test: `tests/test_sync_anthropic_env.py` (new)

- [ ] **Step 1: 写失败测试**

```python
# tests/test_sync_anthropic_env.py
"""sync_anthropic_env 主路径测试 — active credential 优先，否则 fallback 旧 settings。"""

from __future__ import annotations

import os

import pytest

from lib.config.service import sync_anthropic_env
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.db.models.config import SystemSetting


_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)


@pytest.fixture(autouse=True)
def _clear_env() -> None:
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    yield
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


@pytest.mark.asyncio
async def test_sync_uses_active_credential(async_session) -> None:
    repo = AgentCredentialRepository(async_session)
    cred = await repo.create(
        preset_id="deepseek",
        display_name="ds",
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-x",
        model="deepseek-chat",
    )
    await async_session.flush()
    await repo.set_active(cred.id)
    await async_session.flush()

    await sync_anthropic_env(async_session)
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-x"
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert os.environ["ANTHROPIC_MODEL"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_sync_fallback_to_system_settings(async_session) -> None:
    """没有 active credential 时，回退到 system_settings 旧 keys。"""
    async_session.add(SystemSetting(key="anthropic_api_key", value="legacy-k"))
    async_session.add(SystemSetting(key="anthropic_base_url", value="https://legacy.example/"))
    async_session.add(SystemSetting(key="anthropic_model", value="legacy-model"))
    await async_session.flush()

    await sync_anthropic_env(async_session)
    assert os.environ["ANTHROPIC_API_KEY"] == "legacy-k"
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://legacy.example/"
    assert os.environ["ANTHROPIC_MODEL"] == "legacy-model"


@pytest.mark.asyncio
async def test_sync_no_credential_no_settings_clears_env(async_session) -> None:
    os.environ["ANTHROPIC_API_KEY"] = "stale"
    await sync_anthropic_env(async_session)
    assert "ANTHROPIC_API_KEY" not in os.environ
```

- [ ] **Step 2: 改写 `lib/config/service.py`**

打开 `lib/config/service.py`，把 `sync_anthropic_env` 整段替换为：

```python
async def sync_anthropic_env(session: AsyncSession) -> None:
    """把 active credential 同步到 os.environ；无 active 时回退 system_settings。

    Claude Agent SDK 子进程从 os.environ 读取这些值，所以必须实时写入。
    """
    from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

    repo = AgentCredentialRepository(session)
    cred = await repo.get_active()
    if cred is not None:
        env_map: dict[str, str] = {
            "ANTHROPIC_API_KEY": cred.api_key,
            "ANTHROPIC_BASE_URL": cred.base_url,
            "ANTHROPIC_MODEL": cred.model or "",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": cred.haiku_model or "",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": cred.sonnet_model or "",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": cred.opus_model or "",
            "CLAUDE_CODE_SUBAGENT_MODEL": cred.subagent_model or "",
        }
        _apply_env_map(env_map)
        return
    # 兼容回退：从旧 system_settings 读
    settings = await SystemSettingRepository(session).get_all()
    _sync_from_settings(settings)


def _sync_from_settings(all_settings: dict[str, str]) -> None:
    env_map: dict[str, str] = {}
    for db_key, env_key in _ANTHROPIC_ENV_MAP.items():
        env_map[env_key] = all_settings.get(db_key, "").strip()
    _apply_env_map(env_map)


def _apply_env_map(env_map: dict[str, str]) -> None:
    for k, v in env_map.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
```

并删除原同步函数（保留 `_ANTHROPIC_ENV_MAP` 常量供 `_sync_from_settings` 使用）。

- [ ] **Step 3: 更新 `server/app.py` lifespan 调用点**

定位 `server/app.py` 第 170-180 行（lifespan 中调 `sync_anthropic_env(all_settings)` 的段），把：

```python
from lib.config.service import ConfigService, sync_anthropic_env

async with async_session_factory() as session:
    svc = ConfigService(session)
    all_settings = await svc.get_all_settings()
    sync_anthropic_env(all_settings)
```

替换为：

```python
from lib.config.service import sync_anthropic_env

async with async_session_factory() as session:
    await sync_anthropic_env(session)
```

- [ ] **Step 4: 更新 `server/routers/system_config.py`**

定位 `server/routers/system_config.py:360-370` 段（PATCH 后 sync 调用）：

```python
all_settings = await svc.get_all_settings()
sync_anthropic_env(all_settings)
```

替换为：

```python
await sync_anthropic_env(session)
```

并把顶部 import 改为 `from lib.config.service import ConfigService, sync_anthropic_env`（保留 ConfigService 用于其他场景）。

- [ ] **Step 5: 运行测试**

```
uv run python -m pytest tests/test_sync_anthropic_env.py -v
uv run python -m pytest tests/ -x -k "not test_browser and not e2e"
```
Expected: 新测试 PASS；不破坏既有测试。

- [ ] **Step 6: lint / format / 提交**

```
uv run ruff check lib/config/service.py server/app.py server/routers/system_config.py tests/test_sync_anthropic_env.py
uv run ruff format lib/config/service.py server/app.py server/routers/system_config.py tests/test_sync_anthropic_env.py
git add lib/config/service.py server/app.py server/routers/system_config.py tests/test_sync_anthropic_env.py
git commit -m "refactor(config): sync_anthropic_env 改为读 active credential，settings 回退"
```


---

## Phase 5：FastAPI 路由

### Task 9：`/agent/preset-providers` GET（基础响应）

**Files:**
- Create: `server/routers/agent_config.py`
- Test: `tests/test_agent_config_router.py`
- Modify: `server/app.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_agent_config_router.py
"""Agent config 路由测试。"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_preset_providers_returns_catalog(authed_client) -> None:
    resp = await authed_client.get("/api/v1/agent/preset-providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert "custom_sentinel_id" in data
    assert data["custom_sentinel_id"] == "__custom__"
    ids = [p["id"] for p in data["providers"]]
    assert "deepseek" in ids
    assert "anthropic-official" in ids
    deepseek = next(p for p in data["providers"] if p["id"] == "deepseek")
    assert deepseek["messages_url"] == "https://api.deepseek.com/anthropic"
    assert deepseek["discovery_url"] == "https://api.deepseek.com"
    assert "default_model" in deepseek
    assert "icon_key" in deepseek


@pytest.mark.asyncio
async def test_list_preset_providers_requires_auth(unauth_client) -> None:
    resp = await unauth_client.get("/api/v1/agent/preset-providers")
    assert resp.status_code in (401, 403)
```

- [ ] **Step 2: 检查 fixtures**

如 `tests/conftest.py` 没有 `authed_client` / `unauth_client`，参考已有路由测试（如 `tests/test_custom_provider_router.py` 或 `tests/test_system_config.py`，按现存模式）建立。具体细节：

```
grep -n "authed_client\|unauth_client" tests/conftest.py tests/*.py | head -10
```

如果不存在则按现有 router 测试模式（FastAPI TestClient + Auth 头）补；若已有等价 fixture（不同名）则替换为该名。

- [ ] **Step 3: 运行测试确认失败**

```
uv run python -m pytest tests/test_agent_config_router.py::test_list_preset_providers_returns_catalog -v
```
Expected: 404 (路由未注册) 或 ImportError。

- [ ] **Step 4: 实现 router 骨架 + preset-providers 端点**

```python
# server/routers/agent_config.py
"""Agent Anthropic 凭证 + 预设供应商目录 API。

路由前缀: /api/v1/agent
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID, list_presets
from lib.config.repository import mask_secret
from lib.config.service import sync_anthropic_env
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.i18n import Translator
from server.auth import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["Agent 配置"])


# ── Response models ─────────────────────────────────────────────────


class PresetProviderResponse(BaseModel):
    id: str
    display_name: str
    icon_key: str
    messages_url: str
    discovery_url: str | None
    default_model: str
    suggested_models: list[str]
    docs_url: str | None
    api_key_url: str | None
    notes: str | None
    api_key_pattern: str | None
    is_recommended: bool


class PresetProvidersResponse(BaseModel):
    providers: list[PresetProviderResponse]
    custom_sentinel_id: str


# ── Endpoints ──────────────────────────────────────────────────────


@router.get("/preset-providers", response_model=PresetProvidersResponse)
async def list_preset_providers(_user: CurrentUser, _t: Translator) -> PresetProvidersResponse:
    return PresetProvidersResponse(
        providers=[
            PresetProviderResponse(
                id=p.id,
                display_name=p.display_name,
                icon_key=p.icon_key,
                messages_url=p.messages_url,
                discovery_url=p.discovery_url,
                default_model=p.default_model,
                suggested_models=list(p.suggested_models),
                docs_url=p.docs_url,
                api_key_url=p.api_key_url,
                notes=_t(p.notes_i18n_key) if p.notes_i18n_key else None,
                api_key_pattern=p.api_key_pattern,
                is_recommended=p.is_recommended,
            )
            for p in list_presets()
        ],
        custom_sentinel_id=CUSTOM_SENTINEL_ID,
    )
```

- [ ] **Step 5: 注册路由**

打开 `server/app.py`：
- 在第 36-58 行那段 `from server.routers import (...)` 块中加入 `agent_config`（按字母序）
- 在 `app.include_router(...)` 段（约 290-310 行）增加：`app.include_router(agent_config.router, prefix="/api/v1", tags=["Agent 配置"])`，建议放在 `agent_chat.router` 紧邻位置

- [ ] **Step 6: 运行测试确认通过**

```
uv run python -m pytest tests/test_agent_config_router.py::test_list_preset_providers_returns_catalog tests/test_agent_config_router.py::test_list_preset_providers_requires_auth -v
```
Expected: PASS。

- [ ] **Step 7: lint / format / 提交**

```
uv run ruff check server/routers/agent_config.py server/app.py tests/test_agent_config_router.py
uv run ruff format server/routers/agent_config.py server/app.py tests/test_agent_config_router.py
git add server/routers/agent_config.py server/app.py tests/test_agent_config_router.py
git commit -m "feat(api): GET /agent/preset-providers"
```

### Task 10：`/agent/credentials` CRUD

**Files:**
- Modify: `server/routers/agent_config.py`
- Modify: `tests/test_agent_config_router.py`

- [ ] **Step 1: 写失败测试 — 列表/创建**

追加到 `tests/test_agent_config_router.py`：

```python
@pytest.mark.asyncio
async def test_list_credentials_initially_empty(authed_client) -> None:
    resp = await authed_client.get("/api/v1/agent/credentials")
    assert resp.status_code == 200
    assert resp.json() == {"credentials": []}


@pytest.mark.asyncio
async def test_create_with_preset(authed_client) -> None:
    body = {"preset_id": "deepseek", "api_key": "sk-test"}
    resp = await authed_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 201
    cred = resp.json()
    assert cred["preset_id"] == "deepseek"
    assert cred["base_url"] == "https://api.deepseek.com/anthropic"
    assert cred["model"] == "deepseek-chat"
    assert cred["display_name"] == "DeepSeek"
    assert cred["api_key_masked"].startswith("sk-")
    assert cred["icon_key"] == "DeepSeek"
    # 第一条凭证应自动 active
    assert cred["is_active"] is True


@pytest.mark.asyncio
async def test_create_custom_requires_base_url(authed_client) -> None:
    body = {"preset_id": "__custom__", "api_key": "sk"}
    resp = await authed_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_custom_with_base_url(authed_client) -> None:
    body = {
        "preset_id": "__custom__",
        "display_name": "My Proxy",
        "base_url": "https://proxy.example.com/anthropic",
        "api_key": "sk",
        "model": "claude-sonnet-4",
    }
    resp = await authed_client.post("/api/v1/agent/credentials", json=body)
    assert resp.status_code == 201
    assert resp.json()["base_url"] == "https://proxy.example.com/anthropic"
    assert resp.json()["icon_key"] is None


@pytest.mark.asyncio
async def test_create_unknown_preset_rejected(authed_client) -> None:
    resp = await authed_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "nonexistent", "api_key": "sk"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_credential(authed_client) -> None:
    created = (await authed_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "deepseek", "api_key": "sk1"},
    )).json()
    cid = created["id"]
    resp = await authed_client.patch(
        f"/api/v1/agent/credentials/{cid}",
        json={"display_name": "Renamed", "api_key": "sk2"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "Renamed"


@pytest.mark.asyncio
async def test_delete_active_blocked(authed_client) -> None:
    created = (await authed_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "deepseek", "api_key": "sk"},
    )).json()
    resp = await authed_client.delete(f"/api/v1/agent/credentials/{created['id']}")
    assert resp.status_code == 409
```

- [ ] **Step 2: 运行测试确认失败**

```
uv run python -m pytest tests/test_agent_config_router.py -v -k "credential"
```
Expected: 404/422 错误（路由不存在）。

- [ ] **Step 3: 实现 CRUD 端点**

把以下追加到 `server/routers/agent_config.py`：

```python
# ── Credential models ──────────────────────────────────────────────


class CredentialResponse(BaseModel):
    id: int
    preset_id: str
    display_name: str
    icon_key: str | None
    base_url: str
    api_key_masked: str
    model: str | None
    haiku_model: str | None
    sonnet_model: str | None
    opus_model: str | None
    subagent_model: str | None
    is_active: bool
    created_at: str | None


class CredentialListResponse(BaseModel):
    credentials: list[CredentialResponse]


class CreateCredentialRequest(BaseModel):
    preset_id: str
    display_name: str | None = None
    base_url: str | None = None
    api_key: str
    model: str | None = None
    haiku_model: str | None = None
    sonnet_model: str | None = None
    opus_model: str | None = None
    subagent_model: str | None = None
    activate: bool | None = None  # None = 自动 (无 active 时自动 set active)


class UpdateCredentialRequest(BaseModel):
    display_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    haiku_model: str | None = None
    sonnet_model: str | None = None
    opus_model: str | None = None
    subagent_model: str | None = None


def _cred_to_response(cred) -> CredentialResponse:
    from lib.agent_provider_catalog import get_preset

    preset = get_preset(cred.preset_id) if cred.preset_id != CUSTOM_SENTINEL_ID else None
    return CredentialResponse(
        id=cred.id,
        preset_id=cred.preset_id,
        display_name=cred.display_name,
        icon_key=preset.icon_key if preset else None,
        base_url=cred.base_url,
        api_key_masked=mask_secret(cred.api_key),
        model=cred.model,
        haiku_model=cred.haiku_model,
        sonnet_model=cred.sonnet_model,
        opus_model=cred.opus_model,
        subagent_model=cred.subagent_model,
        is_active=cred.is_active,
        created_at=dt_to_iso(cred.created_at),
    )


# ── Credential endpoints ───────────────────────────────────────────


@router.get("/credentials", response_model=CredentialListResponse)
async def list_credentials(
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialListResponse:
    repo = AgentCredentialRepository(session)
    creds = await repo.list_for_user()
    return CredentialListResponse(credentials=[_cred_to_response(c) for c in creds])


@router.post("/credentials", response_model=CredentialResponse, status_code=201)
async def create_credential(
    body: CreateCredentialRequest,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    from lib.agent_provider_catalog import get_preset

    if body.preset_id != CUSTOM_SENTINEL_ID:
        preset = get_preset(body.preset_id)
        if preset is None:
            raise HTTPException(status_code=422, detail=f"unknown preset: {body.preset_id!r}")
        base_url = body.base_url or preset.messages_url
        display_name = body.display_name or preset.display_name
        model = body.model or preset.default_model
    else:
        if not body.base_url:
            raise HTTPException(status_code=422, detail="base_url required for __custom__ mode")
        base_url = body.base_url
        display_name = body.display_name or "Custom"
        model = body.model

    repo = AgentCredentialRepository(session)
    cred = await repo.create(
        preset_id=body.preset_id,
        display_name=display_name,
        base_url=base_url,
        api_key=body.api_key,
        model=model,
        haiku_model=body.haiku_model,
        sonnet_model=body.sonnet_model,
        opus_model=body.opus_model,
        subagent_model=body.subagent_model,
    )
    # 自动 active 策略：activate=True，或 (activate=None 且当前无 active)
    should_activate = body.activate is True
    if body.activate is None:
        existing_active = await repo.get_active()
        if existing_active is None:
            should_activate = True
    if should_activate:
        await repo.set_active(cred.id)
    await session.commit()
    if should_activate:
        await sync_anthropic_env(session)
    await session.refresh(cred)
    return _cred_to_response(cred)


@router.patch("/credentials/{cred_id}", response_model=CredentialResponse)
async def update_credential(
    cred_id: int,
    body: UpdateCredentialRequest,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    repo = AgentCredentialRepository(session)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    cred = await repo.update(cred_id, **fields)
    if cred is None:
        raise HTTPException(status_code=404, detail="credential not found")
    await session.commit()
    if cred.is_active:
        await sync_anthropic_env(session)
    return _cred_to_response(cred)


@router.delete("/credentials/{cred_id}", status_code=204)
async def delete_credential(
    cred_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    repo = AgentCredentialRepository(session)
    try:
        await repo.delete(cred_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await session.commit()
```

- [ ] **Step 4: 运行测试确认通过**

```
uv run python -m pytest tests/test_agent_config_router.py -v
```
Expected: 全 PASS。

- [ ] **Step 5: 提交**

```
uv run ruff check server/routers/agent_config.py tests/test_agent_config_router.py
uv run ruff format server/routers/agent_config.py tests/test_agent_config_router.py
git add server/routers/agent_config.py tests/test_agent_config_router.py
git commit -m "feat(api): /agent/credentials CRUD"
```


### Task 11：`/agent/credentials/{id}/activate` 切 active

**Files:**
- Modify: `server/routers/agent_config.py`
- Modify: `tests/test_agent_config_router.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_agent_config_router.py`：

```python
@pytest.mark.asyncio
async def test_activate_credential_switches(authed_client, monkeypatch) -> None:
    import os

    a = (await authed_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "deepseek", "api_key": "sk-A"},
    )).json()
    b = (await authed_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "kimi", "api_key": "sk-B", "activate": False},
    )).json()
    # 第一条创建时自动 active；第二条 activate=False
    assert a["is_active"] is True
    assert b["is_active"] is False

    resp = await authed_client.post(f"/api/v1/agent/credentials/{b['id']}/activate")
    assert resp.status_code == 200
    assert resp.json() == {"active_id": b["id"]}

    # 校验 list 中 active 已切换
    listing = (await authed_client.get("/api/v1/agent/credentials")).json()["credentials"]
    flags = {c["id"]: c["is_active"] for c in listing}
    assert flags[a["id"]] is False
    assert flags[b["id"]] is True

    # env 已被同步
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-B"


@pytest.mark.asyncio
async def test_activate_unknown_id(authed_client) -> None:
    resp = await authed_client.post("/api/v1/agent/credentials/99999/activate")
    assert resp.status_code == 404
```

- [ ] **Step 2: 实现端点**

把以下追加到 `server/routers/agent_config.py`：

```python
class ActivateResponse(BaseModel):
    active_id: int


@router.post("/credentials/{cred_id}/activate", response_model=ActivateResponse)
async def activate_credential(
    cred_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> ActivateResponse:
    repo = AgentCredentialRepository(session)
    try:
        await repo.set_active(cred_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await session.commit()
    await sync_anthropic_env(session)
    return ActivateResponse(active_id=cred_id)
```

- [ ] **Step 3: 运行测试 + 提交**

```
uv run python -m pytest tests/test_agent_config_router.py -v -k "activate"
uv run ruff check server/routers/agent_config.py tests/test_agent_config_router.py
uv run ruff format server/routers/agent_config.py tests/test_agent_config_router.py
git add server/routers/agent_config.py tests/test_agent_config_router.py
git commit -m "feat(api): POST /agent/credentials/{id}/activate"
```

### Task 12：`/agent/credentials/{id}/test` + `/agent/test-connection`

**Files:**
- Modify: `server/routers/agent_config.py`
- Modify: `tests/test_agent_config_router.py`

- [ ] **Step 1: 写失败测试 — 草稿测试 + 已存凭证测试（mock probe）**

```python
@pytest.mark.asyncio
async def test_test_connection_draft_calls_run_test(authed_client, monkeypatch) -> None:
    """POST /agent/test-connection 调 run_test 并把结果序列化为 JSON。"""
    from unittest.mock import AsyncMock

    from lib.config import anthropic_probe as probe_mod

    expected = probe_mod.TestConnectionResponse(
        overall="ok",
        messages_probe=probe_mod.ProbeResult(success=True, status_code=200, latency_ms=10, error=None),
        discovery_probe=probe_mod.ProbeResult(success=True, status_code=200, latency_ms=8, error=None),
        diagnosis=None,
        suggestion=None,
        derived_messages_root="https://api.deepseek.com/anthropic",
        derived_discovery_root="https://api.deepseek.com",
    )
    fake = AsyncMock(return_value=expected)
    monkeypatch.setattr("server.routers.agent_config.run_test", fake)

    resp = await authed_client.post(
        "/api/v1/agent/test-connection",
        json={"preset_id": "deepseek", "api_key": "sk", "model": None, "base_url": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] == "ok"
    assert body["messages_probe"]["success"] is True
    assert body["derived_messages_root"] == "https://api.deepseek.com/anthropic"
    fake.assert_awaited_once()


@pytest.mark.asyncio
async def test_test_credential_uses_stored(authed_client, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from lib.config import anthropic_probe as probe_mod

    expected = probe_mod.TestConnectionResponse(
        overall="fail",
        messages_probe=probe_mod.ProbeResult(success=False, status_code=401, latency_ms=12, error="bad"),
        discovery_probe=None,
        diagnosis=probe_mod.DiagnosisCode.AUTH_FAILED,
        suggestion=None,
        derived_messages_root="https://api.deepseek.com/anthropic",
        derived_discovery_root="https://api.deepseek.com",
    )
    fake = AsyncMock(return_value=expected)
    monkeypatch.setattr("server.routers.agent_config.run_test", fake)

    cred = (await authed_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "deepseek", "api_key": "sk-stored"},
    )).json()
    resp = await authed_client.post(f"/api/v1/agent/credentials/{cred['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] == "fail"
    assert body["diagnosis"] == "auth_failed"
    fake.assert_awaited_once()
    kwargs = fake.await_args.kwargs
    assert kwargs["api_key"] == "sk-stored"
    assert kwargs["preset_id"] == "deepseek"
```

- [ ] **Step 2: 实现两端点 + 序列化**

把以下追加到 `server/routers/agent_config.py`：

```python
from lib.config.anthropic_probe import (
    DiagnosisCode,
    ProbeResult as ProbeResultDC,
    SuggestionAction as SuggestionActionDC,
    TestConnectionResponse as TestConnectionResponseDC,
    run_test,
)


class ProbeResultModel(BaseModel):
    success: bool
    status_code: int | None
    latency_ms: int | None
    error: str | None


class SuggestionModel(BaseModel):
    kind: Literal["replace_base_url", "check_api_key", "run_discovery", "see_docs"]
    suggested_value: str | None = None


class TestConnectionResponseModel(BaseModel):
    overall: Literal["ok", "warn", "fail"]
    messages_probe: ProbeResultModel
    discovery_probe: ProbeResultModel | None
    diagnosis: str | None
    suggestion: SuggestionModel | None
    derived_messages_root: str
    derived_discovery_root: str


class TestConnectionRequest(BaseModel):
    preset_id: str | None = None
    base_url: str | None = None
    api_key: str
    model: str | None = None


def _serialize_probe(p: ProbeResultDC | None) -> ProbeResultModel | None:
    if p is None:
        return None
    return ProbeResultModel(
        success=p.success, status_code=p.status_code, latency_ms=p.latency_ms, error=p.error
    )


def _serialize_test_response(r: TestConnectionResponseDC) -> TestConnectionResponseModel:
    return TestConnectionResponseModel(
        overall=r.overall,
        messages_probe=_serialize_probe(r.messages_probe),
        discovery_probe=_serialize_probe(r.discovery_probe),
        diagnosis=r.diagnosis.value if isinstance(r.diagnosis, DiagnosisCode) else None,
        suggestion=SuggestionModel(kind=r.suggestion.kind, suggested_value=r.suggestion.suggested_value)
        if r.suggestion
        else None,
        derived_messages_root=r.derived_messages_root,
        derived_discovery_root=r.derived_discovery_root,
    )


@router.post("/test-connection", response_model=TestConnectionResponseModel)
async def test_connection_draft(
    body: TestConnectionRequest,
    _user: CurrentUser,
    _t: Translator,
) -> TestConnectionResponseModel:
    try:
        result = await run_test(
            preset_id=body.preset_id,
            base_url=body.base_url,
            api_key=body.api_key,
            model=body.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _serialize_test_response(result)


@router.post("/credentials/{cred_id}/test", response_model=TestConnectionResponseModel)
async def test_credential(
    cred_id: int,
    _user: CurrentUser,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> TestConnectionResponseModel:
    repo = AgentCredentialRepository(session)
    cred = await repo.get(cred_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="credential not found")
    try:
        result = await run_test(
            preset_id=cred.preset_id,
            base_url=cred.base_url,
            api_key=cred.api_key,
            model=cred.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _serialize_test_response(result)
```

- [ ] **Step 3: 运行测试 + 提交**

```
uv run python -m pytest tests/test_agent_config_router.py -v
uv run ruff check server/routers/agent_config.py tests/test_agent_config_router.py
uv run ruff format server/routers/agent_config.py tests/test_agent_config_router.py
git add server/routers/agent_config.py tests/test_agent_config_router.py
git commit -m "feat(api): /agent/test-connection + /agent/credentials/{id}/test"
```


---

## Phase 6：`_discover_anthropic` 修复

### Task 13：discovery 走 derive 派生根

**Files:**
- Modify: `lib/custom_provider/discovery.py`
- Test: `tests/test_discover_anthropic_path_fix.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_discover_anthropic_path_fix.py
"""回归：_discover_anthropic 在 base_url 带 anthropic 子路径时也走根 + /v1/models。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_discover_strips_anthropic_suffix() -> None:
    from lib.custom_provider.discovery import _discover_anthropic

    fake_response = MagicMock()
    fake_response.json.return_value = {"data": [{"id": "claude-x", "display_name": "X"}]}
    fake_response.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_response)

    with patch("lib.custom_provider.discovery.get_http_client", return_value=fake_client):
        models = await _discover_anthropic("https://api.deepseek.com/anthropic", "sk")
    fake_client.get.assert_awaited_once()
    called_url = fake_client.get.await_args.args[0]
    assert called_url == "https://api.deepseek.com/v1/models"
    assert models[0]["model_id"] == "claude-x"


@pytest.mark.asyncio
async def test_discover_keeps_root_when_no_suffix() -> None:
    from lib.custom_provider.discovery import _discover_anthropic

    fake_response = MagicMock()
    fake_response.json.return_value = {"data": []}
    fake_response.raise_for_status = MagicMock()
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_response)

    with patch("lib.custom_provider.discovery.get_http_client", return_value=fake_client):
        await _discover_anthropic("https://api.anthropic.com", "sk")
    called_url = fake_client.get.await_args.args[0]
    assert called_url == "https://api.anthropic.com/v1/models"
```

- [ ] **Step 2: 运行测试确认失败**

```
uv run python -m pytest tests/test_discover_anthropic_path_fix.py -v
```
Expected: 第一个 case 失败，请求落在 `…/anthropic/v1/models`。

- [ ] **Step 3: 改 `lib/custom_provider/discovery.py`**

把 `_discover_anthropic` 内 `normalized = ensure_anthropic_base_url(base_url) or "https://api.anthropic.com"` 这行改为：

```python
from lib.config.anthropic_url import derive_anthropic_endpoints

ep = derive_anthropic_endpoints(base_url or "https://api.anthropic.com")
normalized = ep.discovery_root or "https://api.anthropic.com"
```

并删除文件顶部 `from lib.config.url_utils import ensure_anthropic_base_url` 这一 import（仅在 _discover_anthropic 用过）。如果 `lib.config.url_utils.ensure_anthropic_base_url` 别处还有调用，则保留 import；用：

```
grep -rn "ensure_anthropic_base_url" lib server tests --include="*.py"
```
确认。如无其他调用方，连同 `lib/config/url_utils.py:ensure_anthropic_base_url` 函数也删除（避免死代码）。

- [ ] **Step 4: 运行测试确认通过**

```
uv run python -m pytest tests/test_discover_anthropic_path_fix.py -v
```
Expected: 全 PASS。

- [ ] **Step 5: 提交**

```
uv run ruff check lib/custom_provider/discovery.py tests/test_discover_anthropic_path_fix.py
uv run ruff format lib/custom_provider/discovery.py tests/test_discover_anthropic_path_fix.py
git add lib/custom_provider/discovery.py tests/test_discover_anthropic_path_fix.py
# 如同步删了 ensure_anthropic_base_url
git add lib/config/url_utils.py 2>/dev/null || true
git commit -m "fix(discovery): /discover-anthropic 派生 discovery_root，剥 anthropic 子路径"
```

### Task 14：`/discover-anthropic` 凭据回退改读 active credential

**Files:**
- Modify: `server/routers/custom_providers.py`

- [ ] **Step 1: 检查现有逻辑**

```
grep -n "discover-anthropic\|discover_anthropic_models_endpoint" server/routers/custom_providers.py
```
找到 `discover_anthropic_models_endpoint` 函数（约 572 行）。

- [ ] **Step 2: 改写 fallback**

把函数体中「needs_key / needs_url → svc.get_setting("anthropic_api_key", "") / "anthropic_base_url"」的整段，替换为：

```python
body_key = (body.api_key or "").strip()
needs_key = not body_key
needs_url = body.base_url is None

cred = None
if needs_key or needs_url:
    from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

    cred = await AgentCredentialRepository(svc._setting_repo.session).get_active()

if needs_key:
    api_key = (cred.api_key if cred else "").strip()
else:
    api_key = body_key
if not api_key:
    raise HTTPException(status_code=400, detail=_t("anthropic_discovery_no_key"))

if needs_url:
    base_url = (cred.base_url if cred else None) or None
else:
    base_url = body.base_url

return await _run_discover("anthropic", base_url, api_key, _t)
```

注意：`svc._setting_repo.session` 沿用同一会话，避免开第二个 connection。如果 ConfigService 没有 `_setting_repo` 公开属性（看 `lib/config/service.py`），改为通过 `Depends(get_async_session)` 注入第二个 `session: AsyncSession` 参数并直接用之。

- [ ] **Step 3: 测试**

新增 `tests/test_discover_anthropic_fallback.py`：

```python
"""/custom-providers/discover-anthropic 回退到 active credential 的回归测试。"""

import pytest


@pytest.mark.asyncio
async def test_discover_falls_back_to_active_credential(authed_client, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    captured = {}

    async def fake_discover(*, discovery_format, base_url, api_key):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return []

    monkeypatch.setattr("lib.custom_provider.discovery.discover_models", fake_discover)

    # 建一条 active credential
    await authed_client.post(
        "/api/v1/agent/credentials",
        json={"preset_id": "deepseek", "api_key": "stored-sk"},
    )

    resp = await authed_client.post(
        "/api/v1/custom-providers/discover-anthropic",
        json={},  # 都不传
    )
    assert resp.status_code == 200
    assert captured["api_key"] == "stored-sk"
    assert captured["base_url"] == "https://api.deepseek.com/anthropic"


@pytest.mark.asyncio
async def test_discover_no_active_no_body_returns_400(authed_client) -> None:
    resp = await authed_client.post(
        "/api/v1/custom-providers/discover-anthropic",
        json={},
    )
    assert resp.status_code == 400
```

- [ ] **Step 4: 运行测试 + 提交**

```
uv run python -m pytest tests/test_discover_anthropic_fallback.py -v
uv run ruff check server/routers/custom_providers.py tests/test_discover_anthropic_fallback.py
uv run ruff format server/routers/custom_providers.py tests/test_discover_anthropic_fallback.py
git add server/routers/custom_providers.py tests/test_discover_anthropic_fallback.py
git commit -m "fix(api): /discover-anthropic 凭据回退改读 active credential"
```


---

## Phase 7：前端类型 + API 客户端 + 底层组件

### Task 15：TypeScript 类型 + API 客户端方法

**Files:**
- Create: `frontend/src/types/agent-credential.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: 创建类型文件**

```typescript
// frontend/src/types/agent-credential.ts
/**
 * Agent Anthropic 凭证 + 预设供应商目录类型。
 *
 * 与后端 server/routers/agent_config.py 的 Pydantic 模型对齐。
 */

export interface PresetProvider {
  id: string;
  display_name: string;
  icon_key: string;
  messages_url: string;
  discovery_url: string | null;
  default_model: string;
  suggested_models: string[];
  docs_url: string | null;
  api_key_url: string | null;
  notes: string | null;
  api_key_pattern: string | null;
  is_recommended: boolean;
}

export interface PresetProvidersResponse {
  providers: PresetProvider[];
  custom_sentinel_id: string;
}

export interface AgentCredential {
  id: number;
  preset_id: string;
  display_name: string;
  icon_key: string | null;
  base_url: string;
  api_key_masked: string;
  model: string | null;
  haiku_model: string | null;
  sonnet_model: string | null;
  opus_model: string | null;
  subagent_model: string | null;
  is_active: boolean;
  created_at: string | null;
}

export interface CreateAgentCredentialRequest {
  preset_id: string;
  display_name?: string | null;
  base_url?: string | null;
  api_key: string;
  model?: string | null;
  haiku_model?: string | null;
  sonnet_model?: string | null;
  opus_model?: string | null;
  subagent_model?: string | null;
  activate?: boolean | null;
}

export type UpdateAgentCredentialRequest = Partial<
  Omit<CreateAgentCredentialRequest, "preset_id" | "activate">
>;

export interface ProbeResult {
  success: boolean;
  status_code: number | null;
  latency_ms: number | null;
  error: string | null;
}

export type DiagnosisCode =
  | "missing_anthropic_suffix"
  | "openai_compat_only"
  | "auth_failed"
  | "model_not_found"
  | "rate_limited"
  | "network"
  | "unknown";

export interface SuggestionAction {
  kind: "replace_base_url" | "check_api_key" | "run_discovery" | "see_docs";
  suggested_value: string | null;
}

export interface TestConnectionResponse {
  overall: "ok" | "warn" | "fail";
  messages_probe: ProbeResult;
  discovery_probe: ProbeResult | null;
  diagnosis: DiagnosisCode | null;
  suggestion: SuggestionAction | null;
  derived_messages_root: string;
  derived_discovery_root: string;
}

export interface TestConnectionRequest {
  preset_id?: string | null;
  base_url?: string | null;
  api_key: string;
  model?: string | null;
}
```

- [ ] **Step 2: 在 `frontend/src/api.ts` 加 8 个新方法**

定位 `// ==================== 自定义供应商 API ====================` 段；在该段之前插入：

```typescript
// ==================== Agent 配置 / 凭证 API ====================

static async listAgentPresetProviders(): Promise<PresetProvidersResponse> {
  return this.request("/agent/preset-providers");
}

static async listAgentCredentials(): Promise<{ credentials: AgentCredential[] }> {
  return this.request("/agent/credentials");
}

static async createAgentCredential(
  data: CreateAgentCredentialRequest,
): Promise<AgentCredential> {
  return this.request("/agent/credentials", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

static async updateAgentCredential(
  id: number,
  data: UpdateAgentCredentialRequest,
): Promise<AgentCredential> {
  return this.request(`/agent/credentials/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

static async deleteAgentCredential(id: number): Promise<void> {
  return this.request(`/agent/credentials/${id}`, { method: "DELETE" });
}

static async activateAgentCredential(id: number): Promise<{ active_id: number }> {
  return this.request(`/agent/credentials/${id}/activate`, { method: "POST" });
}

static async testAgentCredential(id: number): Promise<TestConnectionResponse> {
  return this.request(`/agent/credentials/${id}/test`, { method: "POST" });
}

static async testAgentConnectionDraft(
  data: TestConnectionRequest,
): Promise<TestConnectionResponse> {
  return this.request("/agent/test-connection", {
    method: "POST",
    body: JSON.stringify(data),
  });
}
```

并在 `frontend/src/api.ts` 顶部 `import type { ... }` 块加入：

```typescript
import type {
  AgentCredential,
  CreateAgentCredentialRequest,
  PresetProvidersResponse,
  TestConnectionRequest,
  TestConnectionResponse,
  UpdateAgentCredentialRequest,
} from "@/types/agent-credential";
```

- [ ] **Step 3: typecheck**

```
cd frontend && pnpm check
```
Expected: 无 TS 报错。

- [ ] **Step 4: lint + 提交**

```
cd frontend && pnpm lint
git add frontend/src/types/agent-credential.ts frontend/src/api.ts
git commit -m "feat(api-client): agent credentials & preset providers TS API"
```

### Task 16：`PresetIcon` 组件 + tests

**Files:**
- Create: `frontend/src/components/agent/PresetIcon.tsx`
- Create: `frontend/src/components/agent/__tests__/PresetIcon.test.tsx`

- [ ] **Step 1: 写失败测试**

```typescript
// frontend/src/components/agent/__tests__/PresetIcon.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PresetIcon } from "../PresetIcon";

describe("PresetIcon", () => {
  it("renders lobehub icon when iconKey known", async () => {
    render(<PresetIcon iconKey="DeepSeek" size={24} />);
    await waitFor(() => expect(document.querySelector("svg")).not.toBeNull());
  });

  it("falls back to monogram on unknown iconKey", async () => {
    render(<PresetIcon iconKey="NonExistentBrand" size={24} />);
    await waitFor(() =>
      expect(screen.getByTestId("preset-icon-monogram")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("preset-icon-monogram").textContent).toBe("N");
  });

  it("falls back to monogram for null iconKey", async () => {
    render(<PresetIcon iconKey={null} size={24} />);
    await waitFor(() =>
      expect(screen.getByTestId("preset-icon-monogram")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("preset-icon-monogram").textContent).toBe("?");
  });
});
```

- [ ] **Step 2: 实现组件**

```tsx
// frontend/src/components/agent/PresetIcon.tsx
import { type ComponentType, useEffect, useState } from "react";

interface IconProps {
  size?: number;
}

type IconLoader = () => Promise<{ default: ComponentType<IconProps> }>;

/**
 * iconKey → @lobehub/icons 子组件路径。
 *
 * 与 lib/agent_provider_catalog.py 的 PresetProvider.icon_key 一一对应。
 * 新增供应商时如缺失映射，组件会 fallback 到 monogram。
 */
const ICON_LOADERS: Record<string, IconLoader> = {
  Anthropic: () => import("@lobehub/icons/es/Anthropic/components/Color"),
  Aws: () => import("@lobehub/icons/es/Aws/components/Color"),
  Bedrock: () => import("@lobehub/icons/es/Bedrock/components/Color"),
  ChatGLM: () => import("@lobehub/icons/es/ChatGLM/components/Color"),
  Claude: () => import("@lobehub/icons/es/Claude/components/Color"),
  ClaudeCode: () => import("@lobehub/icons/es/ClaudeCode/components/Color"),
  DeepSeek: () => import("@lobehub/icons/es/DeepSeek/components/Color"),
  Doubao: () => import("@lobehub/icons/es/Doubao/components/Color"),
  Gemini: () => import("@lobehub/icons/es/Gemini/components/Color"),
  Google: () => import("@lobehub/icons/es/Google/components/Color"),
  Hunyuan: () => import("@lobehub/icons/es/Hunyuan/components/Color"),
  Kimi: () => import("@lobehub/icons/es/Kimi/components/Color"),
  KwaiKAT: () => import("@lobehub/icons/es/KwaiKAT/components/Color"),
  LongCat: () => import("@lobehub/icons/es/LongCat/components/Color"),
  Minimax: () => import("@lobehub/icons/es/Minimax/components/Color"),
  Moonshot: () => import("@lobehub/icons/es/Moonshot/components/Color"),
  Nvidia: () => import("@lobehub/icons/es/Nvidia/components/Color"),
  OpenAI: () => import("@lobehub/icons/es/OpenAI/components/Color"),
  OpenRouter: () => import("@lobehub/icons/es/OpenRouter/components/Color"),
  Qwen: () => import("@lobehub/icons/es/Qwen/components/Color"),
  SiliconCloud: () => import("@lobehub/icons/es/SiliconCloud/components/Color"),
  Stepfun: () => import("@lobehub/icons/es/Stepfun/components/Color"),
  Tencent: () => import("@lobehub/icons/es/Tencent/components/Color"),
  TencentCloud: () => import("@lobehub/icons/es/TencentCloud/components/Color"),
  Volcengine: () => import("@lobehub/icons/es/Volcengine/components/Color"),
  XiaomiMiMo: () => import("@lobehub/icons/es/XiaomiMiMo/components/Color"),
  Zhipu: () => import("@lobehub/icons/es/Zhipu/components/Color"),
};

interface Props {
  iconKey: string | null;
  size?: number;
  className?: string;
}

export function PresetIcon({ iconKey, size = 20, className }: Props) {
  const [Icon, setIcon] = useState<ComponentType<IconProps> | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!iconKey) {
      setIcon(null);
      return;
    }
    const loader = ICON_LOADERS[iconKey];
    if (!loader) {
      setIcon(null);
      return;
    }
    void loader()
      .then((m) => !cancelled && setIcon(() => m.default))
      .catch(() => !cancelled && setIcon(null));
    return () => {
      cancelled = true;
    };
  }, [iconKey]);

  if (Icon) return <span className={className}><Icon size={size} /></span>;
  // Monogram fallback
  const letter = (iconKey?.[0] ?? "?").toUpperCase();
  return (
    <span
      data-testid="preset-icon-monogram"
      className={`inline-flex items-center justify-center rounded-md bg-bg-grad-a text-[11px] font-bold text-text-3 ${className ?? ""}`}
      style={{ width: size, height: size }}
    >
      {letter}
    </span>
  );
}
```

- [ ] **Step 3: 运行测试**

```
cd frontend && pnpm check
cd frontend && pnpm vitest run src/components/agent/__tests__/PresetIcon.test.tsx
```
Expected: 全 PASS。

- [ ] **Step 4: lint + 提交**

```
cd frontend && pnpm lint
git add frontend/src/components/agent/PresetIcon.tsx frontend/src/components/agent/__tests__/PresetIcon.test.tsx
git commit -m "feat(ui): PresetIcon 动态加载 lobehub + monogram fallback"
```


---

## Phase 8：前端 List + Modal + TestPanel

### Task 17：`CredentialList` 组件

**Files:**
- Create: `frontend/src/components/agent/CredentialList.tsx`
- Create: `frontend/src/components/agent/__tests__/CredentialList.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/agent/__tests__/CredentialList.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AgentCredential } from "@/types/agent-credential";

import { CredentialList } from "../CredentialList";

const mockCred = (overrides: Partial<AgentCredential> = {}): AgentCredential => ({
  id: 1,
  preset_id: "deepseek",
  display_name: "DeepSeek",
  icon_key: "DeepSeek",
  base_url: "https://api.deepseek.com/anthropic",
  api_key_masked: "sk-x…abcd",
  model: "deepseek-chat",
  haiku_model: null,
  sonnet_model: null,
  opus_model: null,
  subagent_model: null,
  is_active: false,
  created_at: "2026-05-11T00:00:00Z",
  ...overrides,
});

describe("CredentialList", () => {
  it("calls onActivate when activate clicked", () => {
    const onActivate = vi.fn();
    render(
      <CredentialList
        credentials={[mockCred()]}
        onActivate={onActivate}
        onTest={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /set active|activate/i }));
    expect(onActivate).toHaveBeenCalledWith(1);
  });

  it("disables delete on active credential", () => {
    render(
      <CredentialList
        credentials={[mockCred({ is_active: true })]}
        onActivate={vi.fn()}
        onTest={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    const deleteBtn = screen.getByRole("button", { name: /delete|remove/i });
    expect(deleteBtn).toBeDisabled();
  });

  it("renders empty hint when no credentials", () => {
    render(
      <CredentialList
        credentials={[]}
        onActivate={vi.fn()}
        onTest={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByTestId("credential-list-empty")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 实现 `CredentialList.tsx`**

```tsx
// frontend/src/components/agent/CredentialList.tsx
import { CheckCircle, Edit2, Loader2, PlayCircle, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AgentCredential } from "@/types/agent-credential";
import { CARD_STYLE, GHOST_BTN_CLS, ICON_BTN_CLS } from "@/components/ui/darkroom-tokens";

import { PresetIcon } from "./PresetIcon";

interface Props {
  credentials: AgentCredential[];
  busyId?: number | null;
  onActivate: (id: number) => void;
  onTest: (id: number) => void;
  onEdit: (cred: AgentCredential) => void;
  onDelete: (id: number) => void;
}

export function CredentialList({
  credentials,
  busyId = null,
  onActivate,
  onTest,
  onEdit,
  onDelete,
}: Props) {
  const { t } = useTranslation("dashboard");

  if (credentials.length === 0) {
    return (
      <div
        data-testid="credential-list-empty"
        className="rounded-[10px] border border-dashed border-hairline px-4 py-8 text-center text-[12.5px] text-text-3"
      >
        {t("cred_list_empty")}
      </div>
    );
  }

  return (
    <ul className="grid gap-2.5">
      {credentials.map((c) => (
        <li
          key={c.id}
          className={`relative flex items-center gap-3 rounded-[10px] border px-3 py-3 ${
            c.is_active
              ? "border-accent/40 before:absolute before:bottom-2 before:left-0 before:top-2 before:w-[2px] before:rounded-r before:bg-accent"
              : "border-hairline"
          }`}
          style={CARD_STYLE}
        >
          <PresetIcon iconKey={c.icon_key} size={28} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-[13px] font-medium text-text">
                {c.display_name}
              </span>
              {c.is_active && (
                <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.14em] text-accent">
                  <CheckCircle className="h-2.5 w-2.5" aria-hidden />
                  {t("is_active")}
                </span>
              )}
            </div>
            <div className="mt-0.5 truncate font-mono text-[10.5px] text-text-4">
              {c.base_url} · {c.api_key_masked}
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => onTest(c.id)}
              disabled={busyId === c.id}
              className={GHOST_BTN_CLS}
            >
              {busyId === c.id ? (
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
              ) : (
                <PlayCircle className="h-3.5 w-3.5" aria-hidden />
              )}
              {t("test_credential")}
            </button>
            {!c.is_active && (
              <button
                type="button"
                onClick={() => onActivate(c.id)}
                disabled={busyId === c.id}
                className={GHOST_BTN_CLS}
              >
                {t("set_active")}
              </button>
            )}
            <button
              type="button"
              onClick={() => onEdit(c)}
              className={ICON_BTN_CLS}
              aria-label={t("common:edit")}
            >
              <Edit2 className="h-3.5 w-3.5" aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => onDelete(c.id)}
              disabled={c.is_active || busyId === c.id}
              className={ICON_BTN_CLS}
              aria-label={t("common:delete")}
              title={c.is_active ? t("cred_delete_active_blocked") : undefined}
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}
```

> **Design review 修正（Task 17）**：
> 1. **Active card 视觉强化**：active 凭证加左侧 2px accent 立柱 + `border-accent/40`，让「当前生效凭证」一眼可辨。
> 2. **a11y**：删除带可见文本的按钮上冗余 `aria-label`（test / set active），仅保留纯图标按钮的 `aria-label`（edit / delete）。
> 3. **测试调整**：测试代码 `getByRole("button", { name: /set active|activate/i })` 由可见文本即可匹配，不依赖 aria-label。

- [ ] **Step 3: 跑测试 + 提交**

```
cd frontend && pnpm check
cd frontend && pnpm vitest run src/components/agent/__tests__/CredentialList.test.tsx
cd frontend && pnpm lint
git add frontend/src/components/agent/CredentialList.tsx frontend/src/components/agent/__tests__/CredentialList.test.tsx
git commit -m "feat(ui): CredentialList 凭证卡片网格"
```

### Task 18：`AddCredentialModal` 组件

**Files:**
- Create: `frontend/src/components/agent/AddCredentialModal.tsx`
- Create: `frontend/src/components/agent/__tests__/AddCredentialModal.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/agent/__tests__/AddCredentialModal.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PresetProvider } from "@/types/agent-credential";

import { AddCredentialModal } from "../AddCredentialModal";

const presets: PresetProvider[] = [
  {
    id: "deepseek",
    display_name: "DeepSeek",
    icon_key: "DeepSeek",
    messages_url: "https://api.deepseek.com/anthropic",
    discovery_url: "https://api.deepseek.com",
    default_model: "deepseek-chat",
    suggested_models: ["deepseek-chat"],
    docs_url: null,
    api_key_url: "https://platform.deepseek.com/api_keys",
    notes: null,
    api_key_pattern: null,
    is_recommended: true,
  },
];

describe("AddCredentialModal", () => {
  it("renders custom config chip first", () => {
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const chips = screen.getAllByTestId("preset-chip");
    expect(chips[0]).toHaveTextContent(/custom/i);
  });

  it("when preset chosen, base_url is hidden (auto-filled)", () => {
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/i }));
    expect(screen.queryByLabelText(/base url/i)).not.toBeInTheDocument();
  });

  it("when custom chosen, base_url input shown", () => {
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getAllByTestId("preset-chip")[0]); // custom
    expect(screen.getByLabelText(/base url/i)).toBeInTheDocument();
  });

  it("preset submit payload uses preset_id only", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={onSubmit}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/i }));
    fireEvent.change(screen.getByLabelText(/api key/i), {
      target: { value: "sk-test" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^add$|添加|confirm/i }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ preset_id: "deepseek", api_key: "sk-test" }),
    );
  });

  it("get-api-key link rendered when preset has api_key_url", () => {
    render(
      <AddCredentialModal
        open
        presets={presets}
        customSentinelId="__custom__"
        onSubmit={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /DeepSeek/i }));
    const link = screen.getByRole("link", { name: /get api key|获取/i });
    expect(link).toHaveAttribute("href", "https://platform.deepseek.com/api_keys");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });
});
```

- [ ] **Step 2: 实现 modal（基础壳 + 预设网格）**

```tsx
// frontend/src/components/agent/AddCredentialModal.tsx
import { ExternalLink, Star, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  CreateAgentCredentialRequest,
  PresetProvider,
} from "@/types/agent-credential";
import {
  ACCENT_BTN_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  DROPDOWN_PANEL_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import { ModelCombobox } from "@/components/ui/ModelCombobox";

import { PresetIcon } from "./PresetIcon";

interface Props {
  open: boolean;
  presets: PresetProvider[];
  customSentinelId: string;
  initial?: Partial<CreateAgentCredentialRequest>;
  onSubmit: (req: CreateAgentCredentialRequest) => Promise<void>;
  onClose: () => void;
}

export function AddCredentialModal({
  open,
  presets,
  customSentinelId,
  initial,
  onSubmit,
  onClose,
}: Props) {
  const { t } = useTranslation("dashboard");
  const [tab] = useState<"claude" | "unified">("claude"); // unified: coming soon
  const [presetId, setPresetId] = useState<string>(initial?.preset_id ?? customSentinelId);
  const [apiKey, setApiKey] = useState<string>(initial?.api_key ?? "");
  const [baseUrl, setBaseUrl] = useState<string>(initial?.base_url ?? "");
  const [displayName, setDisplayName] = useState<string>(initial?.display_name ?? "");
  const [model, setModel] = useState<string>(initial?.model ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const selected: PresetProvider | null = useMemo(() => {
    if (presetId === customSentinelId) return null;
    return presets.find((p) => p.id === presetId) ?? null;
  }, [presetId, presets, customSentinelId]);

  // 切换预设时填默认值（不覆盖用户已改的字段：仅在 displayName/model 为空或上一预设默认值时同步）
  useEffect(() => {
    if (selected) {
      setDisplayName((cur) => cur || selected.display_name);
      setModel((cur) => cur || selected.default_model);
    }
  }, [selected]);

  const reset = () => {
    setPresetId(customSentinelId);
    setApiKey("");
    setBaseUrl("");
    setDisplayName("");
    setModel("");
    setSubmitError(null);
  };

  // Esc 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const req: CreateAgentCredentialRequest = {
        preset_id: presetId,
        api_key: apiKey,
        display_name: displayName || undefined,
        base_url: presetId === customSentinelId ? baseUrl : undefined,
        model: model || undefined,
      };
      await onSubmit(req);
      reset();
      onClose();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cred-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-[12px] border border-hairline p-5"
        style={DROPDOWN_PANEL_STYLE}
      >
        {/* Header */}
        <div className="mb-4 flex items-start justify-between">
          <h3 id="cred-modal-title" className="text-[15px] font-medium text-text">
            {t("add_credential")}
          </h3>
          <button onClick={onClose} className="text-text-3 hover:text-text" aria-label="close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Tab */}
        <div className="mb-4 flex gap-1 rounded-[8px] border border-hairline p-1">
          <button
            type="button"
            className={`flex-1 rounded-[6px] py-1.5 text-[12px] font-medium ${
              tab === "claude" ? "bg-accent text-white" : "text-text-3"
            }`}
          >
            {t("claude_compat_providers")}
          </button>
          <button
            type="button"
            disabled
            className="flex-1 rounded-[6px] py-1.5 text-[12px] font-medium text-text-4"
            title={t("unified_providers_coming_soon")}
          >
            {t("unified_providers_coming_soon")}
          </button>
        </div>

        {/* Preset grid — 3 列固定网格，custom 固定首格，推荐项前置 */}
        <div className="mb-5">
          <div className="mb-2 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-text-2">
            {t("select_provider")}
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            <PresetChip
              dataTestid="preset-chip"
              selected={presetId === customSentinelId}
              onClick={() => setPresetId(customSentinelId)}
              label={t("custom_config")}
            />
            {[...presets]
              .sort((a, b) => Number(b.is_recommended) - Number(a.is_recommended))
              .map((p) => (
                <PresetChip
                  key={p.id}
                  dataTestid="preset-chip"
                  selected={presetId === p.id}
                  onClick={() => setPresetId(p.id)}
                  label={p.display_name}
                  iconKey={p.icon_key}
                  recommended={p.is_recommended}
                />
              ))}
          </div>
        </div>

        {/* Form */}
        <div className="space-y-4">
          <Field label={t("display_name")} htmlFor="cred-name">
            <input
              id="cred-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className={INPUT_CLS}
            />
          </Field>

          {presetId === customSentinelId && (
            <Field label={t("api_base_url")} htmlFor="cred-url">
              <input
                id="cred-url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.example.com/anthropic"
                className={INPUT_CLS}
              />
            </Field>
          )}

          <Field
            label={t("anthropic_api_key")}
            htmlFor="cred-key"
            trailing={
              selected?.api_key_url ? (
                <a
                  href={selected.api_key_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
                >
                  {t("get_api_key")}
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              ) : null
            }
          >
            <input
              id="cred-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
              className={INPUT_CLS}
            />
          </Field>

          <Field label={t("default_model")}>
            <ModelCombobox
              value={model}
              onChange={setModel}
              options={selected?.suggested_models ?? []}
              placeholder={selected?.default_model ?? "claude-sonnet-4"}
              clearable
            />
          </Field>

          {selected?.notes && (
            <div className="rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-3 py-2 text-[11.5px] text-text-3">
              {selected.notes}
            </div>
          )}

          {submitError && (
            <div className="text-[11.5px] text-warm-bright">{submitError}</div>
          )}
        </div>

        {/* Footer */}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className={GHOST_BTN_CLS}>
            {t("common:cancel")}
          </button>
          <button
            onClick={() => void handleSubmit()}
            disabled={submitting || !apiKey || (presetId === customSentinelId && !baseUrl)}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {submitting ? t("common:loading") : t("common:add") || "Add"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PresetChip({
  selected,
  onClick,
  label,
  iconKey,
  recommended,
  dataTestid,
}: {
  selected: boolean;
  onClick: () => void;
  label: string;
  iconKey?: string;
  recommended?: boolean;
  dataTestid?: string;
}) {
  return (
    <button
      type="button"
      data-testid={dataTestid}
      onClick={onClick}
      className={`group inline-flex items-center justify-start gap-1.5 truncate rounded-[8px] border px-2.5 py-1.5 text-left text-[12px] transition ${
        selected
          ? "border-accent bg-accent/10 text-accent"
          : recommended
            ? "border-amber-300/40 bg-bg-grad-a/35 text-text-2 hover:border-accent/40"
            : "border-hairline bg-bg-grad-a/35 text-text-2 hover:border-accent/40"
      }`}
    >
      {recommended && (
        <Star
          className="h-3 w-3 shrink-0 fill-amber-300 text-amber-300"
          aria-label="recommended"
        />
      )}
      {iconKey && <PresetIcon iconKey={iconKey} size={14} />}
      <span className="truncate">{label}</span>
    </button>
  );
}

function Field({
  label,
  htmlFor,
  children,
  trailing,
}: {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
  trailing?: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <label htmlFor={htmlFor} className="text-[11.5px] font-medium text-text-2">
          {label}
        </label>
        {trailing}
      </div>
      {children}
    </div>
  );
}
```

- [ ] **Step 3: 跑测试 + 提交**

```
cd frontend && pnpm check
cd frontend && pnpm vitest run src/components/agent/__tests__/AddCredentialModal.test.tsx
cd frontend && pnpm lint
git add frontend/src/components/agent/AddCredentialModal.tsx frontend/src/components/agent/__tests__/AddCredentialModal.test.tsx
git commit -m "feat(ui): AddCredentialModal cc-switch 风格预设选择 + 表单"
```

> **Design review 修正（Task 18）**：
> 1. **`PRIMARY_BTN_CLS` 不存在** → 改用 `ACCENT_BTN_CLS` + `style={ACCENT_BUTTON_STYLE}`（gradient + glow，cc-switch 风格主按钮真容）
> 2. **`bg-bg` 不是 Tailwind 类** → modal panel 改 `style={DROPDOWN_PANEL_STYLE}`（已有 dark gradient + backdrop-blur token）
> 3. **a11y**：modal 加 `role="dialog" aria-modal="true" aria-labelledby="cred-modal-title"` + Esc 关闭 + 点遮罩关闭
> 4. **预设网格**：`flex flex-wrap` → `grid grid-cols-3 gap-1.5`，3 列固定布局；推荐项前置排序（`is_recommended` 排前），custom 永远首格
> 5. **Star 角标**：从 `absolute -right-0.5 -top-1`（会被相邻 chip 截断）→ chip 内部前缀 + `border-amber-300/40` 边框点缀，整体更稳

### Task 19：`TestResultPanel` 组件 (诊断 + Apply Fix)

**Files:**
- Create: `frontend/src/components/agent/TestResultPanel.tsx`

- [ ] **Step 1: 实现组件（无 unit test，集成测在 AgentConfigTab 测）**

```tsx
// frontend/src/components/agent/TestResultPanel.tsx
import { AlertCircle, AlertTriangle, ArrowRight, CheckCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { TestConnectionResponse } from "@/types/agent-credential";
import { ACCENT_BTN_SM_CLS, ACCENT_BUTTON_STYLE } from "@/components/ui/darkroom-tokens";

interface Props {
  /**
   * 触发本次测试时用户看到 / 用的 base_url。可能是空（draft 模式新建预设凭证）；
   * 当 suggestion 为 replace_base_url 时用于 Before/After diff 显示。
   */
  originalBaseUrl?: string | null;
  result: TestConnectionResponse;
  onApplyFix?: (suggestedBaseUrl: string) => void;
}

export function TestResultPanel({ originalBaseUrl, result, onApplyFix }: Props) {
  const { t } = useTranslation("dashboard");
  const {
    overall,
    messages_probe,
    discovery_probe,
    diagnosis,
    suggestion,
    derived_messages_root,
    derived_discovery_root,
  } = result;

  const Icon = overall === "ok" ? CheckCircle : overall === "warn" ? AlertTriangle : AlertCircle;
  // 用项目内既有 utility（accent / warm-bright）+ 中性 oklch alpha 表达 ok/warn/fail
  const tone =
    overall === "ok"
      ? "border-accent/40 bg-accent/5 text-accent"
      : overall === "warn"
      ? "border-amber-300/40 bg-amber-300/5 text-amber-200"
      : "border-warm-bright/40 bg-warm-bright/5 text-warm-bright";

  const headlineKey =
    overall === "ok" ? "test_ok" : overall === "warn" ? "test_warn" : "test_fail";

  const hasReplaceFix =
    suggestion?.kind === "replace_base_url" && !!suggestion.suggested_value && !!onApplyFix;

  return (
    <div className={`mt-3 rounded-[10px] border p-3 ${tone}`} role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-[12.5px] font-medium">
        <Icon className="h-4 w-4" aria-hidden />
        {t(headlineKey)}
      </div>

      {diagnosis && (
        <div className="mt-2 text-[12px] leading-[1.55] text-text-2">
          {t(`diagnosis_${diagnosis}`)}
        </div>
      )}

      {/* Before / After diff — 一键修复的视觉重锤 */}
      {hasReplaceFix && (
        <div className="mt-2.5 rounded-[8px] border border-hairline-soft bg-bg-grad-a/40 p-2.5">
          {originalBaseUrl && (
            <div className="flex items-center gap-2 font-mono text-[10.5px]">
              <span className="w-10 shrink-0 uppercase tracking-[0.12em] text-text-4">
                from
              </span>
              <span className="truncate text-text-4 line-through decoration-warm-bright/60">
                {originalBaseUrl}
              </span>
            </div>
          )}
          <div className="mt-1 flex items-center gap-2 font-mono text-[10.5px]">
            <span className="w-10 shrink-0 uppercase tracking-[0.12em] text-accent">to</span>
            <span className="truncate text-text">{suggestion!.suggested_value}</span>
          </div>
          <div className="mt-2.5 flex justify-end">
            <button
              type="button"
              onClick={() => onApplyFix!(suggestion!.suggested_value!)}
              className={ACCENT_BTN_SM_CLS}
              style={ACCENT_BUTTON_STYLE}
            >
              {t("apply_fix")}
              <ArrowRight className="h-3 w-3" aria-hidden />
            </button>
          </div>
        </div>
      )}

      {/* Probe 结果 — 调用 / 发现端点 */}
      <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-[10.5px] text-text-4">
        <div>
          <div className="uppercase tracking-[0.12em]">{t("derived_messages_root")}</div>
          <div className="truncate text-text-3">{derived_messages_root}</div>
          <div className="text-text-4">
            POST · {messages_probe.status_code ?? "—"} · {messages_probe.latency_ms ?? "—"}ms
          </div>
        </div>
        <div>
          <div className="uppercase tracking-[0.12em]">{t("derived_discovery_root")}</div>
          <div className="truncate text-text-3">{derived_discovery_root || "—"}</div>
          <div className="text-text-4">
            GET ·{" "}
            {discovery_probe
              ? `${discovery_probe.status_code ?? "—"} · ${discovery_probe.latency_ms ?? "—"}ms`
              : "—"}
          </div>
        </div>
      </div>

      {messages_probe.error && (
        <details className="mt-2 text-[11px] text-text-4">
          <summary className="cursor-pointer">raw error</summary>
          <pre className="mt-1 whitespace-pre-wrap break-all">{messages_probe.error}</pre>
        </details>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 提交**

```
cd frontend && pnpm check && pnpm lint
git add frontend/src/components/agent/TestResultPanel.tsx
git commit -m "feat(ui): TestResultPanel 诊断 + Apply Fix"
```

> **Design review 修正（Task 19）**：
> 1. **Apply Fix 改 ACCENT 主按钮** —— 原 `GHOST_BTN_CLS` 弱化了核心 CTA；新版 `ACCENT_BTN_SM_CLS + ACCENT_BUTTON_STYLE` 让用户一眼锁定下一步动作，附 `<ArrowRight>` 强化方向性
> 2. **Before/After diff 视觉** —— 新增 `originalBaseUrl` prop。错误 URL `line-through decoration-warm-bright/60` + 正确 URL `text-text` accent，秒懂自愈发生了什么
> 3. **诊断颜色脱离 emerald-300/rose-300** 等通用 Tailwind 色 → 改用项目内 `accent` / `warm-bright` token，与 darkroom 主题一致；warning 仍保留 amber 因 design system 暂无 warning token
> 4. **a11y**：panel 加 `role="status" aria-live="polite"`，让屏幕阅读器在测试结果出现时即时播报


---

## Phase 9：AgentConfigTab 整合 + i18n

### Task 20：i18n 三语 keys

**Files:**
- Modify: `frontend/src/i18n/zh/dashboard.ts`
- Modify: `frontend/src/i18n/en/dashboard.ts`
- Modify: `frontend/src/i18n/vi/dashboard.ts`

- [ ] **Step 1: zh 加 keys**

在 `frontend/src/i18n/zh/dashboard.ts` 的合适位置（建议放在 `agent_*` 相关 keys 附近）追加：

```typescript
// Agent 凭证目录 / 测试 / 诊断
'agent_credentials': '凭证',
'add_credential': '添加凭证',
'select_provider': '选择供应商',
'claude_compat_providers': 'Claude 供应商',
'unified_providers_coming_soon': '统一供应商 (敬请期待)',
'custom_config': '自定义配置',
'cred_list_empty': '尚未添加凭证。点击「添加凭证」开始。',
'set_active': '设为当前',
'is_active': '当前',
'cred_delete_active_blocked': '当前凭证不可删除，请先切换到其他凭证',
'cred_activated_toast': '已切换到 {{name}}，新会话生效；当前会话继续使用旧凭证',
'display_name': '显示名',
'get_api_key': '获取 API Key',
'test_credential': '连接测试',
'test_running': '测试中…',
'test_ok': '连通正常',
'test_warn': '可调用，但模型发现端点异常',
'test_fail': '无法连通',
'apply_fix': '一键修复',
'apply_fix_hint': '建议改为',
'derived_messages_root': '调用端点',
'derived_discovery_root': '发现端点',
'diagnosis_missing_anthropic_suffix': '检测到 base_url 缺少 anthropic 子路径，已自动探测到正确端点',
'diagnosis_openai_compat_only': '该端点返回 OpenAI 兼容协议，Claude SDK 无法直接使用，请确认是否选错 Plan',
'diagnosis_auth_failed': 'API Key 无效或已过期',
'diagnosis_model_not_found': '模型不存在，请先发现可用模型',
'diagnosis_rate_limited': '触发限流，稍后重试',
'diagnosis_network': '网络无法访问，检查 URL 与防火墙',
'diagnosis_unknown': '未知错误，请查看原始错误信息',
'preset_notes_deepseek': 'DeepSeek 官方 anthropic 兼容端点，需 sk- 开头的 API Key',
'preset_notes_ark_coding': '火山方舟 Coding Plan，普通 ark plan 不兼容 Claude 协议',
'preset_notes_bailian': '阿里百炼无公开 /v1/models 列表，模型发现可能失败',
'preset_notes_xiaomi_mimo': '小米 MiMo 仅支持已知模型名，未公开模型列表',
```

- [ ] **Step 2: en 加对应英文翻译**

```typescript
'agent_credentials': 'Credentials',
'add_credential': 'Add credential',
'select_provider': 'Select provider',
'claude_compat_providers': 'Claude providers',
'unified_providers_coming_soon': 'Unified providers (coming soon)',
'custom_config': 'Custom',
'cred_list_empty': 'No credentials yet. Click "Add credential" to begin.',
'set_active': 'Set active',
'is_active': 'ACTIVE',
'cred_delete_active_blocked': 'Cannot delete the active credential. Activate another first.',
'cred_activated_toast': 'Switched to {{name}}. New sessions will use it; running sessions keep the old credential.',
'display_name': 'Display name',
'get_api_key': 'Get API Key',
'test_credential': 'Test',
'test_running': 'Testing…',
'test_ok': 'Connected',
'test_warn': 'Callable, but discovery endpoint failed',
'test_fail': 'Cannot connect',
'apply_fix': 'Apply fix',
'apply_fix_hint': 'Suggested:',
'derived_messages_root': 'Messages endpoint',
'derived_discovery_root': 'Discovery endpoint',
'diagnosis_missing_anthropic_suffix': 'Detected missing anthropic suffix; auto-discovered the correct endpoint.',
'diagnosis_openai_compat_only': 'This endpoint speaks OpenAI-compat protocol — Claude SDK cannot use it. Check the plan.',
'diagnosis_auth_failed': 'API key is invalid or expired.',
'diagnosis_model_not_found': 'Model not found. Discover available models first.',
'diagnosis_rate_limited': 'Rate limited. Try again later.',
'diagnosis_network': 'Network unreachable. Check URL and firewall.',
'diagnosis_unknown': 'Unknown error. See raw response.',
'preset_notes_deepseek': 'DeepSeek official Anthropic-compat endpoint; needs sk- prefixed key.',
'preset_notes_ark_coding': 'Volcengine Ark Coding Plan only; the regular Ark plan is not Anthropic-compat.',
'preset_notes_bailian': 'Aliyun Bailian has no public /v1/models endpoint; discovery may fail.',
'preset_notes_xiaomi_mimo': 'Xiaomi MiMo only accepts known model names; no public model list.',
```

- [ ] **Step 3: vi 翻译**

参考 `frontend/src/i18n/vi/dashboard.ts` 既有风格，把 zh 内容译为越南语；如时间紧迫可先用与 en 相同的英文文案，并加一行注释 `// TODO(i18n vi): translate`。

- [ ] **Step 4: typecheck + i18n 一致性**

```
cd frontend && pnpm check
uv run python -m pytest tests/test_i18n_consistency.py -v
```

- [ ] **Step 5: 提交**

```
git add frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts frontend/src/i18n/vi/dashboard.ts
git commit -m "feat(i18n): agent credentials & test 三语 keys"
```

### Task 21：`AgentConfigTab.tsx` Section 1 替换

**Files:**
- Modify: `frontend/src/components/pages/AgentConfigTab.tsx`
- Modify: `frontend/src/components/pages/AgentConfigTab.test.tsx`

- [ ] **Step 1: 阅读现有 Section 1 (API Credentials) 与 Section 2 (Model Routing)**

```
sed -n '460,810p' frontend/src/components/pages/AgentConfigTab.tsx
```
理解：删除整个 Section 1（约 463-652 行）；保留 Section 2 / Section 3；保留 `ModelCombobox` discovery 流程，但改为依据 active credential 而非 draft.anthropicKey/Url。

- [ ] **Step 2: 替换 Section 1**

把 Section 1 整段（含 `<Section kicker="API Credentials" ...>...</Section>`）替换为：

```tsx
{/* Section 1: Credentials list + Add */}
<Section
  kicker="Credentials"
  title={t("agent_credentials")}
  description={t("anthropic_key_required_desc")}
  trailing={
    <button
      type="button"
      onClick={() => setAddModalOpen(true)}
      className={GHOST_BTN_CLS}
    >
      + {t("add_credential")}
    </button>
  }
>
  <CredentialList
    credentials={credentials}
    busyId={busyCredId}
    onActivate={(id) => void handleActivate(id)}
    onTest={(id) => void handleTest(id)}
    onEdit={(c) => setEditingCred(c)}
    onDelete={(id) => void handleDelete(id)}
  />
  {testResult && (
    <TestResultPanel
      originalBaseUrl={
        testedCredId != null
          ? credentials.find((c) => c.id === testedCredId)?.base_url ?? null
          : null
      }
      result={testResult}
      onApplyFix={(suggestedUrl) => void handleApplyFix(suggestedUrl)}
    />
  )}
</Section>
```

- [ ] **Step 3: 加状态、handler、effect、ModalAddRender**

把以下 hooks/handlers 加到 `AgentConfigTab` 函数体（替换原有 anthropicKey/anthropicBaseUrl 等）：

```tsx
import type { AgentCredential, PresetProvider, TestConnectionResponse } from "@/types/agent-credential";
import { CredentialList } from "@/components/agent/CredentialList";
import { AddCredentialModal } from "@/components/agent/AddCredentialModal";
import { TestResultPanel } from "@/components/agent/TestResultPanel";

// state
const [credentials, setCredentials] = useState<AgentCredential[]>([]);
const [presets, setPresets] = useState<PresetProvider[]>([]);
const [customSentinelId, setCustomSentinelId] = useState("__custom__");
const [addModalOpen, setAddModalOpen] = useState(false);
const [editingCred, setEditingCred] = useState<AgentCredential | null>(null);
const [busyCredId, setBusyCredId] = useState<number | null>(null);
const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null);
const [testedCredId, setTestedCredId] = useState<number | null>(null);

// load
const loadCreds = useCallback(async () => {
  const [c, p] = await Promise.all([
    API.listAgentCredentials(),
    API.listAgentPresetProviders(),
  ]);
  setCredentials(c.credentials);
  setPresets(p.providers);
  setCustomSentinelId(p.custom_sentinel_id);
}, []);

useEffect(() => { void loadCreds(); }, [loadCreds]);

const handleCreate = async (req: CreateAgentCredentialRequest) => {
  await API.createAgentCredential(req);
  await loadCreds();
  useAppStore.getState().pushToast(t("agent_config_saved"), "success");
};

const handleActivate = async (id: number) => {
  setBusyCredId(id);
  try {
    await API.activateAgentCredential(id);
    await loadCreds();
    const c = credentials.find((x) => x.id === id);
    useAppStore.getState().pushToast(
      t("cred_activated_toast", { name: c?.display_name ?? "" }),
      "success",
    );
  } catch (err) {
    useAppStore.getState().pushToast(errMsg(err), "error");
  } finally {
    setBusyCredId(null);
  }
};

const handleTest = async (id: number) => {
  setBusyCredId(id);
  setTestResult(null);
  setTestedCredId(id);
  try {
    const res = await API.testAgentCredential(id);
    setTestResult(res);
  } catch (err) {
    useAppStore.getState().pushToast(errMsg(err), "error");
  } finally {
    setBusyCredId(null);
  }
};

const handleDelete = async (id: number) => {
  if (!window.confirm(t("common:delete_confirm"))) return;
  try {
    await API.deleteAgentCredential(id);
    await loadCreds();
  } catch (err) {
    useAppStore.getState().pushToast(errMsg(err), "error");
  }
};

const handleApplyFix = async (suggestedUrl: string) => {
  // 用 testedCredId 精确定位最近被测的那条凭证（不是 active —— 用户可能测的就是非 active 的）
  if (testedCredId == null) return;
  await API.updateAgentCredential(testedCredId, { base_url: suggestedUrl });
  await loadCreds();
  setTestResult(null);
  setTestedCredId(null);
  useAppStore.getState().pushToast(t("agent_config_saved"), "success");
};
```

并删除原 anthropicKey/anthropicBaseUrl/import-from-provider/discoverModels 相关 state、helpers、JSX 段（若 Section 2 model discovery 仍要用 `discover-anthropic`，保留即可，不强制改）。

`AddCredentialModal` 渲染挂在 Tab 根节点：

```tsx
<AddCredentialModal
  open={addModalOpen}
  presets={presets}
  customSentinelId={customSentinelId}
  onSubmit={handleCreate}
  onClose={() => setAddModalOpen(false)}
/>
```

- [ ] **Step 4: 更新 AgentConfigTab.test.tsx**

打开 `frontend/src/components/pages/AgentConfigTab.test.tsx`，把所有依赖旧 `anthropic_api_key` / `anthropic_base_url` setting 字段的测试断言改为：mock `API.listAgentCredentials` / `listAgentPresetProviders` 返回固定数据，断言列表与 modal 行为。

参考片段：

```tsx
beforeEach(() => {
  vi.spyOn(API, "listAgentCredentials").mockResolvedValue({ credentials: [] });
  vi.spyOn(API, "listAgentPresetProviders").mockResolvedValue({
    providers: [],
    custom_sentinel_id: "__custom__",
  });
  vi.spyOn(API, "getSystemConfig").mockResolvedValue(/* ... existing fixture ... */);
});
```

具体调整范围以测试运行结果为准。

- [ ] **Step 5: typecheck + lint + test**

```
cd frontend && pnpm check && pnpm lint
cd frontend && pnpm vitest run src/components/pages/AgentConfigTab.test.tsx src/components/agent
```

- [ ] **Step 6: 提交**

```
git add frontend/src/components/pages/AgentConfigTab.tsx frontend/src/components/pages/AgentConfigTab.test.tsx
git commit -m "feat(ui): AgentConfigTab Section 1 → cc-switch 凭证目录"
```


---

## Phase 10：手工 / E2E 验证 + 收尾

### Task 22：全量测试 + 启动 dev server 手工跑通

**Files:** —

- [ ] **Step 1: 跑后端全量 + 覆盖率**

```
uv run python -m pytest -x
```
Expected: 全 PASS。如有偶发 flaky，复跑 1 次。

- [ ] **Step 2: 跑前端全量**

```
cd frontend && pnpm lint && pnpm check
```
Expected: 全绿。

- [ ] **Step 3: 启动 dev server**

```
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241
```

新开终端：

```
cd frontend && pnpm dev
```

打开浏览器访问 `http://localhost:5173`（或 vite 实际端口），登录后进入「设置 → Agent」。

- [ ] **Step 4: 手工矩阵 — 预设凭证**

逐项验证：
- [ ] 点击「+ 添加凭证」→ modal 打开，看到 chip 网格 + 自定义在第一格 + 推荐项有星标
- [ ] 选 DeepSeek → API Key 输入框右上角出现「获取 API Key →」链接，target=_blank
- [ ] 输入 `sk-xxx` → 提交，列表中出现 DeepSeek 一条 + ACTIVE 标记
- [ ] 点击「连接测试」→ 在 1-2 秒内显示绿色「连通正常」+ derived_messages_root = `https://api.deepseek.com/anthropic` + derived_discovery_root = `https://api.deepseek.com`
- [ ] 重复 Kimi、GLM、火山方舟 Coding Plan：均能 OK 或在错误时显示对应 diagnosis 文案

- [ ] **Step 5: 手工矩阵 — 自定义 + probe 自愈**

- [ ] 添加凭证 → 选「自定义配置」→ 填 `https://api.deepseek.com`（无后缀）→ API Key 真实值 → 添加
- [ ] 点连接测试 → 显示「检测到 base_url 缺少 anthropic 子路径」+ Apply Fix 按钮
- [ ] 点 Apply Fix → 列表中该凭证 base_url 自动改为 `https://api.deepseek.com/anthropic`
- [ ] 再点连接测试 → 绿色 ok

- [ ] **Step 6: 手工矩阵 — active 切换**

- [ ] 列表中至少 2 条凭证。点非 active 那条「设为当前」按钮 → toast 出现 + ACTIVE 徽标转移
- [ ] 进入任意项目，发起一次 Agent 对话；确认实际生效凭证为新 active（看 Network 请求 / 服务端日志的 ANTHROPIC_BASE_URL）
- [ ] 删除 active 凭证按钮应禁用；先切到其他凭证再删除生效

- [ ] **Step 7: 回归 — `/discover-anthropic`**

- [ ] AgentConfigTab Section 2 (Model Routing) 的「Discover」按钮点击：应能从 active 凭证派生根域，模型列表返回非空（如已选 DeepSeek 应显示 `deepseek-*` 系列）

- [ ] **Step 8: 记录任何 bug 并回炉 Phase 1-9 修复**

如发现回归，按 root cause 回到对应 Phase 增补步骤；不要在 Phase 10 直接 patch。

### Task 23：旧 `system_settings.anthropic_*` 双轨清理标注

**Files:**
- Modify: `server/routers/system_config.py`

- [ ] **Step 1: 在 PATCH 接口中标注**

定位 `_STRING_SETTINGS` 列表与 anthropic_* 写入逻辑（约第 350-365 行），在前面加注释：

```python
# DEPRECATED: anthropic_* 字段已迁移至 agent_anthropic_credentials 表 (spec 2026-05-11)。
# 这里保留写入仅作为旧客户端兼容；新 UI 走 /api/v1/agent/credentials/* 接口。
# 计划在 0.14.0 版本删除。
```

- [ ] **Step 2: 提交**

```
git add server/routers/system_config.py
git commit -m "chore(deprecation): 标注 system_config 中 anthropic_* 双轨期"
```

### Task 24：PR 准备

- [ ] **Step 1: 跑一次完整 verification**

```
uv run python -m pytest -x --cov=lib --cov=server --cov-report=term
cd frontend && pnpm lint && pnpm check
```

- [ ] **Step 2: 检查变更总览**

```
git log --oneline main..HEAD
git diff --stat main..HEAD
```

- [ ] **Step 3: 创建 PR (人工)**

由开发者按 `commit-commands:commit-push-pr` skill 走 PR 流程，PR 描述要点：
- 关联 issue #476
- 截图：列表 / Modal / 测试结果面板（手工跑过的 DeepSeek 实例）
- 标注「双轨期：旧 `system_settings.anthropic_*` 仍可读写，下版本删除」

---

## Self-Review Checklist (写计划者已执行)

**Spec coverage:**
- §2.1 设计目标 1 (预设目录) → Task 4
- §2.2 多套凭证 + active 切换 → Task 5/6/7/11
- §2.3 真实连接测试 → Task 2/3/12
- §2.4 自定义模式智能补全 → Task 3 (run_test 自愈分支)
- §2.5 修复 `_discover_anthropic` → Task 13/14
- §4.7 sync_anthropic_env 重构 → Task 8
- §4.9 前端 cc-switch UI → Task 15-19, 21
- §4.9.4 i18n 三语 → Task 20
- §6.1 后端单元测试覆盖：URL/probe/catalog/repo/router/discovery → 覆盖
- §6.2 前端单元测试覆盖：PresetIcon/CredentialList/AddCredentialModal/AgentConfigTab → Task 16/17/18/21
- §6.4 手工测试矩阵 → Task 22

**Placeholder scan:** 已逐 Task 检查；剩 `<rev>` / `<DOWN_REV>` 占位仅在 alembic 模板字段，由 alembic 自动生成。

**Type consistency:**
- 后端 `TestConnectionResponse` (dataclass) vs `TestConnectionResponseModel` (Pydantic) 区分清楚，仅在路由序列化层互转
- `AgentCredentialRepository` 命名一致（Task 7、8、10、11、12 全用此名）
- 前端 `PresetProvider` / `AgentCredential` / `TestConnectionResponse` 类型字段与后端 Response 字段一一对齐
- icon_key 在 catalog (Python) 与 ICON_LOADERS (TS) 双向手工对齐；缺失则 fallback monogram

---

## 执行交接

**Plan complete and saved to `docs/superpowers/plans/2026-05-11-agent-url-config-optimization.md`. 两种执行方式：**

1. **Subagent-Driven (推荐)** — 每个 Task 派一个新 subagent 执行，主会话两阶段 review，迭代快、不污染主上下文。
2. **Inline Execution** — 主会话直接顺序跑 Task，按 Phase 边界 checkpoint。

**Which approach?**

