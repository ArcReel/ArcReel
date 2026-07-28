# 自定义供应商实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持用户通过 base_url + api_key 接入任意 OpenAI/Google 兼容服务，同时修复 #189 中 3 项 OpenAI 预置供应商改进。

**Architecture:** 平行轨道 — 自定义供应商有独立的 DB 表、Service、API 路由和前端区域，与预置供应商仅在 ConfigResolver（backend 选择）、system-config/options（模型下拉框）、UsageTracker（费用记录）三处汇合。Backend 层使用轻量包装类委托给现有 OpenAI/Gemini 后端。

**Tech Stack:** Python 3.12, SQLAlchemy Async ORM, FastAPI, Alembic, React 19, TypeScript, Tailwind CSS 4, Zustand

---

## Task 1: #189 — Instructor fallback 结构化输出降级

**Files:**
- Modify: `lib/text_backends/openai.py`
- Test: `tests/test_openai_text_backend.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_openai_text_backend.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from lib.text_backends.openai import OpenAITextBackend
from lib.text_backends.base import TextGenerationRequest, TextGenerationResult


@pytest.fixture
def backend():
    with patch("lib.text_backends.openai.create_openai_client") as mock_create:
        mock_client = AsyncMock()
        mock_create.return_value = mock_client
        b = OpenAITextBackend(api_key="sk-test")
        b._test_client = mock_client
        yield b


async def test_instructor_fallback_on_structured_output_failure(backend):
    """原生 response_format 失败时应回退到 Instructor。"""
    from pydantic import BaseModel

    class TestSchema(BaseModel):
        name: str
        value: int

    # 第一次调用（原生）抛 BadRequestError
    from openai import BadRequestError

    error_response = MagicMock()
    error_response.status_code = 400
    error_response.headers = {}
    backend._test_client.chat.completions.create.side_effect = BadRequestError(
        message="Invalid schema", response=error_response, body=None
    )

    request = TextGenerationRequest(prompt="test", response_schema=TestSchema)

    # 应该尝试 Instructor 降级而非直接抛异常
    with patch("lib.text_backends.openai._instructor_fallback") as mock_fallback:
        mock_fallback.return_value = TextGenerationResult(
            text='{"name":"test","value":1}',
            provider="openai",
            model="gpt-5.4-mini",
            input_tokens=10,
            output_tokens=5,
        )
        result = await backend.generate(request)
        assert result.text == '{"name":"test","value":1}'
        mock_fallback.assert_called_once()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_openai_text_backend.py::test_instructor_fallback_on_structured_output_failure -v`
Expected: FAIL — `_instructor_fallback` 不存在

- [ ] **Step 3: 实现 Instructor fallback**

在 `lib/text_backends/openai.py` 中修改 `generate()` 方法，参照 Gemini 后端的 Instructor 集成模式：

```python
# lib/text_backends/openai.py — 在 generate() 方法中包裹 response_format 调用
async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
    messages = _build_messages(request)
    kwargs: dict = {"model": self._model, "messages": messages}

    if request.response_schema:
        schema = resolve_schema(request.response_schema)
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "strict": True,
                "schema": schema,
            },
        }

    try:
        response = await self._client.chat.completions.create(**kwargs)
    except Exception as exc:
        if request.response_schema and _is_schema_error(exc):
            logger.warning("OpenAI 原生结构化输出失败，尝试 Instructor 降级: %s", exc)
            return await _instructor_fallback(self._client, self._model, request)
        raise

    usage = response.usage
    return TextGenerationResult(
        text=response.choices[0].message.content or "",
        provider=PROVIDER_OPENAI,
        model=self._model,
        input_tokens=usage.prompt_tokens if usage else None,
        output_tokens=usage.completion_tokens if usage else None,
    )


def _is_schema_error(exc: Exception) -> bool:
    """判断异常是否为 schema 不兼容错误（应尝试 Instructor 降级）。"""
    from openai import BadRequestError
    if isinstance(exc, BadRequestError):
        return True
    return False


async def _instructor_fallback(
    client, model: str, request: TextGenerationRequest
) -> TextGenerationResult:
    """使用 Instructor 库解析结构化输出。"""
    import instructor

    patched = instructor.from_openai(client)
    messages = _build_messages(request)

    # Instructor 需要 Pydantic 类
    schema = request.response_schema
    if isinstance(schema, type):
        response_model = schema
    else:
        # dict schema — 用 Instructor JSON 模式
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return TextGenerationResult(
            text=response.choices[0].message.content or "",
            provider=PROVIDER_OPENAI,
            model=model,
            input_tokens=response.usage.prompt_tokens if response.usage else None,
            output_tokens=response.usage.completion_tokens if response.usage else None,
        )

    result, completion = await patched.chat.completions.create_with_completion(
        model=model,
        messages=messages,
        response_model=response_model,
    )

    usage = completion.usage
    return TextGenerationResult(
        text=result.model_dump_json(),
        provider=PROVIDER_OPENAI,
        model=model,
        input_tokens=usage.prompt_tokens if usage else None,
        output_tokens=usage.completion_tokens if usage else None,
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_openai_text_backend.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add lib/text_backends/openai.py tests/test_openai_text_backend.py
git commit -m "fix: OpenAI 文本后端 Instructor fallback 结构化输出降级 (#189)"
```

---

## Task 2: #189 — quality 参数传递链

**Files:**
- Modify: `lib/image_backends/base.py:52` — `ImageGenerationResult` 新增 `quality` 字段
- Modify: `lib/image_backends/openai.py:104-113` — `_save_and_return` 填入 quality
- Modify: `lib/usage_tracker.py:53-80` — `finish_call` 新增 quality 参数
- Modify: `lib/db/repositories/usage_repo.py` — `finish_call` 透传 quality
- Modify: `lib/cost_calculator.py:362` — 已支持 quality 参数（无需改动）
- Test: `tests/test_quality_propagation.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_quality_propagation.py
from lib.image_backends.base import ImageGenerationResult
from pathlib import Path


def test_image_generation_result_has_quality_field():
    result = ImageGenerationResult(
        image_path=Path("/tmp/test.png"),
        provider="openai",
        model="gpt-image-1.5",
        quality="high",
    )
    assert result.quality == "high"


def test_image_generation_result_quality_defaults_none():
    result = ImageGenerationResult(
        image_path=Path("/tmp/test.png"),
        provider="openai",
        model="gpt-image-1.5",
    )
    assert result.quality is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_quality_propagation.py -v`
Expected: FAIL — `quality` 不是 `ImageGenerationResult` 的字段

- [ ] **Step 3: 实现 quality 传递链**

`lib/image_backends/base.py` — `ImageGenerationResult` 新增字段：

```python
@dataclass
class ImageGenerationResult:
    image_path: Path
    provider: str
    model: str
    image_uri: str | None = None
    seed: int | None = None
    usage_tokens: int | None = None
    quality: str | None = None  # 新增：实际使用的 quality（如 "low"/"medium"/"high"）
```

`lib/image_backends/openai.py` — `_save_and_return` 填入实际 quality：

```python
def _save_and_return(self, response, request: ImageGenerationRequest) -> ImageGenerationResult:
    image_bytes = base64.b64decode(response.data[0].b64_json)
    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    request.output_path.write_bytes(image_bytes)
    logger.info("OpenAI 图片生成完成: %s", request.output_path)
    return ImageGenerationResult(
        image_path=request.output_path,
        provider=PROVIDER_OPENAI,
        model=self._model,
        quality=_QUALITY_MAP.get(request.image_size, "medium"),
    )
```

`lib/usage_tracker.py` — `finish_call` 新增 `quality` 参数：

```python
async def finish_call(
    self,
    call_id: int,
    status: str,
    output_path: str | None = None,
    error_message: str | None = None,
    retry_count: int = 0,
    usage_tokens: int | None = None,
    service_tier: str = "default",
    generate_audio: bool | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    quality: str | None = None,  # 新增
) -> None:
    async with self._session_factory() as session:
        repo = UsageRepository(session)
        await repo.finish_call(
            call_id,
            status=status,
            output_path=output_path,
            error_message=error_message,
            retry_count=retry_count,
            usage_tokens=usage_tokens,
            service_tier=service_tier,
            generate_audio=generate_audio,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            quality=quality,
        )
```

同步修改 `lib/db/repositories/usage_repo.py` 的 `finish_call` 方法签名和 `CostCalculator` 调用处，将 `quality` 参数透传。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_quality_propagation.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add lib/image_backends/base.py lib/image_backends/openai.py lib/usage_tracker.py lib/db/repositories/usage_repo.py tests/test_quality_propagation.py
git commit -m "fix: OpenAI 图片 quality 参数传递链 (#189)"
```

---

## Task 3: #189 — Video resolution 参数映射

**Files:**
- Modify: `lib/video_backends/openai.py:20-23` — 扩展 `_SIZE_MAP`
- Test: `tests/test_openai_video_resolution.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_openai_video_resolution.py
from lib.video_backends.openai import _resolve_size


def test_sora2_720p_9_16():
    assert _resolve_size("720p", "9:16") == "720x1280"

def test_sora2_720p_16_9():
    assert _resolve_size("720p", "16:9") == "1280x720"

def test_sora2pro_1080p_9_16():
    assert _resolve_size("1080p", "9:16") == "1080x1920"

def test_sora2pro_1080p_16_9():
    assert _resolve_size("1080p", "16:9") == "1920x1080"

def test_default_fallback():
    assert _resolve_size("unknown", "unknown") == "720x1280"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_openai_video_resolution.py -v`
Expected: FAIL — `_resolve_size` 不存在

- [ ] **Step 3: 实现 resolution 映射**

在 `lib/video_backends/openai.py` 中替换现有的 `_SIZE_MAP` 和 `generate()` 中的 size 逻辑：

```python
# 替换现有的 _SIZE_MAP
_SIZE_MAP: dict[tuple[str, str], str] = {
    # (resolution, aspect_ratio) → size
    ("720p", "9:16"): "720x1280",
    ("720p", "16:9"): "1280x720",
    ("1080p", "9:16"): "1080x1920",
    ("1080p", "16:9"): "1920x1080",
    ("1024p", "9:16"): "1024x1792",
    ("1024p", "16:9"): "1792x1024",
}
_DEFAULT_SIZE = "720x1280"


def _resolve_size(resolution: str, aspect_ratio: str) -> str:
    """根据 (resolution, aspect_ratio) 解析视频尺寸。"""
    return _SIZE_MAP.get((resolution, aspect_ratio), _DEFAULT_SIZE)
```

在 `generate()` 方法中更新：

```python
kwargs: dict = {
    "prompt": request.prompt,
    "model": self._model,
    "seconds": _map_duration(request.duration_seconds),
    "size": _resolve_size(request.resolution, request.aspect_ratio),
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_openai_video_resolution.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add lib/video_backends/openai.py tests/test_openai_video_resolution.py
git commit -m "fix: OpenAI 视频 resolution 参数映射 (#189)"
```

---

## Task 4: 自定义供应商 ORM 模型 + 数据库迁移

**Files:**
- Create: `lib/db/models/custom_provider.py`
- Modify: `lib/db/models/__init__.py`
- Create: `alembic/versions/xxx_add_custom_provider_tables.py`（通过 autogenerate）
- Test: `tests/test_custom_provider_models.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_custom_provider_models.py
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_create_custom_provider(session):
    provider = CustomProvider(
        display_name="我的 NewAPI",
        api_format="openai",
        base_url="https://my-newapi.example.com/v1",
        api_key="sk-xxxx",
    )
    session.add(provider)
    await session.flush()
    assert provider.id is not None
    assert provider.provider_id == f"custom-{provider.id}"


async def test_create_custom_provider_model(session):
    provider = CustomProvider(
        display_name="Test",
        api_format="openai",
        base_url="https://example.com",
        api_key="sk-test",
    )
    session.add(provider)
    await session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="deepseek-v3",
        display_name="DeepSeek V3",
        media_type="text",
        is_default=True,
        is_enabled=True,
    )
    session.add(model)
    await session.flush()
    assert model.id is not None


async def test_custom_provider_model_price_nullable(session):
    provider = CustomProvider(
        display_name="Ollama",
        api_format="openai",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )
    session.add(provider)
    await session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="llama3",
        display_name="Llama 3",
        media_type="text",
        is_default=True,
        is_enabled=True,
        price_input=None,
        price_output=None,
        currency=None,
    )
    session.add(model)
    await session.flush()
    assert model.price_input is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_custom_provider_models.py -v`
Expected: FAIL — `custom_provider` 模块不存在

- [ ] **Step 3: 创建 ORM 模型**

```python
# lib/db/models/custom_provider.py
"""自定义供应商 ORM 模型。"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class CustomProvider(TimestampMixin, Base):
    """自定义供应商。每条记录代表用户添加的一个自定义供应商。"""

    __tablename__ = "custom_provider"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_format: Mapped[str] = mapped_column(String(32), nullable=False)  # "openai" | "google"
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)

    @property
    def provider_id(self) -> str:
        """内部标识，用于 ApiCall.provider 等字段。"""
        return f"custom-{self.id}"


class CustomProviderModel(TimestampMixin, Base):
    """自定义供应商的模型配置。"""

    __tablename__ = "custom_provider_model"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="uq_custom_provider_model"),
        Index("ix_custom_provider_model_provider_id", "provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(Integer, nullable=False)  # FK → custom_provider.id
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)  # text | image | video
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    price_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)  # token | image | second
    price_input: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_output: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)  # USD | CNY
```

- [ ] **Step 4: 更新模型导出**

在 `lib/db/models/__init__.py` 中添加：

```python
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
```

并在 `__all__` 列表中追加 `"CustomProvider"`, `"CustomProviderModel"`。

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_custom_provider_models.py -v`
Expected: PASS

- [ ] **Step 6: 生成 Alembic 迁移**

Run: `uv run alembic revision --autogenerate -m "add custom provider tables"`
检查生成的迁移文件，确保包含 `custom_provider` 和 `custom_provider_model` 两张表。

Run: `uv run alembic upgrade head`

- [ ] **Step 7: 提交**

```bash
git add lib/db/models/custom_provider.py lib/db/models/__init__.py alembic/versions/*custom_provider* tests/test_custom_provider_models.py
git commit -m "feat: 自定义供应商 ORM 模型与数据库迁移"
```

---

## Task 5: 自定义供应商 Repository

**Files:**
- Create: `lib/db/repositories/custom_provider_repo.py`
- Test: `tests/test_custom_provider_repo.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_custom_provider_repo.py
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.custom_provider_repo import CustomProviderRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def repo(session):
    return CustomProviderRepository(session)


async def test_create_provider(repo, session):
    provider = await repo.create_provider(
        display_name="我的 NewAPI",
        api_format="openai",
        base_url="https://my-newapi.example.com/v1",
        api_key="sk-xxxx",
    )
    assert provider.id is not None
    assert provider.display_name == "我的 NewAPI"


async def test_create_provider_with_models(repo, session):
    provider = await repo.create_provider(
        display_name="Test",
        api_format="openai",
        base_url="https://example.com",
        api_key="sk-test",
        models=[
            {"model_id": "deepseek-v3", "display_name": "DeepSeek V3", "media_type": "text", "is_default": True},
            {"model_id": "kling-v3", "display_name": "Kling V3", "media_type": "video", "is_default": True},
        ],
    )
    models = await repo.list_models(provider.id)
    assert len(models) == 2


async def test_list_providers(repo, session):
    await repo.create_provider("A", "openai", "https://a.com", "sk-a")
    await repo.create_provider("B", "google", "https://b.com", "sk-b")
    await session.flush()
    providers = await repo.list_providers()
    assert len(providers) == 2


async def test_delete_provider_cascades_models(repo, session):
    provider = await repo.create_provider(
        "Test", "openai", "https://test.com", "sk-test",
        models=[{"model_id": "m1", "display_name": "M1", "media_type": "text", "is_default": True}],
    )
    await session.flush()
    await repo.delete_provider(provider.id)
    await session.flush()
    assert await repo.get_provider(provider.id) is None
    assert len(await repo.list_models(provider.id)) == 0


async def test_update_model_price(repo, session):
    provider = await repo.create_provider(
        "Test", "openai", "https://test.com", "sk-test",
        models=[{"model_id": "m1", "display_name": "M1", "media_type": "text", "is_default": True}],
    )
    await session.flush()
    models = await repo.list_models(provider.id)
    await repo.update_model(models[0].id, price_input=1.0, price_output=2.0, currency="USD")
    await session.flush()
    updated = await repo.list_models(provider.id)
    assert updated[0].price_input == 1.0
    assert updated[0].price_output == 2.0


async def test_get_enabled_models_by_media_type(repo, session):
    provider = await repo.create_provider(
        "Test", "openai", "https://test.com", "sk-test",
        models=[
            {"model_id": "t1", "display_name": "T1", "media_type": "text", "is_default": True},
            {"model_id": "v1", "display_name": "V1", "media_type": "video", "is_default": True},
            {"model_id": "t2", "display_name": "T2", "media_type": "text", "is_enabled": False},
        ],
    )
    await session.flush()
    text_models = await repo.list_enabled_models_by_media_type("text")
    assert len(text_models) == 1
    assert text_models[0].model_id == "t1"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_custom_provider_repo.py -v`
Expected: FAIL — `CustomProviderRepository` 不存在

- [ ] **Step 3: 实现 Repository**

```python
# lib/db/repositories/custom_provider_repo.py
"""自定义供应商数据仓储。"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel


class CustomProviderRepository:
    """自定义供应商 CRUD 操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_provider(
        self,
        display_name: str,
        api_format: str,
        base_url: str,
        api_key: str,
        models: list[dict] | None = None,
    ) -> CustomProvider:
        provider = CustomProvider(
            display_name=display_name,
            api_format=api_format,
            base_url=base_url,
            api_key=api_key,
        )
        self.session.add(provider)
        await self.session.flush()

        if models:
            for m in models:
                model = CustomProviderModel(
                    provider_id=provider.id,
                    model_id=m["model_id"],
                    display_name=m["display_name"],
                    media_type=m["media_type"],
                    is_default=m.get("is_default", False),
                    is_enabled=m.get("is_enabled", True),
                    price_unit=m.get("price_unit"),
                    price_input=m.get("price_input"),
                    price_output=m.get("price_output"),
                    currency=m.get("currency"),
                )
                self.session.add(model)
            await self.session.flush()

        return provider

    async def get_provider(self, provider_id: int) -> CustomProvider | None:
        return await self.session.get(CustomProvider, provider_id)

    async def list_providers(self) -> list[CustomProvider]:
        stmt = select(CustomProvider).order_by(CustomProvider.id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def update_provider(self, provider_id: int, **kwargs) -> None:
        provider = await self.get_provider(provider_id)
        if provider is None:
            return
        for key, value in kwargs.items():
            if hasattr(provider, key):
                setattr(provider, key, value)

    async def delete_provider(self, provider_id: int) -> None:
        await self.session.execute(
            delete(CustomProviderModel).where(CustomProviderModel.provider_id == provider_id)
        )
        await self.session.execute(
            delete(CustomProvider).where(CustomProvider.id == provider_id)
        )

    async def list_models(self, provider_id: int) -> list[CustomProviderModel]:
        stmt = (
            select(CustomProviderModel)
            .where(CustomProviderModel.provider_id == provider_id)
            .order_by(CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def replace_models(self, provider_id: int, models: list[dict]) -> list[CustomProviderModel]:
        await self.session.execute(
            delete(CustomProviderModel).where(CustomProviderModel.provider_id == provider_id)
        )
        result = []
        for m in models:
            model = CustomProviderModel(
                provider_id=provider_id,
                model_id=m["model_id"],
                display_name=m["display_name"],
                media_type=m["media_type"],
                is_default=m.get("is_default", False),
                is_enabled=m.get("is_enabled", True),
                price_unit=m.get("price_unit"),
                price_input=m.get("price_input"),
                price_output=m.get("price_output"),
                currency=m.get("currency"),
            )
            self.session.add(model)
            result.append(model)
        await self.session.flush()
        return result

    async def update_model(self, model_id: int, **kwargs) -> None:
        model = await self.session.get(CustomProviderModel, model_id)
        if model is None:
            return
        for key, value in kwargs.items():
            if hasattr(model, key):
                setattr(model, key, value)

    async def delete_model(self, model_id: int) -> None:
        await self.session.execute(
            delete(CustomProviderModel).where(CustomProviderModel.id == model_id)
        )

    async def list_enabled_models_by_media_type(self, media_type: str) -> list[CustomProviderModel]:
        stmt = (
            select(CustomProviderModel)
            .where(
                CustomProviderModel.is_enabled == True,
                CustomProviderModel.media_type == media_type,
            )
            .order_by(CustomProviderModel.provider_id, CustomProviderModel.id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_default_model(self, provider_id: int, media_type: str) -> CustomProviderModel | None:
        stmt = (
            select(CustomProviderModel)
            .where(
                CustomProviderModel.provider_id == provider_id,
                CustomProviderModel.media_type == media_type,
                CustomProviderModel.is_default == True,
                CustomProviderModel.is_enabled == True,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_custom_provider_repo.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add lib/db/repositories/custom_provider_repo.py tests/test_custom_provider_repo.py
git commit -m "feat: 自定义供应商 Repository 层"
```

---

## Task 6: 自定义 Backend 包装类

**Files:**
- Create: `lib/custom_provider/__init__.py`
- Create: `lib/custom_provider/backends.py`
- Test: `tests/test_custom_backends.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_custom_backends.py
from pathlib import Path
from unittest.mock import AsyncMock

from lib.text_backends.base import TextCapability, TextGenerationRequest, TextGenerationResult
from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.video_backends.base import VideoCapability, VideoGenerationRequest, VideoGenerationResult
from lib.custom_provider.backends import CustomTextBackend, CustomImageBackend, CustomVideoBackend


def test_custom_text_backend_properties():
    delegate = AsyncMock()
    delegate.capabilities = {TextCapability.TEXT_GENERATION, TextCapability.STRUCTURED_OUTPUT}
    backend = CustomTextBackend(provider_id="custom-3", delegate=delegate, model="deepseek-v3")
    assert backend.name == "custom-3"
    assert backend.model == "deepseek-v3"
    assert backend.capabilities == {TextCapability.TEXT_GENERATION, TextCapability.STRUCTURED_OUTPUT}


async def test_custom_text_backend_delegates_generate():
    delegate = AsyncMock()
    delegate.capabilities = {TextCapability.TEXT_GENERATION}
    expected = TextGenerationResult(text="hello", provider="openai", model="m", input_tokens=5, output_tokens=3)
    delegate.generate.return_value = expected
    backend = CustomTextBackend(provider_id="custom-1", delegate=delegate, model="m")
    request = TextGenerationRequest(prompt="test")
    result = await backend.generate(request)
    assert result is expected
    delegate.generate.assert_called_once_with(request)


def test_custom_image_backend_properties():
    delegate = AsyncMock()
    delegate.capabilities = {ImageCapability.TEXT_TO_IMAGE}
    backend = CustomImageBackend(provider_id="custom-5", delegate=delegate, model="dall-e")
    assert backend.name == "custom-5"
    assert backend.model == "dall-e"


def test_custom_video_backend_properties():
    delegate = AsyncMock()
    delegate.capabilities = {VideoCapability.TEXT_TO_VIDEO}
    backend = CustomVideoBackend(provider_id="custom-7", delegate=delegate, model="kling-v3")
    assert backend.name == "custom-7"
    assert backend.model == "kling-v3"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_custom_backends.py -v`
Expected: FAIL — `lib.custom_provider.backends` 不存在

- [ ] **Step 3: 实现包装类**

```python
# lib/custom_provider/__init__.py
"""自定义供应商模块。"""

# lib/custom_provider/backends.py
"""自定义供应商 Backend 包装类。"""

from __future__ import annotations

from lib.image_backends.base import ImageBackend, ImageCapability, ImageGenerationRequest, ImageGenerationResult
from lib.text_backends.base import TextBackend, TextCapability, TextGenerationRequest, TextGenerationResult
from lib.video_backends.base import VideoBackend, VideoCapability, VideoGenerationRequest, VideoGenerationResult


class CustomTextBackend:
    """自定义供应商文本后端。委托给实际的 OpenAI/Gemini 后端，覆盖 name/model。"""

    def __init__(self, *, provider_id: str, delegate: TextBackend, model: str):
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[TextCapability]:
        return self._delegate.capabilities

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return await self._delegate.generate(request)


class CustomImageBackend:
    """自定义供应商图片后端。"""

    def __init__(self, *, provider_id: str, delegate: ImageBackend, model: str):
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return self._delegate.capabilities

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        return await self._delegate.generate(request)


class CustomVideoBackend:
    """自定义供应商视频后端。"""

    def __init__(self, *, provider_id: str, delegate: VideoBackend, model: str):
        self._provider_id = provider_id
        self._delegate = delegate
        self._model = model

    @property
    def name(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[VideoCapability]:
        return self._delegate.capabilities

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        return await self._delegate.generate(request)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_custom_backends.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add lib/custom_provider/__init__.py lib/custom_provider/backends.py tests/test_custom_backends.py
git commit -m "feat: 自定义供应商 Backend 包装类"
```

---

## Task 7: 自定义 Backend 工厂 + 模型发现

**Files:**
- Create: `lib/custom_provider/factory.py`
- Create: `lib/custom_provider/discovery.py`
- Test: `tests/test_custom_provider_factory.py`
- Test: `tests/test_model_discovery.py`

- [ ] **Step 1: 写失败测试 — 工厂**

```python
# tests/test_custom_provider_factory.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from lib.custom_provider.factory import create_custom_backend


async def test_create_openai_text_backend():
    mock_provider = MagicMock()
    mock_provider.api_format = "openai"
    mock_provider.base_url = "https://api.example.com/v1"
    mock_provider.api_key = "sk-test"
    mock_provider.provider_id = "custom-1"

    with patch("lib.custom_provider.factory.create_openai_client") as mock_client:
        mock_client.return_value = AsyncMock()
        backend = create_custom_backend(
            provider=mock_provider,
            model_id="deepseek-v3",
            media_type="text",
        )
    assert backend.name == "custom-1"
    assert backend.model == "deepseek-v3"


async def test_create_google_text_backend():
    mock_provider = MagicMock()
    mock_provider.api_format = "google"
    mock_provider.base_url = "https://generativelanguage.proxy.com"
    mock_provider.api_key = "AIza-test"
    mock_provider.provider_id = "custom-2"

    with patch("lib.custom_provider.factory.genai") as mock_genai:
        backend = create_custom_backend(
            provider=mock_provider,
            model_id="gemini-3-flash",
            media_type="text",
        )
    assert backend.name == "custom-2"
    assert backend.model == "gemini-3-flash"


async def test_unknown_media_type_raises():
    mock_provider = MagicMock()
    mock_provider.api_format = "openai"
    mock_provider.provider_id = "custom-1"
    with pytest.raises(ValueError, match="不支持的媒体类型"):
        create_custom_backend(provider=mock_provider, model_id="m", media_type="audio")
```

- [ ] **Step 2: 写失败测试 — 模型发现**

```python
# tests/test_model_discovery.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from lib.custom_provider.discovery import discover_models, infer_media_type


def test_infer_media_type_text():
    assert infer_media_type("deepseek-v3") == "text"
    assert infer_media_type("gpt-5.4-mini") == "text"


def test_infer_media_type_image():
    assert infer_media_type("gpt-image-1.5") == "image"
    assert infer_media_type("dall-e-3") == "image"


def test_infer_media_type_video():
    assert infer_media_type("sora-2") == "video"
    assert infer_media_type("kling-v3") == "video"
    assert infer_media_type("wan2.5-t2v") == "video"
    assert infer_media_type("seedance-1-5") == "video"
    assert infer_media_type("cogvideox-2") == "video"


async def test_discover_models_openai_format():
    with patch("lib.custom_provider.discovery.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        MockOpenAI.return_value = mock_client
        mock_model_1 = MagicMock()
        mock_model_1.id = "deepseek-v3"
        mock_model_2 = MagicMock()
        mock_model_2.id = "kling-v3"
        mock_client.models.list.return_value = MagicMock(data=[mock_model_1, mock_model_2])

        result = await discover_models("openai", "https://api.example.com/v1", "sk-test")
        assert len(result) == 2
        assert result[0]["model_id"] == "deepseek-v3"
        assert result[0]["media_type"] == "text"
        assert result[1]["model_id"] == "kling-v3"
        assert result[1]["media_type"] == "video"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_custom_provider_factory.py tests/test_model_discovery.py -v`
Expected: FAIL

- [ ] **Step 4: 实现工厂**

```python
# lib/custom_provider/factory.py
"""自定义 Backend 工厂。根据 api_format 和 media_type 创建包装后的 Backend。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.custom_provider.backends import CustomImageBackend, CustomTextBackend, CustomVideoBackend
from lib.openai_shared import create_openai_client

if TYPE_CHECKING:
    from lib.db.models.custom_provider import CustomProvider

try:
    from google import genai
except ImportError:
    genai = None


def create_custom_backend(
    *,
    provider: CustomProvider,
    model_id: str,
    media_type: str,
):
    """根据自定义供应商配置创建包装后的 Backend 实例。"""
    if provider.api_format == "openai":
        return _create_openai_delegate(provider, model_id, media_type)
    elif provider.api_format == "google":
        return _create_google_delegate(provider, model_id, media_type)
    raise ValueError(f"不支持的 API 格式: {provider.api_format}")


def _create_openai_delegate(provider, model_id: str, media_type: str):
    from lib.image_backends.openai import OpenAIImageBackend
    from lib.text_backends.openai import OpenAITextBackend
    from lib.video_backends.openai import OpenAIVideoBackend

    pid = provider.provider_id
    kwargs = {"api_key": provider.api_key, "base_url": provider.base_url, "model": model_id}

    if media_type == "text":
        delegate = OpenAITextBackend(**kwargs)
        return CustomTextBackend(provider_id=pid, delegate=delegate, model=model_id)
    elif media_type == "image":
        delegate = OpenAIImageBackend(**kwargs)
        return CustomImageBackend(provider_id=pid, delegate=delegate, model=model_id)
    elif media_type == "video":
        delegate = OpenAIVideoBackend(**kwargs)
        return CustomVideoBackend(provider_id=pid, delegate=delegate, model=model_id)
    raise ValueError(f"不支持的媒体类型: {media_type}")


def _create_google_delegate(provider, model_id: str, media_type: str):
    from lib.config.url_utils import normalize_base_url
    from lib.image_backends.gemini import GeminiImageBackend
    from lib.text_backends.gemini import GeminiTextBackend
    from lib.video_backends.gemini import GeminiVideoBackend

    pid = provider.provider_id
    kwargs = {"api_key": provider.api_key, "model": model_id}
    base_url = normalize_base_url(provider.base_url)
    if base_url:
        kwargs["base_url"] = base_url

    if media_type == "text":
        delegate = GeminiTextBackend(**kwargs)
        return CustomTextBackend(provider_id=pid, delegate=delegate, model=model_id)
    elif media_type == "image":
        delegate = GeminiImageBackend(**kwargs)
        return CustomImageBackend(provider_id=pid, delegate=delegate, model=model_id)
    elif media_type == "video":
        delegate = GeminiVideoBackend(**kwargs)
        return CustomVideoBackend(provider_id=pid, delegate=delegate, model=model_id)
    raise ValueError(f"不支持的媒体类型: {media_type}")
```

- [ ] **Step 5: 实现模型发现**

```python
# lib/custom_provider/discovery.py
"""模型自动发现与媒体类型推断。"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_IMAGE_KEYWORDS = re.compile(r"image|dall|img", re.IGNORECASE)
_VIDEO_KEYWORDS = re.compile(r"video|sora|kling|wan|seedance|cog|mochi|veo|pika", re.IGNORECASE)


def infer_media_type(model_id: str) -> str:
    """根据模型 ID 中的关键词推断媒体类型。"""
    if _IMAGE_KEYWORDS.search(model_id):
        return "image"
    if _VIDEO_KEYWORDS.search(model_id):
        return "video"
    return "text"


async def discover_models(
    api_format: str,
    base_url: str,
    api_key: str,
) -> list[dict]:
    """调用远程 API 发现可用模型并推断媒体类型。"""
    if api_format == "openai":
        return await _discover_openai(base_url, api_key)
    elif api_format == "google":
        return await _discover_google(base_url, api_key)
    raise ValueError(f"不支持的 API 格式: {api_format}")


async def _discover_openai(base_url: str, api_key: str) -> list[dict]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.models.list()
    models = sorted(response.data, key=lambda m: m.id)

    result = []
    defaults_set: set[str] = set()
    for m in models:
        media_type = infer_media_type(m.id)
        is_default = media_type not in defaults_set
        if is_default:
            defaults_set.add(media_type)
        result.append({
            "model_id": m.id,
            "display_name": m.id,
            "media_type": media_type,
            "is_default": is_default,
            "is_enabled": True,
        })
    return result


async def _discover_google(base_url: str, api_key: str) -> list[dict]:
    from google import genai
    from lib.config.url_utils import normalize_base_url

    effective_url = normalize_base_url(base_url)
    http_options = {"base_url": effective_url} if effective_url else None
    client = genai.Client(api_key=api_key, http_options=http_options)

    models_response = client.models.list()
    result = []
    defaults_set: set[str] = set()
    for m in models_response:
        model_id = m.name.replace("models/", "") if hasattr(m, "name") else str(m)
        media_type = _infer_google_media_type(m, model_id)
        is_default = media_type not in defaults_set
        if is_default:
            defaults_set.add(media_type)
        result.append({
            "model_id": model_id,
            "display_name": getattr(m, "display_name", model_id),
            "media_type": media_type,
            "is_default": is_default,
            "is_enabled": True,
        })
    return result


def _infer_google_media_type(model, model_id: str) -> str:
    """Google 模型媒体类型推断：优先使用 supported_generation_methods，回退关键词。"""
    methods = getattr(model, "supported_generation_methods", None)
    if methods:
        methods_str = " ".join(methods)
        if "generateImages" in methods_str:
            return "image"
        if "predictVideo" in methods_str:
            return "video"
        if "generateContent" in methods_str:
            return "text"
    return infer_media_type(model_id)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_custom_provider_factory.py tests/test_model_discovery.py -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add lib/custom_provider/factory.py lib/custom_provider/discovery.py tests/test_custom_provider_factory.py tests/test_model_discovery.py
git commit -m "feat: 自定义 Backend 工厂与模型发现"
```

---

## Task 8: CostCalculator + ConfigResolver 集成

**Files:**
- Modify: `lib/cost_calculator.py:346-408`
- Modify: `lib/config/resolver.py:211-227`
- Modify: `lib/text_backends/factory.py:11-40`
- Test: `tests/test_custom_cost.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_custom_cost.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from lib.cost_calculator import CostCalculator


def test_custom_text_cost():
    calc = CostCalculator()
    mock_model = MagicMock()
    mock_model.price_input = 1.0  # $1/M input tokens
    mock_model.price_output = 2.0  # $2/M output tokens
    mock_model.currency = "USD"

    with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
        amount, currency = calc.calculate_cost(
            "custom-3", "text", model="deepseek-v3",
            input_tokens=1000, output_tokens=500,
        )
    assert currency == "USD"
    assert abs(amount - (1000 * 1.0 + 500 * 2.0) / 1_000_000) < 0.0001


def test_custom_video_cost():
    calc = CostCalculator()
    mock_model = MagicMock()
    mock_model.price_input = 0.30  # ¥0.30/秒
    mock_model.currency = "CNY"

    with patch.object(calc, "_get_custom_model_price", return_value=mock_model):
        amount, currency = calc.calculate_cost(
            "custom-3", "video", model="kling-v3",
            duration_seconds=10,
        )
    assert currency == "CNY"
    assert abs(amount - 3.0) < 0.01


def test_custom_cost_null_price_returns_zero():
    calc = CostCalculator()
    with patch.object(calc, "_get_custom_model_price", return_value=None):
        amount, currency = calc.calculate_cost(
            "custom-3", "text", model="llama3",
            input_tokens=1000, output_tokens=500,
        )
    assert amount == 0.0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_custom_cost.py -v`
Expected: FAIL — `_get_custom_model_price` 不存在

- [ ] **Step 3: 实现 CostCalculator 扩展**

在 `lib/cost_calculator.py` 的 `CostCalculator` 类中添加：

```python
def _get_custom_model_price(self, provider: str, model: str):
    """从 DB 查询自定义供应商模型价格（同步，因为 CostCalculator 在同步上下文调用）。"""
    import asyncio
    from lib.db import safe_session_factory
    from lib.db.models.custom_provider import CustomProviderModel

    async def _query():
        async with safe_session_factory() as session:
            from sqlalchemy import select
            provider_db_id = int(provider.removeprefix("custom-"))
            stmt = select(CustomProviderModel).where(
                CustomProviderModel.provider_id == provider_db_id,
                CustomProviderModel.model_id == model,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _query()).result()
    return asyncio.run(_query())
```

在 `calculate_cost()` 方法的末尾（`return 0.0, "USD"` 之前）添加自定义供应商分支：

```python
if provider.startswith("custom-"):
    return self._calculate_custom_cost(
        provider, call_type, model=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        duration_seconds=duration_seconds,
    )

return 0.0, "USD"
```

添加 `_calculate_custom_cost` 方法：

```python
def _calculate_custom_cost(
    self, provider: str, call_type: str, *,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    duration_seconds: int | None = None,
) -> tuple[float, str]:
    price_info = self._get_custom_model_price(provider, model or "")
    if price_info is None or price_info.price_input is None:
        return 0.0, "USD"

    currency = price_info.currency or "USD"

    if call_type == "text":
        inp = (input_tokens or 0) * (price_info.price_input or 0)
        out = (output_tokens or 0) * (price_info.price_output or 0)
        return (inp + out) / 1_000_000, currency
    elif call_type == "image":
        return price_info.price_input, currency
    elif call_type == "video":
        return (duration_seconds or 8) * price_info.price_input, currency

    return 0.0, currency
```

- [ ] **Step 4: 实现 ConfigResolver 扩展**

在 `lib/config/resolver.py` 的 `_auto_resolve_backend()` 方法中，在抛出 ValueError 之前添加自定义供应商查询：

```python
async def _auto_resolve_backend(self, svc, media_type):
    # ... 现有预置供应商查询 ...

    # 查询自定义供应商
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository
    async with self._session_factory() as session:
        repo = CustomProviderRepository(session)
        models = await repo.list_enabled_models_by_media_type(media_type)
        for model in models:
            if model.is_default:
                provider = await repo.get_provider(model.provider_id)
                if provider:
                    return f"custom-{provider.id}", model.model_id

    raise ValueError(f"未找到可用的 {media_type} 供应商。请在「全局设置 → 供应商」页面配置至少一个供应商。")
```

在 `lib/text_backends/factory.py` 中扩展 `create_text_backend_for_task`，在现有逻辑前添加自定义供应商处理：

```python
async def create_text_backend_for_task(task_type, project_name=None):
    resolver = ConfigResolver(async_session_factory)
    provider_id, model_id = await resolver.text_backend_for_task(task_type, project_name)
    
    # 自定义供应商走独立路径
    if provider_id.startswith("custom-"):
        from lib.custom_provider.factory import create_custom_backend
        from lib.db import async_session_factory as sf
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository
        async with sf() as session:
            repo = CustomProviderRepository(session)
            db_id = int(provider_id.removeprefix("custom-"))
            provider = await repo.get_provider(db_id)
            if provider is None:
                raise ValueError(f"自定义供应商 {provider_id} 不存在")
            return create_custom_backend(provider=provider, model_id=model_id, media_type="text")

    # ... 现有预置供应商逻辑 ...
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_custom_cost.py -v`
Expected: PASS

- [ ] **Step 6: 运行全量测试**

Run: `uv run python -m pytest -x -q`
Expected: 全部 PASS，无回归

- [ ] **Step 7: 提交**

```bash
git add lib/cost_calculator.py lib/config/resolver.py lib/text_backends/factory.py tests/test_custom_cost.py
git commit -m "feat: CostCalculator 自定义价格 + ConfigResolver 自定义供应商集成"
```

---

## Task 9: 自定义供应商 API 路由

**Files:**
- Create: `server/routers/custom_providers.py`
- Modify: `server/app.py:26,178`
- Test: `tests/test_custom_providers_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_custom_providers_api.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel


@pytest.fixture
async def app_with_db():
    """创建带内存 DB 的测试应用。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    from server.routers.custom_providers import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    # Override DB dependency
    from lib.db import get_async_session
    async def override_session():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_async_session] = override_session

    yield app, factory
    await engine.dispose()


def test_create_custom_provider(app_with_db):
    # 这个测试验证 POST /api/v1/custom-providers 能创建供应商
    pass  # 将在 Step 3 后填充完整的 API 测试


def test_list_custom_providers(app_with_db):
    pass


def test_discover_models(app_with_db):
    pass


def test_test_connection(app_with_db):
    pass
```

- [ ] **Step 2: 实现 API 路由**

创建 `server/routers/custom_providers.py`，包含：

- Pydantic 请求/响应模型：`CreateProviderRequest`, `UpdateProviderRequest`, `ProviderResponse`, `ModelResponse`, `DiscoverRequest`, `TestConnectionRequest`
- 供应商 CRUD 端点：`GET /`, `POST /`, `GET /{id}`, `PATCH /{id}`, `DELETE /{id}`
- 模型管理端点：`PUT /{id}/models`, `POST /{id}/models`, `PATCH /{id}/models/{model_id}`, `DELETE /{id}/models/{model_id}`
- 无状态操作端点：`POST /discover`, `POST /test`
- `api_key` 在 GET 响应中使用 `mask_secret()` 掩蔽

路由使用 `CustomProviderRepository` 进行所有 DB 操作。`/discover` 调用 `lib/custom_provider/discovery.discover_models()`。`/test` 根据 api_format 执行连接测试（OpenAI: `client.models.list()`，Google: `client.models.list()`）。

- [ ] **Step 3: 注册路由**

在 `server/app.py` 中：

```python
# imports 部分添加
from server.routers import custom_providers

# 路由注册部分添加
app.include_router(custom_providers.router, prefix="/api/v1", tags=["自定义供应商"])
```

- [ ] **Step 4: 完善测试并运行**

Run: `uv run python -m pytest tests/test_custom_providers_api.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/routers/custom_providers.py server/app.py tests/test_custom_providers_api.py
git commit -m "feat: 自定义供应商 API 路由（CRUD + 模型发现 + 连接测试）"
```

---

## Task 10: system-config/options 合并 + 用量统计 display_name

**Files:**
- Modify: `server/routers/system_config.py:38-62` — `_build_options()` 追加自定义模型
- Modify: `lib/db/repositories/usage_repo.py:252-282` — stats 返回 `display_name`
- Modify: `frontend/src/components/pages/settings/UsageStatsSection.tsx:97` — 使用 `display_name`
- Test: `tests/test_system_config_options.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_system_config_options.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_build_options_includes_custom_models(session):
    """_build_options 应包含自定义供应商的已启用模型。"""
    provider = CustomProvider(
        display_name="Test",
        api_format="openai",
        base_url="https://test.com",
        api_key="sk-test",
    )
    session.add(provider)
    await session.flush()

    model = CustomProviderModel(
        provider_id=provider.id,
        model_id="deepseek-v3",
        display_name="DeepSeek V3",
        media_type="text",
        is_default=True,
        is_enabled=True,
    )
    session.add(model)
    await session.flush()

    from lib.db.repositories.custom_provider_repo import CustomProviderRepository
    repo = CustomProviderRepository(session)
    text_models = await repo.list_enabled_models_by_media_type("text")
    assert len(text_models) == 1
    assert text_models[0].model_id == "deepseek-v3"
```

- [ ] **Step 2: 实现 system-config options 合并**

在 `server/routers/system_config.py` 的 `_build_options()` 函数末尾追加：

```python
async def _build_options(svc: ConfigService) -> dict[str, list[str]]:
    # ... 现有代码 ...

    # 追加自定义供应商的模型
    from lib.db import async_session_factory
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository
    async with async_session_factory() as session:
        repo = CustomProviderRepository(session)
        providers = await repo.list_providers()
        for provider in providers:
            models = await repo.list_models(provider.id)
            for model in models:
                if not model.is_enabled:
                    continue
                full = f"custom-{provider.id}/{model.model_id}"
                if model.media_type == "video":
                    video_backends.append(full)
                elif model.media_type == "image":
                    image_backends.append(full)
                elif model.media_type == "text":
                    text_backends.append(full)

    return {
        "video_backends": video_backends,
        "image_backends": image_backends,
        "text_backends": text_backends,
    }
```

- [ ] **Step 3: 实现用量统计 display_name**

在 `lib/db/repositories/usage_repo.py` 的 `get_stats_grouped_by_provider()` 方法中，返回 stats 字典时，对 `custom-` 开头的 provider 查询 display_name：

```python
# 在构建 stats 列表后添加 display_name 解析
from lib.config.registry import PROVIDER_REGISTRY
from lib.db.models.custom_provider import CustomProvider

for stat in stats:
    provider_str = stat["provider"]
    if provider_str.startswith("custom-"):
        db_id = int(provider_str.removeprefix("custom-"))
        cp = await self.session.get(CustomProvider, db_id)
        stat["display_name"] = cp.display_name if cp else provider_str
    else:
        meta = PROVIDER_REGISTRY.get(provider_str)
        stat["display_name"] = meta.display_name if meta else provider_str
```

在 `frontend/src/components/pages/settings/UsageStatsSection.tsx:97` 中：

```tsx
<span className="text-sm font-medium text-gray-100">{s.display_name ?? s.provider}</span>
```

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_system_config_options.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add server/routers/system_config.py lib/db/repositories/usage_repo.py frontend/src/components/pages/settings/UsageStatsSection.tsx tests/test_system_config_options.py
git commit -m "feat: system-config options 合并自定义模型 + 用量统计 display_name"
```

---

## Task 11: 前端 — TypeScript 类型 + API 客户端

**Files:**
- Create: `frontend/src/types/custom-provider.ts`
- Modify: `frontend/src/api.ts` — 追加自定义供应商 API 方法

注意：此任务及后续前端任务执行前须先调用 `/frontend-design` skill。

- [ ] **Step 1: 创建 TypeScript 类型**

```typescript
// frontend/src/types/custom-provider.ts
export interface CustomProviderInfo {
  id: number;
  display_name: string;
  api_format: "openai" | "google";
  base_url: string;
  api_key_masked: string;
  models: CustomProviderModelInfo[];
  created_at: string;
}

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
}

export interface DiscoveredModel {
  model_id: string;
  display_name: string;
  media_type: "text" | "image" | "video";
  is_default: boolean;
  is_enabled: boolean;
}

export interface CustomProviderCreateRequest {
  display_name: string;
  api_format: "openai" | "google";
  base_url: string;
  api_key: string;
  models: CustomProviderModelInput[];
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
}
```

- [ ] **Step 2: 追加 API 客户端方法**

在 `frontend/src/api.ts` 中追加：

```typescript
// ==================== 自定义供应商 API ====================

static async listCustomProviders(): Promise<{ providers: CustomProviderInfo[] }> {
  return this.request("/custom-providers");
}

static async createCustomProvider(data: CustomProviderCreateRequest): Promise<CustomProviderInfo> {
  return this.request("/custom-providers", { method: "POST", body: JSON.stringify(data) });
}

static async getCustomProvider(id: number): Promise<CustomProviderInfo> {
  return this.request(`/custom-providers/${id}`);
}

static async updateCustomProvider(id: number, data: Partial<CustomProviderCreateRequest>): Promise<void> {
  return this.request(`/custom-providers/${id}`, { method: "PATCH", body: JSON.stringify(data) });
}

static async deleteCustomProvider(id: number): Promise<void> {
  return this.request(`/custom-providers/${id}`, { method: "DELETE" });
}

static async replaceCustomProviderModels(id: number, models: CustomProviderModelInput[]): Promise<void> {
  return this.request(`/custom-providers/${id}/models`, { method: "PUT", body: JSON.stringify({ models }) });
}

static async discoverModels(data: { api_format: string; base_url: string; api_key: string }): Promise<{ models: DiscoveredModel[] }> {
  return this.request("/custom-providers/discover", { method: "POST", body: JSON.stringify(data) });
}

static async testCustomConnection(data: { api_format: string; base_url: string; api_key: string }): Promise<{ success: boolean; message: string }> {
  return this.request("/custom-providers/test", { method: "POST", body: JSON.stringify(data) });
}
```

- [ ] **Step 3: 运行 typecheck**

Run: `cd frontend && pnpm typecheck`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add frontend/src/types/custom-provider.ts frontend/src/api.ts
git commit -m "feat: 前端自定义供应商 TypeScript 类型与 API 客户端"
```

---

## Task 12: 前端 — 自定义供应商 UI 组件

**Files:**
- Create: `frontend/src/components/pages/settings/CustomProviderSection.tsx`
- Create: `frontend/src/components/pages/settings/CustomProviderForm.tsx`
- Create: `frontend/src/components/pages/settings/CustomProviderDetail.tsx`
- Modify: `frontend/src/components/pages/ProviderSection.tsx` — 集成自定义供应商分区

**重要**：执行此任务前必须先调用 `/frontend-design` skill。

- [ ] **Step 1: 创建 CustomProviderSection 组件**

自定义供应商列表，显示在预置供应商列表下方，包含状态指示和「+ 添加」按钮。

- [ ] **Step 2: 创建 CustomProviderForm 组件**

新建/编辑表单，包含：
- 基础信息（名称、API 格式下拉、Base URL、API Key）
- 「获取模型列表」按钮 → 调用 `API.discoverModels()`
- 模型列表编辑（勾选启用、修正媒体类型、标记默认、填写价格）
- 「测试连接」按钮 → 调用 `API.testCustomConnection()`
- 「保存」→ 一次性提交

- [ ] **Step 3: 创建 CustomProviderDetail 组件**

展示已保存的自定义供应商详情，支持编辑和删除。

- [ ] **Step 4: 集成到 ProviderSection**

在 `ProviderSection.tsx` 的供应商列表下方添加自定义供应商分区，使用分隔线区隔。

- [ ] **Step 5: 运行前端检查**

Run: `cd frontend && pnpm check`
Expected: typecheck + test 均 PASS

- [ ] **Step 6: 提交**

```bash
git add frontend/src/components/pages/settings/CustomProviderSection.tsx frontend/src/components/pages/settings/CustomProviderForm.tsx frontend/src/components/pages/settings/CustomProviderDetail.tsx frontend/src/components/pages/ProviderSection.tsx
git commit -m "feat: 前端自定义供应商 UI（列表 + 新建/编辑 + 详情）"
```

---

## Task 13: 端到端验证 + 最终清理

**Files:**
- 所有已修改文件

- [ ] **Step 1: 运行后端全量测试**

Run: `uv run python -m pytest -x -q`
Expected: 全部 PASS

- [ ] **Step 2: 运行 lint + format**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: 无错误

- [ ] **Step 3: 运行前端检查**

Run: `cd frontend && pnpm check`
Expected: PASS

- [ ] **Step 4: 手动烟雾测试**

启动开发服务器，验证完整流程：
1. 在设置页看到自定义供应商分区
2. 点击「+ 添加自定义供应商」，填写 OpenAI 兼容中转站信息
3. 点击「获取模型列表」，验证模型发现正常
4. 修正媒体类型和价格，保存
5. 在模型选择下拉框中看到自定义模型
6. 在用量统计中看到 display_name

Run:
```bash
uv run uvicorn server.app:app --reload --port 1241 &
cd frontend && pnpm dev
```

- [ ] **Step 5: 提交最终状态**

```bash
git add -A
git commit -m "chore: 自定义供应商端到端验证通过"
```
