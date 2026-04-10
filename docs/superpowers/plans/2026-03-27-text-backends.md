# Text Backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a universal text generation service layer with multi-provider support (Gemini, Ark, Grok), aligning with the existing image/video backend architecture.

**Architecture:** TextBackend Protocol + Registry pattern mirroring image_backends/video_backends. ProviderMeta restructured with per-model capabilities (ModelInfo). ConfigResolver gains text_backend_for_task() with per-task-type model selection and auto-resolve fallback. Four callers (ScriptGenerator, ProjectManager, files.py, CLI script) migrated from GeminiClient to TextBackend interface.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, React 19, TypeScript, Tailwind CSS 4, zustand, google-genai SDK, volcenginesdkarkruntime, xai_sdk

**Spec:** `docs/superpowers/specs/2026-03-27-text-backends-design.md`

---

## File Map

### Create

| File | Responsibility |
|------|---------------|
| `lib/gemini_shared.py` | RateLimiter, retry decorators, VERTEX_SCOPES (extracted from gemini_client.py) |
| `lib/text_backends/__init__.py` | Public API + auto-registration |
| `lib/text_backends/base.py` | TextBackend Protocol, TextCapability, TextTaskType, data classes |
| `lib/text_backends/registry.py` | register_backend / create_backend / get_registered_backends |
| `lib/text_backends/gemini.py` | GeminiTextBackend |
| `lib/text_backends/ark.py` | ArkTextBackend |
| `lib/text_backends/grok.py` | GrokTextBackend |
| `lib/text_backends/factory.py` | create_text_backend_for_task() shared factory |
| `lib/text_backends/prompts.py` | STYLE_ANALYSIS_PROMPT constant |
| `tests/test_text_backends/__init__.py` | Test package |
| `tests/test_text_backends/test_base.py` | Data classes + enums |
| `tests/test_text_backends/test_registry.py` | Registry logic |
| `tests/test_text_backends/test_gemini.py` | GeminiTextBackend |
| `tests/test_text_backends/test_ark.py` | ArkTextBackend |
| `tests/test_text_backends/test_grok.py` | GrokTextBackend |
| `tests/test_text_backends/test_factory.py` | Factory + auto-resolve |

### Modify

| File | Change |
|------|--------|
| `lib/config/registry.py` | ProviderMeta → ModelInfo restructure |
| `lib/config/service.py` | Add get_default_text_backend(), update ProviderStatus with models |
| `lib/config/resolver.py` | Add text_backend_for_task(), _auto_resolve_backend() |
| `lib/script_generator.py` | GeminiClient → TextBackend |
| `lib/project_manager.py` | generate_overview() → TextBackend |
| `server/routers/files.py` | upload_style_image → TextBackend |
| `server/routers/providers.py` | Add models to ProviderSummary |
| `server/routers/system_config.py` | Add text backend settings, replace _PROVIDER_MODELS with registry |
| `server/services/generation_tasks.py` | Imports from gemini_shared |
| `lib/image_backends/gemini.py` | Imports from gemini_shared |
| `lib/video_backends/gemini.py` | Imports from gemini_shared |
| `lib/media_generator.py` | Imports from gemini_shared |
| `lib/__init__.py` | Remove GeminiClient |
| `lib/cost_calculator.py` | Add text cost methods |
| `frontend/src/types/system.ts` | Add text backend fields |
| `frontend/src/stores/config-status-store.ts` | Auto-resolve check logic |
| `frontend/src/components/pages/settings/MediaModelSection.tsx` | Text model selectors |
| `frontend/src/components/pages/ProjectSettingsPage.tsx` | Text model overrides |
| `frontend/src/components/pages/SystemConfigPage.tsx` | Tab rename |
| `agent_runtime_profile/.claude/skills/generate-script/scripts/normalize_drama_script.py` | TextBackend migration |

### Delete

| File | Reason |
|------|--------|
| `lib/gemini_client.py` | Responsibilities split to gemini_shared.py + GeminiTextBackend |
| `lib/text_client.py` | Replaced by registry + factory |
| `tests/test_text_client.py` | Follows text_client.py deletion |
| `tests/test_gemini_client_more.py` | Tests deleted GeminiClient (relevant tests migrated to test_gemini.py) |
| `tests/test_gemini_client_fd.py` | Tests deleted GeminiClient |

---

## Task 1: Extract shared utilities to gemini_shared.py

**Files:**
- Create: `lib/gemini_shared.py`
- Modify: `lib/image_backends/gemini.py`, `lib/video_backends/gemini.py`, `server/services/generation_tasks.py`, `server/routers/providers.py`, `lib/media_generator.py`

This task extracts non-GeminiClient utilities from `lib/gemini_client.py` into a new module, then updates all importers. No behavior change — pure move refactor.

- [ ] **Step 1: Create lib/gemini_shared.py**

Copy from `lib/gemini_client.py` lines 1-259 (everything before the `GeminiClient` class): `VERTEX_SCOPES`, `RETRYABLE_ERRORS`, `RateLimiter`, `_rate_limiter_limits_from_env`, `get_shared_rate_limiter`, `refresh_shared_rate_limiter`, `with_retry`, `with_retry_async`, and their imports.

```python
"""
Gemini 共享工具模块

从 gemini_client.py 提取的可复用工具：
- RateLimiter: 多模型滑动窗口限流器
- with_retry / with_retry_async: 带指数退避的重试装饰器
- VERTEX_SCOPES: Vertex AI OAuth scopes
"""

import asyncio
import functools
import logging
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, Optional, Tuple, Type

from .cost_calculator import cost_calculator

logger = logging.getLogger(__name__)

# Vertex AI 服务账号所需 OAuth scopes
VERTEX_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language",
]

# 可重试的错误类型
RETRYABLE_ERRORS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)

try:
    from google import genai
    from google.api_core import exceptions as google_exceptions

    RETRYABLE_ERRORS = RETRYABLE_ERRORS + (
        google_exceptions.ResourceExhausted,
        google_exceptions.ServiceUnavailable,
        google_exceptions.DeadlineExceeded,
        google_exceptions.InternalServerError,
        genai.errors.ClientError,
        genai.errors.ServerError,
    )
except ImportError:
    pass

# (Include the full RateLimiter class, _rate_limiter_limits_from_env,
#  get_shared_rate_limiter, refresh_shared_rate_limiter,
#  with_retry, with_retry_async — exact copies from gemini_client.py lines 52-391)
```

- [ ] **Step 2: Update imports in 5 files**

Each file that imports from `lib.gemini_client` (except those importing `GeminiClient` itself) must be updated:

`lib/image_backends/gemini.py`:
```python
# Before:
from lib.gemini_client import VERTEX_SCOPES, RateLimiter, get_shared_rate_limiter, with_retry_async
# After:
from lib.gemini_shared import VERTEX_SCOPES, RateLimiter, get_shared_rate_limiter, with_retry_async
```

`lib/video_backends/gemini.py`:
```python
# Same pattern
from lib.gemini_shared import VERTEX_SCOPES, RateLimiter, get_shared_rate_limiter, with_retry_async
```

`server/services/generation_tasks.py`:
```python
# Before:
from lib.gemini_client import get_shared_rate_limiter
# After:
from lib.gemini_shared import get_shared_rate_limiter
```

`server/routers/providers.py`:
```python
# Before:
from lib.gemini_client import VERTEX_SCOPES
# After:
from lib.gemini_shared import VERTEX_SCOPES
```

`lib/media_generator.py`:
```python
# Before:
from lib.gemini_client import RateLimiter
# After:
from lib.gemini_shared import RateLimiter
```

- [ ] **Step 3: Run tests to verify no regressions**

Run: `uv run python -m pytest tests/ -x -q`
Expected: All existing tests pass (pure import refactor).

- [ ] **Step 4: Commit**

```bash
git add lib/gemini_shared.py lib/image_backends/gemini.py lib/video_backends/gemini.py \
  server/services/generation_tasks.py server/routers/providers.py lib/media_generator.py
git commit -m "refactor: extract gemini_shared.py from gemini_client.py"
```

---

## Task 2: TextBackend Protocol + data classes

**Files:**
- Create: `lib/text_backends/base.py`, `tests/test_text_backends/__init__.py`, `tests/test_text_backends/test_base.py`

- [ ] **Step 1: Create test package**

```bash
mkdir -p tests/test_text_backends
touch tests/test_text_backends/__init__.py
```

- [ ] **Step 2: Write failing tests**

`tests/test_text_backends/test_base.py`:
```python
"""TextBackend Protocol + data classes tests."""
from pathlib import Path

from lib.text_backends.base import (
    ImageInput,
    TextBackend,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskType,
)


class TestTextCapability:
    def test_values(self):
        assert TextCapability.TEXT_GENERATION == "text_generation"
        assert TextCapability.STRUCTURED_OUTPUT == "structured_output"
        assert TextCapability.VISION == "vision"

    def test_is_str_enum(self):
        assert isinstance(TextCapability.TEXT_GENERATION, str)


class TestTextTaskType:
    def test_values(self):
        assert TextTaskType.SCRIPT == "script"
        assert TextTaskType.OVERVIEW == "overview"
        assert TextTaskType.STYLE_ANALYSIS == "style"


class TestImageInput:
    def test_path_only(self):
        inp = ImageInput(path=Path("/tmp/img.png"))
        assert inp.path == Path("/tmp/img.png")
        assert inp.url is None

    def test_url_only(self):
        inp = ImageInput(url="https://example.com/img.png")
        assert inp.path is None
        assert inp.url == "https://example.com/img.png"


class TestTextGenerationRequest:
    def test_minimal(self):
        req = TextGenerationRequest(prompt="hello")
        assert req.prompt == "hello"
        assert req.response_schema is None
        assert req.images is None
        assert req.system_prompt is None

    def test_full(self):
        req = TextGenerationRequest(
            prompt="analyze",
            response_schema={"type": "object"},
            images=[ImageInput(path=Path("/tmp/img.png"))],
            system_prompt="You are a helpful assistant.",
        )
        assert req.response_schema == {"type": "object"}
        assert len(req.images) == 1
        assert req.system_prompt == "You are a helpful assistant."


class TestTextGenerationResult:
    def test_minimal(self):
        result = TextGenerationResult(text="output", provider="gemini", model="flash")
        assert result.text == "output"
        assert result.input_tokens is None
        assert result.output_tokens is None

    def test_with_tokens(self):
        result = TextGenerationResult(
            text="output", provider="ark", model="seed",
            input_tokens=100, output_tokens=50,
        )
        assert result.input_tokens == 100
        assert result.output_tokens == 50


class TestTextBackendProtocol:
    """Verify a class satisfying the Protocol can be used as TextBackend."""

    def test_satisfies_protocol(self):
        from typing import Set

        class FakeBackend:
            @property
            def name(self) -> str:
                return "fake"

            @property
            def model(self) -> str:
                return "fake-model"

            @property
            def capabilities(self) -> Set[TextCapability]:
                return {TextCapability.TEXT_GENERATION}

            async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
                return TextGenerationResult(text="ok", provider="fake", model="fake-model")

        backend: TextBackend = FakeBackend()
        assert backend.name == "fake"
        assert backend.model == "fake-model"
        assert TextCapability.TEXT_GENERATION in backend.capabilities
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_text_backends/test_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lib.text_backends'`

- [ ] **Step 4: Implement base.py**

`lib/text_backends/base.py`:
```python
"""Core interface definitions for the text generation service layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, Set


class TextCapability(str, Enum):
    """Enum of capabilities supported by text backends."""
    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"


class TextTaskType(str, Enum):
    """文本生成任务类型。"""
    SCRIPT = "script"
    OVERVIEW = "overview"
    STYLE_ANALYSIS = "style"


@dataclass
class ImageInput:
    """图片输入（用于 vision）。"""
    path: Path | None = None
    url: str | None = None


@dataclass
class TextGenerationRequest:
    """Generic text generation request. Each Backend ignores fields it does not support."""
    prompt: str
    response_schema: dict | None = None
    images: list[ImageInput] | None = None
    system_prompt: str | None = None


@dataclass
class TextGenerationResult:
    """Generic text generation result."""
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class TextBackend(Protocol):
    """Text generation backend protocol."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> Set[TextCapability]: ...

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult: ...
```

Also create an empty `lib/text_backends/__init__.py` for now (will be filled in Task 8):
```python
"""文本生成服务层公共 API。"""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_text_backends/test_base.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add lib/text_backends/ tests/test_text_backends/
git commit -m "feat: add TextBackend Protocol and data classes"
```

---

## Task 3: Text backends registry

**Files:**
- Create: `lib/text_backends/registry.py`, `tests/test_text_backends/test_registry.py`

- [ ] **Step 1: Write failing tests**

`tests/test_text_backends/test_registry.py`:
```python
"""Text backend registry tests."""
import pytest

from lib.text_backends.base import TextBackend, TextCapability, TextGenerationRequest, TextGenerationResult
from lib.text_backends.registry import (
    create_backend,
    get_registered_backends,
    register_backend,
    _BACKEND_FACTORIES,
)


class FakeTextBackend:
    def __init__(self, *, api_key=None, model=None):
        self._model = model or "fake-model"

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return self._model

    @property
    def capabilities(self):
        return {TextCapability.TEXT_GENERATION}

    async def generate(self, request):
        return TextGenerationResult(text="ok", provider="fake", model=self._model)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure a clean registry for each test."""
    saved = dict(_BACKEND_FACTORIES)
    _BACKEND_FACTORIES.clear()
    yield
    _BACKEND_FACTORIES.clear()
    _BACKEND_FACTORIES.update(saved)


class TestRegistry:
    def test_register_and_create(self):
        register_backend("fake", FakeTextBackend)
        backend = create_backend("fake", api_key="k")
        assert backend.name == "fake"
        assert backend.model == "fake-model"

    def test_create_with_model_override(self):
        register_backend("fake", FakeTextBackend)
        backend = create_backend("fake", model="custom-model")
        assert backend.model == "custom-model"

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown text backend"):
            create_backend("nonexistent")

    def test_get_registered_backends(self):
        register_backend("a", FakeTextBackend)
        register_backend("b", FakeTextBackend)
        assert sorted(get_registered_backends()) == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_text_backends/test_registry.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement registry.py**

`lib/text_backends/registry.py`:
```python
"""Text backend registration and factory."""
from __future__ import annotations

from typing import Any, Callable

from lib.text_backends.base import TextBackend

_BACKEND_FACTORIES: dict[str, Callable[..., TextBackend]] = {}


def register_backend(name: str, factory: Callable[..., TextBackend]) -> None:
    """注册一个文本后端工厂函数。"""
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> TextBackend:
    """根据名称创建文本后端实例。"""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown text backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    """返回所有已注册的后端名称。"""
    return list(_BACKEND_FACTORIES.keys())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_text_backends/test_registry.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add lib/text_backends/registry.py tests/test_text_backends/test_registry.py
git commit -m "feat: add text backend registry"
```

---

## Task 4: ProviderMeta restructure with ModelInfo

**Files:**
- Modify: `lib/config/registry.py`
- Create: `tests/test_config_registry_models.py`

This is a cross-cutting change: replaces `media_types` and `capabilities` flat fields on ProviderMeta with a `models` dict containing `ModelInfo` entries. The old properties are preserved as computed properties for backward compatibility.

- [ ] **Step 1: Write failing tests**

`tests/test_config_registry_models.py`:
```python
"""Test ProviderMeta with ModelInfo structure."""
from lib.config.registry import PROVIDER_REGISTRY, ModelInfo, ProviderMeta


class TestModelInfo:
    def test_basic(self):
        m = ModelInfo(
            display_name="Test Model",
            media_type="text",
            capabilities=["text_generation"],
            default=True,
        )
        assert m.display_name == "Test Model"
        assert m.media_type == "text"
        assert m.default is True


class TestProviderMeta:
    def test_media_types_derived_from_models(self):
        meta = ProviderMeta(
            display_name="Test",
            description="Test provider",
            required_keys=["api_key"],
            models={
                "text-model": ModelInfo("TM", "text", ["text_generation"], default=True),
                "image-model": ModelInfo("IM", "image", ["text_to_image"], default=True),
            },
        )
        assert sorted(meta.media_types) == ["image", "text"]

    def test_capabilities_derived_from_models(self):
        meta = ProviderMeta(
            display_name="Test",
            description="Test provider",
            required_keys=["api_key"],
            models={
                "m1": ModelInfo("M1", "text", ["text_generation", "vision"]),
                "m2": ModelInfo("M2", "image", ["text_to_image"]),
            },
        )
        assert sorted(meta.capabilities) == ["text_generation", "text_to_image", "vision"]

    def test_empty_models(self):
        meta = ProviderMeta(
            display_name="T", description="T", required_keys=[],
        )
        assert meta.media_types == []
        assert meta.capabilities == []


class TestProviderRegistry:
    """Verify all 4 providers have text models declared."""

    def test_all_providers_have_text_models(self):
        for provider_id, meta in PROVIDER_REGISTRY.items():
            text_models = [
                mid for mid, m in meta.models.items()
                if m.media_type == "text"
            ]
            assert len(text_models) > 0, f"{provider_id} has no text models"

    def test_all_providers_have_image_models(self):
        for provider_id in ("gemini-aistudio", "gemini-vertex", "ark", "grok"):
            meta = PROVIDER_REGISTRY[provider_id]
            image_models = [
                mid for mid, m in meta.models.items()
                if m.media_type == "image"
            ]
            assert len(image_models) > 0, f"{provider_id} has no image models"

    def test_all_providers_have_video_models(self):
        for provider_id in ("gemini-aistudio", "gemini-vertex", "ark", "grok"):
            meta = PROVIDER_REGISTRY[provider_id]
            video_models = [
                mid for mid, m in meta.models.items()
                if m.media_type == "video"
            ]
            assert len(video_models) > 0, f"{provider_id} has no video models"

    def test_each_media_type_has_default(self):
        for provider_id, meta in PROVIDER_REGISTRY.items():
            by_type: dict[str, list[ModelInfo]] = {}
            for m in meta.models.values():
                by_type.setdefault(m.media_type, []).append(m)
            for mt, models in by_type.items():
                defaults = [m for m in models if m.default]
                assert len(defaults) == 1, (
                    f"{provider_id} has {len(defaults)} default {mt} models, expected 1"
                )

    def test_media_types_property_includes_text(self):
        for provider_id, meta in PROVIDER_REGISTRY.items():
            assert "text" in meta.media_types, f"{provider_id} missing 'text' in media_types"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: FAIL (ModelInfo not defined, ProviderMeta has no models field)

- [ ] **Step 3: Rewrite lib/config/registry.py**

Replace the current file with the new ModelInfo-based structure. Key changes:
- Add `ModelInfo` dataclass
- Remove `media_types` and `capabilities` fields from ProviderMeta, add `models: dict[str, ModelInfo]`
- Add `media_types` and `capabilities` as `@property` computed from `models`
- Populate all 4 providers with text/image/video models

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str                # "text" | "image" | "video"
    capabilities: list[str]
    default: bool = False


@dataclass
class ProviderMeta:
    display_name: str
    description: str
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    models: dict[str, ModelInfo] = field(default_factory=dict)

    @property
    def media_types(self) -> list[str]:
        return sorted(set(m.media_type for m in self.models.values()))

    @property
    def capabilities(self) -> list[str]:
        return sorted(set(c for m in self.models.values() for c in m.capabilities))


PROVIDER_REGISTRY: dict[str, ProviderMeta] = {
    "gemini-aistudio": ProviderMeta(
        display_name="AI Studio",
        description="Google AI Studio 提供 Gemini 系列模型，支持图片和视频生成，适合快速原型和个人项目。",
        required_keys=["api_key"],
        optional_keys=["base_url", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        models={
            "gemini-3-flash-preview": ModelInfo(
                "Gemini 3 Flash", "text",
                ["text_generation", "structured_output", "vision"],
                default=True,
            ),
            "gemini-3.1-flash-image-preview": ModelInfo(
                "Gemini 3.1 Flash Image", "image",
                ["text_to_image", "image_to_image"],
                default=True,
            ),
            "veo-3.1-fast-generate-preview": ModelInfo(
                "Veo 3.1 Fast", "video",
                ["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
                default=True,
            ),
            "veo-3.1-generate-preview": ModelInfo(
                "Veo 3.1", "video",
                ["text_to_video", "image_to_video", "negative_prompt", "video_extend"],
            ),
        },
    ),
    "gemini-vertex": ProviderMeta(
        display_name="Vertex AI",
        description="Google Cloud Vertex AI 企业级平台，支持 Gemini 和 Imagen 模型，提供更高配额和音频生成能力。",
        required_keys=["credentials_path"],
        optional_keys=["gcs_bucket", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=[],
        models={
            "gemini-3-flash-preview": ModelInfo(
                "Gemini 3 Flash", "text",
                ["text_generation", "structured_output", "vision"],
                default=True,
            ),
            "gemini-3.1-flash-image-preview": ModelInfo(
                "Gemini 3.1 Flash Image", "image",
                ["text_to_image", "image_to_image"],
                default=True,
            ),
            "veo-3.1-fast-generate-001": ModelInfo(
                "Veo 3.1 Fast", "video",
                ["text_to_video", "image_to_video", "generate_audio", "negative_prompt", "video_extend"],
                default=True,
            ),
            "veo-3.1-generate-001": ModelInfo(
                "Veo 3.1", "video",
                ["text_to_video", "image_to_video", "generate_audio", "negative_prompt", "video_extend"],
            ),
        },
    ),
    "ark": ProviderMeta(
        display_name="火山方舟",
        description="字节跳动火山方舟 AI 平台，支持 Seedance 视频生成和 Seedream 图片生成，具备音频生成和种子控制能力。",
        required_keys=["api_key"],
        optional_keys=["video_rpm", "image_rpm", "request_gap", "video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
        models={
            "doubao-seed-2-0-lite-260215": ModelInfo(
                "豆包 Seed 2.0 Lite", "text",
                ["text_generation", "structured_output", "vision"],
                default=True,
            ),
            "doubao-seedream-5-0-lite-260128": ModelInfo(
                "Seedream 5.0 Lite", "image",
                ["text_to_image", "image_to_image"],
                default=True,
            ),
            "doubao-seedream-5-0-260128": ModelInfo(
                "Seedream 5.0", "image",
                ["text_to_image", "image_to_image"],
            ),
            "doubao-seedream-4-5-251128": ModelInfo(
                "Seedream 4.5", "image",
                ["text_to_image", "image_to_image"],
            ),
            "doubao-seedream-4-0-250828": ModelInfo(
                "Seedream 4.0", "image",
                ["text_to_image", "image_to_image"],
            ),
            "doubao-seedance-1-5-pro-251215": ModelInfo(
                "Seedance 1.5 Pro", "video",
                ["text_to_video", "image_to_video", "generate_audio", "seed_control", "flex_tier"],
                default=True,
            ),
        },
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        description="xAI Grok 模型，支持视频和图片生成。",
        required_keys=["api_key"],
        optional_keys=["video_rpm", "image_rpm", "request_gap", "video_max_workers", "image_max_workers"],
        secret_keys=["api_key"],
        models={
            "grok-4-1-fast-reasoning": ModelInfo(
                "Grok 4.1 Fast Reasoning", "text",
                ["text_generation", "structured_output", "vision"],
                default=True,
            ),
            "grok-imagine-image": ModelInfo(
                "Grok Imagine Image", "image",
                ["text_to_image", "image_to_image"],
                default=True,
            ),
            "grok-imagine-image-pro": ModelInfo(
                "Grok Imagine Image Pro", "image",
                ["text_to_image", "image_to_image"],
            ),
            "grok-imagine-video": ModelInfo(
                "Grok Imagine Video", "video",
                ["text_to_video", "image_to_video"],
                default=True,
            ),
        },
    ),
}
```

- [ ] **Step 4: Run new tests**

Run: `uv run python -m pytest tests/test_config_registry_models.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `uv run python -m pytest tests/ -x -q`

The `ProviderMeta` no longer uses `frozen=True` (since it now has mutable-default-supporting `@property`). Some tests or code may reference `meta.media_types` or `meta.capabilities` — these should still work since they're now computed properties that return the same types. Fix any failures.

- [ ] **Step 6: Commit**

```bash
git add lib/config/registry.py tests/test_config_registry_models.py
git commit -m "refactor: restructure ProviderMeta with per-model ModelInfo"
```

---

## Task 5: GeminiTextBackend

**Files:**
- Create: `lib/text_backends/gemini.py`, `lib/text_backends/prompts.py`, `tests/test_text_backends/test_gemini.py`

- [ ] **Step 1: Create STYLE_ANALYSIS_PROMPT**

`lib/text_backends/prompts.py`:
```python
"""文本生成 prompt 常量。"""

STYLE_ANALYSIS_PROMPT = (
    "Analyze the visual style of this image. Describe the lighting, "
    "color palette, medium (e.g., oil painting, digital art, photography), "
    "texture, and overall mood. Do NOT describe the subject matter "
    "(e.g., people, objects) or specific content. Focus ONLY on the "
    "artistic style. Provide a concise comma-separated list of descriptors "
    "suitable for an image generation prompt."
)
```

- [ ] **Step 2: Write failing tests**

`tests/test_text_backends/test_gemini.py`:
```python
"""GeminiTextBackend tests."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.text_backends.base import (
    ImageInput,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)
from lib.text_backends.gemini import GeminiTextBackend


class TestGeminiTextBackendProperties:
    def test_name(self):
        with patch("lib.text_backends.gemini.genai"):
            backend = GeminiTextBackend(api_key="test-key")
        assert backend.name == "gemini"

    def test_default_model(self):
        with patch("lib.text_backends.gemini.genai"):
            backend = GeminiTextBackend(api_key="test-key")
        assert backend.model == "gemini-3-flash-preview"

    def test_custom_model(self):
        with patch("lib.text_backends.gemini.genai"):
            backend = GeminiTextBackend(api_key="test-key", model="custom")
        assert backend.model == "custom"

    def test_capabilities(self):
        with patch("lib.text_backends.gemini.genai"):
            backend = GeminiTextBackend(api_key="test-key")
        assert backend.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }


class TestGeminiTextGeneration:
    @pytest.fixture
    def backend(self):
        with patch("lib.text_backends.gemini.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            b = GeminiTextBackend(api_key="test-key")
            b._mock_client = mock_client
            return b

    async def test_plain_text(self, backend):
        mock_response = SimpleNamespace(
            text="generated text",
            usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5),
        )
        backend._mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert isinstance(result, TextGenerationResult)
        assert result.text == "generated text"
        assert result.provider == "gemini"
        assert result.input_tokens == 10
        assert result.output_tokens == 5

    async def test_structured_output(self, backend):
        mock_response = SimpleNamespace(
            text='{"key": "value"}',
            usage_metadata=SimpleNamespace(prompt_token_count=20, candidates_token_count=10),
        )
        backend._mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        result = await backend.generate(
            TextGenerationRequest(prompt="generate json", response_schema=schema)
        )

        assert result.text == '{"key": "value"}'
        # Verify config included response_json_schema
        call_kwargs = backend._mock_client.aio.models.generate_content.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        assert config["response_mime_type"] == "application/json"
        assert config["response_json_schema"] == schema
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_text_backends/test_gemini.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement GeminiTextBackend**

`lib/text_backends/gemini.py`:
```python
"""GeminiTextBackend — Google Gemini 文本生成后端。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Set

from google import genai

from lib.gemini_shared import with_retry_async
from lib.providers import PROVIDER_GEMINI
from lib.text_backends.base import (
    ImageInput,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3-flash-preview"


class GeminiTextBackend:
    """Google Gemini 文本生成后端，支持 AI Studio 和 Vertex AI。"""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        backend: str = "aistudio",
        base_url: Optional[str] = None,
        gcs_bucket: Optional[str] = None,
    ):
        self._model = model or DEFAULT_MODEL
        self._capabilities: Set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }
        self._backend_type = backend.strip().lower()

        if self._backend_type == "vertex":
            from google.oauth2 import service_account
            from lib.gemini_shared import VERTEX_SCOPES
            from lib.system_config import resolve_vertex_credentials_path
            import json as json_module

            credentials_file = resolve_vertex_credentials_path(Path(__file__).parent.parent.parent)
            if credentials_file is None:
                raise ValueError("未找到 Vertex AI 凭证文件")

            with open(credentials_file) as f:
                creds_data = json_module.load(f)
            project_id = creds_data.get("project_id")
            if not project_id:
                raise ValueError(f"凭证文件 {credentials_file} 中未找到 project_id")

            credentials = service_account.Credentials.from_service_account_file(
                str(credentials_file), scopes=VERTEX_SCOPES,
            )
            self._client = genai.Client(
                vertexai=True, project=project_id, location="global", credentials=credentials,
            )
            logger.info("GeminiTextBackend: Vertex AI (凭证: %s)", credentials_file.name)
        else:
            if not api_key:
                raise ValueError("Gemini API Key 未提供")
            http_options = {"base_url": base_url} if base_url else None
            self._client = genai.Client(api_key=api_key, http_options=http_options)
            logger.info("GeminiTextBackend: AI Studio")

    @property
    def name(self) -> str:
        return PROVIDER_GEMINI

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> Set[TextCapability]:
        return self._capabilities

    @with_retry_async(max_attempts=3, backoff_seconds=(2, 4, 8))
    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """生成文本（纯文本/结构化输出/Vision）。"""
        # Build config
        config = None
        if request.response_schema:
            config = {
                "response_mime_type": "application/json",
                "response_json_schema": request.response_schema,
            }

        # Build contents
        contents: list = []

        # System prompt (as first text part)
        if request.system_prompt:
            config = config or {}
            config["system_instruction"] = request.system_prompt

        # Images (for vision)
        if request.images:
            from PIL import Image as PILImage
            for img_input in request.images:
                if img_input.path:
                    with PILImage.open(img_input.path) as img:
                        contents.append(img.copy())
                elif img_input.url:
                    contents.append({"url": img_input.url})

        # Text prompt
        contents.append(request.prompt)

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )

        # Extract token usage
        input_tokens = None
        output_tokens = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            input_tokens = getattr(response.usage_metadata, "prompt_token_count", None)
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", None)

        return TextGenerationResult(
            text=response.text.strip(),
            provider=PROVIDER_GEMINI,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_text_backends/test_gemini.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add lib/text_backends/gemini.py lib/text_backends/prompts.py tests/test_text_backends/test_gemini.py
git commit -m "feat: implement GeminiTextBackend"
```

---

## Task 6: ArkTextBackend

**Files:**
- Create: `lib/text_backends/ark.py`, `tests/test_text_backends/test_ark.py`

- [ ] **Step 1: Write failing tests**

`tests/test_text_backends/test_ark.py`:
```python
"""ArkTextBackend tests."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from lib.text_backends.ark import ArkTextBackend


class TestArkTextBackendProperties:
    def test_name(self):
        with patch("lib.text_backends.ark.Ark"):
            backend = ArkTextBackend(api_key="test-key")
        assert backend.name == "ark"

    def test_default_model(self):
        with patch("lib.text_backends.ark.Ark"):
            backend = ArkTextBackend(api_key="test-key")
        assert backend.model == "doubao-seed-2-0-lite-260215"

    def test_capabilities(self):
        with patch("lib.text_backends.ark.Ark"):
            backend = ArkTextBackend(api_key="test-key")
        assert backend.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }


class TestArkTextGeneration:
    @pytest.fixture
    def backend(self):
        with patch("lib.text_backends.ark.Ark") as MockArk:
            mock_client = MagicMock()
            MockArk.return_value = mock_client
            b = ArkTextBackend(api_key="test-key")
            b._mock_client = mock_client
            return b

    async def test_plain_text(self, backend):
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="ark output"),
            )],
            usage=SimpleNamespace(prompt_tokens=15, completion_tokens=8),
        )
        backend._mock_client.chat.completions.create = MagicMock(return_value=mock_response)

        with patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert result.text == "ark output"
        assert result.provider == "ark"
        assert result.input_tokens == 15
        assert result.output_tokens == 8

    async def test_structured_output(self, backend):
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    parsed=None,
                    content='{"key": "value"}',
                ),
            )],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=10),
        )
        backend._mock_client.beta.chat.completions.parse = MagicMock(return_value=mock_response)

        schema = {"type": "object", "properties": {"key": {"type": "string"}}}
        with patch("asyncio.to_thread", side_effect=lambda fn, **kw: fn(**kw)):
            result = await backend.generate(
                TextGenerationRequest(prompt="generate json", response_schema=schema)
            )

        assert result.text == '{"key": "value"}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_text_backends/test_ark.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ArkTextBackend**

`lib/text_backends/ark.py`:
```python
"""ArkTextBackend — 火山方舟文本生成后端。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Optional, Set

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

    def __init__(self, *, api_key: Optional[str] = None, model: Optional[str] = None):
        from volcenginesdkarkruntime import Ark

        self._api_key = api_key or os.environ.get("ARK_API_KEY")
        if not self._api_key:
            raise ValueError("Ark API Key 未提供")

        self._client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=self._api_key,
        )
        self._model = model or DEFAULT_MODEL
        self._capabilities: Set[TextCapability] = {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }

    @property
    def name(self) -> str:
        return PROVIDER_ARK

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> Set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """生成文本（纯文本/结构化输出/Vision）。"""
        if request.images:
            return await self._generate_vision(request)
        if request.response_schema:
            return await self._generate_structured(request)
        return await self._generate_plain(request)

    async def _generate_plain(self, request: TextGenerationRequest) -> TextGenerationResult:
        messages = self._build_messages(request)
        response = await asyncio.to_thread(
            self._client.chat.completions.create,
            model=self._model,
            messages=messages,
        )
        return self._parse_chat_response(response)

    async def _generate_structured(self, request: TextGenerationRequest) -> TextGenerationResult:
        messages = self._build_messages(request)

        response = await asyncio.to_thread(
            self._client.chat.completions.create,
            model=self._model,
            messages=messages,
            response_format={"type": "json_schema", "json_schema": {
                "name": "response",
                "schema": request.response_schema,
            }},
        )
        return self._parse_chat_response(response)

    async def _generate_vision(self, request: TextGenerationRequest) -> TextGenerationResult:
        content: list[dict[str, Any]] = []
        for img in request.images or []:
            if img.path:
                import base64
                from lib.image_backends.base import image_to_base64_data_uri
                data_uri = image_to_base64_data_uri(img.path)
                content.append({"type": "input_image", "image_url": data_uri})
            elif img.url:
                content.append({"type": "input_image", "image_url": img.url})

        content.append({"type": "input_text", "text": request.prompt})

        messages: list[dict] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": content})

        response = await asyncio.to_thread(
            self._client.responses.create,
            model=self._model,
            input=messages,
        )

        text = response.output_text if hasattr(response, "output_text") else str(response)
        input_tokens = getattr(getattr(response, "usage", None), "input_tokens", None)
        output_tokens = getattr(getattr(response, "usage", None), "output_tokens", None)

        return TextGenerationResult(
            text=text.strip(),
            provider=PROVIDER_ARK,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _build_messages(self, request: TextGenerationRequest) -> list[dict]:
        messages: list[dict] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        return messages

    def _parse_chat_response(self, response) -> TextGenerationResult:
        text = response.choices[0].message.content
        input_tokens = getattr(getattr(response, "usage", None), "prompt_tokens", None)
        output_tokens = getattr(getattr(response, "usage", None), "completion_tokens", None)

        return TextGenerationResult(
            text=text.strip(),
            provider=PROVIDER_ARK,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_text_backends/test_ark.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add lib/text_backends/ark.py tests/test_text_backends/test_ark.py
git commit -m "feat: implement ArkTextBackend"
```

---

## Task 7: GrokTextBackend

**Files:**
- Create: `lib/text_backends/grok.py`, `tests/test_text_backends/test_grok.py`

- [ ] **Step 1: Write failing tests**

`tests/test_text_backends/test_grok.py`:
```python
"""GrokTextBackend tests."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from lib.text_backends.grok import GrokTextBackend


class TestGrokTextBackendProperties:
    def test_name(self):
        with patch("lib.text_backends.grok.xai_sdk"):
            backend = GrokTextBackend(api_key="test-key")
        assert backend.name == "grok"

    def test_default_model(self):
        with patch("lib.text_backends.grok.xai_sdk"):
            backend = GrokTextBackend(api_key="test-key")
        assert backend.model == "grok-4-1-fast-reasoning"

    def test_capabilities(self):
        with patch("lib.text_backends.grok.xai_sdk"):
            backend = GrokTextBackend(api_key="test-key")
        assert backend.capabilities == {
            TextCapability.TEXT_GENERATION,
            TextCapability.STRUCTURED_OUTPUT,
            TextCapability.VISION,
        }


class TestGrokTextGeneration:
    @pytest.fixture
    def backend(self):
        with patch("lib.text_backends.grok.xai_sdk") as mock_sdk:
            mock_client = MagicMock()
            mock_sdk.Client.return_value = mock_client
            b = GrokTextBackend(api_key="test-key")
            b._mock_client = mock_client
            return b

    async def test_plain_text(self, backend):
        mock_chat = MagicMock()
        mock_response = SimpleNamespace(content="grok output")
        mock_chat.sample = MagicMock(return_value=mock_response)
        backend._mock_client.chat.create.return_value = mock_chat

        result = await backend.generate(TextGenerationRequest(prompt="hello"))

        assert result.text == "grok output"
        assert result.provider == "grok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_text_backends/test_grok.py -v`
Expected: FAIL

- [ ] **Step 3: Implement GrokTextBackend**

`lib/text_backends/grok.py`:
```python
"""GrokTextBackend — xAI Grok 文本生成后端。"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Set

import xai_sdk
from xai_sdk.chat import system, user, image

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

    def __init__(self, *, api_key: Optional[str] = None, model: Optional[str] = None):
        if not api_key:
            raise ValueError("XAI_API_KEY 未设置")

        self._client = xai_sdk.Client(api_key=api_key)
        self._model = model or DEFAULT_MODEL
        self._capabilities: Set[TextCapability] = {
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
    def capabilities(self) -> Set[TextCapability]:
        return self._capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        """生成文本（纯文本/结构化输出/Vision）。"""
        chat = self._client.chat.create(model=self._model)

        # System prompt
        if request.system_prompt:
            chat.append(system(request.system_prompt))

        # Build user message parts
        user_parts = []
        if request.images:
            for img_input in request.images:
                if img_input.path:
                    from lib.image_backends.base import image_to_base64_data_uri
                    data_uri = image_to_base64_data_uri(img_input.path)
                    user_parts.append(image(image_url=data_uri))
                elif img_input.url:
                    user_parts.append(image(image_url=img_input.url))

        chat.append(user(request.prompt, *user_parts))

        # Structured output or plain
        if request.response_schema:
            from pydantic import create_model
            # Build a simple Pydantic model from schema for .parse()
            DynamicModel = _schema_to_pydantic(request.response_schema)
            response, parsed = await asyncio.to_thread(chat.parse, DynamicModel)
            text = response.content if hasattr(response, "content") else parsed.model_dump_json()
        else:
            response = await asyncio.to_thread(chat.sample)
            text = response.content if hasattr(response, "content") else str(response)

        return TextGenerationResult(
            text=text.strip() if isinstance(text, str) else text,
            provider=PROVIDER_GROK,
            model=self._model,
        )


def _schema_to_pydantic(schema: dict):
    """Convert a JSON Schema dict to a dynamic Pydantic model.

    Simple implementation: uses Pydantic's create_model with string-typed fields.
    For production, consider using datamodel-code-generator or more sophisticated mapping.
    """
    from pydantic import create_model
    from typing import Any

    properties = schema.get("properties", {})
    fields = {}
    for name, prop in properties.items():
        # Map to Any for flexibility — the LLM will return properly typed JSON
        fields[name] = (Any, ...)

    return create_model("DynamicResponse", **fields)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_text_backends/test_grok.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add lib/text_backends/grok.py tests/test_text_backends/test_grok.py
git commit -m "feat: implement GrokTextBackend"
```

---

## Task 8: Text backend auto-registration

**Files:**
- Modify: `lib/text_backends/__init__.py`

- [ ] **Step 1: Write __init__.py with auto-registration**

`lib/text_backends/__init__.py`:
```python
"""文本生成服务层公共 API。"""

from lib.text_backends.base import (
    ImageInput,
    TextBackend,
    TextCapability,
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskType,
)
from lib.text_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    "ImageInput",
    "TextBackend",
    "TextCapability",
    "TextGenerationRequest",
    "TextGenerationResult",
    "TextTaskType",
    "create_backend",
    "get_registered_backends",
    "register_backend",
]

# Backend auto-registration
from lib.providers import PROVIDER_GEMINI
from lib.text_backends.gemini import GeminiTextBackend
register_backend(PROVIDER_GEMINI, GeminiTextBackend)

from lib.providers import PROVIDER_ARK
from lib.text_backends.ark import ArkTextBackend
register_backend(PROVIDER_ARK, ArkTextBackend)

from lib.providers import PROVIDER_GROK
from lib.text_backends.grok import GrokTextBackend
register_backend(PROVIDER_GROK, GrokTextBackend)
```

- [ ] **Step 2: Verify registration works**

Run: `uv run python -c "from lib.text_backends import get_registered_backends; print(get_registered_backends())"`
Expected: `['gemini', 'ark', 'grok']`

- [ ] **Step 3: Commit**

```bash
git add lib/text_backends/__init__.py
git commit -m "feat: auto-register text backends"
```

---

## Task 9: ConfigService + ConfigResolver text backend support

**Files:**
- Modify: `lib/config/service.py`, `lib/config/resolver.py`
- Create: `tests/test_text_backends/test_factory.py` (partial — resolver tests)

- [ ] **Step 1: Add get_default_text_backend to ConfigService**

In `lib/config/service.py`, add after the `_DEFAULT_IMAGE_BACKEND` line:
```python
_DEFAULT_TEXT_BACKEND = "gemini-aistudio/gemini-3-flash-preview"
```

Add method to `ConfigService`:
```python
async def get_default_text_backend(self) -> tuple[str, str]:
    raw = await self._setting_repo.get("default_text_backend", _DEFAULT_TEXT_BACKEND)
    return self._parse_backend(raw, _DEFAULT_TEXT_BACKEND)
```

Also add `models` to `ProviderStatus`:
```python
@dataclass
class ProviderStatus:
    name: str
    display_name: str
    description: str
    status: Literal["ready", "unconfigured", "error"]
    media_types: list[str]
    capabilities: list[str]
    required_keys: list[str]
    configured_keys: list[str]
    missing_keys: list[str]
    models: dict  # NEW: model_id -> ModelInfo as dict
```

Update `get_all_providers_status()` to populate `models` from `PROVIDER_REGISTRY`.

- [ ] **Step 2: Add text_backend_for_task to ConfigResolver**

In `lib/config/resolver.py`, add imports and methods:

```python
from lib.text_backends.base import TextTaskType
from lib.config.registry import PROVIDER_REGISTRY

# Task-type setting key mapping
_TEXT_TASK_SETTING_KEYS: dict[TextTaskType, str] = {
    TextTaskType.SCRIPT: "text_backend_script",
    TextTaskType.OVERVIEW: "text_backend_overview",
    TextTaskType.STYLE_ANALYSIS: "text_backend_style",
}
```

Add to `ConfigResolver`:
```python
async def text_backend_for_task(
    self, task_type: TextTaskType, project_name: str | None = None,
) -> tuple[str, str]:
    """解析文本 backend。优先级：项目级任务配置 → 全局任务配置 → 全局默认 → 自动推断"""
    async with self._session_factory() as session:
        svc = ConfigService(session)
        return await self._resolve_text_backend(svc, task_type, project_name)

async def default_text_backend(self) -> tuple[str, str]:
    async with self._session_factory() as session:
        svc = ConfigService(session)
        return await svc.get_default_text_backend()

async def _resolve_text_backend(
    self, svc: ConfigService, task_type: TextTaskType, project_name: str | None,
) -> tuple[str, str]:
    setting_key = _TEXT_TASK_SETTING_KEYS[task_type]

    # 1. Project-level task override
    if project_name:
        project = get_project_manager().load_project(project_name)
        project_val = project.get(setting_key)
        if project_val and "/" in str(project_val):
            return ConfigService._parse_backend(str(project_val), "")

    # 2. Global task-type setting
    task_val = await svc.get_setting(setting_key, "")
    if task_val and "/" in task_val:
        return ConfigService._parse_backend(task_val, "")

    # 3. Global default text backend
    default_val = await svc.get_setting("default_text_backend", "")
    if default_val and "/" in default_val:
        return ConfigService._parse_backend(default_val, "")

    # 4. Auto-resolve
    return await self._auto_resolve_backend(svc, "text")

async def _auto_resolve_backend(
    self, svc: ConfigService, media_type: str,
) -> tuple[str, str]:
    """遍历 PROVIDER_REGISTRY，找到第一个 ready 且支持该 media_type 的供应商。"""
    statuses = await svc.get_all_providers_status()
    ready = {s.name for s in statuses if s.status == "ready"}

    for provider_id, meta in PROVIDER_REGISTRY.items():
        if provider_id not in ready:
            continue
        for model_id, model_info in meta.models.items():
            if model_info.media_type == media_type and model_info.default:
                return provider_id, model_id

    raise ValueError(
        f"未找到可用的 {media_type} 供应商。"
        "请在「全局设置 → 供应商」页面配置至少一个供应商。"
    )
```

- [ ] **Step 3: Run tests**

Run: `uv run python -m pytest tests/ -x -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add lib/config/service.py lib/config/resolver.py
git commit -m "feat: add text backend config resolution with auto-resolve"
```

---

## Task 10: Text backend factory

**Files:**
- Create: `lib/text_backends/factory.py`, `tests/test_text_backends/test_factory.py`

- [ ] **Step 1: Write failing test**

`tests/test_text_backends/test_factory.py`:
```python
"""Text backend factory tests."""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from lib.text_backends.base import TextTaskType
from lib.text_backends.factory import create_text_backend_for_task


async def test_creates_backend_from_config():
    """Factory should resolve config and create the right backend."""
    mock_resolver = MagicMock()
    mock_resolver.text_backend_for_task = AsyncMock(return_value=("gemini-aistudio", "gemini-3-flash-preview"))
    mock_resolver.provider_config = AsyncMock(return_value={"api_key": "test-key"})

    with patch("lib.text_backends.factory.ConfigResolver", return_value=mock_resolver), \
         patch("lib.text_backends.factory.create_backend") as mock_create:
        mock_backend = MagicMock()
        mock_create.return_value = mock_backend

        result = await create_text_backend_for_task(TextTaskType.SCRIPT)

        mock_create.assert_called_once_with(
            "gemini", api_key="test-key", model="gemini-3-flash-preview",
        )
        assert result is mock_backend
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_text_backends/test_factory.py -v`
Expected: FAIL

- [ ] **Step 3: Implement factory.py**

`lib/text_backends/factory.py`:
```python
"""文本 backend 工厂。"""
from __future__ import annotations

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.text_backends.base import TextBackend, TextTaskType
from lib.text_backends.registry import create_backend

PROVIDER_ID_TO_BACKEND: dict[str, str] = {
    "gemini-aistudio": "gemini",
    "gemini-vertex": "gemini",
    "ark": "ark",
    "grok": "grok",
}


async def create_text_backend_for_task(
    task_type: TextTaskType,
    project_name: str | None = None,
) -> TextBackend:
    """从 DB 配置创建文本 backend。"""
    resolver = ConfigResolver(async_session_factory)
    provider_id, model_id = await resolver.text_backend_for_task(task_type, project_name)
    provider_config = await resolver.provider_config(provider_id)

    backend_name = PROVIDER_ID_TO_BACKEND.get(provider_id, provider_id)
    kwargs: dict = {"model": model_id}

    if provider_id == "gemini-vertex":
        kwargs["backend"] = "vertex"
        kwargs["gcs_bucket"] = provider_config.get("gcs_bucket")
    else:
        kwargs["api_key"] = provider_config.get("api_key")
        if provider_id == "gemini-aistudio":
            kwargs["base_url"] = provider_config.get("base_url")

    return create_backend(backend_name, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_text_backends/test_factory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/text_backends/factory.py tests/test_text_backends/test_factory.py
git commit -m "feat: add text backend factory with config resolution"
```

---

## Task 11: Refactor ScriptGenerator

**Files:**
- Modify: `lib/script_generator.py`, `tests/test_script_generator.py`

- [ ] **Step 1: Update ScriptGenerator**

Replace `GeminiClient` dependency with `TextBackend`:

```python
# Before:
from lib.gemini_client import GeminiClient

class ScriptGenerator:
    MODEL = "gemini-3-flash-preview"

    def __init__(self, project_path, client: Optional[GeminiClient] = None):
        self.client = client

    @classmethod
    async def create(cls, project_path):
        from lib.text_client import create_text_client
        client = await create_text_client()
        return cls(project_path, client)

    async def generate(self, episode, output_path=None):
        response_text = await self.client.generate_text_async(
            prompt=prompt, model=self.MODEL, response_schema=schema,
        )

# After:
from lib.text_backends.base import TextBackend, TextGenerationRequest, TextTaskType

class ScriptGenerator:
    def __init__(self, project_path, backend: Optional[TextBackend] = None):
        self.backend = backend

    @classmethod
    async def create(cls, project_path):
        from lib.text_backends.factory import create_text_backend_for_task
        project_name = Path(project_path).name
        backend = await create_text_backend_for_task(TextTaskType.SCRIPT, project_name)
        return cls(project_path, backend)

    async def generate(self, episode, output_path=None):
        if self.backend is None:
            raise RuntimeError("TextBackend 未初始化，请使用 ScriptGenerator.create() 工厂方法")
        result = await self.backend.generate(
            TextGenerationRequest(prompt=prompt, response_schema=schema)
        )
        response_text = result.text
```

- [ ] **Step 2: Update tests**

In `tests/test_script_generator.py`, replace `FakeGeminiClient` with a `FakeTextBackend` that satisfies the TextBackend protocol. Update assertions accordingly.

- [ ] **Step 3: Run tests**

Run: `uv run python -m pytest tests/test_script_generator.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add lib/script_generator.py tests/test_script_generator.py
git commit -m "refactor: ScriptGenerator uses TextBackend"
```

---

## Task 12: Refactor ProjectManager.generate_overview + upload_style_image

**Files:**
- Modify: `lib/project_manager.py`, `server/routers/files.py`, `tests/test_project_manager_more.py`, `tests/test_files_router.py`

- [ ] **Step 1: Update ProjectManager.generate_overview()**

```python
# Before:
from .text_client import create_text_client
client = await create_text_client()
response_text = await client.generate_text_async(prompt=prompt, model="gemini-3-flash-preview", response_schema=schema)

# After:
from .text_backends.factory import create_text_backend_for_task
from .text_backends.base import TextGenerationRequest, TextTaskType
backend = await create_text_backend_for_task(TextTaskType.OVERVIEW)
result = await backend.generate(TextGenerationRequest(prompt=prompt, response_schema=schema))
response_text = result.text
```

- [ ] **Step 2: Update upload_style_image**

In `server/routers/files.py`:
```python
# Before:
from lib.text_client import create_text_client
client = await create_text_client()
style_description = client.analyze_style_image(output_path)

# After:
from lib.text_backends.factory import create_text_backend_for_task
from lib.text_backends.base import TextGenerationRequest, TextTaskType, ImageInput
from lib.text_backends.prompts import STYLE_ANALYSIS_PROMPT

backend = await create_text_backend_for_task(TextTaskType.STYLE_ANALYSIS)
result = await backend.generate(
    TextGenerationRequest(prompt=STYLE_ANALYSIS_PROMPT, images=[ImageInput(path=output_path)])
)
style_description = result.text
```

- [ ] **Step 3: Update tests**

Update `tests/test_project_manager_more.py` and `tests/test_files_router.py` to mock `create_text_backend_for_task` instead of `create_text_client`.

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/test_project_manager_more.py tests/test_files_router.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add lib/project_manager.py server/routers/files.py tests/test_project_manager_more.py tests/test_files_router.py
git commit -m "refactor: ProjectManager and files router use TextBackend"
```

---

## Task 13: Refactor CLI script + cleanup dead code

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/generate-script/scripts/normalize_drama_script.py`
- Delete: `lib/gemini_client.py`, `lib/text_client.py`, `tests/test_text_client.py`
- Modify: `lib/__init__.py`

- [ ] **Step 1: Update CLI script**

In `normalize_drama_script.py`:
```python
# Before:
from lib.text_client import create_text_client_sync
client = create_text_client_sync()
response = client.generate_text(prompt=prompt, model=MODEL)

# After:
import asyncio
from lib.text_backends.factory import create_text_backend_for_task
from lib.text_backends.base import TextGenerationRequest, TextTaskType

backend = asyncio.run(create_text_backend_for_task(TextTaskType.SCRIPT))
result = asyncio.run(backend.generate(TextGenerationRequest(prompt=prompt)))
response = result.text
```

- [ ] **Step 2: Update lib/__init__.py**

```python
# Before:
from .gemini_client import GeminiClient

# After: remove the import
# (also remove GeminiClient from __all__)
```

- [ ] **Step 3: Delete dead files**

```bash
rm lib/gemini_client.py lib/text_client.py tests/test_text_client.py
```

- [ ] **Step 4: Run full test suite**

Run: `uv run python -m pytest tests/ -x -q`
Expected: All PASS. Fix any remaining imports of `gemini_client.GeminiClient`.

Also check: `uv run python -m pytest tests/test_gemini_client_more.py` and `tests/test_gemini_client_fd.py` — these test the deleted GeminiClient and should be deleted or migrated to test GeminiTextBackend.

```bash
rm tests/test_gemini_client_more.py tests/test_gemini_client_fd.py
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete GeminiClient and text_client.py, update CLI script"
```

---

## Task 14: Cost calculator text support

**Files:**
- Modify: `lib/cost_calculator.py`
- Modify: `tests/test_cost_calculator.py` (if exists, else create)

- [ ] **Step 1: Add text cost tables and method**

Add to `CostCalculator` class:

```python
# Gemini 文本 token 费率（美元/百万 token）
GEMINI_TEXT_COST = {
    "gemini-3-flash-preview": {"input": 0.10, "output": 0.40},
}

# Ark 文本 token 费率（元/百万 token）
ARK_TEXT_COST = {
    "doubao-seed-2-0-lite-260215": {"input": 0.30, "output": 0.60},
}

# Grok 文本 token 费率（美元/百万 token）
GROK_TEXT_COST = {
    "grok-4-1-fast-reasoning": {"input": 2.00, "output": 10.00},
}

def calculate_text_cost(
    self,
    input_tokens: int,
    output_tokens: int,
    provider: str,
    model: str | None = None,
) -> tuple[float, str]:
    """计算文本生成费用。返回 (amount, currency)。"""
    if provider == "ark":
        model = model or "doubao-seed-2-0-lite-260215"
        rates = self.ARK_TEXT_COST.get(model, {"input": 0.30, "output": 0.60})
        amount = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
        return amount, "CNY"
    elif provider == "grok":
        model = model or "grok-4-1-fast-reasoning"
        rates = self.GROK_TEXT_COST.get(model, {"input": 2.00, "output": 10.00})
        amount = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
        return amount, "USD"
    else:
        model = model or "gemini-3-flash-preview"
        rates = self.GEMINI_TEXT_COST.get(model, {"input": 0.10, "output": 0.40})
        amount = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
        return amount, "USD"
```

- [ ] **Step 2: Write tests for text cost calculation**

Add test cases covering each provider, verifying correct amount and currency.

- [ ] **Step 3: Run tests**

Run: `uv run python -m pytest tests/ -k "cost" -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add lib/cost_calculator.py tests/test_cost_calculator.py
git commit -m "feat: add text generation cost calculation"
```

---

## Task 15: Backend API changes

**Files:**
- Modify: `server/routers/system_config.py`, `server/routers/providers.py`

- [ ] **Step 1: Update system_config.py**

1. Replace `_PROVIDER_MODELS` dict with dynamic generation from `PROVIDER_REGISTRY.models`:

```python
async def _build_options(svc: ConfigService) -> dict[str, list[str]]:
    statuses = await svc.get_all_providers_status()
    ready_providers = {s.name for s in statuses if s.status == "ready"}

    video_backends: list[str] = []
    image_backends: list[str] = []
    text_backends: list[str] = []
    for provider_id, meta in PROVIDER_REGISTRY.items():
        if provider_id not in ready_providers:
            continue
        for model_id, model_info in meta.models.items():
            full = f"{provider_id}/{model_id}"
            if model_info.media_type == "video":
                video_backends.append(full)
            elif model_info.media_type == "image":
                image_backends.append(full)
            elif model_info.media_type == "text":
                text_backends.append(full)

    return {
        "video_backends": video_backends,
        "image_backends": image_backends,
        "text_backends": text_backends,
    }
```

2. Add text backend fields to `SystemConfigPatchRequest`:

```python
class SystemConfigPatchRequest(BaseModel):
    # ... existing fields ...
    default_text_backend: Optional[str] = None
    text_backend_script: Optional[str] = None
    text_backend_overview: Optional[str] = None
    text_backend_style: Optional[str] = None
```

3. Update GET handler to return text backend settings:

```python
settings["default_text_backend"] = all_s.get("default_text_backend") or ""
settings["text_backend_script"] = all_s.get("text_backend_script") or ""
settings["text_backend_overview"] = all_s.get("text_backend_overview") or ""
settings["text_backend_style"] = all_s.get("text_backend_style") or ""
```

4. Update PATCH handler to save text backend settings (add `"default_text_backend"` to the backend validation loop, add task-type keys to `_STRING_SETTINGS`):

```python
for backend_key in ("default_video_backend", "default_image_backend", "default_text_backend"):
    # ... existing validation logic ...

_STRING_SETTINGS = (
    # ... existing ...
    "text_backend_script",
    "text_backend_overview",
    "text_backend_style",
)
```

- [ ] **Step 2: Update providers.py**

Add `models` to `ProviderSummary`:

```python
class ModelInfoResponse(BaseModel):
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool

class ProviderSummary(BaseModel):
    # ... existing fields ...
    models: dict[str, ModelInfoResponse]
```

Update `list_providers` to populate `models` from `ProviderStatus.models`.

- [ ] **Step 3: Run tests**

Run: `uv run python -m pytest tests/ -x -q`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add server/routers/system_config.py server/routers/providers.py
git commit -m "feat: API support for text backend settings and model listing"
```

---

## Task 16: Frontend types + config-status-store

**Files:**
- Modify: `frontend/src/types/system.ts`, `frontend/src/stores/config-status-store.ts`

- [ ] **Step 1: Update types/system.ts**

```typescript
export interface SystemConfigSettings {
  default_video_backend: string;
  default_image_backend: string;
  default_text_backend: string;           // NEW
  text_backend_script: string;            // NEW
  text_backend_overview: string;          // NEW
  text_backend_style: string;             // NEW
  video_generate_audio: boolean;
  // ... rest unchanged
}

export interface SystemConfigOptions {
  video_backends: string[];
  image_backends: string[];
  text_backends: string[];                // NEW
}

export interface SystemConfigPatch {
  default_video_backend?: string;
  default_image_backend?: string;
  default_text_backend?: string;          // NEW
  text_backend_script?: string;           // NEW
  text_backend_overview?: string;         // NEW
  text_backend_style?: string;            // NEW
  // ... rest unchanged
}
```

- [ ] **Step 2: Update config-status-store.ts**

Replace the hard-coded backend check with provider-based ready check:

```typescript
async function getConfigIssues(): Promise<ConfigIssue[]> {
  const issues: ConfigIssue[] = [];

  const [{ providers }, configRes] = await Promise.all([
    API.getProviders(),
    API.getSystemConfig(),
  ]);

  const settings = configRes.settings;

  // 1. Check anthropic key
  if (!settings.anthropic_api_key?.is_set) {
    issues.push({
      key: "anthropic",
      tab: "agent",
      label: "ArcReel 智能体 API Key（Anthropic）未配置",
    });
  }

  // 2. Check any provider supports each media type
  const readyProviders = providers.filter((p) => p.status === "ready");

  const hasMediaType = (type: string) =>
    readyProviders.some((p) => p.media_types.includes(type));

  if (!hasMediaType("video")) {
    issues.push({
      key: "no-video-provider",
      tab: "providers",
      label: "未配置支持视频生成的供应商",
    });
  }
  if (!hasMediaType("image")) {
    issues.push({
      key: "no-image-provider",
      tab: "providers",
      label: "未配置支持图片生成的供应商",
    });
  }
  if (!hasMediaType("text")) {
    issues.push({
      key: "no-text-provider",
      tab: "providers",
      label: "未配置支持文本生成的供应商",
    });
  }

  return issues;
}
```

- [ ] **Step 3: Run frontend tests**

Run: `cd frontend && pnpm test`
Expected: Fix any failing tests due to changed types or store behavior.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/system.ts frontend/src/stores/config-status-store.ts
git commit -m "feat: frontend types and config-status for text backends"
```

---

## Task 17: Frontend MediaModelSection + SystemConfigPage tab rename

**Files:**
- Modify: `frontend/src/components/pages/settings/MediaModelSection.tsx`, `frontend/src/components/pages/SystemConfigPage.tsx`

- [ ] **Step 1: Rename tab in SystemConfigPage.tsx**

```typescript
// Before:
{ id: "media", label: "图片/视频", Icon: Film },

// After:
{ id: "media", label: "模型选择", Icon: Film },
```

- [ ] **Step 2: Expand MediaModelSection with text model selectors**

Add text model section after the image backend selector:

```tsx
{/* Text backend selectors */}
<div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
  <div className="mb-3 text-sm font-medium text-gray-100">文本模型</div>
  <p className="mb-3 text-xs text-gray-500">按任务类型配置文本模型，留空表示自动选择</p>

  {textBackends.length > 0 ? (
    <div className="space-y-3">
      {([
        ["text_backend_script", "剧本生成"],
        ["text_backend_overview", "概述生成"],
        ["text_backend_style", "风格分析"],
      ] as const).map(([key, label]) => (
        <div key={key}>
          <div className="mb-1 text-xs text-gray-400">{label}</div>
          <ProviderModelSelect
            value={draft[key] ?? settings[key] ?? ""}
            options={textBackends}
            providerNames={PROVIDER_NAMES}
            onChange={(v) => setDraft((prev) => ({ ...prev, [key]: v }))}
            allowDefault
            defaultHint="自动"
          />
        </div>
      ))}
    </div>
  ) : (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 px-3 py-2 text-sm text-gray-500">
      暂无可用文本供应商，请先在「供应商」页面配置 API 密钥
    </div>
  )}
</div>
```

Update the section heading:
```tsx
// Before:
<h3 className="text-lg font-semibold text-gray-100">图片 / 视频模型</h3>

// After:
<h3 className="text-lg font-semibold text-gray-100">模型选择</h3>
<p className="mt-1 text-sm text-gray-500">设置全局默认的生成模型，项目内可单独覆盖</p>
```

Add `textBackends` from options:
```tsx
const textBackends: string[] = options.text_backends ?? [];
```

- [ ] **Step 3: Run frontend tests and typecheck**

Run: `cd frontend && pnpm check`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/pages/settings/MediaModelSection.tsx \
  frontend/src/components/pages/SystemConfigPage.tsx
git commit -m "feat: rename tab to 模型选择, add text model selectors"
```

---

## Task 18: Frontend ProjectSettingsPage text model overrides

**Files:**
- Modify: `frontend/src/components/pages/ProjectSettingsPage.tsx`

- [ ] **Step 1: Add text model override selectors**

Add state for text backend overrides:
```tsx
const [textScript, setTextScript] = useState<string>("");
const [textOverview, setTextOverview] = useState<string>("");
const [textStyle, setTextStyle] = useState<string>("");
```

Load from project data in useEffect:
```tsx
setTextScript((project.text_backend_script as string | undefined) ?? "");
setTextOverview((project.text_backend_overview as string | undefined) ?? "");
setTextStyle((project.text_backend_style as string | undefined) ?? "");
```

Add to options state:
```tsx
const [options, setOptions] = useState<{
  video_backends: string[];
  image_backends: string[];
  text_backends: string[];
} | null>(null);
```

Add text model override section in the JSX (after the audio override section):

```tsx
{/* Text model overrides */}
<div className="rounded-xl border border-gray-800 bg-gray-950/40 p-4">
  <div className="mb-3 text-sm font-medium text-gray-100">文本模型</div>
  <div className="space-y-3">
    {([
      [textScript, setTextScript, "剧本生成"],
      [textOverview, setTextOverview, "概述生成"],
      [textStyle, setTextStyle, "风格分析"],
    ] as const).map(([value, setter, label], i) => (
      <div key={i}>
        <div className="mb-1 text-xs text-gray-400">{label}</div>
        <ProviderModelSelect
          value={value}
          options={options.text_backends}
          providerNames={PROVIDER_NAMES}
          onChange={setter}
          allowDefault
          defaultHint="跟随全局默认"
        />
      </div>
    ))}
  </div>
</div>
```

Include in save:
```tsx
await API.updateProject(projectName, {
  // ... existing ...
  text_backend_script: textScript || undefined,
  text_backend_overview: textOverview || undefined,
  text_backend_style: textStyle || undefined,
});
```

- [ ] **Step 2: Run frontend typecheck**

Run: `cd frontend && pnpm check`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/pages/ProjectSettingsPage.tsx
git commit -m "feat: project-level text model overrides"
```

---

## Task 19: Final integration test

**Files:** None new — verification only

- [ ] **Step 1: Run full backend test suite**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 2: Run frontend tests and typecheck**

Run: `cd frontend && pnpm check`
Expected: All PASS

- [ ] **Step 3: Run dev server smoke test**

Run: `uv run uvicorn server.app:app --port 1241 &`
Then verify:
- `curl http://localhost:1241/api/v1/providers` returns providers with `models` field
- `curl http://localhost:1241/api/v1/system/config` returns text backend settings
- Kill server

- [ ] **Step 4: Final commit**

If any fixes were needed during integration:
```bash
git add -A
git commit -m "fix: integration test fixes"
```
