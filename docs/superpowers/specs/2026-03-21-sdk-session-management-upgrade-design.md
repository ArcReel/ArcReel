# SDK 会话管理升级设计

## 背景

项目从 claude-agent-sdk 0.1.48 升级到 0.1.50，利用新增的会话管理 API 简化架构：

- `list_sessions(directory)` — 按 cwd 列出会话，返回 `SDKSessionInfo`（含 `summary` 自动标题）
- `get_session_info(session_id)` — 查询单个会话元数据
- `tag_session(session_id, tag)` — 为会话打标签
- `rename_session(session_id, title)` — 重命名会话

### 当前问题

1. **双 ID 业务耦合**：应用层 `id`（UUID hex）与 SDK 层 `sdk_session_id` 分离，前端/API 路由/内存缓存全部使用 app_id，需要反复查映射
2. **标题管理简陋**：创建时截取用户消息前 30 字符作 title，无自动摘要
3. **空会话 bug**：`create_session` 先创建 DB 记录，若 SDK 连接失败，遗留 `sdk_session_id=null` 的幽灵记录

## 设计目标

- 业务层统一使用 SDK session_id 作为标识（DB 保留 `id` 主键但不再暴露给业务）
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
  → 创建 DB 记录(id=auto, sdk_session_id=xxx) + tag_session
  → 响应返回 sdk_session_id（业务层唯一标识）
  → 前端用 sdk_session_id 连接 SSE
```

### DB 模型变更

**`agent_sessions` 表**：

| 字段 | 变更 | 说明 |
|------|------|------|
| `id` | **降级为内部主键** | 保留自动生成的 UUID，不再暴露给 API/前端 |
| `sdk_session_id` | **升级为业务标识** | 加 UNIQUE + NOT NULL 约束；所有 API 路由、前端、内存缓存统一使用此字段作为 session 标识 |
| `title` | **移除写入** | 不再写入；列暂时保留避免迁移，读时忽略 |
| `project_name` | 不变 | SDK `list_sessions` 按 cwd 过滤，但 cwd ≠ project_name，DB 仍需此字段做映射 |
| `status` | 不变 | SDK 不追踪应用层状态，DB 必须保留 |
| `created_at` | 不变 | |
| `updated_at` | 不变 | |

**关键原则**：`id` 仅用于 DB 内部（主键、索引），所有对外接口（API 路由参数、SSE 事件、前端 store、内存字典 key）统一使用 `sdk_session_id`。`SessionMeta` 模型对外暴露 `id` 字段实际填充 `sdk_session_id` 值。

### 标题来源重构

**读取路径**（`list_sessions` API）：

1. DB 查询：按 `project_name` 过滤，得到 `[{sdk_session_id, status, created_at, ...}]`
2. SDK 查询：调用 `list_sessions(directory=project_cwd, include_worktrees=False)` 一次拿到所有会话的 `summary`（显式禁用 worktree 跨查询，避免多项目 cwd 在同一 git repo 下时互相污染）
3. 合并：按 `session_id` join，将 `summary` 注入返回的 `SessionMeta.title`；SDK 返回但 DB 中不存在的会话忽略（缺少 `project_name` 等 DB 元数据）
4. 无匹配 summary 的记录（SDK 数据已清理等）fallback 到空字符串

**SDK `summary` 的三级降级**（SDK 内部逻辑）：

1. `custom_title`（通过 `rename_session()` 设置）
2. Claude 自动生成的对话摘要
3. `first_prompt`（第一条用户消息）

**写入路径**：无。title 完全由 SDK 管理。

### Tag 标签

在 `sdk_session_id` 首次到达时，调用 `tag_session(sdk_session_id, f"project:{project_name}")`。
注意：`tag_session` 是同步文件 I/O，需用 `asyncio.to_thread()` 包装。
当前不用于查询，为将来 SDK 原生按 tag 过滤铺路。

## 详细改动清单

### 后端

#### 移除

- `POST /sessions` 创建端点（`routers/assistant.py`）
- `POST /sessions/{session_id}/messages` 发送端点（合并到 `POST /sessions/send`）
- `PATCH /sessions/{session_id}` 改名端点（`routers/assistant.py`）
- `CreateSessionRequest`、`UpdateSessionRequest` 模型
- `AssistantService.create_session()`
- `AssistantService.update_session_title()`
- `SessionManager.create_session()`
- `SessionMetaStore.update_title()`
- `SessionRepository.update_title()`

#### 新增/修改

- **`send_message` 端点**：合并为统一的 `POST /sessions/send`，接受 `project_name` + `message` + 可选 `session_id`（body 参数）。移除原 `POST /sessions/{session_id}/messages`。无 `session_id` 时为新会话创建流程，返回 `session_id`（即 sdk_session_id）；有 `session_id` 时为已有会话发送
- **`SessionManager.send_new_session()`**：新会话专用方法。流程：connect → query → 启动 consumer task → await `asyncio.Event`（**10s 超时**）等待 sdk_session_id → 创建 DB 记录 → 返回 sdk_session_id。新会话期间 ManagedSession 先以临时 UUID 为 key 存入 `self.sessions`，拿到 sdk_session_id 后替换 key。超时或错误时清理：cancel consumer task → disconnect client → 从 `self.sessions` 移除临时 key → **返回 HTTP 错误**（前端据此回滚乐观更新）。时序保证：consumer task 在方法返回前已启动，SDK 流事件缓冲在 `message_buffer`（max 100）中，SSE 连接建立后通过 replay_buffer 补发
- **`SessionManager._maybe_update_sdk_session_id()`** → 重命名为 `_register_new_session()`：
  - 首次拿到 sdk_session_id 时创建 DB 记录（`id=auto_uuid, sdk_session_id=xxx`）
  - 调用 `tag_session()`（通过 `asyncio.to_thread`）
  - set `asyncio.Event` 通知 `send_message` 返回
- **`AssistantService.list_sessions()`**：合并 DB 查询 + SDK `list_sessions()` 注入 summary（SDK `list_sessions` 是同步函数，需 `asyncio.to_thread` 包装）
- **`SessionMetaStore`**：`get()`、`update_status()`、`delete()` 改为按 `sdk_session_id` 查找而非 `id`；移除 `update_sdk_session_id()`（新流程创建时直接带 sdk_session_id）
- **`SessionRepository`**：`get()`、`update_status()`、`delete()` 的 WHERE 条件从 `AgentSession.id == x` 改为 `AgentSession.sdk_session_id == x`
- **`_dict_to_session()`**（`session_store.py`）：关键映射点 — `id=d["sdk_session_id"]` 使对外暴露的 `SessionMeta.id` 填充 sdk_session_id 值

#### 业务标识切换（app_id → sdk_session_id）

以下位置的 session 标识从 app `id` 切换为 `sdk_session_id`：

- `service.py`：所有 API 方法的 `session_id` 参数语义变更为 sdk_session_id；`_resolve_sdk_session_id` 移除（session_id 就是 sdk_session_id，无需反查）；`_build_status_event_payload` 中区分两种 ID 的逻辑移除；`_with_session_metadata` 简化
- `session_manager.py`：`sessions` 字典的 key 改用 sdk_session_id；`get_or_connect`、`send_message` 等方法的 `session_id` 参数语义变更
- `sdk_transcript_adapter.py`：`read_raw_messages` 直接用 sdk_session_id（不再需要从 meta 间接获取）
- `models.py`：`SessionMeta` 移除 `sdk_session_id` 字段（对外 `id` 已填充 sdk_session_id 值，由 `_dict_to_session` 映射）；`AssistantSnapshotV2.sdk_session_id` 移除（与 `session_id` 统一）
- `routers/assistant.py`：路由中的 `{session_id}` 参数直接映射到 sdk_session_id

#### DB 迁移

Alembic 迁移：

1. 删除 `sdk_session_id IS NULL` 的幽灵记录
2. 为 `sdk_session_id` 列添加 UNIQUE + NOT NULL 约束（使用 `batch_alter_table`，`render_as_batch=True` 已在 `alembic/env.py` 中配置）
3. ORM 模型 `AgentSession.sdk_session_id` 从 `Optional[str]` 改为 `str`（非空）
4. `title` 列保留但不再写入（server_default 已是空字符串）

### 前端

#### 移除

- `API.createAssistantSession()` 调用
- `API.sendAssistantMessage()` 旧签名（改为统一的 `send` 接口）
- `sendMessage` 中的 title 截取逻辑（`content.trim().slice(0, 30)`）

#### 修改

- **`sendMessage`**：draft 模式时 POST /send 不传 session_id，从响应中获取 `session_id`，更新 store
- **`SessionMeta` 类型**（`types/assistant.ts`）：移除 `sdk_session_id` 字段（后端返回的 `id` 已经是 sdk_session_id）
- **`AssistantSnapshot` 接口**（`types/assistant.ts`）：移除 `sdk_session_id` 字段
- **`AgentCopilot.tsx`**：`displayTitle` fallback 链不变（`title || formatTime(created_at)`），title 质量自动提升
- **测试文件**：所有引用 `sdk_session_id` 的测试需更新（`useAssistantSession.test.tsx`、`stores.test.ts`、`router.test.tsx`、`AgentCopilot.test.tsx` 及后端测试）

### 错误处理

**后端**：
- SDK 连接失败：`send_message` 直接抛异常返回 HTTP 500，无 DB 残留（空会话问题自然消除）
- sdk_session_id 等待超时：设 **10 秒**超时，超时后取消 consumer task、断开 SDK 连接、确保无资源泄漏，返回 HTTP 504
- 已有会话发送失败：返回 HTTP 错误码，前端据此回滚
- `list_sessions` SDK 调用失败：降级为仅返回 DB 数据，title 为空（前端 fallback 到时间戳）

**前端**：
- `sendMessage` 的 `catch` 分支需正确处理新会话创建失败：移除乐观插入的用户消息 turn、恢复 draft 模式、显示错误提示
- 修复现有 bug：当前 SDK 未存储消息但前端乐观显示的情况，通过 send-first 模式 + 错误回滚从根本上解决

## 向后兼容

- 前端 `POST /sessions` 调用移除后，旧版前端将返回 404；属于强制升级，不做兼容
- DB 迁移会删除 `sdk_session_id=null` 的幽灵记录（即空会话），这是预期行为
- 前端缓存/localStorage 中引用旧 app_id 的条目将失效（API 返回的 id 现在是 sdk_session_id），用户需刷新页面

## 不在本次范围

- 用户手动改名（前端无入口，暂不实现）
- `AssistantMessage.usage` token 追踪
- `RateLimitEvent` 捕获
- `AgentDefinition` 的 `skills`/`memory`/`mcpServers` 声明化配置
- SDK summary 的 DB 缓存（已完成会话的 summary 不变，可作后续性能优化）
