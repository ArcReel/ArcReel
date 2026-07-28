# 视频/图片分辨率参数重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每个预置/自定义模型能在 (provider, model) 粒度声明/配置分辨率；运行时按 `project.model_settings → project.video_model_settings(legacy) → custom_model.resolution → None(不传)` 顺序解析；各 backend 收到 None 时不传给 SDK。

**Architecture:** (1) `ModelInfo` 新增 `resolutions: list[str]` 仅供前端下拉；(2) `CustomProviderModel` 新增 `resolution: str | None` 列；(3) 新建 `server/services/resolution_resolver.py`；(4) 各后端 `ImageGenerationRequest.image_size` / `VideoGenerationRequest.resolution` 改为 `str | None`，backend 内按需跳过 SDK 参数；(5) 前端新建 `ResolutionPicker` 组件，接入 Wizard Step2 / ProjectSettingsPage / CustomProviderForm 三处。

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy Async + Alembic (batch mode，SQLite 兼容) / React 19 + TypeScript + Tailwind + wouter / pytest (asyncio_mode=auto) + Vitest

**分辨率标准 token:**
- 图片: `["512px", "1K", "2K", "4K"]`
- 视频: `["480p", "720p", "1080p", "4K"]`

**File Structure:**

*Created:*
- `server/services/resolution_resolver.py` — 解析函数
- `tests/server/services/test_resolution_resolver.py` — 解析函数单测
- `alembic/versions/<hash>_add_resolution_to_custom_provider_model.py` — DB 迁移
- `frontend/src/components/shared/ResolutionPicker.tsx` — UI 组件
- `frontend/src/components/shared/ResolutionPicker.test.tsx` — UI 组件单测

*Modified:*
- `lib/config/registry.py` — ModelInfo 新字段 + 全部预置模型补 resolutions
- `lib/image_backends/base.py` · `lib/image_backends/{gemini,openai,grok}.py` — Optional image_size
- `lib/video_backends/base.py` · `lib/video_backends/{gemini,ark,grok,openai}.py` — Optional resolution
- `lib/media_generator.py` — image_size/resolution 改 Optional
- `lib/db/models/custom_provider.py` — 新增 resolution 列
- `lib/project_manager.py` — 保存时迁移 legacy video_model_settings.resolution
- `server/services/generation_tasks.py` — 删除 DEFAULT_VIDEO_RESOLUTION，接入 resolver，移除 image_size 硬编码
- `server/services/reference_video_tasks.py` — 同 generation_tasks
- `server/routers/providers.py` — ModelInfoResponse 加 resolutions
- `server/routers/custom_providers.py` — DTO 加 resolution
- `server/routers/projects.py` — CreateProjectRequest/UpdateProjectRequest 加 model_settings
- `frontend/src/types/{provider,project,custom-provider}.ts` — 前端类型
- `frontend/src/api.ts` — 请求体 model_settings
- `frontend/src/components/pages/create-project/WizardStep2Models.tsx` — 接入 ResolutionPicker
- `frontend/src/components/shared/ModelConfigSection.tsx` — 新增分辨率行（或配套）
- `frontend/src/components/pages/ProjectSettingsPage.tsx` — 接入 ResolutionPicker
- `frontend/src/components/pages/settings/CustomProviderForm.tsx` — 模型行加分辨率 combobox
- `frontend/src/i18n/{zh,en}/dashboard.ts` — 新增 key

---

## Phase 1 — 后端基础（数据模型 + 解析器 + 基类）

### Task 1: Registry — ModelInfo 新增 resolutions 字段并填充所有预置模型

**Files:**
- Modify: `lib/config/registry.py`
- Test: `tests/lib/config/test_registry_resolutions.py`（新建）

- [ ] **Step 1: Write failing test `tests/lib/config/test_registry_resolutions.py`**

```python
"""测试 ModelInfo.resolutions 字段与预置模型填充。"""

from lib.config.registry import PROVIDER_REGISTRY, ModelInfo


def test_model_info_has_resolutions_default_empty_list():
    info = ModelInfo(display_name="X", media_type="text", capabilities=[])
    assert info.resolutions == []


def test_all_image_video_models_have_resolutions_populated():
    missing: list[str] = []
    for pid, meta in PROVIDER_REGISTRY.items():
        for mid, minfo in meta.models.items():
            if minfo.media_type in ("image", "video"):
                if not minfo.resolutions and not (pid == "ark" and mid.startswith("doubao-seedream")):
                    missing.append(f"{pid}/{mid}")
    assert missing == [], f"以下 image/video 模型缺少 resolutions: {missing}"


def test_text_models_have_empty_resolutions():
    for pid, meta in PROVIDER_REGISTRY.items():
        for mid, minfo in meta.models.items():
            if minfo.media_type == "text":
                assert minfo.resolutions == [], f"{pid}/{mid}: text 模型不应声明 resolutions"


def test_ark_seedream_image_resolutions_empty():
    """Ark Seedream 当前不传分辨率，留空 → UI 不展示下拉。"""
    ark = PROVIDER_REGISTRY["ark"]
    for mid, minfo in ark.models.items():
        if minfo.media_type == "image":
            assert minfo.resolutions == [], f"{mid}: Ark Seedream 应留空"
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/config/test_registry_resolutions.py -v
```

Expected: FAIL（`ModelInfo.__init__()` 不识别 `resolutions` 参数，或填充测试失败）。

- [ ] **Step 3: Add `resolutions` field to ModelInfo**

Modify `lib/config/registry.py` — 在 `ModelInfo` dataclass 里追加字段：

```python
@dataclass(frozen=True)
class ModelInfo:
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)
    duration_resolution_constraints: dict[str, list[int]] = field(default_factory=dict)
    resolutions: list[str] = field(default_factory=list)
```

- [ ] **Step 4: 填充预置模型的 resolutions**

按各模型文档所列官方支持填写。参考值：

| Provider / model | resolutions |
|---|---|
| `gemini-aistudio` 所有 image 模型 | `["1K", "2K", "4K"]` |
| `gemini-aistudio` Veo 3.1 系列 | `["720p", "1080p"]` |
| `gemini-vertex` 所有 image 模型 | `["1K", "2K", "4K"]` |
| `gemini-vertex` Veo 3.1 系列 | `["720p", "1080p"]` |
| `ark` 所有 Seedream image | `[]` (不传，UI 不展示下拉) |
| `ark` Seedance 视频全部型号 | `["480p", "720p", "1080p"]` |
| `grok` 所有 image | `["1K", "2K"]` |
| `grok` video | `["480p", "720p"]` |
| `openai` gpt-image-* | `["512px", "1K", "2K"]` |
| `openai` sora-2 / sora-2-pro | `["720p", "1080p"]` |

在 `lib/config/registry.py` 的每个预置 image/video `ModelInfo(...)` 调用上追加 `resolutions=[...]`。

- [ ] **Step 5: Run tests, expect PASS**

```bash
uv run python -m pytest tests/lib/config/test_registry_resolutions.py -v
```

Expected: 全部 PASS。

- [ ] **Step 6: Ruff + commit**

```bash
uv run ruff check lib/config/registry.py tests/lib/config/test_registry_resolutions.py
uv run ruff format lib/config/registry.py tests/lib/config/test_registry_resolutions.py
git add lib/config/registry.py tests/lib/config/test_registry_resolutions.py
git commit -m "feat(registry): ModelInfo 新增 resolutions 字段并填充预置模型"
```

---

### Task 2: CustomProviderModel — 新增 resolution 列（ORM + Alembic 迁移）

**Files:**
- Modify: `lib/db/models/custom_provider.py`
- Create: `alembic/versions/<hash>_add_resolution_to_custom_provider_model.py`
- Test: `tests/lib/db/test_custom_provider_resolution.py`（新建）

- [ ] **Step 1: Write failing test `tests/lib/db/test_custom_provider_resolution.py`**

```python
"""测试 CustomProviderModel.resolution 字段。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel


@pytest.mark.asyncio
async def test_resolution_column_accepts_none_and_string(db_session: AsyncSession):
    provider = CustomProvider(
        display_name="X",
        api_format="openai",
        base_url="https://api.x.ai",
        api_key="k",
    )
    db_session.add(provider)
    await db_session.flush()

    m_without = CustomProviderModel(
        provider_id=provider.id,
        model_id="m1",
        display_name="M1",
        media_type="image",
        is_default=False,
        is_enabled=True,
        resolution=None,
    )
    m_with = CustomProviderModel(
        provider_id=provider.id,
        model_id="m2",
        display_name="M2",
        media_type="video",
        is_default=False,
        is_enabled=True,
        resolution="1080p",
    )
    db_session.add_all([m_without, m_with])
    await db_session.flush()

    assert m_without.resolution is None
    assert m_with.resolution == "1080p"
```

（`tests/conftest.py` 已提供 `db_session` fixture；若无，按现有 `tests/lib/db/` 下其他测试的 fixture 风格补齐）

- [ ] **Step 2: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/db/test_custom_provider_resolution.py -v
```

Expected: FAIL — `CustomProviderModel.resolution` 不存在。

- [ ] **Step 3: Add resolution column to ORM model**

Modify `lib/db/models/custom_provider.py` — 在 `CustomProviderModel` 类底部（现有 `supported_durations` 之后）追加：

```python
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

- [ ] **Step 4: Create Alembic migration**

```bash
uv run alembic revision --autogenerate -m "add resolution to custom_provider_model"
```

编辑生成的文件，确保只包含本次变更（autogenerate 可能夹带无关差异，需要手动清理到只保留 add_column）：

```python
"""add resolution to custom_provider_model

Revision ID: <hash>
Revises: d67efd76058f
Create Date: ...
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

revision: str = "<hash>"
down_revision: str | Sequence[str] | None = "d67efd76058f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("resolution", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.drop_column("resolution")
```

- [ ] **Step 5: Apply migration & run test**

```bash
uv run alembic upgrade head
uv run python -m pytest tests/lib/db/test_custom_provider_resolution.py -v
```

Expected: upgrade 成功 + 测试 PASS。

- [ ] **Step 6: Ruff + commit**

```bash
uv run ruff check lib/db/models/custom_provider.py alembic/versions/ tests/lib/db/test_custom_provider_resolution.py
uv run ruff format lib/db/models/custom_provider.py alembic/versions/ tests/lib/db/test_custom_provider_resolution.py
git add lib/db/models/custom_provider.py alembic/versions/*_add_resolution_to_custom_provider_model.py tests/lib/db/test_custom_provider_resolution.py
git commit -m "feat(db): CustomProviderModel 新增 resolution 列 + Alembic 迁移"
```

---

### Task 3: Resolution resolver — 独立模块 + 完整单测

**Files:**
- Create: `server/services/resolution_resolver.py`
- Test: `tests/server/services/test_resolution_resolver.py`（新建）

- [ ] **Step 1: Write failing tests**

```python
"""测试 resolve_resolution 按 project → legacy → custom_default → None 顺序解析。"""

from server.services.resolution_resolver import resolve_resolution


def test_returns_none_when_nothing_configured():
    assert resolve_resolution({}, "gemini-aistudio", "veo-3.1-lite-generate-preview") is None


def test_returns_custom_default_when_only_custom():
    assert (
        resolve_resolution({}, "custom-1", "my-model", custom_default="720p")
        == "720p"
    )


def test_returns_legacy_when_only_legacy():
    project = {"video_model_settings": {"veo-3.1": {"resolution": "1080p"}}}
    assert resolve_resolution(project, "gemini-aistudio", "veo-3.1") == "1080p"


def test_project_model_settings_overrides_legacy():
    project = {
        "model_settings": {"gemini-aistudio/veo-3.1": {"resolution": "720p"}},
        "video_model_settings": {"veo-3.1": {"resolution": "1080p"}},
    }
    assert resolve_resolution(project, "gemini-aistudio", "veo-3.1") == "720p"


def test_project_override_wins_over_custom_default():
    project = {"model_settings": {"custom-1/m": {"resolution": "2K"}}}
    assert (
        resolve_resolution(project, "custom-1", "m", custom_default="1K")
        == "2K"
    )


def test_legacy_wins_over_custom_default_when_no_project_model_settings():
    project = {"video_model_settings": {"m": {"resolution": "1080p"}}}
    assert (
        resolve_resolution(project, "custom-1", "m", custom_default="720p")
        == "1080p"
    )


def test_empty_string_project_override_treated_as_unset():
    """空字符串视为"未配置"，继续向下解析。"""
    project = {"model_settings": {"p/m": {"resolution": ""}}}
    assert resolve_resolution(project, "p", "m", custom_default="1K") == "1K"


def test_composite_key_format_uses_slash():
    """key 严格为 '<provider>/<model>'。"""
    project = {"model_settings": {"a/b": {"resolution": "4K"}}}
    assert resolve_resolution(project, "a", "b") == "4K"
    assert resolve_resolution(project, "a-b", "") is None  # 不应误匹配
```

- [ ] **Step 2: Run tests, expect FAIL (module not found)**

```bash
uv run python -m pytest tests/server/services/test_resolution_resolver.py -v
```

Expected: ImportError。

- [ ] **Step 3: Implement `server/services/resolution_resolver.py`**

```python
"""按 project > legacy > custom_default > None 解析每次生成调用的分辨率。

设计：见 docs/superpowers/specs/2026-04-23-resolution-param-refactor-design.md §2
"""

from __future__ import annotations


def resolve_resolution(
    project: dict,
    provider_id: str,
    model_id: str,
    *,
    custom_default: str | None = None,
) -> str | None:
    """按以下顺序返回第一个非空值，否则 None（表示调用时不传）：

    1. project.model_settings["<provider_id>/<model_id>"].resolution
    2. project.video_model_settings[model_id].resolution  (legacy read)
    3. custom_default (仅自定义供应商传入)
    4. None
    """
    key = f"{provider_id}/{model_id}"
    model_settings = project.get("model_settings") or {}
    entry = model_settings.get(key) or {}
    override = entry.get("resolution")
    if override:
        return override

    legacy_root = project.get("video_model_settings") or {}
    legacy_entry = legacy_root.get(model_id) or {}
    legacy = legacy_entry.get("resolution")
    if legacy:
        return legacy

    if custom_default:
        return custom_default

    return None
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
uv run python -m pytest tests/server/services/test_resolution_resolver.py -v
```

Expected: 全部 PASS。

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check server/services/resolution_resolver.py tests/server/services/test_resolution_resolver.py
uv run ruff format server/services/resolution_resolver.py tests/server/services/test_resolution_resolver.py
git add server/services/resolution_resolver.py tests/server/services/test_resolution_resolver.py
git commit -m "feat(service): 新增 resolution_resolver 按项目/legacy/custom_default/None 顺序解析"
```

---

### Task 4: Request dataclass — image_size / resolution 改 Optional

**Files:**
- Modify: `lib/image_backends/base.py`
- Modify: `lib/video_backends/base.py`

- [ ] **Step 1: Modify `lib/image_backends/base.py` — ImageGenerationRequest**

把 `image_size: str = "1K"` 改为 `image_size: str | None = None`：

```python
@dataclass
class ImageGenerationRequest:
    prompt: str
    output_path: Path
    reference_images: list[ReferenceImage] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    image_size: str | None = None
    project_name: str | None = None
    seed: int | None = None
```

- [ ] **Step 2: Modify `lib/video_backends/base.py` — VideoGenerationRequest**

把 `resolution: str = "1080p"` 改为 `resolution: str | None = None`：

```python
@dataclass
class VideoGenerationRequest:
    prompt: str
    output_path: Path
    aspect_ratio: str = "9:16"
    duration_seconds: int = 5
    resolution: str | None = None
    start_image: Path | None = None
    end_image: Path | None = None
    reference_images: list[Path] | None = None
    generate_audio: bool = True
    negative_prompt: str | None = None
    project_name: str | None = None
    service_tier: str = "default"
    seed: int | None = None
```

- [ ] **Step 3: Run current backend tests**

```bash
uv run python -m pytest tests/lib/image_backends/ tests/lib/video_backends/ -v
```

Expected: 有 failure（因为 backends 还没有处理 None；下面各 Task 逐个修）。这一步只是基线。

- [ ] **Step 4: Ruff + commit**

```bash
uv run ruff check lib/image_backends/base.py lib/video_backends/base.py
uv run ruff format lib/image_backends/base.py lib/video_backends/base.py
git add lib/image_backends/base.py lib/video_backends/base.py
git commit -m "refactor(backends): Request 的 image_size/resolution 改为 Optional"
```

---

## Phase 2 — 各后端"不传"语义

### Task 5: GeminiImageBackend — image_size=None 时不传

**Files:**
- Modify: `lib/image_backends/gemini.py:115-122`
- Test: `tests/lib/image_backends/test_gemini_image.py`（扩展或新建）

- [ ] **Step 1: Write failing test for None handling**

新增测试 `tests/lib/image_backends/test_gemini_image_resolution.py`：

```python
"""测试 GeminiImageBackend 对 image_size=None 的处理。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.image_backends.base import ImageGenerationRequest
from lib.image_backends.gemini import GeminiImageBackend


@pytest.mark.asyncio
async def test_image_size_none_not_passed_to_image_config(tmp_path):
    with patch("lib.image_backends.gemini._genai", create=True):  # noqa
        pass  # import style 视 backend 现有测试为准

    backend = GeminiImageBackend.__new__(GeminiImageBackend)
    backend._rate_limiter = None
    backend._image_model = "gemini-3.1-flash-image-preview"
    backend._backend_type = "aistudio"
    backend._types = MagicMock()
    backend._client = MagicMock()
    backend._client.aio.models.generate_content = AsyncMock(return_value=MagicMock(parts=[]))
    backend._capabilities = set()

    req = ImageGenerationRequest(
        prompt="hello",
        output_path=tmp_path / "out.png",
        aspect_ratio="9:16",
        image_size=None,
    )
    with pytest.raises(RuntimeError):  # 因 mocked response 返回空 parts
        await backend.generate(req)

    # 断言 ImageConfig 构造时 image_size 参数未传（kwargs 不含 image_size）
    image_config_call = backend._types.ImageConfig.call_args
    assert "image_size" not in image_config_call.kwargs
    # aspect_ratio 仍然传入
    assert image_config_call.kwargs["aspect_ratio"] == "9:16"


@pytest.mark.asyncio
async def test_image_size_provided_is_passed_to_image_config(tmp_path):
    backend = GeminiImageBackend.__new__(GeminiImageBackend)
    backend._rate_limiter = None
    backend._image_model = "gemini-3.1-flash-image-preview"
    backend._backend_type = "aistudio"
    backend._types = MagicMock()
    backend._client = MagicMock()
    backend._client.aio.models.generate_content = AsyncMock(return_value=MagicMock(parts=[]))
    backend._capabilities = set()

    req = ImageGenerationRequest(
        prompt="hello",
        output_path=tmp_path / "out.png",
        aspect_ratio="9:16",
        image_size="2K",
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    image_config_call = backend._types.ImageConfig.call_args
    assert image_config_call.kwargs["image_size"] == "2K"
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/image_backends/test_gemini_image_resolution.py -v
```

- [ ] **Step 3: Modify `lib/image_backends/gemini.py` — 条件构造 ImageConfig**

替换 `generate()` 里的 config 构造（原 115-122 行）：

```python
        # 3. 构建配置（image_size 为 None 时不传）
        image_config_kwargs: dict = {"aspect_ratio": request.aspect_ratio}
        if request.image_size is not None:
            image_config_kwargs["image_size"] = request.image_size

        config = self._types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=self._types.ImageConfig(**image_config_kwargs),
        )
```

- [ ] **Step 4: Run tests, expect PASS**

```bash
uv run python -m pytest tests/lib/image_backends/test_gemini_image_resolution.py -v
```

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check lib/image_backends/gemini.py tests/lib/image_backends/test_gemini_image_resolution.py
uv run ruff format lib/image_backends/gemini.py tests/lib/image_backends/test_gemini_image_resolution.py
git add lib/image_backends/gemini.py tests/lib/image_backends/test_gemini_image_resolution.py
git commit -m "feat(gemini-image): image_size=None 时不传 ImageConfig 字段"
```

---

### Task 6: GeminiVideoBackend — resolution=None 时不传

**Files:**
- Modify: `lib/video_backends/gemini.py:135-145`
- Test: `tests/lib/video_backends/test_gemini_video_resolution.py`（新建）

- [ ] **Step 1: Read current kwargs assembly**

```bash
grep -n "aspect_ratio\|resolution\|config_params\|generate_videos" lib/video_backends/gemini.py | head -20
```

识别现有 generate kwargs 构造处。典型形式：`"resolution": request.resolution`。

- [ ] **Step 2: Write failing test**

`tests/lib/video_backends/test_gemini_video_resolution.py`：

```python
"""测试 GeminiVideoBackend 对 resolution=None 的处理。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.gemini import GeminiVideoBackend


def _make_backend():
    backend = GeminiVideoBackend.__new__(GeminiVideoBackend)
    backend._rate_limiter = None
    backend._video_model = "veo-3.1-lite-generate-preview"
    backend._backend_type = "aistudio"
    backend._types = MagicMock()
    backend._client = MagicMock()
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_resolution_none_not_in_config(tmp_path):
    backend = _make_backend()
    captured: dict = {}

    async def _fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.aio.models.generate_videos = _fake_create
    req = VideoGenerationRequest(
        prompt="x", output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16", duration_seconds=8, resolution=None,
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    # config 中的 GenerateVideosConfig 构造 kwargs 不应包含 resolution
    cfg_call = backend._types.GenerateVideosConfig.call_args
    assert "resolution" not in (cfg_call.kwargs if cfg_call else {})


@pytest.mark.asyncio
async def test_resolution_string_passed_through(tmp_path):
    backend = _make_backend()
    async def _fake_create(**kwargs):
        raise RuntimeError("stop")
    backend._client.aio.models.generate_videos = _fake_create

    req = VideoGenerationRequest(
        prompt="x", output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16", duration_seconds=8, resolution="1080p",
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    cfg_call = backend._types.GenerateVideosConfig.call_args
    assert cfg_call.kwargs["resolution"] == "1080p"
```

- [ ] **Step 3: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/video_backends/test_gemini_video_resolution.py -v
```

- [ ] **Step 4: Modify `lib/video_backends/gemini.py`**

把当前 `"resolution": request.resolution` 改为条件加入：

```python
        config_kwargs: dict = {
            "aspect_ratio": request.aspect_ratio,
            "duration_seconds": request.duration_seconds,
            # ... 其他现有字段 ...
        }
        if request.resolution is not None:
            config_kwargs["resolution"] = request.resolution

        config = self._types.GenerateVideosConfig(**config_kwargs)
```

（实施时按 gemini.py 现有结构微调；关键是 resolution 改为条件加入。）

- [ ] **Step 5: Run test, expect PASS**

```bash
uv run python -m pytest tests/lib/video_backends/test_gemini_video_resolution.py -v
```

- [ ] **Step 6: Ruff + commit**

```bash
uv run ruff check lib/video_backends/gemini.py tests/lib/video_backends/test_gemini_video_resolution.py
uv run ruff format lib/video_backends/gemini.py tests/lib/video_backends/test_gemini_video_resolution.py
git add lib/video_backends/gemini.py tests/lib/video_backends/test_gemini_video_resolution.py
git commit -m "feat(gemini-video): resolution=None 时不传 GenerateVideosConfig.resolution"
```

---

### Task 7: ArkVideoBackend — resolution=None 时不传

**Files:**
- Modify: `lib/video_backends/ark.py:135-143`
- Test: `tests/lib/video_backends/test_ark_video_resolution.py`（新建）

- [ ] **Step 1: Write failing test**

`tests/lib/video_backends/test_ark_video_resolution.py`：

```python
"""测试 ArkVideoBackend 对 resolution=None 的处理。"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from lib.video_backends.ark import ArkVideoBackend
from lib.video_backends.base import VideoGenerationRequest


def _make_backend():
    backend = ArkVideoBackend.__new__(ArkVideoBackend)
    backend._client = MagicMock()
    backend._model = "doubao-seedance-1-5-pro-251215"
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_resolution_none_not_in_create_params(tmp_path, monkeypatch):
    backend = _make_backend()
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.content_generation.tasks.create = fake_create

    req = VideoGenerationRequest(
        prompt="x", output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16", duration_seconds=5, resolution=None,
    )
    with pytest.raises(RuntimeError):
        # _create_task 内部 to_thread 调用 create；直接调用内部函数避免 asyncio.to_thread 带来的复杂 mock
        await backend._create_task(req, content=[])

    assert "resolution" not in captured


@pytest.mark.asyncio
async def test_resolution_passed_when_set(tmp_path):
    backend = _make_backend()
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.content_generation.tasks.create = fake_create

    req = VideoGenerationRequest(
        prompt="x", output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16", duration_seconds=5, resolution="720p",
    )
    with pytest.raises(RuntimeError):
        await backend._create_task(req, content=[])

    assert captured["resolution"] == "720p"
```

注意：`_create_task` 是现有 backend 的内部方法；若实际命名不同（比如直接在 `generate` 里构造 create_params），测试改为调用 `generate` 并 mock 轮询路径。以现有 `lib/video_backends/ark.py` 的 `_create_task` / `generate` 结构为准。

- [ ] **Step 2: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/video_backends/test_ark_video_resolution.py -v
```

- [ ] **Step 3: Modify `lib/video_backends/ark.py`**

把 `create_params` dict 里的 `"resolution": request.resolution` 改为条件加入：

```python
        create_params = {
            "model": self._model,
            "content": content,
            "ratio": request.aspect_ratio,
            "duration": request.duration_seconds,
            "generate_audio": request.generate_audio,
            "watermark": False,
        }
        if request.resolution is not None:
            create_params["resolution"] = request.resolution
        if VideoCapability.FLEX_TIER in self._capabilities:
            create_params["service_tier"] = request.service_tier
        if request.seed is not None:
            create_params["seed"] = request.seed
```

- [ ] **Step 4: Run test, expect PASS**

```bash
uv run python -m pytest tests/lib/video_backends/test_ark_video_resolution.py -v
```

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check lib/video_backends/ark.py tests/lib/video_backends/test_ark_video_resolution.py
uv run ruff format lib/video_backends/ark.py tests/lib/video_backends/test_ark_video_resolution.py
git add lib/video_backends/ark.py tests/lib/video_backends/test_ark_video_resolution.py
git commit -m "feat(ark-video): resolution=None 时不传 create_params.resolution"
```

---

### Task 8: GrokImageBackend — 移除 _map_image_size_to_resolution，直接透传或不传

**Files:**
- Modify: `lib/image_backends/grok.py:79-127`
- Test: `tests/lib/image_backends/test_grok_image_resolution.py`（新建）

- [ ] **Step 1: Write failing test**

`tests/lib/image_backends/test_grok_image_resolution.py`：

```python
"""测试 GrokImageBackend 对 image_size 的新逻辑：None 不传，非 None 直接透传。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from lib.image_backends.base import ImageGenerationRequest
from lib.image_backends.grok import GrokImageBackend


def _make_backend():
    backend = GrokImageBackend.__new__(GrokImageBackend)
    backend._client = MagicMock()
    backend._model = "grok-imagine-image"
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_image_size_none_not_in_kwargs(tmp_path):
    backend = _make_backend()
    captured = {}

    async def fake_sample(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.image.sample = fake_sample
    req = ImageGenerationRequest(
        prompt="hi", output_path=tmp_path / "o.png",
        aspect_ratio="9:16", image_size=None,
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    assert "resolution" not in captured
    assert captured["aspect_ratio"] == "9:16"


@pytest.mark.asyncio
async def test_image_size_passed_through_as_is(tmp_path):
    backend = _make_backend()
    captured = {}

    async def fake_sample(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.image.sample = fake_sample
    req = ImageGenerationRequest(
        prompt="hi", output_path=tmp_path / "o.png",
        aspect_ratio="9:16", image_size="2K",
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    # 直接透传标准 token（不再经过 _map_image_size_to_resolution 小写映射）
    assert captured["resolution"] == "2K"
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/image_backends/test_grok_image_resolution.py -v
```

- [ ] **Step 3: Modify `lib/image_backends/grok.py`**

删除 `_map_image_size_to_resolution` 函数定义；在 `generate()` 里：

```python
        generate_kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "aspect_ratio": _validate_aspect_ratio(request.aspect_ratio),
        }
        if request.image_size is not None:
            generate_kwargs["resolution"] = request.image_size
```

同时在文件顶部删除对 `_map_image_size_to_resolution` 的引用（如有 import 路径）。

- [ ] **Step 4: Run test, expect PASS**

```bash
uv run python -m pytest tests/lib/image_backends/test_grok_image_resolution.py -v
```

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check lib/image_backends/grok.py tests/lib/image_backends/test_grok_image_resolution.py
uv run ruff format lib/image_backends/grok.py tests/lib/image_backends/test_grok_image_resolution.py
git add lib/image_backends/grok.py tests/lib/image_backends/test_grok_image_resolution.py
git commit -m "feat(grok-image): 移除 _map_image_size_to_resolution；image_size=None 时不传"
```

---

### Task 9: GrokVideoBackend — resolution=None 时不传

**Files:**
- Modify: `lib/video_backends/grok.py:82-90`
- Test: `tests/lib/video_backends/test_grok_video_resolution.py`（新建）

- [ ] **Step 1: Write failing test**

`tests/lib/video_backends/test_grok_video_resolution.py`：

```python
"""测试 GrokVideoBackend 对 resolution=None 的处理（对照 #387 回归）。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.grok import GrokVideoBackend


def _make_backend():
    backend = GrokVideoBackend.__new__(GrokVideoBackend)
    backend._client = MagicMock()
    backend._model = "grok-imagine-video"
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_resolution_none_not_in_generate_kwargs(tmp_path):
    backend = _make_backend()
    captured = {}

    async def fake_sample(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.video.sample = fake_sample
    req = VideoGenerationRequest(
        prompt="x", output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16", duration_seconds=5, resolution=None,
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    assert "resolution" not in captured


@pytest.mark.asyncio
async def test_resolution_passed_when_set(tmp_path):
    backend = _make_backend()
    captured = {}

    async def fake_sample(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.video.sample = fake_sample
    req = VideoGenerationRequest(
        prompt="x", output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16", duration_seconds=5, resolution="720p",
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    assert captured["resolution"] == "720p"
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/video_backends/test_grok_video_resolution.py -v
```

- [ ] **Step 3: Modify `lib/video_backends/grok.py`**

将 generate_kwargs 里 `"resolution": request.resolution` 改为条件加入：

```python
        generate_kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "aspect_ratio": request.aspect_ratio,
            "duration": request.duration_seconds,
        }
        if request.resolution is not None:
            generate_kwargs["resolution"] = request.resolution
```

（按 grok.py 现有 generate_kwargs 结构调整。）

- [ ] **Step 4: Run test, expect PASS**

```bash
uv run python -m pytest tests/lib/video_backends/test_grok_video_resolution.py -v
```

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check lib/video_backends/grok.py tests/lib/video_backends/test_grok_video_resolution.py
uv run ruff format lib/video_backends/grok.py tests/lib/video_backends/test_grok_video_resolution.py
git add lib/video_backends/grok.py tests/lib/video_backends/test_grok_video_resolution.py
git commit -m "feat(grok-video): resolution=None 时不传；移除硬默认"
```

---

### Task 10: OpenAIImageBackend — _SIZE_MAP 重构为复合 key，image_size=None 时 size/quality 都不传

**Files:**
- Modify: `lib/image_backends/openai.py`
- Test: `tests/lib/image_backends/test_openai_image_resolution.py`（新建）

- [ ] **Step 1: Write failing test**

`tests/lib/image_backends/test_openai_image_resolution.py`：

```python
"""测试 OpenAIImageBackend _SIZE_MAP 新语义：
- image_size=None → 不传 size / 不传 quality
- image_size=标准 token → 查 (image_size, aspect_ratio) 复合 key 得到 size，继续传 quality
- image_size=非标准 token → warning 后直接透传 size（由 SDK 校验）
"""

from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
import pytest

from lib.image_backends.base import ImageGenerationRequest
from lib.image_backends.openai import OpenAIImageBackend


def _make_backend():
    backend = OpenAIImageBackend.__new__(OpenAIImageBackend)
    backend._client = MagicMock()
    backend._model = "gpt-image-1.5"
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_image_size_none_omits_size_and_quality(tmp_path):
    backend = _make_backend()
    captured = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data = [type("D", (), {"b64_json": "aGk="})()]
        return FakeResp()

    backend._client.images.generate = fake_generate

    req = ImageGenerationRequest(
        prompt="hi", output_path=tmp_path / "o.png",
        aspect_ratio="9:16", image_size=None,
    )
    await backend.generate(req)

    assert "size" not in captured
    assert "quality" not in captured


@pytest.mark.asyncio
async def test_image_size_token_maps_to_size(tmp_path):
    backend = _make_backend()
    captured = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data = [type("D", (), {"b64_json": "aGk="})()]
        return FakeResp()

    backend._client.images.generate = fake_generate

    req = ImageGenerationRequest(
        prompt="hi", output_path=tmp_path / "o.png",
        aspect_ratio="9:16", image_size="1K",
    )
    await backend.generate(req)

    assert captured["size"] == "1024x1792"
    assert captured["quality"] == "medium"


@pytest.mark.asyncio
async def test_unknown_image_size_passthrough_with_warning(tmp_path, caplog):
    backend = _make_backend()
    captured = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)

        class FakeResp:
            data = [type("D", (), {"b64_json": "aGk="})()]
        return FakeResp()

    backend._client.images.generate = fake_generate

    req = ImageGenerationRequest(
        prompt="hi", output_path=tmp_path / "o.png",
        aspect_ratio="9:16", image_size="1024x1024",
    )
    await backend.generate(req)

    # 非标准 token 直接作为 size 透传
    assert captured["size"] == "1024x1024"
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/image_backends/test_openai_image_resolution.py -v
```

- [ ] **Step 3: Rewrite `lib/image_backends/openai.py` _SIZE_MAP & generate**

替换 `_SIZE_MAP` 为复合 key `(image_size, aspect_ratio) → "WxH"` 形式，并重写 `_generate_create` 的 kwargs 构造：

```python
_SIZE_MAP: dict[tuple[str, str], str] = {
    # (image_size, aspect_ratio): "WxH"
    ("512px", "1:1"): "512x512",
    ("512px", "9:16"): "512x896",
    ("512px", "16:9"): "896x512",
    ("1K", "1:1"): "1024x1024",
    ("1K", "9:16"): "1024x1792",
    ("1K", "16:9"): "1792x1024",
    ("1K", "3:4"): "1024x1792",
    ("1K", "4:3"): "1792x1024",
    ("2K", "1:1"): "2048x2048",
    ("2K", "9:16"): "2048x3584",
    ("2K", "16:9"): "3584x2048",
}

_QUALITY_MAP: dict[str, str] = {
    "512px": "low",
    "1K": "medium",
    "2K": "high",
    "4K": "high",
}


def _resolve_openai_params(
    image_size: str | None,
    aspect_ratio: str,
) -> dict[str, str]:
    """根据 image_size 返回 {size, quality} 子集。

    - None → 空 dict（全不传，走 SDK 默认）
    - 标准 token → 查 _SIZE_MAP 得 size，_QUALITY_MAP 得 quality
    - 未知 token（例如 "1024x1024"）→ warning 后作为 size 透传，不传 quality
    """
    if image_size is None:
        return {}

    mapped_size = _SIZE_MAP.get((image_size, aspect_ratio))
    if mapped_size is not None:
        params: dict[str, str] = {"size": mapped_size}
        quality = _QUALITY_MAP.get(image_size)
        if quality:
            params["quality"] = quality
        return params

    logger.warning(
        "OpenAI image: 未知 image_size=%r (aspect=%r)，原样作为 size 透传",
        image_size, aspect_ratio,
    )
    return {"size": image_size}
```

然后 `_generate_create`：

```python
    async def _generate_create(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        kwargs = {
            "model": self._model,
            "prompt": request.prompt,
            "response_format": "b64_json",
            "n": 1,
        }
        kwargs.update(_resolve_openai_params(request.image_size, request.aspect_ratio))
        response = await self._client.images.generate(**kwargs)
        return await asyncio.to_thread(self._save_and_return, response, request)
```

`_save_and_return` 中 `quality=_QUALITY_MAP.get(request.image_size, "medium")` 改为：

```python
        quality = _QUALITY_MAP.get(request.image_size) if request.image_size else None
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            quality=quality,
        )
```

- [ ] **Step 4: Run test, expect PASS**

```bash
uv run python -m pytest tests/lib/image_backends/test_openai_image_resolution.py -v
```

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check lib/image_backends/openai.py tests/lib/image_backends/test_openai_image_resolution.py
uv run ruff format lib/image_backends/openai.py tests/lib/image_backends/test_openai_image_resolution.py
git add lib/image_backends/openai.py tests/lib/image_backends/test_openai_image_resolution.py
git commit -m "refactor(openai-image): _SIZE_MAP 改复合 key；image_size=None 时不传 size/quality"
```

---

### Task 11: OpenAIVideoBackend — resolution=None 时不传 size

**Files:**
- Modify: `lib/video_backends/openai.py:66-90`
- Test: `tests/lib/video_backends/test_openai_video_resolution.py`（新建）

- [ ] **Step 1: Write failing test**

`tests/lib/video_backends/test_openai_video_resolution.py`：

```python
"""测试 OpenAIVideoBackend resolution=None 时不传 size。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from lib.video_backends.base import VideoGenerationRequest
from lib.video_backends.openai import OpenAIVideoBackend


def _make_backend():
    backend = OpenAIVideoBackend.__new__(OpenAIVideoBackend)
    backend._client = MagicMock()
    backend._model = "sora-2"
    backend._capabilities = set()
    return backend


@pytest.mark.asyncio
async def test_resolution_none_omits_size(tmp_path):
    backend = _make_backend()
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.videos.create_and_poll = fake_create

    req = VideoGenerationRequest(
        prompt="x", output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16", duration_seconds=4, resolution=None,
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    assert "size" not in captured


@pytest.mark.asyncio
async def test_resolution_token_maps_to_size(tmp_path):
    backend = _make_backend()
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    backend._client.videos.create_and_poll = fake_create

    req = VideoGenerationRequest(
        prompt="x", output_path=tmp_path / "o.mp4",
        aspect_ratio="9:16", duration_seconds=4, resolution="720p",
    )
    with pytest.raises(RuntimeError):
        await backend.generate(req)

    assert captured["size"] == "720x1280"
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/video_backends/test_openai_video_resolution.py -v
```

- [ ] **Step 3: Modify `lib/video_backends/openai.py`**

把 `_resolve_size` 改为返回 `str | None`，并在 generate 里条件加入 size：

```python
def _resolve_size(resolution: str | None, aspect_ratio: str) -> str | None:
    if resolution is None:
        return None
    mapped = _SIZE_MAP.get((resolution, aspect_ratio))
    if mapped is not None:
        return mapped
    logger.warning("OpenAI video: 未知 (resolution=%r, aspect=%r)，透传 resolution 作为 size", resolution, aspect_ratio)
    return resolution
```

然后 `generate`：

```python
        kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": _map_duration(request.duration_seconds),
        }
        size = _resolve_size(request.resolution, request.aspect_ratio)
        if size is not None:
            kwargs["size"] = size
        # ... 其余参考图等保持不变
```

- [ ] **Step 4: Run test, expect PASS**

```bash
uv run python -m pytest tests/lib/video_backends/test_openai_video_resolution.py -v
```

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check lib/video_backends/openai.py tests/lib/video_backends/test_openai_video_resolution.py
uv run ruff format lib/video_backends/openai.py tests/lib/video_backends/test_openai_video_resolution.py
git add lib/video_backends/openai.py tests/lib/video_backends/test_openai_video_resolution.py
git commit -m "feat(openai-video): resolution=None 时不传 size"
```

---

## Phase 3 — 服务层接线

### Task 12: MediaGenerator — image_size/resolution 改 Optional

**Files:**
- Modify: `lib/media_generator.py`

- [ ] **Step 1: Modify `generate_image` / `generate_image_async` 签名**

把 `image_size: str = "1K"` 改为 `image_size: str | None = None`。

- [ ] **Step 2: Modify `generate_video` / `generate_video_async` 签名**

把 `resolution: str = "1080p"` 改为 `resolution: str | None = None`。

- [ ] **Step 3: UsageTracker 兼容**

`media_generator.py:215` 处 `resolution=image_size` 传入 UsageTracker；需保持接受 None。

搜索并检查：

```bash
grep -n "resolution" lib/usage_tracker.py
```

若 `start_call(..., resolution: str | None = None, ...)` 已是 Optional，保持。若不是，改为 Optional（`lib/usage_tracker.py`）。

- [ ] **Step 4: 运行现有测试观察回归**

```bash
uv run python -m pytest tests/lib/test_media_generator.py -v
```

Expected: 大多通过；若有因默认值变化而失败的，说明测试显式断言了旧默认，修正为适配 None。

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check lib/media_generator.py lib/usage_tracker.py
uv run ruff format lib/media_generator.py lib/usage_tracker.py
git add lib/media_generator.py lib/usage_tracker.py
git commit -m "refactor(media-generator): image_size/resolution 改为 Optional"
```

---

### Task 13: generation_tasks.py — 删除 DEFAULT_VIDEO_RESOLUTION，接入 resolver，移除 image_size 硬编码

**Files:**
- Modify: `server/services/generation_tasks.py`
- Test: 扩展 `tests/server/services/test_generation_tasks.py`（若不存在可跳过单测，用集成 smoke test）

- [ ] **Step 1: Remove `DEFAULT_VIDEO_RESOLUTION` dict**

删除文件顶部（约 49-54 行）：

```python
DEFAULT_VIDEO_RESOLUTION: dict[str, str] = { ... }
```

- [ ] **Step 2: Add helper `_get_custom_resolution_default` for custom providers**

在文件合适位置新增：

```python
async def _get_custom_resolution_default(
    provider_name: str,
    model_id: str | None,
) -> str | None:
    """若是自定义供应商，返回该模型的默认 resolution（CustomProviderModel.resolution）。"""
    if not is_custom_provider(provider_name) or not model_id:
        return None
    from lib.custom_provider import parse_provider_id
    from lib.db import async_session_factory
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    async with async_session_factory() as session:
        repo = CustomProviderRepository(session)
        db_id = parse_provider_id(provider_name)
        model = await repo.get_model_by_ids(db_id, model_id)
        if model is None:
            return None
        return model.resolution
```

- [ ] **Step 3: 替换 generate_video_task 的 resolution 解析**

定位 `server/services/generation_tasks.py:750-798` 区段。替换：

```python
    # 旧:
    # resolution_key = _PROVIDER_ID_TO_BACKEND.get(provider_name, provider_name)
    # video_model_settings = project.get("video_model_settings", {})
    # model_settings = video_model_settings.get(model_name, {}) if model_name else {}
    # resolution = model_settings.get("resolution") or DEFAULT_VIDEO_RESOLUTION.get(resolution_key, "1080p")
    # 新:
    from server.services.resolution_resolver import resolve_resolution

    custom_default = await _get_custom_resolution_default(provider_name, model_name)
    resolution = resolve_resolution(
        project,
        registry_provider_id or provider_name,
        model_name or "",
        custom_default=custom_default,
    )
```

注意：`registry_provider_id` 是已经解析出的 `provider_id`（见原文 759-776 行逻辑），用它作为 resolver key 的第一段。

- [ ] **Step 4: 替换 generate_storyboard_task / generate_character_task / generate_scene_or_prop_task 的 image_size**

在每处 `image_size="1K"` / `"2K"` 替换为 resolver 调用。以 generate_storyboard_task 为例（约 683-691 行）：

```python
    # 旧: image_size="1K"
    # 新:
    from server.services.resolution_resolver import resolve_resolution

    image_provider_id = payload.get("image_provider") or ""
    image_model_id = payload.get("image_model") or ""
    custom_default = await _get_custom_resolution_default(image_provider_id, image_model_id)
    image_size = resolve_resolution(
        project, image_provider_id, image_model_id, custom_default=custom_default,
    )
    # 生成调用
    await generator.generate_image_async(
        ...
        image_size=image_size,
        ...
    )
```

对 generate_character_task (878-886)、generate_scene_or_prop_task (946-953)、grid 生成（1122-1130）各自做同样替换。

> ⚠️ **grid 那条的 image_size="2K"**：宫格图 historically 用高分辨率，若项目未配置则需要保留高默认。策略：调用 resolver；如果返回 None，再回退到 "2K"：
> ```python
> image_size = resolve_resolution(...) or "2K"  # 宫格图保底高分辨率
> ```

- [ ] **Step 5: Run full server-side test suite**

```bash
uv run python -m pytest tests/server/services/test_generation_tasks.py -v 2>&1 | tail -40
```

Expected: 现有测试通过；若有对 `DEFAULT_VIDEO_RESOLUTION` 的直接引用测试，同步更新。

- [ ] **Step 6: Ruff + commit**

```bash
uv run ruff check server/services/generation_tasks.py
uv run ruff format server/services/generation_tasks.py
git add server/services/generation_tasks.py
git commit -m "refactor(generation-tasks): 接入 resolve_resolution，移除 DEFAULT_VIDEO_RESOLUTION 与 image_size 硬编码"
```

---

### Task 14: reference_video_tasks.py — 接入 resolver

**Files:**
- Modify: `server/services/reference_video_tasks.py:238-244`

- [ ] **Step 1: Replace the legacy resolution resolution block**

```python
# 旧 238-244:
# video_model_settings = project.get("video_model_settings") or {}
# model_resolution_setting = video_model_settings.get(model_name, {}) if model_name else {}
# resolution = model_resolution_setting.get("resolution") or DEFAULT_VIDEO_RESOLUTION.get(provider_name, "1080p")

# 新:
from server.services.resolution_resolver import resolve_resolution
from server.services.generation_tasks import _get_custom_resolution_default

custom_default = await _get_custom_resolution_default(provider_name, model_name)
resolution = resolve_resolution(
    project, provider_name, model_name or "", custom_default=custom_default,
)
# 若 grok_imagine_video 等必传，fallback 到该模型注册表 resolutions[0]（保底）
if resolution is None:
    from lib.config.registry import PROVIDER_REGISTRY
    meta = PROVIDER_REGISTRY.get(provider_name)
    if meta and model_name and meta.models.get(model_name):
        model_info = meta.models[model_name]
        if model_info.resolutions:
            resolution = model_info.resolutions[0]
```

- [ ] **Step 2: Run tests**

```bash
uv run python -m pytest tests/server/services/test_reference_video_tasks.py -v 2>&1 | tail -20
```

Expected: 通过。

- [ ] **Step 3: Ruff + commit**

```bash
uv run ruff check server/services/reference_video_tasks.py
uv run ruff format server/services/reference_video_tasks.py
git add server/services/reference_video_tasks.py
git commit -m "refactor(reference-video-tasks): 接入 resolve_resolution，保底 registry 首个 resolution"
```

---

### Task 15: ProjectManager — 保存 model_settings 时迁移 legacy video_model_settings

**Files:**
- Modify: `lib/project_manager.py`
- Test: `tests/lib/test_project_manager_legacy_migration.py`（新建）

- [ ] **Step 1: Write failing test**

`tests/lib/test_project_manager_legacy_migration.py`：

```python
"""测试 project.video_model_settings[model].resolution 在写 model_settings 时自动迁移。"""

from pathlib import Path
import json
import pytest

from lib.project_manager import ProjectManager


@pytest.fixture
def pm_tmp(tmp_path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "project.json").write_text(json.dumps({
        "video_model_settings": {"veo-3.1": {"resolution": "1080p"}},
    }))
    return ProjectManager(tmp_path), tmp_path


def test_writing_model_settings_migrates_legacy(pm_tmp):
    pm, root = pm_tmp
    # 写入 new-style 配置
    project = pm.load_project("demo")
    project["model_settings"] = {"gemini-aistudio/veo-3.1": {"resolution": "720p"}}
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    # legacy 应被清空（特定模型那条）
    assert saved.get("video_model_settings", {}).get("veo-3.1") in (None, {})
    # new-style 保持
    assert saved["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "720p"


def test_save_without_model_settings_preserves_legacy(pm_tmp):
    """若本次保存未改动 model_settings，legacy 字段保留（读路径仍然兼容）。"""
    pm, root = pm_tmp
    project = pm.load_project("demo")
    project["title"] = "hello"
    pm.save_project("demo", project)

    saved = json.loads((root / "demo" / "project.json").read_text())
    assert saved["video_model_settings"]["veo-3.1"]["resolution"] == "1080p"
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
uv run python -m pytest tests/lib/test_project_manager_legacy_migration.py -v
```

- [ ] **Step 3: Modify `lib/project_manager.py`**

找到 `save_project` 方法，在最终写盘前加入迁移逻辑：

```python
def _migrate_legacy_resolution_on_save(self, project: dict) -> None:
    """若本次 project.model_settings 含 resolution，清除 video_model_settings 中命中的 legacy 条目。

    migration 规则：对每个 new model_settings key（形如 "<provider>/<model>"），若其 resolution 已设置，
    则从 video_model_settings[<model>] 中移除 resolution 字段；如该条目变空则删除该 key。
    """
    model_settings = project.get("model_settings") or {}
    legacy = project.get("video_model_settings") or {}
    if not model_settings or not legacy:
        return
    for composite_key, entry in model_settings.items():
        if "/" not in composite_key:
            continue
        _, model_id = composite_key.split("/", 1)
        if not entry.get("resolution"):
            continue
        legacy_entry = legacy.get(model_id)
        if not legacy_entry:
            continue
        legacy_entry.pop("resolution", None)
        if not legacy_entry:
            legacy.pop(model_id, None)
    if not legacy:
        project.pop("video_model_settings", None)
```

然后在 `save_project`（或 `_write_project_json`）里调用：

```python
    def save_project(self, name: str, project: dict) -> None:
        self._migrate_legacy_resolution_on_save(project)
        # ... 原有写盘逻辑
```

（按 `lib/project_manager.py` 实际 save_project 签名调整；重点是在写盘前调用 migration helper。）

- [ ] **Step 4: Run test, expect PASS**

```bash
uv run python -m pytest tests/lib/test_project_manager_legacy_migration.py -v
```

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check lib/project_manager.py tests/lib/test_project_manager_legacy_migration.py
uv run ruff format lib/project_manager.py tests/lib/test_project_manager_legacy_migration.py
git add lib/project_manager.py tests/lib/test_project_manager_legacy_migration.py
git commit -m "feat(project-manager): 保存 model_settings 时迁移 legacy video_model_settings.resolution"
```

---

## Phase 4 — API 表面

### Task 16: providers router — ModelInfoResponse 新增 resolutions

**Files:**
- Modify: `server/routers/providers.py:67-73`
- Test: 扩展 `tests/server/routers/test_providers.py`

- [ ] **Step 1: Add field to ModelInfoResponse**

```python
class ModelInfoResponse(BaseModel):
    display_name: str
    media_type: str
    capabilities: list[str]
    default: bool
    supported_durations: list[int] = []
    duration_resolution_constraints: dict[str, list[int]] = {}
    resolutions: list[str] = []
```

由于 `models_dict = {mid: asdict(mi) for mid, mi in meta.models.items()}`（`lib/config/service.py:103`）已经包含 `resolutions`（dataclass 自动）→ 这里只需声明即可。

- [ ] **Step 2: Add assertion in existing providers test**

在 `tests/server/routers/test_providers.py` 合适位置新增：

```python
def test_list_providers_exposes_resolutions(client, auth_headers):
    resp = client.get("/api/v1/providers", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    # 任何 image/video 模型应有 resolutions 字段（可为空列表）
    for p in data["providers"]:
        for mid, minfo in p["models"].items():
            assert "resolutions" in minfo
```

- [ ] **Step 3: Run test**

```bash
uv run python -m pytest tests/server/routers/test_providers.py -v
```

- [ ] **Step 4: Ruff + commit**

```bash
uv run ruff check server/routers/providers.py tests/server/routers/test_providers.py
uv run ruff format server/routers/providers.py tests/server/routers/test_providers.py
git add server/routers/providers.py tests/server/routers/test_providers.py
git commit -m "feat(providers-api): ModelInfoResponse 新增 resolutions 字段"
```

---

### Task 17: custom_providers router — DTO 新增 resolution

**Files:**
- Modify: `server/routers/custom_providers.py`
- Test: 扩展 `tests/server/routers/test_custom_providers.py`

- [ ] **Step 1: Identify DTOs**

```bash
grep -n "class Model\|supported_durations\|BaseModel" server/routers/custom_providers.py | head -20
```

找到 `ModelInput`（创建/更新时）和 `ModelResponse`（返回时）两种 DTO。

- [ ] **Step 2: Add `resolution` field to both DTOs**

在 `ModelInput` 和 `ModelResponse`（名称以实际代码为准；即 56-70 行附近与 117-160 行附近的两个类）加：

```python
    resolution: str | None = None
```

在 `to_db_dict()`（约 65-72 行）确保 `resolution` 被写入返回 dict（如果用的是 `model_dump()`，自动包含；如果手动 dict 构造，显式加入）。

在路由里把返回 DTO 构造处加入 `resolution=m.resolution`。

- [ ] **Step 3: Write test**

`tests/server/routers/test_custom_providers.py` 新增（或扩展现有）：

```python
def test_custom_provider_model_crud_with_resolution(client, auth_headers):
    # 创建
    resp = client.post("/api/v1/custom-providers", json={
        "display_name": "X", "api_format": "openai",
        "base_url": "https://api.example.com", "api_key": "k",
        "models": [{
            "model_id": "m1", "display_name": "M1",
            "media_type": "video", "is_default": True, "is_enabled": True,
            "resolution": "720p",
        }],
    }, headers=auth_headers)
    assert resp.status_code == 200
    pid = resp.json()["id"]

    # 读取
    resp = client.get(f"/api/v1/custom-providers/{pid}", headers=auth_headers)
    models = resp.json()["models"]
    assert models[0]["resolution"] == "720p"

    # 更新 resolution 为 null
    resp = client.patch(
        f"/api/v1/custom-providers/{pid}/models/{models[0]['id']}",
        json={"resolution": None}, headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["resolution"] is None
```

- [ ] **Step 4: Run test, expect PASS (after field added)**

```bash
uv run python -m pytest tests/server/routers/test_custom_providers.py -v -k resolution
```

- [ ] **Step 5: Ruff + commit**

```bash
uv run ruff check server/routers/custom_providers.py tests/server/routers/test_custom_providers.py
uv run ruff format server/routers/custom_providers.py tests/server/routers/test_custom_providers.py
git add server/routers/custom_providers.py tests/server/routers/test_custom_providers.py
git commit -m "feat(custom-providers-api): DTO 新增 resolution 字段"
```

---

### Task 18: projects router — CreateProjectRequest/UpdateProjectRequest 接受 model_settings

**Files:**
- Modify: `server/routers/projects.py:71-117`（CreateProjectRequest, UpdateProjectRequest）
- Test: 扩展 `tests/server/routers/test_projects.py`

- [ ] **Step 1: Add Pydantic field**

在 `CreateProjectRequest` 类底部追加：

```python
    model_settings: dict[str, dict[str, str | None]] | None = None
```

在 `UpdateProjectRequest` 同样追加（保持 `| None = None` 以便 PATCH 语义）。

- [ ] **Step 2: Wire into project creation**

找到 `projects.py:483` 附近 `create_project` 调用，把 `model_settings` 传入：

```python
    get_project_manager().create_project(
        name=name,
        title=...,
        aspect_ratio=req.aspect_ratio,
        ...  # 现有字段
        model_settings=req.model_settings or {},
    )
```

在 `ProjectManager.create_project` 中接受 `model_settings` 参数并写入 project.json 根（如果还没有）。

- [ ] **Step 3: Wire into project update**

找到 `projects.py:622` 附近现有的 `aspect_ratio` update 逻辑，添加兄弟分支：

```python
    if "model_settings" in req.model_fields_set and req.model_settings is not None:
        project["model_settings"] = req.model_settings
```

- [ ] **Step 4: Write test**

`tests/server/routers/test_projects.py` 新增：

```python
def test_create_project_with_model_settings(client, auth_headers):
    resp = client.post("/api/v1/projects", json={
        "name": "demo-res",
        "title": "T",
        "model_settings": {
            "gemini-aistudio/veo-3.1-lite-generate-preview": {"resolution": "720p"},
        },
    }, headers=auth_headers)
    assert resp.status_code == 200

    # 读回
    resp = client.get("/api/v1/projects/demo-res", headers=auth_headers)
    assert resp.json()["model_settings"]["gemini-aistudio/veo-3.1-lite-generate-preview"]["resolution"] == "720p"


def test_patch_project_model_settings_migrates_legacy(client, auth_headers, tmp_project_with_legacy):
    # tmp_project_with_legacy fixture 创建一个含 video_model_settings.veo-3.1.resolution=1080p 的项目
    resp = client.patch(
        f"/api/v1/projects/{tmp_project_with_legacy}",
        json={"model_settings": {"gemini-aistudio/veo-3.1": {"resolution": "720p"}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = client.get(f"/api/v1/projects/{tmp_project_with_legacy}", headers=auth_headers).json()
    assert data["model_settings"]["gemini-aistudio/veo-3.1"]["resolution"] == "720p"
    # legacy 已清理
    assert data.get("video_model_settings", {}).get("veo-3.1") in (None, {})
```

注：`tmp_project_with_legacy` fixture 若不存在，临时 inline 或 skip 该 test（迁移已在 Task 15 单独覆盖）。

- [ ] **Step 5: Run tests**

```bash
uv run python -m pytest tests/server/routers/test_projects.py -v -k model_settings
```

- [ ] **Step 6: Ruff + commit**

```bash
uv run ruff check server/routers/projects.py tests/server/routers/test_projects.py
uv run ruff format server/routers/projects.py tests/server/routers/test_projects.py
git add server/routers/projects.py tests/server/routers/test_projects.py
git commit -m "feat(projects-api): CreateProject / UpdateProject 接受 model_settings"
```

---

## Phase 5 — 前端

### Task 19: 前端类型 + api.ts

**Files:**
- Modify: `frontend/src/types/provider.ts`
- Modify: `frontend/src/types/custom-provider.ts`
- Modify: `frontend/src/types/project.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: 添加 `resolutions` 到 ModelInfoResponse**

```typescript
// frontend/src/types/provider.ts
export interface ModelInfoResponse {
  display_name: string;
  media_type: string;
  capabilities: string[];
  default: boolean;
  supported_durations: number[];
  duration_resolution_constraints: Record<string, number[]>;
  resolutions: string[];
}
```

- [ ] **Step 2: 添加 `resolution` 到 CustomProviderModelInfo / CustomProviderModelInput**

```typescript
// frontend/src/types/custom-provider.ts
export interface CustomProviderModelInfo {
  id: number;
  model_id: string;
  display_name: string;
  media_type: "text" | "image" | "video";
  is_default: boolean;
  is_enabled: boolean;
  price_unit: string | null;
  price_input: number | null;
  price_output: number | null;
  currency: string | null;
  supported_durations: number[] | null;
  resolution: string | null;
}

export interface CustomProviderModelInput {
  model_id: string;
  display_name: string;
  media_type: "text" | "image" | "video";
  is_default: boolean;
  is_enabled: boolean;
  price_unit?: string;
  price_input?: number;
  price_output?: number;
  currency?: string;
  supported_durations?: number[] | null;
  resolution?: string | null;
}
```

- [ ] **Step 3: 添加 `model_settings` 到 Project**

```typescript
// frontend/src/types/project.ts
export interface ModelSettingEntry {
  resolution?: string | null;
}

// 在 Project interface 里追加:
export interface Project {
  // ... 现有字段
  model_settings?: Record<string, ModelSettingEntry>;
}
```

- [ ] **Step 4: `api.ts` — CreateProjectParams / UpdateProjectParams 接受 model_settings**

```typescript
export interface CreateProjectParams {
  // ... 现有
  model_settings?: Record<string, { resolution?: string | null }>;
}
export interface UpdateProjectParams {
  // ... 现有
  model_settings?: Record<string, { resolution?: string | null }>;
}
```

- [ ] **Step 5: Typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: 通过。若有相关测试（api.test.ts）因缺字段 error，补齐 fixture 中的 `resolutions: []`。

- [ ] **Step 6: Commit**

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/feat/resolution-param-refactor
git add frontend/src/types/provider.ts frontend/src/types/custom-provider.ts frontend/src/types/project.ts frontend/src/api.ts
git commit -m "feat(frontend-types): 补充 resolutions / resolution / model_settings 字段"
```

---

### Task 20: ResolutionPicker 组件（TDD）

**Files:**
- Create: `frontend/src/components/shared/ResolutionPicker.tsx`
- Create: `frontend/src/components/shared/ResolutionPicker.test.tsx`

- [ ] **Step 1: Write failing test**

`frontend/src/components/shared/ResolutionPicker.test.tsx`：

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ResolutionPicker } from "./ResolutionPicker";

describe("ResolutionPicker", () => {
  it("select mode renders options + default and maps empty to null", () => {
    const onChange = vi.fn();
    render(
      <ResolutionPicker
        mode="select"
        options={["720p", "1080p"]}
        value={null}
        onChange={onChange}
        placeholder="默认（不传）"
      />
    );
    const select = screen.getByRole("combobox");
    expect(select).toBeInTheDocument();
    // 默认 placeholder 显示
    expect(screen.getByText("默认（不传）")).toBeInTheDocument();
    // 更改选项
    fireEvent.change(select, { target: { value: "720p" } });
    expect(onChange).toHaveBeenCalledWith("720p");
    // 选 placeholder 时回 null
    fireEvent.change(select, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("empty options not rendered", () => {
    const { container } = render(
      <ResolutionPicker
        mode="select"
        options={[]}
        value={null}
        onChange={() => {}}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it("combobox mode allows custom input", () => {
    const onChange = vi.fn();
    render(
      <ResolutionPicker
        mode="combobox"
        options={["720p", "1080p", "4K"]}
        value={null}
        onChange={onChange}
        placeholder="默认（不传）"
      />
    );
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "1024x1024" } });
    expect(onChange).toHaveBeenCalledWith("1024x1024");
    fireEvent.change(input, { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});
```

- [ ] **Step 2: Run test, expect FAIL**

```bash
cd frontend && pnpm test -- ResolutionPicker
```

- [ ] **Step 3: Implement `ResolutionPicker.tsx`**

```tsx
import { useId } from "react";

export interface ResolutionPickerProps {
  mode: "select" | "combobox";
  options: string[];
  value: string | null;
  onChange: (v: string | null) => void;
  placeholder?: string;
  disabled?: boolean;
  "aria-label"?: string;
}

export function ResolutionPicker({
  mode,
  options,
  value,
  onChange,
  placeholder = "默认（不传）",
  disabled,
  "aria-label": ariaLabel,
}: ResolutionPickerProps) {
  const listId = useId();
  if (options.length === 0) return null;

  if (mode === "select") {
    return (
      <select
        aria-label={ariaLabel}
        className="rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-sm text-gray-100"
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    );
  }

  // combobox：<input list=...>
  return (
    <>
      <input
        type="text"
        role="textbox"
        aria-label={ariaLabel}
        className="rounded-lg border border-gray-700 bg-gray-900 px-3 py-1.5 text-sm text-gray-100"
        value={value ?? ""}
        disabled={disabled}
        placeholder={placeholder}
        list={listId}
        onChange={(e) => {
          const v = e.target.value.trim();
          onChange(v === "" ? null : v);
        }}
      />
      <datalist id={listId}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </>
  );
}
```

- [ ] **Step 4: Run test, expect PASS**

```bash
cd frontend && pnpm test -- ResolutionPicker
```

- [ ] **Step 5: Commit**

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/feat/resolution-param-refactor
git add frontend/src/components/shared/ResolutionPicker.tsx frontend/src/components/shared/ResolutionPicker.test.tsx
git commit -m "feat(frontend): 新建 ResolutionPicker 组件（select / combobox 双模）"
```

---

### Task 21: CustomProviderForm — 模型行加 ResolutionPicker

**Files:**
- Modify: `frontend/src/components/pages/settings/CustomProviderForm.tsx`
- Modify: `frontend/src/i18n/{zh,en}/dashboard.ts`

- [ ] **Step 1: 扩展 ModelRow / 初始化函数**

```tsx
// 在 ModelRow 接口追加
interface ModelRow {
  // 现有字段
  resolution: string;   // 空字符串 = null
}

function newModelRow(partial?: Partial<ModelRow>): ModelRow {
  return {
    // 现有
    resolution: "",
    ...partial,
  };
}

function existingToRow(m: CustomProviderInfo["models"][number]): ModelRow {
  return newModelRow({
    // 现有
    resolution: m.resolution ?? "",
  });
}

function rowToInput(r: ModelRow): CustomProviderModelInput {
  return {
    // 现有
    ...(r.resolution ? { resolution: r.resolution } : { resolution: null }),
  };
}
```

- [ ] **Step 2: 在模型行 UI 中插入 ResolutionPicker**

找到 `CustomProviderForm.tsx` 中渲染模型行的 JSX（price 字段附近），对 image / video media_type 的行追加分辨率列：

```tsx
import { ResolutionPicker } from "@/components/shared/ResolutionPicker";

const IMAGE_RESOLUTIONS = ["512px", "1K", "2K", "4K"];
const VIDEO_RESOLUTIONS = ["480p", "720p", "1080p", "4K"];

// 在行 JSX 中（price_unit 旁边或独立列）
{(row.media_type === "image" || row.media_type === "video") && (
  <ResolutionPicker
    mode="combobox"
    options={row.media_type === "image" ? IMAGE_RESOLUTIONS : VIDEO_RESOLUTIONS}
    value={row.resolution || null}
    onChange={(v) => updateRow(row.key, { resolution: v ?? "" })}
    placeholder={t("resolution_default_placeholder")}
    aria-label={t("resolution_label")}
  />
)}
```

- [ ] **Step 3: 添加 i18n 键**

`frontend/src/i18n/zh/dashboard.ts`：

```typescript
'resolution_label': '分辨率',
'resolution_default_placeholder': '默认（不传）',
'resolution_help_custom': '常见值可下拉选择，也可直接填写 API 要求的原始值',
```

`frontend/src/i18n/en/dashboard.ts`：

```typescript
'resolution_label': 'Resolution',
'resolution_default_placeholder': 'Default (unset)',
'resolution_help_custom': 'Select a common token from the dropdown, or enter the raw value expected by the API',
```

- [ ] **Step 4: 运行 typecheck + i18n test**

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/feat/resolution-param-refactor
cd frontend && pnpm typecheck && cd ..
uv run python -m pytest tests/lib/i18n/test_i18n_consistency.py -v
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/pages/settings/CustomProviderForm.tsx frontend/src/i18n/zh/dashboard.ts frontend/src/i18n/en/dashboard.ts
git commit -m "feat(custom-provider-ui): 模型行新增分辨率 combobox"
```

---

### Task 22: Wizard Step2 / ModelConfigSection — 接入 ResolutionPicker

**Files:**
- Modify: `frontend/src/components/shared/ModelConfigSection.tsx`
- Modify: `frontend/src/components/pages/CreateProjectModal.tsx`（传入 value/onChange）

- [ ] **Step 1: Extend ModelConfigValue**

```typescript
// frontend/src/components/shared/ModelConfigSection.tsx
export interface ModelConfigValue {
  videoBackend: string;
  imageBackend: string;
  textBackendScript: string;
  textBackendOverview: string;
  textBackendStyle: string;
  defaultDuration: number | null;
  videoResolution: string | null;   // 新增
  imageResolution: string | null;   // 新增
}
```

- [ ] **Step 2: 工具函数：根据 backend 字符串 + providers 查分辨率候选**

在 `frontend/src/utils/provider-models.ts`（或同一目录的 util）新增：

```typescript
export function lookupResolutions(
  providers: ProviderInfo[],
  backend: string,
  customProviders?: CustomProviderInfo[],
): { options: string[]; isCustom: boolean } {
  if (!backend.includes("/")) return { options: [], isCustom: false };
  const [providerId, modelId] = backend.split("/", 2);
  const preset = providers.find((p) => p.id === providerId);
  if (preset) {
    return { options: preset.models[modelId]?.resolutions ?? [], isCustom: false };
  }
  // custom provider
  const custom = (customProviders ?? []).find((c) => `custom-${c.id}` === providerId);
  if (!custom) return { options: [], isCustom: false };
  const mediaType = custom.models.find((m) => m.model_id === modelId)?.media_type;
  if (mediaType === "image") return { options: ["512px","1K","2K","4K"], isCustom: true };
  if (mediaType === "video") return { options: ["480p","720p","1080p","4K"], isCustom: true };
  return { options: [], isCustom: true };
}
```

- [ ] **Step 3: 在 ModelConfigSection 渲染分辨率行**

在 video card 和 image card 各自 `ProviderModelSelect` 下方插入：

```tsx
import { ResolutionPicker } from "./ResolutionPicker";
import { lookupResolutions } from "@/utils/provider-models";

// video card 内（handleVideoChange 附近或 card JSX 中）
const videoRes = useMemo(
  () => lookupResolutions(providers, effectiveVideoBackend, customProviders),
  [providers, effectiveVideoBackend, customProviders],
);
// 渲染：
{videoRes.options.length > 0 && (
  <div className="mt-2 flex items-center gap-2">
    <span className="text-sm text-gray-400">{t("resolution_label")}</span>
    <ResolutionPicker
      mode={videoRes.isCustom ? "combobox" : "select"}
      options={videoRes.options}
      value={value.videoResolution}
      onChange={(v) => onChange({ ...value, videoResolution: v })}
      placeholder={t("resolution_default_placeholder")}
    />
  </div>
)}
```

image card 同理，使用 `effectiveImageBackend` + `imageResolution`。

- [ ] **Step 4: CreateProjectModal — 初始化 + 提交时转 model_settings**

找到 `frontend/src/components/pages/CreateProjectModal.tsx:183` 附近 `createProject` 调用，新增：

```tsx
const modelSettings: Record<string, { resolution: string | null }> = {};
if (modelConfig.videoBackend && modelConfig.videoResolution) {
  modelSettings[modelConfig.videoBackend] = { resolution: modelConfig.videoResolution };
}
if (modelConfig.imageBackend && modelConfig.imageResolution) {
  modelSettings[modelConfig.imageBackend] = { resolution: modelConfig.imageResolution };
}
// 调用:
await API.createProject({
  // ... 现有字段
  model_settings: modelSettings,
});
```

- [ ] **Step 5: Run typecheck + existing tests**

```bash
cd frontend && pnpm typecheck && pnpm test -- WizardStep2Models ModelConfigSection CreateProjectModal
```

调整断点了的测试 fixtures（例如增加 `resolutions: []`）。

- [ ] **Step 6: Commit**

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/feat/resolution-param-refactor
git add frontend/src/components/shared/ModelConfigSection.tsx frontend/src/components/pages/CreateProjectModal.tsx frontend/src/utils/provider-models.ts
git commit -m "feat(wizard): Step2 模型卡片新增分辨率选择"
```

---

### Task 23: ProjectSettingsPage — 接入 ResolutionPicker

**Files:**
- Modify: `frontend/src/components/pages/ProjectSettingsPage.tsx`

- [ ] **Step 1: Load / persist model_settings**

在组件内 state 新增：

```tsx
const [videoResolution, setVideoResolution] = useState<string | null>(null);
const [imageResolution, setImageResolution] = useState<string | null>(null);
```

在加载 project 后初始化：

```tsx
useEffect(() => {
  if (!project) return;
  const ms = project.model_settings ?? {};
  // legacy 读：若 model_settings 没有对应 key，检查 video_model_settings
  const videoKey = project.video_backend;
  const imageKey = project.image_backend;
  if (videoKey && ms[videoKey]?.resolution) {
    setVideoResolution(ms[videoKey].resolution!);
  } else if (project.video_model_settings && videoKey) {
    // legacy 字段仅有 model_id，不是复合 key——首次加载暂不回填，保留为 null
    setVideoResolution(null);
  }
  if (imageKey && ms[imageKey]?.resolution) {
    setImageResolution(ms[imageKey].resolution!);
  }
}, [project]);
```

- [ ] **Step 2: 渲染 ResolutionPicker**

在 video backend select 下方和 image backend select 下方各插入一行（与 Wizard 模式一致，复用 `lookupResolutions`）。

- [ ] **Step 3: 保存时拼 model_settings**

在 `handleSave`/`onSave` 里：

```tsx
const modelSettings: Record<string, { resolution: string | null }> = {
  ...(project.model_settings ?? {}),
};
if (project.video_backend) {
  modelSettings[project.video_backend] = { resolution: videoResolution };
}
if (project.image_backend) {
  modelSettings[project.image_backend] = { resolution: imageResolution };
}
await API.updateProject(projectName, {
  aspect_ratio: aspectRatio || undefined,
  // 现有字段
  model_settings: modelSettings,
});
```

- [ ] **Step 4: Run tests**

```bash
cd frontend && pnpm test -- ProjectSettingsPage
```

调整测试 fixtures。

- [ ] **Step 5: Commit**

```bash
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/feat/resolution-param-refactor
git add frontend/src/components/pages/ProjectSettingsPage.tsx
git commit -m "feat(project-settings): 图片/视频 backend 新增分辨率选择并持久化 model_settings"
```

---

## Phase 6 — 集成验证与收尾

### Task 24: 手动 SDK 必传性验证（不在 CI 运行的文档）

**Files:**
- Create: `docs/superpowers/specs/2026-04-23-resolution-param-refactor-design.md`（追加附录章节）

- [ ] **Step 1: 实施阶段按下列 checklist 人工验证**

对每个 backend 运行一次真实生成调用（使用已配置的 API Key），验证：

| 模型 | None 行为（不传 resolution）| 记录结果 |
|---|---|---|
| gemini-3.1-flash-image-preview | 期望正常生成 | ☐ |
| veo-3.1-lite-generate-preview | 期望正常生成 | ☐ |
| doubao-seedance-1-5-pro-251215 | ☐ | |
| doubao-seedream-5-0-lite-260128 | 期望正常生成（一直不传）| ☐ |
| grok-imagine-image | ☐（#387 对照：历史 xai_sdk 对 1080p 报错）| |
| grok-imagine-video | ☐ | |
| gpt-image-1.5 | 期望正常生成（size/quality 走 SDK 默认）| ☐ |
| sora-2 | 期望正常生成（size 走 SDK 默认）| ☐ |

对任一**验证失败**（SDK 拒绝 None）的模型，在 Task 25 中做前端强制选择处理。

- [ ] **Step 2: Append verification notes to spec**

在 spec 文档末尾追加"附录 A：SDK 必传性验证结果"段落，记录每个模型的验证状态。

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-04-23-resolution-param-refactor-design.md
git commit -m "docs(spec): 追加 SDK 必传性验证结果"
```

---

### Task 25: 强制选择兜底（针对 SDK 必传的模型）

> ⚠️ **条件执行**：只在 Task 24 验证出某模型的 SDK 必传 resolution 时才执行此 task。

**Files:**
- Modify: `frontend/src/components/shared/ResolutionPicker.tsx`
- Modify: `frontend/src/components/shared/ModelConfigSection.tsx`

- [ ] **Step 1: Add required prop**

```tsx
// ResolutionPicker 增加:
required?: boolean;  // 为 true 时，select 模式不提供 placeholder 项
```

实现中：

```tsx
if (mode === "select") {
  return (
    <select ...>
      {!required && <option value="">{placeholder}</option>}
      {options.map(...)}
    </select>
  );
}
```

- [ ] **Step 2: 根据注册表判定 required**

在 ModelConfigSection 里，lookupResolutions 返回值扩展为包含 `required: boolean`，根据模型 id 判断（例如 grok-imagine-video 的已知缺省会报错→ required=true）。

可以硬编码一个 `REQUIRED_RESOLUTION_MODELS = new Set(["grok-imagine-video", ...])` 在 `utils/provider-models.ts`。

- [ ] **Step 3: Run full test suite**

```bash
cd frontend && pnpm check
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/feat/resolution-param-refactor
uv run python -m pytest -q 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/shared/ResolutionPicker.tsx frontend/src/components/shared/ModelConfigSection.tsx frontend/src/utils/provider-models.ts
git commit -m "feat(frontend): SDK 必传模型的分辨率强制选择兜底"
```

---

### Task 26: 全量回归 + 最终提交前检查

- [ ] **Step 1: 后端全量测试**

```bash
uv run python -m pytest --cov=lib --cov=server --cov-report=term-missing 2>&1 | tail -20
```

Expected: 覆盖率 ≥80%；测试全绿。

- [ ] **Step 2: 前端全量检查**

```bash
cd frontend && pnpm check && pnpm build
```

Expected: typecheck + 测试 + 构建全绿。

- [ ] **Step 3: 手动 smoke**

启动开发环境（`uv run python -m uvicorn server.main:app --port 1241 --reload` + `cd frontend && pnpm dev`），走通：

- 创建项目（Wizard Step2 分辨率选择 → 提交 → 检查 project.json）
- 进入项目设置页，切换分辨率 → 保存 → 刷新确认持久化
- 自定义供应商管理页：新增一个 image 模型 + resolution，运行一次图片生成
- 触发一个含 legacy `video_model_settings` 的旧项目，执行保存 → 确认迁移到 `model_settings`

- [ ] **Step 4: Final clean commit（若有 lint/format 残余）**

```bash
uv run ruff check . && uv run ruff format .
cd frontend && pnpm lint --fix 2>/dev/null || true
cd /Users/pollochen/MyProjects/ArcReel/.worktrees/feat/resolution-param-refactor
git status
```

若有 uncommitted 修复，单独 commit：

```bash
git add -A
git commit -m "chore: post-refactor lint/format cleanup"
```

- [ ] **Step 5: 关联 issue 与推 PR（最终步骤，**仅在用户确认后执行**）**

```bash
git push -u origin feat/resolution-param-refactor
gh pr create --title "feat: 视频/图片分辨率参数重构 (#359)" --body "$(cat <<'EOF'
## Summary
- ModelInfo 新增 resolutions 字段；每个预置模型按实际支持填写
- CustomProviderModel 新增 resolution 列 + Alembic 迁移
- 新建 resolve_resolution：按 project → legacy → custom_default → None 解析
- 各 image/video backend 接受 Optional 分辨率参数，None 时不传 SDK
- 前端新建 ResolutionPicker 组件，Wizard Step2 / 项目设置 / 自定义供应商管理页三处接入
- project.json model_settings 复合 key（provider/model），legacy video_model_settings 在保存时自动迁移

Closes #359

## Test plan
- [ ] 后端全量 pytest 通过且覆盖率 ≥80%
- [ ] 前端 typecheck / vitest / build 全绿
- [ ] 手动创建项目 + 选分辨率 + 生成分镜验证
- [ ] 手动触发 legacy project 的迁移
EOF
)"
```

---

## Self-Review

**1. Spec coverage:**
- Spec §1.1 (ModelInfo resolutions) → Task 1 ✅
- Spec §1.2 (CustomProviderModel.resolution + Alembic) → Task 2 ✅
- Spec §1.3 (project.json model_settings 结构 + legacy 迁移) → Task 15, 18 ✅
- Spec §2 (resolve_resolution + 调用点改造) → Task 3, 13, 14 ✅
- Spec §3.1 (Request Optional) → Task 4 ✅
- Spec §3.2 (各 backend 不传语义 + OpenAI image _SIZE_MAP 重构 + Grok 移除映射) → Task 5-11 ✅
- Spec §4.1 (ResolutionPicker 组件) → Task 20 ✅
- Spec §4.2 (三处接入：自定义供应商管理、Wizard Step2、项目设置) → Task 21, 22, 23 ✅
- Spec §4.3 (API 数据流：providers.resolutions / custom_providers.resolution / projects.model_settings) → Task 16, 17, 18 ✅
- Spec §4.4 (i18n 键) → Task 21 ✅
- Spec §5.4 (SDK 必传性验证 + 兜底) → Task 24, 25 ✅
- Spec 回归风险点清单 → Task 13 (DEFAULT_VIDEO_RESOLUTION 移除)、Task 10 (_SIZE_MAP 重写)、Task 9 (Grok video #387)、Task 14 (reference_video_tasks)

**2. Placeholder scan:** 无 "TBD"/"TODO"/"similar to Task N"。Task 15 的 `save_project` 调用需按实际签名适配，但给出了 helper 完整实现。Task 6/7 的 backend 具体结构要求按"现有结构调整"—— 这是因为 Gemini/Ark 视频的 generate 函数内部不完全是一块 dict；步骤里已给出替换原则（"把 resolution 改为条件加入"）。

**3. Type consistency:**
- `resolve_resolution(project, provider_id, model_id, *, custom_default=None)` — Task 3, 13, 14 同一签名 ✅
- `image_size: str | None` / `resolution: str | None` 贯穿 Task 4-12 ✅
- 前端 `ModelSettingEntry = { resolution?: string | null }` 在 Task 19, 22, 23 一致 ✅
- `ResolutionPicker` Props 在 Task 20 定义、Task 21/22/23 使用一致 ✅
- `lookupResolutions` 返回 `{options, isCustom}`（Task 22）—— 在 Task 25 扩展为 `{options, isCustom, required}`，属增量扩展，无冲突 ✅

**4. Scope check:** 本计划单一方向（分辨率重构），所有 task 围绕同一 feature；不需再拆分。

执行顺序建议：Task 1 → 2 → 3 → 4 → 5-11（可并行批次）→ 12 → 13 → 14 → 15 → 16-18 → 19 → 20 → 21-23 → 24 → 25（条件）→ 26。
