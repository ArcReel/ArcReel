# 自定义供应商：协议下沉到模型层（endpoint）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `CustomProvider.api_format` 下沉成 `CustomProviderModel.endpoint`，按媒体类型细分 6 条 endpoint；provider 顶层保留 `discovery_format` 仅用于发现/连通测试。

**Architecture:** 新建 `ENDPOINT_REGISTRY` 单一真相源（`lib/custom_provider/endpoints.py`），`EndpointSpec.build_backend` 闭包替代 factory 的 if/elif 分发；ORM 字段一次性迁移；ConfigResolver / generation_tasks / router / 前端 Form 全部按 endpoint 推 media_type。

**Tech Stack:** Python 3.12 (FastAPI / SQLAlchemy async / alembic)；React 19 + TypeScript + Vitest；ruff line-length 120。

**Spec:** `docs/superpowers/specs/2026-04-26-custom-provider-model-endpoint-design.md`（commit `f86bebc`）

**Preflight：** 当前分支 `feature/custom-provider-model-endpoint`；无未提交改动；spec 已 commit；后端跑 `uv run pytest tests/test_custom_provider*` 全绿。

**File map:**

| 文件 | 责任 |
|---|---|
| `lib/custom_provider/endpoints.py`（新） | ENDPOINT_REGISTRY + EndpointSpec + infer_endpoint + 工具函数 |
| `lib/custom_provider/factory.py` | `create_custom_backend(provider, model_id, endpoint)` 派发 |
| `lib/custom_provider/discovery.py` | `discover_models(discovery_format, ...)` + `infer_endpoint` 调用 |
| `lib/db/models/custom_provider.py` | ORM 列改名/替换 |
| `lib/db/repositories/custom_provider_repo.py` | `list_enabled_models_by_media_type` 改造 |
| `lib/config/resolver.py` | custom 分支按 endpoint 推 media_type |
| `server/services/generation_tasks.py` | `_create_custom_backend` 简化签名 |
| `server/routers/custom_providers.py` | Pydantic schemas + 校验 + i18n |
| `lib/i18n/{zh,en}/errors.py` | 新增 5 条 error keys |
| `alembic/versions/<rev>_endpoint_refactor.py`（新） | 一次性迁移 |
| `frontend/src/types/custom-provider.ts` | TS 类型字段 |
| `frontend/src/api.ts` | 5 处 API client 函数签名 |
| `frontend/src/components/pages/settings/CustomProviderForm.tsx` | 顶部 discoveryFormat 弱化 + 模型行 endpoint select |
| `frontend/src/components/pages/settings/CustomProviderDetail.tsx` | 显示 endpoint |
| `frontend/src/i18n/{zh,en}/dashboard.ts` | endpoint 与 discovery_format 相关展示文案 |

---

## Task 1: ENDPOINT_REGISTRY 基础模块

**Files:**
- Create: `lib/custom_provider/endpoints.py`
- Create: `tests/test_custom_provider_endpoints.py`

- [ ] **Step 1：写失败测试** `tests/test_custom_provider_endpoints.py`

```python
"""ENDPOINT_REGISTRY 完整性与工具函数单测。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    endpoint_to_media_type,
    get_endpoint_spec,
    infer_endpoint,
    list_endpoints_by_media_type,
)


class TestRegistry:
    def test_six_endpoints(self):
        assert set(ENDPOINT_REGISTRY.keys()) == {
            "openai-chat",
            "gemini-generate",
            "openai-images",
            "gemini-image",
            "openai-video",
            "newapi-video",
        }

    def test_each_spec_has_required_fields(self):
        for key, spec in ENDPOINT_REGISTRY.items():
            assert spec.key == key
            assert spec.media_type in {"text", "image", "video"}
            assert spec.family in {"openai", "google", "newapi"}
            assert spec.display_name_key.startswith("endpoint_")
            assert callable(spec.build_backend)

    def test_media_type_groups(self):
        text_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "text"}
        image_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "image"}
        video_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "video"}
        assert text_keys == {"openai-chat", "gemini-generate"}
        assert image_keys == {"openai-images", "gemini-image"}
        assert video_keys == {"openai-video", "newapi-video"}


class TestHelpers:
    def test_get_endpoint_spec(self):
        spec = get_endpoint_spec("openai-chat")
        assert spec.media_type == "text"

    def test_get_endpoint_spec_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown endpoint"):
            get_endpoint_spec("anthropic-messages")

    def test_endpoint_to_media_type(self):
        assert endpoint_to_media_type("newapi-video") == "video"
        assert endpoint_to_media_type("gemini-image") == "image"

    def test_endpoint_to_media_type_unknown_raises(self):
        with pytest.raises(ValueError):
            endpoint_to_media_type("nope")

    def test_list_endpoints_by_media_type(self):
        text = list_endpoints_by_media_type("text")
        assert {s.key for s in text} == {"openai-chat", "gemini-generate"}


class TestInferEndpoint:
    @pytest.mark.parametrize(
        "model_id,discovery_format,expected",
        [
            ("gpt-4o", "openai", "openai-chat"),
            ("gemini-2.5-flash", "google", "gemini-generate"),
            ("gemini-2.5-flash", "openai", "openai-chat"),  # 中转站常见
            ("claude-sonnet-4.5", "openai", "openai-chat"),
            ("dall-e-3", "openai", "openai-images"),
            ("gpt-image-1", "openai", "openai-images"),
            ("imagen-4", "google", "gemini-image"),
            ("imagen-4", "openai", "openai-images"),
            ("flux-pro", "openai", "openai-images"),
            ("sora-2", "openai", "openai-video"),
            ("kling-v2", "openai", "newapi-video"),
            ("veo-3", "openai", "newapi-video"),
            ("veo-3", "google", "newapi-video"),  # google 直连无视频端点 → 兜底 newapi
            ("seedance-1.0", "openai", "newapi-video"),
            ("hailuo-02", "openai", "newapi-video"),
        ],
    )
    def test_infer(self, model_id, discovery_format, expected):
        assert infer_endpoint(model_id, discovery_format) == expected
```

- [ ] **Step 2：跑测试确认 FAIL**

Run: `uv run pytest tests/test_custom_provider_endpoints.py -v`
Expected: collection error / ImportError on `lib.custom_provider.endpoints`

- [ ] **Step 3：写 `lib/custom_provider/endpoints.py`**

```python
"""ENDPOINT_REGISTRY — 自定义供应商可用 endpoint 单一真相源。

每条 endpoint 是一个 EndpointSpec，绑定 media_type、family 与 build_backend 闭包。
factory.create_custom_backend 通过 endpoint 字符串查表派发。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.config.url_utils import ensure_google_base_url, ensure_openai_base_url
from lib.custom_provider.backends import CustomImageBackend, CustomTextBackend, CustomVideoBackend
from lib.image_backends.gemini import GeminiImageBackend
from lib.image_backends.openai import OpenAIImageBackend
from lib.text_backends.gemini import GeminiTextBackend
from lib.text_backends.openai import OpenAITextBackend
from lib.video_backends.newapi import NewAPIVideoBackend
from lib.video_backends.openai import OpenAIVideoBackend

if TYPE_CHECKING:
    from lib.db.models.custom_provider import CustomProvider


# ── EndpointSpec 数据类型 ───────────────────────────────────────────


@dataclass(frozen=True)
class EndpointSpec:
    """单条 endpoint 的元数据 + backend 构造闭包。"""

    key: str  # "openai-chat"
    media_type: str  # "text" | "image" | "video"
    family: str  # "openai" | "google" | "newapi"
    display_name_key: str  # 前端 i18n key（dashboard ns）
    build_backend: Callable[["CustomProvider", str], CustomTextBackend | CustomImageBackend | CustomVideoBackend]


# ── 各 endpoint 的 build_backend 闭包 ──────────────────────────────


def _build_openai_chat(provider, model_id: str) -> CustomTextBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAITextBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomTextBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_gemini_generate(provider, model_id: str) -> CustomTextBackend:
    base_url = ensure_google_base_url(provider.base_url) or None
    delegate = GeminiTextBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomTextBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_images(provider, model_id: str) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIImageBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_gemini_image(provider, model_id: str) -> CustomImageBackend:
    base_url = ensure_google_base_url(provider.base_url) or None
    delegate = GeminiImageBackend(api_key=provider.api_key, base_url=base_url, image_model=model_id)
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_video(provider, model_id: str) -> CustomVideoBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIVideoBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_newapi_video(provider, model_id: str) -> CustomVideoBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = NewAPIVideoBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


# ── ENDPOINT_REGISTRY 注册表 ───────────────────────────────────────


ENDPOINT_REGISTRY: dict[str, EndpointSpec] = {
    "openai-chat": EndpointSpec(
        key="openai-chat",
        media_type="text",
        family="openai",
        display_name_key="endpoint_openai_chat_display",
        build_backend=_build_openai_chat,
    ),
    "gemini-generate": EndpointSpec(
        key="gemini-generate",
        media_type="text",
        family="google",
        display_name_key="endpoint_gemini_generate_display",
        build_backend=_build_gemini_generate,
    ),
    "openai-images": EndpointSpec(
        key="openai-images",
        media_type="image",
        family="openai",
        display_name_key="endpoint_openai_images_display",
        build_backend=_build_openai_images,
    ),
    "gemini-image": EndpointSpec(
        key="gemini-image",
        media_type="image",
        family="google",
        display_name_key="endpoint_gemini_image_display",
        build_backend=_build_gemini_image,
    ),
    "openai-video": EndpointSpec(
        key="openai-video",
        media_type="video",
        family="openai",
        display_name_key="endpoint_openai_video_display",
        build_backend=_build_openai_video,
    ),
    "newapi-video": EndpointSpec(
        key="newapi-video",
        media_type="video",
        family="newapi",
        display_name_key="endpoint_newapi_video_display",
        build_backend=_build_newapi_video,
    ),
}


# ── 工具函数 ───────────────────────────────────────────────────────


def get_endpoint_spec(endpoint: str) -> EndpointSpec:
    spec = ENDPOINT_REGISTRY.get(endpoint)
    if spec is None:
        raise ValueError(f"unknown endpoint: {endpoint!r}")
    return spec


def endpoint_to_media_type(endpoint: str) -> str:
    return get_endpoint_spec(endpoint).media_type


def list_endpoints_by_media_type(media_type: str) -> list[EndpointSpec]:
    return [s for s in ENDPOINT_REGISTRY.values() if s.media_type == media_type]


# ── 启发式：从 model_id + discovery_format 推默认 endpoint ─────────


_IMAGE_PATTERN = re.compile(r"image|dall|img|imagen|flux", re.IGNORECASE)
_VIDEO_PATTERN = re.compile(
    r"video|sora|kling|wan|seedance|cog|mochi|veo|pika|minimax|hailuo|seedream|jimeng|runway",
    re.IGNORECASE,
)
_SORA_PATTERN = re.compile(r"sora", re.IGNORECASE)


def infer_endpoint(model_id: str, discovery_format: str) -> str:
    """根据模型 id 与 discovery_format 推默认 endpoint。

    1) 视频家族:
       - sora-* 且 discovery_format=openai → "openai-video"
       - 其他视频家族 → "newapi-video" (中转站最常见，google 直连本无视频也兜底)
    2) 图像家族 → discovery_format=google 走 "gemini-image" 否则 "openai-images"
    3) 文本（默认）→ discovery_format=google 走 "gemini-generate" 否则 "openai-chat"
    """
    if _VIDEO_PATTERN.search(model_id):
        if discovery_format == "openai" and _SORA_PATTERN.search(model_id):
            return "openai-video"
        return "newapi-video"
    if _IMAGE_PATTERN.search(model_id):
        if discovery_format == "google":
            return "gemini-image"
        return "openai-images"
    if discovery_format == "google":
        return "gemini-generate"
    return "openai-chat"
```

- [ ] **Step 4：跑测试确认 PASS**

Run: `uv run pytest tests/test_custom_provider_endpoints.py -v`
Expected: 全绿（约 25 个用例）。

- [ ] **Step 5：commit**

```bash
git add lib/custom_provider/endpoints.py tests/test_custom_provider_endpoints.py
git commit -m "feat(custom-provider): introduce ENDPOINT_REGISTRY single source of truth"
```

---

## Task 2: ORM 字段改造

**Files:**
- Modify: `lib/db/models/custom_provider.py`
- Modify: `tests/test_custom_provider_models.py`（如有）

- [ ] **Step 1：先看现有 model 测试**

Run: `uv run pytest tests/test_custom_provider_models.py -v 2>&1 | tail -20`
记录哪些断言依赖 `api_format` / `media_type`。

- [ ] **Step 2：改写 ORM 模型**

替换 `lib/db/models/custom_provider.py:18` 行：
```python
- api_format: Mapped[str] = mapped_column(String(32), nullable=False)  # "openai" | "google" | "newapi"
+ discovery_format: Mapped[str] = mapped_column(String(32), nullable=False)  # "openai" | "google"
```

替换 `lib/db/models/custom_provider.py:44` 行：
```python
- media_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "text" | "image" | "video"
+ endpoint: Mapped[str] = mapped_column(String(32), nullable=False)  # ENDPOINT_REGISTRY key
```

- [ ] **Step 3：更新 `tests/test_custom_provider_models.py` 中所有 `api_format=` / `media_type=` 入参为新名**

凡 `CustomProvider(api_format="...")` → `CustomProvider(discovery_format="...")`；
凡 `CustomProviderModel(media_type="text")` → `CustomProviderModel(endpoint="openai-chat")` 等等。
（六对一映射：text→openai-chat 或 gemini-generate；image→openai-images 或 gemini-image；video→newapi-video 或 openai-video；按测试上下文选）。

- [ ] **Step 4：跑测试**

Run: `uv run pytest tests/test_custom_provider_models.py -v`
Expected: 此时 ORM 单测应绿（不依赖现有 DB schema，仅校验对象构造）。

- [ ] **Step 5：commit**

```bash
git add lib/db/models/custom_provider.py tests/test_custom_provider_models.py
git commit -m "refactor(custom-provider): rename api_format→discovery_format, media_type→endpoint on ORM"
```

> 注：本次 commit 后 alembic 迁移尚未创建，DB 与 ORM 短暂错位；Task 9 会补齐迁移。本任务后续到 Task 8 的测试都用 mock，所以不会真访问 DB。

---

## Task 3: Repository 适配

**Files:**
- Modify: `lib/db/repositories/custom_provider_repo.py`
- Modify: `tests/test_custom_provider_repo.py`

- [ ] **Step 1：写失败测试** — 替换 `tests/test_custom_provider_repo.py` 中所有引用 `media_type="..."` 为 `endpoint="..."` 与 `api_format="..."` 为 `discovery_format="..."`；新增一条用例覆盖 `list_enabled_models_by_media_type` 通过 `endpoint_to_media_type` 推算分组。

最简单的新增用例（追加到文件末尾）：

```python
@pytest.mark.asyncio
async def test_list_enabled_models_by_media_type_uses_endpoint(session):
    """list_enabled_models_by_media_type 应按 endpoint 推算 media_type 过滤。"""
    repo = CustomProviderRepository(session)
    provider = await repo.create_provider(
        display_name="P",
        discovery_format="openai",
        base_url="https://x",
        api_key="k",
        models=[
            {"model_id": "gpt-4o", "display_name": "gpt-4o", "endpoint": "openai-chat",
             "is_default": False, "is_enabled": True, "price_unit": None, "price_input": None,
             "price_output": None, "currency": None, "supported_durations": None, "resolution": None},
            {"model_id": "kling-2", "display_name": "kling-2", "endpoint": "newapi-video",
             "is_default": False, "is_enabled": True, "price_unit": None, "price_input": None,
             "price_output": None, "currency": None, "supported_durations": None, "resolution": None},
        ],
    )
    await session.commit()

    text_models = await repo.list_enabled_models_by_media_type("text")
    assert {m.model_id for m in text_models} == {"gpt-4o"}
    video_models = await repo.list_enabled_models_by_media_type("video")
    assert {m.model_id for m in video_models} == {"kling-2"}
```

- [ ] **Step 2：跑测试，确认相关 case FAIL**

Run: `uv run pytest tests/test_custom_provider_repo.py -v`
Expected: 新案例 FAIL（媒体类型还按旧字段过滤）。

- [ ] **Step 3：实现修改 — `lib/db/repositories/custom_provider_repo.py:137-148`**

```python
async def list_enabled_models_by_media_type(self, media_type: str) -> list[CustomProviderModel]:
    """跨所有供应商获取指定媒体类型的已启用模型（按 endpoint 推算 media_type）。"""
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    target_endpoints = [k for k, s in ENDPOINT_REGISTRY.items() if s.media_type == media_type]
    if not target_endpoints:
        return []
    stmt = (
        select(CustomProviderModel)
        .where(
            CustomProviderModel.endpoint.in_(target_endpoints),
            CustomProviderModel.is_enabled == True,  # noqa: E712
        )
        .order_by(CustomProviderModel.id)
    )
    result = await self.session.execute(stmt)
    return list(result.scalars())
```

`get_default_model` 同理改造（`lib/db/repositories/custom_provider_repo.py:159-168`）：

```python
async def get_default_model(self, provider_id: int, media_type: str) -> CustomProviderModel | None:
    """获取指定供应商 + 媒体类型的默认已启用模型（endpoint 推算）。"""
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    target_endpoints = [k for k, s in ENDPOINT_REGISTRY.items() if s.media_type == media_type]
    if not target_endpoints:
        return None
    stmt = select(CustomProviderModel).where(
        CustomProviderModel.provider_id == provider_id,
        CustomProviderModel.endpoint.in_(target_endpoints),
        CustomProviderModel.is_default == True,  # noqa: E712
        CustomProviderModel.is_enabled == True,  # noqa: E712
    )
    result = await self.session.execute(stmt)
    return result.scalar_one_or_none()
```

`create_provider` 形参签名 `api_format: str` → `discovery_format: str`（同步改实例化）。

- [ ] **Step 4：跑测试**

Run: `uv run pytest tests/test_custom_provider_repo.py -v`
Expected: 全绿。

- [ ] **Step 5：commit**

```bash
git add lib/db/repositories/custom_provider_repo.py tests/test_custom_provider_repo.py
git commit -m "refactor(custom-provider): repo.list_enabled_models_by_media_type derives media_type from endpoint"
```

---

## Task 4: Backend factory 重写

**Files:**
- Modify: `lib/custom_provider/factory.py`
- Modify: `tests/test_custom_provider_factory.py`

- [ ] **Step 1：完全重写 `tests/test_custom_provider_factory.py`**

```python
"""create_custom_backend(provider, model_id, endpoint) 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.custom_provider.backends import CustomImageBackend, CustomTextBackend, CustomVideoBackend
from lib.custom_provider.factory import create_custom_backend


def _make_provider(*, base_url: str = "https://api.example.com/v1", api_key: str = "sk-test") -> MagicMock:
    p = MagicMock()
    p.base_url = base_url
    p.api_key = api_key
    p.provider_id = "custom-42"
    return p


class TestEndpointDispatch:
    @patch("lib.custom_provider.endpoints.OpenAITextBackend")
    def test_openai_chat(self, mock_cls):
        provider = _make_provider()
        result = create_custom_backend(provider=provider, model_id="gpt-4o", endpoint="openai-chat")
        assert isinstance(result, CustomTextBackend)
        assert result.model == "gpt-4o"
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="gpt-4o")

    @patch("lib.custom_provider.endpoints.GeminiTextBackend")
    def test_gemini_generate(self, mock_cls):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com")
        create_custom_backend(provider=provider, model_id="gemini-2.5-flash", endpoint="gemini-generate")
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            model="gemini-2.5-flash",
        )

    @patch("lib.custom_provider.endpoints.OpenAIImageBackend")
    def test_openai_images(self, mock_cls):
        provider = _make_provider()
        result = create_custom_backend(provider=provider, model_id="dall-e-3", endpoint="openai-images")
        assert isinstance(result, CustomImageBackend)
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="dall-e-3")

    @patch("lib.custom_provider.endpoints.GeminiImageBackend")
    def test_gemini_image(self, mock_cls):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com")
        create_custom_backend(provider=provider, model_id="imagen-4", endpoint="gemini-image")
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            image_model="imagen-4",
        )

    @patch("lib.custom_provider.endpoints.OpenAIVideoBackend")
    def test_openai_video(self, mock_cls):
        provider = _make_provider()
        result = create_custom_backend(provider=provider, model_id="sora-2", endpoint="openai-video")
        assert isinstance(result, CustomVideoBackend)
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="sora-2")

    @patch("lib.custom_provider.endpoints.NewAPIVideoBackend")
    def test_newapi_video(self, mock_cls):
        provider = _make_provider()
        create_custom_backend(provider=provider, model_id="kling-v2", endpoint="newapi-video")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="kling-v2")


class TestUrlNormalization:
    @patch("lib.custom_provider.endpoints.OpenAITextBackend")
    def test_openai_appends_v1(self, mock_cls):
        provider = _make_provider(base_url="https://api.example.com")
        create_custom_backend(provider=provider, model_id="gpt-4o", endpoint="openai-chat")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url="https://api.example.com/v1", model="gpt-4o")

    @patch("lib.custom_provider.endpoints.GeminiTextBackend")
    def test_google_strips_v1beta(self, mock_cls):
        provider = _make_provider(base_url="https://generativelanguage.googleapis.com/v1beta")
        create_custom_backend(provider=provider, model_id="gemini-2.5", endpoint="gemini-generate")
        mock_cls.assert_called_once_with(
            api_key="sk-test",
            base_url="https://generativelanguage.googleapis.com/",
            model="gemini-2.5",
        )

    @patch("lib.custom_provider.endpoints.GeminiTextBackend")
    def test_google_empty_base_url(self, mock_cls):
        provider = _make_provider(base_url="")
        create_custom_backend(provider=provider, model_id="gemini-2.5", endpoint="gemini-generate")
        mock_cls.assert_called_once_with(api_key="sk-test", base_url=None, model="gemini-2.5")


class TestErrors:
    def test_unknown_endpoint(self):
        provider = _make_provider()
        with pytest.raises(ValueError, match="unknown endpoint"):
            create_custom_backend(provider=provider, model_id="claude-4", endpoint="anthropic-messages")
```

- [ ] **Step 2：跑测试 — Expected FAIL（factory 仍按 api_format / media_type 派发）**

Run: `uv run pytest tests/test_custom_provider_factory.py -v`

- [ ] **Step 3：重写 `lib/custom_provider/factory.py`**

替换整个文件：

```python
"""自定义供应商 Backend 工厂（按 endpoint 派发）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.custom_provider.backends import CustomImageBackend, CustomTextBackend, CustomVideoBackend
from lib.custom_provider.endpoints import get_endpoint_spec

if TYPE_CHECKING:
    from lib.db.models.custom_provider import CustomProvider


def create_custom_backend(
    *,
    provider: CustomProvider,
    model_id: str,
    endpoint: str,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend:
    """按 endpoint 查 ENDPOINT_REGISTRY 并构造 Backend。

    Args:
        provider: 自定义供应商 ORM 对象（需 base_url / api_key / provider_id 属性）
        model_id: 该次调用使用的具体模型 id
        endpoint: ENDPOINT_REGISTRY 的键

    Raises:
        ValueError: endpoint 不在 ENDPOINT_REGISTRY 中
    """
    spec = get_endpoint_spec(endpoint)
    return spec.build_backend(provider, model_id)
```

- [ ] **Step 4：跑测试**

Run: `uv run pytest tests/test_custom_provider_factory.py -v`
Expected: 全绿。

- [ ] **Step 5：commit**

```bash
git add lib/custom_provider/factory.py tests/test_custom_provider_factory.py
git commit -m "refactor(custom-provider): factory dispatches by endpoint via ENDPOINT_REGISTRY"
```

---

## Task 5: Discovery 模块改造

**Files:**
- Modify: `lib/custom_provider/discovery.py`
- Modify: `tests/test_model_discovery.py`

- [ ] **Step 1：改 `tests/test_model_discovery.py`**

把现有断言里的 `media_type="text"` 改为 `endpoint="openai-chat"` 等；新增 `infer_endpoint` 启发式覆盖（已在 Task 1 测过，仅 smoke check）。
关键改动：`_build_result_list` 现在返回 `endpoint` 字段，每种 media_type 推算分组后取首项 default。

新增 fixture 路径示例（替换文件中所有 mock 解释）：

```python
def test_discover_openai_returns_endpoints(monkeypatch):
    """discover_models(discovery_format='openai', ...) 返回项含 endpoint 字段。"""
    from lib.custom_provider import discovery

    fake_models = [MagicMock(id="gpt-4o"), MagicMock(id="kling-v2"), MagicMock(id="dall-e-3")]
    fake_client = MagicMock()
    fake_client.models.list.return_value = fake_models
    monkeypatch.setattr(discovery, "OpenAI", lambda **kw: fake_client)

    result = asyncio.run(discovery.discover_models(
        discovery_format="openai",
        base_url="https://x",
        api_key="k",
    ))
    by_id = {m["model_id"]: m for m in result}
    assert by_id["gpt-4o"]["endpoint"] == "openai-chat"
    assert by_id["kling-v2"]["endpoint"] == "newapi-video"
    assert by_id["dall-e-3"]["endpoint"] == "openai-images"
    # 每种 media_type 仅一个 default
    defaults = [m for m in result if m["is_default"]]
    assert {m["endpoint"] for m in defaults} == {"openai-chat", "newapi-video", "openai-images"}
```

- [ ] **Step 2：跑测试 — Expected FAIL**

Run: `uv run pytest tests/test_model_discovery.py -v`

- [ ] **Step 3：重写 `lib/custom_provider/discovery.py`**

```python
"""自定义供应商模型发现（按 discovery_format 选 SDK；返回 endpoint）。"""

from __future__ import annotations

import asyncio
import logging

from google import genai
from openai import OpenAI

from lib.custom_provider.endpoints import endpoint_to_media_type, infer_endpoint

logger = logging.getLogger(__name__)


async def discover_models(
    *,
    discovery_format: str,
    base_url: str | None,
    api_key: str,
) -> list[dict]:
    """查询供应商可用模型列表，每项标注 endpoint。

    Returns:
        list of dict: model_id, display_name, endpoint, is_default, is_enabled
    """
    if discovery_format == "openai":
        return await _discover_openai(base_url, api_key)
    elif discovery_format == "google":
        return await _discover_google(base_url, api_key)
    else:
        raise ValueError(f"不支持的 discovery_format: {discovery_format!r}，支持: 'openai', 'google'")


async def _discover_openai(base_url: str | None, api_key: str) -> list[dict]:
    def _sync():
        from lib.config.url_utils import ensure_openai_base_url

        client = OpenAI(api_key=api_key, base_url=ensure_openai_base_url(base_url))
        raw_models = client.models.list()
        models = sorted(raw_models, key=lambda m: m.id)
        return _build_result_list([(m.id, infer_endpoint(m.id, "openai")) for m in models])

    return await asyncio.to_thread(_sync)


async def _discover_google(base_url: str | None, api_key: str) -> list[dict]:
    def _sync():
        from lib.config.url_utils import ensure_google_base_url

        kwargs: dict = {"api_key": api_key}
        effective_url = ensure_google_base_url(base_url) if base_url else None
        if effective_url:
            kwargs["http_options"] = {"base_url": effective_url}
        client = genai.Client(**kwargs)
        raw_models = client.models.list()

        entries: list[tuple[str, str]] = []
        for m in raw_models:
            model_id = m.name
            if model_id.startswith("models/"):
                model_id = model_id[len("models/") :]
            entries.append((model_id, infer_endpoint(model_id, "google")))

        entries.sort(key=lambda e: e[0])
        return _build_result_list(entries)

    return await asyncio.to_thread(_sync)


def _build_result_list(entries: list[tuple[str, str]]) -> list[dict]:
    """每个推算 media_type 取首项为 default。"""
    seen_media: set[str] = set()
    result: list[dict] = []
    for model_id, endpoint in entries:
        media = endpoint_to_media_type(endpoint)
        is_default = media not in seen_media
        seen_media.add(media)
        result.append(
            {
                "model_id": model_id,
                "display_name": model_id,
                "endpoint": endpoint,
                "is_default": is_default,
                "is_enabled": True,
            }
        )
    return result
```

- [ ] **Step 4：跑测试**

Run: `uv run pytest tests/test_model_discovery.py tests/test_custom_provider_endpoints.py -v`
Expected: 全绿。

- [ ] **Step 5：commit**

```bash
git add lib/custom_provider/discovery.py tests/test_model_discovery.py
git commit -m "refactor(custom-provider): discovery uses discovery_format + infer_endpoint"
```

---

## Task 6: ConfigResolver custom 分支改造

**Files:**
- Modify: `lib/config/resolver.py`
- Modify: `tests/test_custom_provider_resolution.py`

- [ ] **Step 1：改测试 `tests/test_custom_provider_resolution.py`**

把现有用例里 ORM 数据 `media_type=` 替换为 `endpoint=`；`api_format=` 替换为 `discovery_format=`；新增一例「endpoint=newapi-video → video_capabilities 推 video」。
若文件已有 fail-loud 类用例，新增：

```python
@pytest.mark.asyncio
async def test_video_capabilities_endpoint_mismatch_raises(session_with_data):
    """配 endpoint=openai-chat 但被当作 video_backend 使用 → ValueError。"""
    # 预置：CustomProviderModel(endpoint="openai-chat") 且 project.video_backend="custom-1/gpt-4o"
    # 调用 resolver.video_capabilities() 应抛 ValueError, 含 "endpoint_media_type_mismatch" 或类似
    ...
```

- [ ] **Step 2：跑测试 Expected FAIL**

Run: `uv run pytest tests/test_custom_provider_resolution.py -v`

- [ ] **Step 3：改 `lib/config/resolver.py:241-313`（_resolve_video_capabilities_from_project 中 custom 分支）**

替换 custom 分支为：

```python
if is_custom_provider(provider_id):
    source = "custom"
    try:
        db_pid = parse_provider_id(provider_id)
    except ValueError as exc:
        raise ValueError(f"invalid custom provider_id: {provider_id}") from exc
    repo = CustomProviderRepository(session)
    model = await repo.get_model_by_ids(db_pid, model_id)
    if model is None:
        raise ValueError(f"custom model not found: {provider_id}/{model_id}")

    from lib.custom_provider.endpoints import endpoint_to_media_type

    derived_media = endpoint_to_media_type(model.endpoint)
    if derived_media != "video":
        raise ValueError(
            f"endpoint media_type mismatch: {provider_id}/{model_id} endpoint={model.endpoint!r} "
            f"is {derived_media}, not video"
        )
    raw_durations = model.supported_durations
    supported_durations: list[int] = []
    if raw_durations:
        try:
            parsed = json.loads(raw_durations)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid supported_durations JSON on custom model {provider_id}/{model_id}"
            ) from exc
        if isinstance(parsed, list):
            supported_durations = [int(d) for d in parsed]
```

注意：`_resolve_video_capabilities_from_project` 后续判断 `if not supported_durations:` 已存在，无需改。

`_auto_resolve_backend`（`lib/config/resolver.py:390-416`）末尾 custom 分支已经调 `repo.list_enabled_models_by_media_type(media_type)`；Task 3 已让 repo 内部按 endpoint 推算，无需再改。

- [ ] **Step 4：跑测试**

Run: `uv run pytest tests/test_custom_provider_resolution.py tests/test_config_resolver.py -v`
Expected: 全绿。

- [ ] **Step 5：commit**

```bash
git add lib/config/resolver.py tests/test_custom_provider_resolution.py
git commit -m "refactor(custom-provider): resolver derives media_type from endpoint with mismatch fail-loud"
```

---

## Task 7: generation_tasks._create_custom_backend 简化

**Files:**
- Modify: `server/services/generation_tasks.py`

- [ ] **Step 1：改 `server/services/generation_tasks.py:105-141`**

把 `_create_custom_backend(provider_name, model_id, media_type)` 改为单参 + 自取 endpoint：

```python
async def _create_custom_backend(provider_name: str, model_id: str | None, media_type: str):
    """自定义供应商的 backend 创建路径。

    media_type 仅用于回退到默认模型时分组（仍接收以兼容调用方调用语义）。
    实际派发以 model.endpoint 为准；若 endpoint 推算 media_type 与 caller 传入不符 → 视为模型不存在并 fallback。
    """
    from lib.custom_provider import parse_provider_id
    from lib.custom_provider.endpoints import endpoint_to_media_type
    from lib.custom_provider.factory import create_custom_backend
    from lib.db import async_session_factory
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    async with async_session_factory() as session:
        repo = CustomProviderRepository(session)
        db_id = parse_provider_id(provider_name)
        provider = await repo.get_provider(db_id)
        if provider is None:
            raise ValueError(f"自定义供应商 {provider_name} 不存在")

        model = None
        if model_id:
            from sqlalchemy import select

            from lib.db.models.custom_provider import CustomProviderModel

            stmt = select(CustomProviderModel).where(
                CustomProviderModel.provider_id == db_id,
                CustomProviderModel.model_id == model_id,
                CustomProviderModel.is_enabled == True,  # noqa: E712
            )
            result = await session.execute(stmt)
            candidate = result.scalar_one_or_none()
            if candidate and endpoint_to_media_type(candidate.endpoint) == media_type:
                model = candidate
            else:
                logger.warning(
                    "自定义模型 %s/%s 已不存在 / 已禁用 / 媒体类型不符（期望 %s），回退到默认模型",
                    provider_name,
                    model_id,
                    media_type,
                )
                model_id = None

        if model is None:
            default_model = await repo.get_default_model(db_id, media_type)
            if default_model is None:
                raise ValueError(f"自定义供应商 {provider_name} 没有默认 {media_type} 模型")
            model = default_model
            model_id = default_model.model_id

        return create_custom_backend(provider=provider, model_id=model_id, endpoint=model.endpoint)
```

- [ ] **Step 2：跑后端整套测试**

Run: `uv run pytest tests/server/ -v 2>&1 | tail -30`
Expected: 全绿（如果有专门的 generation_tasks 单测，其入参未变签名，应自然通过）。

- [ ] **Step 3：commit**

```bash
git add server/services/generation_tasks.py
git commit -m "refactor(custom-provider): _create_custom_backend dispatches via model.endpoint"
```

---

## Task 8: Router + Pydantic schemas 改造

**Files:**
- Modify: `server/routers/custom_providers.py`
- Modify: `tests/test_custom_providers_api.py`

- [ ] **Step 1：改 `tests/test_custom_providers_api.py`**

替换全部 fixture / payload：
- `"api_format": "openai"` → `"discovery_format": "openai"`（顶级请求体）
- `"media_type": "text"` → `"endpoint": "openai-chat"`（模型项）
- 删除测 `"api_format": "newapi"` 的用例，改测 `"discovery_format": "openai"` 等价场景
- 新增校验失败用例：

```python
@pytest.mark.asyncio
async def test_create_provider_with_unknown_endpoint_returns_422(client):
    payload = {
        "display_name": "X",
        "discovery_format": "openai",
        "base_url": "https://x",
        "api_key": "k",
        "models": [{
            "model_id": "claude-4",
            "display_name": "Claude 4",
            "endpoint": "anthropic-messages",  # 非法
            "is_default": False,
            "is_enabled": True,
        }],
    }
    resp = await client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 422
    assert "unknown_endpoint" in resp.text or "anthropic-messages" in resp.text


@pytest.mark.asyncio
async def test_create_provider_unknown_discovery_format_returns_422(client):
    payload = {
        "display_name": "X",
        "discovery_format": "newapi",  # 已被剔除
        "base_url": "https://x",
        "api_key": "k",
        "models": [],
    }
    resp = await client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_default_conflict_grouped_by_endpoint_media(client):
    """两条 endpoint 不同但推算 media_type 相同的模型不能同时 is_default。"""
    payload = {
        "display_name": "X", "discovery_format": "openai", "base_url": "https://x", "api_key": "k",
        "models": [
            {"model_id": "gpt-4o", "display_name": "a", "endpoint": "openai-chat",
             "is_default": True, "is_enabled": True},
            {"model_id": "gemini-2.5", "display_name": "b", "endpoint": "gemini-generate",
             "is_default": True, "is_enabled": True},  # 都是 text → 冲突
        ],
    }
    resp = await client.post("/api/v1/custom-providers", json=payload)
    assert resp.status_code == 422
```

- [ ] **Step 2：跑测试 Expected FAIL**

Run: `uv run pytest tests/test_custom_providers_api.py -v`

- [ ] **Step 3：改 `server/routers/custom_providers.py`**

主要改动（diff 视角）：

```python
# ModelInput
class ModelInput(BaseModel):
    model_id: str
    display_name: str
-   media_type: str  # "text" | "image" | "video"
+   endpoint: str  # ENDPOINT_REGISTRY key
    is_default: bool = False
    is_enabled: bool = True
    # ... 其余字段不变 ...

    @model_validator(mode="after")
    def _check_endpoint(self):
        from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

        if self.endpoint not in ENDPOINT_REGISTRY:
            raise ValueError(f"unknown_endpoint:{self.endpoint}")
        return self
```

```python
# CreateProviderRequest / ProviderConnectionRequest
class CreateProviderRequest(BaseModel):
    display_name: str
-   api_format: str
+   discovery_format: str
    base_url: str
    api_key: str
    models: list[ModelInput] = []

    @model_validator(mode="after")
    def _check_discovery_format(self):
        if self.discovery_format not in {"openai", "google"}:
            raise ValueError(f"unknown_discovery_format:{self.discovery_format}")
        return self


class ProviderConnectionRequest(BaseModel):
    discovery_format: str
    base_url: str
    api_key: str
```

```python
# _check_unique_defaults — 改用 endpoint 推 media_type 分组
def _check_unique_defaults(models: list[ModelInput], _t: Callable[..., str]) -> None:
    from lib.custom_provider.endpoints import endpoint_to_media_type

    defaults_by_media: dict[str, list[str]] = {}
    for m in models:
        if m.is_default:
            try:
                media = endpoint_to_media_type(m.endpoint)
            except ValueError:
                continue  # endpoint 校验在 ModelInput.validator 中做
            defaults_by_media.setdefault(media, []).append(m.model_id)
    duplicates = {mt: ids for mt, ids in defaults_by_media.items() if len(ids) > 1}
    if duplicates:
        parts = [f"{mt}({', '.join(ids)})" for mt, ids in duplicates.items()]
        raise HTTPException(
            status_code=422,
            detail=_t("default_model_conflict", conflict="; ".join(parts)),
        )
```

```python
# _check_duplicate_model_ids — 启用必须有 endpoint
def _check_duplicate_model_ids(models: list[ModelInput], _t: Callable[..., str]) -> None:
    seen: set[str] = set()
    for m in models:
        if m.is_enabled:
            if not m.model_id.strip():
                raise HTTPException(status_code=422, detail=_t("model_id_required"))
            if not m.endpoint.strip():
                raise HTTPException(status_code=422, detail=_t("endpoint_required"))
        if m.model_id in seen:
            raise HTTPException(status_code=422, detail=_t("duplicate_model_id", model_id=m.model_id))
        if m.model_id:
            seen.add(m.model_id)
```

```python
# ModelResponse / ProviderResponse 同步改名
class ModelResponse(BaseModel):
    id: int
    model_id: str
    display_name: str
-   media_type: str
+   endpoint: str
    is_default: bool
    is_enabled: bool
    # ... 其余字段不变 ...


class ProviderResponse(BaseModel):
    id: int
    display_name: str
-   api_format: str
+   discovery_format: str
    base_url: str
    api_key_masked: str
    models: list[ModelResponse]
    created_at: str | None = None
```

```python
# _model_to_response / _provider_to_response — 字段改名
def _model_to_response(m) -> ModelResponse:
    durations = json.loads(m.supported_durations) if m.supported_durations else None
    return ModelResponse(
        id=m.id,
        model_id=m.model_id,
        display_name=m.display_name,
-       media_type=m.media_type,
+       endpoint=m.endpoint,
        is_default=m.is_default,
        is_enabled=m.is_enabled,
        # 其余照旧
    )

def _provider_to_response(provider, models) -> ProviderResponse:
    return ProviderResponse(
        id=provider.id,
        display_name=provider.display_name,
-       api_format=provider.api_format,
+       discovery_format=provider.discovery_format,
        # 其余照旧
    )
```

```python
# create_provider / discover_models_endpoint / test_connection 等调用处
# 把 body.api_format → body.discovery_format
# repo.create_provider(api_format=...) → repo.create_provider(discovery_format=...)

@router.post("/discover")
async def discover_models_endpoint(body: ProviderConnectionRequest, _user: CurrentUser, _t: Translator):
    from lib.custom_provider.discovery import discover_models

    try:
        models = await discover_models(
            discovery_format=body.discovery_format,
            base_url=body.base_url or None,
            api_key=body.api_key,
        )
        return DiscoverResponse(models=models)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        err_msg = str(exc)[:200]
        logger.warning("模型发现失败: %s", err_msg)
        raise HTTPException(status_code=502, detail=_t("discovery_failed", err_msg=err_msg))
```

```python
# _run_connection_test — 删除 newapi 分支
async def _run_connection_test(
    discovery_format: str, base_url: str, api_key: str, _t: Callable[..., str]
) -> ConnectionTestResponse:
    try:
        if discovery_format == "openai":
            ...
        elif discovery_format == "google":
            ...
        else:
            return ConnectionTestResponse(
                success=False,
                message=_t("unsupported_discovery_format", discovery_format=discovery_format),
            )
        return result
    # （except 不变）
```

`test_connection_by_id` 把 `provider.api_format` 改 `provider.discovery_format`。

- [ ] **Step 4：跑测试**

Run: `uv run pytest tests/test_custom_providers_api.py -v`
Expected: 全绿。

- [ ] **Step 5：commit**

```bash
git add server/routers/custom_providers.py tests/test_custom_providers_api.py
git commit -m "refactor(custom-provider): router schemas use discovery_format + endpoint"
```

---

## Task 9: Alembic 一次性迁移 + 双向迁移测试

**Files:**
- Create: `alembic/versions/<auto>_endpoint_refactor.py`（运行 `uv run alembic revision -m "..."` 生成空文件后填写）
- Create: `tests/test_alembic_custom_provider_endpoint.py`

- [ ] **Step 1：拉取当前最新 head revision 并替换占位符**

Run: `uv run alembic heads`
记录输出（如 `c9b24204c0de (head)`）。本任务内所有出现 `<PREVIOUS_HEAD>` 的位置（迁移文件 `down_revision` + 双向迁移测试 `command.upgrade(cfg, "<PREVIOUS_HEAD>")` × 2）都用此 id 替换 — 一律字符串字面量替换。

- [ ] **Step 2：手写新 revision 文件**

文件名：`alembic/versions/0426_endpoint_refactor.py`（或用 `uv run alembic revision -m "endpoint refactor"` 生成空模板再填充）。

```python
"""rename api_format→discovery_format, media_type→endpoint

Revision ID: 0426endpointrefactor
Revises: <PREVIOUS_HEAD>
Create Date: 2026-04-26 ...
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0426endpointrefactor"
down_revision: str | Sequence[str] | None = "<PREVIOUS_HEAD>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (api_format, media_type) → endpoint
_UPGRADE_ENDPOINT_MAP = {
    ("openai", "text"): "openai-chat",
    ("openai", "image"): "openai-images",
    ("openai", "video"): "openai-video",
    ("google", "text"): "gemini-generate",
    ("google", "image"): "gemini-image",
    ("google", "video"): "newapi-video",  # 兜底；google 直连本无视频
    ("newapi", "text"): "openai-chat",
    ("newapi", "image"): "openai-images",
    ("newapi", "video"): "newapi-video",
}

# api_format → discovery_format
_UPGRADE_DISCOVERY_MAP = {
    "openai": "openai",
    "google": "google",
    "newapi": "openai",
}

# 反向：endpoint → (api_format_choice, media_type)。downgrade 用。
# 仅返回最常见的 api_format 选择；如果你部署中曾经选过 google 而 endpoint 是 newapi-video（罕见），
# downgrade 会回填为 newapi。
_DOWNGRADE_MAP = {
    "openai-chat": ("openai", "text"),
    "gemini-generate": ("google", "text"),
    "openai-images": ("openai", "image"),
    "gemini-image": ("google", "image"),
    "openai-video": ("openai", "video"),
    "newapi-video": ("newapi", "video"),
}


def upgrade() -> None:
    bind = op.get_bind()

    # ── custom_provider 列改造 ──
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("discovery_format", sa.String(length=32), nullable=True))

    # 回填 discovery_format
    rows = bind.execute(sa.text("SELECT id, api_format FROM custom_provider")).fetchall()
    for row in rows:
        new_val = _UPGRADE_DISCOVERY_MAP.get(row.api_format)
        if new_val is None:
            raise RuntimeError(
                f"custom_provider.id={row.id} api_format={row.api_format!r} 不在迁移映射中"
            )
        bind.execute(
            sa.text("UPDATE custom_provider SET discovery_format = :val WHERE id = :id"),
            {"val": new_val, "id": row.id},
        )

    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.alter_column("discovery_format", nullable=False)
        batch_op.drop_column("api_format")

    # ── custom_provider_model 列改造 ──
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("endpoint", sa.String(length=32), nullable=True))

    # 回填 endpoint —— 需要 join provider 拿原 api_format
    rows = bind.execute(
        sa.text(
            "SELECT m.id AS mid, m.media_type, p.discovery_format, "
            "       (SELECT api_format_old.api_format FROM custom_provider api_format_old WHERE api_format_old.id = m.provider_id) AS api_format "
            "FROM custom_provider_model m "
        )
    ).fetchall()
```

> **注意**：上一段 SQL 不可用 — `api_format` 已 drop。改写为：在 drop `api_format` 之前先备份成临时列，或调换顺序：先做 model 表回填，再做 provider 表 drop。下面给出正确顺序的完整版 — 替换上面 upgrade() 整个函数：

```python
def upgrade() -> None:
    bind = op.get_bind()

    # 1) provider 表：先 add 新列（先不 drop 旧列）
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("discovery_format", sa.String(length=32), nullable=True))

    rows = bind.execute(sa.text("SELECT id, api_format FROM custom_provider")).fetchall()
    for row in rows:
        new_val = _UPGRADE_DISCOVERY_MAP.get(row.api_format)
        if new_val is None:
            raise RuntimeError(f"provider id={row.id} api_format={row.api_format!r} 不在映射中")
        bind.execute(
            sa.text("UPDATE custom_provider SET discovery_format = :v WHERE id = :id"),
            {"v": new_val, "id": row.id},
        )

    # 2) model 表：add endpoint，回填（join provider 取 api_format）
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("endpoint", sa.String(length=32), nullable=True))

    rows = bind.execute(
        sa.text(
            "SELECT m.id AS mid, m.media_type AS media_type, p.api_format AS api_format "
            "FROM custom_provider_model m JOIN custom_provider p ON p.id = m.provider_id"
        )
    ).fetchall()
    for row in rows:
        ep = _UPGRADE_ENDPOINT_MAP.get((row.api_format, row.media_type))
        if ep is None:
            raise RuntimeError(
                f"model id={row.mid} (api_format={row.api_format!r}, media_type={row.media_type!r}) "
                f"不在迁移映射中"
            )
        bind.execute(
            sa.text("UPDATE custom_provider_model SET endpoint = :v WHERE id = :id"),
            {"v": ep, "id": row.mid},
        )

    # 3) drop 旧列
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.alter_column("endpoint", nullable=False)
        batch_op.drop_column("media_type")

    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.alter_column("discovery_format", nullable=False)
        batch_op.drop_column("api_format")


def downgrade() -> None:
    bind = op.get_bind()

    # 1) provider 表：add api_format，回填
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("api_format", sa.String(length=32), nullable=True))

    rows = bind.execute(sa.text("SELECT id, discovery_format FROM custom_provider")).fetchall()
    for row in rows:
        # discovery_format=openai 反向回 openai（NewAPI 信息已丢失，无法精准还原；以 openai 兜底）
        api_format_val = "google" if row.discovery_format == "google" else "openai"
        bind.execute(
            sa.text("UPDATE custom_provider SET api_format = :v WHERE id = :id"),
            {"v": api_format_val, "id": row.id},
        )

    # 2) model 表：add media_type，回填（按 endpoint 反查）
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("media_type", sa.String(length=16), nullable=True))

    rows = bind.execute(sa.text("SELECT id, endpoint FROM custom_provider_model")).fetchall()
    for row in rows:
        rev = _DOWNGRADE_MAP.get(row.endpoint)
        if rev is None:
            raise RuntimeError(f"model id={row.id} endpoint={row.endpoint!r} 不在 downgrade 映射中")
        _, media = rev
        bind.execute(
            sa.text("UPDATE custom_provider_model SET media_type = :v WHERE id = :id"),
            {"v": media, "id": row.id},
        )

    # 3) drop 新列 + alter NOT NULL
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.alter_column("media_type", nullable=False)
        batch_op.drop_column("endpoint")

    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.alter_column("api_format", nullable=False)
        batch_op.drop_column("discovery_format")
```

- [ ] **Step 3：写双向迁移测试**

`tests/test_alembic_custom_provider_endpoint.py`：

```python
"""Alembic 0426endpointrefactor 双向迁移测试。

注入 9 种历史 (api_format, media_type) 组合，upgrade 后断言 endpoint 正确，
downgrade 后断言 (api_format, media_type) 复原（discovery_format=newapi 信息丢失，
是预期 lossy）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def alembic_cfg(tmp_path):
    db_path = tmp_path / "test.db"
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg, db_path


def _seed_pre_endpoint_state(engine, combos: list[tuple[str, str]]) -> None:
    """注入历史数据：每个 (api_format, media_type) 组合写一个 provider+model。"""
    with engine.begin() as conn:
        for i, (api_fmt, media) in enumerate(combos, start=1):
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider (id, display_name, api_format, base_url, api_key, "
                    "created_at, updated_at) VALUES (:id, :n, :f, :u, :k, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": i, "n": f"P{i}", "f": api_fmt, "u": "https://x", "k": "k"},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider_model (provider_id, model_id, display_name, "
                    "media_type, is_default, is_enabled, created_at, updated_at) "
                    "VALUES (:pid, :mid, :dn, :mt, 0, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"pid": i, "mid": f"m-{i}", "dn": f"m-{i}", "mt": media},
            )


def test_upgrade_maps_all_nine_combos(alembic_cfg):
    cfg, db_path = alembic_cfg
    # 1) 升级到 endpoint refactor 的前一版
    command.upgrade(cfg, "<PREVIOUS_HEAD>")
    engine = sa.create_engine(f"sqlite:///{db_path}")

    combos = [
        ("openai", "text"), ("openai", "image"), ("openai", "video"),
        ("google", "text"), ("google", "image"), ("google", "video"),
        ("newapi", "text"), ("newapi", "image"), ("newapi", "video"),
    ]
    _seed_pre_endpoint_state(engine, combos)

    # 2) 升级到目标 revision
    command.upgrade(cfg, "0426endpointrefactor")

    expected_endpoints = [
        "openai-chat", "openai-images", "openai-video",
        "gemini-generate", "gemini-image", "newapi-video",
        "openai-chat", "openai-images", "newapi-video",
    ]
    expected_discovery = ["openai", "openai", "openai",
                          "google", "google", "google",
                          "openai", "openai", "openai"]

    with engine.connect() as conn:
        for i, ep in enumerate(expected_endpoints, start=1):
            row = conn.execute(
                sa.text("SELECT endpoint FROM custom_provider_model WHERE provider_id=:i"),
                {"i": i},
            ).fetchone()
            assert row.endpoint == ep, f"combo {combos[i-1]} → expected {ep}, got {row.endpoint}"

        for i, df in enumerate(expected_discovery, start=1):
            row = conn.execute(
                sa.text("SELECT discovery_format FROM custom_provider WHERE id=:i"), {"i": i}
            ).fetchone()
            assert row.discovery_format == df


def test_downgrade_restores_columns(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "0426endpointrefactor")
    engine = sa.create_engine(f"sqlite:///{db_path}")

    # 直接以新 schema 注入数据
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO custom_provider (id, display_name, discovery_format, base_url, api_key, "
                "created_at, updated_at) VALUES (1, 'P', 'openai', 'https://x', 'k', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO custom_provider_model (provider_id, model_id, display_name, endpoint, "
                "is_default, is_enabled, created_at, updated_at) "
                "VALUES (1, 'sora-2', 'Sora 2', 'openai-video', 0, 1, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    command.downgrade(cfg, "<PREVIOUS_HEAD>")

    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT api_format FROM custom_provider WHERE id=1")).fetchone()
        assert row.api_format == "openai"
        row = conn.execute(
            sa.text("SELECT media_type FROM custom_provider_model WHERE provider_id=1")
        ).fetchone()
        assert row.media_type == "video"
```

> **重要**：把测试与迁移文件中两处 `<PREVIOUS_HEAD>` 替换为 Step 1 看到的实际 revision id。

- [ ] **Step 4：跑迁移测试**

Run: `uv run pytest tests/test_alembic_custom_provider_endpoint.py -v`
Expected: 两个用例全绿。

跑完整 alembic upgrade 看本机 dev DB：
Run: `uv run alembic upgrade head`
Expected: 无错误（如果存在历史 custom_provider 数据，会被无损迁移）。

- [ ] **Step 5：commit**

```bash
git add alembic/versions/0426_endpoint_refactor.py tests/test_alembic_custom_provider_endpoint.py
git commit -m "feat(custom-provider): alembic migration api_format→discovery_format + media_type→endpoint"
```

---

## Task 10: 后端 i18n 错误 keys

**Files:**
- Modify: `lib/i18n/zh/errors.py`
- Modify: `lib/i18n/en/errors.py`

- [ ] **Step 1：在 zh `MESSAGES` 字典插入新 key（追加到 custom provider 错误那一段）**

```python
"unknown_endpoint": "未知 endpoint: {endpoint}",
"unknown_discovery_format": "不支持的 discovery_format: {discovery_format}",
"endpoint_required": "已启用的模型必须填写 endpoint",
"endpoint_media_type_mismatch": "模型 endpoint 与媒体类型不一致: {detail}",
"backend_creation_failed": "Backend 创建失败: {err_msg}",
"unsupported_discovery_format": "供应商 {discovery_format} 暂不支持连接测试",
```

`unsupported_format` 旧 key 保留或改为 `unsupported_discovery_format`（建议改，统一用语）。

- [ ] **Step 2：en 对应 key**

```python
"unknown_endpoint": "Unknown endpoint: {endpoint}",
"unknown_discovery_format": "Unsupported discovery_format: {discovery_format}",
"endpoint_required": "Enabled models must specify endpoint",
"endpoint_media_type_mismatch": "Endpoint media_type mismatch: {detail}",
"backend_creation_failed": "Backend creation failed: {err_msg}",
"unsupported_discovery_format": "Connection test not supported for {discovery_format}",
```

- [ ] **Step 3：跑 i18n 一致性测试**

Run: `uv run pytest tests/test_i18n_consistency.py -v`
Expected: 全绿。

- [ ] **Step 4：commit**

```bash
git add lib/i18n/zh/errors.py lib/i18n/en/errors.py
git commit -m "feat(i18n): add backend error keys for endpoint refactor"
```

---

## Task 11: 前端 TS 类型

**Files:**
- Modify: `frontend/src/types/custom-provider.ts`

- [ ] **Step 1：替换全文**

```ts
export type EndpointKey =
  | "openai-chat"
  | "gemini-generate"
  | "openai-images"
  | "gemini-image"
  | "openai-video"
  | "newapi-video";

export type MediaType = "text" | "image" | "video";

export interface CustomProviderInfo {
  id: number;
  display_name: string;
  discovery_format: "openai" | "google";
  base_url: string;
  api_key_masked: string;
  models: CustomProviderModelInfo[];
  created_at: string;
}

export interface CustomProviderModelInfo {
  id: number;
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  price_unit: string | null;
  price_input: number | null;
  price_output: number | null;
  currency: string | null;
  supported_durations: number[] | null;
  resolution: string | null;
}

export interface DiscoveredModel {
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
}

export interface CustomProviderCreateRequest {
  display_name: string;
  discovery_format: "openai" | "google";
  base_url: string;
  api_key: string;
  models: CustomProviderModelInput[];
}

export interface CustomProviderModelInput {
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  price_unit?: string;
  price_input?: number;
  price_output?: number;
  currency?: string;
  supported_durations?: number[] | null;
  resolution?: string | null;
}

export const ENDPOINT_TO_MEDIA_TYPE: Record<EndpointKey, MediaType> = {
  "openai-chat": "text",
  "gemini-generate": "text",
  "openai-images": "image",
  "gemini-image": "image",
  "openai-video": "video",
  "newapi-video": "video",
};
```

- [ ] **Step 2：跑前端 typecheck**

Run: `cd frontend && pnpm tsc --noEmit 2>&1 | tail -30`
Expected: 大量错误（CustomProviderForm / Detail / api.ts / 等使用旧字段处）— 这是预期，下几个 Task 会修。

- [ ] **Step 3：commit（部分错误，按 phased commit 处理）**

```bash
git add frontend/src/types/custom-provider.ts
git commit -m "refactor(custom-provider/types): use endpoint + discovery_format"
```

---

## Task 12: 前端 API client

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1：改 `frontend/src/api.ts:1514-1546` 区段**

```ts
static async createCustomProvider(data: CustomProviderCreateRequest): Promise<CustomProviderInfo> {
  return await this.request<CustomProviderInfo>("/custom-providers", { method: "POST", json: data });
}

static async updateCustomProvider(
  id: number,
  data: Partial<Omit<CustomProviderCreateRequest, "discovery_format" | "models">>,
): Promise<void> {
  await this.request<void>(`/custom-providers/${id}`, { method: "PATCH", json: data });
}

static async fullUpdateCustomProvider(
  id: number,
  data: { display_name: string; base_url: string; api_key?: string; models: CustomProviderModelInput[] },
): Promise<CustomProviderInfo> {
  return await this.request<CustomProviderInfo>(`/custom-providers/${id}`, { method: "PUT", json: data });
}

static async discoverModels(
  data: { discovery_format: string; base_url: string; api_key: string },
): Promise<{ models: DiscoveredModel[] }> {
  return await this.request<{ models: DiscoveredModel[] }>("/custom-providers/discover", { method: "POST", json: data });
}

static async testCustomConnection(
  data: { discovery_format: string; base_url: string; api_key: string },
): Promise<{ success: boolean; message: string }> {
  return await this.request<{ success: boolean; message: string }>(
    "/custom-providers/test", { method: "POST", json: data });
}
```

- [ ] **Step 2：跑 typecheck — 错误数应减少**

Run: `cd frontend && pnpm tsc --noEmit 2>&1 | tail -30`

- [ ] **Step 3：commit**

```bash
git add frontend/src/api.ts
git commit -m "refactor(custom-provider/api): rename request body fields"
```

---

## Task 13: 前端 CustomProviderForm 重写

**Files:**
- Modify: `frontend/src/components/pages/settings/CustomProviderForm.tsx`

- [ ] **Step 1：改 `ApiFormat` / `MEDIA_TYPE_OPTIONS` 区段**

替换 `frontend/src/components/pages/settings/CustomProviderForm.tsx:20-33`：

```ts
type DiscoveryFormat = "openai" | "google";
import type { EndpointKey, MediaType } from "@/types";
import { ENDPOINT_TO_MEDIA_TYPE } from "@/types";

const DISCOVERY_FORMAT_OPTIONS: { value: DiscoveryFormat; label: string }[] = [
  { value: "openai", label: "OpenAI 兼容" },
  { value: "google", label: "Google AI Studio" },
];

interface EndpointOption {
  value: EndpointKey;
  labelKey: string;     // 翻译 key
  mediaType: MediaType;
}

const ENDPOINT_OPTIONS: EndpointOption[] = [
  { value: "openai-chat", labelKey: "endpoint_openai_chat_display", mediaType: "text" },
  { value: "gemini-generate", labelKey: "endpoint_gemini_generate_display", mediaType: "text" },
  { value: "openai-images", labelKey: "endpoint_openai_images_display", mediaType: "image" },
  { value: "gemini-image", labelKey: "endpoint_gemini_image_display", mediaType: "image" },
  { value: "openai-video", labelKey: "endpoint_openai_video_display", mediaType: "video" },
  { value: "newapi-video", labelKey: "endpoint_newapi_video_display", mediaType: "video" },
];
```

- [ ] **Step 2：改 `ModelRow` 与映射函数**

`ModelRow` 字段 `media_type: MediaType` → `endpoint: EndpointKey`。

```ts
interface ModelRow {
  key: string;
  model_id: string;
  display_name: string;
  endpoint: EndpointKey;
  is_default: boolean;
  is_enabled: boolean;
  price_unit: string;
  price_input: string;
  price_output: string;
  currency: string;
  resolution: string;
}

function newModelRow(partial?: Partial<ModelRow>): ModelRow {
  return {
    key: uid(),
    model_id: "",
    display_name: "",
    endpoint: "openai-chat",
    is_default: false,
    is_enabled: true,
    price_unit: "",
    price_input: "",
    price_output: "",
    currency: "USD",
    resolution: "",
    ...partial,
  };
}

function discoveredToRow(m: DiscoveredModel): ModelRow {
  return newModelRow({
    model_id: m.model_id,
    display_name: m.display_name,
    endpoint: m.endpoint,
    is_default: m.is_default,
    is_enabled: m.is_enabled,
  });
}

function existingToRow(m: CustomProviderInfo["models"][number]): ModelRow {
  return newModelRow({
    model_id: m.model_id,
    display_name: m.display_name,
    endpoint: m.endpoint,
    is_default: m.is_default,
    is_enabled: m.is_enabled,
    price_unit: m.price_unit ?? "",
    price_input: m.price_input != null ? String(m.price_input) : "",
    price_output: m.price_output != null ? String(m.price_output) : "",
    currency: m.currency ?? "",
    resolution: m.resolution ?? "",
  });
}

function rowToInput(r: ModelRow): CustomProviderModelInput {
  return {
    model_id: r.model_id,
    display_name: r.display_name || r.model_id,
    endpoint: r.endpoint,
    is_default: r.is_default,
    is_enabled: r.is_enabled,
    ...(r.price_unit ? { price_unit: r.price_unit } : {}),
    ...(r.price_input ? { price_input: parseFloat(r.price_input) } : {}),
    ...(r.price_output ? { price_output: parseFloat(r.price_output) } : {}),
    ...(r.currency ? { currency: r.currency } : {}),
    ...(r.resolution ? { resolution: r.resolution } : { resolution: null }),
  };
}
```

- [ ] **Step 3：改 `priceLabel` 与 form state**

```ts
function priceLabel(endpoint: EndpointKey, t: (key: string) => string): { input: string; output: string } {
  const media = ENDPOINT_TO_MEDIA_TYPE[endpoint];
  if (media === "video") return { input: t("price_per_second"), output: "" };
  if (media === "image") return { input: t("price_per_image"), output: "" };
  return { input: t("price_per_m_input"), output: t("price_per_m_output") };
}
```

```ts
// CustomProviderForm 主体内：
const [discoveryFormat, setDiscoveryFormat] =
  useState<DiscoveryFormat>(existing?.discovery_format ?? "openai");
// （删除 apiFormat / setApiFormat）
```

handleDiscover / handleTest / handleSave 内 `apiFormat` → `discoveryFormat`；
`createCustomProvider` payload 同名传入。

- [ ] **Step 4：改顶部表单 JSX —— `apiFormat` 选择器弱化为小字行**

把 `frontend/src/components/pages/settings/CustomProviderForm.tsx:337-355` 段：

```tsx
{/* API Format */}
<div>
  <label htmlFor="cp-format" className="mb-1.5 block text-sm text-gray-400">
    {t("api_format_label")}
  </label>
  <select id="cp-format" value={apiFormat} onChange={(e) => setApiFormat(e.target.value as ApiFormat)}
          disabled={isEdit} className={selectCls}>
    {API_FORMAT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
  </select>
</div>
```

替换为（移到 base_url + api_key 之下，作小字行）：

```tsx
{/* Discovery format — 弱化展示 */}
<div className="text-xs text-gray-500">
  <label htmlFor="cp-discovery" className="mr-2">
    {t("discovery_format_label")}：
  </label>
  <select
    id="cp-discovery"
    value={discoveryFormat}
    onChange={(e) => setDiscoveryFormat(e.target.value as DiscoveryFormat)}
    disabled={isEdit}
    className="rounded border border-gray-700 bg-gray-900 px-2 py-0.5 text-xs text-gray-300"
  >
    {DISCOVERY_FORMAT_OPTIONS.map((o) => (
      <option key={o.value} value={o.value}>
        {o.label}
      </option>
    ))}
  </select>
  <span className="ml-2 text-gray-600">{t("discovery_format_help")}</span>
</div>
```

- [ ] **Step 5：改模型行 JSX —— 把 media_type select 替换为 endpoint select（按媒体类型 optgroup 分组）**

替换 `frontend/src/components/pages/settings/CustomProviderForm.tsx:486-498` 中的 `<select>` for `media_type`：

```tsx
{/* Endpoint */}
<select
  value={m.endpoint}
  onChange={(e) => updateModel(m.key, { endpoint: e.target.value as EndpointKey, is_default: false })}
  aria-label={t("endpoint_label")}
  className={selectCls}
>
  <optgroup label={t("endpoint_text_group")}>
    {ENDPOINT_OPTIONS.filter((o) => o.mediaType === "text").map((o) => (
      <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
    ))}
  </optgroup>
  <optgroup label={t("endpoint_image_group")}>
    {ENDPOINT_OPTIONS.filter((o) => o.mediaType === "image").map((o) => (
      <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
    ))}
  </optgroup>
  <optgroup label={t("endpoint_video_group")}>
    {ENDPOINT_OPTIONS.filter((o) => o.mediaType === "video").map((o) => (
      <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
    ))}
  </optgroup>
</select>
```

替换 default 切换 `toggleDefault(m.key, m.media_type)` 为按推算 media_type：

```ts
const toggleDefault = (key: string, endpoint: EndpointKey) => {
  const targetMedia = ENDPOINT_TO_MEDIA_TYPE[endpoint];
  setModels((prev) =>
    prev.map((m) => {
      if (ENDPOINT_TO_MEDIA_TYPE[m.endpoint] !== targetMedia) return m;
      return { ...m, is_default: m.key === key ? !m.is_default : false };
    }),
  );
};
```

调用处 `toggleDefault(m.key, m.media_type)` → `toggleDefault(m.key, m.endpoint)`。

`priceLabel(m.media_type, t)` → `priceLabel(m.endpoint, t)`。
Resolution 行的判断 `m.media_type === "image" || m.media_type === "video"` →
`ENDPOINT_TO_MEDIA_TYPE[m.endpoint] !== "text"`。

ResolutionPicker 的 options：`m.media_type === "image"` → `ENDPOINT_TO_MEDIA_TYPE[m.endpoint] === "image"`。

- [ ] **Step 6：改 `urlPreview` —— 现在依赖 discoveryFormat，且 google 路径不变；newapi 路径删除**

```ts
const urlPreview = (() => {
  const trimmed = baseUrl.trim().replace(/\/+$/, "");
  if (!trimmed) return null;
  if (discoveryFormat === "openai") {
    const base = trimmed.match(/\/v\d+$/) ? trimmed : `${trimmed}/v1`;
    return `${base}/models`;
  }
  // google
  const base = trimmed.replace(/\/v\d+\w*$/, "");
  return `${base}/v1beta/models`;
})();
```

- [ ] **Step 7：抽出 3 个纯函数到独立文件，便于单测**

新建 `frontend/src/components/pages/settings/customProviderHelpers.ts`：

```ts
import { ENDPOINT_TO_MEDIA_TYPE, type EndpointKey } from "@/types";

export type DiscoveryFormat = "openai" | "google";
export type ModelLike = { key: string; endpoint: EndpointKey; is_default: boolean };

/** 价格行标签（按 endpoint 推算 media_type）。 */
export function priceLabel(
  endpoint: EndpointKey,
  t: (key: string) => string,
): { input: string; output: string } {
  const media = ENDPOINT_TO_MEDIA_TYPE[endpoint];
  if (media === "video") return { input: t("price_per_second"), output: "" };
  if (media === "image") return { input: t("price_per_image"), output: "" };
  return { input: t("price_per_m_input"), output: t("price_per_m_output") };
}

/** /models URL 预览。 */
export function urlPreviewFor(format: DiscoveryFormat, rawBaseUrl: string): string | null {
  const trimmed = rawBaseUrl.trim().replace(/\/+$/, "");
  if (!trimmed) return null;
  if (format === "openai") {
    const base = trimmed.match(/\/v\d+$/) ? trimmed : `${trimmed}/v1`;
    return `${base}/models`;
  }
  const base = trimmed.replace(/\/v\d+\w*$/, "");
  return `${base}/v1beta/models`;
}

/** 切 default：仅同 media_type 内互斥；本行 toggle。 */
export function toggleDefaultReducer<T extends ModelLike>(rows: T[], targetKey: string): T[] {
  const target = rows.find((r) => r.key === targetKey);
  if (!target) return rows;
  const targetMedia = ENDPOINT_TO_MEDIA_TYPE[target.endpoint];
  return rows.map((r) => {
    if (ENDPOINT_TO_MEDIA_TYPE[r.endpoint] !== targetMedia) return r;
    if (r.key === targetKey) return { ...r, is_default: !r.is_default };
    return { ...r, is_default: false };
  });
}
```

`CustomProviderForm.tsx` 内部把原 `priceLabel` / `urlPreview` / `toggleDefault` 实现替换为这 3 个 helper 的调用。

- [ ] **Step 8：写 vitest** — `frontend/src/components/pages/settings/customProviderHelpers.test.ts`：

```ts
import { describe, expect, it } from "vitest";
import {
  priceLabel,
  urlPreviewFor,
  toggleDefaultReducer,
} from "./customProviderHelpers";

const id = (k: string) => k;

describe("priceLabel", () => {
  it("video endpoint → per-second label", () => {
    expect(priceLabel("newapi-video", id).input).toBe("price_per_second");
    expect(priceLabel("openai-video", id).output).toBe("");
  });
  it("image endpoint → per-image label", () => {
    expect(priceLabel("openai-images", id).input).toBe("price_per_image");
    expect(priceLabel("gemini-image", id).output).toBe("");
  });
  it("text endpoint → per-M-token labels", () => {
    expect(priceLabel("openai-chat", id).input).toBe("price_per_m_input");
    expect(priceLabel("gemini-generate", id).output).toBe("price_per_m_output");
  });
});

describe("urlPreviewFor", () => {
  it("openai appends /v1 when missing", () => {
    expect(urlPreviewFor("openai", "https://api.example.com")).toBe(
      "https://api.example.com/v1/models",
    );
  });
  it("openai preserves /v1", () => {
    expect(urlPreviewFor("openai", "https://api.example.com/v1")).toBe(
      "https://api.example.com/v1/models",
    );
  });
  it("openai strips trailing slash and appends /v1", () => {
    expect(urlPreviewFor("openai", "https://api.example.com/")).toBe(
      "https://api.example.com/v1/models",
    );
  });
  it("google uses /v1beta/models", () => {
    expect(urlPreviewFor("google", "https://generativelanguage.googleapis.com")).toBe(
      "https://generativelanguage.googleapis.com/v1beta/models",
    );
  });
  it("google strips user-supplied version path", () => {
    expect(urlPreviewFor("google", "https://generativelanguage.googleapis.com/v1beta")).toBe(
      "https://generativelanguage.googleapis.com/v1beta/models",
    );
  });
  it("empty base_url returns null", () => {
    expect(urlPreviewFor("openai", "")).toBeNull();
    expect(urlPreviewFor("google", "  ")).toBeNull();
  });
});

describe("toggleDefaultReducer", () => {
  it("toggles target row and clears siblings within same media_type", () => {
    const rows = [
      { key: "a", endpoint: "openai-chat" as const, is_default: true },
      { key: "b", endpoint: "gemini-generate" as const, is_default: false },
      { key: "c", endpoint: "openai-images" as const, is_default: true },
    ];
    const result = toggleDefaultReducer(rows, "b");
    // b 被点亮；a 同为 text 应被清掉；c 是 image 不受影响
    expect(result.find((r) => r.key === "a")?.is_default).toBe(false);
    expect(result.find((r) => r.key === "b")?.is_default).toBe(true);
    expect(result.find((r) => r.key === "c")?.is_default).toBe(true);
  });

  it("toggling already-default row turns it off", () => {
    const rows = [{ key: "a", endpoint: "openai-chat" as const, is_default: true }];
    expect(toggleDefaultReducer(rows, "a")[0].is_default).toBe(false);
  });
});
```

- [ ] **Step 9：跑前端 typecheck + 单测**

Run: `cd frontend && pnpm check`
Expected: typecheck 全绿；3 个 describe 共 ~12 个 it 全部 PASS。

- [ ] **Step 10：commit**

```bash
git add frontend/src/components/pages/settings/CustomProviderForm.tsx \
        frontend/src/components/pages/settings/customProviderHelpers.ts \
        frontend/src/components/pages/settings/customProviderHelpers.test.ts
git commit -m "feat(custom-provider/ui): endpoint dropdown grouped by media type, discovery_format de-emphasized"
```

---

## Task 14: 前端 CustomProviderDetail 显示适配

**Files:**
- Modify: `frontend/src/components/pages/settings/CustomProviderDetail.tsx`

- [ ] **Step 1：阅读现状**

Run: `wc -l frontend/src/components/pages/settings/CustomProviderDetail.tsx`
（约 282 行）。重点：第 138/147/183 行，引用 `provider.api_format` / `m.media_type`。

- [ ] **Step 2：改字段**

把 `provider.api_format === "openai" ? "OpenAI" : "Google"` 改为 `provider.discovery_format === "openai" ? "OpenAI" : "Google"`；
`t("api_format_label")` 改为 `t("discovery_format_label")`；
模型行 `m.media_type` 推算成 `ENDPOINT_TO_MEDIA_TYPE[m.endpoint]`，用 `MEDIA_LABELS` map 翻译；额外可在小字位置显示 endpoint 展示名（用 ENDPOINT_OPTIONS 找 labelKey）。

最小改动版（只改字段名，UI 视觉保持）：

```tsx
// 顶部
{provider.discovery_format === "openai" ? "OpenAI" : "Google"} &middot; {provider.base_url}

// 详情行
<span className="text-gray-500">{t("discovery_format_label")}</span>
<span>{provider.discovery_format === "openai" ? "OpenAI" : "Google"}</span>

// 模型行 media_type 显示
{(() => {
  const media = ENDPOINT_TO_MEDIA_TYPE[m.endpoint];
  return MEDIA_LABELS[media] ? t(MEDIA_LABELS[media]) : media;
})()}
```

- [ ] **Step 3：跑 typecheck**

Run: `cd frontend && pnpm tsc --noEmit 2>&1 | tail -20`
Expected: 无错误。

- [ ] **Step 4：commit**

```bash
git add frontend/src/components/pages/settings/CustomProviderDetail.tsx
git commit -m "refactor(custom-provider/detail): show discovery_format + endpoint-derived media"
```

---

## Task 15: 前端 i18n dashboard keys

**Files:**
- Modify: `frontend/src/i18n/zh/dashboard.ts`
- Modify: `frontend/src/i18n/en/dashboard.ts`

- [ ] **Step 1：在 zh dashboard 里追加（删除旧 `api_format_label` 行）**

```ts
'discovery_format_label': '模型发现协议',
'discovery_format_help': '仅用于"发现模型"和"连通测试"，不影响调用',
'endpoint_label': '调用端点',
'endpoint_text_group': '📝 文本',
'endpoint_image_group': '🖼 图片',
'endpoint_video_group': '🎬 视频',
'endpoint_openai_chat_display': 'OpenAI Chat Completions',
'endpoint_gemini_generate_display': 'Google Gemini',
'endpoint_openai_images_display': 'OpenAI Images API',
'endpoint_gemini_image_display': 'Google Gemini Image',
'endpoint_openai_video_display': 'OpenAI Video (Sora)',
'endpoint_newapi_video_display': 'NewAPI Unified Video',
```

删除：
```ts
- 'api_format_label': 'API 格式',
```

`media_type_text` / `media_type_image` / `media_type_video` 保留（CustomProviderDetail 还在用作图标 label 映射）。

- [ ] **Step 2：en dashboard 同步**

```ts
'discovery_format_label': 'Model Discovery Protocol',
'discovery_format_help': 'Only used for model discovery and connection testing; does not affect runtime calls',
'endpoint_label': 'Call Endpoint',
'endpoint_text_group': '📝 Text',
'endpoint_image_group': '🖼 Image',
'endpoint_video_group': '🎬 Video',
'endpoint_openai_chat_display': 'OpenAI Chat Completions',
'endpoint_gemini_generate_display': 'Google Gemini',
'endpoint_openai_images_display': 'OpenAI Images API',
'endpoint_gemini_image_display': 'Google Gemini Image',
'endpoint_openai_video_display': 'OpenAI Video (Sora)',
'endpoint_newapi_video_display': 'NewAPI Unified Video',
```

删除：
```ts
- 'api_format_label': 'API Format',
```

- [ ] **Step 3：跑 i18n 一致性测试**

Run: `uv run pytest tests/test_i18n_consistency.py -v`
Expected: 全绿。

Run: `cd frontend && pnpm check`
Expected: 全绿。

- [ ] **Step 4：commit**

```bash
git add frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "feat(custom-provider/i18n): endpoint + discovery_format display strings (zh/en)"
```

---

## Task 16: 集成验证 + 清理

**Files:**
- 全仓搜索旧标识符

- [ ] **Step 1：grep 残留 `api_format` / 旧 `media_type` 直接引用**

Run:
```bash
grep -rn "api_format" --include="*.py" --include="*.ts" --include="*.tsx" lib server frontend tests \
  | grep -v "test_custom_provider_factory.py.bak\|alembic/versions/0426" \
  | grep -v "^Binary"
```

Expected: 无残留（除 alembic 迁移文件本身和它的测试断言）。

```bash
grep -rn '"media_type"' --include="*.py" lib server tests \
  | grep -v alembic
```

Expected: 仅出现在 endpoint_to_media_type 调用、ENDPOINT_REGISTRY 初始化、PROVIDER_REGISTRY（内置供应商）等合理位置。

- [ ] **Step 2：完整测试 + ruff**

Run:
```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest --cov=lib --cov=server --cov-report=term-missing 2>&1 | tail -50
```

Expected: ruff 无 issue；pytest 全绿；覆盖率 ≥ 80%（未达标的话补单测；本次新增逻辑都有对应单测）。

- [ ] **Step 3：前端验证**

Run:
```bash
cd frontend && pnpm build
```

Expected: typecheck + build 全绿。

- [ ] **Step 4：手动冒烟**

```bash
uv run alembic upgrade head     # 已在 Task 9 跑过，再次确认幂等
uv run uvicorn server.app:app --reload --port 1241
# 浏览器：登录 → /settings/providers → 「自定义供应商」
#   1) 添加新 provider，填中转站 base_url + key
#   2) 点击「发现模型」，验证返回项 endpoint 字段（gpt-* → openai-chat，kling-* → newapi-video）
#   3) 编辑模型行 endpoint 下拉，验证按媒体类型分组
#   4) 测试连接 → 成功
#   5) 保存后回列表页，确认显示 discovery_format 值
#   6) 项目设置选自定义模型 → 生成视频 → 确认无 400/500
```

每条手动核验通过后打 ✓。

- [ ] **Step 5：最终 commit（清理 + 文档对账）**

如有零碎残留：

```bash
git add -A
git commit -m "chore(custom-provider): final cleanup after endpoint refactor"
```

如无残留则跳过此步。

---

## 自审 / 验证矩阵

实施完毕后跑：

| 命令 | 预期 |
|---|---|
| `uv run pytest` | 全绿（含 alembic 双向迁移、6 endpoint factory、router 校验） |
| `uv run ruff check . && uv run ruff format --check .` | 无 issue |
| `cd frontend && pnpm check` | typecheck + vitest 全绿 |
| `cd frontend && pnpm build` | 通过 |
| `uv run alembic upgrade head` | 幂等通过 |
| `grep -rn "api_format" lib server frontend` | 仅 alembic 迁移内引用 |
