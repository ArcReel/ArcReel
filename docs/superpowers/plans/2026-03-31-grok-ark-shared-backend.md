# Grok & Ark Shared Backend Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create shared modules (`grok_shared.py` / `ark_shared.py`) for Grok and Ark, unify client creation logic, and eliminate duplicate code across three backends.

**Architecture:** 新增两个共享模块 `lib/grok_shared.py` 和 `lib/ark_shared.py`，提供工厂函数。各后端改为调用共享工厂。Grok 文本后端额外从同步 Client 迁移到 AsyncClient。

**Tech Stack:** Python, xai_sdk, volcenginesdkarkruntime, pytest

---

## File Structure

| File | Action | Responsibility |
|------|------|------|
| `lib/ark_shared.py` | Add | `ARK_BASE_URL` constant + `create_ark_client()` factory |
| `lib/grok_shared.py` | Add | `create_grok_client()` factory |
| `lib/image_backends/ark.py` | Modify | Use `create_ark_client()` |
| `lib/video_backends/ark.py` | Modify | Use `create_ark_client()` |
| `lib/text_backends/ark.py` | Modify | Use `create_ark_client()` + import `ARK_BASE_URL` |
| `lib/image_backends/grok.py` | Modify | Use `create_grok_client()` |
| `lib/video_backends/grok.py` | Modify | Use `create_grok_client()` |
| `lib/text_backends/grok.py` | Modify | Use `create_grok_client()` + make async |
| `server/routers/providers.py` | Modify | `_test_ark()` uses `create_ark_client()` |
| `tests/test_image_backends/test_ark.py` | Modify | mock 路径从 `volcenginesdkarkruntime.Ark` 改为 `lib.ark_shared.create_ark_client` |
| `tests/test_video_backend_ark.py` | Modify | 同上 |
| `tests/test_text_backends/test_ark.py` | Modify | 同上 |
| `tests/test_image_backends/test_grok.py` | Modify | mock 路径适配 |
| `tests/test_grok_video_backend.py` | Modify | mock 路径适配 |
| `tests/test_text_backends/test_grok.py` | Modify | mock 路径适配 + 移除 `sync_to_thread` |

---

### Task 1: 创建 `lib/ark_shared.py` + 适配 Ark 图片后端

**Files:**
- Create: `lib/ark_shared.py`
- Modify: `lib/image_backends/ark.py`
- Modify: `tests/test_image_backends/test_ark.py`

- [ ] **Step 1: 创建 `lib/ark_shared.py`**

```python
"""
Ark (火山方舟) 共享工具模块

供 text_backends / image_backends / video_backends / providers 复用。

包含：
- ARK_BASE_URL — 火山方舟 API 基础 URL
- create_ark_client — Ark 客户端工厂
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def create_ark_client(*, api_key: str | None = None):
    """创建 Ark 客户端，统一校验 api_key 并构造。"""
    from volcenginesdkarkruntime import Ark

    resolved_key = api_key or os.environ.get("ARK_API_KEY")
    if not resolved_key:
        raise ValueError("Ark API Key 未提供。请在「全局设置 → 供应商」页面配置 API Key。")
    return Ark(base_url=ARK_BASE_URL, api_key=resolved_key)
```

- [ ] **Step 2: 改造 `lib/image_backends/ark.py`**

将 `__init__` 中的客户端创建逻辑替换为 `create_ark_client()` 调用。

改动前（第 1-48 行）：
```python
"""ArkImageBackend — 火山方舟 Seedream 图片生成后端。"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    image_to_base64_data_uri,
)
from lib.providers import PROVIDER_ARK

logger = logging.getLogger(__name__)


class ArkImageBackend:
    """Ark (火山方舟) Seedream 图片生成后端。"""

    DEFAULT_MODEL = "doubao-seedream-5-0-lite-260128"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        from volcenginesdkarkruntime import Ark

        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError("Ark API Key 未提供。请在「全局设置 → 供应商」页面配置 API Key。")

        self._client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=self._api_key,
        )
        self._model = model or self.DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }
```

改动后：
```python
"""ArkImageBackend — 火山方舟 Seedream 图片生成后端。"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

from lib.ark_shared import create_ark_client
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    image_to_base64_data_uri,
)
from lib.providers import PROVIDER_ARK

logger = logging.getLogger(__name__)


class ArkImageBackend:
    """Ark (火山方舟) Seedream 图片生成后端。"""

    DEFAULT_MODEL = "doubao-seedream-5-0-lite-260128"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_ark_client(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }
```

关键变化：
- 删除 `import os`
- 删除 `from volcenginesdkarkruntime import Ark`
- 新增 `from lib.ark_shared import create_ark_client`
- `__init__` 中删除 `self._api_key` 字段、环境变量读取、校验和手动 `Ark()` 构造
- 替换为单行 `self._client = create_ark_client(api_key=api_key)`

- [ ] **Step 3: 适配测试 `tests/test_image_backends/test_ark.py`**

mock 路径需要从 `volcenginesdkarkruntime.Ark` 改为 `lib.ark_shared.create_ark_client`。

将所有 `patch("volcenginesdkarkruntime.Ark" ...)` 替换为 `patch("lib.ark_shared.create_ark_client" ...)`。

主要改动点：

**`TestArkImageBackendInit` 类：**

改动前：
```python
class TestArkImageBackendInit:
    """构造函数测试。"""

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        from lib.image_backends.ark import ArkImageBackend

        with pytest.raises(ValueError, match="Ark API Key"):
            ArkImageBackend(api_key=None)

    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARK_API_KEY", "env-key")
        with patch("volcenginesdkarkruntime.Ark") as MockArk:
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend()
            MockArk.assert_called_once()
            assert backend.name == PROVIDER_ARK

    def test_api_key_from_param(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("volcenginesdkarkruntime.Ark") as MockArk:
            from lib.image_backends.ark import ArkImageBackend

            ArkImageBackend(api_key="my-key")
            MockArk.assert_called_once_with(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key="my-key",
            )
```

改动后：
```python
class TestArkImageBackendInit:
    """构造函数测试。"""

    def test_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        from lib.image_backends.ark import ArkImageBackend

        with pytest.raises(ValueError, match="Ark API Key"):
            ArkImageBackend(api_key=None)

    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ARK_API_KEY", "env-key")
        with patch("lib.ark_shared.create_ark_client") as mock_create:
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend()
            mock_create.assert_called_once_with(api_key=None)
            assert backend.name == PROVIDER_ARK

    def test_api_key_from_param(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("lib.ark_shared.create_ark_client") as mock_create:
            from lib.image_backends.ark import ArkImageBackend

            ArkImageBackend(api_key="my-key")
            mock_create.assert_called_once_with(api_key="my-key")
```

**`TestArkImageBackendProperties` 类中的 fixtures 和 `test_custom_model`：**

改动前：
```python
    @pytest.fixture()
    def backend(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("volcenginesdkarkruntime.Ark"):
            from lib.image_backends.ark import ArkImageBackend

            return ArkImageBackend(api_key="test-key")

    # ...

    def test_custom_model(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("volcenginesdkarkruntime.Ark"):
            from lib.image_backends.ark import ArkImageBackend

            b = ArkImageBackend(api_key="k", model="custom-model")
            assert b.model == "custom-model"
```

改动后：
```python
    @pytest.fixture()
    def backend(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("lib.ark_shared.create_ark_client"):
            from lib.image_backends.ark import ArkImageBackend

            return ArkImageBackend(api_key="test-key")

    # ...

    def test_custom_model(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        with patch("lib.ark_shared.create_ark_client"):
            from lib.image_backends.ark import ArkImageBackend

            b = ArkImageBackend(api_key="k", model="custom-model")
            assert b.model == "custom-model"
```

**`TestArkImageBackendGenerate` 类的 fixture：**

改动前：
```python
    @pytest.fixture()
    def backend_and_client(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        mock_client = _make_client_mock()
        with patch("volcenginesdkarkruntime.Ark", return_value=mock_client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
        return backend, mock_client
```

改动后：
```python
    @pytest.fixture()
    def backend_and_client(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ARK_API_KEY", raising=False)
        mock_client = _make_client_mock()
        with patch("lib.ark_shared.create_ark_client", return_value=mock_client):
            from lib.image_backends.ark import ArkImageBackend

            backend = ArkImageBackend(api_key="test-key")
        return backend, mock_client
```

- [ ] **Step 4: 运行 Ark 图片后端测试**

Run: `uv run python -m pytest tests/test_image_backends/test_ark.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lib/ark_shared.py lib/image_backends/ark.py tests/test_image_backends/test_ark.py
git commit -m "refactor: 新增 ark_shared.py，Ark 图片后端改用共享工厂"
```

---

### Task 2: 适配 Ark 视频后端

**Files:**
- Modify: `lib/video_backends/ark.py`
- Modify: `tests/test_video_backend_ark.py`

- [ ] **Step 1: 改造 `lib/video_backends/ark.py`**

改动前（第 1-48 行）：
```python
"""ArkVideoBackend — 火山方舟 Ark 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
import os

from lib.providers import PROVIDER_ARK
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
)

logger = logging.getLogger(__name__)


class ArkVideoBackend:
    """Ark (火山方舟) 视频生成后端。"""

    DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError("ARK_API_KEY 环境变量未设置\n请在 .env 文件中添加：ARK_API_KEY=your-api-key")

        from volcenginesdkarkruntime import Ark

        self._client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=self._api_key,
        )
        self._model = model or self.DEFAULT_MODEL
```

改动后：
```python
"""ArkVideoBackend — 火山方舟 Ark 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging

from lib.ark_shared import create_ark_client
from lib.providers import PROVIDER_ARK
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
)

logger = logging.getLogger(__name__)


class ArkVideoBackend:
    """Ark (火山方舟) 视频生成后端。"""

    DEFAULT_MODEL = "doubao-seedance-1-5-pro-251215"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_ark_client(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
```

关键变化：
- 删除 `import os`
- 删除 `from volcenginesdkarkruntime import Ark`（原在 __init__ 内部）
- 新增 `from lib.ark_shared import create_ark_client`
- `__init__` 中删除 `self._api_key` 字段和所有手动逻辑
- 替换为单行 `self._client = create_ark_client(api_key=api_key)`

- [ ] **Step 2: 适配测试 `tests/test_video_backend_ark.py`**

改动点：
1. fixture `backend` 中的 `patch("volcenginesdkarkruntime.Ark", ...)` → `patch("lib.ark_shared.create_ark_client", ...)`
2. `test_missing_api_key_raises` 中的 mock 路径同步更新

改动前（fixture，第 24-31 行）：
```python
@pytest.fixture
def backend(mock_ark_client):
    with patch("volcenginesdkarkruntime.Ark", return_value=mock_ark_client):
        b = ArkVideoBackend(
            api_key="test-ark-key",
        )
    b._client = mock_ark_client
    return b
```

改动后：
```python
@pytest.fixture
def backend(mock_ark_client):
    with patch("lib.ark_shared.create_ark_client", return_value=mock_ark_client):
        b = ArkVideoBackend(
            api_key="test-ark-key",
        )
    b._client = mock_ark_client
    return b
```

改动前（`test_missing_api_key_raises`，第 196-200 行）：
```python
    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("volcenginesdkarkruntime.Ark"):
                with pytest.raises(ValueError, match="ARK_API_KEY"):
                    ArkVideoBackend(api_key=None)
```

改动后：
```python
    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="Ark API Key"):
                ArkVideoBackend(api_key=None)
```

Note: 错误消息从 `"ARK_API_KEY"` 变为 `"Ark API Key"`（统一为 `ark_shared.py` 中的消息）。不再需要 mock `volcenginesdkarkruntime.Ark`，因为 `create_ark_client()` 会在校验失败时直接抛出。

- [ ] **Step 3: 运行 Ark 视频后端测试**

Run: `uv run python -m pytest tests/test_video_backend_ark.py -v`
Expected: all PASS

- [ ] **Step 4: 提交**

```bash
git add lib/video_backends/ark.py tests/test_video_backend_ark.py
git commit -m "refactor: Ark 视频后端改用 ark_shared 共享工厂"
```

---

### Task 3: 适配 Ark 文本后端

**Files:**
- Modify: `lib/text_backends/ark.py`
- Modify: `tests/test_text_backends/test_ark.py`

- [ ] **Step 1: 改造 `lib/text_backends/ark.py`**

改动前（第 1-41 行）：
```python
"""ArkTextBackend — 火山方舟文本生成后端。"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from lib.providers import PROVIDER_ARK
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "doubao-seed-2-0-lite-260215"
_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


class ArkTextBackend:
    """Ark (火山方舟) 文本生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        from volcenginesdkarkruntime import Ark

        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError("Ark API Key 未提供")

        self._client = Ark(
            base_url=_ARK_BASE_URL,
            api_key=self._api_key,
        )
        # Instructor 要求 openai.OpenAI 实例；Ark SDK client 类型不兼容，
        # 但 Ark API 是 OpenAI 兼容的，因此额外创建原生 OpenAI 客户端供降级使用。
        from openai import OpenAI

        self._openai_client = OpenAI(base_url=_ARK_BASE_URL, api_key=self._api_key)
```

改动后：
```python
"""ArkTextBackend — 火山方舟文本生成后端。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lib.ark_shared import ARK_BASE_URL, create_ark_client
from lib.providers import PROVIDER_ARK
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "doubao-seed-2-0-lite-260215"


class ArkTextBackend:
    """Ark (火山方舟) 文本生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        self._client = create_ark_client(api_key=api_key)
        # Instructor 要求 openai.OpenAI 实例；Ark SDK client 类型不兼容，
        # 但 Ark API 是 OpenAI 兼容的，因此额外创建原生 OpenAI 客户端供降级使用。
        from openai import OpenAI

        resolved_key = api_key or __import__("os").environ.get("ARK_API_KEY", "")
        self._openai_client = OpenAI(base_url=ARK_BASE_URL, api_key=resolved_key)
```

关键变化：
- 删除 `import os`
- 删除 `from volcenginesdkarkruntime import Ark`
- 删除 `_ARK_BASE_URL` 局部常量
- 新增 `from lib.ark_shared import ARK_BASE_URL, create_ark_client`
- `__init__` 中删除 `self._api_key`、环境变量读取、校验
- 主客户端改为 `create_ark_client(api_key=api_key)`
- `OpenAI` 兼容客户端保留，但使用 `ARK_BASE_URL`。因为 `self._api_key` 已删除，需要重新解析 key 给 OpenAI 客户端

**Note: ** `self._openai_client` 需要原始 api_key。由于 `create_ark_client()` 内部已处理 env fallback，这里也需要同样的逻辑。更干净的做法是从 `os` 获取：

```python
    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        import os

        self._client = create_ark_client(api_key=api_key)
        # Instructor 要求 openai.OpenAI 实例；Ark SDK client 类型不兼容，
        # 但 Ark API 是 OpenAI 兼容的，因此额外创建原生 OpenAI 客户端供降级使用。
        from openai import OpenAI

        resolved_key = api_key or os.environ.get("ARK_API_KEY", "")
        self._openai_client = OpenAI(base_url=ARK_BASE_URL, api_key=resolved_key)
```

- [ ] **Step 2: 适配测试 `tests/test_text_backends/test_ark.py`**

改动前（mock fixture，第 13-17 行）：
```python
@pytest.fixture
def mock_ark():
    with patch("lib.text_backends.ark.Ark", create=True) as MockArk:
        # Also patch the import inside __init__
        with patch.dict("sys.modules", {"volcenginesdkarkruntime": MagicMock(Ark=MockArk)}):
            yield MockArk
```

改动后：
```python
@pytest.fixture
def mock_ark():
    mock_client = MagicMock()
    with patch("lib.ark_shared.create_ark_client", return_value=mock_client) as mock_create:
        yield mock_create, mock_client
```

由于 fixture 返回值类型变了（从 `MockArk` 到 `(mock_create, mock_client)` 元组），需要更新所有使用 `mock_ark` 的地方。

**TestProperties 类（第 20-39 行）：**

改动前：
```python
class TestProperties:
    def test_name(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.name == "ark"

    def test_default_model(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.model == "doubao-seed-2-0-lite-260215"

    def test_capabilities(self, mock_ark):
        b = ArkTextBackend(api_key="k")
        assert b.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.VISION,
        }

    def test_no_api_key_raises(self, mock_ark):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API Key"):
                ArkTextBackend()
```

改动后（不需要改 —— fixture 仍然提供 mock，`ArkTextBackend(api_key="k")` 正常工作）：

实际上需要微调 —— `mock_ark` 现在返回元组，但 TestProperties 中不需要解包，fixture 仍然激活 patch context。无需改代码，只需确保 fixture 的 context manager 覆盖测试执行。

**TestGenerate 类的 backend fixture（第 42-52 行）：**

改动前：
```python
class TestGenerate:
    @pytest.fixture
    def backend(self, mock_ark):
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b
```

改动后：
```python
class TestGenerate:
    @pytest.fixture
    def backend(self, mock_ark):
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b
```

**TestCapabilityAwareStructured 类的 fixtures（第 70-88 行）：**

改动前：
```python
    @pytest.fixture
    def backend_no_structured(self, mock_ark):
        """创建一个模型不支持原生 structured_output 的 backend。"""
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    @pytest.fixture
    def backend_with_structured(self, mock_ark):
        """创建一个模型支持原生 structured_output 的 backend（模拟）。"""
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        b = ArkTextBackend(api_key="k", model="mock-model-with-structured")
        b._test_client = mock_client
        b._capabilities.add(TextCapability.STRUCTURED_OUTPUT)
        return b
```

改动后：
```python
    @pytest.fixture
    def backend_no_structured(self, mock_ark):
        """创建一个模型不支持原生 structured_output 的 backend。"""
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k")
        b._test_client = mock_client
        return b

    @pytest.fixture
    def backend_with_structured(self, mock_ark):
        """创建一个模型支持原生 structured_output 的 backend（模拟）。"""
        _, mock_client = mock_ark
        b = ArkTextBackend(api_key="k", model="mock-model-with-structured")
        b._test_client = mock_client
        b._capabilities.add(TextCapability.STRUCTURED_OUTPUT)
        return b
```

**`test_unknown_model_falls_back_to_instructor`（第 132-137 行）：**

改动前：
```python
    async def test_unknown_model_falls_back_to_instructor(self, mock_ark):
        """未注册模型保守降级为 Instructor。"""
        mock_client = MagicMock()
        mock_ark.return_value = mock_client
        b = ArkTextBackend(api_key="k", model="unknown-model-xyz")
        assert TextCapability.STRUCTURED_OUTPUT not in b.capabilities
```

改动后：
```python
    async def test_unknown_model_falls_back_to_instructor(self, mock_ark):
        """未注册模型保守降级为 Instructor。"""
        b = ArkTextBackend(api_key="k", model="unknown-model-xyz")
        assert TextCapability.STRUCTURED_OUTPUT not in b.capabilities
```

- [ ] **Step 3: 运行 Ark 文本后端测试**

Run: `uv run python -m pytest tests/test_text_backends/test_ark.py -v`
Expected: all PASS

- [ ] **Step 4: 提交**

```bash
git add lib/text_backends/ark.py tests/test_text_backends/test_ark.py
git commit -m "refactor: Ark 文本后端改用 ark_shared 共享工厂"
```

---

### Task 4: 适配 `providers.py` 中的 Ark 连接测试

**Files:**
- Modify: `server/routers/providers.py`

- [ ] **Step 1: 改造 `_test_ark` 函数**

改动前（第 536-550 行）：
```python
def _test_ark(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 tasks.list 验证 Ark API Key。"""
    from volcenginesdkarkruntime import Ark

    client = Ark(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=config["api_key"],
    )
    # 轻量级调用验证连通性，不创建任何资源
    client.content_generation.tasks.list(page_size=1)
    return ConnectionTestResponse(
        success=True,
        available_models=[],
        message="连接成功",
    )
```

改动后：
```python
def _test_ark(config: dict[str, str]) -> ConnectionTestResponse:
    """通过 tasks.list 验证 Ark API Key。"""
    from lib.ark_shared import create_ark_client

    client = create_ark_client(api_key=config["api_key"])
    # 轻量级调用验证连通性，不创建任何资源
    client.content_generation.tasks.list(page_size=1)
    return ConnectionTestResponse(
        success=True,
        available_models=[],
        message="连接成功",
    )
```

- [ ] **Step 2: 运行全部 Ark 相关测试confirm no regressions**

Run: `uv run python -m pytest tests/test_image_backends/test_ark.py tests/test_video_backend_ark.py tests/test_text_backends/test_ark.py -v`
Expected: all PASS

- [ ] **Step 3: 提交**

```bash
git add server/routers/providers.py
git commit -m "refactor: providers.py Ark 连接测试改用 ark_shared"
```

---

### Task 5: 创建 `lib/grok_shared.py` + 适配 Grok 图片后端

**Files:**
- Create: `lib/grok_shared.py`
- Modify: `lib/image_backends/grok.py`
- Modify: `tests/test_image_backends/test_grok.py`

- [ ] **Step 1: 创建 `lib/grok_shared.py`**

```python
"""
Grok (xAI) 共享工具模块

供 text_backends / image_backends / video_backends 复用。

包含：
- create_grok_client — xAI AsyncClient 客户端工厂
"""

from __future__ import annotations

import logging

import xai_sdk

logger = logging.getLogger(__name__)


def create_grok_client(*, api_key: str) -> xai_sdk.AsyncClient:
    """创建 xAI AsyncClient，统一校验和构造。"""
    if not api_key:
        raise ValueError("XAI_API_KEY 未设置\n请在系统配置页中配置 xAI API Key")
    return xai_sdk.AsyncClient(api_key=api_key)
```

- [ ] **Step 2: 改造 `lib/image_backends/grok.py`**

改动前（第 1-42 行）：
```python
"""GrokImageBackend — xAI Grok (Aurora) 图片生成后端。"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    image_to_base64_data_uri,
)
from lib.providers import PROVIDER_GROK

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-imagine-image"


class GrokImageBackend:
    """xAI Grok (Aurora) 图片生成后端，支持 T2I 和 I2I。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        if not api_key:
            raise ValueError("XAI_API_KEY 未设置\n请在系统配置页中配置 xAI API Key")

        import xai_sdk

        self._client = xai_sdk.AsyncClient(api_key=api_key)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }
```

改动后：
```python
"""GrokImageBackend — xAI Grok (Aurora) 图片生成后端。"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from lib.grok_shared import create_grok_client
from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    image_to_base64_data_uri,
)
from lib.providers import PROVIDER_GROK

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-imagine-image"


class GrokImageBackend:
    """xAI Grok (Aurora) 图片生成后端，支持 T2I 和 I2I。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_grok_client(api_key=api_key or "")
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }
```

关键变化：
- 删除 `import xai_sdk`（原在 __init__ 内部）和内联校验
- 新增 `from lib.grok_shared import create_grok_client`
- `__init__` 中替换为 `create_grok_client(api_key=api_key or "")`
  - 传 `api_key or ""` 是因为 `create_grok_client` 需要 `str` 类型（非 Optional），空字符串会触发校验错误

- [ ] **Step 3: 适配测试 `tests/test_image_backends/test_grok.py`**

当前测试通过 `patch.dict("sys.modules", {"xai_sdk": mock_sdk})` mock 整个 xai_sdk 模块。由于 `grok_shared.py` 在模块级别 `import xai_sdk`，需要改为 mock `lib.grok_shared.create_grok_client`。

改动前（fixture，第 16-23 行）：
```python
@pytest.fixture()
def _patch_xai_sdk():
    """Patch xai_sdk 以免依赖真实 SDK。"""
    mock_sdk = MagicMock()
    mock_client_instance = MagicMock()
    mock_sdk.AsyncClient.return_value = mock_client_instance
    with patch.dict("sys.modules", {"xai_sdk": mock_sdk}):
        yield mock_sdk, mock_client_instance
```

改动后：
```python
@pytest.fixture()
def _patch_xai_sdk():
    """Patch create_grok_client 以免依赖真实 SDK。"""
    mock_client_instance = MagicMock()
    with patch("lib.grok_shared.create_grok_client", return_value=mock_client_instance):
        yield mock_client_instance
```

**使用 fixture 的地方需要适配：**

`backend` fixture（第 26-30 行）：

改动前：
```python
@pytest.fixture()
def backend(_patch_xai_sdk):
    from lib.image_backends.grok import GrokImageBackend

    return GrokImageBackend(api_key="fake-xai-key")
```

改动后（不需要改 —— fixture 仍然覆盖 mock context，`GrokImageBackend(api_key=...)` 会调用 mock 的 `create_grok_client`）。

`backend_pro` fixture 同理不需要改。

**TestInit 类（第 67-78 行）：**

改动前：
```python
class TestInit:
    def test_missing_api_key_raises(self, _patch_xai_sdk):
        from lib.image_backends.grok import GrokImageBackend

        with pytest.raises(ValueError, match="XAI_API_KEY"):
            GrokImageBackend()

    def test_empty_api_key_raises(self, _patch_xai_sdk):
        from lib.image_backends.grok import GrokImageBackend

        with pytest.raises(ValueError, match="XAI_API_KEY"):
            GrokImageBackend(api_key="")
```

改动后：
```python
class TestInit:
    def test_missing_api_key_raises(self, _patch_xai_sdk):
        from lib.image_backends.grok import GrokImageBackend

        with patch("lib.grok_shared.create_grok_client", side_effect=ValueError("XAI_API_KEY 未设置")):
            with pytest.raises(ValueError, match="XAI_API_KEY"):
                GrokImageBackend()

    def test_empty_api_key_raises(self, _patch_xai_sdk):
        from lib.image_backends.grok import GrokImageBackend

        with patch("lib.grok_shared.create_grok_client", side_effect=ValueError("XAI_API_KEY 未设置")):
            with pytest.raises(ValueError, match="XAI_API_KEY"):
                GrokImageBackend(api_key="")
```

Note: 由于 `_patch_xai_sdk` 已经 mock 了 `create_grok_client` 返回成功，错误测试需要用 `side_effect` 覆盖来模拟校验失败。或者更简单的方式 —— 不使用 `_patch_xai_sdk` fixture，直接测试：

```python
class TestInit:
    def test_missing_api_key_raises(self):
        from lib.image_backends.grok import GrokImageBackend

        with pytest.raises(ValueError, match="XAI_API_KEY"):
            GrokImageBackend()

    def test_empty_api_key_raises(self):
        from lib.image_backends.grok import GrokImageBackend

        with pytest.raises(ValueError, match="XAI_API_KEY"):
            GrokImageBackend(api_key="")
```

这更好 —— `create_grok_client` 传入空字符串或 None（通过 `api_key or ""`）会直接校验失败，不需要 mock。但 `grok_shared.py` 顶层 `import xai_sdk` 需要该模块存在。因此仍需保留 `_patch_xai_sdk` 或者改用 `patch.dict("sys.modules", {"xai_sdk": MagicMock()})`。

最佳方案：让 `_patch_xai_sdk` 仍然 mock `sys.modules` 来满足 import，同时对错误测试使用 `side_effect`：

```python
@pytest.fixture(autouse=True)
def _mock_xai_sdk_module():
    """Mock xai_sdk 模块以免依赖真实 SDK 安装。"""
    with patch.dict("sys.modules", {"xai_sdk": MagicMock()}):
        yield


@pytest.fixture()
def _patch_xai_sdk(_mock_xai_sdk_module):
    """Patch create_grok_client 以免依赖真实 SDK。"""
    mock_client_instance = MagicMock()
    with patch("lib.grok_shared.create_grok_client", return_value=mock_client_instance):
        yield mock_client_instance
```

但这对于此测试文件过于复杂。更简单的做法 —— 由于 `grok_shared.py` 已有顶层 `import xai_sdk`，如果 `xai_sdk` 已安装为项目依赖（应该是），则无需 mock `sys.modules`。检查 pyproject.toml 确认。如果 `xai_sdk` 是可选依赖未必安装，则需要保留 module mock。

**实施时判断策略：** 如果 `xai_sdk` 已在 pyproject.toml dependencies 中，直接移除 `sys.modules` mock。如果不在，保留 `autouse` 级别的 module mock + 单独的 `create_grok_client` mock。

- [ ] **Step 4: 运行 Grok 图片后端测试**

Run: `uv run python -m pytest tests/test_image_backends/test_grok.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lib/grok_shared.py lib/image_backends/grok.py tests/test_image_backends/test_grok.py
git commit -m "refactor: 新增 grok_shared.py，Grok 图片后端改用共享工厂"
```

---

### Task 6: 适配 Grok 视频后端

**Files:**
- Modify: `lib/video_backends/grok.py`
- Modify: `tests/test_grok_video_backend.py`

- [ ] **Step 1: 改造 `lib/video_backends/grok.py`**

改动前（第 1-43 行）：
```python
"""GrokVideoBackend — xAI Grok 视频生成后端。"""

from __future__ import annotations

import base64
import logging
from datetime import timedelta
from pathlib import Path

import xai_sdk

from lib.providers import PROVIDER_GROK
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
)

logger = logging.getLogger(__name__)


class GrokVideoBackend:
    """xAI Grok 视频生成后端。"""

    DEFAULT_MODEL = "grok-imagine-video"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        if not api_key:
            raise ValueError("XAI_API_KEY 未设置\n请在系统配置页中配置 xAI API Key")

        self._client = xai_sdk.AsyncClient(api_key=api_key)
        self._model = model or self.DEFAULT_MODEL
```

改动后：
```python
"""GrokVideoBackend — xAI Grok 视频生成后端。"""

from __future__ import annotations

import base64
import logging
from datetime import timedelta
from pathlib import Path

from lib.grok_shared import create_grok_client
from lib.providers import PROVIDER_GROK
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
    download_video,
)

logger = logging.getLogger(__name__)


class GrokVideoBackend:
    """xAI Grok 视频生成后端。"""

    DEFAULT_MODEL = "grok-imagine-video"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self._client = create_grok_client(api_key=api_key or "")
        self._model = model or self.DEFAULT_MODEL
```

关键变化：
- 删除 `import xai_sdk` 顶层导入和内联校验
- 新增 `from lib.grok_shared import create_grok_client`
- `__init__` 替换为 `create_grok_client(api_key=api_key or "")`

- [ ] **Step 2: 适配测试 `tests/test_grok_video_backend.py`**

当前测试用 `@patch("lib.video_backends.grok.xai_sdk")` 来 mock。由于 `xai_sdk` 不再直接在 `video_backends/grok.py` 中导入，需改为 mock `create_grok_client`。

**同步测试（`test_name_and_model`, `test_capabilities`, `test_custom_model`，第 23-47 行）：**

改动前：
```python
    @patch("lib.video_backends.grok.xai_sdk")
    def test_name_and_model(self, mock_sdk):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert backend.name == PROVIDER_GROK
        assert backend.model == "grok-imagine-video"

    @patch("lib.video_backends.grok.xai_sdk")
    def test_capabilities(self, mock_sdk):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
        assert VideoCapability.GENERATE_AUDIO not in backend.capabilities
        assert VideoCapability.NEGATIVE_PROMPT not in backend.capabilities
        assert VideoCapability.SEED_CONTROL not in backend.capabilities

    @patch("lib.video_backends.grok.xai_sdk")
    def test_custom_model(self, mock_sdk):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-2")
        assert backend.model == "grok-imagine-video-2"
```

改动后：
```python
    @patch("lib.grok_shared.create_grok_client")
    def test_name_and_model(self, mock_create):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert backend.name == PROVIDER_GROK
        assert backend.model == "grok-imagine-video"

    @patch("lib.grok_shared.create_grok_client")
    def test_capabilities(self, mock_create):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key")
        assert VideoCapability.TEXT_TO_VIDEO in backend.capabilities
        assert VideoCapability.IMAGE_TO_VIDEO in backend.capabilities
        assert VideoCapability.GENERATE_AUDIO not in backend.capabilities
        assert VideoCapability.NEGATIVE_PROMPT not in backend.capabilities
        assert VideoCapability.SEED_CONTROL not in backend.capabilities

    @patch("lib.grok_shared.create_grok_client")
    def test_custom_model(self, mock_create):
        from lib.video_backends.grok import GrokVideoBackend

        backend = GrokVideoBackend(api_key="test-key", model="grok-imagine-video-2")
        assert backend.model == "grok-imagine-video-2"
```

**`test_missing_api_key_raises`（第 49-53 行）：**

改动前：
```python
    def test_missing_api_key_raises(self):
        from lib.video_backends.grok import GrokVideoBackend

        with pytest.raises(ValueError, match="XAI_API_KEY"):
            GrokVideoBackend(api_key=None)
```

改动后 —— 不需要改。`create_grok_client(api_key="")` 会抛出 ValueError，匹配 `"XAI_API_KEY"`。但需要 `xai_sdk` 模块可导入（`grok_shared.py` 顶层 import）。如果 `xai_sdk` 是已安装依赖则无需改动；否则需加 `sys.modules` mock。

**异步测试（`test_text_to_video`，第 55-105 行）：**

改动前：
```python
    async def test_text_to_video(self, output_path: Path):
        # ...
        with patch("lib.video_backends.grok.xai_sdk") as mock_sdk:
            mock_sdk.AsyncClient.return_value = mock_client

            backend = GrokVideoBackend(api_key="test-key")
```

改动后：
```python
    async def test_text_to_video(self, output_path: Path):
        # ...
        with patch("lib.grok_shared.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")
```

**`test_image_to_video`（第 107-152 行）同理：**

改动前：
```python
        with patch("lib.video_backends.grok.xai_sdk") as mock_sdk:
            mock_sdk.AsyncClient.return_value = mock_client

            backend = GrokVideoBackend(api_key="test-key")
```

改动后：
```python
        with patch("lib.grok_shared.create_grok_client", return_value=mock_client):
            backend = GrokVideoBackend(api_key="test-key")
```

- [ ] **Step 3: 运行 Grok 视频后端测试**

Run: `uv run python -m pytest tests/test_grok_video_backend.py -v`
Expected: all PASS

- [ ] **Step 4: 提交**

```bash
git add lib/video_backends/grok.py tests/test_grok_video_backend.py
git commit -m "refactor: Grok 视频后端改用 grok_shared 共享工厂"
```

---

### Task 7: 适配 Grok 文本后端（含异步化）

**Files:**
- Modify: `lib/text_backends/grok.py`
- Modify: `tests/test_text_backends/test_grok.py`

这是最复杂的任务：同步 `xai_sdk.Client` → 异步 `xai_sdk.AsyncClient`。

- [ ] **Step 1: 改造 `lib/text_backends/grok.py`**

改动前（完整文件）：
```python
"""GrokTextBackend — xAI Grok 文本生成后端。"""

from __future__ import annotations

import asyncio
import logging

from lib.providers import PROVIDER_GROK
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-4-1-fast-reasoning"


class GrokTextBackend:
    """xAI Grok 文本生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        if not api_key:
            raise ValueError("XAI_API_KEY 未设置")

        import xai_sdk

        self._xai_sdk = xai_sdk
        self._client = xai_sdk.Client(api_key=api_key)
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    # ... generate 和辅助方法
```

改动后（完整文件）：
```python
"""GrokTextBackend — xAI Grok 文本生成后端。"""

from __future__ import annotations

import logging

import xai_sdk

from lib.grok_shared import create_grok_client
from lib.providers import PROVIDER_GROK
from lib.text_backends.base import (
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "grok-4-1-fast-reasoning"


class GrokTextBackend:
    """xAI Grok 文本生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        self._xai_sdk = xai_sdk
        self._client = create_grok_client(api_key=api_key or "")
        self._model = model or DEFAULT_MODEL
        self._capabilities: set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    @property
    def name(self) -> str:
        return PROVIDER_GROK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        chat = self._client.chat.create(model=self._model)

        # System prompt
        if request.system_prompt:
            chat.append(self._xai_sdk.chat.system(request.system_prompt))

        # Build user message parts
        user_parts: list = []

        # Images for vision
        if request.images:
            for img_input in request.images:
                if img_input.path:
                    from lib.image_backends.base import image_to_base64_data_uri

                    data_uri = image_to_base64_data_uri(img_input.path)
                    user_parts.append(self._xai_sdk.chat.image(image_url=data_uri))
                elif img_input.url:
                    user_parts.append(self._xai_sdk.chat.image(image_url=img_input.url))

        chat.append(self._xai_sdk.chat.user(request.prompt, *user_parts))

        # Structured output or plain
        if request.response_schema:
            if isinstance(request.response_schema, type):
                DynamicModel = request.response_schema
            else:
                from lib.text_backends.base import resolve_schema

                DynamicModel = _schema_to_pydantic(resolve_schema(request.response_schema))
            response, parsed = await chat.parse(DynamicModel)
            text = response.content if hasattr(response, "content") else parsed.model_dump_json()
        else:
            response = await chat.sample()
            text = response.content if hasattr(response, "content") else str(response)

        # Try to extract token usage from the response
        input_tokens = None
        output_tokens = None
        if hasattr(response, "usage"):
            usage = response.usage
            input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None)

        return TextGenerationResult(
            text=text.strip() if isinstance(text, str) else str(text),
            provider=PROVIDER_GROK,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


# _schema_to_pydantic 保持不变
```

关键变化：
- 删除 `import asyncio`
- 删除内联 `import xai_sdk`，改为顶层 `import xai_sdk`（模块级）
- 新增 `from lib.grok_shared import create_grok_client`
- `__init__` 中删除手动校验，改用 `create_grok_client(api_key=api_key or "")`
- `await asyncio.to_thread(chat.sample)` → `await chat.sample()`
- `await asyncio.to_thread(chat.parse, DynamicModel)` → `await chat.parse(DynamicModel)`

**Fallback Note: ** 如果 `xai_sdk.AsyncClient` 的 `chat` API 不提供 async `sample()` / `parse()`，需退回 `asyncio.to_thread` 方式。实施时通过运行测试确认。

- [ ] **Step 2: 适配测试 `tests/test_text_backends/test_grok.py`**

核心改动：
1. mock 对象从 `xai_sdk.Client` 改为 `create_grok_client` 返回的 mock
2. `sync_to_thread` fixture 不再需要（因为不再用 `asyncio.to_thread`）
3. `chat.sample` / `chat.parse` mock 需要改为 `AsyncMock`（异步调用）

改动前（fixture，第 13-20 行）：
```python
@pytest.fixture
def mock_xai():
    mock_sdk = MagicMock()
    mock_sdk.chat.system = MagicMock(side_effect=lambda x: f"system:{x}")
    mock_sdk.chat.user = MagicMock(side_effect=lambda text, *parts: f"user:{text}")
    mock_sdk.chat.image = MagicMock(side_effect=lambda **kw: f"image:{kw}")
    mock_sdk.Client = MagicMock()
    with patch.dict("sys.modules", {"xai_sdk": mock_sdk, "xai_sdk.chat": mock_sdk.chat}):
        yield mock_sdk
```

改动后：
```python
@pytest.fixture
def mock_xai():
    mock_sdk = MagicMock()
    mock_sdk.chat.system = MagicMock(side_effect=lambda x: f"system:{x}")
    mock_sdk.chat.user = MagicMock(side_effect=lambda text, *parts: f"user:{text}")
    mock_sdk.chat.image = MagicMock(side_effect=lambda **kw: f"image:{kw}")
    with patch.dict("sys.modules", {"xai_sdk": mock_sdk, "xai_sdk.chat": mock_sdk.chat}):
        yield mock_sdk
```

（删除 `mock_sdk.Client = MagicMock()`，不再需要。）

**TestGenerate 类的 backend fixture（第 46-52 行）：**

改动前：
```python
class TestGenerate:
    @pytest.fixture
    def backend(self, mock_xai):
        mock_client = MagicMock()
        mock_xai.Client.return_value = mock_client
        b = GrokTextBackend(api_key="k")
        b._test_client = mock_client
        return b
```

改动后：
```python
class TestGenerate:
    @pytest.fixture
    def backend(self, mock_xai):
        mock_client = MagicMock()
        with patch("lib.grok_shared.create_grok_client", return_value=mock_client):
            b = GrokTextBackend(api_key="k")
        b._test_client = mock_client
        return b
```

**`test_plain_text`（第 54-64 行）：**

改动前：
```python
    async def test_plain_text(self, backend, sync_to_thread):
        mock_chat = MagicMock()
        mock_response = SimpleNamespace(content="  grok output  ")
        mock_chat.sample = MagicMock(return_value=mock_response)
        backend._test_client.chat.create.return_value = mock_chat

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "grok output"
        assert result.provider == "grok"
```

改动后：
```python
    async def test_plain_text(self, backend):
        mock_chat = MagicMock()
        mock_response = SimpleNamespace(content="  grok output  ")
        mock_chat.sample = AsyncMock(return_value=mock_response)
        backend._test_client.chat.create.return_value = mock_chat

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "grok output"
        assert result.provider == "grok"
```

关键变化：
- 移除 `sync_to_thread` fixture 参数
- `mock_chat.sample = MagicMock(...)` → `mock_chat.sample = AsyncMock(...)`

**`test_structured_output`（第 66-77 行）：**

改动前：
```python
    async def test_structured_output(self, backend, sync_to_thread):
        mock_chat = MagicMock()
        mock_response = SimpleNamespace(content='{"name": "test"}')
        mock_parsed = MagicMock()
        mock_parsed.model_dump_json.return_value = '{"name": "test"}'
        mock_chat.parse = MagicMock(return_value=(mock_response, mock_parsed))
        backend._test_client.chat.create.return_value = mock_chat

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        result = await backend.generate(TextGenerationRequest(prompt="gen", response_schema=schema))

        assert result.text == '{"name": "test"}'
```

改动后：
```python
    async def test_structured_output(self, backend):
        mock_chat = MagicMock()
        mock_response = SimpleNamespace(content='{"name": "test"}')
        mock_parsed = MagicMock()
        mock_parsed.model_dump_json.return_value = '{"name": "test"}'
        mock_chat.parse = AsyncMock(return_value=(mock_response, mock_parsed))
        backend._test_client.chat.create.return_value = mock_chat

        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        result = await backend.generate(TextGenerationRequest(prompt="gen", response_schema=schema))

        assert result.text == '{"name": "test"}'
```

关键变化：
- 移除 `sync_to_thread` fixture 参数
- `mock_chat.parse = MagicMock(...)` → `mock_chat.parse = AsyncMock(...)`

别忘了At the top of the file添加 `AsyncMock` 的导入：

```python
from unittest.mock import AsyncMock, MagicMock, patch
```

（原来只有 `MagicMock, patch`。）

- [ ] **Step 3: 运行 Grok 文本后端测试**

Run: `uv run python -m pytest tests/test_text_backends/test_grok.py -v`
Expected: all PASS

如果 `chat.sample()` / `chat.parse()` 在 AsyncClient 上不是 async 方法，测试会报错。此时需要退回 `asyncio.to_thread` 方案并恢复 `sync_to_thread` + `MagicMock`。

- [ ] **Step 4: 提交**

```bash
git add lib/text_backends/grok.py tests/test_text_backends/test_grok.py
git commit -m "refactor: Grok 文本后端改用 grok_shared 共享工厂 + 异步化"
```

---

### Task 8: 全量验证 + ruff

**Files:** 无新改动

- [ ] **Step 1: ruff 检查**

Run: `uv run ruff check lib/ark_shared.py lib/grok_shared.py lib/image_backends/ark.py lib/image_backends/grok.py lib/video_backends/ark.py lib/video_backends/grok.py lib/text_backends/ark.py lib/text_backends/grok.py server/routers/providers.py`
Expected: 无错误

如有错误，修复后重新 commit。

- [ ] **Step 2: ruff format 检查**

Run: `uv run ruff format --check lib/ark_shared.py lib/grok_shared.py lib/image_backends/ark.py lib/image_backends/grok.py lib/video_backends/ark.py lib/video_backends/grok.py lib/text_backends/ark.py lib/text_backends/grok.py server/routers/providers.py`
Expected: 无需格式化

如有需要，运行 `uv run ruff format <files>` 并提交。

- [ ] **Step 3: 全量测试**

Run: `uv run python -m pytest -v`
Expected: all PASS，无回归

- [ ] **Step 4: 如有 lint/format 修复，提交**

```bash
git add -u
git commit -m "style: ruff lint/format 修复"
```
