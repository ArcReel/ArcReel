# Text Generation Cost Calculation and Usage Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Include text generation (script, overview, style analysis) API calls in the cost calculation and usage tracking system.

**Architecture:** Create a `TextGenerator` wrapper layer (similar to `MediaGenerator`), combining `TextBackend` + `UsageTracker`. Add `input_tokens` / `output_tokens` columns to the database, add a text cost calculation branch to `UsageRepository`. Add text type display on the frontend.

**Tech Stack:** Python / SQLAlchemy / Alembic / FastAPI / React / TypeScript / Zustand

---

## File Structure

### New Files
- `lib/text_generator.py` — TextGenerator 包装层，组合 TextBackend + UsageTracker
- `tests/test_text_generator.py` — TextGenerator 单元测试
- `alembic/versions/xxxx_add_input_output_tokens.py` — 数据库迁移（Alembic autogenerate）

### Modified Files
- `lib/db/models/api_call.py` — 新增 `input_tokens` / `output_tokens` 列
- `lib/db/repositories/usage_repo.py` — `finish_call()` 增加 text 成本计算；`get_stats()` 增加 `text_count`；`_row_to_dict()` 增加新字段
- `lib/usage_tracker.py` — `finish_call()` 透传 `input_tokens` / `output_tokens`
- `lib/script_generator.py` — 改用 `TextGenerator`
- `lib/project_manager.py` — `generate_overview()` 改用 `TextGenerator`
- `server/routers/files.py` — 风格分析改用 `TextGenerator`
- `tests/test_usage_repo.py` — 新增 text call 测试
- `tests/test_usage_tracker.py` — 新增 text call 测试
- `frontend/src/stores/usage-store.ts` — `UsageStats` 增加 `text_count`；`UsageCall` 增加 `input_tokens` / `output_tokens`
- `frontend/src/types/provider.ts` — `UsageStat` 类型无需改动（已有 `call_type` 字段，text 自然出现）
- `frontend/src/components/layout/UsageDrawer.tsx` — 增加 text 类型图标和 token 信息展示
- `frontend/src/components/layout/GlobalHeader.tsx` — `usageStats` 类型增加 `text_count`
- `frontend/src/components/pages/settings/UsageStatsSection.tsx` — text 类型卡片展示 token 信息代替时长

---

### Task 1: 数据库模型 + 迁移

**Files:**
- Modify: `lib/db/models/api_call.py:37` (在 `usage_tokens` 后新增两列)
- Create: `alembic/versions/xxxx_add_input_output_tokens.py` (autogenerate)

- [ ] **Step 1: 修改 ApiCall 模型，新增 `input_tokens` 和 `output_tokens` 列**

在 `lib/db/models/api_call.py` 的 `usage_tokens` 行之后、`__table_args__` 之前新增：

```python
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 2: 生成 Alembic 迁移**

Run: `uv run alembic revision --autogenerate -m "add input_tokens and output_tokens to api_calls"`
Expected: 生成新迁移文件，包含两个 `add_column` 操作

- [ ] **Step 3: 执行迁移**

Run: `uv run alembic upgrade head`
Expected: 无报错

- [ ] **Step 4: Commit**

```bash
git add lib/db/models/api_call.py alembic/versions/*input_output_tokens*
git commit -m "feat: add input_tokens and output_tokens columns to api_calls"
```

---

### Task 2: UsageRepository 支持 text 成本计算

**Files:**
- Test: `tests/test_usage_repo.py`
- Modify: `lib/db/repositories/usage_repo.py`

- [ ] **Step 1: 写失败测试 — text call 的 start + finish + 成本计算**

在 `tests/test_usage_repo.py` 的 `TestMultiProviderUsage` 类末尾新增：

```python
    async def test_text_call_gemini_cost(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="gemini-3-flash-preview",
            prompt="分析小说内容",
            provider="gemini",
        )

        await repo.finish_call(
            call_id,
            status="success",
            input_tokens=1000,
            output_tokens=500,
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["call_type"] == "text"
        assert item["input_tokens"] == 1000
        assert item["output_tokens"] == 500
        assert item["currency"] == "USD"
        # cost = (1000 * 0.10 + 500 * 0.40) / 1_000_000 = 0.0003
        assert item["cost_amount"] == pytest.approx(0.0003)

    async def test_text_call_ark_cost(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="doubao-seed-2-0-lite-260215",
            prompt="分析小说内容",
            provider="ark",
        )

        await repo.finish_call(
            call_id,
            status="success",
            input_tokens=2000,
            output_tokens=1000,
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["currency"] == "CNY"
        # cost = (2000 * 0.30 + 1000 * 0.60) / 1_000_000 = 0.0012
        assert item["cost_amount"] == pytest.approx(0.0012)

    async def test_text_call_failed_zero_cost(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="text",
            model="gemini-3-flash-preview",
            provider="gemini",
        )

        await repo.finish_call(
            call_id,
            status="failed",
            error_message="API error",
        )

        calls = await repo.get_calls(project_name="demo")
        item = calls["items"][0]
        assert item["cost_amount"] == 0.0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_usage_repo.py::TestMultiProviderUsage::test_text_call_gemini_cost -v`
Expected: FAIL — `finish_call()` 不接受 `input_tokens` 参数

- [ ] **Step 3: 实现 `finish_call()` 的 text 成本计算**

修改 `lib/db/repositories/usage_repo.py`：

**3a.** `finish_call()` 签名新增参数：

```python
    async def finish_call(
        self,
        call_id: int,
        *,
        status: str,
        output_path: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_count: int = 0,
        usage_tokens: Optional[int] = None,
        service_tier: str = "default",
        generate_audio: Optional[bool] = None,
        input_tokens: Optional[int] = None,      # 新增
        output_tokens: Optional[int] = None,      # 新增
    ) -> None:
```

**3b.** 在 `finish_call()` 的成本计算 `if status == "success":` 块内，在最后一个 `elif row.call_type == "video":` 块之后新增：

```python
            elif row.call_type == "text" and input_tokens is not None:
                cost_amount, currency = cost_calculator.calculate_text_cost(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens or 0,
                    provider=effective_provider,
                    model=row.model,
                )
```

**3c.** 在 `finish_call()` 的 `update().values(...)` 中追加 `input_tokens` 和 `output_tokens`：

```python
        await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id)
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=duration_ms,
                retry_count=retry_count,
                cost_amount=cost_amount,
                currency=currency,
                usage_tokens=usage_tokens,
                output_path=output_path,
                error_message=error_truncated,
                input_tokens=input_tokens,       # 新增
                output_tokens=output_tokens,     # 新增
            )
        )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run python -m pytest tests/test_usage_repo.py::TestMultiProviderUsage -v`
Expected: all PASS（包括原有测试和 3 个新测试）

- [ ] **Step 5: 写失败测试 — get_stats 包含 text_count**

在 `tests/test_usage_repo.py` 的 `TestUsageRepository` 类中的 `test_get_stats` 方法末尾新增断言覆盖：

将现有 `test_get_stats` 改为也包含 text call：

```python
    async def test_get_stats_includes_text_count(self, db_session):
        repo = UsageRepository(db_session)
        c1 = await repo.start_call(project_name="demo", call_type="image", model="m")
        await repo.finish_call(c1, status="success")

        c2 = await repo.start_call(project_name="demo", call_type="video", model="m", duration_seconds=8)
        await repo.finish_call(c2, status="failed", error_message="timeout")

        c3 = await repo.start_call(project_name="demo", call_type="text", model="m", provider="gemini")
        await repo.finish_call(c3, status="success", input_tokens=100, output_tokens=50)

        stats = await repo.get_stats(project_name="demo")
        assert stats["image_count"] == 1
        assert stats["video_count"] == 1
        assert stats["text_count"] == 1
        assert stats["failed_count"] == 1
        assert stats["total_count"] == 3
```

- [ ] **Step 6: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_usage_repo.py::TestUsageRepository::test_get_stats_includes_text_count -v`
Expected: FAIL — `text_count` key 不存在

- [ ] **Step 7: 实现 get_stats 增加 text_count**

在 `lib/db/repositories/usage_repo.py` 的 `get_stats()` 方法中：

**7a.** 在 main aggregation query 的 `select()` 中，在 `video_count` 行之后增加：

```python
            func.count(case((ApiCall.call_type == "text", 1))).label("text_count"),
```

**7b.** 在返回字典中增加：

```python
            "text_count": row.text_count,
```

- [ ] **Step 8: `_row_to_dict()` 增加新字段**

在 `lib/db/repositories/usage_repo.py` 的 `_row_to_dict()` 函数中，在 `"usage_tokens"` 行之后新增：

```python
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
```

- [ ] **Step 9: 运行全部 usage_repo 测试**

Run: `uv run python -m pytest tests/test_usage_repo.py -v`
Expected: all PASS

- [ ] **Step 10: Commit**

```bash
git add lib/db/repositories/usage_repo.py tests/test_usage_repo.py
git commit -m "feat: UsageRepository 支持 text call_type 成本计算和 text_count 统计"
```

---

### Task 3: UsageTracker 透传新参数

**Files:**
- Test: `tests/test_usage_tracker.py`
- Modify: `lib/usage_tracker.py`

- [ ] **Step 1: 写失败测试 — UsageTracker 处理 text call**

在 `tests/test_usage_tracker.py` 的 `TestUsageTracker` 类末尾新增：

```python
    async def test_text_call_with_token_tracking(self, tracker):
        call_id = await tracker.start_call(
            project_name="demo",
            call_type="text",
            model="gemini-3-flash-preview",
            prompt="测试 prompt",
            provider="gemini",
        )
        await tracker.finish_call(
            call_id,
            status="success",
            input_tokens=1000,
            output_tokens=500,
        )

        result = await tracker.get_calls(project_name="demo")
        item = result["items"][0]
        assert item["call_type"] == "text"
        assert item["input_tokens"] == 1000
        assert item["output_tokens"] == 500
        assert item["cost_amount"] == pytest.approx(0.0003)

        stats = await tracker.get_stats(project_name="demo")
        assert stats["text_count"] == 1
        assert stats["total_count"] == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_usage_tracker.py::TestUsageTracker::test_text_call_with_token_tracking -v`
Expected: FAIL — `finish_call()` 不接受 `input_tokens` 参数

- [ ] **Step 3: 修改 UsageTracker.finish_call() 透传新参数**

在 `lib/usage_tracker.py` 的 `finish_call()` 方法中：

**3a.** 签名新增参数：

```python
    async def finish_call(
        self,
        call_id: int,
        status: str,
        output_path: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_count: int = 0,
        usage_tokens: Optional[int] = None,
        service_tier: str = "default",
        generate_audio: Optional[bool] = None,
        input_tokens: Optional[int] = None,      # 新增
        output_tokens: Optional[int] = None,      # 新增
    ) -> None:
```

**3b.** 在 `repo.finish_call()` 调用中追加：

```python
            await repo.finish_call(
                call_id,
                status=status,
                output_path=output_path,
                error_message=error_message,
                retry_count=retry_count,
                usage_tokens=usage_tokens,
                service_tier=service_tier,
                generate_audio=generate_audio,
                input_tokens=input_tokens,       # 新增
                output_tokens=output_tokens,     # 新增
            )
```

- [ ] **Step 4: 运行全部 usage_tracker 测试**

Run: `uv run python -m pytest tests/test_usage_tracker.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lib/usage_tracker.py tests/test_usage_tracker.py
git commit -m "feat: UsageTracker 透传 input_tokens/output_tokens 参数"
```

---

### Task 4: TextGenerator 包装层

**Files:**
- Create: `lib/text_generator.py`
- Test: `tests/test_text_generator.py`

- [ ] **Step 1: Write failing tests**

创建 `tests/test_text_generator.py`：

```python
"""Tests for TextGenerator wrapper."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.text_backends.base import TextGenerationRequest, TextGenerationResult
from lib.text_generator import TextGenerator
from lib.usage_tracker import UsageTracker


@pytest.fixture
async def tracker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    t = UsageTracker(session_factory=factory)
    yield t
    await engine.dispose()


def _make_backend(provider="gemini", model="gemini-3-flash-preview"):
    backend = AsyncMock()
    backend.name = provider
    backend.model = model
    backend.generate = AsyncMock(return_value=TextGenerationResult(
        text="生成的文本",
        provider=provider,
        model=model,
        input_tokens=100,
        output_tokens=50,
    ))
    return backend


class TestTextGenerator:
    async def test_generate_records_usage_on_success(self, tracker):
        backend = _make_backend()
        gen = TextGenerator(backend, tracker)

        result = await gen.generate(
            TextGenerationRequest(prompt="测试"),
            project_name="demo",
        )

        assert result.text == "生成的文本"
        assert result.input_tokens == 100
        assert result.output_tokens == 50

        calls = await tracker.get_calls(project_name="demo")
        assert calls["total"] == 1
        item = calls["items"][0]
        assert item["call_type"] == "text"
        assert item["status"] == "success"
        assert item["input_tokens"] == 100
        assert item["output_tokens"] == 50
        assert item["provider"] == "gemini"
        assert item["cost_amount"] == pytest.approx((100 * 0.10 + 50 * 0.40) / 1_000_000)

    async def test_generate_records_usage_on_failure(self, tracker):
        backend = _make_backend()
        backend.generate = AsyncMock(side_effect=RuntimeError("API 超时"))
        gen = TextGenerator(backend, tracker)

        with pytest.raises(RuntimeError, match="API 超时"):
            await gen.generate(
                TextGenerationRequest(prompt="测试"),
                project_name="demo",
            )

        calls = await tracker.get_calls(project_name="demo")
        assert calls["total"] == 1
        item = calls["items"][0]
        assert item["status"] == "failed"
        assert item["cost_amount"] == 0.0
        assert "API 超时" in item["error_message"]

    async def test_generate_without_project_name(self, tracker):
        backend = _make_backend()
        gen = TextGenerator(backend, tracker)

        result = await gen.generate(TextGenerationRequest(prompt="工具箱调用"))

        assert result.text == "生成的文本"
        calls = await tracker.get_calls()
        assert calls["total"] == 1
        item = calls["items"][0]
        assert item["project_name"] == ""
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_text_generator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.text_generator'`

- [ ] **Step 3: 实现 TextGenerator**

创建 `lib/text_generator.py`：

```python
"""TextGenerator — 文本生成 + 用量追踪包装层。

类似 MediaGenerator，组合 TextBackend + UsageTracker，
调用方无需关心 usage tracking 细节。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lib.text_backends.base import (
    TextGenerationRequest,
    TextGenerationResult,
    TextTaskType,
)
from lib.text_backends.factory import create_text_backend_for_task
from lib.usage_tracker import UsageTracker

if TYPE_CHECKING:
    from lib.text_backends.base import TextBackend

logger = logging.getLogger(__name__)


class TextGenerator:
    """组合 TextBackend + UsageTracker，统一封装文本生成 + 用量追踪。"""

    def __init__(self, backend: TextBackend, usage_tracker: UsageTracker):
        self.backend = backend
        self.usage_tracker = usage_tracker

    @classmethod
    async def create(
        cls,
        task_type: TextTaskType,
        project_name: str | None = None,
    ) -> TextGenerator:
        """工厂方法：根据任务类型创建对应的 backend + usage_tracker。"""
        backend = await create_text_backend_for_task(task_type, project_name)
        usage_tracker = UsageTracker()
        return cls(backend, usage_tracker)

    async def generate(
        self,
        request: TextGenerationRequest,
        project_name: str | None = None,
    ) -> TextGenerationResult:
        """生成文本并自动记录用量。"""
        call_id = await self.usage_tracker.start_call(
            project_name=project_name or "",
            call_type="text",
            model=self.backend.model,
            prompt=request.prompt[:500],
            provider=self.backend.name,
        )
        try:
            result = await self.backend.generate(request)
            await self.usage_tracker.finish_call(
                call_id,
                status="success",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            return result
        except Exception as e:
            await self.usage_tracker.finish_call(
                call_id,
                status="failed",
                error_message=str(e)[:500],
            )
            raise
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run python -m pytest tests/test_text_generator.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lib/text_generator.py tests/test_text_generator.py
git commit -m "feat: TextGenerator 包装层，组合 TextBackend + UsageTracker"
```

---

### Task 5: 改造 3 个调用点

**Files:**
- Modify: `lib/script_generator.py:50-57,108-112`
- Modify: `lib/project_manager.py:1579-1598`
- Modify: `server/routers/files.py:524-531`

- [ ] **Step 1: 改造 ScriptGenerator**

修改 `lib/script_generator.py`：

**1a.** 更新 import（第 15 行）：

将：
```python
from lib.text_backends.base import TextBackend, TextGenerationRequest, TextTaskType
```
替换为：
```python
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.text_generator import TextGenerator
```

**1b.** 更新 `__init__` 签名和类型（第 35 行）：

将：
```python
    def __init__(self, project_path: Union[str, Path], backend: Optional["TextBackend"] = None):
```
替换为：
```python
    def __init__(self, project_path: Union[str, Path], generator: Optional["TextGenerator"] = None):
```

**1c.** 更新 `__init__` 内部赋值（第 44 行）：

将：
```python
        self.backend = backend
```
替换为：
```python
        self.generator = generator
```

**1d.** 更新 `create()` 工厂方法（第 51-57 行）：

将：
```python
    @classmethod
    async def create(cls, project_path: Union[str, Path]) -> "ScriptGenerator":
        """异步工厂方法，自动从 DB 加载供应商配置创建 backend。"""
        from lib.text_backends.factory import create_text_backend_for_task

        project_name = Path(project_path).name
        backend = await create_text_backend_for_task(TextTaskType.SCRIPT, project_name)
        return cls(project_path, backend)
```
替换为：
```python
    @classmethod
    async def create(cls, project_path: Union[str, Path]) -> "ScriptGenerator":
        """异步工厂方法，自动从 DB 加载供应商配置创建 TextGenerator。"""
        project_name = Path(project_path).name
        generator = await TextGenerator.create(TextTaskType.SCRIPT, project_name)
        return cls(project_path, generator)
```

**1e.** 更新 `generate()` 中的 backend 检查（第 74-77 行）：

将：
```python
        if self.backend is None:
            raise RuntimeError(
                "TextBackend 未初始化，请使用 ScriptGenerator.create() 工厂方法"
            )
```
替换为：
```python
        if self.generator is None:
            raise RuntimeError(
                "TextGenerator 未初始化，请使用 ScriptGenerator.create() 工厂方法"
            )
```

**1f.** 更新 `generate()` 中的 API 调用（第 109-112 行）：

将：
```python
        logger.info("正在生成第 %d 集剧本...", episode)
        result = await self.backend.generate(
            TextGenerationRequest(prompt=prompt, response_schema=schema)
        )
        response_text = result.text
```
替换为：
```python
        logger.info("正在生成第 %d 集剧本...", episode)
        project_name = self.project_path.name
        result = await self.generator.generate(
            TextGenerationRequest(prompt=prompt, response_schema=schema),
            project_name=project_name,
        )
        response_text = result.text
```

**1g.** 更新 `_add_metadata()` 中的 generator 引用（第 261 行）：

将：
```python
        script_data["metadata"]["generator"] = self.backend.model if self.backend else "unknown"
```
替换为：
```python
        script_data["metadata"]["generator"] = self.generator.backend.model if self.generator else "unknown"
```

- [ ] **Step 2: 改造 ProjectManager.generate_overview()**

修改 `lib/project_manager.py` 第 1579-1598 行。

将：
```python
        from .text_backends.factory import create_text_backend_for_task
        from .text_backends.base import TextGenerationRequest, TextTaskType

        # 读取源文件内容
        source_content = self._read_source_files(project_name)
        if not source_content:
            raise ValueError("source 目录为空，无法生成概述")

        # 从 DB 加载供应商配置创建 backend
        backend = await create_text_backend_for_task(TextTaskType.OVERVIEW)

        # 调用 TextBackend（Structured Outputs）
        prompt = f"请分析以下小说内容，提取关键信息：\n\n{source_content}"

        result = await backend.generate(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=ProjectOverview.model_json_schema(),
            )
        )
        response_text = result.text
```
替换为：
```python
        from .text_backends.base import TextGenerationRequest, TextTaskType
        from .text_generator import TextGenerator

        # 读取源文件内容
        source_content = self._read_source_files(project_name)
        if not source_content:
            raise ValueError("source 目录为空，无法生成概述")

        # 创建 TextGenerator（自动追踪用量）
        generator = await TextGenerator.create(TextTaskType.OVERVIEW)

        # 调用 TextGenerator（Structured Outputs）
        prompt = f"请分析以下小说内容，提取关键信息：\n\n{source_content}"

        result = await generator.generate(
            TextGenerationRequest(
                prompt=prompt,
                response_schema=ProjectOverview.model_json_schema(),
            ),
            project_name=project_name,
        )
        response_text = result.text
```

- [ ] **Step 3: 改造 files.py 风格分析**

修改 `server/routers/files.py` 第 524-531 行。

将：
```python
        # 调用 TextBackend 分析风格
        from lib.text_backends.factory import create_text_backend_for_task
        from lib.text_backends.base import TextGenerationRequest, TextTaskType, ImageInput
        from lib.text_backends.prompts import STYLE_ANALYSIS_PROMPT
        backend = await create_text_backend_for_task(TextTaskType.STYLE_ANALYSIS)
        result = await backend.generate(
            TextGenerationRequest(prompt=STYLE_ANALYSIS_PROMPT, images=[ImageInput(path=output_path)])
        )
        style_description = result.text
```
替换为：
```python
        # 调用 TextGenerator 分析风格（自动追踪用量）
        from lib.text_backends.base import TextGenerationRequest, TextTaskType, ImageInput
        from lib.text_backends.prompts import STYLE_ANALYSIS_PROMPT
        from lib.text_generator import TextGenerator
        generator = await TextGenerator.create(TextTaskType.STYLE_ANALYSIS)
        result = await generator.generate(
            TextGenerationRequest(prompt=STYLE_ANALYSIS_PROMPT, images=[ImageInput(path=output_path)]),
            project_name=project_name,
        )
        style_description = result.text
```

- [ ] **Step 4: 运行现有测试确保无回归**

Run: `uv run python -m pytest tests/ -v --ignore=tests/test_text_generator.py -k "script or usage" --timeout=30`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add lib/script_generator.py lib/project_manager.py server/routers/files.py
git commit -m "feat: 3 个调用点改用 TextGenerator，自动追踪文本生成用量"
```

---

### Task 6: 前端类型和 UsageDrawer

**Files:**
- Modify: `frontend/src/stores/usage-store.ts`
- Modify: `frontend/src/components/layout/UsageDrawer.tsx`
- Modify: `frontend/src/components/layout/GlobalHeader.tsx`
- Modify: `frontend/src/components/pages/settings/UsageStatsSection.tsx`

- [ ] **Step 1: 更新 usage-store 类型**

修改 `frontend/src/stores/usage-store.ts`：

**1a.** 在 `UsageStats` 接口中，在 `video_count: number;` 之后新增：

```typescript
  text_count: number;
```

**1b.** 在 `UsageCall` 接口中，在 `created_at: string;` 之后新增：

```typescript
  input_tokens: number | null;
  output_tokens: number | null;
```

- [ ] **Step 2: 更新 UsageDrawer — 增加 text 图标和 token 信息展示**

修改 `frontend/src/components/layout/UsageDrawer.tsx`：

**2a.** 更新 import，在 `Image, Video` 之后添加 `FileText`：

将：
```typescript
import { X, Image, Video, AlertCircle, DollarSign, ChevronLeft, ChevronRight } from "lucide-react";
```
替换为：
```typescript
import { X, Image, Video, FileText, AlertCircle, DollarSign, ChevronLeft, ChevronRight } from "lucide-react";
```

**2b.** 在 `UsageDrawer` 组件内部的本地 `UsageCall` 接口（约第 42 行）中，在 `created_at: string;` 之后新增：

```typescript
  input_tokens: number | null;
  output_tokens: number | null;
```

**2c.** 在 Stats summary 的 grid 中，在 `视频` StatBlock 之后、`失败` StatBlock 之前新增文本统计：

将 `grid-cols-4` 改为 `grid-cols-5`：

```typescript
      <div className="grid grid-cols-5 gap-2 border-b border-gray-800 px-4 py-3">
```

在 `<StatBlock label="视频" ... />` 之后新增：

```typescript
        <StatBlock label="文本" value={String(stats?.text_count ?? 0)} icon={<FileText className="h-3 w-3 text-green-400" />} />
```

**2d.** 更新调用记录列表中的类型判断。在 `calls.map((call) => {` 回调内部：

将 `typeLabel` 定义改为：

```typescript
              const typeLabel = call.call_type === "video" ? "视频" : call.call_type === "text" ? "文本" : "图片";
```

将 type icon 的三元表达式改为：

```typescript
                    <span className="shrink-0">
                      {call.call_type === "video" ? (
                        <Video className="h-3.5 w-3.5 text-purple-400" />
                      ) : call.call_type === "text" ? (
                        <FileText className="h-3.5 w-3.5 text-green-400" />
                      ) : (
                        <Image className="h-3.5 w-3.5 text-blue-400" />
                      )}
                    </span>
```

**2e.** 在 Row 2（model + resolution + duration + time）中，增加 text 类型的 token 展示。

将 resolution 和 duration 的渲染改为：

```typescript
                    {call.call_type === "text" ? (
                      <>
                        {call.input_tokens != null && <span>输入 {call.input_tokens.toLocaleString()}</span>}
                        {call.output_tokens != null && <span>输出 {call.output_tokens.toLocaleString()} tokens</span>}
                      </>
                    ) : (
                      <>
                        {call.resolution && <span>{call.resolution}</span>}
                        {durationInfo && <span>{durationInfo}</span>}
                      </>
                    )}
```

- [ ] **Step 3: 更新 GlobalHeader 中的 usageStats 类型**

修改 `frontend/src/components/layout/GlobalHeader.tsx`：

在 `setUsageStats(res as { ... })` 的类型断言中，在 `video_count: number;` 之后新增：

```typescript
        text_count: number;
```

- [ ] **Step 4: 更新 UsageStatsSection — text 卡片展示 token 信息**

修改 `frontend/src/components/pages/settings/UsageStatsSection.tsx`：

在统计卡片的 `<div className="mt-2 flex ...">` 内部，将时长展示改为条件渲染：

将：
```typescript
                {s.total_duration_seconds !== undefined && (
                  <span>时长: {s.total_duration_seconds}s</span>
                )}
```
替换为：
```typescript
                {s.call_type === "text" ? (
                  s.total_calls > 0 && <span>类型: 文本生成</span>
                ) : s.total_duration_seconds !== undefined && (
                  <span>时长: {s.total_duration_seconds}s</span>
                )}
```

- [ ] **Step 5: 运行前端类型检查**

Run: `cd frontend && pnpm typecheck`
Expected: 无类型错误

- [ ] **Step 6: Commit**

```bash
git add frontend/src/stores/usage-store.ts frontend/src/components/layout/UsageDrawer.tsx frontend/src/components/layout/GlobalHeader.tsx frontend/src/components/pages/settings/UsageStatsSection.tsx
git commit -m "feat: 前端支持文本生成用量展示，绿色 FileText 图标 + token 信息"
```

---

### Task 7: 全量验证

**Files:** 无新改动

- [ ] **Step 1: 运行全部后端测试**

Run: `uv run python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 2: 运行前端检查**

Run: `cd frontend && pnpm check`
Expected: typecheck + test 全部通过

- [ ] **Step 3: 运行前端构建**

Run: `cd frontend && pnpm build`
Expected: 构建成功

- [ ] **Step 4: Commit（如有 lint 修复）**

如果前述步骤产生了 lint 修复，提交：

```bash
git add -A
git commit -m "chore: lint fixes"
```
