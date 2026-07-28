# 全局资产库 + Clue 重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增全局资产库（人物/场景/道具 跨项目复用），同时彻底删除 `clue`/`importance` 概念拆为独立 `scene`+`prop`，并把项目工作台的手动新增 UI 改造成顶部操作栏+模态表单。

**Architecture:** 后端 Asset ORM + 独立图片目录 `projects/_global_assets/{type}/<uuid>`；project.json 加 `schema_version` 字段，启动时文件级自动迁移器（v0→v1 拆分 clues、重命名文件、级联剧本）；前端新增 `AssetLibraryPage` + 统一 `AssetFormModal`（5 场景复用）+ `AssetPickerModal`；agent skill 合并为 `generate-assets` 并支持三类并行。所有 scene/prop 一律视为"要生成图"，pending 判定从 `importance==major && !sheet` 变为 `!sheet`。

**Tech Stack:** Python 3.11+ / FastAPI / SQLAlchemy async ORM / Alembic / Pydantic / pytest | React 19 + TypeScript / vitest / wouter / zustand / Tailwind / i18next

## 参考设计

- Spec: `docs/superpowers/specs/2026-04-15-global-asset-library-design.md`

## 执行顺序（5 个 Stage）

1. **Stage 1 · 后端 Clue 重构**（Task 1-15）数据层、schema 迁移、scene/prop 路由、生成任务拆分
2. **Stage 2 · 后端 Asset 库**（Task 16-20）Asset ORM、资产 API、全局图片服务
3. **Stage 3 · 前端 Clue 重构**（Task 21-30）types、store、SceneCard/PropCard、路由拆分
4. **Stage 4 · 前端 Asset 库 + UI 改造**（Task 31-40）AssetFormModal / AssetLibraryPage / AssetPickerModal / GalleryToolbar
5. **Stage 5 · Agent / Skill 改造**（Task 41-45）generate-assets / add_assets.py / analyze-assets / manga-workflow 合并

---

## 文件结构

### 后端新增

| 文件 | 职责 |
|---|---|
| `lib/db/models/asset.py` | Asset ORM |
| `lib/db/repositories/asset_repo.py` | Asset 异步 Repository |
| `lib/project_migrations/__init__.py` | MIGRATORS 注册 + 启动时自动迁移入口 |
| `lib/project_migrations/v0_to_v1_clues_to_scenes_props.py` | v0→v1 迁移器（纯函数、幂等） |
| `lib/i18n/{zh,en}/assets.py` | 资产库错误/提示文案 |
| `server/routers/assets.py` | `/api/v1/assets/*` |
| `server/routers/scenes.py` | `/api/v1/scenes/*`（取代 clues） |
| `server/routers/props.py` | `/api/v1/props/*`（取代 clues） |
| `alembic/versions/<hash>_create_assets.py` | 建表迁移 |

### 后端删除

| 文件 | 理由 |
|---|---|
| `server/routers/clues.py` | Clue 概念取消 |

### 后端改造

| 文件 | 改造点 |
|---|---|
| `lib/script_models.py` | `DramaScene.clues` / `NarrationSegment.clues` 拆为 `scenes` + `props`；root 加 `schema_version` |
| `lib/data_validator.py` | 删 `VALID_CLUE_IMPORTANCE`；校验 `scenes` / `props` |
| `lib/project_manager.py` | `add_clue` / `update_clue` / `get_pending_clues` 拆 scene/prop；删 importance 参数；`SUBDIRS` 加 `scenes`/`props` |
| `lib/prompt_builders.py` | `build_clue_prompt` 拆 `build_scene_prompt` + `build_prop_prompt` |
| `lib/prompt_builders_script.py` | 线索提取 prompt 改场景/道具提取 |
| `lib/status_calculator.py` | `clues_count` 拆 `scenes_count` + `props_count` |
| `server/services/generation_tasks.py` | `execute_clue_task` 拆；`collect_reference_sheets` 字段拆 |
| `server/services/project_events.py` | 事件名 clue→scene/prop |
| `server/services/project_archive.py` | 导出目录名调整 |
| `server/services/cost_estimation.py` | 按 scene + prop 分别估 |
| `server/routers/versions.py` | 资源类型 `"clues"` 拆 |
| `server/routers/generate.py` | `POST /generate/clue` 拆 `/generate/scene` + `/generate/prop` |
| `server/routers/files.py` | 新增 `/global-assets/{type}/{filename}` |
| `server/app.py` | Startup 插入 `run_project_migrations()` |

### 前端新增

| 文件 | 职责 |
|---|---|
| `frontend/src/types/asset.ts` | `Asset` / `AssetType` / `AssetPayload` |
| `frontend/src/stores/assets-store.ts` | zustand store：list/byId/loading/actions |
| `frontend/src/components/assets/AssetCard.tsx` | 资产卡片 |
| `frontend/src/components/assets/AssetGrid.tsx` | 网格 |
| `frontend/src/components/assets/AssetFormModal.tsx` | 统一 create/edit/import 模态 |
| `frontend/src/components/assets/AssetPickerModal.tsx` | 从资产库选择对话框 |
| `frontend/src/components/assets/AddToLibraryButton.tsx` | 卡片 📦 按钮 |
| `frontend/src/components/pages/AssetLibraryPage.tsx` | 资产库主页 |
| `frontend/src/components/canvas/lorebook/GalleryToolbar.tsx` | 三类资源页的顶部操作栏 |
| `frontend/src/components/canvas/lorebook/SceneCard.tsx` | 场景卡片 |
| `frontend/src/components/canvas/lorebook/PropCard.tsx` | 道具卡片 |
| `frontend/src/i18n/{zh,en}/assets.ts` | 资产库 namespace |

### 前端删除

| 文件 | 理由 |
|---|---|
| `frontend/src/components/canvas/lorebook/ClueCard.tsx` | 被 SceneCard+PropCard 取代 |
| `frontend/src/components/canvas/lorebook/AddClueForm.tsx` | 迁入 AssetFormModal |
| `frontend/src/components/canvas/lorebook/AddCharacterForm.tsx` | 迁入 AssetFormModal |
| `frontend/src/components/canvas/lorebook/LorebookGallery.tsx` | 拆成独立页 |

### 前端改造

| 文件 | 改造点 |
|---|---|
| `frontend/src/types/project.ts` | 删 `Clue`；新增 `Scene`、`Prop` |
| `frontend/src/types/script.ts` | `DramaScene.clues` / `NarrationSegment.clues` 拆 |
| `frontend/src/stores/projects-store.ts` | 字段迁移 |
| `frontend/src/api.ts` | 删 clue 方法；新增 scene/prop/assets |
| `frontend/src/components/canvas/lorebook/CharacterCard.tsx` | 顶部图标行加 📦；保留现有编辑 |
| `frontend/src/components/canvas/StudioCanvasRouter.tsx` | 路由拆分 + 每页用 GalleryToolbar + 对应卡片网格；表单统一 AssetFormModal |
| `frontend/src/components/layout/GlobalHeader.tsx` | 加 📦 图标按钮 → `/app/assets` |
| `frontend/src/components/layout/AssetSidebar.tsx` | 空态文本改可点击按钮；Clues 子节拆 Scenes + Props |
| `frontend/src/router.tsx` | 加 `/app/assets` 路由 |
| `frontend/src/hooks/useProjectEventsSSE.ts` | 事件类型拆 |
| `frontend/src/i18n/{zh,en}/dashboard.ts` | 删 importance / clue 文案；加 scene / prop |

### Agent / Skill

| 文件 | 操作 |
|---|---|
| `agent_runtime_profile/.claude/skills/generate-clues/` | 重命名 → `.claude/skills/generate-assets/`，SKILL.md 大改 |
| `agent_runtime_profile/.claude/skills/manage-project/scripts/add_characters_clues.py` | 重命名 → `add_assets.py`，CLI 参数改 |
| `agent_runtime_profile/.claude/agents/analyze-characters-clues.md` | 重命名 → `analyze-assets.md`，输出 schema 改 |
| `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md` | 阶段 5/6 合并，并行调度 |
| `agent_runtime_profile/CLAUDE.md` | 清理 clue/importance 提及 |

---

## Stage 1 · 后端 Clue 重构

### Task 1: Asset ORM 模型

**Files:**
- Create: `lib/db/models/asset.py`
- Modify: `lib/db/models/__init__.py`
- Test: `tests/test_asset_model.py`

- [ ] **Step 1: 写测试 `tests/test_asset_model.py`**

```python
"""Asset ORM 模型结构测试。"""
import pytest
from sqlalchemy import select

from lib.db.engine import AsyncSessionLocal
from lib.db.models.asset import Asset


@pytest.mark.asyncio
async def test_asset_create_and_fetch():
    async with AsyncSessionLocal() as session:
        asset = Asset(
            id="00000000-0000-0000-0000-000000000001",
            type="character",
            name="王小明",
            description="白衣少年",
            voice_style="清亮",
            image_path="_global_assets/character/abc.png",
            source_project="demo",
        )
        session.add(asset)
        await session.commit()

        row = (await session.execute(select(Asset).where(Asset.name == "王小明"))).scalar_one()
        assert row.type == "character"
        assert row.voice_style == "清亮"
        assert row.image_path == "_global_assets/character/abc.png"


@pytest.mark.asyncio
async def test_asset_unique_type_name(test_db_cleared):
    async with AsyncSessionLocal() as session:
        session.add(Asset(id="id-1", type="prop", name="玉佩", description=""))
        await session.commit()

        session.add(Asset(id="id-2", type="prop", name="玉佩", description=""))
        with pytest.raises(Exception):
            await session.commit()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_asset_model.py -v`
Expected: FAIL（`lib.db.models.asset` 不存在）

- [ ] **Step 3: 实现 `lib/db/models/asset.py`**

```python
"""Asset ORM: 全局资产库条目。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.models.base import Base


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # character/scene/prop
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    voice_style: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_project: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("type", "name", name="uq_asset_type_name"),
        Index("ix_asset_type", "type"),
        Index("ix_asset_name", "name"),
    )
```

- [ ] **Step 4: 导出到 package**

在 `lib/db/models/__init__.py` 末尾追加：

```python
from lib.db.models.asset import Asset  # noqa: F401
```

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run python -m pytest tests/test_asset_model.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add lib/db/models/asset.py lib/db/models/__init__.py tests/test_asset_model.py
git commit -m "feat(db): Asset ORM 模型 (type/name 唯一约束)"
```

---

### Task 2: Alembic 迁移 — 建 assets 表

**Files:**
- Create: `alembic/versions/<auto>_create_assets.py`（自动生成）

- [ ] **Step 1: 生成迁移文件**

```bash
uv run alembic revision --autogenerate -m "create assets table"
```

- [ ] **Step 2: 审阅生成的迁移文件**

打开 `alembic/versions/<hash>_create_assets.py`，确保：
- `upgrade()` 包含 `op.create_table("assets", ...)` + `UniqueConstraint("type","name")` + 两个索引
- `downgrade()` 包含 `op.drop_table("assets")`

如缺失 UniqueConstraint/索引，手工补齐：

```python
sa.UniqueConstraint("type", "name", name="uq_asset_type_name"),
```

末尾 `op.create_index("ix_asset_type", "assets", ["type"])` / `ix_asset_name`。

- [ ] **Step 3: 应用迁移并验证**

```bash
uv run alembic upgrade head
uv run python -c "import sqlite3; c = sqlite3.connect('projects/.arcreel.db'); print(c.execute('SELECT sql FROM sqlite_master WHERE name=\"assets\"').fetchone())"
```

Expected: 输出 CREATE TABLE assets 的 DDL，包含 UNIQUE(type, name)

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/
git commit -m "feat(db): alembic 迁移新增 assets 表"
```

---

### Task 3: Asset Repository（异步 CRUD）

**Files:**
- Create: `lib/db/repositories/asset_repo.py`
- Test: `tests/test_asset_repo.py`

- [ ] **Step 1: 写测试 `tests/test_asset_repo.py`**

```python
"""AssetRepository 异步 CRUD 测试。"""
import pytest
import uuid

from lib.db.engine import AsyncSessionLocal
from lib.db.repositories.asset_repo import AssetRepository


@pytest.fixture
async def repo():
    async with AsyncSessionLocal() as session:
        yield AssetRepository(session)
        await session.rollback()


@pytest.mark.asyncio
async def test_create_and_get_by_id(repo):
    asset = await repo.create(
        type="character", name="A", description="d", voice_style="", image_path=None, source_project=None
    )
    fetched = await repo.get_by_id(asset.id)
    assert fetched is not None
    assert fetched.name == "A"


@pytest.mark.asyncio
async def test_get_by_type_name_returns_none_when_absent(repo):
    assert await repo.get_by_type_name("scene", "missing") is None


@pytest.mark.asyncio
async def test_list_filters_by_type_and_name_fuzzy(repo):
    await repo.create(type="character", name="王小明", description="", voice_style="")
    await repo.create(type="character", name="小师妹", description="", voice_style="")
    await repo.create(type="scene", name="庙宇", description="", voice_style="")

    chars = await repo.list(type="character", q=None, limit=10, offset=0)
    assert len(chars) == 2

    fuzzy = await repo.list(type="character", q="小", limit=10, offset=0)
    assert len(fuzzy) == 2

    scenes = await repo.list(type="scene", q=None, limit=10, offset=0)
    assert len(scenes) == 1


@pytest.mark.asyncio
async def test_update_patch_fields(repo):
    asset = await repo.create(type="prop", name="玉佩", description="旧", voice_style="")
    updated = await repo.update(asset.id, description="新")
    assert updated.description == "新"


@pytest.mark.asyncio
async def test_delete_removes_row(repo):
    asset = await repo.create(type="prop", name="key", description="", voice_style="")
    await repo.delete(asset.id)
    assert await repo.get_by_id(asset.id) is None


@pytest.mark.asyncio
async def test_exists(repo):
    await repo.create(type="prop", name="key", description="", voice_style="")
    assert await repo.exists("prop", "key") is True
    assert await repo.exists("prop", "nope") is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_asset_repo.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `lib/db/repositories/asset_repo.py`**

```python
"""AssetRepository: 异步 CRUD。"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.asset import Asset


class AssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        type: str,
        name: str,
        description: str = "",
        voice_style: str = "",
        image_path: str | None = None,
        source_project: str | None = None,
    ) -> Asset:
        asset = Asset(
            id=str(uuid.uuid4()),
            type=type,
            name=name,
            description=description,
            voice_style=voice_style,
            image_path=image_path,
            source_project=source_project,
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def get_by_id(self, asset_id: str) -> Asset | None:
        return (
            await self._session.execute(select(Asset).where(Asset.id == asset_id))
        ).scalar_one_or_none()

    async def get_by_type_name(self, type: str, name: str) -> Asset | None:
        return (
            await self._session.execute(
                select(Asset).where(Asset.type == type, Asset.name == name)
            )
        ).scalar_one_or_none()

    async def list(
        self,
        *,
        type: str | None,
        q: str | None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Asset]:
        stmt = select(Asset)
        if type:
            stmt = stmt.where(Asset.type == type)
        if q:
            stmt = stmt.where(Asset.name.contains(q))
        stmt = stmt.order_by(Asset.updated_at.desc()).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars())

    async def update(self, asset_id: str, **fields: Any) -> Asset:
        asset = await self.get_by_id(asset_id)
        if asset is None:
            raise ValueError(f"Asset not found: {asset_id}")
        for k, v in fields.items():
            setattr(asset, k, v)
        await self._session.flush()
        return asset

    async def delete(self, asset_id: str) -> None:
        asset = await self.get_by_id(asset_id)
        if asset:
            await self._session.delete(asset)
            await self._session.flush()

    async def exists(self, type: str, name: str) -> bool:
        return await self.get_by_type_name(type, name) is not None
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run python -m pytest tests/test_asset_repo.py -v`
Expected: PASS

- [ ] **Step 5: Format + Commit**

```bash
uv run ruff format lib/db/repositories/asset_repo.py tests/test_asset_repo.py
uv run ruff check lib/db/repositories/asset_repo.py tests/test_asset_repo.py
git add lib/db/repositories/asset_repo.py tests/test_asset_repo.py
git commit -m "feat(db): AssetRepository 异步 CRUD + 模糊搜索"
```

---

### Task 4: 项目迁移框架 `lib/project_migrations/`

**Files:**
- Create: `lib/project_migrations/__init__.py`
- Create: `lib/project_migrations/runner.py`
- Test: `tests/test_project_migration_runner.py`

- [ ] **Step 1: 写测试 `tests/test_project_migration_runner.py`**

```python
"""迁移 runner：版本检测、幂等、错误隔离、备份清理。"""
import json
import time
from pathlib import Path

import pytest

from lib.project_migrations.runner import (
    CURRENT_SCHEMA_VERSION,
    run_project_migrations,
    cleanup_stale_backups,
)


@pytest.fixture
def tmp_projects(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def _write_project(root: Path, name: str, data: dict) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(data, ensure_ascii=False))
    return d


def test_skip_already_current(tmp_projects: Path):
    _write_project(tmp_projects, "p1", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "p1"})
    summary = run_project_migrations(tmp_projects)
    assert summary.migrated == []
    assert summary.skipped == ["p1"]


def test_migrate_v0_bumps_version(tmp_projects: Path, monkeypatch):
    _write_project(tmp_projects, "p1", {"name": "p1"})  # 无 schema_version

    called = {}

    def fake_migrate_v0_to_v1(project_dir: Path) -> None:
        called["p1"] = True
        data = json.loads((project_dir / "project.json").read_text())
        data["schema_version"] = 1
        (project_dir / "project.json").write_text(json.dumps(data))

    monkeypatch.setattr(
        "lib.project_migrations.runner.MIGRATORS",
        {0: fake_migrate_v0_to_v1},
    )

    summary = run_project_migrations(tmp_projects)
    assert "p1" in summary.migrated
    assert called == {"p1": True}
    data = json.loads((tmp_projects / "p1" / "project.json").read_text())
    assert data["schema_version"] == 1


def test_skip_underscore_dirs(tmp_projects: Path):
    (tmp_projects / "_global_assets").mkdir()
    (tmp_projects / "_global_assets" / "keep.txt").write_text("x")
    _write_project(tmp_projects, "p1", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "p1"})
    summary = run_project_migrations(tmp_projects)
    assert "_global_assets" not in summary.skipped
    assert "_global_assets" not in summary.migrated


def test_error_isolated_not_abort(tmp_projects: Path, monkeypatch):
    _write_project(tmp_projects, "broken", {"name": "broken"})
    _write_project(tmp_projects, "ok", {"schema_version": CURRENT_SCHEMA_VERSION, "name": "ok"})

    def bad(_d):
        raise RuntimeError("boom")

    monkeypatch.setattr("lib.project_migrations.runner.MIGRATORS", {0: bad})
    summary = run_project_migrations(tmp_projects)
    assert "broken" in summary.failed
    assert "ok" in summary.skipped


def test_cleanup_old_backups(tmp_projects: Path):
    p = _write_project(tmp_projects, "p1", {"schema_version": 1})
    old = p / "project.json.bak.v0-100000000"
    new = p / "project.json.bak.v0-9999999999"
    old.write_text("old")
    new.write_text("new")

    # mtime 控制：old 文件 mtime 设为 8 天前
    eight_days_ago = time.time() - 8 * 86400
    import os
    os.utime(old, (eight_days_ago, eight_days_ago))

    cleanup_stale_backups(tmp_projects, max_age_days=7)
    assert not old.exists()
    assert new.exists()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_project_migration_runner.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `lib/project_migrations/__init__.py`**

```python
"""Project 文件级 schema 迁移框架。

约定：project.json 顶层 schema_version。缺失视为 v0。当前版本 = 1。
迁移器是纯函数，幂等，签名 ``def migrate(project_dir: Path) -> None``。
"""
from lib.project_migrations.runner import (
    CURRENT_SCHEMA_VERSION,
    MIGRATORS,
    run_project_migrations,
    cleanup_stale_backups,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "MIGRATORS",
    "run_project_migrations",
    "cleanup_stale_backups",
]
```

- [ ] **Step 4: 实现 `lib/project_migrations/runner.py`**

```python
"""Runner: 扫描 projects/ 并按版本顺序跑迁移器。"""
from __future__ import annotations

import json
import logging
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION = 1

# 实际 migrator 在 v0_to_v1_clues_to_scenes_props.py 注册（Task 5）
MIGRATORS: dict[int, Callable[[Path], None]] = {}


@dataclass
class MigrationSummary:
    migrated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _load_schema_version(project_dir: Path) -> int:
    pj = project_dir / "project.json"
    if not pj.exists():
        return -1  # 跳过非项目目录
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("project.json 损坏，跳过：%s", project_dir)
        return -1
    return int(data.get("schema_version", 0))


def _backup_project_json(project_dir: Path, from_version: int) -> None:
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    ts = int(time.time())
    bak = project_dir / f"project.json.bak.v{from_version}-{ts}"
    bak.write_bytes(pj.read_bytes())


def run_project_migrations(projects_root: Path) -> MigrationSummary:
    """扫 projects_root 下每个项目目录，升级到 CURRENT_SCHEMA_VERSION。"""
    summary = MigrationSummary()
    if not projects_root.exists():
        return summary

    error_log = projects_root / "_migration_errors.log"

    for child in sorted(projects_root.iterdir()):
        if not child.is_dir():
            continue
        # 跳过下划线前缀与隐藏目录
        if child.name.startswith("_") or child.name.startswith("."):
            continue

        version = _load_schema_version(child)
        if version < 0:
            continue  # 非项目目录
        if version >= CURRENT_SCHEMA_VERSION:
            summary.skipped.append(child.name)
            continue

        try:
            # 逐级迁移
            while version < CURRENT_SCHEMA_VERSION:
                _backup_project_json(child, version)
                migrator = MIGRATORS.get(version)
                if not migrator:
                    raise RuntimeError(f"no migrator from v{version}")
                migrator(child)
                version += 1
            summary.migrated.append(child.name)
        except Exception as e:
            summary.failed.append(child.name)
            tb = traceback.format_exc()
            logger.error("迁移失败 %s: %s", child.name, e)
            error_log.parent.mkdir(parents=True, exist_ok=True)
            with error_log.open("a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {child.name}\n{tb}\n")

    return summary


def cleanup_stale_backups(projects_root: Path, max_age_days: int = 7) -> None:
    """删除超过 max_age_days 的 .bak.v*- 备份文件。"""
    if not projects_root.exists():
        return
    cutoff = time.time() - max_age_days * 86400
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        for bak in project_dir.glob("project.json.bak.v*-*"):
            try:
                if bak.stat().st_mtime < cutoff:
                    bak.unlink()
            except OSError:
                logger.warning("无法删除备份：%s", bak)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run python -m pytest tests/test_project_migration_runner.py -v`
Expected: PASS（所有 5 个 case）

- [ ] **Step 6: Format + Commit**

```bash
uv run ruff format lib/project_migrations/ tests/test_project_migration_runner.py
uv run ruff check lib/project_migrations/ tests/test_project_migration_runner.py
git add lib/project_migrations/ tests/test_project_migration_runner.py
git commit -m "feat(migrations): 项目级 schema 迁移 runner + 备份清理"
```

---

### Task 5: v0→v1 迁移器（clue → scenes/props）

**Files:**
- Create: `lib/project_migrations/v0_to_v1_clues_to_scenes_props.py`
- Test: `tests/test_project_migration_v0_v1.py`

- [ ] **Step 1: 写测试 `tests/test_project_migration_v0_v1.py`**

```python
"""v0→v1 迁移：clues → scenes/props + 剧本级联 + 文件重命名。"""
import json
from pathlib import Path

import pytest

from lib.project_migrations.v0_to_v1_clues_to_scenes_props import migrate_v0_to_v1


def _make_v0_project(root: Path) -> Path:
    p = root / "demo"
    (p / "characters").mkdir(parents=True)
    (p / "clues").mkdir(parents=True)
    (p / "clues" / "玉佩.png").write_bytes(b"prop-image")
    (p / "clues" / "庙宇.png").write_bytes(b"scene-image")
    (p / "scripts").mkdir(parents=True)

    (p / "project.json").write_text(json.dumps({
        "name": "demo",
        "characters": {"王小明": {"description": "", "voice_style": ""}},
        "clues": {
            "玉佩": {"type": "prop", "importance": "major", "description": "白玉", "clue_sheet": "clues/玉佩.png"},
            "庙宇": {"type": "location", "importance": "minor", "description": "阴森"},
        },
    }, ensure_ascii=False), encoding="utf-8")

    (p / "scripts" / "ep1.json").write_text(json.dumps({
        "content_mode": "drama",
        "scenes": [
            {"scene_id": "s1", "characters": ["王小明"], "clues": ["玉佩", "庙宇"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    return p


def test_migrate_v0_to_v1_project_json(tmp_path: Path):
    p = _make_v0_project(tmp_path)
    migrate_v0_to_v1(p)

    data = json.loads((p / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert "clues" not in data
    assert set(data["scenes"].keys()) == {"庙宇"}
    assert set(data["props"].keys()) == {"玉佩"}
    # importance / type 字段被清理
    assert "importance" not in data["props"]["玉佩"]
    assert "type" not in data["props"]["玉佩"]
    # sheet 字段重命名
    assert data["props"]["玉佩"]["prop_sheet"] == "props/玉佩.png"
    assert "clue_sheet" not in data["props"]["玉佩"]


def test_migrate_v0_to_v1_moves_files(tmp_path: Path):
    p = _make_v0_project(tmp_path)
    migrate_v0_to_v1(p)

    assert not (p / "clues").exists()
    assert (p / "scenes" / "庙宇.png").read_bytes() == b"scene-image"
    assert (p / "props" / "玉佩.png").read_bytes() == b"prop-image"


def test_migrate_v0_to_v1_script_clues_split(tmp_path: Path):
    p = _make_v0_project(tmp_path)
    migrate_v0_to_v1(p)

    script = json.loads((p / "scripts" / "ep1.json").read_text(encoding="utf-8"))
    assert script["schema_version"] == 1
    scene = script["scenes"][0]
    assert "clues" not in scene
    assert scene["scenes"] == ["庙宇"]
    assert scene["props"] == ["玉佩"]


def test_migrate_idempotent(tmp_path: Path):
    p = _make_v0_project(tmp_path)
    migrate_v0_to_v1(p)
    migrate_v0_to_v1(p)  # 再跑一次不应抛错
    data = json.loads((p / "project.json").read_text())
    assert data["schema_version"] == 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run python -m pytest tests/test_project_migration_v0_v1.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 `lib/project_migrations/v0_to_v1_clues_to_scenes_props.py`**

```python
"""v0→v1 迁移：拆分 clues → scenes + props；删除 importance；级联剧本 JSON。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(tmp, path)


def _split_clues(clues: dict[str, dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    scenes: dict[str, dict] = {}
    props: dict[str, dict] = {}
    for name, data in clues.items():
        clue_type = (data.get("type") or "prop").lower()
        new_item = {
            "description": data.get("description", ""),
        }
        # 保留生成图路径但换字段名
        sheet = data.get("clue_sheet")
        if clue_type == "location":
            if sheet:
                new_item["scene_sheet"] = sheet.replace("clues/", "scenes/", 1)
            scenes[name] = new_item
        else:
            if sheet:
                new_item["prop_sheet"] = sheet.replace("clues/", "props/", 1)
            props[name] = new_item
    return scenes, props


def _relocate_clue_files(project_dir: Path, old_clues: dict[str, dict]) -> None:
    clues_dir = project_dir / "clues"
    if not clues_dir.exists():
        return
    scenes_dir = project_dir / "scenes"
    props_dir = project_dir / "props"
    scenes_dir.mkdir(exist_ok=True)
    props_dir.mkdir(exist_ok=True)

    for name, data in old_clues.items():
        clue_type = (data.get("type") or "prop").lower()
        target = scenes_dir if clue_type == "location" else props_dir
        for ext in ("png", "jpg", "jpeg", "webp"):
            src = clues_dir / f"{name}.{ext}"
            if src.exists():
                shutil.move(str(src), str(target / f"{name}.{ext}"))

    # 清理空 clues 目录（即使有残余未知文件也保留，避免误删）
    try:
        clues_dir.rmdir()
    except OSError:
        pass

    # versions/clues 同样按原 clue type 分流
    versions_clues = project_dir / "versions" / "clues"
    if versions_clues.exists():
        for name, data in old_clues.items():
            clue_type = (data.get("type") or "prop").lower()
            target_versions = project_dir / "versions" / ("scenes" if clue_type == "location" else "props")
            target_versions.mkdir(parents=True, exist_ok=True)
            for file in versions_clues.glob(f"{name}*"):
                shutil.move(str(file), str(target_versions / file.name))
        try:
            versions_clues.rmdir()
        except OSError:
            pass


def _migrate_scripts(project_dir: Path, old_clues: dict[str, dict]) -> None:
    """把剧本里每条 scene/segment 的 clues[] 拆为 scenes[] + props[]"""
    scripts_dir = project_dir / "scripts"
    if not scripts_dir.exists():
        return

    def kind(clue_name: str) -> str:
        data = old_clues.get(clue_name, {})
        return "scene" if (data.get("type") or "prop").lower() == "location" else "prop"

    for sp in scripts_dir.glob("*.json"):
        try:
            data = _load_json(sp)
        except Exception:
            continue
        if data.get("schema_version", 0) >= 1:
            continue

        for bucket_key in ("scenes", "segments"):
            items = data.get(bucket_key) or []
            for item in items:
                old = item.pop("clues", None)
                if old is None:
                    continue
                scenes_list: list[str] = []
                props_list: list[str] = []
                for nm in old:
                    (scenes_list if kind(nm) == "scene" else props_list).append(nm)
                item["scenes"] = scenes_list
                item["props"] = props_list

        data["schema_version"] = 1
        _atomic_write_json(sp, data)


def migrate_v0_to_v1(project_dir: Path) -> None:
    """幂等。若已是 v1 直接返回。"""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = _load_json(pj)
    if data.get("schema_version", 0) >= 1:
        return

    old_clues: dict[str, dict] = data.get("clues") or {}
    scenes, props = _split_clues(old_clues)

    # 更新 project.json
    data["scenes"] = scenes
    data["props"] = props
    data.pop("clues", None)
    data["schema_version"] = 1
    _atomic_write_json(pj, data)

    # 文件系统
    _relocate_clue_files(project_dir, old_clues)

    # 级联剧本
    _migrate_scripts(project_dir, old_clues)
```

- [ ] **Step 4: 注册 migrator 到 MIGRATORS**

编辑 `lib/project_migrations/runner.py`：

```python
# 在 MIGRATORS 定义后追加：
from lib.project_migrations.v0_to_v1_clues_to_scenes_props import migrate_v0_to_v1

MIGRATORS[0] = migrate_v0_to_v1
```

- [ ] **Step 5: 运行测试**

```bash
uv run python -m pytest tests/test_project_migration_v0_v1.py tests/test_project_migration_runner.py -v
```
Expected: 全部 PASS

- [ ] **Step 6: Format + Commit**

```bash
uv run ruff format lib/project_migrations/ tests/test_project_migration_v0_v1.py
uv run ruff check lib/project_migrations/ tests/test_project_migration_v0_v1.py
git add lib/project_migrations/ tests/test_project_migration_v0_v1.py
git commit -m "feat(migrations): v0→v1 迁移器 (clues 拆 scenes+props, 级联剧本)"
```

---

### Task 6: 启动时自动跑迁移

**Files:**
- Modify: `server/app.py`
- Test: `tests/test_app_startup_migration.py`

- [ ] **Step 1: 写测试 `tests/test_app_startup_migration.py`**

```python
"""FastAPI 启动时调用 run_project_migrations。"""
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_startup_invokes_migrations(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))

    with patch("lib.project_migrations.run_project_migrations") as mock_run, \
         patch("lib.project_migrations.cleanup_stale_backups") as mock_cleanup:
        from server.app import app

        async with app.router.lifespan_context(app):
            pass

        mock_run.assert_called_once()
        mock_cleanup.assert_called_once()
```

- [ ] **Step 2: 运行测试（预期失败）**

Run: `uv run python -m pytest tests/test_app_startup_migration.py -v`
Expected: FAIL（启动流程没调用）

- [ ] **Step 3: 在 `server/app.py` 的 lifespan 里插入迁移调用**

在现有 `@asynccontextmanager` 装饰的 `lifespan` 函数中，`yield` 之前追加：

```python
from lib.project_migrations import run_project_migrations, cleanup_stale_backups
from lib.project_manager import get_projects_root

projects_root = get_projects_root()
summary = run_project_migrations(projects_root)
if summary.migrated:
    logger.info("Project migrations: migrated=%s skipped=%d failed=%d",
                summary.migrated, len(summary.skipped), len(summary.failed))
cleanup_stale_backups(projects_root, max_age_days=7)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `uv run python -m pytest tests/test_app_startup_migration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_app_startup_migration.py
git commit -m "feat(app): 启动时自动跑项目迁移 + 7 天备份清理"
```

---

### Task 7: `lib/script_models.py` 拆分 clues → scenes/props

**Files:**
- Modify: `lib/script_models.py`
- Test: `tests/test_script_models.py`（新增或扩展现有）

- [ ] **Step 1: 写测试**

```python
"""DramaScene / NarrationSegment 字段迁移后的测试。"""
import pytest
from pydantic import ValidationError

from lib.script_models import DramaScene, NarrationSegment


def test_drama_scene_has_scenes_and_props_not_clues():
    s = DramaScene(
        scene_id="s1",
        characters=["王"],
        scenes=["庙宇"],
        props=["玉佩"],
        image_prompt="p",
        video_prompt="v",
        duration_seconds=4,
    )
    assert s.scenes == ["庙宇"]
    assert s.props == ["玉佩"]
    assert not hasattr(s, "clues")


def test_narration_segment_has_scenes_and_props():
    s = NarrationSegment(
        segment_id="n1",
        text="x",
        characters=[],
        scenes=[],
        props=[],
        image_prompt="p",
        video_prompt="v",
        duration_seconds=4,
    )
    assert s.scenes == []
    assert s.props == []
```

- [ ] **Step 2: 运行测试（预期失败）**

Run: `uv run python -m pytest tests/test_script_models.py -v`
Expected: FAIL（字段 `scenes` / `props` 不存在）

- [ ] **Step 3: 改写 `lib/script_models.py`**

把 `DramaScene` 和 `NarrationSegment` 中的 `clues: list[str] = Field(default_factory=list)` 替换为：

```python
scenes: list[str] = Field(default_factory=list, description="场景引用名列表")
props: list[str] = Field(default_factory=list, description="道具引用名列表")
```

保留其他字段不动。如有 `EpisodeScript` 根类，确保 root 可选 `schema_version: int | None = None`。

- [ ] **Step 4: 运行测试**

```bash
uv run python -m pytest tests/test_script_models.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff format lib/script_models.py tests/test_script_models.py
git add lib/script_models.py tests/test_script_models.py
git commit -m "refactor(models): DramaScene/NarrationSegment 拆 clues 为 scenes+props"
```

---

### Task 8: `lib/data_validator.py` 删除 importance + 拆 scenes/props 校验

**Files:**
- Modify: `lib/data_validator.py`
- Test: `tests/test_data_validator.py`（改）

- [ ] **Step 1: 改测试**

在 `tests/test_data_validator.py` 中：
- 删除所有 `importance` 相关 assertion（如 `"线索 '...' 缺少必填字段: importance"` 测试）
- 删除对 `VALID_CLUE_IMPORTANCE` 的引用
- 新增对 `scenes` / `props` dict 校验的测试：每条必须有 `description`（空串允许），图片字段名分别为 `scene_sheet` / `prop_sheet`

完整示例：

```python
def test_project_json_validates_scenes_and_props():
    from lib.data_validator import ProjectValidator

    valid = {
        "name": "demo",
        "schema_version": 1,
        "characters": {},
        "scenes": {"庙宇": {"description": "阴森"}},
        "props": {"玉佩": {"description": "白玉"}},
    }
    assert ProjectValidator().validate_project(valid) == []


def test_project_json_rejects_legacy_clues():
    from lib.data_validator import ProjectValidator

    legacy = {"name": "demo", "clues": {"x": {"type": "prop", "importance": "major"}}}
    errors = ProjectValidator().validate_project(legacy)
    # clues 字段应提示迁移
    assert any("clues" in e for e in errors)
```

- [ ] **Step 2: 运行测试（预期失败）**

```bash
uv run python -m pytest tests/test_data_validator.py -v
```
Expected: FAIL（当前校验 clues + importance）

- [ ] **Step 3: 改 `lib/data_validator.py`**

删除：
- `VALID_CLUE_IMPORTANCE` 常量
- `_validate_clues()` 方法（或改名 `_validate_scenes` + `_validate_props`）
- 所有 `importance` 相关校验

新增两个方法：

```python
def _validate_scenes(self, scenes: dict, errors: list[str]) -> None:
    for name, data in scenes.items():
        if not isinstance(data, dict):
            errors.append(f"scenes['{name}'] 必须是字典")
            continue
        # description 可空字符串但必须存在
        if "description" not in data:
            errors.append(f"scenes['{name}'] 缺少 description")

def _validate_props(self, props: dict, errors: list[str]) -> None:
    for name, data in props.items():
        if not isinstance(data, dict):
            errors.append(f"props['{name}'] 必须是字典")
            continue
        if "description" not in data:
            errors.append(f"props['{name}'] 缺少 description")
```

在 `validate_project()` 主流程里：
- 若顶层出现 `clues`（未迁移），报告 `"project.json 含已废弃字段 clues，请等待自动迁移或手动重启服务"`
- 调用 `_validate_scenes(data.get("scenes") or {}, errors)` 和 `_validate_props(...)`

- [ ] **Step 4: 运行测试**

```bash
uv run python -m pytest tests/test_data_validator.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff format lib/data_validator.py tests/test_data_validator.py
git add lib/data_validator.py tests/test_data_validator.py
git commit -m "refactor(validator): 删 importance；scenes/props 独立校验"
```

---

### Task 9: `lib/project_manager.py` 重写 clue 方法为 scene/prop

**Files:**
- Modify: `lib/project_manager.py`
- Test: `tests/test_project_manager_more.py`（改）

**背景**：现有方法 `add_clue` / `update_clue` / `delete_clue` / `add_clues_batch` / `get_pending_clues` 需要拆成 scene 和 prop 两套；`SUBDIRS` 常量里 `clues` 替换为 `scenes` + `props`。

- [ ] **Step 1: 改测试 `tests/test_project_manager_more.py`**

删除所有 `clue` 测试，替换为：

```python
def test_add_scene_creates_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import ProjectManager
    pm = ProjectManager()
    pm.create_project("demo", "Demo")
    assert pm.add_scene("demo", "庙宇", "阴森古朴") is True
    # 再次添加同名应返回 False
    assert pm.add_scene("demo", "庙宇", "x") is False
    data = pm.load_project("demo")
    assert data["scenes"]["庙宇"]["description"] == "阴森古朴"


def test_add_prop_creates_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import ProjectManager
    pm = ProjectManager()
    pm.create_project("demo", "Demo")
    assert pm.add_prop("demo", "玉佩", "白玉雕纹") is True
    data = pm.load_project("demo")
    assert data["props"]["玉佩"]["description"] == "白玉雕纹"


def test_get_pending_scenes_lists_without_sheet(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import ProjectManager
    pm = ProjectManager()
    pm.create_project("demo", "Demo")
    pm.add_scene("demo", "A", "")
    pm.add_scene("demo", "B", "")
    # 让 B 有 sheet
    pm.update_project("demo", lambda d: d["scenes"]["B"].update({"scene_sheet": "scenes/B.png"}))

    pending = pm.get_pending_scenes("demo")
    assert [n for n, _ in pending] == ["A"]
```

- [ ] **Step 2: 运行测试（预期失败）**

```bash
uv run python -m pytest tests/test_project_manager_more.py -v
```
Expected: FAIL（方法不存在）

- [ ] **Step 3: 改 `lib/project_manager.py`**

找到 `SUBDIRS` 常量（约第 47 行），从中去掉 `clues`，加入 `scenes`、`props`。例如：

```python
SUBDIRS = (
    "characters",
    "scenes",
    "props",
    "storyboards",
    "videos",
    "scripts",
    "versions",
    "source",
)
```

替换 clue 方法。删除：
- `add_clue` / `update_clue` / `delete_clue` / `add_clues_batch` / `get_pending_clues`

新增两组对称方法，示例（以 scene 为例，prop 同构）：

```python
def add_scene(self, project_name: str, name: str, description: str) -> bool:
    """已存在返回 False。"""
    def _mut(data: dict) -> None:
        scenes = data.setdefault("scenes", {})
        if name in scenes:
            raise KeyError("EXISTS")
        scenes[name] = {"description": description}

    try:
        self.update_project(project_name, _mut)
        return True
    except KeyError as e:
        if str(e) == "'EXISTS'":
            return False
        raise


def update_scene(self, project_name: str, name: str, fields: dict) -> None:
    def _mut(data: dict) -> None:
        scenes = data.setdefault("scenes", {})
        if name not in scenes:
            raise KeyError(name)
        scenes[name].update(fields)

    self.update_project(project_name, _mut)


def delete_scene(self, project_name: str, name: str) -> None:
    def _mut(data: dict) -> None:
        data.get("scenes", {}).pop(name, None)
    self.update_project(project_name, _mut)


def get_pending_scenes(self, project_name: str) -> list[tuple[str, dict]]:
    data = self.load_project(project_name)
    return [(n, d) for n, d in (data.get("scenes") or {}).items() if not d.get("scene_sheet")]


def add_scenes_batch(self, project_name: str, scenes: dict[str, dict]) -> int:
    """批量合并；已有同名跳过。返回新增条目数。"""
    added = 0

    def _mut(data: dict) -> None:
        nonlocal added
        bucket = data.setdefault("scenes", {})
        for nm, payload in scenes.items():
            if nm in bucket:
                continue
            bucket[nm] = {"description": payload.get("description", "")}
            added += 1

    self.update_project(project_name, _mut)
    return added
```

Prop 同构实现（用 `props` 字段与 `prop_sheet`）。

Character 相关方法不变。

- [ ] **Step 4: 运行测试**

```bash
uv run python -m pytest tests/test_project_manager_more.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff format lib/project_manager.py tests/test_project_manager_more.py
git add lib/project_manager.py tests/test_project_manager_more.py
git commit -m "refactor(project-manager): clue 方法拆为 scene/prop, SUBDIRS 更新"
```

---

### Task 10: `lib/prompt_builders.py` 拆分

**Files:**
- Modify: `lib/prompt_builders.py`
- Modify: `lib/prompt_builders_script.py`（线索提取文案）

- [ ] **Step 1: 查看现有 `build_clue_prompt` 签名**

```bash
grep -n "def build_clue_prompt" lib/prompt_builders.py
```

- [ ] **Step 2: 实现 `build_scene_prompt` 与 `build_prop_prompt`**

在 `lib/prompt_builders.py` 中，拷贝原 `build_clue_prompt` 两份，重命名为 `build_scene_prompt` / `build_prop_prompt`。两者差异仅在 `style_desc` 段文案（scene 强调空间/氛围，prop 强调质感/形态）。保留原签名 `(name, user_prompt, clue_type, style, style_desc)`，但把 `clue_type` 参数移除（新函数类型内嵌）。

删除 `build_clue_prompt`。

- [ ] **Step 3: 改 `lib/prompt_builders_script.py`**

搜索 `线索` 出现：把"线索提取"这节 prompt 改为"场景提取"与"道具提取"两节，说明分别指空间位置 / 物体道具，要求 LLM 输出 `scenes: {}` 与 `props: {}` 两个字典。

- [ ] **Step 4: 更新所有调用方**

`grep -rn "build_clue_prompt" server/ lib/` 找出所有调用点，依据上下文（来自 scene 还是 prop）改为对应新函数。

- [ ] **Step 5: 运行测试**

```bash
uv run python -m pytest tests/ -v -k "prompt" --no-header
```
Expected: PASS（或先跑全部后再修复）

- [ ] **Step 6: Commit**

```bash
uv run ruff format lib/prompt_builders.py lib/prompt_builders_script.py
git add lib/prompt_builders.py lib/prompt_builders_script.py
git commit -m "refactor(prompt): build_clue_prompt 拆为 build_scene_prompt + build_prop_prompt"
```

---

### Task 11: `server/services/generation_tasks.py` 拆 execute_clue_task

**Files:**
- Modify: `server/services/generation_tasks.py`
- Test: `tests/test_generation_tasks_service.py`（改）

- [ ] **Step 1: 改测试**

删除 `test_execute_clue_task` 系列，新增 `test_execute_scene_task` / `test_execute_prop_task`。核心断言：
- 任务执行后 project.json 的 `scenes[name].scene_sheet` / `props[name].prop_sheet` 被写入
- 图片落盘到 `scenes/<name>.png` / `props/<name>.png`
- 版本被记录

- [ ] **Step 2: 改实现**

在 `server/services/generation_tasks.py`：

- 找到 `async def execute_clue_task(...)` —— 拷贝两份重命名为 `execute_scene_task` 与 `execute_prop_task`
- 把内部：
  - `project_path / "clues" / f"{resource_id}.png"` → `project_path / ("scenes" if scene else "props") / f"{resource_id}.png"`
  - 字段 `project["clues"][resource_id]["clue_sheet"]` → `project["scenes"][resource_id]["scene_sheet"]` / `project["props"][resource_id]["prop_sheet"]`
  - `resource_type="clues"` → `resource_type="scenes"` / `"props"`（影响版本目录）
  - `build_clue_prompt` → `build_scene_prompt` / `build_prop_prompt`
- 在 task_type 注册表里：

```python
TASK_EXECUTORS = {
    ...,
    "scene": execute_scene_task,
    "prop": execute_prop_task,
    # 旧 'clue' 保留为兼容别名，映射到 execute_prop_task（只作为 fallback 不再新发）
    "clue": execute_prop_task,
}
```

（别名保留理由：已存在 task_type='clue' 的旧任务仍可回放；新任务一律发 scene/prop）

- `collect_reference_sheets`：把 `clue_field` 参数拆为 `scene_field`、`prop_field`，分别从 `project.scenes` / `project.props` 拿 sheet。

- `get_storyboard_items` 的返回 tuple 从 `(items, id_field, char_field, clue_field)` 改为 `(items, id_field, char_field, scene_field, prop_field)`——调用方相应更新。

- [ ] **Step 3: 运行测试**

```bash
uv run python -m pytest tests/test_generation_tasks_service.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
uv run ruff format server/services/generation_tasks.py tests/test_generation_tasks_service.py
git add server/services/generation_tasks.py tests/test_generation_tasks_service.py
git commit -m "refactor(tasks): execute_clue_task 拆 scene/prop + 参考图 sheet 双路注入"
```

---

### Task 12: status_calculator / project_archive / project_events / cost_estimation / versions_router 跟进

**Files:**
- Modify: `lib/status_calculator.py`
- Modify: `server/services/project_archive.py`
- Modify: `server/services/project_events.py`
- Modify: `server/services/cost_estimation.py`
- Modify: `server/routers/versions.py`
- Modify: `tests/test_status_calculator.py`、`tests/test_project_archive_service.py`、`tests/test_project_events_service.py`

- [ ] **Step 1: `lib/status_calculator.py`**

找 `clues_count`，改为分别计算 `scenes_count` + `props_count`（两个独立字段注入到 status）。项目完成度算法里 `clues_total` 分成两部分，权重等分或合并按 `scenes_count + props_count` 当总量。

```python
# before (示意)
status["clues_count"] = len(project.get("clues", {}))
# after
status["scenes_count"] = len(project.get("scenes", {}))
status["props_count"] = len(project.get("props", {}))
```

progress 计算的 `with_sheet / total` 分母改为 `scenes_with_sheet + props_with_sheet / scenes_total + props_total`；不再按 importance 过滤。

- [ ] **Step 2: `server/services/project_archive.py`**

`export_project_zip()` 中打包目录列表：去掉 `"clues"`，加入 `"scenes"`、`"props"`。版本目录 `versions/clues` 同样换成 `versions/scenes` / `versions/props`。

- [ ] **Step 3: `server/services/project_events.py`**

所有字符串事件 name `"clue"` / `"clues"` 改为按具体类型。原函数 `emit_clue_changed(project, name, ...)` 拆为 `emit_scene_changed` + `emit_prop_changed`。前端对应 hook 在 Task 27 改。

事件数据 payload 中如果有 `importance` 字段一并删除。

- [ ] **Step 4: `server/services/cost_estimation.py`**

费用估算按场景数 + 道具数分别累加；去掉 "只算 major clues" 的筛选条件（现在不存在 importance）。

- [ ] **Step 5: `server/routers/versions.py`**

资源类型白名单从 `["characters", "clues", ...]` 改为 `["characters", "scenes", "props", ...]`。所有 `resource_type == "clues"` 相关分支同步拆两份。

- [ ] **Step 6: 跑测试 + 修复**

```bash
uv run python -m pytest tests/test_status_calculator.py tests/test_project_archive_service.py tests/test_project_events_service.py tests/test_versions_router.py -v
```

按失败信息把 fixture / assertion 从 clue 迁到 scene/prop。

- [ ] **Step 7: Commit**

```bash
uv run ruff format lib/status_calculator.py server/services/*.py server/routers/versions.py tests/
git add lib/status_calculator.py server/services/project_archive.py server/services/project_events.py \
       server/services/cost_estimation.py server/routers/versions.py tests/
git commit -m "refactor(services): status/archive/events/cost/versions 全线去 clue"
```

---

### Task 13: 新增 `server/routers/scenes.py`

**Files:**
- Create: `server/routers/scenes.py`
- Test: `tests/test_scenes_router.py`

- [ ] **Step 1: 写测试 `tests/test_scenes_router.py`**

```python
"""scenes 路由 CRUD。"""
import pytest
from httpx import AsyncClient, ASGITransport

from server.app import app


@pytest.mark.asyncio
async def test_add_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import get_project_manager, reset_project_manager
    reset_project_manager()
    pm = get_project_manager()
    pm.create_project("demo", "Demo")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/scenes/demo/add",
            json={"name": "庙宇", "description": "阴森"},
        )
        assert r.status_code == 200
        assert r.json()["scene"]["description"] == "阴森"

        # 重复应 409
        r2 = await c.post(
            "/api/v1/scenes/demo/add",
            json={"name": "庙宇", "description": ""},
        )
        assert r2.status_code == 409


@pytest.mark.asyncio
async def test_update_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import get_project_manager, reset_project_manager
    reset_project_manager()
    pm = get_project_manager()
    pm.create_project("demo", "Demo")
    pm.add_scene("demo", "A", "old")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.patch("/api/v1/scenes/demo/A", json={"description": "new"})
        assert r.status_code == 200
        assert r.json()["scene"]["description"] == "new"


@pytest.mark.asyncio
async def test_delete_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import get_project_manager, reset_project_manager
    reset_project_manager()
    pm = get_project_manager()
    pm.create_project("demo", "Demo")
    pm.add_scene("demo", "A", "")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.delete("/api/v1/scenes/demo/A")
        assert r.status_code == 204
```

- [ ] **Step 2: 运行测试（预期失败）**

```bash
uv run python -m pytest tests/test_scenes_router.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 `server/routers/scenes.py`**

```python
"""Scenes CRUD — 取代旧 clues router 的场景部分。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from lib.i18n.deps import Translator, get_translator
from lib.project_manager import get_project_manager
from fastapi import Depends

router = APIRouter(prefix="/api/v1/scenes", tags=["scenes"])


class AddSceneRequest(BaseModel):
    name: str
    description: str = ""


class UpdateSceneRequest(BaseModel):
    description: str | None = None
    scene_sheet: str | None = None


@router.post("/{project_name}/add")
async def add_scene(project_name: str, req: AddSceneRequest, _t: Translator = Depends(get_translator)):
    pm = get_project_manager()
    ok = pm.add_scene(project_name, req.name, req.description)
    if not ok:
        raise HTTPException(status_code=409, detail=_t("scene_already_exists", name=req.name))
    data = pm.load_project(project_name)
    return {"scene": data["scenes"][req.name]}


@router.patch("/{project_name}/{name}")
async def update_scene(project_name: str, name: str, req: UpdateSceneRequest, _t: Translator = Depends(get_translator)):
    pm = get_project_manager()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        pm.update_scene(project_name, name, updates)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("scene_not_found", name=name))
    data = pm.load_project(project_name)
    return {"scene": data["scenes"][name]}


@router.delete("/{project_name}/{name}", status_code=204)
async def delete_scene(project_name: str, name: str):
    pm = get_project_manager()
    pm.delete_scene(project_name, name)
    return
```

- [ ] **Step 4: 注册路由**

在 `server/app.py` 路由注册段：

```python
from server.routers import scenes as scenes_router
app.include_router(scenes_router.router)
```

- [ ] **Step 5: 在 i18n 里加 `scene_already_exists` / `scene_not_found` key**

`lib/i18n/zh/errors.py` 和 `lib/i18n/en/errors.py` 追加：
```python
"scene_already_exists": "场景「{name}」已存在",
"scene_not_found": "场景「{name}」不存在",
```
（英文对应翻译）

- [ ] **Step 6: 运行测试**

```bash
uv run python -m pytest tests/test_scenes_router.py -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
uv run ruff format server/routers/scenes.py tests/test_scenes_router.py lib/i18n/
git add server/routers/scenes.py tests/test_scenes_router.py server/app.py lib/i18n/
git commit -m "feat(api): scenes 路由 CRUD"
```

---

### Task 14: 新增 `server/routers/props.py`

**Files:**
- Create: `server/routers/props.py`
- Test: `tests/test_props_router.py`

同构于 Task 13，差异：
- 路径 `/api/v1/props/*`
- 字段 `prop_sheet`、方法 `add_prop` / `update_prop` / `delete_prop`
- i18n key `prop_already_exists` / `prop_not_found`

- [ ] **Step 1-5: 重复 Task 13 的步骤，把 scene→prop、Scene→Prop 全局替换**

代码模板（props.py 只列差异）：

```python
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from lib.i18n.deps import Translator, get_translator
from lib.project_manager import get_project_manager

router = APIRouter(prefix="/api/v1/props", tags=["props"])


class AddPropRequest(BaseModel):
    name: str
    description: str = ""


class UpdatePropRequest(BaseModel):
    description: str | None = None
    prop_sheet: str | None = None


@router.post("/{project_name}/add")
async def add_prop(project_name: str, req: AddPropRequest, _t: Translator = Depends(get_translator)):
    pm = get_project_manager()
    ok = pm.add_prop(project_name, req.name, req.description)
    if not ok:
        raise HTTPException(status_code=409, detail=_t("prop_already_exists", name=req.name))
    data = pm.load_project(project_name)
    return {"prop": data["props"][req.name]}


@router.patch("/{project_name}/{name}")
async def update_prop(project_name: str, name: str, req: UpdatePropRequest, _t: Translator = Depends(get_translator)):
    pm = get_project_manager()
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        pm.update_prop(project_name, name, updates)
    except KeyError:
        raise HTTPException(status_code=404, detail=_t("prop_not_found", name=name))
    data = pm.load_project(project_name)
    return {"prop": data["props"][name]}


@router.delete("/{project_name}/{name}", status_code=204)
async def delete_prop(project_name: str, name: str):
    get_project_manager().delete_prop(project_name, name)
    return
```

测试结构一致：`test_add_prop` / `test_update_prop` / `test_delete_prop`。

- [ ] **Step 6: 注册路由到 `server/app.py` + i18n key + 跑测试 + format + commit**

```bash
uv run python -m pytest tests/test_props_router.py -v
git add server/routers/props.py tests/test_props_router.py server/app.py lib/i18n/
git commit -m "feat(api): props 路由 CRUD"
```

---

### Task 15: 删除旧 `clues_router` + `generate.py` 拆分 + 后端测试重写

**Files:**
- Delete: `server/routers/clues.py`、`tests/test_clues_router.py`
- Modify: `server/routers/generate.py`
- Test: `tests/test_generate_router.py`（改）

- [ ] **Step 1: 删文件**

```bash
git rm server/routers/clues.py tests/test_clues_router.py
```

并从 `server/app.py` 去掉 `clues_router` 的 include。

- [ ] **Step 2: 拆 generate endpoints**

找到 `server/routers/generate.py` 中 `POST /generate/clue` 对应 handler（名字可能为 `generate_clue`）。复制两份重命名为 `generate_scene` / `generate_prop`，分别：
- 路径 `/api/v1/generate/scene` / `/generate/prop`
- task_type 分别为 `"scene"` / `"prop"`
- `project.scenes` / `project.props` 里查资源是否存在（否则 404）
- 调用 `enqueue_and_wait(task_type="scene", ...)` 或 `"prop"`

删除原 `generate_clue` handler。

- [ ] **Step 3: 改测试**

`tests/test_generate_router.py` 中 `test_generate_clue` 重写为 `test_generate_scene` + `test_generate_prop`，分别断言对应 task 入队 + 返回成功。

- [ ] **Step 4: 运行后端全量测试**

```bash
uv run python -m pytest -x -v 2>&1 | tail -30
```

修复剩余失败（一般是 clue fixture 漏改的地方）。

- [ ] **Step 5: Commit**

```bash
uv run ruff format server/routers/generate.py tests/test_generate_router.py
git add -A
git commit -m "refactor(api): 删 clues router; generate 拆 scene/prop endpoints"
```

---

## Stage 2 · 后端 Asset 库

### Task 16: Asset i18n + 全局资产目录常量

**Files:**
- Create: `lib/i18n/zh/assets.py`、`lib/i18n/en/assets.py`
- Modify: `lib/i18n/core.py`（注册新命名空间）
- Modify: `lib/project_manager.py`（暴露 `get_global_assets_root()` helper）

- [ ] **Step 1: 写 i18n 文案**

`lib/i18n/zh/assets.py`：

```python
TRANSLATIONS = {
    "asset_not_found": "资产「{name}」不存在",
    "asset_already_exists": "同类型下已有同名资产「{name}」",
    "asset_invalid_type": "资产类型必须为 character / scene / prop",
    "asset_upload_too_large": "图片大小超过限制（5MB）",
    "asset_unsupported_format": "仅支持 png/jpg/jpeg/webp",
    "asset_source_resource_not_found": "项目「{project}」中不存在{kind}「{name}」",
    "asset_target_project_not_found": "目标项目「{project}」不存在",
}
```

`lib/i18n/en/assets.py`：对应英文。

- [ ] **Step 2: 注册命名空间**

在 `lib/i18n/core.py`（或 `__init__.py`）的 namespace 注册表里追加 `"assets"`。

- [ ] **Step 3: `ProjectManager.get_global_assets_root()`**

```python
def get_global_assets_root(self) -> Path:
    root = self.projects_root / "_global_assets"
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("character", "scene", "prop"):
        (root / sub).mkdir(exist_ok=True)
    return root
```

确保 `list_projects()` 跳过 `_global_assets`（现有逻辑可能已过滤下划线前缀，验证一下）。

- [ ] **Step 4: Commit**

```bash
git add lib/i18n/ lib/project_manager.py
git commit -m "feat(i18n): assets namespace + 全局资产目录"
```

---

### Task 17: assets 路由基础 CRUD

**Files:**
- Create: `server/routers/assets.py`
- Test: `tests/test_assets_router.py`

- [ ] **Step 1: 写测试 `tests/test_assets_router.py`**

```python
"""assets 路由基础 CRUD。"""
import io
import pytest
from httpx import AsyncClient, ASGITransport

from server.app import app


@pytest.fixture(autouse=True)
async def _clean_assets_table():
    from lib.db.engine import AsyncSessionLocal
    from lib.db.models.asset import Asset
    from sqlalchemy import delete
    async with AsyncSessionLocal() as s:
        await s.execute(delete(Asset))
        await s.commit()


@pytest.mark.asyncio
async def test_create_and_list(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/assets", data={
            "type": "character", "name": "王小明", "description": "白衣少年",
        })
        assert r.status_code == 200
        asset_id = r.json()["asset"]["id"]
        assert asset_id

        r2 = await c.get("/api/v1/assets?type=character")
        assert r2.status_code == 200
        assert len(r2.json()["items"]) == 1
        assert r2.json()["items"][0]["id"] == asset_id


@pytest.mark.asyncio
async def test_duplicate_type_name_returns_409(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/api/v1/assets", data={"type": "prop", "name": "玉佩"})
        r = await c.post("/api/v1/assets", data={"type": "prop", "name": "玉佩"})
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_patch_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/assets", data={"type": "scene", "name": "A"})
        aid = r.json()["asset"]["id"]

        r2 = await c.patch(f"/api/v1/assets/{aid}", json={"description": "new"})
        assert r2.status_code == 200
        assert r2.json()["asset"]["description"] == "new"

        r3 = await c.delete(f"/api/v1/assets/{aid}")
        assert r3.status_code == 204

        r4 = await c.get(f"/api/v1/assets/{aid}")
        assert r4.status_code == 404
```

- [ ] **Step 2: 运行测试（预期失败）**

```bash
uv run python -m pytest tests/test_assets_router.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 `server/routers/assets.py`（基础 CRUD 部分）**

```python
"""assets 全局资产库路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from lib.db.engine import AsyncSessionLocal
from lib.db.repositories.asset_repo import AssetRepository
from lib.i18n.deps import Translator, get_translator

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

VALID_TYPES = {"character", "scene", "prop"}


def _serialize(asset) -> dict:
    return {
        "id": asset.id,
        "type": asset.type,
        "name": asset.name,
        "description": asset.description,
        "voice_style": asset.voice_style,
        "image_path": asset.image_path,
        "source_project": asset.source_project,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


@router.get("")
async def list_assets(
    type: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    async with AsyncSessionLocal() as s:
        items = await AssetRepository(s).list(type=type, q=q, limit=limit, offset=offset)
        return {"items": [_serialize(a) for a in items]}


@router.get("/{asset_id}")
async def get_asset(asset_id: str, _t: Translator = Depends(get_translator)):
    async with AsyncSessionLocal() as s:
        a = await AssetRepository(s).get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        return {"asset": _serialize(a)}


@router.post("")
async def create_asset(
    type: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    voice_style: str = Form(""),
    image: UploadFile | None = File(None),
    _t: Translator = Depends(get_translator),
):
    if type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_type"))

    image_path: str | None = None
    if image is not None:
        image_path = await _save_upload(image, type, _t)

    async with AsyncSessionLocal() as s:
        repo = AssetRepository(s)
        if await repo.exists(type, name):
            raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=name))
        try:
            a = await repo.create(
                type=type, name=name, description=description,
                voice_style=voice_style, image_path=image_path, source_project=None,
            )
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=name))
    return {"asset": _serialize(a)}


class UpdateAssetRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    voice_style: str | None = None


@router.patch("/{asset_id}")
async def update_asset(asset_id: str, req: UpdateAssetRequest, _t: Translator = Depends(get_translator)):
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    async with AsyncSessionLocal() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        if "name" in patch and patch["name"] != a.name:
            if await repo.exists(a.type, patch["name"]):
                raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=patch["name"]))
        try:
            a = await repo.update(asset_id, **patch)
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(status_code=409, detail=_t("asset_already_exists", name=patch.get("name", "")))
    return {"asset": _serialize(a)}


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(asset_id: str):
    async with AsyncSessionLocal() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if a:
            # 删图片
            if a.image_path:
                _delete_global_asset_file(a.image_path)
            await repo.delete(asset_id)
            await s.commit()
    return


# ---- helpers ----
import uuid
from pathlib import Path

from lib.project_manager import get_project_manager

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


async def _save_upload(file: UploadFile, type: str, _t) -> str:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=415, detail=_t("asset_unsupported_format"))

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_t("asset_upload_too_large"))

    pm = get_project_manager()
    root = pm.get_global_assets_root() / type
    uid = uuid.uuid4().hex
    target = root / f"{uid}{ext}"
    target.write_bytes(data)
    # 返回相对路径（相对 projects/）
    return f"_global_assets/{type}/{uid}{ext}"


def _delete_global_asset_file(rel_path: str) -> None:
    pm = get_project_manager()
    p = pm.projects_root / rel_path
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
```

注册到 `server/app.py`：

```python
from server.routers import assets as assets_router
app.include_router(assets_router.router)
```

- [ ] **Step 4: 运行测试**

```bash
uv run python -m pytest tests/test_assets_router.py -v
```
Expected: PASS（3 个基础 case）

- [ ] **Step 5: Commit**

```bash
uv run ruff format server/routers/assets.py tests/test_assets_router.py
git add server/routers/assets.py tests/test_assets_router.py server/app.py
git commit -m "feat(api): assets 路由基础 CRUD + 上传"
```

---

### Task 18: assets 图片替换 + from-project 入库

**Files:**
- Modify: `server/routers/assets.py`
- Test: `tests/test_assets_router.py`（追加）

- [ ] **Step 1: 追加测试**

```python
@pytest.mark.asyncio
async def test_replace_image(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/assets", data={"type": "scene", "name": "A"})
        aid = r.json()["asset"]["id"]

        img = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
        r2 = await c.post(
            f"/api/v1/assets/{aid}/image",
            files={"image": ("pic.png", img, "image/png")},
        )
        assert r2.status_code == 200
        assert r2.json()["asset"]["image_path"] is not None


@pytest.mark.asyncio
async def test_from_project_copies_image(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import get_project_manager, reset_project_manager
    reset_project_manager()
    pm = get_project_manager()
    pm.create_project("demo", "Demo")
    pm.add_character("demo", "王", "d", "")
    # 写一个 fake character_sheet
    (pm.projects_root / "demo" / "characters" / "王.png").write_bytes(b"img")
    pm.update_project("demo", lambda d: d["characters"]["王"].update({"character_sheet": "characters/王.png"}))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/v1/assets/from-project", json={
            "project_name": "demo",
            "resource_type": "character",
            "resource_id": "王",
        })
        assert r.status_code == 200
        ip = r.json()["asset"]["image_path"]
        assert ip and ip.startswith("_global_assets/character/")

        # 真实文件落盘
        assert (pm.projects_root / ip).read_bytes() == b"img"


@pytest.mark.asyncio
async def test_from_project_conflict_409(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import get_project_manager, reset_project_manager
    reset_project_manager()
    pm = get_project_manager()
    pm.create_project("demo", "Demo")
    pm.add_character("demo", "王", "d", "")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/api/v1/assets/from-project", json={
            "project_name": "demo",
            "resource_type": "character",
            "resource_id": "王",
        })
        assert r1.status_code == 200
        r2 = await c.post("/api/v1/assets/from-project", json={
            "project_name": "demo",
            "resource_type": "character",
            "resource_id": "王",
        })
        assert r2.status_code == 409
        # 允许通过 overwrite=True 覆盖
        r3 = await c.post("/api/v1/assets/from-project", json={
            "project_name": "demo",
            "resource_type": "character",
            "resource_id": "王",
            "overwrite": True,
        })
        assert r3.status_code == 200
```

- [ ] **Step 2: 运行（预期失败）**

```bash
uv run python -m pytest tests/test_assets_router.py -v
```

- [ ] **Step 3: 实现两个新接口**

在 `server/routers/assets.py` 追加：

```python
@router.post("/{asset_id}/image")
async def replace_image(asset_id: str, image: UploadFile = File(...), _t: Translator = Depends(get_translator)):
    async with AsyncSessionLocal() as s:
        repo = AssetRepository(s)
        a = await repo.get_by_id(asset_id)
        if not a:
            raise HTTPException(status_code=404, detail=_t("asset_not_found", name=asset_id))
        # 删旧图
        if a.image_path:
            _delete_global_asset_file(a.image_path)
        new_path = await _save_upload(image, a.type, _t)
        a = await repo.update(asset_id, image_path=new_path)
        await s.commit()
    return {"asset": _serialize(a)}


class FromProjectRequest(BaseModel):
    project_name: str
    resource_type: str  # 'character' | 'scene' | 'prop'
    resource_id: str
    override_name: str | None = None
    overwrite: bool = False


@router.post("/from-project")
async def from_project(req: FromProjectRequest, _t: Translator = Depends(get_translator)):
    if req.resource_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail=_t("asset_invalid_type"))

    pm = get_project_manager()
    try:
        project = pm.load_project(req.project_name)
    except Exception:
        raise HTTPException(status_code=404, detail=_t("asset_target_project_not_found", project=req.project_name))

    bucket_key = {"character": "characters", "scene": "scenes", "prop": "props"}[req.resource_type]
    sheet_key = {"character": "character_sheet", "scene": "scene_sheet", "prop": "prop_sheet"}[req.resource_type]

    resource = (project.get(bucket_key) or {}).get(req.resource_id)
    if not resource:
        raise HTTPException(
            status_code=404,
            detail=_t("asset_source_resource_not_found", project=req.project_name, kind=req.resource_type, name=req.resource_id),
        )

    name = req.override_name or req.resource_id
    description = resource.get("description", "")
    voice_style = resource.get("voice_style", "") if req.resource_type == "character" else ""

    # 处理图片：从项目目录拷到全局
    image_path: str | None = None
    src_sheet = resource.get(sheet_key)
    if src_sheet:
        src_abs = pm.projects_root / req.project_name / src_sheet
        if src_abs.exists():
            suffix = src_abs.suffix or ".png"
            uid = uuid.uuid4().hex
            target_rel = f"_global_assets/{req.resource_type}/{uid}{suffix}"
            target_abs = pm.projects_root / target_rel
            target_abs.parent.mkdir(parents=True, exist_ok=True)
            target_abs.write_bytes(src_abs.read_bytes())
            image_path = target_rel

    async with AsyncSessionLocal() as s:
        repo = AssetRepository(s)
        existing = await repo.get_by_type_name(req.resource_type, name)
        if existing and not req.overwrite:
            raise HTTPException(
                status_code=409,
                detail={"message": _t("asset_already_exists", name=name), "existing": _serialize(existing)},
            )
        if existing and req.overwrite:
            # 删旧图 → 更新记录
            if existing.image_path and existing.image_path != image_path:
                _delete_global_asset_file(existing.image_path)
            a = await repo.update(
                existing.id,
                description=description, voice_style=voice_style,
                image_path=image_path, source_project=req.project_name,
            )
        else:
            a = await repo.create(
                type=req.resource_type, name=name, description=description,
                voice_style=voice_style, image_path=image_path, source_project=req.project_name,
            )
        await s.commit()
    return {"asset": _serialize(a)}
```

- [ ] **Step 4: 运行测试**

```bash
uv run python -m pytest tests/test_assets_router.py -v
```
Expected: PASS（含新 3 个 case）

- [ ] **Step 5: Commit**

```bash
uv run ruff format server/routers/assets.py
git add server/routers/assets.py tests/test_assets_router.py
git commit -m "feat(api): assets 图片替换 + from-project 入库 + 冲突处理"
```

---

### Task 19: apply-to-project 批量接口

**Files:**
- Modify: `server/routers/assets.py`
- Test: `tests/test_assets_router.py`（追加）

- [ ] **Step 1: 追加测试**

```python
@pytest.mark.asyncio
async def test_apply_to_project_success_and_skip_rename_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import get_project_manager, reset_project_manager
    reset_project_manager()
    pm = get_project_manager()
    pm.create_project("target", "Target")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # 先在库里准备两个 scene 资产
        for n in ("A", "B"):
            await c.post("/api/v1/assets", data={"type": "scene", "name": n})
        ids = [a["id"] for a in (await c.get("/api/v1/assets?type=scene")).json()["items"]]

        r = await c.post("/api/v1/assets/apply-to-project", json={
            "asset_ids": ids,
            "target_project": "target",
            "conflict_policy": "skip",
        })
        assert r.status_code == 200
        body = r.json()
        assert len(body["succeeded"]) == 2
        data = pm.load_project("target")
        assert set(data["scenes"].keys()) == {"A", "B"}

        # 第二次相同 ids + skip → 全部 skip
        r2 = await c.post("/api/v1/assets/apply-to-project", json={
            "asset_ids": ids,
            "target_project": "target",
            "conflict_policy": "skip",
        })
        body2 = r2.json()
        assert len(body2["succeeded"]) == 0
        assert len(body2["skipped"]) == 2
```

- [ ] **Step 2: 运行（预期失败）**

- [ ] **Step 3: 实现 apply-to-project**

追加到 `server/routers/assets.py`：

```python
class ApplyToProjectRequest(BaseModel):
    asset_ids: list[str]
    target_project: str
    conflict_policy: str = "skip"  # 'skip' | 'overwrite' | 'rename'


@router.post("/apply-to-project")
async def apply_to_project(req: ApplyToProjectRequest, _t: Translator = Depends(get_translator)):
    if req.conflict_policy not in {"skip", "overwrite", "rename"}:
        raise HTTPException(status_code=400, detail="invalid conflict_policy")

    pm = get_project_manager()
    try:
        project = pm.load_project(req.target_project)
    except Exception:
        raise HTTPException(status_code=404, detail=_t("asset_target_project_not_found", project=req.target_project))

    succeeded: list[dict] = []
    failed: list[dict] = []
    skipped: list[dict] = []

    async with AsyncSessionLocal() as s:
        repo = AssetRepository(s)
        for aid in req.asset_ids:
            a = await repo.get_by_id(aid)
            if not a:
                failed.append({"id": aid, "reason": "not_found"})
                continue

            bucket_key = {"character": "characters", "scene": "scenes", "prop": "props"}[a.type]
            sheet_key = {"character": "character_sheet", "scene": "scene_sheet", "prop": "prop_sheet"}[a.type]

            existing_bucket = project.get(bucket_key) or {}
            desired_name = a.name
            if desired_name in existing_bucket:
                if req.conflict_policy == "skip":
                    skipped.append({"id": aid, "name": desired_name})
                    continue
                elif req.conflict_policy == "rename":
                    i = 2
                    while f"{a.name} ({i})" in existing_bucket:
                        i += 1
                    desired_name = f"{a.name} ({i})"
                # overwrite: 直接复用 name

            # 拷贝图片
            target_sheet: str | None = None
            if a.image_path:
                src_abs = pm.projects_root / a.image_path
                if src_abs.exists():
                    ext = src_abs.suffix or ".png"
                    rel = f"{bucket_key}/{desired_name}{ext}"
                    target_abs = pm.projects_root / req.target_project / rel
                    target_abs.parent.mkdir(parents=True, exist_ok=True)
                    target_abs.write_bytes(src_abs.read_bytes())
                    target_sheet = rel

            # 写入 project.json
            def _mut(data: dict, _n=desired_name, _tk=bucket_key, _sk=sheet_key, _s=target_sheet, _a=a):
                bucket = data.setdefault(_tk, {})
                payload = {"description": _a.description}
                if _a.type == "character":
                    payload["voice_style"] = _a.voice_style
                if _s:
                    payload[_sk] = _s
                bucket[_n] = payload
            pm.update_project(req.target_project, _mut)

            succeeded.append({"id": aid, "name": desired_name})
            # 更新本轮快照
            project = pm.load_project(req.target_project)

    return {"succeeded": succeeded, "skipped": skipped, "failed": failed}
```

- [ ] **Step 4: 运行测试**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff format server/routers/assets.py
git add server/routers/assets.py tests/test_assets_router.py
git commit -m "feat(api): assets apply-to-project 批量 + 冲突策略"
```

---

### Task 20: `/api/v1/global-assets/{type}/{filename}` 静态服务

**Files:**
- Modify: `server/routers/files.py`
- Test: `tests/test_files_router.py`（追加）

- [ ] **Step 1: 写测试**

```python
@pytest.mark.asyncio
async def test_serve_global_asset_image(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from lib.project_manager import get_project_manager, reset_project_manager
    reset_project_manager()
    pm = get_project_manager()
    target = pm.get_global_assets_root() / "character" / "abc.png"
    target.write_bytes(b"img-bytes")

    from httpx import AsyncClient, ASGITransport
    from server.app import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/global-assets/character/abc.png")
        assert r.status_code == 200
        assert r.content == b"img-bytes"


@pytest.mark.asyncio
async def test_global_asset_path_traversal_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_PROJECTS_DIR", str(tmp_path))
    from httpx import AsyncClient, ASGITransport
    from server.app import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/global-assets/character/..%2Fevil.png")
        assert r.status_code in (400, 403, 404)
```

- [ ] **Step 2: 追加路由到 `server/routers/files.py`**

```python
@router.get("/api/v1/global-assets/{asset_type}/{filename}")
async def serve_global_asset(asset_type: str, filename: str):
    if asset_type not in {"character", "scene", "prop"}:
        raise HTTPException(status_code=400, detail="invalid type")
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    pm = get_project_manager()
    path = pm.get_global_assets_root() / asset_type / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404)
    # 路径遍历防护：确保最终 resolved 路径仍在 global_assets 下
    resolved = path.resolve()
    root = pm.get_global_assets_root().resolve()
    if root not in resolved.parents and resolved != root:
        raise HTTPException(status_code=403)
    return FileResponse(str(path))
```

- [ ] **Step 3: 跑测试**

```bash
uv run python -m pytest tests/test_files_router.py -v -k global_asset
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
uv run ruff format server/routers/files.py
git add server/routers/files.py tests/test_files_router.py
git commit -m "feat(api): /global-assets/{type}/{filename} 静态服务"
```

---

## Stage 3 · 前端 Clue 重构

### Task 21: `types/project.ts` + `types/script.ts` 字段迁移

**Files:**
- Modify: `frontend/src/types/project.ts`
- Modify: `frontend/src/types/script.ts`

- [ ] **Step 1: 改 `frontend/src/types/project.ts`**

删除 `Clue` interface，新增：

```typescript
export interface Scene {
  description: string;
  scene_sheet?: string;
}

export interface Prop {
  description: string;
  prop_sheet?: string;
}
```

在 `Project` interface 里：
- 删除 `clues: Record<string, Clue>`
- 新增 `scenes?: Record<string, Scene>` / `props?: Record<string, Prop>`
- 新增 `schema_version?: number`

`ProjectStatus` 里 `clues` 字段（若存在）拆成 `scenes_count` + `props_count`。

- [ ] **Step 2: 改 `frontend/src/types/script.ts`**

`DramaScene` / `NarrationSegment`：
- 删 `clues?: string[]`
- 加 `scenes?: string[]` / `props?: string[]`

`EpisodeScript` root：加 `schema_version?: number`。

- [ ] **Step 3: typecheck**

```bash
cd frontend && pnpm typecheck
```
Expected: 大量错误（调用方未迁移），先记录下哪些文件报错，下几个 task 逐个修复。

- [ ] **Step 4: Commit（先承担失败，后续 task 会修复）**

```bash
cd frontend && pnpm exec prettier --write src/types/project.ts src/types/script.ts
git add frontend/src/types/
git commit -m "refactor(types): Clue 删除 + Scene/Prop 新增（下游待迁移）"
```

---

### Task 22: `stores/projects-store.ts` 字段迁移

**Files:**
- Modify: `frontend/src/stores/projects-store.ts`
- Test: 现有测试通过即可（`store` 本身结构较简单）

- [ ] **Step 1: 修改字段访问**

`projects-store.ts` 的 `CurrentProject` 类型 / `setCurrentProject` 的 action：
- 凡读 `currentProjectData.clues` 的地方改读 `scenes` / `props`（按上下文拆两份）
- `getAssetFingerprint(path)` 逻辑已与路径无关，保持不变；资产 fingerprint 服务于新 `scenes/` `props/` 相对路径

- [ ] **Step 2: typecheck 子集**

```bash
cd frontend && pnpm typecheck 2>&1 | grep projects-store
```
Expected: 该文件无错误；其他引用点还有错（下一个 task 解决）

- [ ] **Step 3: Commit**

```bash
cd frontend && pnpm exec prettier --write src/stores/projects-store.ts
git add frontend/src/stores/projects-store.ts
git commit -m "refactor(store): projects-store 字段迁移 clues→scenes+props"
```

---

### Task 23: `api.ts` — 删 clue 方法，新增 scene/prop 方法

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/api.test.ts`

- [ ] **Step 1: 改测试 `frontend/src/api.test.ts`**

删除：
```
test("addClue calls POST /clues/add")
test("updateClue calls PATCH /clues/{name}")
test("deleteClue ...")
test("generateClue ...")
```

新增对称（以 scene 为例）：

```typescript
describe("addScene", () => {
  it("POSTs /api/v1/scenes/{project}/add", async () => {
    const { fetchMock } = setupFetchMock();
    fetchMock.mockResolvedValueOnce(mockJson({ scene: { description: "" } }));
    await API.addScene("demo", "A", "desc");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/scenes/demo/add"),
      expect.objectContaining({ method: "POST" })
    );
  });
});

describe("updateScene", () => {
  it("PATCHes with partial payload", async () => {
    const { fetchMock } = setupFetchMock();
    fetchMock.mockResolvedValueOnce(mockJson({ scene: { description: "n" } }));
    await API.updateScene("demo", "A", { description: "n" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/scenes/demo/A"),
      expect.objectContaining({ method: "PATCH" })
    );
  });
});

describe("generateScene", () => {
  it("POSTs generate endpoint", async () => {
    const { fetchMock } = setupFetchMock();
    fetchMock.mockResolvedValueOnce(mockJson({ task_id: "x" }));
    await API.generateScene("demo", "A", "prompt");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/generate/scene"),
      expect.anything()
    );
  });
});
```

Prop 测试同构（path `/props`、方法 `addProp` 等）。

- [ ] **Step 2: 运行测试（预期失败）**

```bash
cd frontend && pnpm exec vitest run src/api.test.ts
```
Expected: FAIL

- [ ] **Step 3: 改 `frontend/src/api.ts`**

删除：
```typescript
addClue / updateClue / deleteClue / generateClue
```

新增：

```typescript
async addScene(project: string, name: string, description: string) {
  return this._post<{ scene: Scene }>(`/api/v1/scenes/${encodeURIComponent(project)}/add`, {
    name, description,
  });
}

async updateScene(project: string, name: string, patch: Partial<Scene>) {
  return this._patch<{ scene: Scene }>(
    `/api/v1/scenes/${encodeURIComponent(project)}/${encodeURIComponent(name)}`,
    patch,
  );
}

async deleteScene(project: string, name: string) {
  return this._delete(`/api/v1/scenes/${encodeURIComponent(project)}/${encodeURIComponent(name)}`);
}

async generateScene(project: string, name: string, prompt: string | Record<string, unknown>) {
  return this._post<{ task_id: string }>(`/api/v1/generate/scene`, {
    project_name: project, resource_id: name, prompt,
  });
}
```

Prop 同构。

- [ ] **Step 4: 运行测试**

```bash
cd frontend && pnpm exec vitest run src/api.test.ts
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd frontend && pnpm exec prettier --write src/api.ts src/api.test.ts
git add frontend/src/api.ts frontend/src/api.test.ts
git commit -m "refactor(api): 前端 clue 方法拆为 scene + prop"
```

---

### Task 24: SceneCard + PropCard 组件

**Files:**
- Create: `frontend/src/components/canvas/lorebook/SceneCard.tsx`
- Create: `frontend/src/components/canvas/lorebook/PropCard.tsx`
- Create: `frontend/src/components/canvas/lorebook/SceneCard.test.tsx`
- Create: `frontend/src/components/canvas/lorebook/PropCard.test.tsx`

- [ ] **Step 1: 写 `SceneCard.test.tsx`**

```typescript
import { render, screen, fireEvent } from "@testing-library/react";
import { SceneCard } from "./SceneCard";

const scene = { description: "阴森古朴" };

describe("SceneCard", () => {
  it("renders name and description", () => {
    render(
      <SceneCard
        name="庙宇"
        scene={scene}
        projectName="demo"
        onUpdate={() => Promise.resolve()}
        onGenerate={() => {}}
      />
    );
    expect(screen.getByText("庙宇")).toBeInTheDocument();
    expect(screen.getByDisplayValue("阴森古朴")).toBeInTheDocument();
  });

  it("invokes onGenerate when ⚡ clicked", () => {
    const onGenerate = vi.fn();
    render(
      <SceneCard name="A" scene={scene} projectName="demo"
        onUpdate={() => Promise.resolve()} onGenerate={onGenerate} />
    );
    fireEvent.click(screen.getByRole("button", { name: /生成/ }));
    expect(onGenerate).toHaveBeenCalledWith("A");
  });
});
```

- [ ] **Step 2: 运行测试（预期失败）**

- [ ] **Step 3: 实现 `SceneCard.tsx`**

基于现有 `ClueCard.tsx` 裁剪：
- 删除所有 `importance` 相关渲染（ClueCard 有根据 `clue.importance === "major"` 的分支）
- 把 `clue` / `clue_type` / `clue_sheet` 字段全部替换为 `scene` / `scene_sheet`
- 图片部分无条件渲染（去掉 "major only" 条件）
- 顶部按钮行加占位 `onAddToLibrary?: () => void`（Task 34 AddToLibraryButton 会接入）

`SceneCard` props：

```typescript
interface SceneCardProps {
  name: string;
  scene: Scene;
  projectName: string;
  onUpdate: (name: string, updates: Partial<Scene>) => void;
  onGenerate: (name: string) => void;
  onRestoreVersion?: () => void | Promise<void>;
  onAddToLibrary?: () => void;
  generating?: boolean;
}
```

布局参考 ClueCard：顶部横向按钮行（生成 ⚡、编辑 ✎、📦 可选、版本 🕐），图片区，描述 textarea。

- [ ] **Step 4: 实现 `PropCard.tsx`**

与 SceneCard 同构：把 `scene` / `scene_sheet` → `prop` / `prop_sheet`；props 字段无 voice_style（与 scene 一致）。

- [ ] **Step 5: PropCard.test.tsx 同构**

- [ ] **Step 6: 运行测试**

```bash
cd frontend && pnpm exec vitest run src/components/canvas/lorebook/SceneCard.test.tsx src/components/canvas/lorebook/PropCard.test.tsx
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd frontend && pnpm exec prettier --write src/components/canvas/lorebook/SceneCard.tsx src/components/canvas/lorebook/PropCard.tsx src/components/canvas/lorebook/SceneCard.test.tsx src/components/canvas/lorebook/PropCard.test.tsx
git add frontend/src/components/canvas/lorebook/SceneCard.tsx frontend/src/components/canvas/lorebook/PropCard.tsx frontend/src/components/canvas/lorebook/SceneCard.test.tsx frontend/src/components/canvas/lorebook/PropCard.test.tsx
git commit -m "feat(ui): SceneCard + PropCard 组件（取代 ClueCard）"
```

---

### Task 25: `StudioCanvasRouter.tsx` 路由拆 `/characters` `/scenes` `/props`

**Files:**
- Modify: `frontend/src/components/canvas/StudioCanvasRouter.tsx`
- Modify: `frontend/src/components/canvas/StudioCanvasRouter.test.tsx`

- [ ] **Step 1: 改测试**

`StudioCanvasRouter.test.tsx`：
- 删除 `/clues` 路由测试
- 新增 `/scenes` 渲染 SceneCard 列表、`/props` 渲染 PropCard 列表的断言
- `/characters` 继续渲染 CharacterCard

- [ ] **Step 2: 改实现**

删除现有 `LorebookGallery` 的三路使用，替换为按 location 分三条 Route：

```tsx
<Route path="/characters">
  <CharactersPage
    projectName={currentProjectName}
    characters={currentProjectData?.characters ?? {}}
    onSaveCharacter={handleSaveCharacter}
    onGenerateCharacter={handleGenerateCharacterVoid}
    onAddCharacter={() => setAddingCharacter(true)}
    onRestoreCharacterVersion={handleRestoreAsset}
    generatingCharacterNames={generatingCharacterNames}
  />
</Route>
<Route path="/scenes">
  <ScenesPage
    projectName={currentProjectName}
    scenes={currentProjectData?.scenes ?? {}}
    onUpdateScene={handleUpdateSceneVoid}
    onGenerateScene={handleGenerateSceneVoid}
    onAddScene={() => setAddingScene(true)}
    onRestoreSceneVersion={handleRestoreAsset}
    generatingSceneNames={generatingSceneNames}
  />
</Route>
<Route path="/props">
  <PropsPage
    projectName={currentProjectName}
    props={currentProjectData?.props ?? {}}
    onUpdateProp={handleUpdatePropVoid}
    onGenerateProp={handleGeneratePropVoid}
    onAddProp={() => setAddingProp(true)}
    onRestorePropVersion={handleRestoreAsset}
    generatingPropNames={generatingPropNames}
  />
</Route>
```

新增回调（对称 clue）：`handleAddSceneSubmit` / `handleAddPropSubmit` / `handleUpdateSceneVoid` / `handleGenerateSceneVoid`（和旧 clue 同构，API 调用改 addScene/addProp）。

`CharactersPage` / `ScenesPage` / `PropsPage` 三个组件在 Task 32（GalleryToolbar）时创建；本 task 只需声明它们为占位：

```tsx
// 临时占位（Task 32 填充 GalleryToolbar）
function CharactersPage(props: { /* ... */ }) {
  return <div>chars: {Object.keys(props.characters).length}</div>;
}
```

- [ ] **Step 3: 运行测试**

Expected: `/characters` `/scenes` `/props` 路由 smoke test PASS

- [ ] **Step 4: Commit**

```bash
cd frontend && pnpm exec prettier --write src/components/canvas/StudioCanvasRouter.tsx src/components/canvas/StudioCanvasRouter.test.tsx
git add frontend/src/components/canvas/StudioCanvasRouter.tsx frontend/src/components/canvas/StudioCanvasRouter.test.tsx
git commit -m "refactor(routes): /clues 拆 /scenes + /props 两路由"
```

---

### Task 26: 删除 ClueCard / AddClueForm / AddCharacterForm / LorebookGallery

**Files:**
- Delete: `frontend/src/components/canvas/lorebook/ClueCard.tsx`
- Delete: `frontend/src/components/canvas/lorebook/AddClueForm.tsx`
- Delete: `frontend/src/components/canvas/lorebook/AddCharacterForm.tsx`
- Delete: `frontend/src/components/canvas/lorebook/LorebookGallery.tsx`
- Delete: 对应 `.test.tsx`

- [ ] **Step 1: 确认无引用**

```bash
grep -rn "ClueCard\|AddClueForm\|AddCharacterForm\|LorebookGallery" frontend/src --include="*.ts" --include="*.tsx"
```
Expected: 0 行引用（如还有，这是前一个 task 遗留，修复后再继续）

- [ ] **Step 2: 删除**

```bash
cd frontend && git rm \
  src/components/canvas/lorebook/ClueCard.tsx \
  src/components/canvas/lorebook/AddClueForm.tsx \
  src/components/canvas/lorebook/AddCharacterForm.tsx \
  src/components/canvas/lorebook/LorebookGallery.tsx \
  src/components/canvas/lorebook/AddCharacterForm.test.tsx
```

- [ ] **Step 3: 全前端 lint / typecheck**

```bash
cd frontend && pnpm typecheck && pnpm lint
```
Expected: 可能还有未清引用，逐个修复后重跑。

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(ui): 删除 ClueCard/AddClueForm/AddCharacterForm/LorebookGallery"
```

---

### Task 27: `useProjectEventsSSE` 事件拆分

**Files:**
- Modify: `frontend/src/hooks/useProjectEventsSSE.ts`
- Modify: `frontend/src/hooks/useProjectEventsSSE.test.tsx`

- [ ] **Step 1: 改测试**

`useProjectEventsSSE.test.tsx` 中 `change.resource_type === "clue"` 的 case 拆为 `"scene"` 和 `"prop"` 两条 case（内容同构）。确保事件处理后 `invalidateEntities` 收到 `buildEntityRevisionKey("scene", name)` 或 `"prop"` 而不是 `"clue"`。

- [ ] **Step 2: 改实现**

`useProjectEventsSSE.ts` 中所有 `change.resource_type === "clue"` 分支改为同时处理 `"scene"` 和 `"prop"`：

```typescript
if (change.resource_type === "scene" || change.resource_type === "prop") {
  keys.push(buildEntityRevisionKey(change.resource_type, change.resource_id));
}
```

若函数签名 / utils 中有 `"clue"` 常量或 type union，同步改为 `"scene" | "prop"`（保留 `"character"`）。

- [ ] **Step 3: 跑测试**

```bash
cd frontend && pnpm exec vitest run src/hooks/useProjectEventsSSE.test.tsx
```
Expected: PASS

- [ ] **Step 4: 同时改 `frontend/src/utils/project-changes.ts`**

`buildEntityRevisionKey(type: "character" | "scene" | "prop", name: string)` — type union 里 `"clue"` 替换。

- [ ] **Step 5: Commit**

```bash
cd frontend && pnpm exec prettier --write src/hooks/useProjectEventsSSE.ts src/hooks/useProjectEventsSSE.test.tsx src/utils/project-changes.ts
git add frontend/src/hooks/useProjectEventsSSE.ts frontend/src/hooks/useProjectEventsSSE.test.tsx frontend/src/utils/project-changes.ts
git commit -m "refactor(sse): project-events 事件 clue→scene+prop"
```

---

### Task 28: `AssetSidebar` 拆 Scenes/Props 子节 + 空态可点

**Files:**
- Modify: `frontend/src/components/layout/AssetSidebar.tsx`
- Test: `frontend/src/components/layout/AssetSidebar.test.tsx`（若存在）

- [ ] **Step 1: 改实现**

把 `Clues sub-section`（约 354-387 行）的单一 `clues` 展开改为两个对称块 `Scenes sub-section` + `Props sub-section`：

```tsx
{/* Scenes */}
<div className="mb-1">
  <div className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-gray-600">
    <Landmark className="h-3 w-3" />
    <span>{t("dashboard:scenes")}</span>
  </div>
  {sceneEntries.length === 0 ? (
    <EmptyAction onClick={() => setLocation("/scenes")} text={t("dashboard:no_scenes_hint_clickable")} />
  ) : (
    <ul>{/* 复用原 clue 列表结构，icon 用 Landmark，path /scenes */}</ul>
  )}
</div>

{/* Props */}
<div>
  {/* 同构，icon Package, path /props */}
</div>
```

新增 `EmptyAction` 子组件（替代 `EmptyState`）：用 `<button>` 可点击，跳转到对应路由（即使空态）。

```tsx
function EmptyAction({ text, onClick }: { text: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className="w-full text-left px-8 py-2 text-[11px] italic text-gray-500 hover:text-gray-300 hover:bg-gray-800/40 transition-colors">
      {text} →
    </button>
  );
}
```

Characters 子节同样把 `EmptyState` 替换为 `EmptyAction`。Source Files 子节保持（已有上传入口）。

从 props / currentProjectData 里读 `scenes` + `props` 替代 `clues`。

- [ ] **Step 2: 添加 i18n key**

前端：
- `dashboard:scenes` / `dashboard:props` / `dashboard:no_scenes_hint_clickable` / `dashboard:no_props_hint_clickable` / `dashboard:no_characters_hint_clickable`（新增）

- [ ] **Step 3: 跑测试**

```bash
cd frontend && pnpm exec vitest run src/components/layout/AssetSidebar
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd frontend && pnpm exec prettier --write src/components/layout/AssetSidebar.tsx
git add frontend/src/components/layout/AssetSidebar.tsx frontend/src/i18n/
git commit -m "refactor(sidebar): Clues 拆 Scenes+Props + 空态可点击"
```

---

### Task 29: i18n `dashboard.ts` 清理 clue/importance 文案

**Files:**
- Modify: `frontend/src/i18n/zh/dashboard.ts`
- Modify: `frontend/src/i18n/en/dashboard.ts`

- [ ] **Step 1: 移除 clue / importance 相关 key**

`grep -n "clue\|importance\|minor\|major_option" frontend/src/i18n/zh/dashboard.ts` 列出所有相关 key，逐一处理：
- 与 UI 不再相关的 key 删除
- clue 命名但语义仍存在的改为 scene/prop（如 `clue_added_toast` → 拆 `scene_added_toast` + `prop_added_toast`）
- `importance_label` / `major_option` / `minor_option` / `minor_option` 等全部删除

- [ ] **Step 2: 新增 key**

- `scenes` / `props` / `scene_added_toast` / `prop_added_toast`
- `update_scene_failed` / `update_prop_failed` / `add_scene` / `add_prop`
- `generate_scene_failed` / `generate_prop_failed`
- `scene_task_submitted_toast` / `prop_task_submitted_toast`
- `from_library_button_label`（"从资产库选择"）
- `add_to_library_button_label`（"加入资产库"）

中英文对应。

- [ ] **Step 3: 跑 i18n 一致性测试**

```bash
uv run python -m pytest tests/test_i18n_consistency.py -v
```
Expected: PASS（zh/en keys 一致）

- [ ] **Step 4: Commit**

```bash
cd frontend && pnpm exec prettier --write src/i18n/
git add frontend/src/i18n/
git commit -m "i18n(dashboard): 清理 clue/importance；新增 scene/prop/assets 文案"
```

---

### Task 30: 前端 Clue 相关测试整体修复

**Files:**
- Various 前端测试

- [ ] **Step 1: 执行完整测试**

```bash
cd frontend && pnpm exec vitest run 2>&1 | tail -50
```

- [ ] **Step 2: 修复失败用例**

常见修复模式：
- fixture 里的 `currentProjectData.clues` 改为 `.scenes` + `.props`
- 断言里 `/clues` URL 改 `/scenes` 或 `/props`
- Mock event payload `resource_type: "clue"` → `"scene"` / `"prop"`

- [ ] **Step 3: 跑 typecheck + lint**

```bash
cd frontend && pnpm typecheck && pnpm lint
```
Expected: 全部通过

- [ ] **Step 4: Commit**

```bash
git add -A frontend/src
git commit -m "test(frontend): 全量修复 clue→scene/prop 测试迁移"
```

---

## Stage 4 · 前端 Asset 库 + UI 改造

### Task 31: `types/asset.ts` + `stores/assets-store.ts`

**Files:**
- Create: `frontend/src/types/asset.ts`
- Create: `frontend/src/stores/assets-store.ts`
- Test: `frontend/src/stores/assets-store.test.ts`

- [ ] **Step 1: 写 types**

```typescript
// frontend/src/types/asset.ts
export type AssetType = "character" | "scene" | "prop";

export interface Asset {
  id: string;
  type: AssetType;
  name: string;
  description: string;
  voice_style: string;
  image_path: string | null;
  source_project: string | null;
  updated_at: string | null;
}

export interface AssetCreatePayload {
  type: AssetType;
  name: string;
  description?: string;
  voice_style?: string;
}

export interface AssetUpdatePayload {
  name?: string;
  description?: string;
  voice_style?: string;
}
```

- [ ] **Step 2: 写 store 测试**

```typescript
// frontend/src/stores/assets-store.test.ts
import { describe, it, expect, beforeEach, vi } from "vitest";
import { useAssetsStore } from "./assets-store";
import { API } from "@/api";

vi.mock("@/api");

describe("useAssetsStore", () => {
  beforeEach(() => {
    useAssetsStore.setState({ byType: { character: [], scene: [], prop: [] }, loading: false });
    vi.clearAllMocks();
  });

  it("loads list by type", async () => {
    (API.listAssets as any).mockResolvedValue({ items: [{ id: "1", type: "scene", name: "A", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null }] });
    await useAssetsStore.getState().loadList("scene");
    expect(useAssetsStore.getState().byType.scene).toHaveLength(1);
  });

  it("removes asset locally after delete", async () => {
    useAssetsStore.setState({ byType: { character: [], scene: [{ id: "1", type: "scene", name: "A", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null }], prop: [] }, loading: false });
    (API.deleteAsset as any).mockResolvedValue(undefined);
    await useAssetsStore.getState().deleteAsset("1", "scene");
    expect(useAssetsStore.getState().byType.scene).toHaveLength(0);
  });
});
```

- [ ] **Step 3: 实现 `stores/assets-store.ts`**

```typescript
import { create } from "zustand";
import { API } from "@/api";
import type { Asset, AssetType } from "@/types/asset";

interface AssetsStore {
  byType: Record<AssetType, Asset[]>;
  loading: boolean;
  loadList: (type: AssetType, q?: string) => Promise<void>;
  addAsset: (asset: Asset) => void;
  updateAsset: (asset: Asset) => void;
  deleteAsset: (id: string, type: AssetType) => Promise<void>;
}

export const useAssetsStore = create<AssetsStore>((set) => ({
  byType: { character: [], scene: [], prop: [] },
  loading: false,
  loadList: async (type, q) => {
    set({ loading: true });
    try {
      const res = await API.listAssets({ type, q });
      set((s) => ({ byType: { ...s.byType, [type]: res.items } }));
    } finally {
      set({ loading: false });
    }
  },
  addAsset: (asset) =>
    set((s) => ({
      byType: { ...s.byType, [asset.type]: [asset, ...s.byType[asset.type]] },
    })),
  updateAsset: (asset) =>
    set((s) => ({
      byType: {
        ...s.byType,
        [asset.type]: s.byType[asset.type].map((a) => (a.id === asset.id ? asset : a)),
      },
    })),
  deleteAsset: async (id, type) => {
    await API.deleteAsset(id);
    set((s) => ({
      byType: { ...s.byType, [type]: s.byType[type].filter((a) => a.id !== id) },
    }));
  },
}));
```

- [ ] **Step 4: 跑测试**

```bash
cd frontend && pnpm exec vitest run src/stores/assets-store.test.ts
```
Expected: PASS（API mock 需 Task 32 把方法加上，先跳过 if 失败）

- [ ] **Step 5: Commit**

```bash
cd frontend && pnpm exec prettier --write src/types/asset.ts src/stores/assets-store.ts src/stores/assets-store.test.ts
git add frontend/src/types/asset.ts frontend/src/stores/assets-store.ts frontend/src/stores/assets-store.test.ts
git commit -m "feat(store): assets-store + Asset 类型"
```

---

### Task 32: `api.ts` 新增 assets 方法

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/api.test.ts`

- [ ] **Step 1: 改测试，加入 assets 相关 case**

```typescript
describe("listAssets", () => {
  it("GETs /api/v1/assets with type query", async () => {
    const { fetchMock } = setupFetchMock();
    fetchMock.mockResolvedValueOnce(mockJson({ items: [] }));
    await API.listAssets({ type: "character" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/assets?type=character"),
      expect.anything()
    );
  });
});

describe("createAsset", () => {
  it("POSTs multipart to /api/v1/assets", async () => {
    const { fetchMock } = setupFetchMock();
    fetchMock.mockResolvedValueOnce(mockJson({ asset: { id: "x", type: "scene", name: "A", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null } }));
    const res = await API.createAsset({ type: "scene", name: "A", description: "d" });
    expect(res.asset.id).toBe("x");
  });
});

describe("addAssetFromProject", () => {
  it("POSTs /api/v1/assets/from-project", async () => {
    const { fetchMock } = setupFetchMock();
    fetchMock.mockResolvedValueOnce(mockJson({ asset: { id: "x", type: "character", name: "王", description: "", voice_style: "", image_path: null, source_project: "demo", updated_at: null } }));
    await API.addAssetFromProject({ project_name: "demo", resource_type: "character", resource_id: "王" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/assets/from-project"),
      expect.objectContaining({ method: "POST" })
    );
  });
});

describe("applyAssetsToProject", () => {
  it("POSTs /api/v1/assets/apply-to-project", async () => {
    const { fetchMock } = setupFetchMock();
    fetchMock.mockResolvedValueOnce(mockJson({ succeeded: [], skipped: [], failed: [] }));
    await API.applyAssetsToProject({ asset_ids: ["1"], target_project: "demo", conflict_policy: "skip" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/assets/apply-to-project"),
      expect.anything()
    );
  });
});
```

- [ ] **Step 2: 实现 `api.ts` 新方法**

```typescript
// listAssets
async listAssets(params: { type?: AssetType; q?: string; limit?: number; offset?: number } = {}) {
  const usp = new URLSearchParams();
  if (params.type) usp.set("type", params.type);
  if (params.q) usp.set("q", params.q);
  if (params.limit) usp.set("limit", String(params.limit));
  if (params.offset) usp.set("offset", String(params.offset));
  return this._get<{ items: Asset[] }>(`/api/v1/assets?${usp.toString()}`);
}

async getAsset(id: string) {
  return this._get<{ asset: Asset }>(`/api/v1/assets/${encodeURIComponent(id)}`);
}

async createAsset(payload: AssetCreatePayload & { image?: File }) {
  const form = new FormData();
  form.append("type", payload.type);
  form.append("name", payload.name);
  form.append("description", payload.description ?? "");
  form.append("voice_style", payload.voice_style ?? "");
  if (payload.image) form.append("image", payload.image);
  const res = await fetch(`${this.baseUrl}/api/v1/assets`, {
    method: "POST", body: form, credentials: "include",
  });
  if (!res.ok) throw await this._toError(res);
  return res.json() as Promise<{ asset: Asset }>;
}

async updateAsset(id: string, patch: AssetUpdatePayload) {
  return this._patch<{ asset: Asset }>(`/api/v1/assets/${encodeURIComponent(id)}`, patch);
}

async replaceAssetImage(id: string, image: File) {
  const form = new FormData();
  form.append("image", image);
  const res = await fetch(`${this.baseUrl}/api/v1/assets/${encodeURIComponent(id)}/image`, {
    method: "POST", body: form, credentials: "include",
  });
  if (!res.ok) throw await this._toError(res);
  return res.json() as Promise<{ asset: Asset }>;
}

async deleteAsset(id: string): Promise<void> {
  await this._delete(`/api/v1/assets/${encodeURIComponent(id)}`);
}

async addAssetFromProject(payload: {
  project_name: string;
  resource_type: AssetType;
  resource_id: string;
  override_name?: string;
  overwrite?: boolean;
}) {
  return this._post<{ asset: Asset }>(`/api/v1/assets/from-project`, payload);
}

async applyAssetsToProject(payload: {
  asset_ids: string[];
  target_project: string;
  conflict_policy: "skip" | "overwrite" | "rename";
}) {
  return this._post<{
    succeeded: Array<{ id: string; name: string }>;
    skipped: Array<{ id: string; name: string }>;
    failed: Array<{ id: string; reason: string }>;
  }>(`/api/v1/assets/apply-to-project`, payload);
}

getGlobalAssetUrl(assetId: string, path: string | null, fp?: string | null): string | null {
  if (!path) return null;
  // path 形如 "_global_assets/character/abc.png"
  const parts = path.split("/");
  if (parts.length < 3 || parts[0] !== "_global_assets") return null;
  const type = parts[1];
  const filename = parts.slice(2).join("/");
  const qs = fp ? `?fp=${encodeURIComponent(fp)}` : "";
  return `${this.baseUrl}/api/v1/global-assets/${type}/${filename}${qs}`;
}
```

在 `api.ts` 顶部 import `Asset` / `AssetType` / `AssetCreatePayload` / `AssetUpdatePayload`。

- [ ] **Step 3: 跑测试**

```bash
cd frontend && pnpm exec vitest run src/api.test.ts
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd frontend && pnpm exec prettier --write src/api.ts src/api.test.ts
git add frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(api): 前端 assets 系列方法"
```

---

### Task 33: `AssetFormModal` 统一模态

**Files:**
- Create: `frontend/src/components/assets/AssetFormModal.tsx`
- Test: `frontend/src/components/assets/AssetFormModal.test.tsx`

- [ ] **Step 1: 写测试**

```typescript
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AssetFormModal } from "./AssetFormModal";

describe("AssetFormModal", () => {
  it("create mode renders empty fields and calls onSubmit", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <AssetFormModal type="character" mode="create" scope="library"
        onClose={() => {}} onSubmit={onSubmit} />
    );
    fireEvent.change(screen.getByLabelText(/名称/), { target: { value: "王小明" } });
    fireEvent.click(screen.getByRole("button", { name: /创建/ }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ name: "王小明" })));
  });

  it("edit mode prefills fields", () => {
    render(
      <AssetFormModal
        type="scene" mode="edit" scope="library"
        initialData={{ name: "庙宇", description: "阴森" }}
        onClose={() => {}} onSubmit={vi.fn()}
      />
    );
    expect(screen.getByDisplayValue("庙宇")).toBeInTheDocument();
    expect(screen.getByDisplayValue("阴森")).toBeInTheDocument();
  });

  it("import mode with conflict shows warning", () => {
    render(
      <AssetFormModal
        type="character" mode="import" scope="library"
        initialData={{ name: "王", description: "" }}
        conflictWith={{ id: "1", type: "character", name: "王", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null }}
        onClose={() => {}} onSubmit={vi.fn()}
      />
    );
    expect(screen.getByText(/已有同名/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /覆盖已有/ })).toBeInTheDocument();
  });

  it("shows voice_style field only for character type", () => {
    const { rerender } = render(
      <AssetFormModal type="character" mode="create" scope="library"
        onClose={() => {}} onSubmit={vi.fn()} />
    );
    expect(screen.getByLabelText(/声音风格/)).toBeInTheDocument();

    rerender(
      <AssetFormModal type="scene" mode="create" scope="library"
        onClose={() => {}} onSubmit={vi.fn()} />
    );
    expect(screen.queryByLabelText(/声音风格/)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 实现 `AssetFormModal.tsx`**

```tsx
import { useState, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { Asset, AssetType } from "@/types/asset";

type Mode = "create" | "edit" | "import";
type Scope = "project" | "library";

interface Props {
  type: AssetType;
  mode: Mode;
  scope: Scope;
  initialData?: Partial<Asset>;
  conflictWith?: Asset;
  targetProject?: string;
  onClose: () => void;
  onSubmit: (payload: {
    name: string;
    description: string;
    voice_style: string;
    image?: File | null;
    overwrite?: boolean;
  }) => Promise<void>;
}

export function AssetFormModal({
  type, mode, scope, initialData, conflictWith, onClose, onSubmit,
}: Props) {
  const { t } = useTranslation("assets");
  const [name, setName] = useState(initialData?.name ?? "");
  const [description, setDescription] = useState(initialData?.description ?? "");
  const [voiceStyle, setVoiceStyle] = useState(initialData?.voice_style ?? "");
  const [image, setImage] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const isCharacter = type === "character";
  const title = mode === "create" ? t("create_title", { type: t(`type.${type}`) })
    : mode === "edit" ? t("edit_title", { type: t(`type.${type}`), name: initialData?.name })
    : t("import_title", { name: initialData?.name });

  const submit = async (overwrite = false) => {
    setSubmitting(true);
    try {
      await onSubmit({ name: name.trim(), description, voice_style: voiceStyle, image, overwrite });
      onClose();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div role="dialog" className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[480px] max-w-[96vw] rounded-lg bg-gray-900 border border-gray-700 shadow-2xl">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-800 bg-gray-950/60">
          <h3 className="flex-1 text-sm font-semibold text-white">{title}</h3>
          <button onClick={onClose} aria-label={t("close")} className="text-gray-500 hover:text-gray-300">✕</button>
        </div>

        {conflictWith && (
          <div className="px-4 py-2 bg-amber-950 border-l-2 border-amber-600 text-xs text-amber-200">
            {t("conflict_warning", { name: conflictWith.name })}
          </div>
        )}

        <div className="grid grid-cols-[160px_1fr] gap-4 p-4">
          <div className="flex flex-col gap-2">
            <button type="button" onClick={() => fileRef.current?.click()}
              className="aspect-[3/4] border border-dashed border-gray-700 rounded flex flex-col items-center justify-center text-gray-500 text-xs hover:border-gray-500">
              {image ? image.name : t("upload_image_optional")}
            </button>
            <input ref={fileRef} type="file" accept=".png,.jpg,.jpeg,.webp" className="hidden"
              onChange={(e) => setImage(e.target.files?.[0] ?? null)} />
          </div>
          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-xs text-gray-400">
              {t("field.name")} *
              <input value={name} onChange={(e) => setName(e.target.value)}
                className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-sm text-gray-200" />
            </label>
            <label className="flex flex-col gap-1 text-xs text-gray-400">
              {t("field.description")}
              <textarea value={description} onChange={(e) => setDescription(e.target.value)}
                rows={3}
                className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-sm text-gray-200" />
            </label>
            {isCharacter && (
              <label className="flex flex-col gap-1 text-xs text-gray-400">
                {t("field.voice_style")}
                <input value={voiceStyle} onChange={(e) => setVoiceStyle(e.target.value)}
                  className="bg-gray-950 border border-gray-800 rounded px-2 py-1 text-sm text-gray-200" />
              </label>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-t border-gray-800 bg-gray-950/60">
          <button onClick={onClose} className="px-3 py-1 text-xs rounded bg-gray-800 text-gray-300">
            {t("cancel")}
          </button>
          {mode === "import" && conflictWith && (
            <button onClick={() => submit(true)} disabled={submitting}
              className="px-3 py-1 text-xs rounded bg-gray-700 text-white">
              {t("overwrite_existing")}
            </button>
          )}
          <button onClick={() => submit(false)} disabled={submitting || !name.trim()}
            className="ml-auto px-3 py-1 text-xs rounded bg-indigo-600 text-white disabled:opacity-50">
            {mode === "create" ? t("create") : mode === "edit" ? t("save") : t("confirm_import")}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 添加 i18n (`frontend/src/i18n/{zh,en}/assets.ts`)**

```typescript
// zh
export default {
  "type.character": "人物",
  "type.scene": "场景",
  "type.prop": "道具",
  "create_title": "新增{{type}}",
  "edit_title": "编辑{{type}}：{{name}}",
  "import_title": "加入资产库：{{name}}",
  "conflict_warning": "资产库里已有同名资产「{{name}}」，可改名或选择覆盖",
  "upload_image_optional": "点击上传图片（可选）",
  "field.name": "名称",
  "field.description": "描述",
  "field.voice_style": "声音风格",
  "create": "创建",
  "save": "保存",
  "confirm_import": "📦 确认入库",
  "overwrite_existing": "覆盖已有",
  "cancel": "取消",
  "close": "关闭",
  "library_title": "资产库",
  "add_asset": "+ 新增资产",
  "search_placeholder": "搜索资产...",
  "delete_confirm": "确定删除这个{{type}}？图片也会被删除",
  "from_library": "从资产库选择",
  "picker_title_character": "从资产库选择人物",
  "picker_title_scene": "从资产库选择场景",
  "picker_title_prop": "从资产库选择道具",
  "import_count": "导入 {{count}} 个",
  "already_in_project": "已在项目",
  "no_assets_hint": "暂无资产，点击上方【+ 新增资产】",
};
```

英文对应翻译。`i18n/index.ts` 注册 `assets` namespace。

- [ ] **Step 4: 跑测试**

```bash
cd frontend && pnpm exec vitest run src/components/assets/AssetFormModal.test.tsx
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd frontend && pnpm exec prettier --write src/components/assets/AssetFormModal.tsx src/components/assets/AssetFormModal.test.tsx src/i18n/ src/i18n/index.ts
git add frontend/src/components/assets/AssetFormModal.tsx frontend/src/components/assets/AssetFormModal.test.tsx frontend/src/i18n/zh/assets.ts frontend/src/i18n/en/assets.ts frontend/src/i18n/index.ts
git commit -m "feat(ui): AssetFormModal 统一模态 (create/edit/import 5 场景复用)"
```

---

### Task 34: `AssetCard` + `AssetGrid`

**Files:**
- Create: `frontend/src/components/assets/AssetCard.tsx`
- Create: `frontend/src/components/assets/AssetGrid.tsx`
- Test: `frontend/src/components/assets/AssetCard.test.tsx`

- [ ] **Step 1: 写测试**

```typescript
import { render, screen, fireEvent } from "@testing-library/react";
import { AssetCard } from "./AssetCard";

const asset = {
  id: "1", type: "scene" as const, name: "庙宇", description: "阴森古朴",
  voice_style: "", image_path: null, source_project: "demo", updated_at: null,
};

describe("AssetCard", () => {
  it("shows name + description", () => {
    render(<AssetCard asset={asset} onEdit={() => {}} onDelete={() => {}} />);
    expect(screen.getByText("庙宇")).toBeInTheDocument();
    expect(screen.getByText("阴森古朴")).toBeInTheDocument();
  });

  it("invokes onEdit on ✎", () => {
    const onEdit = vi.fn();
    render(<AssetCard asset={asset} onEdit={onEdit} onDelete={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /编辑/ }));
    expect(onEdit).toHaveBeenCalledWith(asset);
  });
});
```

- [ ] **Step 2: 实现 `AssetCard.tsx`**

```tsx
import { useTranslation } from "react-i18next";
import { Edit2, Trash2, User as UserIcon, Landmark, Package } from "lucide-react";
import { API } from "@/api";
import type { Asset } from "@/types/asset";

interface Props {
  asset: Asset;
  onEdit: (asset: Asset) => void;
  onDelete: (asset: Asset) => void;
}

const TYPE_ICON = { character: UserIcon, scene: Landmark, prop: Package };

export function AssetCard({ asset, onEdit, onDelete }: Props) {
  const { t } = useTranslation("assets");
  const Icon = TYPE_ICON[asset.type];
  const imageUrl = API.getGlobalAssetUrl(asset.id, asset.image_path, asset.updated_at);

  return (
    <div className="group bg-gray-900 border border-gray-800 rounded-lg overflow-hidden hover:border-gray-600 transition-colors">
      <div className="aspect-[3/4] bg-gradient-to-br from-gray-800 to-gray-700 flex items-center justify-center">
        {imageUrl ? (
          <img src={imageUrl} alt={asset.name} className="h-full w-full object-cover" />
        ) : (
          <Icon className="h-10 w-10 text-gray-600" />
        )}
      </div>
      <div className="p-3">
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-sm text-white truncate">{asset.name}</div>
            {asset.description && (
              <div className="mt-1 text-xs text-gray-400 line-clamp-2">{asset.description}</div>
            )}
          </div>
          <div className="flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button onClick={() => onEdit(asset)} aria-label={t("edit")}
              className="p-1 text-gray-400 hover:text-white rounded">
              <Edit2 className="h-3.5 w-3.5" />
            </button>
            <button onClick={() => onDelete(asset)} aria-label={t("delete")}
              className="p-1 text-gray-400 hover:text-red-400 rounded">
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 实现 `AssetGrid.tsx`**

```tsx
import { AssetCard } from "./AssetCard";
import type { Asset } from "@/types/asset";

interface Props {
  assets: Asset[];
  onEdit: (a: Asset) => void;
  onDelete: (a: Asset) => void;
}

export function AssetGrid({ assets, onEdit, onDelete }: Props) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
      {assets.map((a) => (
        <AssetCard key={a.id} asset={a} onEdit={onEdit} onDelete={onDelete} />
      ))}
    </div>
  );
}
```

- [ ] **Step 4: 跑测试 + commit**

```bash
cd frontend && pnpm exec vitest run src/components/assets/AssetCard.test.tsx
cd frontend && pnpm exec prettier --write src/components/assets/AssetCard.tsx src/components/assets/AssetGrid.tsx src/components/assets/AssetCard.test.tsx
git add frontend/src/components/assets/
git commit -m "feat(ui): AssetCard + AssetGrid"
```

---

### Task 35: `AssetLibraryPage` + 路由

**Files:**
- Create: `frontend/src/components/pages/AssetLibraryPage.tsx`
- Modify: `frontend/src/router.tsx`

- [ ] **Step 1: 写 AssetLibraryPage**

```tsx
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Search } from "lucide-react";
import { AssetGrid } from "@/components/assets/AssetGrid";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { useAssetsStore } from "@/stores/assets-store";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { Asset, AssetType } from "@/types/asset";

const TABS: AssetType[] = ["character", "scene", "prop"];

export function AssetLibraryPage() {
  const { t } = useTranslation("assets");
  const [activeTab, setActiveTab] = useState<AssetType>("character");
  const [q, setQ] = useState("");
  const [formModal, setFormModal] = useState<{ mode: "create" | "edit"; asset?: Asset } | null>(null);

  const byType = useAssetsStore((s) => s.byType);
  const loadList = useAssetsStore((s) => s.loadList);
  const addAsset = useAssetsStore((s) => s.addAsset);
  const updateAsset = useAssetsStore((s) => s.updateAsset);
  const deleteAssetLocal = useAssetsStore((s) => s.deleteAsset);

  useEffect(() => {
    void loadList(activeTab, q || undefined);
  }, [activeTab, q, loadList]);

  const assets = byType[activeTab];

  const handleSubmit = async (payload: {
    name: string; description: string; voice_style: string; image?: File | null;
  }) => {
    try {
      if (formModal?.mode === "edit" && formModal.asset) {
        const { asset } = await API.updateAsset(formModal.asset.id, {
          name: payload.name, description: payload.description, voice_style: payload.voice_style,
        });
        if (payload.image) {
          const { asset: after } = await API.replaceAssetImage(asset.id, payload.image);
          updateAsset(after);
        } else {
          updateAsset(asset);
        }
      } else {
        const { asset } = await API.createAsset({
          type: activeTab, name: payload.name, description: payload.description,
          voice_style: payload.voice_style, image: payload.image ?? undefined,
        });
        addAsset(asset);
      }
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
    }
  };

  const handleDelete = async (asset: Asset) => {
    if (!confirm(t("delete_confirm", { type: t(`type.${asset.type}`) }))) return;
    try {
      await deleteAssetLocal(asset.id, asset.type);
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
    }
  };

  return (
    <div className="flex flex-col h-full">
      <header className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 bg-gray-900">
        <h2 className="text-sm font-semibold text-white">📦 {t("library_title")}</h2>
        <div className="flex-1 flex items-center gap-2 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded">
          <Search className="h-3.5 w-3.5 text-gray-500" />
          <input type="text" placeholder={t("search_placeholder")}
            value={q} onChange={(e) => setQ(e.target.value)}
            className="flex-1 bg-transparent text-sm text-gray-200 outline-none" />
        </div>
        <button onClick={() => setFormModal({ mode: "create" })}
          className="flex items-center gap-1 px-3 py-1.5 bg-indigo-600 text-white text-xs rounded">
          <Plus className="h-3.5 w-3.5" />
          {t("add_asset")}
        </button>
      </header>

      <nav className="flex border-b border-gray-800 px-4 gap-0">
        {TABS.map((tt) => (
          <button key={tt} type="button"
            onClick={() => setActiveTab(tt)}
            className={`px-4 py-2 text-sm transition-colors ${
              activeTab === tt ? "text-white border-b-2 border-indigo-500" : "text-gray-500 hover:text-gray-300"
            }`}>
            {t(`type.${tt}`)} ({byType[tt].length})
          </button>
        ))}
      </nav>

      <div className="flex-1 overflow-y-auto p-4">
        {assets.length === 0 ? (
          <div className="text-center py-16 text-gray-500 text-sm">{t("no_assets_hint")}</div>
        ) : (
          <AssetGrid assets={assets} onEdit={(a) => setFormModal({ mode: "edit", asset: a })} onDelete={handleDelete} />
        )}
      </div>

      {formModal && (
        <AssetFormModal
          type={formModal.asset?.type ?? activeTab}
          mode={formModal.mode}
          scope="library"
          initialData={formModal.asset}
          onClose={() => setFormModal(null)}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 2: 加路由**

在 `frontend/src/router.tsx`，`/app/assets` 渲染 `<AssetLibraryPage />`。

- [ ] **Step 3: Smoke 测试**

启动 dev server `pnpm dev`，浏览器打开 `/app/assets` 验证：
- Tab 切换
- 空态提示
- 新增模态能点开

- [ ] **Step 4: Commit**

```bash
cd frontend && pnpm exec prettier --write src/components/pages/AssetLibraryPage.tsx src/router.tsx
git add frontend/src/components/pages/AssetLibraryPage.tsx frontend/src/router.tsx
git commit -m "feat(ui): AssetLibraryPage + /app/assets 路由"
```

---

### Task 36: `AssetPickerModal` 挑选对话框

**Files:**
- Create: `frontend/src/components/assets/AssetPickerModal.tsx`
- Test: `frontend/src/components/assets/AssetPickerModal.test.tsx`

- [ ] **Step 1: 写测试**

```typescript
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AssetPickerModal } from "./AssetPickerModal";

vi.mock("@/api");
import { API } from "@/api";

const fixtures = [
  { id: "1", type: "character" as const, name: "王小明", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null },
  { id: "2", type: "character" as const, name: "小师妹", description: "", voice_style: "", image_path: null, source_project: null, updated_at: null },
];

describe("AssetPickerModal", () => {
  beforeEach(() => {
    (API.listAssets as any).mockResolvedValue({ items: fixtures });
  });

  it("multi-selects and calls onImport", async () => {
    const onImport = vi.fn();
    render(
      <AssetPickerModal
        type="character"
        existingNames={new Set()}
        onClose={() => {}}
        onImport={onImport}
      />
    );
    await waitFor(() => screen.getByText("王小明"));
    fireEvent.click(screen.getByText("王小明"));
    fireEvent.click(screen.getByText("小师妹"));
    fireEvent.click(screen.getByRole("button", { name: /导入 2/ }));
    await waitFor(() => expect(onImport).toHaveBeenCalledWith(["1", "2"]));
  });

  it("disables already-in-project assets", async () => {
    render(
      <AssetPickerModal type="character" existingNames={new Set(["王小明"])}
        onClose={() => {}} onImport={vi.fn()} />
    );
    await waitFor(() => screen.getByText("王小明"));
    const card = screen.getByText("王小明").closest("[role='button']") as HTMLElement;
    expect(card).toHaveAttribute("aria-disabled", "true");
  });
});
```

- [ ] **Step 2: 实现**

```tsx
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { Asset, AssetType } from "@/types/asset";

interface Props {
  type: AssetType;
  existingNames: Set<string>;  // 目标项目已有的同类 name
  onClose: () => void;
  onImport: (assetIds: string[]) => void;
}

export function AssetPickerModal({ type, existingNames, onClose, onImport }: Props) {
  const { t } = useTranslation("assets");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    let disposed = false;
    (async () => {
      const res = await API.listAssets({ type, q: q || undefined });
      if (!disposed) setAssets(res.items);
    })();
    return () => { disposed = true; };
  }, [type, q]);

  const toggle = (id: string, disabled: boolean) => {
    if (disabled) return;
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  };

  const titleKey = `picker_title_${type}` as const;

  return (
    <div role="dialog" className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="w-[720px] max-w-[96vw] max-h-[90vh] flex flex-col rounded-lg bg-gray-900 border border-gray-700 shadow-2xl">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-800">
          <h3 className="text-sm font-semibold text-white flex-1">📦 {t(titleKey)}</h3>
          <input type="text" value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={t("search_placeholder")}
            className="px-3 py-1.5 bg-gray-800 border border-gray-700 rounded text-sm text-gray-200 w-48" />
          <button onClick={onClose} aria-label={t("close")} className="text-gray-500 hover:text-gray-300">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 grid grid-cols-4 gap-2">
          {assets.map((a) => {
            const dup = existingNames.has(a.name);
            const sel = selected.has(a.id);
            const url = API.getGlobalAssetUrl(a.id, a.image_path, a.updated_at);
            return (
              <div key={a.id} role="button" aria-disabled={dup}
                onClick={() => toggle(a.id, dup)}
                className={`relative rounded border p-2 cursor-pointer transition-colors ${
                  dup ? "opacity-40 cursor-not-allowed" :
                  sel ? "border-indigo-500 bg-indigo-950" : "border-gray-700 bg-gray-800 hover:border-gray-600"
                }`}>
                <div className="aspect-[3/4] bg-gray-700 rounded flex items-center justify-center text-gray-500 text-xs">
                  {url ? <img src={url} alt={a.name} className="h-full w-full object-cover rounded" /> : "—"}
                </div>
                <div className="mt-1 text-xs font-semibold text-white truncate">{a.name}</div>
                {a.description && <div className="text-[10px] text-gray-400 truncate">{a.description}</div>}
                {dup && (
                  <span className="absolute top-1 right-1 text-[9px] px-1 py-0.5 bg-amber-900 text-amber-200 rounded">
                    {t("already_in_project")}
                  </span>
                )}
              </div>
            );
          })}
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-t border-gray-800">
          <span className="text-xs text-gray-400 flex-1">
            {t("import_count", { count: selected.size })}
          </span>
          <button onClick={onClose} className="px-3 py-1 text-xs rounded bg-gray-800 text-gray-300">
            {t("cancel")}
          </button>
          <button disabled={selected.size === 0}
            onClick={() => onImport(Array.from(selected))}
            className="px-3 py-1 text-xs rounded bg-indigo-600 text-white disabled:opacity-50">
            {t("import_count", { count: selected.size })}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: 跑测试 + commit**

```bash
cd frontend && pnpm exec vitest run src/components/assets/AssetPickerModal.test.tsx
cd frontend && pnpm exec prettier --write src/components/assets/AssetPickerModal.tsx src/components/assets/AssetPickerModal.test.tsx
git add frontend/src/components/assets/AssetPickerModal.tsx frontend/src/components/assets/AssetPickerModal.test.tsx
git commit -m "feat(ui): AssetPickerModal 挑选对话框（多选 + 重名禁用）"
```

---

### Task 37: `GalleryToolbar` + `CharactersPage` / `ScenesPage` / `PropsPage`

**Files:**
- Create: `frontend/src/components/canvas/lorebook/GalleryToolbar.tsx`
- Create: `frontend/src/components/canvas/lorebook/CharactersPage.tsx`
- Create: `frontend/src/components/canvas/lorebook/ScenesPage.tsx`
- Create: `frontend/src/components/canvas/lorebook/PropsPage.tsx`
- Modify: `frontend/src/components/canvas/StudioCanvasRouter.tsx`

- [ ] **Step 1: 写 GalleryToolbar**

```tsx
import { useTranslation } from "react-i18next";
import { Plus, Package, Search } from "lucide-react";

interface Props {
  title: string;
  count: number;
  searchQuery?: string;
  onSearchChange?: (q: string) => void;
  onAdd: () => void;
  onPickFromLibrary: () => void;
}

export function GalleryToolbar({ title, count, searchQuery, onSearchChange, onAdd, onPickFromLibrary }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-800 bg-gray-900/60">
      <h2 className="text-sm font-semibold text-white">{title}</h2>
      <span className="text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">{count}</span>
      {onSearchChange && (
        <div className="flex-1 flex items-center gap-2 px-3 py-1.5 bg-gray-800 border border-gray-700 rounded max-w-[320px]">
          <Search className="h-3.5 w-3.5 text-gray-500" />
          <input type="text" value={searchQuery ?? ""}
            placeholder={t("assets:search_placeholder")}
            onChange={(e) => onSearchChange(e.target.value)}
            className="flex-1 bg-transparent text-xs text-gray-200 outline-none" />
        </div>
      )}
      {!onSearchChange && <div className="flex-1" />}
      <button onClick={onPickFromLibrary}
        className="flex items-center gap-1 px-3 py-1.5 text-xs text-indigo-300 border border-indigo-700 rounded hover:bg-indigo-950">
        <Package className="h-3.5 w-3.5" />
        {t("assets:from_library")}
      </button>
      <button onClick={onAdd}
        className="flex items-center gap-1 px-3 py-1.5 text-xs text-white bg-indigo-600 rounded hover:bg-indigo-500">
        <Plus className="h-3.5 w-3.5" />
        {title}
      </button>
    </div>
  );
}
```

- [ ] **Step 2: 写 ScenesPage（PropsPage / CharactersPage 同构）**

```tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { GalleryToolbar } from "./GalleryToolbar";
import { SceneCard } from "./SceneCard";
import { AssetFormModal } from "@/components/assets/AssetFormModal";
import { AssetPickerModal } from "@/components/assets/AssetPickerModal";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { Scene } from "@/types";

interface Props {
  projectName: string;
  scenes: Record<string, Scene>;
  onUpdateScene: (name: string, updates: Partial<Scene>) => void;
  onGenerateScene: (name: string) => void;
  onAddScene: (name: string, description: string) => Promise<void>;
  onRestoreSceneVersion?: () => Promise<void> | void;
  generatingSceneNames?: Set<string>;
}

export function ScenesPage({ projectName, scenes, onUpdateScene, onGenerateScene, onAddScene, onRestoreSceneVersion, generatingSceneNames }: Props) {
  const { t } = useTranslation(["dashboard", "assets"]);
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState(false);

  const entries = Object.entries(scenes);

  const handleImport = async (ids: string[]) => {
    try {
      await API.applyAssetsToProject({
        asset_ids: ids,
        target_project: projectName,
        conflict_policy: "skip",
      });
      useAppStore.getState().pushToast(t("assets:import_count", { count: ids.length }), "success");
      // 刷新 project
      const res = await API.getProject(projectName);
      // 交由调用方 refreshProject，此处发 store action
      // 最简单：window reload；更好方案是 StudioCanvasRouter 暴露 onReload prop
      window.location.reload();
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
    } finally {
      setPicking(false);
    }
  };

  return (
    <div className="flex flex-col">
      <GalleryToolbar
        title={t("dashboard:scenes")}
        count={entries.length}
        onAdd={() => setAdding(true)}
        onPickFromLibrary={() => setPicking(true)}
      />
      <div className="p-4">
        {entries.length === 0 ? (
          <div className="py-16 text-center text-gray-500 text-sm">
            {t("dashboard:no_scenes_empty_hint")}
          </div>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {entries.map(([name, scene]) => (
              <SceneCard key={name} name={name} scene={scene} projectName={projectName}
                onUpdate={(n, u) => onUpdateScene(n, u)}
                onGenerate={onGenerateScene}
                onRestoreVersion={onRestoreSceneVersion}
                generating={generatingSceneNames?.has(name)}
              />
            ))}
          </div>
        )}
      </div>

      {adding && (
        <AssetFormModal
          type="scene"
          mode="create"
          scope="project"
          targetProject={projectName}
          onClose={() => setAdding(false)}
          onSubmit={async ({ name, description }) => {
            await onAddScene(name, description);
            setAdding(false);
          }}
        />
      )}

      {picking && (
        <AssetPickerModal
          type="scene"
          existingNames={new Set(Object.keys(scenes))}
          onClose={() => setPicking(false)}
          onImport={handleImport}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: CharactersPage / PropsPage 同构**

CharactersPage 多一个 voice_style 字段传到 onAddCharacter；结构一致。

- [ ] **Step 4: 更新 `StudioCanvasRouter.tsx`**

把 Task 25 里的占位 Page 替换为真正的三个页面。删除 `addingCharacter` / `addingClue` 相关本地 state（现在每页自己管理）；保留 `handleAddCharacterSubmit` / `handleAddSceneSubmit` / `handleAddPropSubmit` 作为 callback 传给 Page。

- [ ] **Step 5: 跑测试**

```bash
cd frontend && pnpm exec vitest run src/components/canvas/
```

- [ ] **Step 6: Commit**

```bash
cd frontend && pnpm exec prettier --write src/components/canvas/
git add frontend/src/components/canvas/
git commit -m "feat(ui): GalleryToolbar + CharactersPage/ScenesPage/PropsPage 顶部操作栏"
```

---

### Task 38: `AddToLibraryButton` + CharacterCard/SceneCard/PropCard 集成

**Files:**
- Create: `frontend/src/components/assets/AddToLibraryButton.tsx`
- Modify: `frontend/src/components/canvas/lorebook/CharacterCard.tsx`
- Modify: `frontend/src/components/canvas/lorebook/SceneCard.tsx`
- Modify: `frontend/src/components/canvas/lorebook/PropCard.tsx`

- [ ] **Step 1: 写 AddToLibraryButton**

```tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Package } from "lucide-react";
import { API } from "@/api";
import { AssetFormModal } from "./AssetFormModal";
import { useAppStore } from "@/stores/app-store";
import type { Asset, AssetType } from "@/types/asset";

interface Props {
  resourceType: AssetType;
  resourceId: string;  // 项目内 name
  projectName: string;
  initialDescription: string;
  initialVoiceStyle?: string;
}

export function AddToLibraryButton({ resourceType, resourceId, projectName, initialDescription, initialVoiceStyle = "" }: Props) {
  const { t } = useTranslation("assets");
  const [modal, setModal] = useState<{ conflictWith?: Asset } | null>(null);

  const openPreview = async () => {
    // 先查是否冲突
    try {
      const res = await API.listAssets({ type: resourceType, q: resourceId });
      const exact = res.items.find((a) => a.name === resourceId);
      setModal({ conflictWith: exact });
    } catch {
      setModal({});
    }
  };

  const handleSubmit = async (payload: { name: string; description: string; voice_style: string; overwrite?: boolean }) => {
    try {
      await API.addAssetFromProject({
        project_name: projectName,
        resource_type: resourceType,
        resource_id: resourceId,
        override_name: payload.name !== resourceId ? payload.name : undefined,
        overwrite: payload.overwrite,
      });
      useAppStore.getState().pushToast(t("add_to_library_success", { name: payload.name }), "success");
    } catch (err) {
      useAppStore.getState().pushToast((err as Error).message, "error");
      throw err;
    }
  };

  return (
    <>
      <button type="button" onClick={openPreview}
        aria-label={t("add_to_library")}
        className="inline-flex items-center justify-center h-6 w-6 rounded bg-indigo-950/40 text-indigo-300 hover:bg-indigo-900 hover:text-white transition-colors">
        <Package className="h-3.5 w-3.5" />
      </button>
      {modal && (
        <AssetFormModal
          type={resourceType}
          mode="import"
          scope="library"
          initialData={{
            name: resourceId,
            description: initialDescription,
            voice_style: initialVoiceStyle,
          }}
          conflictWith={modal.conflictWith}
          onClose={() => setModal(null)}
          onSubmit={handleSubmit}
        />
      )}
    </>
  );
}
```

在 assets.ts 添加 `add_to_library` / `add_to_library_success` key。

- [ ] **Step 2: 集成到 CharacterCard / SceneCard / PropCard**

每个卡片的顶部图标按钮行追加一个 `AddToLibraryButton`。示例（SceneCard 片段）：

```tsx
import { AddToLibraryButton } from "@/components/assets/AddToLibraryButton";
// ...
<div className="flex items-center gap-1">
  <GenerateButton onClick={() => onGenerate(name)} generating={generating} />
  <button aria-label={t("edit")}> ... </button>
  <AddToLibraryButton resourceType="scene" resourceId={name} projectName={projectName}
    initialDescription={scene.description} />
  {onRestoreVersion && <VersionTimeMachine ... />}
</div>
```

PropCard 同构；CharacterCard 多传 `initialVoiceStyle={character.voice_style ?? ""}`。

- [ ] **Step 3: 跑测试 + smoke**

```bash
cd frontend && pnpm exec vitest run
```

- [ ] **Step 4: Commit**

```bash
cd frontend && pnpm exec prettier --write src/components/assets/AddToLibraryButton.tsx src/components/canvas/lorebook/
git add frontend/src/components/assets/AddToLibraryButton.tsx frontend/src/components/canvas/lorebook/ frontend/src/i18n/
git commit -m "feat(ui): AddToLibraryButton 挂载到 Character/Scene/Prop 卡片"
```

---

### Task 39: GlobalHeader 📦 入口按钮

**Files:**
- Modify: `frontend/src/components/layout/GlobalHeader.tsx`

- [ ] **Step 1: 在 GlobalHeader 添加按钮**

找到现有图标按钮区（Settings / Usage / Download）。新增：

```tsx
import { Package } from "lucide-react";
// ...
<button type="button" onClick={() => navigate("/app/assets")}
  aria-label={t("assets:library_title")}
  title={t("assets:library_title")}
  className="flex items-center justify-center h-8 w-8 rounded text-gray-400 hover:text-white hover:bg-gray-800 transition-colors">
  <Package className="h-4 w-4" />
</button>
```

`navigate` 从 `wouter` 的 `useLocation` 解构：`const [, navigate] = useLocation();`。

- [ ] **Step 2: 更新 GlobalHeader.test.tsx**

断言按钮存在、点击后 navigate 被调。

- [ ] **Step 3: 跑测试 + commit**

```bash
cd frontend && pnpm exec vitest run src/components/layout/GlobalHeader.test.tsx
cd frontend && pnpm exec prettier --write src/components/layout/GlobalHeader.tsx src/components/layout/GlobalHeader.test.tsx
git add frontend/src/components/layout/GlobalHeader.tsx frontend/src/components/layout/GlobalHeader.test.tsx
git commit -m "feat(header): 资产库 📦 图标入口按钮"
```

---

### Task 40: CharacterCard 表单模态化 + 整体 smoke

**Files:**
- Modify: `frontend/src/components/canvas/lorebook/CharacterCard.tsx`

- [ ] **Step 1: 改造 CharacterCard**

现有 CharacterCard 的 inline 编辑（`isEditing` 状态打开下方编辑面板）保留作为"快速编辑"；新增"完整编辑"通过 AssetFormModal，但本 task 的简化路径是**不动 CharacterCard 的 inline 编辑**，只在顶部按钮行加 📦（Task 38 完成）。

本 task 主要做：
- 确保 CharacterCard 保留当前行为
- CharactersPage 里用 AssetFormModal(mode=create) 取代原 AddCharacterForm

（在 Task 37 CharactersPage 同构实现时已经完成——此 task 是**验证完整性**而不是改造代码。）

- [ ] **Step 2: 手工 Smoke 验收**

`cd frontend && pnpm dev`，访问 `http://localhost:5173/app/projects/<demo>/characters`：

- [ ] 空态：顶部工具栏显示，点【+ 新增角色】弹出模态
- [ ] 添加"王小明" → 模态关闭、卡片出现
- [ ] 卡片顶部按钮行有 📦，点开弹出"加入资产库"预览模态
- [ ] 确认入库 → `/app/assets` 能看到
- [ ] 删除资产 → 列表更新

对 `/scenes` `/props` 重复验证。

- [ ] **Step 3: Commit smoke 记录**

无代码变更则跳过 commit。若 smoke 发现 bug，先修 bug 再提交：

```bash
git commit -m "fix(ui): smoke 过程发现的问题修复"
```

---

## Stage 5 · Agent / Skill / Prompt 改造

### Task 41: `analyze-characters-clues.md` → `analyze-assets.md`

**Files:**
- Move: `agent_runtime_profile/.claude/agents/analyze-characters-clues.md` → `analyze-assets.md`
- Modify: 引用该 agent 的 SKILL.md（`generate-script/` / `manga-workflow/`）

- [ ] **Step 1: 重命名文件**

```bash
git mv agent_runtime_profile/.claude/agents/analyze-characters-clues.md \
       agent_runtime_profile/.claude/agents/analyze-assets.md
```

- [ ] **Step 2: 改写 frontmatter + 输出 schema**

在 `analyze-assets.md` 头部 YAML：

```yaml
---
name: analyze-assets
description: 从剧本中提取角色 / 场景 / 道具三类资产定义，按 type 分别输出 JSON，供 add_assets.py 导入。
---
```

正文：
- 删除所有"线索（clue）"措辞
- 删除 importance 标注要求
- 输出 schema 改为：

```json
{
  "characters": {"名": {"description": "...", "voice_style": "..."}},
  "scenes": {"名": {"description": "..."}},
  "props": {"名": {"description": "..."}}
}
```

把示例 shell 命令里的：

```
python .claude/skills/manage-project/scripts/add_characters_clues.py \
  --characters '{"角色": {...}}' \
  --clues '{"玉佩": {"type": "prop", "importance": "major", "description": "..."}}'
```

改为：

```
python .claude/skills/manage-project/scripts/add_assets.py \
  --characters '{"角色": {"description": "...", "voice_style": "..."}}' \
  --scenes '{"庙宇": {"description": "..."}}' \
  --props '{"玉佩": {"description": "..."}}'
```

- [ ] **Step 3: 全局搜索替换引用**

```bash
grep -rln "analyze-characters-clues" agent_runtime_profile/
```

命中的 SKILL.md 里把 agent 名改为 `analyze-assets`。

- [ ] **Step 4: Commit**

```bash
git add agent_runtime_profile/.claude/agents/analyze-assets.md agent_runtime_profile/.claude/skills/
git commit -m "refactor(agent): analyze-characters-clues → analyze-assets（无 importance/clue 概念）"
```

---

### Task 42: `add_characters_clues.py` → `add_assets.py`

**Files:**
- Move: `agent_runtime_profile/.claude/skills/manage-project/scripts/add_characters_clues.py` → `add_assets.py`
- Modify: `agent_runtime_profile/.claude/skills/manage-project/SKILL.md`
- Modify: 其他 SKILL.md 内命令示例

- [ ] **Step 1: 重命名 + 改写**

```bash
git mv agent_runtime_profile/.claude/skills/manage-project/scripts/add_characters_clues.py \
       agent_runtime_profile/.claude/skills/manage-project/scripts/add_assets.py
```

编辑 `add_assets.py`：
- `argparse` 参数：删除 `--clues`；新增 `--scenes`、`--props`（都接受 JSON 字符串）
- 内部不再接受 `type` / `importance` 字段；读到这些字段时输出 warning 并忽略
- 调用 `ProjectManager.add_scenes_batch` / `add_props_batch`（Task 9 中已实现）

示例 docstring：

```python
"""用法：
    python add_assets.py --project-name demo \
        --characters '{"王小明": {"description": "...", "voice_style": "..."}}' \
        --scenes '{"庙宇": {"description": "..."}}' \
        --props '{"玉佩": {"description": "..."}}'
"""
```

主逻辑：

```python
if args.characters:
    added = pm.add_characters_batch(args.project_name, json.loads(args.characters))
    print(f"characters added: {added}")
if args.scenes:
    added = pm.add_scenes_batch(args.project_name, json.loads(args.scenes))
    print(f"scenes added: {added}")
if args.props:
    added = pm.add_props_batch(args.project_name, json.loads(args.props))
    print(f"props added: {added}")
```

- [ ] **Step 2: 改 `manage-project/SKILL.md`**

搜 `add_characters_clues.py` 全部替换为 `add_assets.py`；更新参数示例；删除对 `type` / `importance` 的引用。

- [ ] **Step 3: 跑脚本 smoke**

```bash
cd projects/<demo-project> && \
python ../../agent_runtime_profile/.claude/skills/manage-project/scripts/add_assets.py \
  --project-name <demo-project> \
  --scenes '{"test_scene": {"description": "smoke test"}}'
```

验证 `project.json.scenes.test_scene` 被写入。随后从 `project.json` 里删除这条测试数据。

- [ ] **Step 4: Commit**

```bash
git add agent_runtime_profile/.claude/skills/manage-project/
git commit -m "refactor(skill): add_characters_clues.py → add_assets.py（--characters/--scenes/--props）"
```

---

### Task 43: `generate-clues` → `generate-assets`（合并 character/scene/prop 三路）

**Files:**
- Move: `agent_runtime_profile/.claude/skills/generate-clues/` → `.claude/skills/generate-assets/`
- 合并：若存在 `.claude/skills/generate-characters/`（本仓目前只有 `generate-clues`，以现状为准）
- Modify: `agent_runtime_profile/.claude/skills/generate-assets/SKILL.md`
- Modify: `scripts/generate_clue.py` → `scripts/generate_asset.py`

- [ ] **Step 1: 重命名目录**

```bash
cd agent_runtime_profile/.claude/skills/
git mv generate-clues generate-assets
git mv generate-assets/scripts/generate_clue.py generate-assets/scripts/generate_asset.py
```

- [ ] **Step 2: 改写 `SKILL.md`**

- `name`: `generate-assets`
- `description`: "统一资产生成 skill：接受 `--type=character|scene|prop`，或不传自动扫所有 pending（缺 sheet）资源并按类型分发。"
- pending 判定规则：
  - character：`character_sheet` 为空
  - scene：`scene_sheet` 为空
  - prop：`prop_sheet` 为空
  - ~~importance==major~~ 已删除
- 并行调度：如果调用方（manga-workflow）给定多个类型，可以分别 dispatch（subagent-driven 模型）

- [ ] **Step 3: 改写 `scripts/generate_asset.py`**

脚本入参：
```
python generate_asset.py --project-name <p> --type <character|scene|prop> --name <n>
```

内部路径：
- type=character → POST `/api/v1/generate/character`（现有）
- type=scene → POST `/api/v1/generate/scene`（Task 15 新增）
- type=prop → POST `/api/v1/generate/prop`（Task 15 新增）

轮询 `/api/v1/tasks/{task_id}` 直到完成，打印结构化日志。

- [ ] **Step 4: 全局搜索替换引用**

```bash
grep -rln "generate-clues\|generate_clue\b" agent_runtime_profile/
```

替换为 `generate-assets` / `generate_asset`。

若现状有 `generate-characters/` skill，内容合并进 `generate-assets/SKILL.md`（以 `type=character` 分支呈现），然后删除目录；若没有则跳过合并。

- [ ] **Step 5: Commit**

```bash
git add agent_runtime_profile/.claude/skills/
git commit -m "refactor(skill): generate-clues → generate-assets（--type 三类统一）"
```

---

### Task 44: `manga-workflow/SKILL.md` 阶段 5/6 合并

**Files:**
- Modify: `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md`

- [ ] **Step 1: 重构阶段列表**

原 SKILL.md（约第 40-80 行工作流阶段表）：

```
阶段 4: 世界观设定（analyze-assets）
阶段 5: 角色设计（generate-assets --type character）
阶段 6: 线索设计（generate-assets --type clue）← 已废弃
阶段 7: 分镜图
阶段 8: 视频
```

改为：

```
阶段 4: 世界观设定（analyze-assets）
阶段 5: 资产设计（三类并行）
        ├─ character（generate-assets --type character）
        ├─ scene（generate-assets --type scene）
        └─ prop（generate-assets --type prop）
阶段 6: 分镜图
阶段 7: 视频
```

触发条件改为："若 project.json 中任一类资产仍有缺 sheet 项（character_sheet / scene_sheet / prop_sheet 缺失），则进入阶段 5。"

并行调度段说明：controller dispatch 三个 subagent（或 task group），分别针对三种 type，收集所有完成事件后再进入阶段 6。

- [ ] **Step 2: 清理所有 "线索" / "clue" / "importance" 措辞**

```bash
grep -n "线索\|clue\|importance" agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md
```

逐行替换：
- "线索" → "场景 / 道具"（按上下文）
- "importance=major" → 删除（pending 判定不再依赖此字段）
- `importance=major 线索缺 clue_sheet` → `scene 缺 scene_sheet 或 prop 缺 prop_sheet`

- [ ] **Step 3: 更新流程图（若是 dot / mermaid 嵌入）**

把阶段 5、6 两个节点合并为单节点 "stage5:资产设计"，内部三路并行子节点。

- [ ] **Step 4: Commit**

```bash
git add agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md
git commit -m "refactor(workflow): 阶段 5/6 合并为「资产设计」三类并行"
```

---

### Task 45: `agent_runtime_profile/CLAUDE.md` 清理 + 整体 smoke + 最终验证

**Files:**
- Modify: `agent_runtime_profile/CLAUDE.md`

- [ ] **Step 1: 搜索并清理残留**

```bash
grep -rn "clue\|clues\|importance\|major\b\|minor\b" agent_runtime_profile/
```

对每处命中：
- 文档 / 说明性文本：重写为 scene / prop 语义
- 变量 / 字段名：若已在 Task 41-44 替换则跳过

- [ ] **Step 2: 修改 project.json 结构描述段**

CLAUDE.md 第 ~135 行附近（"clues：线索完整定义（type、description、importance、clue_sheet）"）：

```
- characters：角色完整定义（description、voice_style、character_sheet）
- scenes：场景完整定义（description、scene_sheet）
- props：道具完整定义（description、prop_sheet）
- schema_version：项目数据格式版本（当前 1）
```

- [ ] **Step 3: 最终整体测试**

```bash
# 后端
uv run python -m pytest -x
uv run ruff check .
uv run ruff format --check .

# 前端
cd frontend && pnpm check   # typecheck + test
cd frontend && pnpm lint
cd frontend && pnpm build   # 生产构建
```

全部 PASS，CI 将绿。

- [ ] **Step 4: 启动一次 dev 服务器，做端到端 smoke**

```bash
# 终端 1
uv run uvicorn server.app:app --reload --port 1241
# 终端 2
cd frontend && pnpm dev
```

打开 `http://localhost:5173`：
- [ ] 项目大厅打开 → GlobalHeader 📦 按钮可见
- [ ] 📦 → `/app/assets` 显示三 Tab + 空态
- [ ] 新增一个 scene 资产（上传图片 + 名称）
- [ ] 打开一个项目 → `/scenes` 页面顶部工具栏有【+ 新增场景】和【📦 从资产库】
- [ ] 【📦 从资产库】→ 弹出挑选模态，能选刚才创建的 scene，导入到项目
- [ ] 导入的 scene 在项目里出现，SceneCard 有 📦 图标
- [ ] 项目里点 📦 → 预览模态弹出，确认入库后 /app/assets 可见
- [ ] 启动时观察日志是否输出"项目迁移"信息（若有 v0 项目）

- [ ] **Step 5: Commit**

```bash
git add agent_runtime_profile/CLAUDE.md
git commit -m "docs(agent): CLAUDE.md 清理 clue/importance 残留"
```

---

## 附录：Spec 覆盖校验

| Spec 要求 | 对应 Task |
|---|---|
| Asset ORM 表 + UniqueConstraint(type,name) | Task 1, 2 |
| AssetRepository 异步 CRUD | Task 3 |
| project.json `schema_version` 字段 | Task 5（写入）, 6（启动读） |
| 自动迁移机制（runner + 错误隔离 + 7 天备份清理） | Task 4, 5, 6 |
| v0→v1 迁移（clues→scenes+props、剧本级联、文件重命名） | Task 5 |
| Clue 拆 scene+prop 独立一级对象 | Task 7-15（后端）, 21-30（前端） |
| 删除 importance | Task 7-11 |
| `/api/v1/assets/*` 路由（CRUD + from-project + apply-to-project + 替换图） | Task 17, 18, 19 |
| `/api/v1/scenes/*` + `/api/v1/props/*` | Task 13, 14 |
| `/api/v1/generate/scene` + `/generate/prop` | Task 15 |
| `/api/v1/global-assets/{type}/{filename}` | Task 20 |
| 图片独立目录 `projects/_global_assets/` | Task 16, 17, 20 |
| 图片快照复制（入库 / 应用） | Task 18, 19 |
| 前端 `/app/assets` 路由 + AssetLibraryPage | Task 35 |
| AssetFormModal 统一 5 场景 | Task 33, 37, 38 |
| AssetPickerModal（锁 type、多选、冲突禁用） | Task 36 |
| 统一 Tab + 网格 + 搜索 + 新增 | Task 35 |
| GalleryToolbar 顶部操作栏 + 空态可交互 | Task 37 |
| 卡片顶部 📦 图标按钮 | Task 38 |
| GlobalHeader 📦 入口 | Task 39 |
| AssetSidebar Clues 拆 Scenes+Props + 空态可点 | Task 28 |
| Character/Scene/Prop 表单模态化 | Task 33, 37, 38 |
| 类型内唯一 + 冲突弹窗 | Task 17, 18, 33 |
| SSE 事件 clue→scene/prop | Task 12, 27 |
| `generate-assets` skill（合并 + --type） | Task 43 |
| `add_assets.py`（拆 --characters/--scenes/--props） | Task 42 |
| `analyze-assets` agent | Task 41 |
| manga-workflow 阶段 5/6 合并（三路并行） | Task 44 |
| CLAUDE.md 清理 | Task 45 |
| 后端测试覆盖 ≥80% | Task 3-20 的单测 + Task 30 前端补齐 |
| i18n 一致性 | Task 29, 33 (assets ns) |

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-15-global-asset-library.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — 为每个 Task 派一个新 subagent 执行，主会话审阅并决定是否进入下一 task。适合本 plan（45 个 task、多阶段、涉及大量跨文件一致性），可避免主会话 context 被占满。

**2. Inline Execution** — 在当前会话里按 executing-plans 顺序批量执行，期间穿插人工 checkpoint。适合想在一次对话里看到全过程、愿意承担更高 context 占用。

**Which approach?**






