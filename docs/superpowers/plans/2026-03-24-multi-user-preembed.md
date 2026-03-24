# 多用户预埋重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在开源版中预埋多用户支持的基础设施（User 模型、Mixin 基类、Repository 模板方法、Auth 对象化），为未来扩展做准备。

**Architecture:** 通过 Mixin 统一审计字段，Repository 基类提供 `_scope_query` 覆盖点，Auth 返回 Pydantic 对象替代 dict。所有改动对开源版单用户体验透明。

**Tech Stack:** SQLAlchemy 2.0 ORM, Pydantic v2, Alembic, FastAPI Depends

**重要约束:** 所有 commit message 不得提及商业版本。

**设计文档:** `docs/superpowers/specs/2026-03-24-multi-user-preembed-design.md`

---

### Task 1: Mixin 基础设施 + User 模型

**Files:**
- Modify: `lib/db/base.py`
- Create: `lib/db/models/user.py`
- Modify: `lib/db/models/__init__.py`
- Test: `tests/test_db_models.py`

- [ ] **Step 1: 在 base.py 中添加 _utc_now、TimestampMixin、UserOwnedMixin**

```python
# lib/db/base.py — 在 Base 类定义之后追加

from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

class TimestampMixin:
    """统一的创建/更新时间戳。"""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

class UserOwnedMixin:
    """用户归属标记。"""
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, server_default="default", index=True,
    )
```

- [ ] **Step 2: 创建 User 模型**

```python
# lib/db/models/user.py

from datetime import datetime
from sqlalchemy import String, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from lib.db.base import Base, _utc_now

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )
```

- [ ] **Step 3: 更新 models/__init__.py 导出 User**

在 `__init__.py` 中添加：
```python
from lib.db.models.user import User
```
并在 `__all__` 列表中加入 `"User"`。

- [ ] **Step 4: 编写 Mixin 和 User 模型的测试**

在 `tests/test_db_models.py` 中新增测试：
- `test_user_model_columns`: 验证 User 表的列名和类型
- `test_timestamp_mixin_defaults`: 验证 TimestampMixin 的 default/onupdate
- `test_user_owned_mixin_server_default`: 验证 UserOwnedMixin 的 server_default="default"

- [ ] **Step 5: 运行测试验证**

Run: `uv run python -m pytest tests/test_db_models.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add lib/db/base.py lib/db/models/user.py lib/db/models/__init__.py tests/test_db_models.py
git commit -m "refactor: add User model and Mixin base classes (TimestampMixin, UserOwnedMixin)"
```

---

### Task 2: 现有模型应用 Mixin

**Files:**
- Modify: `lib/db/models/task.py` — 应用 UserOwnedMixin
- Modify: `lib/db/models/api_call.py` — 应用 TimestampMixin + UserOwnedMixin
- Modify: `lib/db/models/api_key.py` — 应用 TimestampMixin + UserOwnedMixin
- Modify: `lib/db/models/session.py` — 应用 TimestampMixin + UserOwnedMixin
- Modify: `lib/db/models/config.py` — 从 base.py 导入 _utc_now
- Test: `tests/test_db_models.py`

- [ ] **Step 1: Task 模型应用 UserOwnedMixin**

修改 `lib/db/models/task.py`：
- 导入 `UserOwnedMixin` from `lib.db.base`
- `Task(Base)` → `Task(UserOwnedMixin, Base)`
- Task 保留现有的 `queued_at`/`updated_at`，不用 TimestampMixin

注意：TaskEvent 和 WorkerLease 不加 Mixin。

- [ ] **Step 2: ApiCall 模型应用 TimestampMixin + UserOwnedMixin**

修改 `lib/db/models/api_call.py`：
- 导入 `TimestampMixin, UserOwnedMixin` from `lib.db.base`
- `ApiCall(Base)` → `ApiCall(TimestampMixin, UserOwnedMixin, Base)`
- **删除** ApiCall 自有的 `created_at` 字段定义（由 TimestampMixin 提供，修复 Optional → NOT NULL）
- **新增** `updated_at` 通过 TimestampMixin 自动获得

- [ ] **Step 3: ApiKey 模型应用 TimestampMixin + UserOwnedMixin**

修改 `lib/db/models/api_key.py`：
- `ApiKey(Base)` → `ApiKey(TimestampMixin, UserOwnedMixin, Base)`
- **删除** ApiKey 自有的 `created_at` 字段定义（由 TimestampMixin 提供）
- **新增** `updated_at` 通过 TimestampMixin 自动获得

- [ ] **Step 4: AgentSession 模型应用 TimestampMixin + UserOwnedMixin**

修改 `lib/db/models/session.py`：
- `AgentSession(Base)` → `AgentSession(TimestampMixin, UserOwnedMixin, Base)`
- **删除** AgentSession 自有的 `created_at` 和 `updated_at` 字段定义

- [ ] **Step 5: config.py 统一使用 base.py 的 _utc_now**

修改 `lib/db/models/config.py`：
- 删除本地 `_utc_now()` 定义
- 添加 `from lib.db.base import _utc_now`

- [ ] **Step 6: 编写测试验证 Mixin 应用**

在 `tests/test_db_models.py` 中新增：
- `test_task_has_user_id`: 验证 Task 有 user_id 列
- `test_api_call_has_timestamp_and_user_id`: 验证 ApiCall 有 created_at(NOT NULL)、updated_at、user_id
- `test_api_key_has_timestamp_and_user_id`: 验证 ApiKey 有 updated_at 和 user_id
- `test_agent_session_has_timestamp_and_user_id`: 验证 AgentSession 的 Mixin 字段
- `test_task_event_no_user_id`: 验证 TaskEvent 没有 user_id（不应用 Mixin）
- `test_worker_lease_no_user_id`: 验证 WorkerLease 没有 user_id

- [ ] **Step 7: 运行测试**

Run: `uv run python -m pytest tests/test_db_models.py -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add lib/db/models/task.py lib/db/models/api_call.py lib/db/models/api_key.py lib/db/models/session.py lib/db/models/config.py tests/test_db_models.py
git commit -m "refactor: apply TimestampMixin and UserOwnedMixin to ORM models"
```

---

### Task 3: Repository 基类 + _scope_query

**Files:**
- Create: `lib/db/repositories/base.py`
- Modify: `lib/db/repositories/task_repo.py`
- Modify: `lib/db/repositories/usage_repo.py`
- Modify: `lib/db/repositories/session_repo.py`
- Modify: `lib/db/repositories/api_key_repository.py`
- Create: `tests/test_repository_base.py`

- [ ] **Step 1: 创建 BaseRepository**

```python
# lib/db/repositories/base.py

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from lib.db.base import Base


class BaseRepository:
    """Repository 基类。提供 _scope_query 覆盖点。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _scope_query(self, stmt: Select, model: type[Base]) -> Select:
        """查询范围限定。子类可覆盖以注入额外过滤条件。"""
        return stmt
```

- [ ] **Step 2: TaskRepository 继承 BaseRepository，插入 _scope_query**

修改 `lib/db/repositories/task_repo.py`：
- 导入 `from lib.db.repositories.base import BaseRepository`
- `class TaskRepository:` → `class TaskRepository(BaseRepository):`
- 删除 `__init__` 中的 `self.session = session`（继承自 BaseRepository）
- 在以下方法的查询构建处插入 `stmt = self._scope_query(stmt, Task)`：
  - `list_tasks`: 在 `stmt = select(Task)` 之后
  - `get`: 在 `stmt = select(Task).where(Task.task_id == task_id)` 之后
  - `get_stats`: 在 `stmt = select(...)` 之后
  - `get_recent_tasks_snapshot`: 在 `stmt = select(Task)` 之后
- 在 TaskEvent 查询方法中，通过 JOIN Task 过滤：
  - `get_events_since`: 在查询中添加 `.join(Task, TaskEvent.task_id == Task.task_id)` 然后 `self._scope_query(stmt, Task)`
  - `get_latest_event_id`: 同上
- `claim_next`: 在注释中标记 `# TODO: 重构为 ORM 查询以支持 _scope_query 覆盖`

- [ ] **Step 3: UsageRepository 继承 BaseRepository，插入 _scope_query**

修改 `lib/db/repositories/usage_repo.py`：
- 继承 BaseRepository，删除 `self.session = session`
- 删除本地 `_utc_now()` 定义，改为 `from lib.db.base import _utc_now`
- 在以下方法插入 `_scope_query`：
  - `get_stats`
  - `get_stats_grouped_by_provider`
  - `get_calls`
  - `get_projects_list`

- [ ] **Step 4: SessionRepository 继承 BaseRepository，插入 _scope_query**

修改 `lib/db/repositories/session_repo.py`：
- 继承 BaseRepository，删除 `self.session = session`
- 删除本地 `_utc_now()` 定义，改为从 base 导入
- 在以下方法插入 `_scope_query`：
  - `get`
  - `list`

- [ ] **Step 5: ApiKeyRepository 继承 BaseRepository，插入 _scope_query**

修改 `lib/db/repositories/api_key_repository.py`：
- 继承 BaseRepository，删除 `self.session = session`
- 删除本地 `_utc_now()` 定义，改为从 base 导入
- 在以下方法插入 `_scope_query`：
  - `list_all`
  - `get_by_hash`
  - `get_by_id`

- [ ] **Step 6: 编写 BaseRepository 和 _scope_query 测试**

`tests/test_repository_base.py`：

```python
import pytest
from sqlalchemy import select
from lib.db.repositories.base import BaseRepository
from lib.db.models import Task

class TestBaseRepository:
    def test_scope_query_noop(self):
        """_scope_query 默认返回原始 stmt 不做修改。"""
        repo = BaseRepository.__new__(BaseRepository)
        stmt = select(Task)
        result = repo._scope_query(stmt, Task)
        assert str(result) == str(stmt)

    def test_scope_query_overridable(self):
        """子类可以覆盖 _scope_query 添加过滤条件。"""
        class ScopedRepo(BaseRepository):
            def _scope_query(self, stmt, model):
                return stmt.where(model.user_id == "test-user")

        repo = ScopedRepo.__new__(ScopedRepo)
        stmt = select(Task)
        result = repo._scope_query(stmt, Task)
        assert "user_id" in str(result)
```

- [ ] **Step 7: 运行全部 Repository 测试**

Run: `uv run python -m pytest tests/test_repository_base.py tests/test_task_repo.py tests/test_usage_repo.py tests/test_session_repo.py -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add lib/db/repositories/base.py lib/db/repositories/task_repo.py lib/db/repositories/usage_repo.py lib/db/repositories/session_repo.py lib/db/repositories/api_key_repository.py tests/test_repository_base.py
git commit -m "refactor: add BaseRepository with _scope_query and unify _utc_now imports"
```

---

### Task 4: Repository 写入方法添加 user_id 参数

**Files:**
- Modify: `lib/db/repositories/task_repo.py`
- Modify: `lib/db/repositories/usage_repo.py`
- Modify: `lib/db/repositories/session_repo.py`
- Modify: `lib/db/repositories/api_key_repository.py`
- Modify: existing tests

- [ ] **Step 1: TaskRepository.enqueue 添加 user_id**

修改方法签名：
```python
async def enqueue(
    self,
    *,
    project_name: str,
    task_type: str,
    media_type: str,
    resource_id: str,
    payload: Optional[dict[str, Any]] = None,
    script_file: Optional[str] = None,
    source: str = "webui",
    user_id: str = "default",      # ← 新增
    dependency_task_id: Optional[str] = None,
    dependency_group: Optional[str] = None,
    dependency_index: Optional[int] = None,
) -> dict[str, Any]:
```

在创建 Task 实例时，传入 `user_id=user_id`。

- [ ] **Step 2: UsageRepository.start_call 添加 user_id**

修改方法签名，在 `provider` 参数后添加：
```python
    user_id: str = "default",
```

在创建 ApiCall 实例时，传入 `user_id=user_id`。

- [ ] **Step 3: SessionRepository.create 添加 user_id**

修改方法签名：
```python
async def create(self, project_name: str, sdk_session_id: str, title: str = "", user_id: str = "default") -> dict[str, Any]:
```

在创建 AgentSession 实例时，传入 `user_id=user_id`。

- [ ] **Step 4: ApiKeyRepository.create 添加 user_id**

修改方法签名：
```python
async def create(self, *, name: str, key_hash: str, key_prefix: str, expires_at: Optional[datetime] = None, user_id: str = "default") -> dict[str, Any]:
```

在创建 ApiKey 实例时，传入 `user_id=user_id`。

- [ ] **Step 5: 更新现有测试适配新签名**

检查并修复受影响的测试文件中调用这些方法的地方。由于 `user_id` 默认值为 `"default"`，大部分现有测试无需修改。需重点检查：
- `tests/test_task_repo.py`
- `tests/test_usage_repo.py`
- `tests/test_session_repo.py`

- [ ] **Step 6: 运行全部测试**

Run: `uv run python -m pytest tests/ -x -v --timeout=60`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add lib/db/repositories/task_repo.py lib/db/repositories/usage_repo.py lib/db/repositories/session_repo.py lib/db/repositories/api_key_repository.py
git commit -m "refactor: add user_id parameter to repository write methods"
```

---

### Task 5: Auth 改造（CurrentUserInfo + 类型别名）

**Files:**
- Modify: `server/auth.py`
- Modify: `tests/test_auth.py`

- [ ] **Step 1: 定义 CurrentUserInfo 和类型别名**

在 `server/auth.py` 中添加：

```python
from pydantic import BaseModel, ConfigDict
from typing import Annotated

class CurrentUserInfo(BaseModel):
    """当前登录用户信息。"""
    id: str
    sub: str
    role: str = "admin"

    model_config = ConfigDict(frozen=True)

# 类型别名，供路由使用
CurrentUser = Annotated[CurrentUserInfo, Depends(get_current_user)]
CurrentUserFlexible = Annotated[CurrentUserInfo, Depends(get_current_user_flexible)]
```

- [ ] **Step 2: 修改 get_current_user 返回 CurrentUserInfo**

```python
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> CurrentUserInfo:
    payload = await _verify_and_get_payload_async(token)
    sub = payload.get("sub", "")
    return CurrentUserInfo(id="default", sub=sub, role="admin")
```

- [ ] **Step 3: 修改 get_current_user_flexible 返回 CurrentUserInfo**

```python
async def get_current_user_flexible(
    token: Annotated[str | None, Depends(oauth2_scheme_optional)] = None,
    query_token: str | None = Query(None, alias="token"),
) -> CurrentUserInfo:
    raw = token or query_token
    if not raw:
        raise HTTPException(status_code=401, detail="缺少认证 token", ...)
    payload = await _verify_and_get_payload_async(raw)
    sub = payload.get("sub", "")
    return CurrentUserInfo(id="default", sub=sub, role="admin")
```

- [ ] **Step 4: 更新 auth 测试**

修改 `tests/test_auth.py` 中对 `get_current_user` 返回值的断言：
- `result["sub"]` → `result.sub`
- `isinstance(result, dict)` → `isinstance(result, CurrentUserInfo)`

- [ ] **Step 5: 运行 auth 测试**

Run: `uv run python -m pytest tests/test_auth.py tests/test_auth_api_key.py tests/test_auth_middleware.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add server/auth.py tests/test_auth.py tests/test_auth_api_key.py tests/test_auth_middleware.py
git commit -m "refactor: return CurrentUserInfo from auth instead of dict"
```

---

### Task 6: 路由层迁移（dict → CurrentUser）

**Files:**
- Modify: `server/routers/projects.py`
- Modify: `server/routers/generate.py`
- Modify: `server/routers/assistant.py`
- Modify: `server/routers/agent_chat.py`
- Modify: `server/routers/tasks.py`
- Modify: `server/routers/project_events.py`
- Modify: `server/routers/files.py`
- Modify: `server/routers/characters.py`
- Modify: `server/routers/clues.py`
- Modify: `server/routers/api_keys.py`
- Modify: `server/routers/usage.py`
- Modify: `server/routers/system_config.py`
- Modify: `server/routers/versions.py`
- Modify: `server/routers/auth.py`
- Test: 全部 router 测试

此 Task 为**机械替换**，每个路由文件的模式相同：

1. 将 `from server.auth import get_current_user` 改为 `from server.auth import CurrentUser`（如果也用了 flexible 版本，同时导入 `CurrentUserFlexible`）
2. 路由函数参数中 `_user: Annotated[dict, Depends(get_current_user)]` → `_user: CurrentUser`
3. 路由函数参数中 `Annotated[dict, Depends(get_current_user_flexible)]` → `_user: CurrentUserFlexible`
4. 所有 `current_user["sub"]` → `current_user.sub` 的属性访问替换

- [ ] **Step 1: 迁移 projects.py（最复杂，含属性访问）**

`server/routers/projects.py` 有 ~13 个路由使用 `current_user`，其中至少 1 处使用 `current_user["sub"]`。

- [ ] **Step 2: 迁移 generate.py**

~4 个路由。

- [ ] **Step 3: 迁移 assistant.py（含 flexible）**

~9 个路由，其中 SSE 端点使用 `get_current_user_flexible`。

- [ ] **Step 4: 迁移 tasks.py（含 flexible）**

~4 个路由，task stream SSE 使用 `get_current_user_flexible`。

- [ ] **Step 5: 迁移 project_events.py（含 flexible）**

SSE 端点使用 `get_current_user_flexible`。

- [ ] **Step 6: 迁移其余路由文件**

按批处理：
- `files.py`（~12 路由）
- `characters.py`（~3 路由）
- `clues.py`（~3 路由）
- `api_keys.py`（~3 路由）
- `usage.py`（~3 路由）
- `system_config.py`（~2 路由）
- `versions.py`（~2 路由）
- `auth.py`（~1 路由）
- `agent_chat.py`（~1 路由）

- [ ] **Step 7: 运行全部路由测试**

Run: `uv run python -m pytest tests/test_*router*.py tests/test_*routes*.py tests/test_*sse*.py -v --timeout=60`
Expected: PASS

- [ ] **Step 8: 运行全部测试确认无回归**

Run: `uv run python -m pytest tests/ -x --timeout=60`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add server/routers/
git commit -m "refactor: migrate all routes from dict auth to CurrentUser type alias"
```

---

### Task 7: Generation Pipeline user_id 透传

**Files:**
- Modify: `lib/generation_queue.py` — `enqueue_task` 添加 `user_id`
- Modify: `lib/generation_queue_client.py` — `enqueue_and_wait`、`enqueue_task_only` 添加 `user_id`
- Modify: `lib/usage_tracker.py` — `start_call` 添加 `user_id`
- Modify: `lib/media_generator.py` — 构造时接受 `user_id`，传给 usage_tracker
- Modify: `lib/generation_worker.py` — 从 task record 取 `user_id` 传给 MediaGenerator
- Modify: `server/routers/generate.py` — 传入 `user.id` 到 enqueue

- [ ] **Step 1: GenerationQueue.enqueue_task 添加 user_id**

修改 `lib/generation_queue.py` 的 `enqueue_task` 方法签名：
```python
async def enqueue_task(
    self,
    *,
    project_name: str,
    task_type: str,
    media_type: str,
    resource_id: str,
    payload: Optional[Dict[str, Any]] = None,
    script_file: Optional[str] = None,
    source: str = "webui",
    user_id: str = "default",      # ← 新增
    ...
) -> Dict[str, Any]:
```

在调用 `self._repo.enqueue(...)` 时透传 `user_id=user_id`。

- [ ] **Step 2: generation_queue_client 添加 user_id**

修改 `lib/generation_queue_client.py`：
- `enqueue_and_wait` 签名新增 `user_id: str = "default"`
- `enqueue_task_only` 签名新增 `user_id: str = "default"`
- 透传到 `GenerationQueue.enqueue_task(user_id=user_id, ...)`

- [ ] **Step 3: UsageTracker.start_call 添加 user_id**

修改 `lib/usage_tracker.py` 的 `start_call` 方法签名：
```python
async def start_call(
    self,
    project_name: str,
    call_type: str,
    model: str,
    ...,
    user_id: str = "default",      # ← 新增
) -> int:
```
透传到 `UsageRepository.start_call(user_id=user_id, ...)`。

- [ ] **Step 4: MediaGenerator 支持 user_id**

修改 `lib/media_generator.py`：
- 构造函数新增 `user_id: str = "default"` 参数
- 存储为 `self._user_id = user_id`
- 在所有 `self.usage_tracker.start_call(...)` 调用中追加 `user_id=self._user_id`

- [ ] **Step 5: GenerationWorker 从 task 传递 user_id**

修改 `lib/generation_worker.py`：
- 在从 task record 创建 MediaGenerator 时，传入 `user_id=task.get("user_id", "default")`

- [ ] **Step 6: generate.py 路由传入 user.id**

修改 `server/routers/generate.py`：
- 在调用 `queue.enqueue_task(...)` 时追加 `user_id=user.id`

- [ ] **Step 7: 运行 generation pipeline 测试**

Run: `uv run python -m pytest tests/test_generation_queue.py tests/test_generation_queue_client.py tests/test_generation_worker_module.py tests/test_media_generator_module.py tests/test_generate_router.py -v --timeout=60`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add lib/generation_queue.py lib/generation_queue_client.py lib/usage_tracker.py lib/media_generator.py lib/generation_worker.py server/routers/generate.py
git commit -m "refactor: propagate user_id through generation pipeline"
```

---

### Task 8: Alembic Migration

**Files:**
- Create: `alembic/versions/<hash>_add_users_and_user_id.py`

- [ ] **Step 1: 生成 migration**

Run: `uv run alembic revision --autogenerate -m "add users table and user_id to models"`

- [ ] **Step 2: 审查并编辑生成的 migration**

自动生成的 migration 需要手动调整：

1. **确保 `users` 表在其他变更之前创建**（因为 FK 依赖）
2. **插入默认用户**：
```python
op.execute("""
    INSERT INTO users (id, username, role, is_active, created_at, updated_at)
    VALUES ('default', 'admin', 'admin', 1,
            strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'),
            strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
""")
```
3. **SQLite 特殊处理**：修改列可空性（ApiCall.created_at Optional → NOT NULL）需要 `op.batch_alter_table()`
4. **数据修复**：填充 ApiCall.created_at 为 NULL 的行：
```python
op.execute("UPDATE api_calls SET created_at = started_at WHERE created_at IS NULL")
```
5. **确保 user_id 字段的索引被创建**

- [ ] **Step 3: 运行 migration 测试**

Run: `uv run alembic upgrade head`
Expected: 无报错

Run: `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: 回退和重新应用均无报错

- [ ] **Step 4: 运行全部测试**

Run: `uv run python -m pytest tests/ -x --timeout=60`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add alembic/versions/
git commit -m "feat: add users table and user_id fields via migration"
```

---

### Task 9: 全量回归测试 + 清理

**Files:**
- 可能修改：任何因回归失败需要修复的文件

- [ ] **Step 1: 运行全部测试套件**

Run: `uv run python -m pytest tests/ -v --timeout=120`
Expected: 全部 PASS

- [ ] **Step 2: TypeScript 类型检查（前端未改但确认不受影响）**

Run: `cd frontend && pnpm check`
Expected: PASS

- [ ] **Step 3: 检查 import 循环**

Run: `uv run python -c "from lib.db.models import User, Task, ApiCall, ApiKey, AgentSession; print('OK')"`
Expected: 输出 "OK"

- [ ] **Step 4: 修复任何回归问题**

如有测试失败，逐个修复并重新运行。

- [ ] **Step 5: 最终提交（如有修复）**

```bash
git add -A
git commit -m "fix: address regression issues from multi-user preembed refactor"
```
