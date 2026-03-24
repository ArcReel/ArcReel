# 多用户预埋重构设计

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

放在 `lib/db/base.py`：

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
class BaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _scope_query(self, stmt, model=None):
        """查询范围限定。开源版 no-op，商业版覆盖以注入 user_id 过滤。"""
        return stmt
```

四个 Repository 继承 `BaseRepository`：`TaskRepository`、`UsageRepository`、`SessionRepository`、`ApiKeyRepository`。

所有查询方法在执行前调用 `self._scope_query(stmt, Model)`：

```python
async def list_tasks(self, project_name=None, ...):
    stmt = select(Task)
    stmt = self._scope_query(stmt, Task)
    if project_name:
        stmt = stmt.where(Task.project_name == project_name)
    ...
```

**商业版子类示例：**

```python
class MultiUserTaskRepository(TaskRepository):
    def __init__(self, session, user_id: str):
        super().__init__(session)
        self._user_id = user_id

    def _scope_query(self, stmt, model=None):
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

### 5.2 get_current_user 改造

```python
async def get_current_user(...) -> CurrentUserInfo:
    payload = await _verify_and_get_payload(token, db)
    sub = payload.get("sub", "")
    return CurrentUserInfo(
        id="default",
        sub=sub,
        role="admin",
    )
```

### 5.3 类型别名

```python
CurrentUser = Annotated[CurrentUserInfo, Depends(get_current_user)]
```

### 5.4 对现有代码的影响

- 所有 `current_user["sub"]` → `current_user.sub`（dict 访问改为属性访问）
- 路由签名 `current_user: dict` → `user: CurrentUser`

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

### 6.2 GenerationQueue

`enqueue()` 方法新增 `user_id` 参数，透传到 Task 创建。

---

## 七、Migration 计划

单个 migration 文件完成所有 schema 变更：

1. 创建 `users` 表
2. 插入默认用户 `(id="default", username="admin", role="admin")`
3. 给 Task、ApiCall、AgentSession、ApiKey 添加 `user_id` 字段（`server_default="default"`，FK → `users.id`）
4. 修复 ApiCall.created_at：Optional → NOT NULL（填充现有 NULL 行为 `started_at` 值）
5. 给 ApiCall 新增 `updated_at` 字段
6. 给 ApiKey 新增 `updated_at` 字段
7. AgentSession 的 `created_at`/`updated_at` 迁移为 Mixin 统一实现（schema 不变，仅代码层面）

---

## 八、不做什么

- **不改 ProjectManager**：扁平目录结构不变
- **不加登录流程**：开源版保持现有单用户认证
- **不建管理后台**：留给商业版
- **不加配额系统**：留给商业版
- **不改前端**：无用户可见的功能变化
