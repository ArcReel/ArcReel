# 视频生成服务层实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提取通用视频生成服务层抽象，接入 Seedance 1.5 作为第二个视频供应商

**Architecture:** VideoBackend Protocol 定义通用接口，GeminiVideoBackend 从 GeminiClient 提取视频逻辑，SeedanceVideoBackend 封装火山方舟 Ark SDK。MediaGenerator 通过注入的 VideoBackend 调用，保留版本管理和用量追踪横切关注点。

**Tech Stack:** Python 3.12, SQLAlchemy async ORM, Alembic, volcengine-python-sdk[ark], google-genai

**Spec:** `docs/superpowers/specs/2026-03-16-video-service-layer-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `lib/video_backends/__init__.py` | 导出公共 API |
| Create | `lib/video_backends/base.py` | Protocol, dataclasses, VideoCapability enum |
| Create | `lib/video_backends/gemini.py` | GeminiVideoBackend — 提取自 GeminiClient |
| Create | `lib/video_backends/seedance.py` | SeedanceVideoBackend — Ark SDK 封装 |
| Create | `lib/video_backends/registry.py` | Backend 注册 + 工厂函数 |
| Create | `tests/test_video_backend_base.py` | base.py 单元测试 |
| Create | `tests/test_video_backend_gemini.py` | GeminiVideoBackend 单元测试 |
| Create | `tests/test_video_backend_seedance.py` | SeedanceVideoBackend 单元测试 |
| Create | `tests/test_video_backend_registry.py` | Registry 单元测试 |
| Modify | `lib/cost_calculator.py` | 新增 Seedance 计费策略 |
| Modify | `tests/test_cost_calculator.py` | 新增 Seedance 计费测试 |
| Modify | `lib/db/models/api_call.py` | 新增 provider/currency/usage_tokens 列 |
| Create | Alembic migration | 数据库迁移脚本 |
| Modify | `lib/db/repositories/usage_repo.py` | provider-aware 计费 + 按币种分组统计 |
| Modify | `lib/usage_tracker.py` | 传递 provider 参数 |
| Modify | `tests/test_usage_repo.py` | 新增多供应商用量测试 |
| Modify | `lib/system_config.py` | 新增 video_provider / ark_api_key / file_service_base_url 配置项 |
| Modify | `server/routers/system_config.py` | 配置 API 支持新字段 |
| Modify | `lib/media_generator.py` | 注入 VideoBackend，重命名 video_backend → _gemini_backend_type |
| Modify | `server/services/generation_tasks.py` | get_media_generator 读取项目配置创建 Backend |
| Modify | `server/routers/generate.py` | payload 中快照 provider + settings |
| Modify | `tests/test_media_generator_module.py` | 适配新签名 |
| Modify | `tests/test_generation_tasks_service.py` | 适配新流程 |

---

## Chunk 1: 核心接口 + Registry

### Task 1: VideoBackend Protocol 和数据类

**Files:**
- Create: `lib/video_backends/__init__.py`
- Create: `lib/video_backends/base.py`
- Create: `tests/test_video_backend_base.py`

- [ ] **Step 1: 编写 base.py 测试**

```python
# tests/test_video_backend_base.py
from pathlib import Path

from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)


class TestVideoCapability:
    def test_enum_values(self):
        assert VideoCapability.TEXT_TO_VIDEO == "text_to_video"
        assert VideoCapability.IMAGE_TO_VIDEO == "image_to_video"
        assert VideoCapability.GENERATE_AUDIO == "generate_audio"
        assert VideoCapability.NEGATIVE_PROMPT == "negative_prompt"
        assert VideoCapability.VIDEO_EXTEND == "video_extend"
        assert VideoCapability.SEED_CONTROL == "seed_control"
        assert VideoCapability.FLEX_TIER == "flex_tier"

    def test_enum_is_str(self):
        assert isinstance(VideoCapability.TEXT_TO_VIDEO, str)


class TestVideoGenerationRequest:
    def test_defaults(self):
        req = VideoGenerationRequest(prompt="test", output_path=Path("/tmp/out.mp4"))
        assert req.aspect_ratio == "9:16"
        assert req.duration_seconds == 5
        assert req.resolution == "1080p"
        assert req.start_image is None
        assert req.generate_audio is True
        assert req.negative_prompt is None
        assert req.service_tier == "default"
        assert req.seed is None

    def test_all_fields(self):
        req = VideoGenerationRequest(
            prompt="action",
            output_path=Path("/tmp/out.mp4"),
            aspect_ratio="16:9",
            duration_seconds=8,
            resolution="720p",
            start_image=Path("/tmp/frame.png"),
            generate_audio=False,
            negative_prompt="no music",
            service_tier="flex",
            seed=42,
        )
        assert req.duration_seconds == 8
        assert req.seed == 42
        assert req.service_tier == "flex"


class TestVideoGenerationResult:
    def test_required_fields(self):
        result = VideoGenerationResult(
            video_path=Path("/tmp/out.mp4"),
            provider="gemini",
            model="veo-3.1-generate-001",
            duration_seconds=8,
        )
        assert result.video_uri is None
        assert result.seed is None
        assert result.usage_tokens is None
        assert result.task_id is None

    def test_optional_fields(self):
        result = VideoGenerationResult(
            video_path=Path("/tmp/out.mp4"),
            provider="seedance",
            model="doubao-seedance-1-5-pro-251215",
            duration_seconds=5,
            video_uri="https://cdn.example.com/video.mp4",
            seed=58944,
            usage_tokens=246840,
            task_id="cgt-20250101",
        )
        assert result.usage_tokens == 246840
        assert result.task_id == "cgt-20250101"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_video_backend_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.video_backends'`

- [ ] **Step 3: 实现 base.py**

```python
# lib/video_backends/base.py
"""视频生成服务层核心接口定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol, Set


class VideoCapability(str, Enum):
    """视频后端支持的能力枚举。"""
    TEXT_TO_VIDEO = "text_to_video"
    IMAGE_TO_VIDEO = "image_to_video"
    GENERATE_AUDIO = "generate_audio"
    NEGATIVE_PROMPT = "negative_prompt"
    VIDEO_EXTEND = "video_extend"
    SEED_CONTROL = "seed_control"
    FLEX_TIER = "flex_tier"


@dataclass
class VideoGenerationRequest:
    """通用视频生成请求。各 Backend 忽略不支持的字段。"""
    prompt: str
    output_path: Path
    aspect_ratio: str = "9:16"
    duration_seconds: int = 5
    resolution: str = "1080p"
    start_image: Optional[Path] = None
    generate_audio: bool = True

    # Veo 特有
    negative_prompt: Optional[str] = None

    # Seedance 特有
    service_tier: str = "default"
    seed: Optional[int] = None


@dataclass
class VideoGenerationResult:
    """通用视频生成结果。"""
    video_path: Path
    provider: str
    model: str
    duration_seconds: int

    video_uri: Optional[str] = None
    seed: Optional[int] = None
    usage_tokens: Optional[int] = None
    task_id: Optional[str] = None


class VideoBackend(Protocol):
    """视频生成后端协议。"""

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> Set[VideoCapability]: ...

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult: ...
```

```python
# lib/video_backends/__init__.py
"""视频生成服务层公共 API。"""

from lib.video_backends.base import (
    VideoBackend,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)

__all__ = [
    "VideoBackend",
    "VideoCapability",
    "VideoGenerationRequest",
    "VideoGenerationResult",
]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_video_backend_base.py -v`
Expected: PASS — 所有 7 个测试通过

- [ ] **Step 5: 提交**

```bash
git add lib/video_backends/__init__.py lib/video_backends/base.py tests/test_video_backend_base.py
git commit -m "feat: add VideoBackend protocol and data classes"
```

---

### Task 2: Registry 模块

**Files:**
- Create: `lib/video_backends/registry.py`
- Create: `tests/test_video_backend_registry.py`

- [ ] **Step 1: 编写 registry 测试**

```python
# tests/test_video_backend_registry.py
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lib.video_backends.base import VideoCapability, VideoGenerationRequest, VideoGenerationResult
from lib.video_backends.registry import (
    register_backend,
    create_backend,
    get_registered_backends,
    _BACKEND_FACTORIES,
)


class _FakeBackend:
    name = "fake"
    capabilities = {VideoCapability.TEXT_TO_VIDEO}

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def generate(self, request):
        pass


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个测试前清空 registry，测试后恢复。"""
    saved = dict(_BACKEND_FACTORIES)
    _BACKEND_FACTORIES.clear()
    yield
    _BACKEND_FACTORIES.clear()
    _BACKEND_FACTORIES.update(saved)


class TestRegistry:
    def test_register_and_create(self):
        register_backend("fake", lambda **kw: _FakeBackend(**kw))
        backend = create_backend("fake", api_key="test-key")
        assert backend.name == "fake"
        assert backend.api_key == "test-key"

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown video backend"):
            create_backend("nonexistent")

    def test_get_registered_backends(self):
        register_backend("fake", lambda **kw: _FakeBackend(**kw))
        assert "fake" in get_registered_backends()

    def test_register_overwrites(self):
        register_backend("fake", lambda **kw: _FakeBackend(**kw))
        register_backend("fake", lambda **kw: _FakeBackend(api_key="overwritten"))
        backend = create_backend("fake")
        assert backend.api_key == "overwritten"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_video_backend_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 registry.py**

```python
# lib/video_backends/registry.py
"""视频后端注册与工厂。"""

from __future__ import annotations

from typing import Any, Callable

from lib.video_backends.base import VideoBackend

_BACKEND_FACTORIES: dict[str, Callable[..., VideoBackend]] = {}


def register_backend(name: str, factory: Callable[..., VideoBackend]) -> None:
    """注册一个视频后端工厂函数。"""
    _BACKEND_FACTORIES[name] = factory


def create_backend(name: str, **kwargs: Any) -> VideoBackend:
    """根据名称创建视频后端实例。"""
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"Unknown video backend: {name}")
    return _BACKEND_FACTORIES[name](**kwargs)


def get_registered_backends() -> list[str]:
    """返回所有已注册的后端名称。"""
    return list(_BACKEND_FACTORIES.keys())
```

- [ ] **Step 4: 更新 `__init__.py` 导出 registry**

在 `lib/video_backends/__init__.py` 追加导出：

```python
from lib.video_backends.registry import create_backend, get_registered_backends, register_backend

__all__ = [
    # ... 已有导出
    "create_backend",
    "get_registered_backends",
    "register_backend",
]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_video_backend_registry.py -v`
Expected: PASS — 所有 4 个测试通过

- [ ] **Step 6: 提交**

```bash
git add lib/video_backends/registry.py lib/video_backends/__init__.py tests/test_video_backend_registry.py
git commit -m "feat: add video backend registry"
```

---

## Chunk 2: CostCalculator + 数据库迁移

### Task 3: CostCalculator 新增 Seedance 计费

**Files:**
- Modify: `lib/cost_calculator.py`
- Modify: `tests/test_cost_calculator.py`

- [ ] **Step 1: 编写 Seedance 计费测试**

在 `tests/test_cost_calculator.py` 末尾追加：

```python
class TestSeedanceCost:
    def test_online_with_audio(self):
        calculator = CostCalculator()
        # 246840 tokens, 在线有声, 16.00 元/百万token
        amount, currency = calculator.calculate_seedance_video_cost(
            usage_tokens=246840,
            service_tier="default",
            generate_audio=True,
            model="doubao-seedance-1-5-pro-251215",
        )
        assert currency == "CNY"
        assert amount == pytest.approx(3.9494, rel=1e-3)

    def test_online_no_audio(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_seedance_video_cost(
            usage_tokens=246840,
            service_tier="default",
            generate_audio=False,
        )
        assert currency == "CNY"
        assert amount == pytest.approx(1.9747, rel=1e-3)

    def test_flex_with_audio(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_seedance_video_cost(
            usage_tokens=246840,
            service_tier="flex",
            generate_audio=True,
        )
        assert currency == "CNY"
        assert amount == pytest.approx(1.9747, rel=1e-3)

    def test_flex_no_audio(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_seedance_video_cost(
            usage_tokens=246840,
            service_tier="flex",
            generate_audio=False,
        )
        assert currency == "CNY"
        assert amount == pytest.approx(0.9874, rel=1e-3)

    def test_zero_tokens(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_seedance_video_cost(
            usage_tokens=0,
            service_tier="default",
            generate_audio=True,
        )
        assert amount == 0.0
        assert currency == "CNY"

    def test_unknown_model_uses_default(self):
        calculator = CostCalculator()
        amount, currency = calculator.calculate_seedance_video_cost(
            usage_tokens=1_000_000,
            service_tier="default",
            generate_audio=True,
            model="unknown-model",
        )
        # 应回退到默认模型费率
        assert currency == "CNY"
        assert amount == pytest.approx(16.0)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_cost_calculator.py::TestSeedanceCost -v`
Expected: FAIL — `AttributeError: 'CostCalculator' object has no attribute 'calculate_seedance_video_cost'`

- [ ] **Step 3: 实现 Seedance 计费**

在 `lib/cost_calculator.py` 的 `CostCalculator` 类中新增：

```python
    # Seedance 视频费用（元/百万 token），按 (service_tier, generate_audio) 查表
    SEEDANCE_VIDEO_COST = {
        "doubao-seedance-1-5-pro-251215": {
            ("default", True): 16.00,
            ("default", False): 8.00,
            ("flex", True): 8.00,
            ("flex", False): 4.00,
        },
    }

    DEFAULT_SEEDANCE_MODEL = "doubao-seedance-1-5-pro-251215"

    def calculate_seedance_video_cost(
        self,
        usage_tokens: int,
        service_tier: str = "default",
        generate_audio: bool = True,
        model: str | None = None,
    ) -> tuple[float, str]:
        """
        计算 Seedance 视频生成费用。

        Returns:
            (amount, currency) — 金额和币种 (CNY)
        """
        model = model or self.DEFAULT_SEEDANCE_MODEL
        model_costs = self.SEEDANCE_VIDEO_COST.get(
            model, self.SEEDANCE_VIDEO_COST[self.DEFAULT_SEEDANCE_MODEL]
        )
        key = (service_tier, generate_audio)
        price_per_million = model_costs.get(
            key,
            model_costs.get(("default", True), 16.00),
        )
        amount = usage_tokens / 1_000_000 * price_per_million
        return amount, "CNY"
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_cost_calculator.py -v`
Expected: PASS — 所有测试通过（原有 + 新增 6 个）

- [ ] **Step 5: 提交**

```bash
git add lib/cost_calculator.py tests/test_cost_calculator.py
git commit -m "feat: add Seedance video cost calculation (CNY/token)"
```

---

### Task 4: 数据库迁移 — api_calls 表新增列

**Files:**
- Modify: `lib/db/models/api_call.py`
- Create: Alembic migration script

- [ ] **Step 1: 修改 ApiCall 模型**

编辑 `lib/db/models/api_call.py`，做以下变更：

1. 将 `cost_usd` 重命名为 `cost_amount`
2. 新增 `currency`, `provider`, `usage_tokens` 列

```python
    # 替换 cost_usd 行:
    cost_amount: Mapped[float] = mapped_column(Float, server_default="0.0")
    currency: Mapped[str] = mapped_column(String, server_default="USD")
    provider: Mapped[str] = mapped_column(String, server_default="gemini")
    usage_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 2: 生成 Alembic 迁移脚本**

Run: `uv run alembic revision --autogenerate -m "add provider currency usage_tokens to api_calls"`

检查生成的迁移脚本，确认包含：
- `cost_usd` 列重命名为 `cost_amount`（用 `op.alter_column` 的 `new_column_name`）
- 新增 `currency` 列（String，server_default="USD"）
- 新增 `provider` 列（String，server_default="gemini"）
- 新增 `usage_tokens` 列（Integer，nullable=True）

> 注意：SQLite 不支持 `ALTER COLUMN RENAME`，需要用 `batch_alter_table` 上下文管理器。检查迁移脚本是否使用了 `with op.batch_alter_table("api_calls") as batch_op:` 的形式。如果 Alembic 没有自动生成重命名，需要手动编辑迁移脚本。

- [ ] **Step 2.5: 全局搜索 cost_usd 引用**

Run: `rg "cost_usd" --type py`

确认所有引用都在以下文件中，并在对应任务中更新：
- `lib/db/models/api_call.py` — 本任务已修改
- `lib/db/repositories/usage_repo.py` — Task 5 修改
- `tests/test_usage_repo.py` — Task 5 修改

若发现其他文件引用 `cost_usd`，在此步骤中一并标记并修复。

- [ ] **Step 3: 运行迁移**

Run: `uv run alembic upgrade head`
Expected: 成功执行，无报错

- [ ] **Step 4: 验证模型一致性**

Run: `python -m pytest tests/test_db_models.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add lib/db/models/api_call.py alembic/versions/*.py
git commit -m "migrate: add provider, currency, usage_tokens to api_calls; rename cost_usd to cost_amount"
```

---

### Task 5: UsageRepository + UsageTracker 多供应商适配

**Files:**
- Modify: `lib/db/repositories/usage_repo.py`
- Modify: `lib/usage_tracker.py`
- Modify: `tests/test_usage_repo.py`

- [ ] **Step 1: 编写多供应商用量测试**

在 `tests/test_usage_repo.py` 追加：

```python
class TestMultiProviderUsage:
    async def test_seedance_call_records_provider_and_tokens(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="doubao-seedance-1-5-pro-251215",
            prompt="test",
            resolution="1080p",
            duration_seconds=5,
            generate_audio=True,
            provider="seedance",
        )

        await repo.finish_call(
            call_id,
            status="success",
            usage_tokens=246840,
            provider="seedance",
            service_tier="default",
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["provider"] == "seedance"
        assert item["currency"] == "CNY"
        assert item["usage_tokens"] == 246840
        assert item["cost_amount"] == pytest.approx(3.9494, rel=1e-3)

    async def test_gemini_call_defaults_to_usd(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="veo-3.1-generate-001",
            resolution="1080p",
            duration_seconds=8,
            generate_audio=True,
        )
        await repo.finish_call(call_id, status="success")

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["provider"] == "gemini"
        assert item["currency"] == "USD"
        assert item["cost_amount"] == pytest.approx(3.2)

    async def test_get_stats_groups_by_currency(self, db_session):
        repo = UsageRepository(db_session)

        # Gemini call
        c1 = await repo.start_call(
            project_name="demo", call_type="video",
            model="veo-3.1-generate-001", duration_seconds=8,
            resolution="1080p", generate_audio=True,
        )
        await repo.finish_call(c1, status="success")

        # Seedance call
        c2 = await repo.start_call(
            project_name="demo", call_type="video",
            model="doubao-seedance-1-5-pro-251215", duration_seconds=5,
            resolution="1080p", generate_audio=True, provider="seedance",
        )
        await repo.finish_call(c2, status="success", usage_tokens=246840, provider="seedance", service_tier="default")

        stats = await repo.get_stats(project_name="demo")
        assert stats["total_count"] == 2
        # 按币种分组的费用
        assert "cost_by_currency" in stats
        assert stats["cost_by_currency"]["USD"] == pytest.approx(3.2)
        assert stats["cost_by_currency"]["CNY"] == pytest.approx(3.9494, rel=1e-3)
        # 保留 total_cost 向后兼容（仅 USD）
        assert stats["total_cost"] == pytest.approx(3.2)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_usage_repo.py::TestMultiProviderUsage -v`
Expected: FAIL

- [ ] **Step 3: 修改 UsageRepository**

更新 `lib/db/repositories/usage_repo.py`:

1. `_row_to_dict`: 将 `cost_usd` 改为 `cost_amount`，同时保留 `"cost_usd": row.cost_amount` 别名以向后兼容前端，新增 `currency`、`provider`、`usage_tokens` 字段
2. `start_call`: 新增 `provider: str = "gemini"` 参数，写入 `ApiCall.provider`
3. `finish_call`: 新增 `usage_tokens`、`provider`、`service_tier` 可选参数；根据 provider 分派计费逻辑：
   - `provider == "gemini"` → 现有 `cost_calculator.calculate_video_cost()`，`currency = "USD"`
   - `provider == "seedance"` → `cost_calculator.calculate_seedance_video_cost()`，`currency = "CNY"`
   - 更新时写入 `cost_amount`、`currency`、`usage_tokens`
4. `get_stats`: 新增 `cost_by_currency` 字段（按 `currency` 分组求和），保留 `total_cost`（仅 USD 部分）向后兼容

- [ ] **Step 4: 修改 UsageTracker 传递 provider**

更新 `lib/usage_tracker.py` 的 `start_call` 和 `finish_call` 方法签名，透传 `provider`、`usage_tokens`、`service_tier` 参数到 `UsageRepository`。

- [ ] **Step 5: 运行全部用量测试**

Run: `python -m pytest tests/test_usage_repo.py tests/test_usage_tracker.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add lib/db/repositories/usage_repo.py lib/usage_tracker.py tests/test_usage_repo.py
git commit -m "feat: multi-provider usage tracking with currency-aware cost calculation"
```

---

## Chunk 3: GeminiVideoBackend

### Task 6: 从 GeminiClient 提取视频逻辑

**Files:**
- Create: `lib/video_backends/gemini.py`
- Create: `tests/test_video_backend_gemini.py`

- [ ] **Step 1: 编写 GeminiVideoBackend 测试**

```python
# tests/test_video_backend_gemini.py
"""GeminiVideoBackend 单元测试 — mock genai SDK。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.video_backends.base import VideoCapability, VideoGenerationRequest, VideoGenerationResult
from lib.video_backends.gemini import GeminiVideoBackend


@pytest.fixture
def mock_rate_limiter():
    rl = MagicMock()
    rl.acquire = MagicMock()
    return rl


@pytest.fixture
def backend(mock_rate_limiter):
    """创建 aistudio 模式的 GeminiVideoBackend，mock genai client。"""
    with patch("lib.video_backends.gemini.genai") as mock_genai:
        b = GeminiVideoBackend(
            backend_type="aistudio",
            api_key="test-key",
            rate_limiter=mock_rate_limiter,
        )
        # 替换内部 client 为 mock
        b._client = MagicMock()
        b._client.aio = MagicMock()
        yield b


class TestGeminiVideoBackendProperties:
    def test_name(self, backend):
        assert backend.name == "gemini"

    def test_capabilities_aistudio(self, backend):
        caps = backend.capabilities
        assert VideoCapability.TEXT_TO_VIDEO in caps
        assert VideoCapability.IMAGE_TO_VIDEO in caps
        assert VideoCapability.NEGATIVE_PROMPT in caps
        assert VideoCapability.VIDEO_EXTEND in caps
        # aistudio 不支持 generate_audio 控制
        assert VideoCapability.GENERATE_AUDIO not in caps

    def test_capabilities_vertex(self, mock_rate_limiter):
        with patch("lib.video_backends.gemini.genai"):
            b = GeminiVideoBackend(
                backend_type="vertex",
                rate_limiter=mock_rate_limiter,
            )
            assert VideoCapability.GENERATE_AUDIO in b.capabilities


class TestGeminiVideoBackendGenerate:
    async def test_generate_text_to_video(self, backend, tmp_path):
        """文生视频：无 start_image。"""
        output = tmp_path / "out.mp4"

        # Mock operation 链
        mock_video = MagicMock()
        mock_video.uri = "gs://bucket/video.mp4"
        mock_video.video_bytes = b"fake-video-bytes"
        mock_generated = MagicMock()
        mock_generated.video = mock_video

        mock_response = MagicMock()
        mock_response.generated_videos = [mock_generated]

        mock_op = MagicMock()
        mock_op.done = True
        mock_op.response = mock_response
        mock_op.error = None

        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)
        backend._client.aio.operations.get = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="a cat",
            output_path=output,
            duration_seconds=8,
            negative_prompt="no music",
        )

        result = await backend.generate(request)

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "gemini"
        assert result.video_uri == "gs://bucket/video.mp4"
        assert result.video_path == output

    async def test_generate_image_to_video(self, backend, tmp_path):
        """图生视频：有 start_image。"""
        output = tmp_path / "out.mp4"
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"fake-png")

        mock_video = MagicMock()
        mock_video.uri = None
        mock_video.video_bytes = b"fake-video-bytes"
        mock_generated = MagicMock()
        mock_generated.video = mock_video
        mock_response = MagicMock()
        mock_response.generated_videos = [mock_generated]
        mock_op = MagicMock()
        mock_op.done = True
        mock_op.response = mock_response
        mock_op.error = None

        backend._client.aio.models.generate_videos = AsyncMock(return_value=mock_op)

        request = VideoGenerationRequest(
            prompt="cat moves",
            output_path=output,
            start_image=frame,
        )

        result = await backend.generate(request)
        assert result.provider == "gemini"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_video_backend_gemini.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.video_backends.gemini'`

- [ ] **Step 3: 实现 GeminiVideoBackend**

创建 `lib/video_backends/gemini.py`，从 `lib/gemini_client.py` 提取视频生成逻辑：

核心提取清单：
- `generate_video` / `generate_video_async` 的**生成模式**逻辑（非延长模式，延长本次不做为 generate 接口的一部分）
- `_prepare_image_param`（起始帧处理）
- `_download_video`（视频下载）
- 客户端初始化逻辑（aistudio/vertex 双后端）
- `normalize_veo_duration_seconds` 引用（从 `generation_tasks.py` 导入或内联）

关键实现要点：
- `__init__` 接受 `backend_type`, `api_key`, `rate_limiter`, `video_model` 参数
- `generate()` 方法为 async，内部使用 genai SDK 的 aio API
- duration_seconds 内部标准化为 str（"4"/"6"/"8"）
- 使用 `with_retry_async` 装饰器复用现有重试逻辑
- 轮询逻辑复用现有模式（poll_interval=10, max_wait_time=600）
- 结果写入 `request.output_path` 并返回 `VideoGenerationResult`

从 `gemini_client.py` 提取的具体方法和行范围：
- **客户端初始化** (`gemini_client.py:__init__` 中 backend/client/types 逻辑) → `GeminiVideoBackend.__init__`
- **生成模式逻辑** (`gemini_client.py:887-911`) → `generate()` 中构建 config + source + 调用 API
- **轮询等待逻辑** (`gemini_client.py:913-929`) → `generate()` 中 async 轮询循环
- **结果处理+下载** (`gemini_client.py:931-965`) → `generate()` 中下载视频到 output_path
- **`_prepare_image_param`** (`gemini_client.py:1339-1376`) → 内部方法，处理 start_image 为 PIL Image
- **`_download_video`** → 内部方法，从 genai Video 对象下载到文件
- **config 构建** (`gemini_client.py:967-1000` 的 `_prepare_video_generate_config`) → 内联到 `generate()`

`generate()` 方法骨架：

```python
async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
    # 1. 限流
    if self._rate_limiter:
        self._rate_limiter.acquire(self._video_model)

    # 2. 标准化 duration
    duration_str = self._normalize_duration(request.duration_seconds)

    # 3. 构建 config (aspect_ratio, resolution, duration, negative_prompt, generate_audio)
    config = self._build_generate_config(request, duration_str)

    # 4. 准备 source (prompt + optional start_image)
    image_param = self._prepare_image_param(request.start_image) if request.start_image else None
    source = self._types.GenerateVideosSource(prompt=request.prompt, image=image_param)

    # 5. 调用 API + 异步轮询
    operation = await self._client.aio.models.generate_videos(
        model=self._video_model, source=source, config=config
    )
    operation = await self._poll_until_done(operation)

    # 6. 提取结果 + 下载
    generated = operation.response.generated_videos[0]
    video_ref = generated.video
    await self._save_video(video_ref, request.output_path)

    return VideoGenerationResult(
        video_path=request.output_path,
        provider="gemini",
        model=self._video_model,
        duration_seconds=request.duration_seconds,
        video_uri=video_ref.uri if video_ref else None,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_video_backend_gemini.py -v`
Expected: PASS

- [ ] **Step 5: 运行全量测试确认无回归**

Run: `python -m pytest -x -q`
Expected: PASS — 无回归

- [ ] **Step 6: 提交**

```bash
git add lib/video_backends/gemini.py tests/test_video_backend_gemini.py
git commit -m "feat: extract GeminiVideoBackend from GeminiClient"
```

---

## Chunk 4: SeedanceVideoBackend

### Task 7: Seedance 后端实现

**Files:**
- Create: `lib/video_backends/seedance.py`
- Create: `tests/test_video_backend_seedance.py`

- [ ] **Step 1: 安装 Seedance SDK 依赖**

Run: `uv add 'volcengine-python-sdk[ark]'`

- [ ] **Step 2: 编写 SeedanceVideoBackend 测试**

```python
# tests/test_video_backend_seedance.py
"""SeedanceVideoBackend 单元测试 — mock Ark SDK。"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from lib.video_backends.base import VideoCapability, VideoGenerationRequest, VideoGenerationResult
from lib.video_backends.seedance import SeedanceVideoBackend


@pytest.fixture
def mock_ark_client():
    client = MagicMock()
    client.content_generation = MagicMock()
    client.content_generation.tasks = MagicMock()
    return client


@pytest.fixture
def backend(mock_ark_client):
    with patch("lib.video_backends.seedance.Ark", return_value=mock_ark_client):
        b = SeedanceVideoBackend(
            api_key="test-ark-key",
            file_service_base_url="https://example.com",
        )
    b._client = mock_ark_client
    return b


class TestSeedanceProperties:
    def test_name(self, backend):
        assert backend.name == "seedance"

    def test_capabilities(self, backend):
        caps = backend.capabilities
        assert VideoCapability.TEXT_TO_VIDEO in caps
        assert VideoCapability.IMAGE_TO_VIDEO in caps
        assert VideoCapability.GENERATE_AUDIO in caps
        assert VideoCapability.SEED_CONTROL in caps
        assert VideoCapability.FLEX_TIER in caps
        assert VideoCapability.NEGATIVE_PROMPT not in caps


class TestSeedanceGenerate:
    async def test_text_to_video(self, backend, tmp_path):
        """文生视频：无 start_image。"""
        output = tmp_path / "out.mp4"

        # Mock create → 返回 task_id
        create_result = MagicMock()
        create_result.id = "cgt-20250101-test"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        # Mock get → 第一次 running，第二次 succeeded
        get_result_running = MagicMock()
        get_result_running.status = "running"

        get_result_done = MagicMock()
        get_result_done.status = "succeeded"
        get_result_done.content = MagicMock()
        get_result_done.content.video_url = "https://cdn.example.com/video.mp4"
        get_result_done.seed = 58944
        get_result_done.usage = MagicMock()
        get_result_done.usage.completion_tokens = 246840

        backend._client.content_generation.tasks.get = MagicMock(
            side_effect=[get_result_running, get_result_done]
        )

        # Mock 视频下载
        with patch("lib.video_backends.seedance.httpx") as mock_httpx:
            mock_response = MagicMock()
            mock_response.content = b"fake-mp4-data"
            mock_response.raise_for_status = MagicMock()
            mock_httpx.get = MagicMock(return_value=mock_response)

            request = VideoGenerationRequest(
                prompt="日落海滩",
                output_path=output,
                duration_seconds=5,
                aspect_ratio="16:9",
                resolution="1080p",
                generate_audio=True,
            )

            # 使用 0 间隔加速测试
            result = await backend.generate(request, _poll_interval_override=0)

        assert isinstance(result, VideoGenerationResult)
        assert result.provider == "seedance"
        assert result.model == "doubao-seedance-1-5-pro-251215"
        assert result.task_id == "cgt-20250101-test"
        assert result.seed == 58944
        assert result.usage_tokens == 246840
        assert result.video_uri == "https://cdn.example.com/video.mp4"
        assert output.read_bytes() == b"fake-mp4-data"

    async def test_image_to_video_uploads_image(self, backend, tmp_path):
        """图生视频：start_image 需要先上传获取 URL。"""
        output = tmp_path / "out.mp4"
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"fake-png")

        # Mock 文件上传
        with patch.object(backend, "_upload_image", new_callable=AsyncMock) as mock_upload:
            mock_upload.return_value = "https://example.com/files/frame.png"

            create_result = MagicMock()
            create_result.id = "cgt-test-i2v"
            backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

            get_result = MagicMock()
            get_result.status = "succeeded"
            get_result.content = MagicMock()
            get_result.content.video_url = "https://cdn.example.com/video.mp4"
            get_result.seed = 100
            get_result.usage = MagicMock()
            get_result.usage.completion_tokens = 100000
            backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

            with patch("lib.video_backends.seedance.httpx") as mock_httpx:
                mock_resp = MagicMock()
                mock_resp.content = b"video-data"
                mock_resp.raise_for_status = MagicMock()
                mock_httpx.get = MagicMock(return_value=mock_resp)

                request = VideoGenerationRequest(
                    prompt="girl opens eyes",
                    output_path=output,
                    start_image=frame,
                )
                result = await backend.generate(request, _poll_interval_override=0)

            mock_upload.assert_called_once_with(frame)

            # 验证 create 调用中包含 image_url
            create_call = backend._client.content_generation.tasks.create.call_args
            content = create_call.kwargs.get("content") or create_call[1].get("content")
            image_items = [c for c in content if c.get("type") == "image_url"]
            assert len(image_items) == 1
            assert image_items[0]["image_url"]["url"] == "https://example.com/files/frame.png"

    async def test_failed_task_raises(self, backend, tmp_path):
        """任务失败应抛出异常。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-fail"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "failed"
        get_result.error = "content policy violation"
        backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        request = VideoGenerationRequest(prompt="test", output_path=output)

        with pytest.raises(RuntimeError, match="content policy violation"):
            await backend.generate(request, _poll_interval_override=0)

    async def test_expired_task_raises(self, backend, tmp_path):
        """expired 状态应抛出异常。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-expired"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "expired"
        get_result.error = "task expired"
        backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        request = VideoGenerationRequest(prompt="test", output_path=output)

        with pytest.raises(RuntimeError, match="expired"):
            await backend.generate(request, _poll_interval_override=0)

    async def test_timeout_raises(self, backend, tmp_path):
        """超时应抛出 TimeoutError。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-timeout"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        # 始终返回 running
        get_result = MagicMock()
        get_result.status = "running"
        backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        request = VideoGenerationRequest(prompt="test", output_path=output)

        with pytest.raises(TimeoutError):
            await backend.generate(request, _poll_interval_override=0, _max_wait_override=0)

    async def test_flex_tier_passed_to_api(self, backend, tmp_path):
        """service_tier=flex 应传入 API。"""
        output = tmp_path / "out.mp4"

        create_result = MagicMock()
        create_result.id = "cgt-flex"
        backend._client.content_generation.tasks.create = MagicMock(return_value=create_result)

        get_result = MagicMock()
        get_result.status = "succeeded"
        get_result.content = MagicMock()
        get_result.content.video_url = "https://cdn.example.com/v.mp4"
        get_result.seed = 1
        get_result.usage = MagicMock()
        get_result.usage.completion_tokens = 100
        backend._client.content_generation.tasks.get = MagicMock(return_value=get_result)

        with patch("lib.video_backends.seedance.httpx") as mock_httpx:
            mock_resp = MagicMock()
            mock_resp.content = b"data"
            mock_resp.raise_for_status = MagicMock()
            mock_httpx.get = MagicMock(return_value=mock_resp)

            request = VideoGenerationRequest(
                prompt="test",
                output_path=output,
                service_tier="flex",
            )
            await backend.generate(request, _poll_interval_override=0)

        create_call = backend._client.content_generation.tasks.create.call_args
        assert create_call.kwargs.get("service_tier") == "flex"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/test_video_backend_seedance.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: 实现 SeedanceVideoBackend**

创建 `lib/video_backends/seedance.py`：

核心实现要点：
- `__init__`: 初始化 `Ark` 客户端（`base_url`, `api_key`），存储 `model`, `file_service_base_url`
- `generate()`:
  1. 构建 `content` 列表：`[{"type": "text", "text": prompt}]`
  2. 若有 `start_image`：调用 `_upload_image()` 获取 URL，追加 `{"type": "image_url", "image_url": {"url": url}}`
  3. 调用 `client.content_generation.tasks.create(model=..., content=..., ratio=aspect_ratio, duration=duration_seconds, resolution=resolution, generate_audio=..., watermark=False, service_tier=..., seed=...)`
  4. 轮询 `tasks.get(task_id=...)` 直到 `succeeded`/`failed`/`expired`
  5. 下载 MP4 到 `output_path`（使用 `httpx.get(video_url)`）
  6. 返回 `VideoGenerationResult`
- `_upload_image(path)`: POST 到 `{file_service_base_url}/api/v1/files/upload` 获取公网 URL
- 轮询间隔：`default` → 10s，`flex` → 60s
- 超时：`default` → 600s，`flex` → 172800s
- `generate` 接受可选 `_poll_interval_override` 参数供测试用

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_video_backend_seedance.py -v`
Expected: PASS

- [ ] **Step 6: 注册 Backend**

在 `lib/video_backends/__init__.py` 追加自动注册。**使用惰性导入**避免未安装 Seedance SDK 时 import 失败：

```python
from lib.video_backends.registry import register_backend


def _create_gemini_backend(**kwargs):
    from lib.video_backends.gemini import GeminiVideoBackend
    return GeminiVideoBackend(**kwargs)


def _create_seedance_backend(**kwargs):
    from lib.video_backends.seedance import SeedanceVideoBackend
    return SeedanceVideoBackend(**kwargs)


register_backend("gemini", _create_gemini_backend)

try:
    import volcenginesdkarkruntime  # noqa: F401
    register_backend("seedance", _create_seedance_backend)
except ImportError:
    pass  # Seedance SDK 未安装时跳过注册
```

- [ ] **Step 7: 提交**

```bash
git add lib/video_backends/seedance.py lib/video_backends/__init__.py tests/test_video_backend_seedance.py
git commit -m "feat: implement SeedanceVideoBackend with Ark SDK"
```

---

## Chunk 5: 配置层 + 集成

### Task 8: SystemConfigManager 新增配置项

**Files:**
- Modify: `lib/system_config.py`
- Modify: `server/routers/system_config.py`

- [ ] **Step 1: 修改 SystemConfigManager**

在 `lib/system_config.py` 中：

1. `_ENV_KEYS` 元组新增：`"DEFAULT_VIDEO_PROVIDER"`, `"ARK_API_KEY"`, `"FILE_SERVICE_BASE_URL"`
2. `_apply_to_env` 方法新增对应的环境变量映射：

```python
        # Video provider
        if "video_provider" in overrides:
            self._set_env("DEFAULT_VIDEO_PROVIDER", overrides.get("video_provider"))
        else:
            self._restore_or_unset("DEFAULT_VIDEO_PROVIDER")

        # Ark API key (Seedance)
        if "ark_api_key" in overrides:
            self._set_env("ARK_API_KEY", overrides.get("ark_api_key"))
        else:
            self._restore_or_unset("ARK_API_KEY")

        # File service base URL
        if "file_service_base_url" in overrides:
            self._set_env("FILE_SERVICE_BASE_URL", overrides.get("file_service_base_url"))
        else:
            self._restore_or_unset("FILE_SERVICE_BASE_URL")
```

- [ ] **Step 2: 修改 system_config router**

在 `server/routers/system_config.py` 的 GET 端点响应中新增这三个字段的读取和返回逻辑，遵循现有模式（读环境变量、mask 密钥、标记 source）。

PATCH 端点的验证规则：
- `video_provider`: 必须是 `"gemini"` 或 `"seedance"` 之一
- `ark_api_key`: 字符串，无特殊验证
- `file_service_base_url`: 字符串，无特殊验证

- [ ] **Step 3: 运行现有系统配置测试**

Run: `python -m pytest tests/test_system_config.py tests/test_system_config_router.py -v`
Expected: PASS — 确认无回归

- [ ] **Step 4: 提交**

```bash
git add lib/system_config.py server/routers/system_config.py
git commit -m "feat: add video_provider, ark_api_key, file_service_base_url to system config"
```

---

### Task 9: MediaGenerator 适配 VideoBackend

**Files:**
- Modify: `lib/media_generator.py`
- Modify: `tests/test_media_generator_module.py`

- [ ] **Step 1: 修改 MediaGenerator**

编辑 `lib/media_generator.py`：

1. 将 `self.video_backend`（str）重命名为 `self._gemini_backend_type`
2. 新增 `video_backend: VideoBackend | None = None` 构造参数
3. 存储为 `self._video_backend`
4. 修改 `generate_video_async()`：
   - 如果 `self._video_backend` 不为 None → 构造 `VideoGenerationRequest`，调用 `self._video_backend.generate(request)`，从 `VideoGenerationResult` 提取返回值
   - 如果为 None → 保留原有 GeminiClient 调用逻辑（向后兼容）
5. 同样修改同步 `generate_video()` 方法
6. UsageTracker.start_call 新增 `provider` 参数（从 backend.name 获取）
7. UsageTracker.finish_call 新增 `usage_tokens`、`provider`、`service_tier` 参数（从 result 和 request 获取）

- [ ] **Step 2: 更新 MediaGenerator 测试**

在 `tests/test_media_generator_module.py` 中更新受影响的测试（主要是 `video_backend` 属性重命名为 `_gemini_backend_type` 的引用），并新增一个测试验证注入 VideoBackend 时的行为。

- [ ] **Step 3: 运行 MediaGenerator 测试**

Run: `python -m pytest tests/test_media_generator_module.py -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add lib/media_generator.py tests/test_media_generator_module.py
git commit -m "feat: MediaGenerator accepts injected VideoBackend"
```

---

### Task 10: 任务执行层集成

**Files:**
- Modify: `server/services/generation_tasks.py`
- Modify: `server/routers/generate.py`
- Modify: `tests/test_generation_tasks_service.py`

- [ ] **Step 1: 修改 get_media_generator 读取项目配置**

编辑 `server/services/generation_tasks.py` 的 `get_media_generator()`：

```python
def get_media_generator(project_name: str, payload: dict | None = None) -> MediaGenerator:
    project_path = get_project_manager().get_project_path(project_name)

    # 确定视频供应商：payload 快照 > project.json > 全局默认
    provider_name = None
    provider_settings = {}

    if payload:
        provider_name = payload.get("video_provider")
        provider_settings = payload.get("video_provider_settings", {})

    if not provider_name:
        project = get_project_manager().load_project(project_name)
        provider_name = project.get("video_provider")
        if not provider_name:
            provider_name = os.environ.get("DEFAULT_VIDEO_PROVIDER", "gemini")
        provider_settings = project.get("video_provider_settings", {}).get(provider_name, {})

    # 创建 VideoBackend
    video_backend = _create_video_backend(provider_name, provider_settings)

    return MediaGenerator(project_path, rate_limiter=rate_limiter, video_backend=video_backend)
```

新增辅助函数 `_create_video_backend()`：

```python
def _create_video_backend(provider_name: str, provider_settings: dict) -> VideoBackend:
    from lib.video_backends import create_backend

    if provider_name == "gemini":
        backend_type = (os.environ.get("GEMINI_VIDEO_BACKEND") or "aistudio").strip().lower()
        return create_backend(
            "gemini",
            backend_type=backend_type,
            api_key=os.environ.get("GEMINI_API_KEY"),
            rate_limiter=rate_limiter,
            video_model=os.environ.get("GEMINI_VIDEO_MODEL", "veo-3.1-generate-001"),
        )
    elif provider_name == "seedance":
        return create_backend(
            "seedance",
            api_key=os.environ.get("ARK_API_KEY"),
            file_service_base_url=os.environ.get("FILE_SERVICE_BASE_URL", ""),
            model=provider_settings.get("model", "doubao-seedance-1-5-pro-251215"),
        )
    else:
        raise ValueError(f"Unknown video provider: {provider_name}")
```

- [ ] **Step 2: 修改 execute_video_task 适配新接口**

编辑 `execute_video_task()`：

1. 从 `payload` 读取 `video_provider` 和 `video_provider_settings`
2. 传入 `get_media_generator(project_name, payload)`
3. duration_seconds 不再在此处调用 `normalize_veo_duration_seconds()`，改为直接传 int（Backend 内部标准化）
4. 从 `generate_video_async` 返回值适配（如果用了 VideoBackend 路径，返回 `VideoGenerationResult`）

- [ ] **Step 3: 修改 generate.py 入队时快照配置**

编辑 `server/routers/generate.py` 的 `generate_video()` 端点：

在 `payload` 中追加 provider 和 settings 快照：

```python
        project = get_project_manager().load_project(project_name)
        video_provider = project.get("video_provider") or os.environ.get("DEFAULT_VIDEO_PROVIDER", "gemini")
        video_settings = project.get("video_settings", {})
        video_provider_settings = project.get("video_provider_settings", {}).get(video_provider, {})

        result = await queue.enqueue_task(
            ...
            payload={
                "prompt": req.prompt,
                "script_file": req.script_file,
                "duration_seconds": req.duration_seconds,
                "seed": getattr(req, "seed", None),
                "video_provider": video_provider,
                "video_settings": video_settings,
                "video_provider_settings": video_provider_settings,
            },
            ...
        )
```

同时更新 `GenerateVideoRequest` 模型，新增可选 `seed` 字段：

```python
class GenerateVideoRequest(BaseModel):
    prompt: Union[str, dict]
    script_file: str
    duration_seconds: Optional[int] = 4
    seed: Optional[int] = None
```

- [ ] **Step 4: 更新集成测试**

修改 `tests/test_generation_tasks_service.py` 中 `execute_video_task` 相关测试以适配新参数。

- [ ] **Step 5: 运行全量测试**

Run: `python -m pytest -x -q`
Expected: PASS — 所有测试通过

- [ ] **Step 6: 提交**

```bash
git add server/services/generation_tasks.py server/routers/generate.py tests/test_generation_tasks_service.py
git commit -m "feat: integrate VideoBackend into task execution pipeline"
```

---

## Chunk 6: GeminiClient 废弃标记 + 文档

### Task 11: GeminiClient 视频方法废弃标记

**Files:**
- Modify: `lib/gemini_client.py`

- [ ] **Step 1: 在 GeminiClient 的视频方法上添加废弃标记**

在 `generate_video()` 和 `generate_video_async()` 方法上添加 `warnings.warn` 废弃警告：

```python
import warnings

def generate_video(self, ...):
    warnings.warn(
        "GeminiClient.generate_video() is deprecated, use GeminiVideoBackend.generate() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    # 保留原有实现不变
    ...
```

- [ ] **Step 2: 运行全量测试确认无回归**

Run: `python -m pytest -x -q`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add lib/gemini_client.py
git commit -m "chore: deprecate GeminiClient video methods in favor of GeminiVideoBackend"
```

---

### Task 12: 更新 .env.example 和依赖

**Files:**
- Modify: `.env.example`
- Modify: `pyproject.toml` (if `uv add` didn't already)

- [ ] **Step 1: 更新 .env.example**

追加以下注释和配置项：

```bash
# === 视频供应商 ===
# DEFAULT_VIDEO_PROVIDER=gemini     # 全局默认视频供应商 (gemini | seedance)

# === Seedance (火山方舟) ===
# ARK_API_KEY=                      # 火山方舟 API key
# FILE_SERVICE_BASE_URL=            # 项目文件服务公网地址 (Seedance 图片上传需要公网访问)
```

- [ ] **Step 2: 确认依赖已添加**

Run: `uv sync && python -c "from volcenginesdkarkruntime import Ark; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 3: 运行全量测试最终确认**

Run: `python -m pytest -x -q`
Expected: PASS — 所有测试通过

- [ ] **Step 4: 提交**

```bash
git add .env.example pyproject.toml uv.lock
git commit -m "chore: add Seedance SDK dependency and env config documentation"
```
