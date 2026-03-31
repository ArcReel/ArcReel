# Grok & Ark 共享后端重构设计

## 背景

当前 AI 后端中，OpenAI 通过 `openai_shared.py` 提供 `create_openai_client()` 工厂函数，
Gemini 通过 `gemini_shared.py` 提供共享 RateLimiter + 重试机制。
但 Grok 和 Ark 的 image/video/text 三个后端各自独立创建客户端，存在重复的初始化逻辑、
校验逻辑和硬编码常量。

## 目标

为 Grok 和 Ark 各创建一个共享模块（`grok_shared.py` / `ark_shared.py`），
提供统一的客户端工厂函数，消除三处后端中的重复代码。采用与 `openai_shared.py` 相同的模式。

## 设计

### 1. `lib/grok_shared.py`

新增模块，职责：
- 提供 `create_grok_client(*, api_key: str) -> xai_sdk.AsyncClient` 工厂函数
- 统一 API Key 校验逻辑和错误消息

```python
"""
Grok (xAI) 共享工具模块

供 text_backends / image_backends / video_backends 复用。
"""
from __future__ import annotations
import xai_sdk

def create_grok_client(*, api_key: str) -> xai_sdk.AsyncClient:
    """创建 xAI AsyncClient，统一校验和构造。"""
    if not api_key:
        raise ValueError("XAI_API_KEY 未设置\n请在系统配置页中配置 xAI API Key")
    return xai_sdk.AsyncClient(api_key=api_key)
```

### 2. `lib/ark_shared.py`

新增模块，职责：
- 导出 `ARK_BASE_URL` 常量（消除三处硬编码）
- 提供 `create_ark_client(*, api_key: str | None = None) -> Ark` 工厂函数
- 统一 API Key 校验（支持环境变量 fallback）和错误消息

```python
"""
Ark (火山方舟) 共享工具模块

供 text_backends / image_backends / video_backends 复用。
"""
from __future__ import annotations
import os

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

def create_ark_client(*, api_key: str | None = None):
    """创建 Ark 客户端，统一校验和构造。"""
    from volcenginesdkarkruntime import Ark

    resolved_key = api_key or os.environ.get("ARK_API_KEY")
    if not resolved_key:
        raise ValueError("Ark API Key 未提供。请在「全局设置 → 供应商」页面配置 API Key。")
    return Ark(base_url=ARK_BASE_URL, api_key=resolved_key)
```

### 3. Grok 后端改造

#### image_backends/grok.py
- 删除 `import xai_sdk` 和内联 API Key 校验
- `__init__` 改为 `self._client = create_grok_client(api_key=api_key)`

#### video_backends/grok.py
- 同上：删除 `import xai_sdk` 顶层导入和内联校验
- `__init__` 改为 `self._client = create_grok_client(api_key=api_key)`

#### text_backends/grok.py（最大变更）
- 同步 `xai_sdk.Client` 改为异步 `xai_sdk.AsyncClient`（通过 `create_grok_client()`）
- `asyncio.to_thread(chat.sample)` → `await chat.sample()`
- `asyncio.to_thread(chat.parse, ...)` → `await chat.parse(...)`
- 删除 `import asyncio`
- 保留 `self._xai_sdk = xai_sdk`（仍需 `xai_sdk.chat.system()` 等构造器）
- **Fallback**：若 AsyncClient 的 chat API 与 Client 不一致，退回 `to_thread` + 同步调用

### 4. Ark 后端改造

#### image_backends/ark.py
- 删除 `from volcenginesdkarkruntime import Ark`、`os.environ` 读取、base_url 硬编码
- `__init__` 改为 `self._client = create_ark_client(api_key=api_key)`
- 删除 `self._api_key` 字段（不再需要）

#### video_backends/ark.py
- 同上
- 删除 `self._api_key` 字段

#### text_backends/ark.py
- 主客户端改为 `self._client = create_ark_client(api_key=api_key)`
- `_ARK_BASE_URL` 局部常量改为从 `ark_shared` 导入 `ARK_BASE_URL`
- `OpenAI` 兼容客户端保留在文本后端内部（Instructor 降级专用）

### 5. 不改动的部分

- `openai_shared.py` / `gemini_shared.py` — 维持现状
- `lib/config/` 配置系统 — 不受影响
- `text_backends/ark.py` 的 OpenAI 兼容客户端 — 留在原处

## 变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `lib/grok_shared.py` | 新增 | 工厂函数 |
| `lib/ark_shared.py` | 新增 | 工厂函数 + base_url 常量 |
| `lib/image_backends/grok.py` | 改动 | 改用 `create_grok_client()` |
| `lib/video_backends/grok.py` | 改动 | 改用 `create_grok_client()` |
| `lib/text_backends/grok.py` | 改动 | 改用 `create_grok_client()` + 异步化 |
| `lib/image_backends/ark.py` | 改动 | 改用 `create_ark_client()` |
| `lib/video_backends/ark.py` | 改动 | 改用 `create_ark_client()` |
| `lib/text_backends/ark.py` | 改动 | 主客户端改用 `create_ark_client()` |

## 测试策略

纯重构，行为不变。`ruff check` + `pytest` 全量跑通即可，无需新增测试。
如有涉及 Grok/Ark 后端的 mock，需适配新的 import 路径。
