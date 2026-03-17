# Alembic 最佳实践修复设计

## 概述

对项目 Alembic + Async SQLAlchemy 配置进行全面修复，包括：基础设施配置补齐、时间戳类型统一（String → DateTime）、外键约束添加、PostgreSQL 连接池优化。采用单次迁移方案，一步到位。

## 背景

审查发现 6 个问题：

1. **`render_as_batch=True` 缺失（高优先级）** — 未来 SQLite 上 ALTER TABLE 迁移会失败
2. **时间戳类型混用（中优先级）** — 5 张表用 String，1 张表（ApiKey）用 DateTime，三个 repo 的格式还不一致
3. **TaskEvent 无外键（中优先级）** — 数据完整性无数据库级保护
4. **post_write_hooks 未启用（低优先级）** — 迁移文件格式不统一
5. **PostgreSQL 连接池未配置（低优先级）** — 仅影响生产性能
6. **`server_default` 跨库兼容性（低优先级）** — Boolean 默认值写法不规范

## 方案选择

**选定方案一：单次大迁移**。理由：项目早期（仅 2 个迁移版本）、表数据量小、改动逻辑单一（String→DateTime），拆分反而引入中间不一致状态。

数据迁移策略：**直接转换**（非新旧列并存），理由同上。

## 改动范围

### 1. Alembic 基础设施（3 个文件）

#### `alembic/env.py`

`do_run_migrations()` 和 `run_migrations_offline()` 都加 `render_as_batch=True`：

```python
def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
```

离线模式同理。

#### `alembic.ini`

取消注释 ruff post_write_hook：

```ini
[post_write_hooks]
hooks = ruff
ruff.type = exec
ruff.executable = ruff
ruff.options = check --fix REVISION_SCRIPT_FILENAME
```

#### `lib/db/engine.py`

PostgreSQL 时添加连接池参数：

```python
if not _is_sqlite:
    kwargs.update(pool_size=10, max_overflow=20, pool_recycle=3600)
```

### 2. 数据库迁移脚本（1 个新迁移文件）

新迁移 `xxxx_unify_timestamps_and_add_fk.py`。

#### 涉及的列（11 列，5 张表）

| 表 | 列 | String → DateTime(timezone=True) |
|---|---|---|
| tasks | queued_at, started_at, finished_at, updated_at | 4 列 |
| task_events | created_at | 1 列 |
| worker_lease | updated_at | 1 列 |
| api_calls | started_at, finished_at, created_at | 3 列 |
| agent_sessions | created_at, updated_at | 2 列 |

**不改的列**：`worker_lease.lease_until`（Float，epoch 时间戳语义，保持不变）。

#### SQLite 路径

`render_as_batch=True` 下，Alembic 通过重建表实现列类型变更。流程：创建新表 → `INSERT INTO new SELECT * FROM old` → 删旧表 → 重命名。SQLite 没有原生 DateTime 类型，ISO 字符串会原样复制到新列中（仍为 TEXT 存储）。aiosqlite + SQLAlchemy 在读取 DateTime 列时会自动做 `fromisoformat` 解析，所以运行时不受影响。

#### PostgreSQL 路径

使用防御性 USING 子句处理可能的脏数据：

```sql
ALTER COLUMN col TYPE TIMESTAMP WITH TIME ZONE
USING CASE WHEN col IS NOT NULL AND col != '' THEN col::timestamptz END
```

三种现存 ISO 格式（`2026-03-17T12:00:00Z`、`...000+00:00`、`...+00:00`）PostgreSQL 都能正确解析。

#### 外键约束

`task_events.task_id` 添加 `ForeignKey("tasks.task_id", ondelete="CASCADE")`。

**孤立数据清理**：迁移 upgrade 中，在添加外键约束之前，先清理可能存在的孤立记录：

```sql
DELETE FROM task_events WHERE task_id NOT IN (SELECT task_id FROM tasks)
```

#### server_default 修正

`api_calls.generate_audio` 的 `server_default="1"` 改为 `server_default=sa.true_()`。`sa.true_()` 是 SQLAlchemy 提供的跨后端布尔字面量（PostgreSQL 生成 `true`，SQLite 生成 `1`），避免 `sa.text("true")` 在 SQLite 上存储字符串 "true" 的问题。

#### downgrade

- DateTime 列改回 String
  - PostgreSQL 用 `USING to_char(col AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')`
  - SQLite 用 batch mode 重建表
  - **注意**：downgrade 后时间戳格式统一为 `YYYY-MM-DDTHH:MM:SSZ`（无毫秒），与原始三种不同格式不完全一致。这是可接受的退化，已记录在案
- 删除 task_events 外键
- `server_default` 改回 `"1"`

### 3. ORM 模型改动（4 个文件）

- `lib/db/models/task.py` — Task（4 列）、TaskEvent（1 列 + 外键）、WorkerLease（1 列）
- `lib/db/models/api_call.py` — ApiCall（3 列 + server_default）
- `lib/db/models/session.py` — AgentSession（2 列）
- `lib/db/models/api_key.py` — 不改（已经是 DateTime）

所有时间戳字段：
```python
# 之前
queued_at: Mapped[str] = mapped_column(String, nullable=False)

# 之后
queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

TaskEvent 新增外键：
```python
task_id: Mapped[str] = mapped_column(
    String, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False
)
```

### 4. Repository 层改动（3 个文件）

三个 repo 各自的 `_utc_now_iso()` helper 统一改为 `_utc_now()`，返回 `datetime.now(timezone.utc)`。

#### `lib/db/repositories/task_repo.py`（~12 处）

- `_utc_now_iso()` → `_utc_now()`，删除 strftime
- 所有 `queued_at=now`、`started_at=now` 等写入点直接赋 datetime 对象
- **`_task_to_dict()` 和 `_event_to_dict()`**：datetime 字段必须显式调用 `.isoformat()` 转为字符串。原因：
  1. `_task_to_dict()` 结果被传入 `json.dumps()`（存入 `TaskEvent.data_json`），datetime 对象会导致 `TypeError: Object of type datetime is not JSON serializable`
  2. `_event_to_dict()` 结果被传入 SSE 事件流，同样需要字符串

#### `lib/db/repositories/usage_repo.py`（~8 处）

- 删除 `_iso_millis()` helper
- `_utc_now_iso()` → `_utc_now()`
- 删除 `datetime.fromisoformat()` 解析（`finish_call` 中计算 duration_ms 直接用 datetime 减法）
- 过滤条件 `ApiCall.started_at >= _iso_millis(start)` 改为 `ApiCall.started_at >= start`（直接传 datetime）
- **`_row_to_dict()`**：datetime 字段显式 `.isoformat()` 以保证 API 返回格式一致

#### `lib/db/repositories/session_repo.py`（~6 处）

- `_utc_now_iso()` → `_utc_now()`
- 所有写入点同上
- **`_row_to_dict()`**：datetime 字段显式 `.isoformat()` 以保证 API 返回格式一致

### 5. Pydantic 模型适配

#### `server/agent_runtime/models.py` — SessionMeta

`created_at` 和 `updated_at` 从 `str` 改为 `datetime` 类型：

```python
# 之前
created_at: str
updated_at: str

# 之后
created_at: datetime
updated_at: datetime
```

原因：当 `session_repo._row_to_dict()` 返回 datetime 对象时，Pydantic 用 `str()` 强转 datetime 会产生 `2026-03-17 12:00:00+00:00`（注意空格而非 T），破坏下游 `_parse_iso_datetime()` 的解析。改为 datetime 类型后 Pydantic 正确处理。

### 6. 周边代码适配

#### `server/auth.py`

删除 `datetime.fromisoformat(expires_at.replace("Z", "+00:00"))` 字符串解析。`ApiKey.expires_at` 从数据库读出即 datetime 对象，直接比较。

#### `server/agent_runtime/service.py` — **不改**

`_parse_iso_datetime()` 仅用于解析 SSE buffer 消息的 timestamp 字段（来自 session_manager 的 `_utc_now_iso()` 和 SDK 原始消息流），完全不涉及数据库字段。保持不变。

#### `server/agent_runtime/session_manager.py` — **不改**

`_utc_now_iso()` 仅用于构造 SSE buffer 中的 `"timestamp"` 字段值（第 817、917、957 行），不涉及数据库写入。保持不变。

### 7. 不改的部分

- `lib/project_manager.py` — 写 JSON 文件的 `.isoformat()` 不变
- `lib/script_generator.py` — 同上
- `server/services/project_archive.py` — 同上
- `server/agent_runtime/service.py` — SSE 解析，不涉及数据库
- `server/agent_runtime/session_manager.py` — SSE 时间戳，不涉及数据库

### 8. 测试适配

- `tests/conftest.py`、`tests/factories.py`、`tests/fakes.py` 中的时间戳字符串 fixture 改为 datetime 对象
- `tests/test_app_module.py` 及其他测试中的时间戳断言适配
- `SessionMeta` 相关测试适配新的 datetime 类型
- 确保 `pytest` 全部通过

## API 兼容性

FastAPI JSON 序列化默认将 `datetime` 输出为 ISO 8601 字符串（`datetime.isoformat()` 格式，即 `+00:00` 后缀而非 `Z`）。

**格式细微差异**：原来 task_repo 输出 `Z` 后缀，改后统一为 `+00:00` 后缀。JavaScript 的 `new Date()` / `Date.parse()` 对两种格式都能正确解析，前端大概率不受影响。

**降低风险措施**：在 `*_to_dict()` 中统一用 `.isoformat()` 显式序列化，确保输出格式可控。如需保持 `Z` 后缀兼容性，可用 `.isoformat().replace("+00:00", "Z")`。

## 验证计划

1. `python -m pytest` — 全部测试通过
2. `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head` — 往返验证
3. 检查 API 响应中时间戳格式（确认 JavaScript Date.parse 兼容）
4. 检查前端代码中对时间戳字段的解析方式，确认无硬编码 `Z` 后缀匹配

## 文件清单

| 文件 | 改动类型 |
|---|---|
| `alembic/env.py` | 加 `render_as_batch=True` |
| `alembic.ini` | 启用 ruff hook |
| `lib/db/engine.py` | PG 连接池参数 |
| `alembic/versions/xxxx_unify_timestamps_and_add_fk.py` | 新迁移 |
| `lib/db/models/task.py` | String → DateTime, 外键 |
| `lib/db/models/api_call.py` | String → DateTime, server_default |
| `lib/db/models/session.py` | String → DateTime |
| `lib/db/repositories/task_repo.py` | _utc_now_iso → _utc_now |
| `lib/db/repositories/usage_repo.py` | 同上 + 删除解析逻辑 |
| `lib/db/repositories/session_repo.py` | 同上 |
| `server/auth.py` | 删除字符串解析 |
| `server/agent_runtime/models.py` | SessionMeta 时间戳 str → datetime |
| `tests/conftest.py` | fixture 适配 |
| `tests/factories.py` | fixture 适配 |
| `tests/fakes.py` | fixture 适配 |
