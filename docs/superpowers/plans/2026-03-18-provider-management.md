# Provider Management Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate system configuration from JSON files to the database, add provider management API and frontend page, and support per-provider independent concurrency/rate limiting.

**Architecture:** 数据层（ORM + Repository）→ 业务层（ConfigService + Provider Registry）→ API 层（providers + system_config 重构）→ 前端（侧边栏布局 + 供应商管理 + 模型选择 + 用量统计）。所有 `os.environ.get()` 配置读取迁移到 ConfigService。GenerationWorker 从全局池改为按供应商分池。

**Tech Stack:** SQLAlchemy Async ORM, Alembic, FastAPI, Pydantic v2, React 19, TypeScript, Tailwind CSS 4, zustand, wouter, lucide-react, @lobehub/icons

**Spec:** `docs/superpowers/specs/2026-03-18-provider-management-design.md`

---

### Task 1: DB 模型 — ProviderConfig + SystemSetting

**Files:**
- Create: `lib/db/models/config.py`
- Modify: `lib/db/models/__init__.py`
- Modify: `lib/db/base.py` (确认 Base 导出)
- Test: `tests/test_config_models.py`

- [ ] **Step 1: 写 ProviderConfig 和 SystemSetting 模型测试**

```python
# tests/test_config_models.py
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import async_sessionmaker

from lib.db.base import Base
from lib.db.models.config import ProviderConfig, SystemSetting


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()


async def test_provider_config_crud(session: AsyncSession):
    row = ProviderConfig(
        provider="gemini-aistudio",
        key="api_key",
        value="AIza-test",
        is_secret=True,
    )
    session.add(row)
    await session.flush()

    result = await session.execute(
        select(ProviderConfig).where(ProviderConfig.provider == "gemini-aistudio")
    )
    found = result.scalar_one()
    assert found.key == "api_key"
    assert found.value == "AIza-test"
    assert found.is_secret is True
    assert found.updated_at is not None


async def test_provider_config_unique_constraint(session: AsyncSession):
    row1 = ProviderConfig(provider="gemini-aistudio", key="api_key", value="v1", is_secret=True)
    row2 = ProviderConfig(provider="gemini-aistudio", key="api_key", value="v2", is_secret=True)
    session.add(row1)
    await session.flush()
    session.add(row2)
    with pytest.raises(Exception):  # IntegrityError
        await session.flush()


async def test_system_setting_crud(session: AsyncSession):
    row = SystemSetting(key="default_video_backend", value="gemini-vertex/veo-3.1-fast-generate-001")
    session.add(row)
    await session.flush()

    result = await session.execute(
        select(SystemSetting).where(SystemSetting.key == "default_video_backend")
    )
    found = result.scalar_one()
    assert found.value == "gemini-vertex/veo-3.1-fast-generate-001"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_config_models.py -v`
Expected: ImportError — `lib.db.models.config` 不存在

- [ ] **Step 3: 实现 ORM 模型**

```python
# lib/db/models/config.py
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProviderConfig(Base):
    __tablename__ = "provider_config"
    __table_args__ = (
        UniqueConstraint("provider", "key", name="uq_provider_key"),
        Index("ix_provider_config_provider", "provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )


class SystemSetting(Base):
    __tablename__ = "system_setting"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_utc_now, onupdate=_utc_now
    )
```

- [ ] **Step 4: 更新模型导出**

在 `lib/db/models/__init__.py` 中添加:

```python
from lib.db.models.config import ProviderConfig, SystemSetting
```

- [ ] **Step 5: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_config_models.py -v`
Expected: 3 tests PASS

- [ ] **Step 6: 生成 Alembic 迁移**

Run: `uv run alembic revision --autogenerate -m "add provider_config and system_setting tables"`
Run: `uv run alembic upgrade head`

- [ ] **Step 7: Commit**

```bash
git add lib/db/models/config.py lib/db/models/__init__.py tests/test_config_models.py alembic/versions/
git commit -m "feat(db): add ProviderConfig and SystemSetting ORM models"
```

---

### Task 2: Provider Registry — 静态元数据

**Files:**
- Create: `lib/config/registry.py`
- Create: `lib/config/__init__.py`
- Test: `tests/test_config_registry.py`

- [ ] **Step 1: 写 registry 测试**

```python
# tests/test_config_registry.py
from lib.config.registry import PROVIDER_REGISTRY, ProviderMeta


def test_all_providers_registered():
    assert set(PROVIDER_REGISTRY.keys()) == {
        "gemini-aistudio", "gemini-vertex", "seedance", "grok"
    }


def test_provider_meta_fields():
    meta = PROVIDER_REGISTRY["gemini-aistudio"]
    assert isinstance(meta, ProviderMeta)
    assert meta.display_name == "Gemini AI Studio"
    assert "video" in meta.media_types
    assert "image" in meta.media_types
    assert "api_key" in meta.required_keys
    assert "api_key" in meta.secret_keys
    assert "text_to_video" in meta.capabilities


def test_seedance_video_only():
    meta = PROVIDER_REGISTRY["seedance"]
    assert meta.media_types == ["video"]
    assert "image" not in meta.media_types


def test_required_keys_are_subset_of_all_keys():
    for name, meta in PROVIDER_REGISTRY.items():
        all_keys = set(meta.required_keys) | set(meta.optional_keys)
        for rk in meta.required_keys:
            assert rk in all_keys, f"{name}: required key {rk} not in all keys"


def test_secret_keys_are_subset_of_required_or_optional():
    for name, meta in PROVIDER_REGISTRY.items():
        all_keys = set(meta.required_keys) | set(meta.optional_keys)
        for sk in meta.secret_keys:
            assert sk in all_keys, f"{name}: secret key {sk} not in all keys"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_config_registry.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 registry**

```python
# lib/config/__init__.py
"""Configuration management package."""

# lib/config/registry.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderMeta:
    display_name: str
    media_types: list[str]
    required_keys: list[str]
    optional_keys: list[str] = field(default_factory=list)
    secret_keys: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)


PROVIDER_REGISTRY: dict[str, ProviderMeta] = {
    "gemini-aistudio": ProviderMeta(
        display_name="Gemini AI Studio",
        media_types=["video", "image"],
        required_keys=["api_key"],
        optional_keys=["base_url", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=["api_key"],
        capabilities=["text_to_video", "image_to_video", "text_to_image", "negative_prompt", "video_extend"],
    ),
    "gemini-vertex": ProviderMeta(
        display_name="Gemini Vertex AI",
        media_types=["video", "image"],
        required_keys=["credentials_path"],
        optional_keys=["gcs_bucket", "image_rpm", "video_rpm", "request_gap", "image_max_workers", "video_max_workers"],
        secret_keys=[],
        capabilities=["text_to_video", "image_to_video", "text_to_image", "generate_audio", "negative_prompt", "video_extend"],
    ),
    "seedance": ProviderMeta(
        display_name="Seedance",
        media_types=["video"],
        required_keys=["api_key"],
        optional_keys=["file_service_base_url", "video_rpm", "request_gap", "video_max_workers"],
        secret_keys=["api_key"],
        capabilities=["text_to_video", "image_to_video", "generate_audio", "seed_control", "flex_tier"],
    ),
    "grok": ProviderMeta(
        display_name="Grok",
        media_types=["video"],
        required_keys=["api_key"],
        optional_keys=["video_rpm", "request_gap", "video_max_workers"],
        secret_keys=["api_key"],
        capabilities=["text_to_video", "image_to_video"],
    ),
}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_config_registry.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/config/ tests/test_config_registry.py
git commit -m "feat(config): add provider registry with static metadata"
```

---

### Task 3: Repositories — ProviderConfigRepository + SystemSettingRepository

**Files:**
- Create: `lib/config/repository.py`
- Test: `tests/test_config_repository.py`

- [ ] **Step 1: 写 repository 测试**

```python
# tests/test_config_repository.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import async_sessionmaker

from lib.db.base import Base
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()


# --- ProviderConfigRepository ---

async def test_set_and_get(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("gemini-aistudio", "api_key", "AIza-test", is_secret=True)
    config = await repo.get_all("gemini-aistudio")
    assert config == {"api_key": "AIza-test"}


async def test_set_overwrites(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("gemini-aistudio", "api_key", "old", is_secret=True)
    await repo.set("gemini-aistudio", "api_key", "new", is_secret=True)
    config = await repo.get_all("gemini-aistudio")
    assert config == {"api_key": "new"}


async def test_delete(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("grok", "api_key", "xai-test", is_secret=True)
    await repo.delete("grok", "api_key")
    config = await repo.get_all("grok")
    assert config == {}


async def test_get_secrets_masked(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("gemini-aistudio", "api_key", "AIzaSyD-longkey123", is_secret=True)
    await repo.set("gemini-aistudio", "base_url", "https://example.com", is_secret=False)
    masked = await repo.get_all_masked("gemini-aistudio")
    assert masked["api_key"]["is_set"] is True
    assert "AIzaSyD" not in masked["api_key"]["masked"]  # 值被掩码
    assert masked["base_url"]["is_set"] is True
    assert masked["base_url"]["value"] == "https://example.com"


async def test_get_configured_keys(session: AsyncSession):
    repo = ProviderConfigRepository(session)
    await repo.set("seedance", "api_key", "ark-test", is_secret=True)
    keys = await repo.get_configured_keys("seedance")
    assert keys == ["api_key"]


# --- SystemSettingRepository ---

async def test_setting_set_and_get(session: AsyncSession):
    repo = SystemSettingRepository(session)
    await repo.set("default_video_backend", "gemini-vertex/veo-3.1-fast-generate-001")
    val = await repo.get("default_video_backend")
    assert val == "gemini-vertex/veo-3.1-fast-generate-001"


async def test_setting_get_default(session: AsyncSession):
    repo = SystemSettingRepository(session)
    val = await repo.get("nonexistent", default="fallback")
    assert val == "fallback"


async def test_setting_get_all(session: AsyncSession):
    repo = SystemSettingRepository(session)
    await repo.set("key1", "val1")
    await repo.set("key2", "val2")
    all_settings = await repo.get_all()
    assert all_settings == {"key1": "val1", "key2": "val2"}
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_config_repository.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 repositories**

```python
# lib/config/repository.py
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.config import ProviderConfig, SystemSetting


def _mask_value(value: str) -> str:
    """Mask a secret value, showing first 4 and last 4 chars."""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}…{value[-4:]}"


class ProviderConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set(
        self, provider: str, key: str, value: str, *, is_secret: bool = False
    ) -> None:
        stmt = select(ProviderConfig).where(
            ProviderConfig.provider == provider, ProviderConfig.key == key
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            row.value = value
            row.is_secret = is_secret
            row.updated_at = datetime.now(timezone.utc)
        else:
            self.session.add(
                ProviderConfig(
                    provider=provider, key=key, value=value, is_secret=is_secret
                )
            )
        await self.session.flush()

    async def delete(self, provider: str, key: str) -> None:
        stmt = delete(ProviderConfig).where(
            ProviderConfig.provider == provider, ProviderConfig.key == key
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def get_all(self, provider: str) -> dict[str, str]:
        stmt = select(ProviderConfig).where(ProviderConfig.provider == provider)
        result = await self.session.execute(stmt)
        return {row.key: row.value for row in result.scalars()}

    async def get_all_masked(self, provider: str) -> dict[str, dict]:
        stmt = select(ProviderConfig).where(ProviderConfig.provider == provider)
        result = await self.session.execute(stmt)
        out: dict[str, dict] = {}
        for row in result.scalars():
            if row.is_secret:
                out[row.key] = {"is_set": True, "masked": _mask_value(row.value)}
            else:
                out[row.key] = {"is_set": True, "value": row.value}
        return out

    async def get_configured_keys(self, provider: str) -> list[str]:
        stmt = select(ProviderConfig.key).where(ProviderConfig.provider == provider)
        result = await self.session.execute(stmt)
        return [row for row in result.scalars()]


class SystemSettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set(self, key: str, value: str) -> None:
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            row.value = value
            row.updated_at = datetime.now(timezone.utc)
        else:
            self.session.add(SystemSetting(key=key, value=value))
        await self.session.flush()

    async def get(self, key: str, default: str = "") -> str:
        stmt = select(SystemSetting.value).where(SystemSetting.key == key)
        result = await self.session.execute(stmt)
        val = result.scalar_one_or_none()
        return val if val is not None else default

    async def get_all(self) -> dict[str, str]:
        stmt = select(SystemSetting)
        result = await self.session.execute(stmt)
        return {row.key: row.value for row in result.scalars()}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_config_repository.py -v`
Expected: 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/config/repository.py tests/test_config_repository.py
git commit -m "feat(config): add ProviderConfig and SystemSetting repositories"
```

---

### Task 4: ConfigService — 业务逻辑层

**Files:**
- Create: `lib/config/service.py`
- Test: `tests/test_config_service.py`

- [ ] **Step 1: 写 ConfigService 测试**

```python
# tests/test_config_service.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import async_sessionmaker

from lib.db.base import Base
from lib.config.service import ConfigService


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def config_service(session: AsyncSession) -> ConfigService:
    return ConfigService(session)


async def test_get_all_providers_status_empty(config_service: ConfigService):
    statuses = await config_service.get_all_providers_status()
    assert len(statuses) == 4
    for s in statuses:
        assert s.status == "unconfigured"


async def test_provider_becomes_ready(config_service: ConfigService):
    await config_service.set_provider_config("gemini-aistudio", "api_key", "AIza-test")
    statuses = await config_service.get_all_providers_status()
    aistudio = next(s for s in statuses if s.name == "gemini-aistudio")
    assert aistudio.status == "ready"
    assert "api_key" in aistudio.configured_keys
    assert aistudio.missing_keys == []


async def test_get_provider_config(config_service: ConfigService):
    await config_service.set_provider_config("grok", "api_key", "xai-test")
    config = await config_service.get_provider_config("grok")
    assert config == {"api_key": "xai-test"}


async def test_delete_provider_config(config_service: ConfigService):
    await config_service.set_provider_config("grok", "api_key", "xai-test")
    await config_service.delete_provider_config("grok", "api_key")
    config = await config_service.get_provider_config("grok")
    assert config == {}


async def test_system_settings(config_service: ConfigService):
    await config_service.set_setting("default_video_backend", "gemini-vertex/veo-3.1-fast-generate-001")
    val = await config_service.get_setting("default_video_backend")
    assert val == "gemini-vertex/veo-3.1-fast-generate-001"


async def test_get_default_video_backend(config_service: ConfigService):
    await config_service.set_setting("default_video_backend", "seedance/doubao-seedance-1-5-pro-251215")
    provider_id, model_id = await config_service.get_default_video_backend()
    assert provider_id == "seedance"
    assert model_id == "doubao-seedance-1-5-pro-251215"


async def test_get_default_backend_fallback(config_service: ConfigService):
    # 未设置时应返回合理默认值
    provider_id, model_id = await config_service.get_default_video_backend()
    assert provider_id == "gemini-aistudio"  # 默认回退


async def test_unknown_provider_raises(config_service: ConfigService):
    with pytest.raises(ValueError, match="Unknown provider"):
        await config_service.set_provider_config("unknown-provider", "key", "val")
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_config_service.py -v`
Expected: ImportError

- [ ] **Step 3: 实现 ConfigService**

```python
# lib/config/service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository

# 默认回退值
_DEFAULT_VIDEO_BACKEND = "gemini-aistudio/veo-3.1-generate-001"
_DEFAULT_IMAGE_BACKEND = "gemini-aistudio/gemini-3.1-flash-image-preview"


@dataclass
class ProviderStatus:
    name: str
    display_name: str
    status: Literal["ready", "unconfigured", "error"]
    media_types: list[str]
    capabilities: list[str]
    required_keys: list[str]
    configured_keys: list[str]
    missing_keys: list[str]


class ConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self._provider_repo = ProviderConfigRepository(session)
        self._setting_repo = SystemSettingRepository(session)

    # --- Provider config ---

    async def get_provider_config(self, provider: str) -> dict[str, str]:
        self._validate_provider(provider)
        return await self._provider_repo.get_all(provider)

    async def set_provider_config(self, provider: str, key: str, value: str) -> None:
        self._validate_provider(provider)
        meta = PROVIDER_REGISTRY[provider]
        is_secret = key in meta.secret_keys
        await self._provider_repo.set(provider, key, value, is_secret=is_secret)

    async def delete_provider_config(self, provider: str, key: str) -> None:
        self._validate_provider(provider)
        await self._provider_repo.delete(provider, key)

    async def get_all_providers_status(self) -> list[ProviderStatus]:
        statuses = []
        for name, meta in PROVIDER_REGISTRY.items():
            configured = await self._provider_repo.get_configured_keys(name)
            missing = [k for k in meta.required_keys if k not in configured]
            status: Literal["ready", "unconfigured", "error"] = (
                "ready" if not missing else "unconfigured"
            )
            statuses.append(
                ProviderStatus(
                    name=name,
                    display_name=meta.display_name,
                    status=status,
                    media_types=list(meta.media_types),
                    capabilities=list(meta.capabilities),
                    required_keys=list(meta.required_keys),
                    configured_keys=configured,
                    missing_keys=missing,
                )
            )
        return statuses

    async def get_provider_config_masked(self, provider: str) -> dict[str, dict]:
        self._validate_provider(provider)
        return await self._provider_repo.get_all_masked(provider)

    # --- System settings ---

    async def get_setting(self, key: str, default: str = "") -> str:
        return await self._setting_repo.get(key, default)

    async def set_setting(self, key: str, value: str) -> None:
        await self._setting_repo.set(key, value)

    # --- Convenience ---

    async def get_default_video_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_video_backend", _DEFAULT_VIDEO_BACKEND)
        return self._parse_backend(raw, _DEFAULT_VIDEO_BACKEND)

    async def get_default_image_backend(self) -> tuple[str, str]:
        raw = await self._setting_repo.get("default_image_backend", _DEFAULT_IMAGE_BACKEND)
        return self._parse_backend(raw, _DEFAULT_IMAGE_BACKEND)

    # --- Private ---

    @staticmethod
    def _validate_provider(provider: str) -> None:
        if provider not in PROVIDER_REGISTRY:
            raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def _parse_backend(raw: str, fallback: str) -> tuple[str, str]:
        if "/" in raw:
            provider_id, model_id = raw.split("/", 1)
            return provider_id, model_id
        # 无效格式，返回 fallback
        parts = fallback.split("/", 1)
        return parts[0], parts[1]
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_config_service.py -v`
Expected: 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/config/service.py tests/test_config_service.py
git commit -m "feat(config): add ConfigService business logic layer"
```

---

### Task 5: JSON → DB 迁移

**Files:**
- Create: `lib/config/migration.py`
- Test: `tests/test_config_migration.py`

- [ ] **Step 1: 写迁移测试**

```python
# tests/test_config_migration.py
import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import async_sessionmaker

from lib.db.base import Base
from lib.config.migration import migrate_json_to_db
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def json_file(tmp_path: Path) -> Path:
    data = {
        "version": 1,
        "overrides": {
            "gemini_api_key": "AIza-test-key",
            "video_backend": "vertex",
            "image_backend": "aistudio",
            "video_model": "veo-3.1-fast-generate-001",
            "image_model": "gemini-3.1-flash-image-preview",
            "video_generate_audio": False,
            "anthropic_api_key": "sk-ant-test",
            "anthropic_base_url": "https://proxy.example.com",
            "gemini_image_rpm": 15,
            "gemini_video_rpm": 10,
            "gemini_request_gap": 3.1,
            "image_max_workers": 3,
            "video_max_workers": 2,
            "ark_api_key": "ark-test-key",
        },
    }
    p = tmp_path / ".system_config.json"
    p.write_text(json.dumps(data))
    return p


async def test_migrate_provider_configs(session: AsyncSession, json_file: Path):
    await migrate_json_to_db(session, json_file)
    repo = ProviderConfigRepository(session)

    # Gemini AI Studio gets the API key
    config = await repo.get_all("gemini-aistudio")
    assert config["api_key"] == "AIza-test-key"
    assert config["image_rpm"] == "15"

    # Seedance gets ARK key
    config = await repo.get_all("seedance")
    assert config["api_key"] == "ark-test-key"


async def test_migrate_system_settings(session: AsyncSession, json_file: Path):
    await migrate_json_to_db(session, json_file)
    repo = SystemSettingRepository(session)

    # video_backend + video_model combined
    val = await repo.get("default_video_backend")
    assert val == "gemini-vertex/veo-3.1-fast-generate-001"

    val = await repo.get("default_image_backend")
    assert val == "gemini-aistudio/gemini-3.1-flash-image-preview"

    val = await repo.get("anthropic_api_key")
    assert val == "sk-ant-test"


async def test_migrate_renames_file(session: AsyncSession, json_file: Path):
    await migrate_json_to_db(session, json_file)
    assert not json_file.exists()
    assert json_file.with_suffix(".json.bak").exists()


async def test_migrate_max_workers_to_all_configured_providers(
    session: AsyncSession, json_file: Path
):
    await migrate_json_to_db(session, json_file)
    repo = ProviderConfigRepository(session)

    # Seedance was configured (has ark_api_key), so gets video_max_workers
    seedance = await repo.get_all("seedance")
    assert seedance.get("video_max_workers") == "2"

    # Grok was NOT configured, should NOT get max_workers
    grok = await repo.get_all("grok")
    assert "video_max_workers" not in grok
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_config_migration.py -v`
Expected: ImportError

- [ ] **Step 3: 实现迁移逻辑**

```python
# lib/config/migration.py
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.registry import PROVIDER_REGISTRY
from lib.config.repository import ProviderConfigRepository, SystemSettingRepository

logger = logging.getLogger(__name__)

# JSON key → (target_table, provider, config_key, is_secret)
_PROVIDER_KEY_MAP: list[tuple[str, str, str, bool]] = [
    ("gemini_api_key", "gemini-aistudio", "api_key", True),
    ("gemini_base_url", "gemini-aistudio", "base_url", False),
    ("vertex_gcs_bucket", "gemini-vertex", "gcs_bucket", False),
    ("ark_api_key", "seedance", "api_key", True),
    ("file_service_base_url", "seedance", "file_service_base_url", False),
    ("xai_api_key", "grok", "api_key", True),
]

# Gemini rate limit keys → write to both aistudio and vertex
_GEMINI_RATE_KEYS: list[tuple[str, str]] = [
    ("gemini_image_rpm", "image_rpm"),
    ("gemini_video_rpm", "video_rpm"),
    ("gemini_request_gap", "request_gap"),
]

# Keys that go to system_setting directly
_SYSTEM_SETTING_KEYS: list[str] = [
    "video_generate_audio",
    "anthropic_api_key",
    "anthropic_base_url",
    "anthropic_model",
    "anthropic_default_haiku_model",
    "anthropic_default_opus_model",
    "anthropic_default_sonnet_model",
    "claude_code_subagent_model",
]

# Keys handled specially (not passed through to system_setting)
_HANDLED_KEYS = {
    "gemini_api_key", "gemini_base_url", "vertex_gcs_bucket",
    "ark_api_key", "file_service_base_url", "xai_api_key",
    "gemini_image_rpm", "gemini_video_rpm", "gemini_request_gap",
    "image_max_workers", "video_max_workers",
    "image_backend", "video_backend", "video_model", "image_model",
    "version", "updated_at",
} | set(_SYSTEM_SETTING_KEYS)


async def migrate_json_to_db(session: AsyncSession, json_path: Path) -> None:
    """Migrate .system_config.json to database, then rename to .bak."""
    if not json_path.exists():
        return

    logger.info("Migrating %s to database...", json_path)
    data = json.loads(json_path.read_text())
    overrides: dict = data.get("overrides", {})

    provider_repo = ProviderConfigRepository(session)
    setting_repo = SystemSettingRepository(session)

    # 1. Provider-specific keys
    for json_key, provider, config_key, is_secret in _PROVIDER_KEY_MAP:
        value = overrides.get(json_key)
        if value is not None:
            await provider_repo.set(provider, config_key, str(value), is_secret=is_secret)

    # 2. Gemini rate limit keys → both aistudio and vertex
    for json_key, config_key in _GEMINI_RATE_KEYS:
        value = overrides.get(json_key)
        if value is not None:
            for p in ("gemini-aistudio", "gemini-vertex"):
                await provider_repo.set(p, config_key, str(value), is_secret=False)

    # 3. Combined backend fields: image_backend + image_model → default_image_backend
    image_backend = overrides.get("image_backend", "aistudio")
    image_model = overrides.get("image_model", "gemini-3.1-flash-image-preview")
    await setting_repo.set(
        "default_image_backend", f"gemini-{image_backend}/{image_model}"
    )

    video_backend = overrides.get("video_backend", "aistudio")
    video_model = overrides.get("video_model", "veo-3.1-generate-001")
    await setting_repo.set(
        "default_video_backend", f"gemini-{video_backend}/{video_model}"
    )

    # 4. System setting keys
    for key in _SYSTEM_SETTING_KEYS:
        value = overrides.get(key)
        if value is not None:
            await setting_repo.set(key, str(value))

    # 5. max_workers → write to all *configured* providers that support the media type
    configured_providers = set()
    for json_key, provider, _, _ in _PROVIDER_KEY_MAP:
        if overrides.get(json_key) is not None:
            configured_providers.add(provider)

    image_max = overrides.get("image_max_workers")
    video_max = overrides.get("video_max_workers")

    for provider_id in configured_providers:
        meta = PROVIDER_REGISTRY.get(provider_id)
        if not meta:
            continue
        if image_max is not None and "image" in meta.media_types:
            await provider_repo.set(provider_id, "image_max_workers", str(image_max), is_secret=False)
        if video_max is not None and "video" in meta.media_types:
            await provider_repo.set(provider_id, "video_max_workers", str(video_max), is_secret=False)

    # 6. Catch-all: any remaining override keys → system_setting
    for key, value in overrides.items():
        if key not in _HANDLED_KEYS:
            await setting_repo.set(key, str(value))

    await session.commit()

    # 7. Rename to .bak
    bak_path = json_path.with_suffix(".json.bak")
    json_path.rename(bak_path)
    logger.info("Migration complete. Renamed to %s", bak_path)
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_config_migration.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add lib/config/migration.py tests/test_config_migration.py
git commit -m "feat(config): add JSON to DB migration"
```

---

### Task 6: Providers API Router

**Files:**
- Create: `server/routers/providers.py`
- Modify: `server/app.py` (注册路由)
- Test: `tests/test_providers_api.py`

- [ ] **Step 1: 写 API 测试**

测试关键端点：GET /providers、GET /providers/{id}/config、PATCH /providers/{id}/config。

```python
# tests/test_providers_api.py
import pytest
from httpx import ASGITransport, AsyncClient

from server.app import app


@pytest.fixture
async def client():
    """创建测试客户端 — 注意需要适配项目的 app 初始化逻辑"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_list_providers(client: AsyncClient):
    resp = await client.get("/api/v1/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    ids = [p["id"] for p in data["providers"]]
    assert "gemini-aistudio" in ids
    assert "seedance" in ids


async def test_get_provider_config(client: AsyncClient):
    resp = await client.get("/api/v1/providers/gemini-aistudio/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "gemini-aistudio"
    assert "fields" in data


async def test_get_unknown_provider(client: AsyncClient):
    resp = await client.get("/api/v1/providers/nonexistent/config")
    assert resp.status_code == 404


async def test_patch_provider_config(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/providers/gemini-aistudio/config",
        json={"api_key": "AIza-new-test"},
    )
    assert resp.status_code == 204

    # 验证已保存
    resp = await client.get("/api/v1/providers/gemini-aistudio/config")
    fields = {f["key"]: f for f in resp.json()["fields"]}
    assert fields["api_key"]["is_set"] is True


async def test_patch_null_clears(client: AsyncClient):
    # 先设置
    await client.patch(
        "/api/v1/providers/grok/config",
        json={"api_key": "xai-test"},
    )
    # 再清除
    resp = await client.patch(
        "/api/v1/providers/grok/config",
        json={"api_key": None},
    )
    assert resp.status_code == 204

    resp = await client.get("/api/v1/providers/grok/config")
    fields = {f["key"]: f for f in resp.json()["fields"]}
    assert fields["api_key"]["is_set"] is False
```

> **注意**: API 测试可能需要适配项目现有的测试基础设施（DB 初始化、认证绕过等）。实现时参考 `tests/conftest.py` 中已有的 fixture 模式。

- [ ] **Step 2: Run tests to confirm they fail**

Run: `uv run python -m pytest tests/test_providers_api.py -v`
Expected: ImportError 或 404

- [ ] **Step 3: 实现 providers router**

在 `server/routers/providers.py` 中实现以下端点：
- `GET /api/v1/providers` — 调用 `config_service.get_all_providers_status()`
- `GET /api/v1/providers/{id}/config` — 返回 provider fields（合并 registry 元数据 + DB 值）
- `PATCH /api/v1/providers/{id}/config` — 遍历 body fields，null 则 delete，否则 set
- `POST /api/v1/providers/{id}/test` — 连接测试（复用/重构现有 `connection-test` 逻辑）
- `POST /api/v1/providers/gemini-vertex/credentials` — 移植现有 Vertex 上传逻辑

路由前缀: `router = APIRouter(prefix="/api/v1/providers", tags=["providers"])`

依赖注入: ConfigService 通过 FastAPI Depends 获取 AsyncSession，创建 ConfigService 实例。

- [ ] **Step 4: 在 `server/app.py` 中注册路由**

```python
from server.routers.providers import router as providers_router
app.include_router(providers_router)
```

- [ ] **Step 5: Run tests to confirm they pass**

Run: `uv run python -m pytest tests/test_providers_api.py -v`
Expected: 5 tests PASS

- [ ] **Step 6: Commit**

```bash
git add server/routers/providers.py server/app.py tests/test_providers_api.py
git commit -m "feat(api): add /api/v1/providers router"
```

---

### Task 7: System Config Router 重构

**Files:**
- Modify: `server/routers/system_config.py`
- Test: `tests/test_system_config_api.py` (若存在则修改，否则新增)

- [ ] **Step 1: 重构 system_config router**

目标：
1. 移除所有 `os.environ.get()` 调用，改用 ConfigService
2. GET 响应改为 spec 中定义的 `settings` + `options` 结构
3. `options.video_backends` / `image_backends` 只列出 status=ready 的供应商模型
4. PATCH 写入 system_setting 表
5. 移除供应商相关的配置逻辑（已迁移到 providers router）
6. 移除 `SystemConfigManager` 依赖

关键改动点（参考探索报告）：
- 行 257-308 的 `_config_payload()` — 全部改走 ConfigService
- 行 362-468 的 GET/PATCH — 改用 ConfigService 读写
- 行 471-541 的 vertex-credentials / connection-test — 移到 providers router，此处删除

- [ ] **Step 2: 更新/编写测试**

验证新的 GET/PATCH 行为，确保 `options` 中只返回已就绪供应商的模型。

- [ ] **Step 3: 运行全部测试**

Run: `uv run python -m pytest tests/ -v -k "system_config"`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add server/routers/system_config.py tests/
git commit -m "refactor(api): slim system_config router to use ConfigService"
```

---

### Task 8: Usage Stats API 扩展

**Files:**
- Modify: `server/routers/usage.py`
- Modify: `lib/db/repositories/usage_repo.py` (若需扩展查询)
- Test: 在现有 usage 测试中增加筛选用例

- [ ] **Step 1: 扩展 usage stats 查询**

在 `GET /api/v1/usage/stats` 添加查询参数：
- `provider` — 按供应商筛选
- `start` / `end` — 时间范围
- `group_by` — 分组方式 (provider, call_type)

修改 `UsageRepository.get_stats()` 支持新的筛选参数。

- [ ] **Step 2: 写测试并验证**

Run: `uv run python -m pytest tests/ -v -k "usage"`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add server/routers/usage.py lib/db/repositories/usage_repo.py tests/
git commit -m "feat(api): extend usage stats with provider filter and grouping"
```

---

### Task 9: 调用方迁移 — 后端 os.environ 清理

**Files:**
- Modify: `server/services/generation_tasks.py`
- Modify: `lib/media_generator.py`
- Modify: `lib/gemini_client.py`
- Modify: `server/routers/assistant.py`
- Modify: `server/auth.py`
- Modify: `server/agent_runtime/session_manager.py`

- [ ] **Step 1: 迁移 generation_tasks.py**

核心修改：
- `_get_or_create_video_backend()` — 从 ConfigService 读取供应商配置而非 `os.environ.get()`
- `get_media_generator()` — 接收 ConfigService，通过它获取 provider config
- 需要接收 `AsyncSession` 或 `ConfigService` 作为依赖

```python
# 修改前
backend_type = os.environ.get("GEMINI_VIDEO_BACKEND", "aistudio")
api_key = os.environ.get("GEMINI_API_KEY")

# 修改后
config = await config_service.get_provider_config(provider_id)
api_key = config.get("api_key")
```

- [ ] **Step 2: 迁移 gemini_client.py**

修改 `_rate_limiter_limits_from_env()` 和 `get_shared_rate_limiter()` 接收配置参数而非读 env。
修改 `RateLimiter` 中 `GEMINI_REQUEST_GAP` 的读取方式。

- [ ] **Step 3: 迁移其他模块**

- `server/routers/assistant.py` — `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`
- `server/auth.py` — 认证相关配置
- `server/agent_runtime/session_manager.py` — Agent SDK 配置

每个模块的模式一致：`os.environ.get("KEY")` → `await config_service.get_setting("key")` 或 `config_service.get_provider_config()`

- [ ] **Step 4: 运行全部测试确保无回归**

Run: `uv run python -m pytest -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/services/generation_tasks.py lib/media_generator.py lib/gemini_client.py server/routers/assistant.py server/auth.py server/agent_runtime/
git commit -m "refactor: migrate all os.environ config reads to ConfigService"
```

---

### Task 10: GenerationWorker 按供应商分池

**Files:**
- Modify: `lib/generation_worker.py`
- Test: `tests/test_generation_worker.py` (扩展或新增)

- [ ] **Step 1: 设计新的 Worker 结构**

当前：
```python
self.image_workers = 3  # 全局
self.video_workers = 2  # 全局
self._image_inflight: dict[str, asyncio.Task] = {}
self._video_inflight: dict[str, asyncio.Task] = {}
```

改为：
```python
# 每个 provider 独立的 inflight 池
self._provider_pools: dict[str, ProviderPool] = {}

@dataclass
class ProviderPool:
    provider_id: str
    image_max: int
    video_max: int
    image_inflight: dict[str, asyncio.Task]
    video_inflight: dict[str, asyncio.Task]
```

- [ ] **Step 2: 实现 ProviderPool 和新的 _run_loop**

主循环改为：
1. 从 ConfigService 获取所有 ready 供应商的并发配置
2. 为每个供应商维护独立的 pool
3. claim_next 时需要按 provider 筛选（或在任务入队时记录 provider_id）
4. 每个 pool 独立检查是否有空闲 worker 位

- [ ] **Step 3: 写测试验证按供应商分池**

验证：
- Gemini pool 满时不影响 Seedance pool 继续接受任务
- 单个供应商的 max_workers 限制正确生效
- reload 配置后 pool 动态调整

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/test_generation_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/generation_worker.py tests/test_generation_worker.py
git commit -m "refactor(worker): per-provider pool scheduling"
```

---

### Task 11: 废弃 SystemConfigManager

**Files:**
- Modify: `lib/system_config.py` — 标记废弃或清空
- Modify: `server/app.py` — 启动时调用 JSON 迁移替代 `init_and_apply_system_config()`
- Modify: 所有导入 `SystemConfigManager` 的地方

- [ ] **Step 1: 替换 app 启动流程**

在 `server/app.py` 的 lifespan 中：
```python
# 旧: init_and_apply_system_config()
# 新: 检查 JSON 文件 → 迁移 → ConfigService 就绪
from lib.config.migration import migrate_json_to_db

async with get_async_session() as session:
    json_path = Path("projects/.system_config.json")
    await migrate_json_to_db(session, json_path)
```

- [ ] **Step 2: 移除所有 SystemConfigManager 引用**

搜索 `SystemConfigManager`、`get_system_config_manager`、`init_and_apply_system_config` 的使用点，全部替换。

- [ ] **Step 3: 运行全部测试**

Run: `uv run python -m pytest -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add lib/system_config.py server/app.py
git commit -m "refactor: deprecate SystemConfigManager, use ConfigService"
```

---

### Task 12: 前端类型 + API 层

**Files:**
- Create: `frontend/src/types/provider.ts`
- Modify: `frontend/src/types/system.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: 定义前端类型**

```typescript
// frontend/src/types/provider.ts
export interface ProviderInfo {
  id: string;
  display_name: string;
  status: "ready" | "unconfigured" | "error";
  media_types: string[];
  capabilities: string[];
  configured_keys: string[];
  missing_keys: string[];
}

export interface ProviderField {
  key: string;
  label: string;
  type: "secret" | "text" | "url" | "number" | "file";
  required: boolean;
  is_set: boolean;
  value?: string;
  value_masked?: string;
  placeholder?: string;
}

export interface ProviderConfigDetail {
  id: string;
  display_name: string;
  status: string;
  fields: ProviderField[];
}

export interface ProviderTestResult {
  success: boolean;
  available_models: string[];
  message: string;
}

export interface UsageStat {
  provider: string;
  call_type: string;
  total_calls: number;
  success_calls: number;
  total_cost_usd: number;
  total_duration_seconds?: number;
}
```

- [ ] **Step 2: 更新 system.ts 类型**

更新 `SystemConfigView` 以匹配新的 GET /system/config 响应格式（settings + options 结构）。

- [ ] **Step 3: 添加 API 函数**

在 `frontend/src/api.ts` 中添加：

```typescript
// Providers
async getProviders(): Promise<{ providers: ProviderInfo[] }>
async getProviderConfig(id: string): Promise<ProviderConfigDetail>
async patchProviderConfig(id: string, patch: Record<string, string | null>): Promise<void>
async testProviderConnection(id: string): Promise<ProviderTestResult>

// Usage
async getUsageStats(params: { provider?: string; start?: string; end?: string; group_by?: string }): Promise<{ stats: UsageStat[]; period: { start: string; end: string } }>
```

- [ ] **Step 4: 运行前端类型检查**

Run: `cd frontend && pnpm typecheck`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/ frontend/src/api.ts
git commit -m "feat(frontend): add provider types and API functions"
```

---

### Task 13: SystemConfigPage 侧边栏布局

**Files:**
- Modify: `frontend/src/components/pages/SystemConfigPage.tsx`

- [ ] **Step 1: 重构为侧边栏布局**

将现有的 Tab 导航替换为左侧固定侧边栏 + 右侧内容区。

侧边栏 4 项，使用 `lucide-react` 图标：
- `Bot` — 智能体
- `Plug` — 供应商
- `Film` — 图片/视频
- `BarChart3` — 用量统计

使用 query parameter `?section=agent|providers|media|usage` 控制活跃栏位。

右侧内容区根据 section 渲染对应组件。

- [ ] **Step 2: 运行前端构建检查**

Run: `cd frontend && pnpm build`
Expected: PASS (此时子组件可以是占位符)

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/pages/SystemConfigPage.tsx
git commit -m "refactor(frontend): SystemConfigPage tab to sidebar layout"
```

---

### Task 14: ProviderModelSelect — 分组下拉组件

**Files:**
- Create: `frontend/src/components/ui/ProviderModelSelect.tsx`
- Test: `frontend/src/components/ui/__tests__/ProviderModelSelect.test.tsx`

- [ ] **Step 1: 写组件测试**

```typescript
// 测试：按 provider 分组、选中状态、仅展示传入的 options
import { render, screen } from "@testing-library/react";
import { ProviderModelSelect } from "../ProviderModelSelect";

test("renders grouped options", () => {
  render(
    <ProviderModelSelect
      value="gemini-aistudio/veo-3.1-generate-001"
      options={[
        "gemini-aistudio/veo-3.1-generate-001",
        "gemini-aistudio/veo-3.1-fast-generate-001",
        "seedance/doubao-seedance-1-5-pro-251215",
      ]}
      providerNames={{ "gemini-aistudio": "Gemini AI Studio", seedance: "Seedance" }}
      onChange={() => {}}
    />
  );
  // 验证当前选中值显示
  expect(screen.getByText(/veo-3.1-generate-001/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 实现组件**

按 `/` 拆分 options，provider 作为分组标题（使用 `<optgroup>` 或自定义下拉），model 作为选项。

- [ ] **Step 3: 运行测试**

Run: `cd frontend && pnpm test -- ProviderModelSelect`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/ui/ProviderModelSelect.tsx frontend/src/components/ui/__tests__/
git commit -m "feat(frontend): add ProviderModelSelect grouped dropdown"
```

---

### Task 15: ProviderSection — 供应商配置栏位

**Files:**
- Create: `frontend/src/components/pages/settings/ProviderSection.tsx`
- Create: `frontend/src/components/pages/settings/ProviderDetail.tsx`

- [ ] **Step 1: 实现 ProviderSection**

列表 + 详情布局：
- 左侧：供应商列表，调用 `API.getProviders()`，每个供应商显示名称 + 状态指示器
- 右侧：选中供应商的详情，调用 `API.getProviderConfig(id)`
- 供应商 logo 使用 `@lobehub/icons`

- [ ] **Step 2: 实现 ProviderDetail**

配置表单：
- 遍历 `fields`，根据 type 渲染不同输入
- secret 字段掩码显示 + 清除按钮
- 高级配置区（折叠）：并发数、限流参数
- 底部：连接测试按钮 + 保存按钮
- PATCH 保存时只发送变更字段

- [ ] **Step 3: 运行前端构建**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/pages/settings/
git commit -m "feat(frontend): add ProviderSection with list+detail layout"
```

---

### Task 16: MediaModelSection — 图片/视频模型栏位

**Files:**
- Create: `frontend/src/components/pages/settings/MediaModelSection.tsx`

- [ ] **Step 1: 实现 MediaModelSection**

- 调用 `GET /api/v1/system/config` 获取 settings + options
- 使用 `ProviderModelSelect` 组件展示默认视频模型和默认图片模型
- `video_generate_audio` 开关
- 保存按钮调用 `PATCH /api/v1/system/config`

- [ ] **Step 2: 运行前端构建**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/pages/settings/MediaModelSection.tsx
git commit -m "feat(frontend): add MediaModelSection with grouped selectors"
```

---

### Task 17: AgentSection 适配

**Files:**
- Modify: `frontend/src/components/pages/AgentConfigTab.tsx` (或提取为 `settings/AgentSection.tsx`)

- [ ] **Step 1: 适配新 API 响应结构**

AgentConfigTab 当前从 `GetSystemConfigResponse.config` 读取 anthropic 配置。
改为从新的 `settings` 结构读取。组件逻辑基本不变，只是数据源路径变化。

- [ ] **Step 2: 运行前端构建**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/pages/
git commit -m "refactor(frontend): adapt AgentSection to new API structure"
```

---

### Task 18: UsageStatsSection

**Files:**
- Create: `frontend/src/components/pages/settings/UsageStatsSection.tsx`

- [ ] **Step 1: 实现用量统计栏位**

- 调用 `API.getUsageStats()` 获取数据
- 筛选器：时间范围选择器、供应商下拉、调用类型下拉
- 按供应商分组展示：调用次数、成功率、费用、时长
- 使用表格或卡片布局

- [ ] **Step 2: 运行前端构建**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/pages/settings/UsageStatsSection.tsx
git commit -m "feat(frontend): add UsageStatsSection with filters"
```

---

### Task 19: config-status-store 重构

**Files:**
- Modify: `frontend/src/stores/config-status-store.ts`

- [ ] **Step 1: 改用 providers API**

当前 `getConfigIssues()` 硬编码检查 Gemini 凭证。
改为调用 `API.getProviders()` 获取所有供应商状态，对 status != "ready" 但被选为默认后端的供应商生成 issue。

- [ ] **Step 2: 运行前端类型检查和测试**

Run: `cd frontend && pnpm check`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add frontend/src/stores/config-status-store.ts
git commit -m "refactor(frontend): config-status-store uses providers API"
```

---

### Task 20: 项目设置页 — 路由修复 + 模型覆盖

**Files:**
- Modify: `frontend/src/router.tsx`
- Create: `frontend/src/components/pages/ProjectSettingsPage.tsx`
- Modify: 项目工作台中的设置按钮入口

- [ ] **Step 1: 创建 ProjectSettingsPage**

全屏 overlay 页面：
- 左上角返回按钮（`ArrowLeft` from lucide），点击回到 `/projects/:name`
- 标题：项目名称 + "设置"
- 内容：
  - 视频模型：`ProviderModelSelect` + 顶部「跟随全局默认」选项
  - 图片模型：同上
  - 生成音频：三态（跟随全局 / 开启 / 关闭）
- 保存：写入 `project.json` 的 `video_backend` / `image_backend` 字段

- [ ] **Step 2: 修复路由**

在 `router.tsx` 中添加或修复：
```typescript
<Route path="/projects/:name/settings">
  <ProjectSettingsPage />
</Route>
```

确保该路由渲染全屏内容而非嵌套在项目工作台内。

- [ ] **Step 3: 运行前端构建**

Run: `cd frontend && pnpm build`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/pages/ProjectSettingsPage.tsx frontend/src/router.tsx
git commit -m "feat(frontend): project settings page with model override"
```

---

### Task 21: 清理废弃文件

**Files:**
- Delete: `frontend/src/components/pages/MediaConfigTab.tsx`
- Delete: `frontend/src/components/pages/AdvancedConfigTab.tsx`
- Delete: `frontend/src/components/pages/ApiKeysTab.tsx`
- Modify: `frontend/src/components/pages/SystemConfigPage.tsx` (移除旧 Tab 导入)

- [ ] **Step 1: 移除废弃组件**

删除不再使用的 Tab 组件文件，移除 SystemConfigPage 中对它们的导入。

- [ ] **Step 2: 运行全部前端检查**

Run: `cd frontend && pnpm check`
Expected: PASS

- [ ] **Step 3: 运行全部后端测试**

Run: `uv run python -m pytest -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "chore: remove deprecated MediaConfigTab, AdvancedConfigTab, ApiKeysTab"
```

---

### Task 22: 端到端验证

- [ ] **Step 1: 启动后端**

Run: `uv run uvicorn server.app:app --reload --port 1241`
验证启动时 JSON → DB 迁移正常执行（如有 JSON 文件）。

- [ ] **Step 2: 启动前端**

Run: `cd frontend && pnpm dev`
验证全局设置页侧边栏布局正常。

- [ ] **Step 3: 手动验证供应商配置流程**

1. 打开设置页 → 供应商栏位
2. 选择 Gemini AI Studio → 输入 API Key → 保存
3. 连接测试 → 确认成功
4. 切到图片/视频栏位 → 下拉中出现 Gemini AI Studio 的模型
5. 选择默认视频模型 → 保存

- [ ] **Step 4: 手动验证项目级覆盖**

1. 进入项目 → 设置
2. 选择不同于全局默认的视频模型
3. 保存 → 确认 project.json 已更新
4. 返回项目工作台

- [ ] **Step 5: 运行全部测试套件**

Run: `uv run python -m pytest -v && cd frontend && pnpm check`
Expected: all PASS
