# SDK 会话管理升级设计

## 背景

项目从 claude-agent-sdk 0.1.48 升级到 0.1.50，利用新增的会话管理 API 简化架构：

- `list_sessions(directory)` — 按 cwd 列出会话，返回 `SDKSessionInfo`（含 `summary` 自动标题）
- `get_session_info(session_id)` — 查询单个会话元数据
- `tag_session(session_id, tag)` — 为会话打标签
- `rename_session(session_id, title)` — 重命名会话

### 当前问题

1. **双 ID 映射冗余**：应用层 `id`（UUID hex）与 SDK 层 `sdk_session_id` 分离，需要手动关联维护
2. **标题管理简陋**：创建时截取用户消息前 30 字符作 title，无自动摘要
3. **空会话 bug**：`create_session` 先创建 DB 记录，若 SDK 连接失败，遗留 `sdk_session_id=null` 的幽灵记录

## 设计目标

- 消除双 ID，统一使用 SDK session_id 作为唯一标识
- 用 SDK 的 `summary` 自动命名替代手动截取
- 从根本上消除空会话问题
- 移除 DB 中 title 字段的写入维护

## 架构变更

### 会话生命周期：从"先创建后发送"到"发送即创建"

**当前流程**（两步串行，有空会话风险）：

```
POST /sessions(project_name, title) → DB 创建记录(app_id)
POST /send(app_id, message)         → SDK 连接 → 流中提取 sdk_session_id → DB 更新
```

**新流程**（单步，DB 记录仅在 SDK 成功响应后创建）：

```
POST /send(project_name, message, session_id=null)
  → SDK 连接 + query
  → 等待 sdk_session_id 从流中到达（asyncio.Event）
  → 创建 DB 记录(id=sdk_session_id) + tag_session
  → 响应返回 session_id
  → 前端用 session_id 连接 SSE
```

### DB 模型变更

**`agent_sessions` 表**：

| 字段 | 变更 | 说明 |
|------|------|------|
| `id` | **语义变更** | 改为存储 SDK session_id（原为应用层 UUID） |
| `sdk_session_id` | **移除** | 与 `id` 合并 |
| `title` | **移除写入** | 不再写入；列暂时保留避免迁移，读时忽略 |
| `project_name` | 不变 | SDK `list_sessions` 按 cwd 过滤，但 cwd ≠ project_name，DB 仍需此字段做映射 |
| `status` | 不变 | SDK 不追踪应用层状态，DB 必须保留 |
| `created_at` | 不变 | |
| `updated_at` | 不变 | |

### 标题来源重构

**读取路径**（`list_sessions` API）：

1. DB 查询：按 `project_name` 过滤，得到 `[{id, status, created_at, ...}]`
2. SDK 查询：调用 `list_sessions(directory=project_cwd)` 一次拿到所有会话的 `summary`
3. 合并：按 `session_id` join，将 `summary` 注入返回的 `SessionMeta.title`
4. 无匹配 summary 的记录（SDK 数据已清理等）fallback 到空字符串

**SDK `summary` 的三级降级**（SDK 内部逻辑）：

1. `custom_title`（通过 `rename_session()` 设置）
2. Claude 自动生成的对话摘要
3. `first_prompt`（第一条用户消息）

**写入路径**：无。title 完全由 SDK 管理。

### Tag 标签

在 `sdk_session_id` 首次到达时，调用 `tag_session(sdk_session_id, f"project:{project_name}")`。
当前不用于查询，为将来 SDK 原生按 tag 过滤铺路。

## 详细改动清单

### 后端

#### 移除

- `POST /sessions` 创建端点（`routers/assistant.py`）
- `PATCH /sessions/{session_id}` 改名端点（`routers/assistant.py`）
- `CreateSessionRequest`、`UpdateSessionRequest` 模型
- `AssistantService.create_session()`
- `AssistantService.update_session_title()`
- `SessionManager.create_session()`
- `SessionMetaStore.update_title()`
- `SessionMetaStore.update_sdk_session_id()`
- `SessionRepository.update_title()`
- `SessionRepository.update_sdk_session_id()`
- `SessionMeta.sdk_session_id` 字段（合并到 `id`）
- `ManagedSession.sdk_session_id` 字段（与 `session_id` 统一）

#### 新增/修改

- **`send_message` 端点**：接受可选 `session_id`；无 session_id 时为新会话创建流程
- **`SessionManager.send_message()`**：新会话场景下增加 `asyncio.Event` 等待 sdk_session_id，拿到后创建 DB 记录并返回
- **`SessionManager._maybe_update_sdk_session_id()`** → 重命名为 `_register_new_session()`：
  - 首次拿到 sdk_session_id 时创建 DB 记录（`id=sdk_session_id`）
  - 调用 `tag_session()`
  - set `asyncio.Event` 通知 `send_message` 返回
- **`AssistantService.list_sessions()`**：合并 DB 查询 + SDK `list_sessions()` 注入 summary
- **`SessionMetaStore.create()`**：接受显式 `session_id` 参数（不再自动生成 UUID）

#### DB 迁移

- Alembic 迁移：移除 `sdk_session_id` 列（数据迁移：将现有记录的 `id` 更新为 `sdk_session_id` 值，`sdk_session_id=null` 的记录直接删除）
- `title` 列保留但 server_default 改为空字符串（已经是）

### 前端

#### 移除

- `API.createAssistantSession()` 调用
- `sendMessage` 中的 title 截取逻辑（`content.trim().slice(0, 30)`）

#### 修改

- **`sendMessage`**：draft 模式时 POST /send 不传 session_id，从响应中获取 `session_id`，更新 store
- **`SessionMeta` 类型**：移除 `sdk_session_id` 字段
- **`AgentCopilot.tsx`**：`displayTitle` fallback 链不变（`title || formatTime(created_at)`），title 质量自动提升

### 错误处理

- SDK 连接失败：`send_message` 直接抛异常，无 DB 残留（空会话问题自然消除）
- sdk_session_id 等待超时：设 30 秒超时，超时后清理 SDK 连接并抛错
- `list_sessions` SDK 调用失败：降级为仅返回 DB 数据，title 为空（前端 fallback 到时间戳）

## 向后兼容

- 前端 `POST /sessions` 调用移除后，旧版前端将返回 404；属于强制升级，不做兼容
- DB 迁移会删除 `sdk_session_id=null` 的幽灵记录（即空会话），这是预期行为
- 现有 `sdk_session_id` 不为空的记录，其 `id` 会被替换为 `sdk_session_id`，所有引用旧 app_id 的前端缓存/localStorage 将失效，用户需刷新页面

## 不在本次范围

- 用户手动改名（前端无入口，暂不实现）
- `AssistantMessage.usage` token 追踪
- `RateLimitEvent` 捕获
- `AgentDefinition` 的 `skills`/`memory`/`mcpServers` 声明化配置
