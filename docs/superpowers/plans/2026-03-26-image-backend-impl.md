# Image Backend 通用图片生成服务层实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提取 `ImageBackend` 抽象层，接入 Gemini/Ark/Grok 三大供应商的图片生成能力，同时将 `seedance` 重命名为 `ark`。

**Architecture:** 镜像 `lib/video_backends/` 的 Protocol + Registry + 具体实现模式，在 `lib/image_backends/` 下创建对称结构。重构 `MediaGenerator` 和 `generation_tasks.py` 以注入 `ImageBackend`，移除 `GeminiClient` 图片生成直调。

**Tech Stack:** Python 3.12, SQLAlchemy async, google-genai SDK, volcenginesdkarkruntime (Ark), xai-sdk (Grok), Alembic migrations, pytest (asyncio_mode=auto)

**Spec:** `docs/superpowers/specs/2026-03-26-image-backend-design.md`

---

## 文件变更清单

### 新建文件

| 文件 | 职责 |
|------|------|
| `lib/image_backends/__init__.py` | 公共 API 导出 + auto-register backends |
| `lib/image_backends/base.py` | `ImageBackend` Protocol + `ImageGenerationRequest/Result` + `ImageCapability` enum |
| `lib/image_backends/registry.py` | factory registry (`create_backend`/`register_backend`) |
| `lib/image_backends/gemini.py` | `GeminiImageBackend` (AI Studio + Vertex AI) |
| `lib/image_backends/ark.py` | `ArkImageBackend` (Seedream) |
| `lib/image_backends/grok.py` | `GrokImageBackend` (Aurora) |
| `lib/video_backends/ark.py` | 从 `seedance.py` 重命名 |
| `alembic/versions/xxxx_rename_seedance_to_ark.py` | DB migration |
| `tests/test_image_backends/test_base.py` | base 数据模型测试 |
| `tests/test_image_backends/test_registry.py` | registry 测试 |
| `tests/test_image_backends/test_gemini.py` | GeminiImageBackend 测试 |
| `tests/test_image_backends/test_ark.py` | ArkImageBackend 测试 |
| `tests/test_image_backends/test_grok.py` | GrokImageBackend 测试 |

### 修改文件

| 文件 | 变更概述 |
|------|---------|
| `lib/video_backends/base.py` | `PROVIDER_SEEDANCE` → `PROVIDER_ARK` |
| `lib/video_backends/__init__.py` | 更新 import/注册，使用 ark |
| `lib/video_backends/seedance.py` | 删除（重命名为 ark.py） |
| `lib/config/registry.py` | `"seedance"` → `"ark"`，grok 加 image |
| `server/routers/system_config.py` | `_PROVIDER_MODELS` 更新 |
| `server/routers/providers.py` | `_test_seedance` → `_test_ark`，dispatch dict 更新 |
| `lib/config/migration.py` | legacy env 映射 `"seedance"` → `"ark"` |
| `frontend/src/components/ui/ProviderIcon.tsx` | `"seedance"` → `"ark"` display name/icon |
| `lib/cost_calculator.py` | 重命名 seedance → ark + 新增 image cost 方法 |
| `lib/db/repositories/usage_repo.py` | 成本路由扩展 |
| `server/services/generation_tasks.py` | 删除 `_resolve_image_backend`，新增 `_get_or_create_image_backend` |
| `lib/generation_worker.py` | `_normalize_provider_id` 加 seedance→ark |
| `lib/media_generator.py` | 注入 `image_backend`，移除 GeminiClient 图片依赖 |
| `lib/gemini_client.py` | 删除 image/video 方法，精简为文本客户端 |
| `tests/fakes.py` | 新增 `FakeImageBackend` |
| `tests/test_video_backend_seedance.py` | 重命名为 `tests/test_video_backend_ark.py`，更新引用 |

---

### Task 1: `seedance` → `ark` 全局重命名

**Files:**
- Modify: `lib/video_backends/base.py:13-15`
- Create: `lib/video_backends/ark.py` (从 `seedance.py` 迁移)
- Delete: `lib/video_backends/seedance.py`
- Modify: `lib/video_backends/__init__.py`
- Modify: `lib/config/registry.py:36-44`
- Modify: `lib/cost_calculator.py:74-84`
- Modify: `lib/db/repositories/usage_repo.py:119`
- Modify: `server/services/generation_tasks.py:31,42-55,106-110`
- Modify: `lib/generation_worker.py:116-122`
- Modify: `server/routers/system_config.py:47-54`

- [ ] **Step 1: 更新 `lib/video_backends/base.py` 常量**

```python
# 将第 14 行
PROVIDER_SEEDANCE = "seedance"
# 改为
PROVIDER_ARK = "ark"
```

- [ ] **Step 2: 创建 `lib/video_backends/ark.py`**

复制 `lib/video_backends/seedance.py` 的内容，做以下替换：
- 类名 `SeedanceVideoBackend` → `ArkVideoBackend`
- import `PROVIDER_SEEDANCE` → `PROVIDER_ARK`
- `name` 属性返回 `PROVIDER_ARK`
- 日志中 `"Seedance"` → `"Ark"`

- [ ] **Step 3: 删除 `lib/video_backends/seedance.py`**

```bash
git rm lib/video_backends/seedance.py
```

- [ ] **Step 4: 更新 `lib/video_backends/__init__.py`**

```python
from lib.video_backends.base import (
    PROVIDER_GEMINI,
    PROVIDER_GROK,
    PROVIDER_ARK,       # was PROVIDER_SEEDANCE
    ...
)

__all__ = [
    "PROVIDER_GEMINI",
    "PROVIDER_GROK",
    "PROVIDER_ARK",     # was PROVIDER_SEEDANCE
    ...
]

# Auto-register backends
from lib.video_backends.ark import ArkVideoBackend       # was seedance
register_backend(PROVIDER_ARK, ArkVideoBackend)           # was PROVIDER_SEEDANCE
```

- [ ] **Step 5: 更新 `lib/config/registry.py`**

```python
"ark": ProviderMeta(
    display_name="Ark (火山方舟)",
    description="字节跳动火山方舟 AI 服务，支持 Seedance 视频和 Seedream 图片生成。",
    media_types=["video", "image"],
    required_keys=["api_key"],
    optional_keys=["file_service_base_url", "image_rpm", "video_rpm", "request_gap",
                    "image_max_workers", "video_max_workers"],
    secret_keys=["api_key"],
    capabilities=["text_to_video", "image_to_video", "text_to_image", "image_to_image",
                   "generate_audio", "seed_control", "flex_tier"],
),
"grok": ProviderMeta(
    display_name="Grok",
    description="xAI Grok 模型，支持视频和图片生成。",
    media_types=["video", "image"],
    required_keys=["api_key"],
    optional_keys=["image_rpm", "video_rpm", "request_gap",
                    "image_max_workers", "video_max_workers"],
    secret_keys=["api_key"],
    capabilities=["text_to_video", "image_to_video", "text_to_image", "image_to_image"],
),
```

- [ ] **Step 6: 更新 `lib/cost_calculator.py`**

重命名常量和方法：
- `SEEDANCE_VIDEO_COST` → `ARK_VIDEO_COST`
- `DEFAULT_SEEDANCE_MODEL` → `DEFAULT_ARK_VIDEO_MODEL`
- `calculate_seedance_video_cost` → `calculate_ark_video_cost`

- [ ] **Step 7: 更新 `lib/db/repositories/usage_repo.py`**

```python
# 将 PROVIDER_SEEDANCE → PROVIDER_ARK 的 import 和匹配
from lib.video_backends.base import PROVIDER_ARK, PROVIDER_GEMINI, PROVIDER_GROK

if effective_provider == PROVIDER_ARK and row.call_type == "video":
    cost_amount, currency = cost_calculator.calculate_ark_video_cost(...)
```

- [ ] **Step 8: 更新 `server/services/generation_tasks.py`**

```python
from lib.video_backends.base import PROVIDER_GEMINI, PROVIDER_GROK, PROVIDER_ARK

_DEFAULT_VIDEO_RESOLUTION: dict[str, str] = {
    PROVIDER_GEMINI: "1080p",
    PROVIDER_ARK: "720p",      # was PROVIDER_SEEDANCE
    PROVIDER_GROK: "720p",
}

_PROVIDER_ID_TO_BACKEND: dict[str, str] = {
    "gemini-aistudio": PROVIDER_GEMINI,
    "gemini-vertex": PROVIDER_GEMINI,
    PROVIDER_GEMINI: PROVIDER_GEMINI,
    PROVIDER_ARK: PROVIDER_ARK,     # was PROVIDER_SEEDANCE
    PROVIDER_GROK: PROVIDER_GROK,
}
```

在 `_get_or_create_video_backend` 中 `elif backend_name == PROVIDER_SEEDANCE:` → `elif backend_name == PROVIDER_ARK:`，DB config 从 `"seedance"` → `"ark"`。

- [ ] **Step 9: 更新 `lib/generation_worker.py` `_normalize_provider_id`**

```python
def _normalize_provider_id(raw: str) -> str:
    mapping = {
        "gemini": "gemini-aistudio",
        "vertex": "gemini-vertex",
        "seedance": "ark",        # 向后兼容
    }
    return mapping.get(raw, raw)
```

- [ ] **Step 10: 更新 `server/routers/system_config.py` `_PROVIDER_MODELS`**

```python
"ark": {
    "video": ["doubao-seedance-1-5-pro-251215"],
    "image": ["doubao-seedream-5-0-260128", "doubao-seedream-5-0-lite-260128",
               "doubao-seedream-4-5-251128", "doubao-seedream-4-0-250828"],
},
"grok": {
    "video": ["grok-imagine-video"],
    "image": ["grok-imagine-image", "grok-imagine-image-pro"],
},
```

- [ ] **Step 11: 更新 `server/routers/providers.py`**

重命名 `_test_seedance` → `_test_ark`，更新 dispatch dict 中的 key `"seedance"` → `"ark"`。

- [ ] **Step 12: 更新 `lib/config/migration.py`**

将 legacy env 映射 `("ark_api_key", "seedance", ...)` 改为 `("ark_api_key", "ark", ...)`。

- [ ] **Step 13: 更新 `frontend/src/components/ui/ProviderIcon.tsx`**

将 `"seedance"` 引用改为 `"ark"`（display name 和 icon 条件）。

- [ ] **Step 14: 重命名测试文件**

将 `tests/test_video_backend_seedance.py` 重命名为 `tests/test_video_backend_ark.py`，更新内部引用。

- [ ] **Step 15: 全局搜索替换残留引用**

搜索 `PROVIDER_SEEDANCE`、`"seedance"` 的所有残留，确保全部更新为 `PROVIDER_ARK`/`"ark"`。注意保留 `_normalize_provider_id` 中的向后兼容 `"seedance"` 字符串和 migration 中的 SQL 值。

- [ ] **Step 16: 运行测试验证重命名无破坏**

Run: `uv run python -m pytest -x -q`
Expected: 全部 PASS（或仅存在与本次无关的已有失败）

- [ ] **Step 17: 提交**

```bash
git add -A && git commit -m "refactor: rename seedance provider to ark"
```

---

### Task 2: Alembic Migration `seedance` → `ark`

**Files:**
- Create: `alembic/versions/xxxx_rename_seedance_to_ark.py`

- [ ] **Step 1: 生成空 migration**

```bash
uv run alembic revision -m "rename seedance provider to ark"
```

- [ ] **Step 2: 编写 migration 内容**

```python
def upgrade() -> None:
    op.execute("UPDATE provider_config SET provider = 'ark' WHERE provider = 'seedance'")
    op.execute(
        "UPDATE system_setting SET value = REPLACE(value, 'seedance/', 'ark/') "
        "WHERE key IN ('default_video_backend', 'default_image_backend')"
    )

def downgrade() -> None:
    op.execute("UPDATE provider_config SET provider = 'seedance' WHERE provider = 'ark'")
    op.execute(
        "UPDATE system_setting SET value = REPLACE(value, 'ark/', 'seedance/') "
        "WHERE key IN ('default_video_backend', 'default_image_backend')"
    )
```

- [ ] **Step 3: 验证 migration 执行**

```bash
uv run alembic upgrade head
```

- [ ] **Step 4: 验证 migration 数据转换**

在测试或 Python shell 中验证：向 DB 插入 `seedance` provider_config 行，运行 migration，断言变为 `ark`。也验证空表场景不报错。

- [ ] **Step 5: 提交**

```bash
git add alembic/ && git commit -m "migration: rename seedance provider to ark in DB"
```

---

### Task 3: ImageBackend 核心抽象层

**Files:**
- Create: `lib/image_backends/base.py`
- Create: `lib/image_backends/registry.py`
- Create: `lib/image_backends/__init__.py` (空壳，后续 Task 填充注册)
- Test: `tests/test_image_backends/test_base.py`
- Test: `tests/test_image_backends/test_registry.py`

- [ ] **Step 1: 创建 `tests/test_image_backends/__init__.py`**

空文件。

- [ ] **Step 2: 编写 `tests/test_image_backends/test_base.py`**

```python
from pathlib import Path

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
)


def test_image_capability_is_str_enum():
    assert ImageCapability.TEXT_TO_IMAGE == "text_to_image"
    assert ImageCapability.IMAGE_TO_IMAGE == "image_to_image"


def test_reference_image_defaults():
    ref = ReferenceImage(path="/tmp/test.png")
    assert ref.path == "/tmp/test.png"
    assert ref.label == ""


def test_image_generation_request_defaults():
    req = ImageGenerationRequest(prompt="hello", output_path=Path("/tmp/out.png"))
    assert req.aspect_ratio == "9:16"
    assert req.image_size == "1K"
    assert req.reference_images == []
    assert req.project_name is None
    assert req.seed is None


def test_image_generation_result():
    result = ImageGenerationResult(
        image_path=Path("/tmp/out.png"),
        provider="grok",
        model="grok-imagine-image",
    )
    assert result.image_uri is None
    assert result.seed is None
    assert result.usage_tokens is None
```

- [ ] **Step 3: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_image_backends/test_base.py -v`
Expected: FAIL (模块不存在)

- [ ] **Step 4: 创建 `lib/image_backends/base.py`**

```python
"""图片生成服务层核心接口定义。"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol, Set

# Provider 常量从 video_backends 复用，避免重复定义
from lib.video_backends.base import PROVIDER_ARK, PROVIDER_GEMINI, PROVIDER_GROK

# 图片后缀 → MIME 类型映射
IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def image_to_base64_data_uri(image_path: Path) -> str:
    """将本地图片转为 base64 data URI。"""
    suffix = image_path.suffix.lower()
    mime_type = IMAGE_MIME_TYPES.get(suffix, "image/png")
    image_data = image_path.read_bytes()
    b64 = base64.b64encode(image_data).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


class ImageCapability(str, Enum):
    """图片后端支持的能力枚举。"""
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"


@dataclass
class ReferenceImage:
    """参考图片。"""
    path: str
    label: str = ""


@dataclass
class ImageGenerationRequest:
    """通用图片生成请求。各 Backend 忽略不支持的字段。"""
    prompt: str
    output_path: Path
    reference_images: list[ReferenceImage] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    image_size: str = "1K"
    project_name: str | None = None
    seed: int | None = None


@dataclass
class ImageGenerationResult:
    """通用图片生成结果。"""
    image_path: Path
    provider: str
    model: str
    image_uri: str | None = None
    seed: int | None = None
    usage_tokens: int | None = None


class ImageBackend(Protocol):
    """图片生成后端协议。"""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> Set[ImageCapability]: ...

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult: ...
```

- [ ] **Step 5: 运行 base 测试验证通过**

Run: `uv run python -m pytest tests/test_image_backends/test_base.py -v`
Expected: PASS

- [ ] **Step 6: 编写 `tests/test_image_backends/test_registry.py`**

```python
import pytest

from lib.image_backends.registry import (
    create_backend,
    get_registered_backends,
    register_backend,
)


class _DummyBackend:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_register_and_create(monkeypatch):
    # 用 monkeypatch 隔离全局 dict
    from lib.image_backends import registry
    monkeypatch.setattr(registry, "_BACKEND_FACTORIES", {})

    register_backend("dummy", _DummyBackend)
    assert "dummy" in get_registered_backends()

    backend = create_backend("dummy", api_key="test")
    assert backend.kwargs == {"api_key": "test"}


def test_create_unknown_raises(monkeypatch):
    from lib.image_backends import registry
    monkeypatch.setattr(registry, "_BACKEND_FACTORIES", {})

    with pytest.raises(ValueError, match="Unknown image backend"):
        create_backend("nonexistent")
```

- [ ] **Step 7: 创建 `lib/image_backends/registry.py`**

```python
"""图片后端注册与工厂。"""

from __future__ import annotations

from typing import Any, Callable

from lib.image_backends.base import ImageBackend

_BACKEND_FACTORIES: dict[str, Callable[..., ImageBackend]] = {}


def register_backend(name: str, factory: Callable[..., ImageBackend]) -> None:
    """注册一个图片后端工厂函数。"""
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> ImageBackend:
    """根据名称创建图片后端实例。"""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown image backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    """返回所有已注册的后端名称。"""
    return list(_BACKEND_FACTORIES.keys())
```

- [ ] **Step 8: 创建 `lib/image_backends/__init__.py` (空壳)**

```python
"""图片生成服务层公共 API。"""

from lib.image_backends.base import (
    ImageBackend,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
)
from lib.image_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "ImageBackend",
    "ImageCapability",
    "ImageGenerationRequest",
    "ImageGenerationResult",
    "ReferenceImage",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]

# Backend auto-registration happens after concrete backends are implemented.
```

- [ ] **Step 9: 运行全部 image_backends 测试**

Run: `uv run python -m pytest tests/test_image_backends/ -v`
Expected: PASS

- [ ] **Step 10: 新增 `FakeImageBackend` 到 `tests/fakes.py`**

在文件末尾添加：

```python
from pathlib import Path
from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult


class FakeImageBackend:
    """Fake image backend for testing."""

    def __init__(self, *, provider: str = "fake", model: str = "fake-model"):
        self._provider = provider
        self._model = model

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        # 写一个 1x1 PNG 到 output_path
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider,
            model=self._model,
        )
```

- [ ] **Step 11: 提交**

```bash
git add lib/image_backends/ tests/test_image_backends/ tests/fakes.py
git commit -m "feat: add ImageBackend abstraction layer with protocol, registry, and FakeImageBackend"
```

---

### Task 4: GeminiImageBackend 实现

**Files:**
- Create: `lib/image_backends/gemini.py`
- Modify: `lib/image_backends/__init__.py` (加入注册)
- Test: `tests/test_image_backends/test_gemini.py`

- [ ] **Step 1: 编写 `tests/test_image_backends/test_gemini.py`**

```python
"""GeminiImageBackend 单元测试 — mock google-genai SDK。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.image_backends.base import (
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
    ReferenceImage,
)
from lib.image_backends.gemini import GeminiImageBackend


@pytest.fixture
def mock_genai():
    """Mock google.genai 模块。"""
    with patch("lib.image_backends.gemini._genai") as mock:
        # 模拟 types
        mock_types = MagicMock()
        mock.types = mock_types

        # 模拟 client
        mock_client = MagicMock()
        mock.Client.return_value = mock_client

        # 模拟 aio.models.generate_content 返回
        fake_image_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        mock_part = MagicMock()
        mock_part.inline_data = MagicMock()
        mock_part.inline_data.data = fake_image_bytes
        mock_part.inline_data.mime_type = "image/png"

        mock_response = MagicMock()
        mock_response.parts = [mock_part]
        mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        yield mock, mock_client, mock_response, mock_part


def test_capabilities():
    with patch("lib.image_backends.gemini._genai"):
        backend = GeminiImageBackend(api_key="test-key")
    assert ImageCapability.TEXT_TO_IMAGE in backend.capabilities
    assert ImageCapability.IMAGE_TO_IMAGE in backend.capabilities


def test_name_aistudio():
    with patch("lib.image_backends.gemini._genai"):
        backend = GeminiImageBackend(api_key="test-key", backend_type="aistudio")
    assert backend.name == "gemini-aistudio"


def test_name_vertex():
    with patch("lib.image_backends.gemini._genai"):
        with patch("lib.image_backends.gemini._resolve_vertex"):
            backend = GeminiImageBackend(backend_type="vertex")
    assert backend.name == "gemini-vertex"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_image_backends/test_gemini.py -v`
Expected: FAIL

- [ ] **Step 3: 创建 `lib/image_backends/gemini.py`**

从 `GeminiClient` 迁移图片生成逻辑。关键点：
- 构造函数参数：`backend_type`, `api_key`, `rate_limiter`, `image_model`, `base_url`
- Vertex 模式：迁移 `GeminiClient` 的凭证初始化逻辑
- `generate()` 方法：迁移 `generate_image_async()` + `_build_contents_with_labeled_refs()` + `_prepare_image_config()` + `_process_image_response()`
- 将 `ReferenceImage` 列表转为 `contents` 中的 `[label, PIL.Image, ...]` 序列
- 输出文件保存到 `request.output_path`

```python
"""GeminiImageBackend — Gemini 图片生成后端。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Set

from PIL import Image

from lib.gemini_client import VERTEX_SCOPES, RateLimiter, get_shared_rate_limiter, with_retry_async
from lib.image_backends.base import (
    PROVIDER_GEMINI,
    ImageCapability,
    ImageGenerationRequest,
    ImageGenerationResult,
)

logger = logging.getLogger(__name__)

# SDK 延迟导入，在模块级 import 以便测试 mock
try:
    from google import genai as _genai
except ImportError:
    _genai = None  # type: ignore[assignment]


def _resolve_vertex(credentials_path: str | None = None):
    """解析 Vertex AI 凭证，返回 (credentials, project_id)。"""
    import json as json_module
    from google.oauth2 import service_account
    from lib.system_config import resolve_vertex_credentials_path

    if credentials_path:
        cred_file = Path(credentials_path)
    else:
        cred_file = resolve_vertex_credentials_path(Path(__file__).parent.parent.parent)
    if cred_file is None:
        raise ValueError("未找到 Vertex AI 凭证文件")

    with open(cred_file) as f:
        creds_data = json_module.load(f)
    project_id = creds_data.get("project_id")
    if not project_id:
        raise ValueError(f"凭证文件中未找到 project_id")

    credentials = service_account.Credentials.from_service_account_file(
        str(cred_file), scopes=VERTEX_SCOPES
    )
    return credentials, project_id


class GeminiImageBackend:
    """Gemini 图片生成后端（AI Studio + Vertex AI）。"""

    DEFAULT_MODEL = "gemini-3.1-flash-image-preview"

    # 跳过名称推断的文件名模式
    _SKIP_NAME_PATTERNS = ("scene_", "storyboard_", "output_")

    def __init__(
        self,
        *,
        backend_type: str = "aistudio",
        api_key: Optional[str] = None,
        rate_limiter: Optional[RateLimiter] = None,
        image_model: Optional[str] = None,
        base_url: Optional[str] = None,
        credentials_path: Optional[str] = None,
    ):
        self._backend_type = backend_type.strip().lower()
        self._rate_limiter = rate_limiter or get_shared_rate_limiter()
        self._image_model = image_model or self.DEFAULT_MODEL

        if self._backend_type == "vertex":
            credentials, project_id = _resolve_vertex(credentials_path)
            self._client = _genai.Client(
                vertexai=True,
                project=project_id,
                location="global",
                credentials=credentials,
            )
        else:
            _api_key = api_key or os.environ.get("GEMINI_API_KEY")
            if not _api_key:
                raise ValueError(
                    "Gemini API Key 未提供。请在「全局设置 → 供应商」页面配置 API Key。"
                )
            effective_base_url = base_url
            http_options = {"base_url": effective_base_url} if effective_base_url else None
            self._client = _genai.Client(api_key=_api_key, http_options=http_options)

        self._capabilities: Set[ImageCapability] = {
            ImageCapability.TEXT_TO_IMAGE,
            ImageCapability.IMAGE_TO_IMAGE,
        }

    @property
    def name(self) -> str:
        return f"gemini-{self._backend_type}"

    @property
    def model(self) -> str:
        return self._image_model

    @property
    def capabilities(self) -> Set[ImageCapability]:
        return self._capabilities

    @with_retry_async(max_attempts=5, backoff_seconds=(2, 4, 8, 16, 32))
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片。"""
        if self._rate_limiter:
            await self._rate_limiter.acquire_async(self._image_model)

        contents = self._build_contents(request)
        config = _genai.types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=_genai.types.ImageConfig(
                aspect_ratio=request.aspect_ratio,
                image_size=request.image_size,
            ),
        )

        response = await self._client.aio.models.generate_content(
            model=self._image_model, contents=contents, config=config,
        )

        # 解析响应并保存图片
        for part in response.parts:
            if part.inline_data is not None:
                image = part.as_image()
                request.output_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(request.output_path)
                return ImageGenerationResult(
                    image_path=request.output_path,
                    provider=self.name,
                    model=self._image_model,
                )

        raise RuntimeError("Gemini API 未返回图片")

    def _build_contents(self, request: ImageGenerationRequest) -> list:
        """构建带名称标签的 contents 列表。"""
        contents: list = []

        for ref in request.reference_images:
            ref_path = Path(ref.path)
            # 推断名称标签
            annotation = ref.label or self._extract_name(ref_path)
            if annotation:
                contents.append(annotation)
            # 加载图片
            with Image.open(ref_path) as img:
                contents.append(img.copy())

        contents.append(request.prompt)
        return contents

    @classmethod
    def _extract_name(cls, path: Path) -> str | None:
        """从路径推断名称（用于参考图标签）。"""
        stem = path.stem
        for pattern in cls._SKIP_NAME_PATTERNS:
            if stem.startswith(pattern):
                return None
        return stem
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run python -m pytest tests/test_image_backends/test_gemini.py -v`
Expected: PASS

- [ ] **Step 5: 在 `__init__.py` 中注册 GeminiImageBackend**

在 `lib/image_backends/__init__.py` 末尾加入：

```python
from lib.image_backends.gemini import GeminiImageBackend
register_backend(PROVIDER_GEMINI, GeminiImageBackend)
```

- [ ] **Step 6: 提交**

```bash
git add lib/image_backends/ tests/test_image_backends/
git commit -m "feat: implement GeminiImageBackend with T2I and I2I support"
```

---

### Task 5: ArkImageBackend 实现

**Files:**
- Create: `lib/image_backends/ark.py`
- Modify: `lib/image_backends/__init__.py`
- Test: `tests/test_image_backends/test_ark.py`

- [ ] **Step 1: 编写测试**

测试 `ArkImageBackend` 的构造、capabilities、`generate()` SDK 参数转换（mock `volcenginesdkarkruntime.Ark`）。

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_image_backends/test_ark.py -v`

- [ ] **Step 3: 创建 `lib/image_backends/ark.py`**

关键实现：
- 使用 `Ark(base_url=..., api_key=...)` 创建客户端
- `generate()`: 通过 `asyncio.to_thread(self._client.images.generate, ...)` 调用同步 SDK
- T2I: `prompt` 参数
- I2I: 读取 `reference_images` 为 base64 字符串列表传入 `image` 参数
- 从响应中提取图片数据并保存到 `output_path`

- [ ] **Step 4: 运行测试验证通过**

- [ ] **Step 5: 在 `__init__.py` 中注册**

```python
from lib.image_backends.ark import ArkImageBackend
register_backend(PROVIDER_ARK, ArkImageBackend)
```

- [ ] **Step 6: 提交**

```bash
git add lib/image_backends/ tests/test_image_backends/
git commit -m "feat: implement ArkImageBackend (Seedream) with T2I and I2I"
```

---

### Task 6: GrokImageBackend 实现

**Files:**
- Create: `lib/image_backends/grok.py`
- Modify: `lib/image_backends/__init__.py`
- Test: `tests/test_image_backends/test_grok.py`

- [ ] **Step 1: 编写测试**

测试 `GrokImageBackend` 的构造、capabilities、`generate()` SDK 参数转换（mock `xai_sdk.AsyncClient`）。

- [ ] **Step 2: 运行测试验证失败**

- [ ] **Step 3: 创建 `lib/image_backends/grok.py`**

关键实现：
- 使用 `xai_sdk.AsyncClient(api_key=...)` 创建客户端
- T2I: `await client.image.sample(prompt=..., model=..., aspect_ratio=..., resolution=...)`
- I2I: 读取第一张参考图为 base64 data URI，传入 `image_url` 参数
- 从 `response.url` 下载图片到 `output_path`（或使用 `image_format="base64"` 直接获取数据）

- [ ] **Step 4: 运行测试验证通过**

- [ ] **Step 5: 在 `__init__.py` 中注册**

```python
from lib.image_backends.grok import GrokImageBackend
register_backend(PROVIDER_GROK, GrokImageBackend)
```

- [ ] **Step 6: 提交**

```bash
git add lib/image_backends/ tests/test_image_backends/
git commit -m "feat: implement GrokImageBackend (Aurora) with T2I and I2I"
```

---

### Task 7: CostCalculator 扩展 + UsageRepo 计费路由

**Files:**
- Modify: `lib/cost_calculator.py`
- Modify: `lib/db/repositories/usage_repo.py`
- Test: `tests/test_cost_calculator.py` (新建或追加)

- [ ] **Step 1: 编写计费测试**

```python
from lib.cost_calculator import cost_calculator

def test_ark_image_cost_default():
    cost, currency = cost_calculator.calculate_ark_image_cost()
    assert currency == "CNY"
    assert cost == pytest.approx(0.22)  # lite 默认

def test_ark_image_cost_by_model():
    cost, _ = cost_calculator.calculate_ark_image_cost(model="doubao-seedream-4-5-251128")
    assert cost == pytest.approx(0.25)

def test_ark_image_cost_n_images():
    cost, _ = cost_calculator.calculate_ark_image_cost(n=3)
    assert cost == pytest.approx(0.22 * 3)

def test_grok_image_cost_default():
    cost = cost_calculator.calculate_grok_image_cost()
    assert cost == pytest.approx(0.02)

def test_grok_image_cost_pro():
    cost = cost_calculator.calculate_grok_image_cost(model="grok-imagine-image-pro")
    assert cost == pytest.approx(0.07)
```

- [ ] **Step 2: 运行测试验证失败**

- [ ] **Step 3: 在 `CostCalculator` 中新增方法和常量**

```python
# Ark 图片费用（元/张）
ARK_IMAGE_COST = {
    "doubao-seedream-5-0-260128": 0.22,
    "doubao-seedream-5-0-lite-260128": 0.22,
    "doubao-seedream-4-5-251128": 0.25,
    "doubao-seedream-4-0-250828": 0.20,
}
DEFAULT_ARK_IMAGE_MODEL = "doubao-seedream-5-0-lite-260128"

# Grok 图片费用（美元/张）
GROK_IMAGE_COST = {
    "grok-imagine-image": 0.02,
    "grok-imagine-image-pro": 0.07,
}
DEFAULT_GROK_IMAGE_MODEL = "grok-imagine-image"

def calculate_ark_image_cost(self, model: str | None = None, n: int = 1) -> tuple[float, str]:
    model = model or self.DEFAULT_ARK_IMAGE_MODEL
    per_image = self.ARK_IMAGE_COST.get(model, self.ARK_IMAGE_COST[self.DEFAULT_ARK_IMAGE_MODEL])
    return per_image * n, "CNY"

def calculate_grok_image_cost(self, model: str | None = None, n: int = 1) -> float:
    model = model or self.DEFAULT_GROK_IMAGE_MODEL
    per_image = self.GROK_IMAGE_COST.get(model, self.GROK_IMAGE_COST[self.DEFAULT_GROK_IMAGE_MODEL])
    return per_image * n
```

- [ ] **Step 4: 更新 `usage_repo.py` 成本路由**

在 `finish_call` 方法中，在 `elif row.call_type == "image":` 分支加入 Ark/Grok 判断：

```python
elif row.call_type == "image":
    if effective_provider == PROVIDER_ARK:
        cost_amount, currency = cost_calculator.calculate_ark_image_cost(model=row.model)
    elif effective_provider == PROVIDER_GROK:
        cost_amount = cost_calculator.calculate_grok_image_cost(model=row.model)
        currency = "USD"
    else:
        cost_amount = cost_calculator.calculate_image_cost(row.resolution or "1K", model=row.model)
        currency = "USD"
```

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run python -m pytest tests/test_cost_calculator.py -v`

- [ ] **Step 6: 提交**

```bash
git add lib/cost_calculator.py lib/db/repositories/usage_repo.py tests/
git commit -m "feat: add ark/grok image cost calculation and usage routing"
```

---

### Task 8: generation_tasks.py 集成 + MediaGenerator 适配

**Files:**
- Modify: `server/services/generation_tasks.py`
- Modify: `lib/media_generator.py`

- [ ] **Step 1: 更新 `_backend_cache` 类型和 video cache key**

将 `_backend_cache` 类型改为 `dict[tuple[str, ...], Any]`，并将 `_get_or_create_video_backend` 中的 cache key 改为 3-tuple `("video", provider_name, effective_model)` 以与 image cache key 对齐并防止 key 冲突。

- [ ] **Step 2: 在 `generation_tasks.py` 中新增 `_get_or_create_image_backend`**

删除 `_resolve_image_backend()`，替换为：

```python
async def _get_or_create_image_backend(
    provider_name: str,
    provider_settings: dict,
    resolver: "ConfigResolver",
    *,
    default_image_model: str | None = None,
):
    """获取或创建 ImageBackend 实例（带缓存）。"""
    from lib.image_backends import create_backend

    effective_model = provider_settings.get("model") or default_image_model or None
    cache_key = ("image", provider_name, effective_model)
    if cache_key in _backend_cache:
        return _backend_cache[cache_key]

    backend_name = _PROVIDER_ID_TO_BACKEND.get(provider_name, provider_name)

    kwargs: dict = {}
    if backend_name == PROVIDER_GEMINI:
        if provider_name == "gemini-vertex":
            kwargs["backend_type"] = "vertex"
        else:
            kwargs["backend_type"] = "aistudio"
        config_id = "gemini-vertex" if kwargs["backend_type"] == "vertex" else "gemini-aistudio"
        db_config = await resolver.provider_config(config_id)
        kwargs["api_key"] = db_config.get("api_key")
        kwargs["base_url"] = db_config.get("base_url")
        kwargs["rate_limiter"] = rate_limiter
        kwargs["image_model"] = effective_model
    elif backend_name == PROVIDER_ARK:
        db_config = await resolver.provider_config("ark")
        kwargs["api_key"] = db_config.get("api_key")
        kwargs["model"] = effective_model
    elif backend_name == PROVIDER_GROK:
        db_config = await resolver.provider_config("grok")
        kwargs["api_key"] = db_config.get("api_key")
        kwargs["model"] = effective_model

    backend = create_backend(backend_name, **kwargs)
    _backend_cache[cache_key] = backend
    return backend
```

- [ ] **Step 3: 重写 `get_media_generator()` 注入 image_backend**

```python
async def get_media_generator(project_name: str, payload: dict | None = None, *, user_id: str = DEFAULT_USER_ID) -> MediaGenerator:
    from lib.config.resolver import ConfigResolver
    from lib.db import async_session_factory

    project_path = get_project_manager().get_project_path(project_name)
    resolver = ConfigResolver(async_session_factory)

    # 解析 image backend
    image_provider_id, image_model = await resolver.default_image_backend()
    if payload and payload.get("image_provider"):
        image_provider_id = payload["image_provider"]
        image_model = payload.get("image_model", "") or image_model
    image_backend = await _get_or_create_image_backend(
        image_provider_id, {}, resolver, default_image_model=image_model,
    )

    # 解析 video backend（保持现有逻辑）
    video_backend, video_backend_type, video_model = await _resolve_video_backend(
        project_name, resolver, payload,
    )

    return MediaGenerator(
        project_path,
        rate_limiter=rate_limiter,
        image_backend=image_backend,
        video_backend=video_backend,
        config_resolver=resolver,
        user_id=user_id,
    )
```

- [ ] **Step 4: 重构 `MediaGenerator` 构造函数和 `generate_image_async`**

构造函数：
- 新增 `image_backend` 参数
- 移除 `image_backend_type`、`gemini_image_model`、`gemini_api_key`、`gemini_base_url`、`gemini_video_model` 参数
- 移除 `_gemini_image`、`_gemini_video`、`_get_gemini_image()`、`_get_gemini_video()` 方法
- 注意：`video_backend` 已通过构造函数注入，不再需要 GeminiClient 做视频 fallback；如果 `video_backend` 为 None，视频生成方法应抛出 `RuntimeError` 而非 fallback 到 GeminiClient

`generate_image_async`:
```python
async def generate_image_async(self, prompt, resource_type, resource_id,
                                reference_images=None, aspect_ratio="9:16",
                                image_size="1K", **version_metadata):
    from lib.image_backends.base import ImageGenerationRequest, ReferenceImage

    output_path = self._get_output_path(resource_type, resource_id)
    self._ensure_parent_dir(output_path)

    if output_path.exists():
        self.versions.ensure_current_tracked(...)

    if self._image_backend is None:
        raise RuntimeError("image_backend not configured")

    call_id = await self.usage_tracker.start_call(
        project_name=self.project_name,
        call_type="image",
        model=self._image_backend.model,
        prompt=prompt,
        resolution=image_size,
        aspect_ratio=aspect_ratio,
        provider=self._image_backend.name,
        user_id=self._user_id,
    )

    try:
        # 转换参考图格式
        ref_images = []
        if reference_images:
            for ref in reference_images:
                if isinstance(ref, dict):
                    ref_images.append(ReferenceImage(
                        path=str(ref.get("image", "")),
                        label=str(ref.get("label", "")),
                    ))
                elif isinstance(ref, (str, Path)):
                    ref_images.append(ReferenceImage(path=str(ref)))
                # 其他类型（Path-like）也转为字符串

        request = ImageGenerationRequest(
            prompt=prompt,
            output_path=output_path,
            reference_images=ref_images,
            aspect_ratio=aspect_ratio,
            image_size=image_size,
            project_name=self.project_name,
        )
        result = await self._image_backend.generate(request)

        await self.usage_tracker.finish_call(
            call_id=call_id, status="success", output_path=str(output_path),
        )
    except Exception as e:
        logger.exception("生成失败 (image)")
        await self.usage_tracker.finish_call(
            call_id=call_id, status="failed", error_message=str(e),
        )
        raise

    new_version = self.versions.add_version(...)
    return output_path, new_version
```

同步 `generate_image` 做类似重构。

- [ ] **Step 5: 运行全部测试**

Run: `uv run python -m pytest -x -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add server/services/generation_tasks.py lib/media_generator.py
git commit -m "feat: integrate ImageBackend into generation pipeline"
```

---

### Task 9: GeminiClient 死代码清理

**Files:**
- Modify: `lib/gemini_client.py`
- Modify: `lib/media_generator.py` (移除残留 import)
- Modify: `lib/__init__.py`

- [ ] **Step 1: 从 `GeminiClient` 中删除以下方法/属性**

- `generate_image()` / `generate_image_async()` / `generate_image_with_chat()`
- `generate_video()`
- `_build_contents_with_labeled_refs()` / `_prepare_image_config()` / `_process_image_response()`
- `_normalize_reference_image()` / `_extract_name_from_path()` / `_load_image_detached()`
- `SKIP_NAME_PATTERNS` 类属性
- `IMAGE_MODEL` / `VIDEO_MODEL` 属性
- `ReferenceImageValue` / `ReferenceImageInput` 类型别名（已迁移到 `image_backends/base.py`）

保留：构造函数、`client` 属性、`VERTEX_SCOPES`、`RateLimiter`、重试装饰器。

- [ ] **Step 2: 迁移 `ReferenceImageInput` / `ReferenceImageValue` 类型别名**

在 `lib/image_backends/base.py` 中，`ReferenceImage` dataclass 已替代这两个类型别名。在 `lib/gemini_client.py` 中删除 `ReferenceImageValue` 和 `ReferenceImageInput` 定义。更新所有使用 `ReferenceImageInput` 的 import 站点：
- `lib/media_generator.py` — 不再需要此类型（内部已使用 `ReferenceImage`）
- 其他通过 `lib/__init__.py` 导出的引用也需清理

- [ ] **Step 3: 更新 `lib/media_generator.py` import**

移除 `from lib.gemini_client import GeminiClient, RateLimiter, ReferenceImageInput`，仅保留 `from lib.gemini_client import RateLimiter`。

移除 `_get_gemini_image()` / `_get_gemini_video()` 方法和相关属性（Task 8 已移除构造函数参数，此处清理残留方法和 import）。

- [ ] **Step 4: 运行全部测试**

Run: `uv run python -m pytest -x -q`

- [ ] **Step 5: 提交**

```bash
git add lib/gemini_client.py lib/media_generator.py lib/__init__.py lib/image_backends/base.py
git commit -m "refactor: strip image/video methods from GeminiClient, now text-only"
```

---

### Task 10: 全量回归测试 + 最终验证

**Files:** 无新文件

- [ ] **Step 1: 运行全部测试**

```bash
uv run python -m pytest -v --tb=short
```

- [ ] **Step 2: 验证 image_backends registry 注册正确**

```bash
uv run python -c "from lib.image_backends import get_registered_backends; print(get_registered_backends())"
```

Expected: `['gemini', 'ark', 'grok']`

- [ ] **Step 3: 验证 video_backends registry 仍正常**

```bash
uv run python -c "from lib.video_backends import get_registered_backends; print(get_registered_backends())"
```

Expected: `['gemini', 'ark', 'grok']`

- [ ] **Step 4: 验证 Alembic migration chain 完整**

```bash
uv run alembic check
```

- [ ] **Step 5: TypeCheck 前端（确认无 seedance 硬编码残留影响）**

```bash
cd frontend && pnpm typecheck
```

- [ ] **Step 6: 最终提交（如有修复）**

```bash
git add -A && git commit -m "fix: address regression test findings"
```
