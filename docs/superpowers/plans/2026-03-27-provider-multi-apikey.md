# Provider Multi-API Key Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support configuring multiple API Keys / Vertex credentials per provider, manually switching the active key, and running connection tests against any key.

**Architecture:** 新建 `provider_credential` 表存储凭证（名称、密钥、base_url、活跃状态），与现有 `provider_config` 表（RPM/workers 共享配置）分离。`ConfigResolver.provider_config()` 在返回时合并活跃凭证信息，使下游消费方无感知。前端 `ProviderDetail` 拆分为凭证管理区 + 共享配置区。

**Tech Stack:** Python 3.12, SQLAlchemy async ORM, Alembic, FastAPI, React 19, TypeScript, Tailwind CSS 4

**Spec:** `docs/superpowers/specs/2026-03-27-provider-multi-apikey-design.md`

---

## File Structure

### New Files
- `lib/db/models/credential.py` — ProviderCredential ORM 模型
- `lib/db/repositories/credential_repository.py` — 凭证 CRUD Repository
- `lib/config/url_utils.py` — `normalize_base_url()` 工具函数
- `tests/test_credential_repository.py` — Repository 层测试
- `tests/test_credential_api.py` — 凭证 API 端点测试
- `tests/test_normalize_base_url.py` — URL 归一化测试
- `alembic/versions/xxxx_add_provider_credential_table.py` — 数据库迁移
- `frontend/src/components/pages/CredentialList.tsx` — 凭证列表管理组件

### Modified Files
- `lib/db/models/__init__.py` — 导出 ProviderCredential
- `lib/config/repository.py` — 新增 CredentialRepository（或在新文件）
- `lib/config/service.py` — 状态判定逻辑改用凭证表
- `lib/config/resolver.py` — provider_config() 合并活跃凭证
- `server/routers/providers.py` — 新增凭证 CRUD 端点，改造连接测试和 Vertex 上传
- `server/dependencies.py` — 可选：新增凭证相关依赖
- `lib/image_backends/gemini.py` — base_url 防御性归一化
- `lib/video_backends/gemini.py` — base_url 防御性归一化
- `lib/gemini_client.py` — base_url 防御性归一化
- `frontend/src/types/provider.ts` — 新增 ProviderCredential 类型
- `frontend/src/api.ts` — 新增凭证 API 方法
- `frontend/src/components/pages/ProviderDetail.tsx` — 重构为凭证管理区 + 共享配置区
- `tests/test_providers_api.py` — 更新已有测试适配 fields 变更

---

## Task 1: base_url 归一化工具函数 + 防御性修复

**Files:**
- Create: `lib/config/url_utils.py`
- Create: `tests/test_normalize_base_url.py`
- Modify: `lib/image_backends/gemini.py:88-89`
- Modify: `lib/video_backends/gemini.py:86-87`
- Modify: `lib/gemini_client.py:497-498`
- Modify: `server/routers/providers.py:285-286`

- [ ] **Step 1: 编写 normalize_base_url 测试**

创建 `tests/test_normalize_base_url.py`：

```python
"""base_url 归一化工具函数测试。"""

from lib.config.url_utils import normalize_base_url


class TestNormalizeBaseUrl:
    def test_none_returns_none(self):
        assert normalize_base_url(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_base_url("") is None

    def test_whitespace_only_returns_none(self):
        assert normalize_base_url("   ") is None

    def test_adds_trailing_slash(self):
        assert normalize_base_url("https://proxy.example.com/v1") == "https://proxy.example.com/v1/"

    def test_preserves_existing_trailing_slash(self):
        assert normalize_base_url("https://proxy.example.com/v1/") == "https://proxy.example.com/v1/"

    def test_strips_whitespace(self):
        assert normalize_base_url("  https://proxy.example.com/v1  ") == "https://proxy.example.com/v1/"

    def test_plain_domain(self):
        assert normalize_base_url("https://example.com") == "https://example.com/"
```

- [ ] **Step 2: 运行测试验证失败**

运行: `uv run python -m pytest tests/test_normalize_base_url.py -v`
预期: FAIL — `ModuleNotFoundError: No module named 'lib.config.url_utils'`

- [ ] **Step 3: 实现 normalize_base_url**

创建 `lib/config/url_utils.py`：

```python
"""URL 归一化工具函数。"""

from __future__ import annotations


def normalize_base_url(url: str | None) -> str | None:
    """确保 base_url 以 / 结尾。

    Google genai SDK 的 http_options.base_url 要求尾部带 /，
    否则请求路径拼接会失败。
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.endswith("/"):
        url += "/"
    return url
```

- [ ] **Step 4: 运行测试验证通过**

运行: `uv run python -m pytest tests/test_normalize_base_url.py -v`
预期: 全部 PASS

- [ ] **Step 5: 在 4 处消费点添加防御性归一化**

修改 `lib/image_backends/gemini.py`，将第 88 行：
```python
            effective_base_url = base_url or os.environ.get("GEMINI_BASE_URL", "").strip() or None
```
改为：
```python
            from lib.config.url_utils import normalize_base_url
            effective_base_url = normalize_base_url(base_url or os.environ.get("GEMINI_BASE_URL", "").strip())
```

修改 `lib/video_backends/gemini.py`，将第 86 行：
```python
            base_url = os.environ.get("GEMINI_BASE_URL", "").strip() or None
```
改为：
```python
            from lib.config.url_utils import normalize_base_url
            base_url = normalize_base_url(os.environ.get("GEMINI_BASE_URL", "").strip())
```

修改 `lib/gemini_client.py`，将第 497 行：
```python
            effective_base_url = base_url
```
改为：
```python
            from lib.config.url_utils import normalize_base_url
            effective_base_url = normalize_base_url(base_url)
```

修改 `server/routers/providers.py` 的 `_test_gemini_aistudio` 函数，将第 285 行：
```python
    base_url = config.get("base_url", "").strip() or None
```
改为：
```python
    from lib.config.url_utils import normalize_base_url
    base_url = normalize_base_url(config.get("base_url"))
```

- [ ] **Step 6: 运行全部测试confirm no regressions**

运行: `uv run python -m pytest tests/test_normalize_base_url.py tests/test_providers_api.py -v`
预期: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add lib/config/url_utils.py tests/test_normalize_base_url.py lib/image_backends/gemini.py lib/video_backends/gemini.py lib/gemini_client.py server/routers/providers.py
git commit -m "fix: base_url 尾部斜杠归一化，修复代理 URL 不带 / 导致请求失败"
```

---

## Task 2: ProviderCredential ORM 模型

**Files:**
- Create: `lib/db/models/credential.py`
- Modify: `lib/db/models/__init__.py`

- [ ] **Step 1: 创建 ProviderCredential 模型**

创建 `lib/db/models/credential.py`：

```python
"""Provider credential ORM model."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class ProviderCredential(TimestampMixin, Base):
    """供应商凭证。每个供应商可有多条凭证，其中最多一条 is_active=True。"""

    __tablename__ = "provider_credential"
    __table_args__ = (
        Index("ix_provider_credential_provider", "provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    credentials_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

- [ ] **Step 2: 在 models/__init__.py 中导出**

修改 `lib/db/models/__init__.py`，添加导入和导出：

在现有导入后添加：
```python
from lib.db.models.credential import ProviderCredential
```

在 `__all__` 列表中添加 `"ProviderCredential"`。

- [ ] **Step 3: 验证模型可导入**

运行: `uv run python -c "from lib.db.models import ProviderCredential; print(ProviderCredential.__tablename__)"`
预期: 输出 `provider_credential`

- [ ] **Step 4: 提交**

```bash
git add lib/db/models/credential.py lib/db/models/__init__.py
git commit -m "feat: 新增 ProviderCredential ORM 模型"
```

---

## Task 3: Alembic 迁移（建表 + 数据迁移）

**Files:**
- Create: `alembic/versions/xxxx_add_provider_credential_table.py`（由 autogenerate 生成后手动编辑）

- [ ] **Step 1: 生成迁移脚本**

运行: `uv run alembic revision --autogenerate -m "add provider credential table"`

预期: 在 `alembic/versions/` 下生成新迁移文件，包含 `create_table('provider_credential', ...)` 操作。

- [ ] **Step 2: 编辑迁移脚本，添加数据迁移逻辑**

在 autogenerate 生成的 `upgrade()` 函数末尾，在 `create_table` 之后添加数据迁移：

```python
    # 数据迁移：将 provider_config 中的凭证行迁入 provider_credential
    conn = op.get_bind()

    # 读出所有现有的凭证相关配置
    rows = conn.execute(
        sa.text(
            "SELECT provider, key, value FROM provider_config "
            "WHERE key IN ('api_key', 'credentials_path', 'base_url')"
        )
    ).fetchall()

    # 按 provider 分组
    from collections import defaultdict
    provider_data: dict[str, dict[str, str]] = defaultdict(dict)
    for provider, key, value in rows:
        provider_data[provider][key] = value

    # 为每个 provider 创建凭证记录
    now = sa.func.now()
    cred_table = sa.table(
        "provider_credential",
        sa.column("provider", sa.String),
        sa.column("name", sa.String),
        sa.column("api_key", sa.Text),
        sa.column("credentials_path", sa.Text),
        sa.column("base_url", sa.Text),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    for provider, data in provider_data.items():
        if not data.get("api_key") and not data.get("credentials_path"):
            continue  # 没有密钥的 provider 不迁移
        conn.execute(
            cred_table.insert().values(
                provider=provider,
                name="默认密钥",
                api_key=data.get("api_key"),
                credentials_path=data.get("credentials_path"),
                base_url=data.get("base_url"),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )

    # 从 provider_config 中删除已迁移的行
    conn.execute(
        sa.text(
            "DELETE FROM provider_config WHERE key IN ('api_key', 'credentials_path', 'base_url')"
        )
    )
```

在 `downgrade()` 函数中，在 `drop_table` 之前添加反向迁移：

```python
    # 反向迁移：将 provider_credential 中的活跃凭证写回 provider_config
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT provider, api_key, credentials_path, base_url "
            "FROM provider_credential WHERE is_active = 1"
        )
    ).fetchall()

    config_table = sa.table(
        "provider_config",
        sa.column("provider", sa.String),
        sa.column("key", sa.String),
        sa.column("value", sa.Text),
        sa.column("is_secret", sa.Boolean),
        sa.column("updated_at", sa.DateTime),
    )
    now = sa.func.now()
    for provider, api_key, cred_path, base_url in rows:
        if api_key:
            conn.execute(config_table.insert().values(
                provider=provider, key="api_key", value=api_key, is_secret=True, updated_at=now,
            ))
        if cred_path:
            conn.execute(config_table.insert().values(
                provider=provider, key="credentials_path", value=cred_path, is_secret=False, updated_at=now,
            ))
        if base_url:
            conn.execute(config_table.insert().values(
                provider=provider, key="base_url", value=base_url, is_secret=False, updated_at=now,
            ))
```

- [ ] **Step 3: 运行迁移**

运行: `uv run alembic upgrade head`
预期: 成功，无报错

- [ ] **Step 4: 验证表结构**

运行: `uv run python -c "import sqlite3; conn = sqlite3.connect('projects/.arcreel.db'); print([r[1] for r in conn.execute('PRAGMA table_info(provider_credential)').fetchall()])"`
预期: 输出列名列表，包含 `provider`, `name`, `api_key`, `credentials_path`, `base_url`, `is_active`, `created_at`, `updated_at`

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "feat: 添加 provider_credential 表迁移，含数据迁移逻辑"
```

---

## Task 4: CredentialRepository（数据访问层）

**Files:**
- Create: `lib/db/repositories/credential_repository.py`
- Create: `tests/test_credential_repository.py`

- [ ] **Step 1: 编写 Repository 测试**

创建 `tests/test_credential_repository.py`：

```python
"""ProviderCredential Repository 测试。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.models.credential import ProviderCredential
from lib.db.repositories.credential_repository import CredentialRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()


class TestCredentialRepository:
    async def test_create_and_list(self, session: AsyncSession):
        repo = CredentialRepository(session)
        cred = await repo.create(provider="gemini-aistudio", name="测试Key", api_key="AIza-test")
        await session.flush()
        creds = await repo.list_by_provider("gemini-aistudio")
        assert len(creds) == 1
        assert creds[0].name == "测试Key"
        assert creds[0].api_key == "AIza-test"
        assert creds[0].id == cred.id

    async def test_first_credential_is_active(self, session: AsyncSession):
        repo = CredentialRepository(session)
        cred = await repo.create(provider="gemini-aistudio", name="第一个", api_key="AIza-1")
        await session.flush()
        assert cred.is_active is True

    async def test_second_credential_is_not_active(self, session: AsyncSession):
        repo = CredentialRepository(session)
        await repo.create(provider="gemini-aistudio", name="第一个", api_key="AIza-1")
        cred2 = await repo.create(provider="gemini-aistudio", name="第二个", api_key="AIza-2")
        await session.flush()
        assert cred2.is_active is False

    async def test_activate(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c1 = await repo.create(provider="gemini-aistudio", name="第一个", api_key="AIza-1")
        c2 = await repo.create(provider="gemini-aistudio", name="第二个", api_key="AIza-2")
        await session.flush()

        await repo.activate(c2.id, "gemini-aistudio")
        await session.flush()

        creds = await repo.list_by_provider("gemini-aistudio")
        active_map = {c.id: c.is_active for c in creds}
        assert active_map[c1.id] is False
        assert active_map[c2.id] is True

    async def test_get_active(self, session: AsyncSession):
        repo = CredentialRepository(session)
        await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await session.flush()
        active = await repo.get_active("gemini-aistudio")
        assert active is not None
        assert active.name == "Key1"

    async def test_get_active_returns_none_when_empty(self, session: AsyncSession):
        repo = CredentialRepository(session)
        active = await repo.get_active("gemini-aistudio")
        assert active is None

    async def test_get_by_id(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c = await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await session.flush()
        found = await repo.get_by_id(c.id)
        assert found is not None
        assert found.name == "Key1"

    async def test_update(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c = await repo.create(provider="gemini-aistudio", name="旧名", api_key="AIza-old")
        await session.flush()
        await repo.update(c.id, name="新名", api_key="AIza-new")
        await session.flush()
        updated = await repo.get_by_id(c.id)
        assert updated is not None
        assert updated.name == "新名"
        assert updated.api_key == "AIza-new"

    async def test_delete(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c = await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await session.flush()
        await repo.delete(c.id)
        await session.flush()
        assert await repo.get_by_id(c.id) is None

    async def test_delete_active_promotes_oldest(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c1 = await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        c2 = await repo.create(provider="gemini-aistudio", name="Key2", api_key="AIza-2")
        await session.flush()
        # c1 is active, delete it
        await repo.delete(c1.id)
        await session.flush()
        remaining = await repo.list_by_provider("gemini-aistudio")
        assert len(remaining) == 1
        assert remaining[0].is_active is True

    async def test_has_active_credential(self, session: AsyncSession):
        repo = CredentialRepository(session)
        assert await repo.has_active_credential("gemini-aistudio") is False
        await repo.create(provider="gemini-aistudio", name="Key1", api_key="AIza-1")
        await session.flush()
        assert await repo.has_active_credential("gemini-aistudio") is True

    async def test_get_active_credentials_bulk(self, session: AsyncSession):
        repo = CredentialRepository(session)
        await repo.create(provider="gemini-aistudio", name="K1", api_key="AIza-1")
        await repo.create(provider="ark", name="K2", api_key="ark-key")
        await session.flush()
        bulk = await repo.get_active_credentials_bulk()
        assert "gemini-aistudio" in bulk
        assert "ark" in bulk

    async def test_base_url_normalized_on_create(self, session: AsyncSession):
        repo = CredentialRepository(session)
        c = await repo.create(
            provider="gemini-aistudio", name="Key", api_key="AIza-1",
            base_url="https://proxy.example.com/v1",
        )
        await session.flush()
        assert c.base_url == "https://proxy.example.com/v1/"
```

- [ ] **Step 2: 运行测试验证失败**

运行: `uv run python -m pytest tests/test_credential_repository.py -v`
预期: FAIL — `ModuleNotFoundError: No module named 'lib.db.repositories.credential_repository'`

- [ ] **Step 3: 实现 CredentialRepository**

创建 `lib/db/repositories/credential_repository.py`：

```python
"""Provider credential repository."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.url_utils import normalize_base_url
from lib.db.models.credential import ProviderCredential


class CredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        provider: str,
        name: str,
        api_key: str | None = None,
        credentials_path: str | None = None,
        base_url: str | None = None,
    ) -> ProviderCredential:
        """创建凭证。若为该供应商的第一条，自动设为活跃。"""
        is_first = not await self.has_active_credential(provider)
        cred = ProviderCredential(
            provider=provider,
            name=name,
            api_key=api_key,
            credentials_path=credentials_path,
            base_url=normalize_base_url(base_url),
            is_active=is_first,
        )
        self.session.add(cred)
        await self.session.flush()
        return cred

    async def get_by_id(self, cred_id: int) -> ProviderCredential | None:
        stmt = select(ProviderCredential).where(ProviderCredential.id == cred_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_provider(self, provider: str) -> list[ProviderCredential]:
        stmt = (
            select(ProviderCredential)
            .where(ProviderCredential.provider == provider)
            .order_by(ProviderCredential.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_active(self, provider: str) -> ProviderCredential | None:
        stmt = select(ProviderCredential).where(
            ProviderCredential.provider == provider,
            ProviderCredential.is_active == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def has_active_credential(self, provider: str) -> bool:
        return await self.get_active(provider) is not None

    async def get_active_credentials_bulk(self) -> dict[str, ProviderCredential]:
        """批量获取所有供应商的活跃凭证。"""
        stmt = select(ProviderCredential).where(
            ProviderCredential.is_active == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return {c.provider: c for c in result.scalars()}

    async def activate(self, cred_id: int, provider: str) -> None:
        """激活指定凭证，同时取消同供应商的其他活跃标记。"""
        # 先全部取消
        await self.session.execute(
            update(ProviderCredential)
            .where(ProviderCredential.provider == provider)
            .values(is_active=False)
        )
        # 再激活目标
        await self.session.execute(
            update(ProviderCredential)
            .where(ProviderCredential.id == cred_id)
            .values(is_active=True)
        )

    async def update(
        self,
        cred_id: int,
        *,
        name: str | None = None,
        api_key: str | None = None,
        credentials_path: str | None = None,
        base_url: str | None = ...,  # type: ignore[assignment]
    ) -> None:
        """更新凭证字段。仅更新非 None 参数（base_url 用 ... 表示未传入）。"""
        cred = await self.get_by_id(cred_id)
        if cred is None:
            return
        if name is not None:
            cred.name = name
        if api_key is not None:
            cred.api_key = api_key
        if credentials_path is not None:
            cred.credentials_path = credentials_path
        if base_url is not ...:
            cred.base_url = normalize_base_url(base_url)  # type: ignore[arg-type]

    async def delete(self, cred_id: int) -> None:
        """删除凭证。若删除的是活跃凭证，自动将最早的另一条设为活跃。"""
        cred = await self.get_by_id(cred_id)
        if cred is None:
            return
        provider = cred.provider
        was_active = cred.is_active
        await self.session.delete(cred)
        await self.session.flush()

        if was_active:
            # 选 created_at 最早的一条设为活跃
            stmt = (
                select(ProviderCredential)
                .where(ProviderCredential.provider == provider)
                .order_by(ProviderCredential.created_at)
                .limit(1)
            )
            result = await self.session.execute(stmt)
            next_cred = result.scalar_one_or_none()
            if next_cred:
                next_cred.is_active = True
```

- [ ] **Step 4: 运行测试验证通过**

运行: `uv run python -m pytest tests/test_credential_repository.py -v`
预期: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add lib/db/repositories/credential_repository.py tests/test_credential_repository.py
git commit -m "feat: 新增 CredentialRepository，支持凭证 CRUD + 活跃切换"
```

---

## Task 5: ConfigService 和 ConfigResolver 集成

**Files:**
- Modify: `lib/config/service.py`
- Modify: `lib/config/resolver.py`
- Modify: `lib/config/repository.py` — 添加 `mask_secret` 导出（已有）

- [ ] **Step 1: 修改 ConfigService.get_all_providers_status()**

修改 `lib/config/service.py` 的 `get_all_providers_status` 方法，状态判定改为基于 `provider_credential` 表。

At the top of the file imports 中添加：
```python
from lib.db.repositories.credential_repository import CredentialRepository
```

将 `get_all_providers_status` 方法改为：

```python
    async def get_all_providers_status(self) -> list[ProviderStatus]:
        all_configured = await self._provider_repo.get_all_configured_keys_bulk()
        cred_repo = CredentialRepository(self._provider_repo.session)
        active_creds = await cred_repo.get_active_credentials_bulk()
        statuses = []
        for name, meta in PROVIDER_REGISTRY.items():
            has_active = name in active_creds
            # 共享配置中的 key 列表（RPM/workers 等，不含凭证字段）
            configured = all_configured.get(name, [])
            # 对于 ready 状态判定：必须有活跃凭证
            if has_active:
                status: Literal["ready", "unconfigured", "error"] = "ready"
                missing: list[str] = []
            else:
                status = "unconfigured"
                missing = list(meta.required_keys)
            statuses.append(
                ProviderStatus(
                    name=name,
                    display_name=meta.display_name,
                    description=meta.description,
                    status=status,
                    media_types=list(meta.media_types),
                    capabilities=list(meta.capabilities),
                    required_keys=list(meta.required_keys),
                    configured_keys=configured,
                    missing_keys=missing,
                )
            )
        return statuses
```

- [ ] **Step 2: 修改 ConfigResolver.provider_config() 合并活跃凭证**

修改 `lib/config/resolver.py`，在 imports 中添加：
```python
from sqlalchemy.ext.asyncio import AsyncSession
from lib.db.repositories.credential_repository import CredentialRepository
```
（Note: `AsyncSession` 已在 `TYPE_CHECKING` 块中导入 `async_sessionmaker`，现在需要在运行时也导入 `AsyncSession`。）

修改调用方，将 session 传入 `_resolve_*` 方法：

```python
    async def provider_config(self, provider_id: str) -> dict[str, str]:
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_provider_config(svc, session, provider_id)

    async def all_provider_configs(self) -> dict[str, dict[str, str]]:
        async with self._session_factory() as session:
            svc = ConfigService(session)
            return await self._resolve_all_provider_configs(svc, session)
```

将 `_resolve_provider_config` 方法改为：

```python
    async def _resolve_provider_config(
        self, svc: ConfigService, session: AsyncSession, provider_id: str,
    ) -> dict[str, str]:
        # 1. 读共享配置（RPM / workers 等）
        config = await svc.get_provider_config(provider_id)
        # 2. 读活跃凭证，合并 api_key / base_url / credentials_path
        cred_repo = CredentialRepository(session)
        active = await cred_repo.get_active(provider_id)
        if active:
            if active.api_key:
                config["api_key"] = active.api_key
            if active.credentials_path:
                config["credentials_path"] = active.credentials_path
            if active.base_url:
                config["base_url"] = active.base_url
        return config
```

同样修改 `_resolve_all_provider_configs`：

```python
    async def _resolve_all_provider_configs(
        self, svc: ConfigService, session: AsyncSession,
    ) -> dict[str, dict[str, str]]:
        configs = await svc.get_all_provider_configs()
        cred_repo = CredentialRepository(session)
        active_creds = await cred_repo.get_active_credentials_bulk()
        for provider_id, cred in active_creds.items():
            cfg = configs.setdefault(provider_id, {})
            if cred.api_key:
                cfg["api_key"] = cred.api_key
            if cred.credentials_path:
                cfg["credentials_path"] = cred.credentials_path
            if cred.base_url:
                cfg["base_url"] = cred.base_url
        return configs
```

- [ ] **Step 3: 运行已有测试confirm no regressions**

运行: `uv run python -m pytest tests/ -v -k "provider or config" --timeout=30`
预期: 全部 PASS（可能有些测试需要调整 mock）

- [ ] **Step 4: 提交**

```bash
git add lib/config/service.py lib/config/resolver.py
git commit -m "feat: ConfigService 状态判定和 ConfigResolver 配置合并改用凭证表"
```

---

## Task 6: 凭证 CRUD API 端点

**Files:**
- Modify: `server/routers/providers.py`
- Create: `tests/test_credential_api.py`

- [ ] **Step 1: 编写凭证 API 测试**

创建 `tests/test_credential_api.py`：

```python
"""供应商凭证管理 API 测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.db import get_async_session
from lib.db.models.credential import ProviderCredential
from lib.db.repositories.credential_repository import CredentialRepository
from server.routers import providers


def _make_app() -> tuple[FastAPI, MagicMock]:
    """创建测试应用，返回 (app, mock_cred_repo)。"""
    app = FastAPI()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    async def _override():
        yield mock_session

    app.dependency_overrides[get_async_session] = _override
    app.include_router(providers.router, prefix="/api/v1")
    return app, mock_session


def _fake_cred(
    id: int = 1,
    provider: str = "gemini-aistudio",
    name: str = "测试Key",
    api_key: str = "AIzaSyFAKE12345678",
    is_active: bool = True,
    base_url: str | None = None,
    credentials_path: str | None = None,
) -> ProviderCredential:
    cred = ProviderCredential(
        provider=provider,
        name=name,
        api_key=api_key,
        is_active=is_active,
        base_url=base_url,
        credentials_path=credentials_path,
    )
    cred.id = id
    return cred


class TestListCredentials:
    def test_returns_200(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.list_by_provider = AsyncMock(return_value=[_fake_cred()])
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-aistudio/credentials")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["credentials"]) == 1
        assert body["credentials"][0]["name"] == "测试Key"
        # api_key 应脱敏
        assert body["credentials"][0]["api_key_masked"] is not None
        assert "FAKE" not in body["credentials"][0]["api_key_masked"]

    def test_returns_404_for_unknown_provider(self):
        app, _ = _make_app()
        with TestClient(app) as client:
            resp = client.get("/api/v1/providers/nonexistent/credentials")
        assert resp.status_code == 404


class TestCreateCredential:
    def test_returns_201(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.create = AsyncMock(return_value=_fake_cred())
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/providers/gemini-aistudio/credentials",
                    json={"name": "测试Key", "api_key": "AIza-new"},
                )
        assert resp.status_code == 201

    def test_requires_name(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/providers/gemini-aistudio/credentials",
                    json={"api_key": "AIza-new"},
                )
        assert resp.status_code == 422


class TestActivateCredential:
    def test_returns_204(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred(provider="gemini-aistudio"))
        mock_repo.activate = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/credentials/1/activate")
        assert resp.status_code == 204

    def test_returns_404_for_nonexistent(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=None)
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/credentials/999/activate")
        assert resp.status_code == 404


class TestDeleteCredential:
    def test_returns_204(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred())
        mock_repo.delete = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.delete("/api/v1/providers/gemini-aistudio/credentials/1")
        assert resp.status_code == 204
```

- [ ] **Step 2: 运行测试验证失败**

运行: `uv run python -m pytest tests/test_credential_api.py -v`
预期: FAIL — 端点不存在

- [ ] **Step 3: 在 providers.py 中实现凭证端点**

在 `server/routers/providers.py` 中添加以下内容。

在 imports 区域添加：
```python
from lib.db.repositories.credential_repository import CredentialRepository
from lib.config.repository import mask_secret
from lib.config.url_utils import normalize_base_url
```

添加 Pydantic 模型：
```python
class CredentialResponse(BaseModel):
    id: int
    provider: str
    name: str
    api_key_masked: Optional[str] = None
    credentials_filename: Optional[str] = None
    base_url: Optional[str] = None
    is_active: bool
    created_at: str


class CredentialListResponse(BaseModel):
    credentials: list[CredentialResponse]


class CreateCredentialRequest(BaseModel):
    name: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class UpdateCredentialRequest(BaseModel):
    name: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
```

添加辅助函数：
```python
def _cred_to_response(cred: "ProviderCredential") -> CredentialResponse:
    from lib.db.base import dt_to_iso
    return CredentialResponse(
        id=cred.id,
        provider=cred.provider,
        name=cred.name,
        api_key_masked=mask_secret(cred.api_key) if cred.api_key else None,
        credentials_filename=Path(cred.credentials_path).name if cred.credentials_path else None,
        base_url=cred.base_url,
        is_active=cred.is_active,
        created_at=dt_to_iso(cred.created_at) or "",
    )
```

添加端点：

```python
@router.get("/{provider_id}/credentials", response_model=CredentialListResponse)
async def list_credentials(
    provider_id: str,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialListResponse:
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")
    repo = CredentialRepository(session)
    creds = await repo.list_by_provider(provider_id)
    return CredentialListResponse(credentials=[_cred_to_response(c) for c in creds])


@router.post("/{provider_id}/credentials", status_code=201, response_model=CredentialResponse)
async def create_credential(
    provider_id: str,
    body: CreateCredentialRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> CredentialResponse:
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")
    repo = CredentialRepository(session)
    cred = await repo.create(
        provider=provider_id,
        name=body.name,
        api_key=body.api_key,
        base_url=body.base_url,
    )
    await session.commit()
    _invalidate_caches(request)
    return _cred_to_response(cred)


@router.patch("/{provider_id}/credentials/{cred_id}", status_code=204)
async def update_credential(
    provider_id: str,
    cred_id: int,
    body: UpdateCredentialRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")
    repo = CredentialRepository(session)
    cred = await repo.get_by_id(cred_id)
    if not cred or cred.provider != provider_id:
        raise HTTPException(status_code=404, detail="凭证不存在")
    kwargs: dict = {}
    if body.name is not None:
        kwargs["name"] = body.name
    if body.api_key is not None:
        kwargs["api_key"] = body.api_key
    if body.base_url is not None:
        kwargs["base_url"] = body.base_url
    if kwargs:
        await repo.update(cred_id, **kwargs)
        await session.commit()
        if cred.is_active:
            _invalidate_caches(request)
    return Response(status_code=204)


@router.delete("/{provider_id}/credentials/{cred_id}", status_code=204)
async def delete_credential(
    provider_id: str,
    cred_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")
    repo = CredentialRepository(session)
    cred = await repo.get_by_id(cred_id)
    if not cred or cred.provider != provider_id:
        raise HTTPException(status_code=404, detail="凭证不存在")
    await repo.delete(cred_id)
    await session.commit()
    _invalidate_caches(request)
    return Response(status_code=204)


@router.post("/{provider_id}/credentials/{cred_id}/activate", status_code=204)
async def activate_credential(
    provider_id: str,
    cred_id: int,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")
    repo = CredentialRepository(session)
    cred = await repo.get_by_id(cred_id)
    if not cred or cred.provider != provider_id:
        raise HTTPException(status_code=404, detail="凭证不存在")
    await repo.activate(cred_id, provider_id)
    await session.commit()
    _invalidate_caches(request)
    return Response(status_code=204)
```

提取公共缓存清理函数（替代 `patch_provider_config` 端点中的内联代码）：

```python
def _invalidate_caches(request: Request) -> None:
    """配置变更后清理后端缓存。"""
    from server.services.generation_tasks import invalidate_backend_cache
    invalidate_backend_cache()
    worker = getattr(request.app.state, "generation_worker", None)
    if worker:
        import asyncio
        asyncio.ensure_future(worker.reload_limits())
```

- [ ] **Step 4: 运行测试验证通过**

运行: `uv run python -m pytest tests/test_credential_api.py -v`
预期: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add server/routers/providers.py tests/test_credential_api.py
git commit -m "feat: 供应商凭证 CRUD API 端点"
```

---

## Task 7: 连接测试改造 + Vertex 上传改造 + ProviderConfig fields 变更

**Files:**
- Modify: `server/routers/providers.py`
- Modify: `tests/test_providers_api.py`

- [ ] **Step 1: 改造连接测试端点**

修改 `server/routers/providers.py` 中的 `test_provider_connection` 端点，添加 `credential_id` 可选参数：

```python
@router.post("/{provider_id}/test", response_model=ConnectionTestResponse)
async def test_provider_connection(
    provider_id: str,
    credential_id: Optional[int] = None,
    session: AsyncSession = Depends(get_async_session),
) -> ConnectionTestResponse:
    """调用供应商 API 验证连通性。可指定 credential_id 测试特定凭证。"""
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail=f"未知供应商: {provider_id}")

    repo = CredentialRepository(session)

    if credential_id is not None:
        cred = await repo.get_by_id(credential_id)
        if not cred or cred.provider != provider_id:
            raise HTTPException(status_code=404, detail="凭证不存在")
    else:
        cred = await repo.get_active(provider_id)

    if cred is None:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message="缺少凭证配置，请先添加密钥",
        )

    # 构建 config dict（合并凭证 + 共享配置）
    svc = ConfigService(session)
    config = await svc.get_provider_config(provider_id)
    if cred.api_key:
        config["api_key"] = cred.api_key
    if cred.credentials_path:
        config["credentials_path"] = cred.credentials_path
    if cred.base_url:
        config["base_url"] = cred.base_url

    test_fn = _TEST_DISPATCH.get(provider_id)
    if test_fn is None:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message=f"供应商 {provider_id} 暂不支持连接测试",
        )

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(test_fn, config),
            timeout=_CONNECTION_TEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message="连接超时，请检查网络或 API 配置",
        )
    except Exception as exc:
        err_msg = str(exc)
        if len(err_msg) > 200:
            err_msg = err_msg[:200] + "..."
        logger.warning("连接测试失败 [%s]: %s", provider_id, err_msg)
        return ConnectionTestResponse(
            success=False,
            available_models=[],
            message=f"连接失败: {err_msg}",
        )

    return result
```

- [ ] **Step 2: 改造 Vertex 凭证上传端点**

将原有的 `upload_vertex_credentials` 端点改为创建凭证记录：

```python
@router.post("/gemini-vertex/credentials/upload", status_code=201, response_model=CredentialResponse)
async def upload_vertex_credential(
    request: Request,
    name: str = "Vertex 凭证",
    session: AsyncSession = Depends(get_async_session),
    file: UploadFile = File(...),
) -> CredentialResponse:
    """上传 Vertex AI 服务账号 JSON 凭证文件，同时创建凭证记录。"""
    try:
        contents = await file.read(MAX_VERTEX_CREDENTIALS_BYTES + 1)
    except Exception:
        raise HTTPException(status_code=400, detail="读取上传文件失败")

    if len(contents) > MAX_VERTEX_CREDENTIALS_BYTES:
        raise HTTPException(status_code=413, detail="凭证文件过大")

    try:
        payload = json.loads(contents.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 JSON 凭证文件")

    if not isinstance(payload, dict) or not payload.get("project_id"):
        raise HTTPException(status_code=400, detail="凭证文件缺少 project_id")

    # 先创建凭证记录获取 id
    repo = CredentialRepository(session)
    cred = await repo.create(provider="gemini-vertex", name=name)
    await session.flush()

    # 保存文件：vertex_keys/vertex_cred_{id}.json
    dest = PROJECT_ROOT / "vertex_keys" / f"vertex_cred_{cred.id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(".tmp")
    tmp_path.write_bytes(contents)
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        logger.warning("无法设置临时凭证文件权限: %s", tmp_path, exc_info=True)
    os.replace(tmp_path, dest)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        logger.warning("无法设置凭证文件权限: %s", dest, exc_info=True)

    # 更新凭证记录的 credentials_path
    await repo.update(cred.id, credentials_path=str(dest))
    await session.commit()
    _invalidate_caches(request)

    # 重新读取以获取更新后的字段
    cred = await repo.get_by_id(cred.id)
    return _cred_to_response(cred)  # type: ignore[arg-type]
```

删除旧的 `upload_vertex_credentials` 端点。

- [ ] **Step 3: 修改 get_provider_config 端点，移除凭证字段**

修改 `get_provider_config` 端点，将 `api_key`、`credentials_path`、`base_url` 从 fields 中移除：

```python
# 凭证相关的 key 不再出现在 config fields 中（已移到凭证管理）
_CREDENTIAL_KEYS = frozenset({"api_key", "credentials_path", "base_url"})
```

在 `get_provider_config` 端点中，修改字段构建逻辑：

```python
    # 构建字段列表：排除凭证相关字段（已移到凭证管理区）
    fields: list[FieldInfo] = []
    for key in meta.required_keys:
        if key not in _CREDENTIAL_KEYS:
            fields.append(_build_field(key, required=True, db_entry=db_values.get(key)))
    for key in meta.optional_keys:
        if key not in _CREDENTIAL_KEYS:
            fields.append(_build_field(key, required=False, db_entry=db_values.get(key)))
```

同时修改 status 判定：
```python
    # 状态改为基于凭证表
    repo = CredentialRepository(session)  # 需要添加 session 参数
    has_active = await repo.has_active_credential(provider_id)
    status = "ready" if has_active else "unconfigured"
```

Note: `get_provider_config` 端点需要改为同时依赖 `session`，而非仅依赖 `svc`。

- [ ] **Step 4: 更新已有测试**

修改 `tests/test_providers_api.py`：

- `TestGetProviderConfig` 中的测试需要 mock `CredentialRepository` 来控制 status
- 去掉断言 `api_key` 出现在 fields 中的测试（改为断言它不出现）
- 连接测试类 `TestTestProviderConnection` 需要 mock `CredentialRepository` 代替 `ConfigService`

- [ ] **Step 5: 运行全部 provider 测试**

运行: `uv run python -m pytest tests/test_providers_api.py tests/test_credential_api.py -v`
预期: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add server/routers/providers.py tests/test_providers_api.py tests/test_credential_api.py
git commit -m "feat: 连接测试支持指定凭证，Vertex 上传改造，config 端点移除凭证字段"
```

---

## Task 8: 前端类型定义 + API 方法

**Files:**
- Modify: `frontend/src/types/provider.ts`
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: 添加凭证类型定义**

在 `frontend/src/types/provider.ts` 底部（`UsageStatsResponse` 之前）添加：

```typescript
export interface ProviderCredential {
  id: number;
  provider: string;
  name: string;
  api_key_masked: string | null;
  credentials_filename: string | null;
  base_url: string | null;
  is_active: boolean;
  created_at: string;
}
```

- [ ] **Step 2: 添加 API 方法**

在 `frontend/src/api.ts` 的 Provider 管理 API 区域末尾添加：

```typescript
  // ==================== Provider 凭证管理 API ====================

  /** 获取指定 provider 的凭证列表。 */
  static async listCredentials(providerId: string): Promise<{ credentials: ProviderCredential[] }> {
    return this.request(`/providers/${encodeURIComponent(providerId)}/credentials`);
  }

  /** 新增凭证。 */
  static async createCredential(
    providerId: string,
    data: { name: string; api_key?: string; base_url?: string },
  ): Promise<ProviderCredential> {
    return this.request(`/providers/${encodeURIComponent(providerId)}/credentials`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  /** 更新凭证。 */
  static async updateCredential(
    providerId: string,
    credId: number,
    data: { name?: string; api_key?: string; base_url?: string },
  ): Promise<void> {
    return this.request(
      `/providers/${encodeURIComponent(providerId)}/credentials/${credId}`,
      { method: "PATCH", body: JSON.stringify(data) },
    );
  }

  /** 删除凭证。 */
  static async deleteCredential(providerId: string, credId: number): Promise<void> {
    return this.request(
      `/providers/${encodeURIComponent(providerId)}/credentials/${credId}`,
      { method: "DELETE" },
    );
  }

  /** 激活凭证。 */
  static async activateCredential(providerId: string, credId: number): Promise<void> {
    return this.request(
      `/providers/${encodeURIComponent(providerId)}/credentials/${credId}/activate`,
      { method: "POST" },
    );
  }

  /** 测试指定凭证的连接。 */
  static async testProviderConnection(id: string, credentialId?: number): Promise<ProviderTestResult> {
    const params = credentialId ? `?credential_id=${credentialId}` : "";
    return this.request(`/providers/${encodeURIComponent(id)}/test${params}`, {
      method: "POST",
    });
  }

  /** 上传 Vertex AI 凭证文件并创建凭证记录。 */
  static async uploadVertexCredential(
    name: string,
    file: File,
  ): Promise<ProviderCredential> {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(
      `${API_BASE}/providers/gemini-vertex/credentials/upload?name=${encodeURIComponent(name)}`,
      withAuth({
        method: "POST",
        body: formData,
      }),
    );

    await throwIfNotOk(response, "上传凭证失败");
    return response.json();
  }
```

Note: `testProviderConnection` 的签名变更（添加了可选的 `credentialId` 参数），需要同时更新原有方法。移除旧的 `uploadVertexCredentialsForProvider` 方法。

- [ ] **Step 3: 添加 ProviderCredential 到 imports**

确保 `frontend/src/types/index.ts` 已通过 `export * from "./provider"` 导出新类型（已有，无需修改）。

检查 `frontend/src/api.ts` 的 import，确保引入 `ProviderCredential`：

在顶部 type imports 中添加 `ProviderCredential`（如果使用具名导入的话）。

- [ ] **Step 4: 运行前端类型检查**

运行: `cd frontend && pnpm typecheck`
预期: 无错误

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/provider.ts frontend/src/api.ts
git commit -m "feat: 前端凭证类型定义和 API 方法"
```

---

## Task 9: CredentialList 前端组件

**Files:**
- Create: `frontend/src/components/pages/CredentialList.tsx`

- [ ] **Step 1: 创建 CredentialList 组件**

创建 `frontend/src/components/pages/CredentialList.tsx`：

```tsx
import { useState, useEffect, useCallback, useRef } from "react";
import {
  Check,
  Edit2,
  Loader2,
  Plus,
  Trash2,
  Upload,
  Wifi,
  X,
} from "lucide-react";
import { API } from "@/api";
import type { ProviderCredential, ProviderTestResult } from "@/types";

// ---------------------------------------------------------------------------
// CredentialRow — 单条凭证行
// ---------------------------------------------------------------------------

interface RowProps {
  cred: ProviderCredential;
  providerId: string;
  isVertex: boolean;
  onChanged: () => void;
}

function CredentialRow({ cred, providerId, isVertex, onChanged }: RowProps) {
  const [editing, setEditing] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ProviderTestResult | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [draft, setDraft] = useState({ name: cred.name, api_key: "", base_url: cred.base_url ?? "" });

  const handleActivate = useCallback(async () => {
    await API.activateCredential(providerId, cred.id);
    onChanged();
  }, [providerId, cred.id, onChanged]);

  const handleTest = useCallback(async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await API.testProviderConnection(providerId, cred.id);
      setTestResult(result);
    } catch (e) {
      setTestResult({ success: false, available_models: [], message: String(e) });
    }
    setTesting(false);
  }, [providerId, cred.id]);

  const handleDelete = useCallback(async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    setDeleting(true);
    await API.deleteCredential(providerId, cred.id);
    onChanged();
  }, [providerId, cred.id, confirmDelete, onChanged]);

  const handleSaveEdit = useCallback(async () => {
    const data: Record<string, string> = {};
    if (draft.name !== cred.name) data.name = draft.name;
    if (draft.api_key) data.api_key = draft.api_key;
    if (draft.base_url !== (cred.base_url ?? "")) data.base_url = draft.base_url;
    if (Object.keys(data).length > 0) {
      await API.updateCredential(providerId, cred.id, data);
    }
    setEditing(false);
    onChanged();
  }, [draft, cred, providerId, onChanged]);

  return (
    <div className="border-b border-gray-800 py-3 last:border-b-0">
      <div className="flex items-center gap-3">
        {/* Active indicator */}
        <button
          type="button"
          onClick={cred.is_active ? undefined : handleActivate}
          disabled={cred.is_active}
          className={`h-3 w-3 rounded-full flex-shrink-0 ${
            cred.is_active
              ? "bg-green-400"
              : "border border-gray-600 hover:border-gray-400 cursor-pointer"
          }`}
          title={cred.is_active ? "当前使用中" : "点击激活"}
        />

        {/* Info */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-200">{cred.name}</span>
            {cred.is_active && (
              <span className="rounded bg-green-900/30 px-1.5 py-0.5 text-[10px] text-green-400">
                使用中
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            {cred.api_key_masked && (
              <span className="text-xs text-gray-500 font-mono">{cred.api_key_masked}</span>
            )}
            {cred.credentials_filename && (
              <span className="text-xs text-gray-500">{cred.credentials_filename}</span>
            )}
          </div>
          {cred.base_url && (
            <div className="text-xs text-gray-600 mt-0.5 truncate">{cred.base_url}</div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <button
            type="button"
            onClick={handleTest}
            disabled={testing}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-200"
            title="测试连接"
          >
            {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Wifi className="h-3 w-3" />}
          </button>
          {!isVertex && (
            <button
              type="button"
              onClick={() => {
                setEditing(!editing);
                setDraft({ name: cred.name, api_key: "", base_url: cred.base_url ?? "" });
              }}
              className="inline-flex items-center rounded px-2 py-1 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-200"
              title="编辑"
            >
              <Edit2 className="h-3 w-3" />
            </button>
          )}
          {!confirmDelete ? (
            <button
              type="button"
              onClick={handleDelete}
              disabled={deleting}
              className="inline-flex items-center rounded px-2 py-1 text-xs text-gray-400 hover:bg-gray-800 hover:text-red-400"
              title="删除"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          ) : (
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className="rounded px-2 py-1 text-xs text-red-400 hover:bg-red-900/30"
              >
                {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : "确认"}
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                className="rounded px-2 py-1 text-xs text-gray-400 hover:bg-gray-800"
              >
                取消
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Test result */}
      {testResult && (
        <div className={`mt-2 ml-6 rounded p-2 text-xs ${
          testResult.success ? "bg-green-900/20 text-green-400" : "bg-red-900/20 text-red-400"
        }`}>
          {testResult.message}
          {testResult.success && testResult.available_models.length > 0 && (
            <div className="mt-0.5 opacity-75">
              可用模型: {testResult.available_models.join(", ")}
            </div>
          )}
        </div>
      )}

      {/* Inline edit form */}
      {editing && (
        <div className="mt-2 ml-6 space-y-2 rounded border border-gray-800 bg-gray-900/50 p-3">
          <div>
            <label className="text-xs text-gray-500">名称</label>
            <input
              type="text"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              className="mt-0.5 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500">API Key（留空保留现有值）</label>
            <input
              type="password"
              value={draft.api_key}
              onChange={(e) => setDraft((d) => ({ ...d, api_key: e.target.value }))}
              placeholder="••••••••"
              className="mt-0.5 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100 placeholder-gray-600"
            />
          </div>
          {providerId === "gemini-aistudio" && (
            <div>
              <label className="text-xs text-gray-500">Base URL（可选）</label>
              <input
                type="url"
                value={draft.base_url}
                onChange={(e) => setDraft((d) => ({ ...d, base_url: e.target.value }))}
                placeholder="默认官方地址"
                className="mt-0.5 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100 placeholder-gray-600"
              />
            </div>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void handleSaveEdit()}
              className="inline-flex items-center gap-1 rounded bg-indigo-600 px-3 py-1 text-xs text-white hover:bg-indigo-500"
            >
              <Check className="h-3 w-3" /> 保存
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="inline-flex items-center gap-1 rounded border border-gray-700 px-3 py-1 text-xs text-gray-400 hover:text-gray-200"
            >
              <X className="h-3 w-3" /> 取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AddCredentialForm — 新增凭证表单
// ---------------------------------------------------------------------------

interface AddFormProps {
  providerId: string;
  isVertex: boolean;
  onCreated: () => void;
  onCancel: () => void;
}

function AddCredentialForm({ providerId, isVertex, onCreated, onCancel }: AddFormProps) {
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleSubmit = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      if (isVertex) {
        const file = fileRef.current?.files?.[0];
        if (!file) return;
        await API.uploadVertexCredential(name, file);
      } else {
        await API.createCredential(providerId, {
          name,
          api_key: apiKey || undefined,
          base_url: baseUrl || undefined,
        });
      }
      onCreated();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded border border-gray-700 bg-gray-900/50 p-3 space-y-2">
      <div>
        <label className="text-xs text-gray-500">名称 *</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="例如：个人账号"
          className="mt-0.5 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100 placeholder-gray-600"
        />
      </div>
      {isVertex ? (
        <div>
          <label className="text-xs text-gray-500">凭证文件 *</label>
          <div className="mt-0.5">
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="inline-flex items-center gap-1 rounded border border-gray-700 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800"
            >
              <Upload className="h-3 w-3" /> 选择 JSON 文件
            </button>
            <input ref={fileRef} type="file" accept=".json" className="hidden" />
          </div>
        </div>
      ) : (
        <>
          <div>
            <label className="text-xs text-gray-500">API Key *</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="mt-0.5 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100"
            />
          </div>
          {providerId === "gemini-aistudio" && (
            <div>
              <label className="text-xs text-gray-500">Base URL（可选）</label>
              <input
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="默认官方地址"
                className="mt-0.5 w-full rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-100 placeholder-gray-600"
              />
            </div>
          )}
        </>
      )}
      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={() => void handleSubmit()}
          disabled={saving || !name.trim()}
          className="inline-flex items-center gap-1 rounded bg-indigo-600 px-3 py-1 text-xs text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
          添加
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded border border-gray-700 px-3 py-1 text-xs text-gray-400 hover:text-gray-200"
        >
          取消
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CredentialList — 凭证列表主组件
// ---------------------------------------------------------------------------

interface Props {
  providerId: string;
  onChanged?: () => void;
}

export function CredentialList({ providerId, onChanged }: Props) {
  const [credentials, setCredentials] = useState<ProviderCredential[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const isVertex = providerId === "gemini-vertex";

  const refresh = useCallback(async () => {
    const { credentials: creds } = await API.listCredentials(providerId);
    setCredentials(creds);
    setLoading(false);
    onChanged?.();
  }, [providerId, onChanged]);

  useEffect(() => {
    setLoading(true);
    setShowAdd(false);
    void refresh();
  }, [refresh]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-300">密钥管理</h4>
        {!showAdd && (
          <button
            type="button"
            onClick={() => setShowAdd(true)}
            className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs text-indigo-400 hover:bg-gray-800 hover:text-indigo-300"
          >
            <Plus className="h-3 w-3" /> 添加密钥
          </button>
        )}
      </div>

      {credentials.length === 0 && !showAdd && (
        <p className="text-sm text-gray-500">暂无密钥，请添加。</p>
      )}

      {credentials.map((c) => (
        <CredentialRow
          key={c.id}
          cred={c}
          providerId={providerId}
          isVertex={isVertex}
          onChanged={() => void refresh()}
        />
      ))}

      {showAdd && (
        <div className="mt-2">
          <AddCredentialForm
            providerId={providerId}
            isVertex={isVertex}
            onCreated={() => {
              setShowAdd(false);
              void refresh();
            }}
            onCancel={() => setShowAdd(false)}
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 运行前端类型检查**

运行: `cd frontend && pnpm typecheck`
预期: 无错误

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/pages/CredentialList.tsx
git commit -m "feat: CredentialList 凭证管理前端组件"
```

---

## Task 10: ProviderDetail 页面重构

**Files:**
- Modify: `frontend/src/components/pages/ProviderDetail.tsx`

- [ ] **Step 1: 重构 ProviderDetail**

修改 `frontend/src/components/pages/ProviderDetail.tsx`：

1. 在 imports 中添加 `CredentialList`：
```tsx
import { CredentialList } from "@/components/pages/CredentialList";
```

2. 移除 `CredentialsUploadField` 组件（整个函数和接口定义）

3. 在主组件的 JSX 中，将 `basicFields` 区域替换为 `CredentialList`：

将 `basicFields` 的渲染部分：
```tsx
      {/* Basic fields */}
      <div className="space-y-4">
        {basicFields.map((field) =>
          field.key === "credentials_path" ? (
            <CredentialsUploadField ... />
          ) : (
            <FieldEditor ... />
          )
        )}
      </div>
```

替换为：
```tsx
      {/* Credential management */}
      <CredentialList providerId={providerId} onChanged={onSaved} />
```

4. `basicFields` 过滤逻辑不再需要（因为 config 端点已不返回凭证字段，fields 里只剩高级配置）。简化为：所有 fields 都放在高级配置区。

5. 移除顶部的「测试连接」按钮（测试功能已移到每个凭证行内）。保留 handleSave 逻辑用于高级配置保存。

最终组件结构：

```tsx
export function ProviderDetail({ providerId, onSaved }: Props) {
  const [detail, setDetail] = useState<ProviderConfigDetail | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    let disposed = false;
    setDraft({});
    setDetail(null);
    API.getProviderConfig(providerId).then((res) => {
      if (!disposed) setDetail(res);
    });
    return () => { disposed = true; };
  }, [providerId]);

  const handleSave = useCallback(async () => {
    if (Object.keys(draft).length === 0) return;
    setSaving(true);
    try {
      const patch: Record<string, string | null> = {};
      for (const [key, value] of Object.entries(draft)) {
        patch[key] = value || null;
      }
      await API.patchProviderConfig(providerId, patch);
      const updated = await API.getProviderConfig(providerId);
      setDetail(updated);
      setDraft({});
      onSaved?.();
    } finally {
      setSaving(false);
    }
  }, [draft, providerId, onSaved]);

  if (!detail) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载中…
      </div>
    );
  }

  const hasDraft = Object.keys(draft).length > 0;

  return (
    <div className="max-w-xl">
      {/* Header */}
      <div className="mb-6 flex items-start gap-3">
        <ProviderIcon providerId={providerId} className="mt-0.5 h-7 w-7" />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-gray-100">{detail.display_name}</h3>
            <StatusBadge status={detail.status} />
          </div>
          {detail.description && (
            <p className="mt-1 text-sm text-gray-500">{detail.description}</p>
          )}
        </div>
      </div>

      {/* Capabilities */}
      {detail.media_types && detail.media_types.length > 0 && (
        <div className="mb-5 flex flex-wrap gap-1.5">
          {detail.media_types.map((t) => (
            <span key={t} className="rounded-md bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
              {t === "video" ? "视频" : t === "image" ? "图片" : t}
            </span>
          ))}
        </div>
      )}

      {/* Credentials */}
      <CredentialList providerId={providerId} onChanged={onSaved} />

      {/* Advanced config */}
      {detail.fields.length > 0 && (
        <div className="mt-6">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200"
          >
            <ChevronRight
              className={`h-4 w-4 transition-transform ${showAdvanced ? "rotate-90" : ""}`}
            />
            高级配置
          </button>
          {showAdvanced && (
            <div className="mt-3 space-y-4">
              {detail.fields.map((field) => (
                <FieldEditor key={field.key} field={field} draft={draft} setDraft={setDraft} />
              ))}
              {hasDraft && (
                <button
                  type="button"
                  onClick={() => void handleSave()}
                  disabled={saving}
                  className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50"
                >
                  {saving ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      保存中…
                    </>
                  ) : (
                    "保存"
                  )}
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 运行前端类型检查和测试**

运行: `cd frontend && pnpm check`
预期: typecheck + test 都通过

- [ ] **Step 3: 提交**

```bash
git add frontend/src/components/pages/ProviderDetail.tsx
git commit -m "feat: ProviderDetail 重构为凭证管理区 + 共享配置区"
```

---

## Task 11: 端到端验证 + 清理

**Files:**
- 所有相关文件

- [ ] **Step 1: 运行全部后端测试**

运行: `uv run python -m pytest tests/ -v --timeout=30`
预期: 全部 PASS

- [ ] **Step 2: 运行前端检查**

运行: `cd frontend && pnpm check`
预期: 全部通过

- [ ] **Step 3: 清理旧的 Vertex 上传端点引用**

搜索并确认旧的 `uploadVertexCredentialsForProvider` 在前端无残余引用：

运行: `cd frontend && grep -r "uploadVertexCredentialsForProvider" src/`
预期: 无输出（已全部替换）

- [ ] **Step 4: 验证数据库迁移可回退**

运行: `uv run alembic downgrade -1 && uv run alembic upgrade head`
预期: 正常迁移和回退

- [ ] **Step 5: 提交最终清理（如有）**

```bash
git add -A
git commit -m "chore: 清理旧代码引用，完成多 API Key 功能"
```
