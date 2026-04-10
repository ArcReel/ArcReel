# Multi-User Pre-embedding Refactor Design

> **日期**：2026-03-24
> **目标**：在开源版中做接口和数据模型预埋，让商业版能以继承/覆盖方式干净地扩展多用户功能
> **范围**：适度预埋（不含多租户目录隔离、不含登录流程、不含管理后台）

---

## 一、设计决策摘要

| 决策 | 结论 |
|------|------|
| 重构范围 | 适度预埋，不做多租户 |
| 开源版用户体验 | 保持单用户，不变 |
| 项目隔离策略 | 扁平目录不变，通过 DB `user_id` 控制可见性 |
| `get_current_user` 返回值 | Pydantic model (`CurrentUserInfo`) |
| Repository 预埋 | 模板方法 `_scope_query()`，开源版 no-op |
| ORM 模型 user_id | 预埋，带默认值 `"default"` |
| ProjectManager | 不改动 |

---

## 二、User ORM 模型

新增 `lib/db/models/user.py`：

```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- 开源版只保留身份必需字段
- 商业版通过 migration 扩展字段（email、hashed_password、display_name、quota_*、last_login_at）
- Migration 创建表时插入默认用户：`id="default", username="admin", role="admin"`

---

## 三、模型基类体系（Mixin）

### 3.1 现有问题

| 不一致 | 涉及模型 |
|--------|---------|
| `created_at` 有的 NOT NULL，有的 Optional，有的没有 | ApiCall(Optional!)、Task(无)、ProviderConfig(无) |
| 时间戳生成策略混用 | ProviderConfig/SystemSetting 用 Python `default`，其余靠应用层手动赋值 |
| `updated_at` 有的有，有的没有 | ApiKey(无)、TaskEvent(无) |

### 3.2 Mixin 定义

放在 `lib/db/base.py`（统一为全局唯一定义，各 repository 和 config.py 中的重复 `_utc_now()` 改为从此处导入）：

```python
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
    """用户归属标记。开源版固定为 "default"，商业版通过 _scope_query 过滤。"""
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, server_default="default", index=True,
    )
```

### 3.3 各模型的 Mixin 应用

| 模型 | TimestampMixin | UserOwnedMixin | 说明 |
|------|:-:|:-:|------|
| **Task** | - | ✓ | 保留 `queued_at`/`updated_at`（有领域含义） |
| **TaskEvent** | - | - | 不可变事件，通过 Task FK 间接关联用户 |
| **ApiCall** | ✓ | ✓ | 修复 `created_at` Optional → NOT NULL，新增 `updated_at` |
| **ApiKey** | ✓ | ✓ | 新增 `updated_at` |
| **AgentSession** | ✓ | ✓ | 已有时间戳，改为从 Mixin 继承 |
| **WorkerLease** | - | - | 基础设施，不涉及用户 |
| **ProviderConfig** | - | - | 系统配置，保留自有时间戳 |
| **SystemSetting** | - | - | 同上 |

### 3.4 不应用 Mixin 的理由

- **Task**：`queued_at` 是创建时间的领域表达，强行替换为 `created_at` 会丢失业务语义
- **TaskEvent**：通过 `task_id` FK 间接归属用户，加冗余 `user_id` 违背范式
- **WorkerLease**：基础设施模型，无用户归属概念
- **ProviderConfig / SystemSetting**：系统级配置，无用户归属；已有 `_utc_now` 实现，移入 Mixin 后可复用

---

## 四、Repository 基类

新增 `lib/db/repositories/base.py`：

```python
from sqlalchemy import Select

class BaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _scope_query(self, stmt: Select, model: type[Base]) -> Select:
        """查询范围限定。开源版 no-op，商业版覆盖以注入 user_id 过滤。"""
        return stmt
```

四个 Repository 继承 `BaseRepository`：`TaskRepository`、`UsageRepository`、`SessionRepository`、`ApiKeyRepository`。

### 4.1 需要插入 `_scope_query` 的查询方法完整清单

| Repository | 方法 | 说明 |
|---|---|---|
| **TaskRepository** | `list_tasks`, `get`, `get_stats`, `get_recent_tasks_snapshot` | 直接查询 Task 表的方法 |
| **TaskRepository** | `get_events_since`, `get_latest_event_id` | 查询 TaskEvent 表，需通过 JOIN Task 过滤用户（TaskEvent 无 `user_id` 字段） |
| **TaskRepository** | `claim_next` | **特殊处理**：当前使用原生 SQL（依赖自连接），需重构为 ORM 查询以支持 `_scope_query`，或标记为商业版必须 override 的方法 |
| **UsageRepository** | `get_stats`, `get_stats_grouped_by_provider`, `get_calls`, `get_projects_list` | 所有读取方法 |
| **SessionRepository** | `get`, `list` | 所有读取方法 |
| **ApiKeyRepository** | `list_all`, `get_by_hash`, `get_by_id` | 所有读取方法 |

示例：

```python
async def list_tasks(self, project_name=None, ...):
    stmt = select(Task)
    stmt = self._scope_query(stmt, Task)
    if project_name:
        stmt = stmt.where(Task.project_name == project_name)
    ...
```

### 4.2 `claim_next` 原生 SQL 问题

`TaskRepository.claim_next()` 使用 `text()` 原生 SQL 处理依赖自连接，`_scope_query` 无法拦截。需要将其重构为 ORM 查询，以确保商业版的用户过滤能正确生效。如果重构复杂度过高，则标记为商业版必须 override 的方法，并在方法文档中注明。

### 4.3 需要新增 `user_id` 参数的写入方法

| Repository | 方法 |
|---|---|
| **TaskRepository** | `enqueue` |
| **UsageRepository** | `start_call` |
| **SessionRepository** | `create` |
| **ApiKeyRepository** | `create` |

这些方法新增 `user_id: str = "default"` 参数，写入对应模型的 `user_id` 字段。

### 4.4 商业版子类示例

```python
class MultiUserTaskRepository(TaskRepository):
    def __init__(self, session, user_id: str):
        super().__init__(session)
        self._user_id = user_id

    def _scope_query(self, stmt: Select, model: type[Base]) -> Select:
        return stmt.where(model.user_id == self._user_id)
```

---

## 五、Auth 改造

### 5.1 CurrentUserInfo 模型

放在 `server/auth.py`：

```python
class CurrentUserInfo(BaseModel):
    id: str
    sub: str
    role: str = "admin"

    model_config = ConfigDict(frozen=True)
```

### 5.2 `get_current_user` 和 `get_current_user_flexible` 同步改造

两个认证函数都需要改为返回 `CurrentUserInfo`：

```python
async def get_current_user(...) -> CurrentUserInfo:
    payload = await _verify_and_get_payload(token, db)
    sub = payload.get("sub", "")
    return CurrentUserInfo(id="default", sub=sub, role="admin")

async def get_current_user_flexible(...) -> CurrentUserInfo:
    # 用于 SSE 端点（支持 query param token）
    # 同样改为返回 CurrentUserInfo
    ...
```

`get_current_user_flexible` 被以下 SSE 端点使用，必须同步改造：
- `server/routers/assistant.py` — SSE stream
- `server/routers/tasks.py` — SSE stream
- `server/routers/project_events.py` — SSE stream

### 5.3 类型别名

```python
CurrentUser = Annotated[CurrentUserInfo, Depends(get_current_user)]
CurrentUserFlexible = Annotated[CurrentUserInfo, Depends(get_current_user_flexible)]
```

### 5.4 `id` 与 `sub` 的语义说明

- `id`：对应 `users.id` 主键，用于数据库关联。开源版固定 `"default"`，商业版为真实用户 ID
- `sub`：JWT payload 中的 subject claim，表示登录身份（用户名或 `apikey:<name>`）。保留此字段以兼容现有日志/审计逻辑

### 5.5 对现有代码的影响

- 约 15 个路由文件中共 ~80 处 `current_user` 引用需更新（签名类型 + 变量名 + 属性访问方式）
- 路由签名 `current_user: dict` → `user: CurrentUser`（大部分改动）
- `current_user["sub"]` → `current_user.sub`（仅约 2 处 dict 属性访问）

---

## 六、路由层改造

### 6.1 写入时传递 user_id

```python
# 改造前
@router.post("/api/v1/projects/{project_name}/tasks")
async def create_task(project_name: str, ...):
    await task_repo.create_task(project_name=project_name, ...)

# 改造后
@router.post("/api/v1/projects/{project_name}/tasks")
async def create_task(project_name: str, user: CurrentUser, ...):
    await task_repo.create_task(project_name=project_name, user_id=user.id, ...)
```

### 6.2 GenerationQueue 完整调用链

`user_id` 需要在以下完整调用链中透传：

```
路由层 (user.id)
  → GenerationQueue.enqueue_task(user_id=...)
    → TaskRepository.enqueue(user_id=...)

Skill 脚本 (agent runtime)
  → generation_queue_client.enqueue_and_wait(user_id=...)
    → enqueue_task_only(user_id=...)
      → GenerationQueue.enqueue_task(user_id=...)
```

Skill 脚本运行在 agent runtime 中无 HTTP 认证上下文，`user_id` 来源策略：开源版默认 `"default"`，商业版由 agent session 携带 `user_id` 传入。

### 6.3 UsageRepository 调用链

`MediaGenerator` 调用 `UsageRepository.start_call()` 时也需要透传 `user_id`。`user_id` 来源策略：Task 模型已有 `user_id` 字段，`GenerationWorker` 从队列取任务时从 task record 中获取 `user_id`，传给 `MediaGenerator`，再透传到 `start_call()`。

---

## 七、Migration 计划

单个 migration 文件完成所有 schema 变更：

1. 创建 `users` 表
2. 插入默认用户 `(id="default", username="admin", role="admin")`
3. 给 Task、ApiCall、AgentSession、ApiKey 添加 `user_id` 字段（`server_default="default"`，FK → `users.id`，含索引）
4. 修复 ApiCall.created_at：Optional → NOT NULL（填充现有 NULL 行为 `started_at` 值）
5. 给 ApiCall 新增 `updated_at` 字段
6. 给 ApiKey 新增 `updated_at` 字段
7. AgentSession 的 `created_at`/`updated_at` 迁移为 Mixin 统一实现（schema 不变，仅代码层面）

**实现提示**：SQLite 不支持 `ALTER COLUMN`，修改列可空性（步骤 4）需使用 `op.batch_alter_table()` 重建表。

---

## 八、不做什么

- **不改 ProjectManager**：扁平目录结构不变
- **不加登录流程**：开源版保持现有单用户认证
- **不建管理后台**：留给商业版
- **不加配额系统**：留给商业版
- **不改前端**：无用户可见的功能变化
