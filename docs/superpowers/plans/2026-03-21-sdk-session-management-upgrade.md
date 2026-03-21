# SDK 会话管理升级实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 升级 claude-agent-sdk 到 0.1.50，统一用 sdk_session_id 作为业务标识，用 SDK summary 替代手动标题，消除空会话 bug。

**Architecture:** DB 保留内部 `id` 主键，`sdk_session_id` 升级为 UNIQUE NOT NULL 业务键。所有 API/前端/内存缓存统一使用 sdk_session_id。创建与发送合并为 `POST /sessions/send`，DB 记录仅在 SDK 成功响应后写入。`list_sessions` 合并 DB 查询与 SDK `list_sessions()` 注入 summary 标题。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, claude-agent-sdk 0.1.50, React 19, TypeScript, zustand

**Spec:** `docs/superpowers/specs/2026-03-21-sdk-session-management-upgrade-design.md`

---

## 文件结构

**修改的后端文件：**
- `lib/db/models/session.py` — ORM 模型，`sdk_session_id` 改为非空
- `lib/db/repositories/session_repo.py` — 查询/更新改用 `sdk_session_id` 作为业务键
- `server/agent_runtime/session_store.py` — `SessionMetaStore` 适配新查询键
- `server/agent_runtime/models.py` — `SessionMeta` 和 `AssistantSnapshotV2` 移除 `sdk_session_id` 字段
- `server/agent_runtime/session_manager.py` — 新增 `send_new_session()`，改造 `_maybe_update_sdk_session_id`
- `server/agent_runtime/service.py` — 移除旧方法，新增 `send_or_create()`，重构 `list_sessions`
- `server/routers/assistant.py` — 合并端点为 `POST /sessions/send`
- `server/routers/agent_chat.py` — 适配 send-first 流程

**新增文件：**
- `alembic/versions/xxxx_upgrade_sdk_session_id.py` — DB 迁移脚本

**修改的前端文件：**
- `frontend/src/types/assistant.ts` — 移除 `sdk_session_id` 字段
- `frontend/src/api.ts` — 新增统一 `sendMessage` API，移除旧 API
- `frontend/src/hooks/useAssistantSession.ts` — 适配新 send 流程

**修改的测试文件（后端）：**
- `tests/test_session_repo.py`
- `tests/test_session_meta_store.py`
- `tests/test_session_manager_sdk_session_id.py`
- `tests/test_session_manager_more.py`
- `tests/test_session_manager_user_input.py`
- `tests/test_assistant_router_full.py`
- `tests/test_assistant_service_more.py`
- `tests/test_assistant_service_streaming.py`
- `tests/test_agent_chat_router.py`
- `tests/test_transcript_reader.py`
- `tests/factories.py`
- `tests/fakes.py`

**修改的测试文件（前端）：**
- `frontend/src/hooks/useAssistantSession.test.tsx`
- `frontend/src/stores/stores.test.ts`
- `frontend/src/router.test.tsx`
- `frontend/src/components/copilot/AgentCopilot.test.tsx`
- `frontend/src/api.test.ts`

---

## Task 1: DB 层 — ORM 模型 + Repository 改用 sdk_session_id 查询

**Files:**
- Modify: `lib/db/models/session.py:14-28`
- Modify: `lib/db/repositories/session_repo.py:24-127`
- Test: `tests/test_session_repo.py`

- [ ] **Step 1: 更新 ORM 模型**

`lib/db/models/session.py` — `sdk_session_id` 从 `Optional[str]` 改为 `str`，加 unique 约束：

```python
sdk_session_id: Mapped[str] = mapped_column(String, unique=True)
```

- [ ] **Step 2: Repository — 新增 `get_by_sdk_id` 方法，用于按 sdk_session_id 查找**

`lib/db/repositories/session_repo.py` — 所有业务查询方法（`get`, `update_status`, `delete`）改为按 `sdk_session_id` 过滤。`create` 方法接受 `sdk_session_id` 参数。移除 `update_title` 和 `update_sdk_session_id` 方法。

关键变更点：
- `create()`: 接受 `sdk_session_id: str` 参数，写入记录
- `get()`: WHERE 条件从 `AgentSession.id == session_id` 改为 `AgentSession.sdk_session_id == session_id`
- `update_status()`: 同上
- `delete()`: 同上
- 移除 `update_title()` (lines 101-109)
- 移除 `update_sdk_session_id()` (lines 91-99)

- [ ] **Step 3: 更新 Repository 测试**

`tests/test_session_repo.py` — 更新所有测试用例：
- 创建时传入 `sdk_session_id`
- 查询/更新/删除按 `sdk_session_id` 操作
- 移除 `test_update_title` 和 `test_update_sdk_session_id` 测试

- [ ] **Step 4: 运行测试**

```bash
uv run python -m pytest tests/test_session_repo.py -v
```

- [ ] **Step 5: 提交**

```bash
git add lib/db/models/session.py lib/db/repositories/session_repo.py tests/test_session_repo.py
git commit -m "refactor(db): sdk_session_id 升级为业务键，Repository 改用 sdk_session_id 查询"
```

---

## Task 2: SessionMetaStore + SessionMeta 模型适配

**Files:**
- Modify: `server/agent_runtime/session_store.py:16-97`
- Modify: `server/agent_runtime/models.py:1-30`
- Modify: `tests/factories.py`
- Modify: `tests/fakes.py`
- Test: `tests/test_session_meta_store.py`

- [ ] **Step 1: 更新 SessionMeta 模型**

`server/agent_runtime/models.py` — 移除 `sdk_session_id` 字段，`id` 字段将填充 sdk_session_id 值。同时移除 `AssistantSnapshotV2.sdk_session_id`。

```python
class SessionMeta(BaseModel):
    """Session metadata stored in database."""
    id: str  # 对外暴露，填充 sdk_session_id 值
    project_name: str
    title: str = ""
    status: SessionStatus = "idle"
    created_at: datetime
    updated_at: datetime

class AssistantSnapshotV2(BaseModel):
    """Unified assistant snapshot for history and reconnect."""
    session_id: str
    status: SessionStatus
    turns: list[dict[str, Any]]
    draft_turn: Optional[dict[str, Any]] = None
    pending_questions: list[dict[str, Any]] = Field(default_factory=list)
```

- [ ] **Step 2: 更新 `_dict_to_session` 映射和 SessionMetaStore**

`server/agent_runtime/session_store.py` — 关键映射：`id=d["sdk_session_id"]`。移除 `update_title`、`update_sdk_session_id` 方法。`create` 接受 `sdk_session_id` 参数。所有查询方法参数名从 `session_id` 语义上对应 sdk_session_id。

```python
def _dict_to_session(d: dict) -> SessionMeta:
    return SessionMeta(
        id=d["sdk_session_id"],  # 关键映射：业务 ID = sdk_session_id
        project_name=d["project_name"],
        title=d.get("title") or "",
        status=d["status"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )
```

`create()` 签名变更：

```python
async def create(self, project_name: str, sdk_session_id: str) -> SessionMeta:
```

- [ ] **Step 3: 更新 factories.py 和 fakes.py**

`tests/factories.py` 和 `tests/fakes.py` — 移除 `sdk_session_id` 相关的工厂方法和 fake 实现，适配新签名。

- [ ] **Step 4: 更新 SessionMetaStore 测试**

`tests/test_session_meta_store.py` — 适配新签名，移除 title 和 sdk_session_id 更新测试。

- [ ] **Step 5: 运行测试**

```bash
uv run python -m pytest tests/test_session_meta_store.py tests/test_session_repo.py -v
```

- [ ] **Step 6: 提交**

```bash
git add server/agent_runtime/models.py server/agent_runtime/session_store.py tests/factories.py tests/fakes.py tests/test_session_meta_store.py
git commit -m "refactor(models): SessionMeta 移除 sdk_session_id 字段，id 映射到 sdk_session_id"
```

---

## Task 3: SessionManager — send_new_session + _register_new_session

**Files:**
- Modify: `server/agent_runtime/session_manager.py:54-71,603-606,608-646,648-702,734-761,1106-1134`
- Test: `tests/test_session_manager_sdk_session_id.py`
- Test: `tests/test_session_manager_more.py`

- [ ] **Step 1: 更新 ManagedSession 数据结构**

`session_manager.py` — `ManagedSession.sdk_session_id` 字段移除，新增 `sdk_id_event: asyncio.Event` 和 `project_name: str` 用于新会话注册：

```python
@dataclass
class ManagedSession:
    session_id: str  # 此字段语义改为 sdk_session_id（已有会话）或临时 UUID（新会话等待中）
    client: Any
    status: SessionStatus = "idle"
    project_name: str = ""  # 新增：用于 _register_new_session
    sdk_id_event: asyncio.Event = field(default_factory=asyncio.Event)  # 新增
    resolved_sdk_id: Optional[str] = None  # 新增：consumer 设置，send_new_session 读取
    message_buffer: list[dict[str, Any]] = field(default_factory=list)
    # ... 其余不变
```

- [ ] **Step 2: 移除 `create_session`，新增 `send_new_session`**

移除 `create_session()` (lines 603-606)。新增 `send_new_session()` 方法：

```python
async def send_new_session(
    self,
    project_name: str,
    prompt: Union[str, AsyncIterable[dict]],
    *,
    echo_text: Optional[str] = None,
    echo_content: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Create a new session via send-first: connect SDK, send message, wait for sdk_session_id."""
    temp_id = uuid.uuid4().hex

    options = self._build_options(
        project_name,
        resume_id=None,
        can_use_tool=await self._build_can_use_tool_callback(temp_id),
    )
    client = ClaudeSDKClient(options=options)
    await client.connect()

    managed = ManagedSession(
        session_id=temp_id,
        client=client,
        status="running",
        project_name=project_name,
    )
    self.sessions[temp_id] = managed

    # Echo user message
    display_text = echo_text or (prompt if isinstance(prompt, str) else "")
    dedup_key = display_text or (self._IMAGE_ONLY_SENTINEL if echo_content else "")
    if dedup_key:
        managed.pending_user_echoes.append(dedup_key)
    managed.add_message(self._build_user_echo_message(display_text, echo_content))

    # NOTE: can_use_tool callback 必须捕获 managed 对象引用而非 temp_id，
    # 因为 temp_id 会在 key swap 后失效。_build_can_use_tool_callback
    # 需要改为接受 managed 而非 session_id（详见 Step 2 注意事项）。

    try:
        await managed.client.query(prompt)
    except Exception:
        logger.exception("新会话消息发送失败")
        del self.sessions[temp_id]
        try:
            await client.disconnect()
        except Exception:
            pass
        raise

    managed.consumer_task = asyncio.create_task(self._consume_messages(managed))

    # Wait for sdk_session_id with timeout
    try:
        await asyncio.wait_for(managed.sdk_id_event.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.error("等待 sdk_session_id 超时 temp_id=%s", temp_id)
        managed.cancel_pending_questions("session creation timed out")
        if managed.consumer_task and not managed.consumer_task.done():
            managed.consumer_task.cancel()
            await asyncio.gather(managed.consumer_task, return_exceptions=True)
        del self.sessions[temp_id]
        try:
            await client.disconnect()
        except Exception:
            pass
        raise TimeoutError("SDK 会话创建超时")

    sdk_id = managed.resolved_sdk_id
    assert sdk_id is not None

    # Replace temp key with real sdk_session_id
    del self.sessions[temp_id]
    managed.session_id = sdk_id
    self.sessions[sdk_id] = managed

    return sdk_id
```

- [ ] **Step 3: 改造 `_maybe_update_sdk_session_id` 为 `_register_new_session`**

原方法改为双模式：新会话时注册 DB + set event；已有会话时无操作（sdk_session_id 已确定）。

```python
async def _on_sdk_session_id_received(
    self,
    managed: ManagedSession,
    message: Any,
    msg_dict: dict[str, Any],
) -> None:
    """Handle sdk_session_id from stream. For new sessions: create DB record + signal event."""
    sdk_id = self._extract_sdk_session_id(message, msg_dict)
    if not sdk_id:
        return
    if managed.resolved_sdk_id is not None:
        return  # Already registered

    managed.resolved_sdk_id = sdk_id

    # Only create DB record for new sessions (no existing meta)
    if not managed.sdk_id_event.is_set():
        await self.meta_store.create(managed.project_name, sdk_id)
        try:
            await asyncio.to_thread(tag_session, sdk_id, f"project:{managed.project_name}")
        except Exception:
            logger.warning("tag_session failed for %s", sdk_id, exc_info=True)
        await self.meta_store.update_status(sdk_id, "running")
        managed.sdk_id_event.set()
```

- [ ] **Step 4: 更新 `_consume_messages` 中的调用**

`_consume_messages` (lines 734-761) — 将 `_maybe_update_sdk_session_id` 调用替换为 `_on_sdk_session_id_received`。

- [ ] **Step 5: 更新 `get_or_connect`**

`get_or_connect` — `meta.sdk_session_id` 引用改为 `meta.id`（因为 SessionMeta.id 现在就是 sdk_session_id）。`ManagedSession` 构造时必须设置 `resolved_sdk_id=meta.id`，防止 `_on_sdk_session_id_received` 误判为新会话而重复创建 DB 记录（会触发 UNIQUE 约束错误）。

```python
managed = ManagedSession(
    session_id=meta.id,  # 现在就是 sdk_session_id
    client=client,
    status=meta.status if meta.status != "idle" else "idle",
    project_name=meta.project_name,
    resolved_sdk_id=meta.id,  # 关键：标记为已注册，防止重复创建 DB 记录
)
managed.sdk_id_event.set()  # 已有会话不需要等待
```

- [ ] **Step 6: 更新 `send_message` 方法**

`send_message` — 移除 `await self.meta_store.update_status(session_id, "running")` 中对 session_id 的使用不变（语义已改为 sdk_session_id）。`_finalize_turn` 和 `_mark_session_terminal` 中的 `managed.session_id` 已经是 sdk_session_id。

- [ ] **Step 7: 更新相关测试**

`tests/test_session_manager_sdk_session_id.py` — 测试改为验证 `_on_sdk_session_id_received` 和 `send_new_session` 流程。
`tests/test_session_manager_more.py` — 适配 ManagedSession 字段变更。
`tests/test_session_manager_user_input.py` — 适配字段变更。

- [ ] **Step 8: 运行测试**

```bash
uv run python -m pytest tests/test_session_manager_sdk_session_id.py tests/test_session_manager_more.py tests/test_session_manager_user_input.py -v
```

- [ ] **Step 9: 提交**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_sdk_session_id.py tests/test_session_manager_more.py tests/test_session_manager_user_input.py
git commit -m "feat(session-manager): send_new_session 实现 send-first 会话创建"
```

---

## Task 4: AssistantService — 移除旧方法 + list_sessions 合并 summary

**Files:**
- Modify: `server/agent_runtime/service.py:80-118,178-212,631-720`
- Test: `tests/test_assistant_service_more.py`
- Test: `tests/test_assistant_service_streaming.py`

- [ ] **Step 1: 移除 `create_session` 和 `update_session_title`**

`service.py` — 删除 `create_session()` (lines 80-86) 和 `update_session_title()` (lines 111-118)。

- [ ] **Step 2: 新增 `send_or_create` 方法**

替代原 `send_message`，统一处理新建和已有会话：

```python
async def send_or_create(
    self,
    project_name: str,
    content: str,
    *,
    session_id: Optional[str] = None,
    images: Optional[list["ImageAttachment"]] = None,
) -> dict[str, Any]:
    """Unified send: create new session or send to existing one."""
    self.pm.get_project_path(project_name)  # Validate project

    if session_id:
        # Existing session
        meta = await self.meta_store.get(session_id)
        if meta is None:
            raise FileNotFoundError(f"session not found: {session_id}")
        if meta.project_name != project_name:
            raise FileNotFoundError(f"session not found: {session_id}")
        self._snapshot_cache.pop(session_id, None)
        # Build prompt (same as current send_message logic)
        text, sdk_prompt, echo_blocks = self._prepare_prompt(content, images)
        if sdk_prompt is not None:
            await self.session_manager.send_message(
                session_id, sdk_prompt, echo_text=text, echo_content=echo_blocks, meta=meta
            )
        else:
            await self.session_manager.send_message(session_id, text, meta=meta)
        return {"status": "accepted", "session_id": session_id}
    else:
        # New session
        text, sdk_prompt, echo_blocks = self._prepare_prompt(content, images)
        prompt = sdk_prompt if sdk_prompt is not None else text
        sdk_session_id = await self.session_manager.send_new_session(
            project_name,
            prompt,
            echo_text=text,
            echo_content=echo_blocks,
        )
        return {"status": "accepted", "session_id": sdk_session_id}
```

提取 `_prepare_prompt` 私有方法，复用现有 `send_message` 中构建 multimodal prompt 的逻辑。

- [ ] **Step 3: 重构 `list_sessions` — 合并 SDK summary**

```python
async def list_sessions(
    self,
    project_name: Optional[str] = None,
    status: Optional[SessionStatus] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionMeta]:
    sessions = await self.meta_store.list(
        project_name=project_name, status=status, limit=limit, offset=offset
    )
    if not sessions or not project_name:
        return sessions

    # Inject SDK summary as title
    try:
        project_cwd = str(self.projects_root / project_name)
        sdk_sessions = await asyncio.to_thread(
            sdk_list_sessions, directory=project_cwd, include_worktrees=False
        )
        summary_map = {s.session_id: s.summary for s in sdk_sessions}
    except Exception:
        logger.warning("SDK list_sessions failed, titles will be empty", exc_info=True)
        return sessions

    return [
        SessionMeta(**{**s.model_dump(), "title": summary_map.get(s.id, s.title)})
        for s in sessions
    ]
```

- [ ] **Step 4: 简化 `_resolve_sdk_session_id` → 移除**

`_resolve_sdk_session_id` (lines 691-720) — 整个方法移除。所有调用点改为直接使用 `session_id`（已经就是 sdk_session_id）。

`_build_status_event_payload` (lines 631-666) — 移除双 ID 区分逻辑，简化为直接使用 `session_id`。

`_with_session_metadata` (lines 668-689) — 简化，不再注入 `sdk_session_id` 字段。

- [ ] **Step 5: 更新 `get_session` 和所有 `meta.sdk_session_id` 引用**

所有使用 `meta.sdk_session_id` 的地方改为 `meta.id`（SessionMeta 已无 `sdk_session_id` 字段）：
- `get_snapshot()` 中 `meta.sdk_session_id` → `meta.id`
- `_build_projector()` (line 545) 中传给 `transcript_adapter.read_raw_messages` 的参数：`meta.sdk_session_id` → `meta.id`
- `stream_events()` 中的 `meta.sdk_session_id` → `meta.id`

注意：`sdk_transcript_adapter.py` 本身不需要改（它接受 `sdk_session_id` 字符串参数），但 **service.py 中调用它的地方**必须改。这是 Task 2 移除 `SessionMeta.sdk_session_id` 后的直接依赖。

- [ ] **Step 6: 更新相关测试**

`tests/test_assistant_service_more.py` 和 `tests/test_assistant_service_streaming.py` — 适配新方法签名，移除 `create_session` 和 `update_session_title` 测试。

- [ ] **Step 7: 运行测试**

```bash
uv run python -m pytest tests/test_assistant_service_more.py tests/test_assistant_service_streaming.py -v
```

- [ ] **Step 8: 提交**

```bash
git add server/agent_runtime/service.py tests/test_assistant_service_more.py tests/test_assistant_service_streaming.py
git commit -m "feat(service): send_or_create 统一创建/发送 + list_sessions 注入 SDK summary"
```

---

## Task 5: 路由层 — POST /sessions/send + agent_chat 适配

**Files:**
- Modify: `server/routers/assistant.py:49-192`
- Modify: `server/routers/agent_chat.py:125-192`
- Test: `tests/test_assistant_router_full.py`
- Test: `tests/test_agent_chat_router.py`

- [ ] **Step 1: assistant.py — 新增 `POST /sessions/send`，移除旧端点**

移除：
- `POST /sessions` (`create_session`, lines 71-83)
- `POST /sessions/{session_id}/messages` (`send_message`, lines 177-192)
- `PATCH /sessions/{session_id}` (`update_session`, lines 119-134)
- `CreateSessionRequest`、`UpdateSessionRequest` 模型

新增统一 send 端点：

```python
class SendRequest(BaseModel):
    content: str = ""
    images: list[ImageAttachment] = Field(default_factory=list, max_length=5)
    session_id: Optional[str] = None

@router.post("/sessions/send")
async def send_message(
    project_name: str,
    req: SendRequest,
    _user: Annotated[dict, Depends(get_current_user)],
):
    try:
        service = get_assistant_service()
        result = await service.send_or_create(
            project_name,
            req.content,
            session_id=req.session_id,
            images=req.images,
        )
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="会话或项目不存在")
    except TimeoutError:
        raise HTTPException(status_code=504, detail="SDK 会话创建超时")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=str(exc))
```

其他保留的端点（`GET /sessions`, `GET /sessions/{session_id}`, `DELETE /sessions/{session_id}`, `GET /sessions/{session_id}/snapshot`, `GET /sessions/{session_id}/stream`, `POST /sessions/{session_id}/interrupt`, `POST /sessions/{session_id}/questions/{question_id}/answer`）中的 `{session_id}` 语义自然变为 sdk_session_id（因为前端传的就是 sdk_session_id）。

- [ ] **Step 2: agent_chat.py — 适配 send-first 流程**

`server/routers/agent_chat.py` — 移除 `service.create_session()` 调用，改用 `service.send_or_create()`：

```python
# 替换 lines 146-168:
if body.session_id:
    session = await service.get_session(body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话 '{body.session_id}' 不存在")
    if session.project_name != body.project_name:
        raise HTTPException(
            status_code=400,
            detail=f"会话 '{body.session_id}' 属于项目 '{session.project_name}'，与请求项目 '{body.project_name}' 不符",
        )

try:
    result = await service.send_or_create(
        body.project_name,
        body.message,
        session_id=body.session_id,
    )
    session_id = result["session_id"]
except TimeoutError:
    raise HTTPException(status_code=504, detail="SDK 会话创建超时")
except ValueError as exc:
    raise HTTPException(status_code=409, detail=str(exc))
```

- [ ] **Step 3: 更新路由测试**

`tests/test_assistant_router_full.py` — 移除 `create_session` 和 `update_title` 测试，新增 `POST /sessions/send` 测试。
`tests/test_agent_chat_router.py` — 适配新流程。

- [ ] **Step 4: 运行测试**

```bash
uv run python -m pytest tests/test_assistant_router_full.py tests/test_agent_chat_router.py -v
```

- [ ] **Step 5: 提交**

```bash
git add server/routers/assistant.py server/routers/agent_chat.py tests/test_assistant_router_full.py tests/test_agent_chat_router.py
git commit -m "feat(routes): POST /sessions/send 统一端点 + agent_chat 适配"
```

---

## Task 6: Alembic 迁移

> **部署说明**：生产环境需先运行迁移再部署新代码。迁移会删除 `sdk_session_id IS NULL` 的记录，此操作不可逆。

**Files:**
- Create: `alembic/versions/xxxx_upgrade_sdk_session_id.py`
- Modify: `lib/db/models/session.py` (已在 Task 1 完成)

- [ ] **Step 1: 生成迁移脚本**

```bash
uv run alembic revision --autogenerate -m "sdk_session_id upgrade to unique not null"
```

- [ ] **Step 2: 编辑迁移脚本**

手动编辑生成的迁移脚本，确保：

```python
def upgrade() -> None:
    # 1. 删除 sdk_session_id IS NULL 的幽灵记录
    op.execute("DELETE FROM agent_sessions WHERE sdk_session_id IS NULL")

    # 2. 添加 UNIQUE + NOT NULL 约束
    with op.batch_alter_table("agent_sessions") as batch_op:
        batch_op.alter_column("sdk_session_id", nullable=False, existing_type=sa.String())
        batch_op.create_unique_constraint("uq_agent_sessions_sdk_session_id", ["sdk_session_id"])

def downgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch_op:
        batch_op.drop_constraint("uq_agent_sessions_sdk_session_id", type_="unique")
        batch_op.alter_column("sdk_session_id", nullable=True, existing_type=sa.String())
```

- [ ] **Step 3: 运行迁移**

```bash
uv run alembic upgrade head
```

- [ ] **Step 4: 验证**

```bash
uv run python -c "
from lib.db.engine import sync_engine
from sqlalchemy import inspect
insp = inspect(sync_engine)
cols = {c['name']: c for c in insp.get_columns('agent_sessions')}
print('sdk_session_id nullable:', cols['sdk_session_id']['nullable'])
uniqs = insp.get_unique_constraints('agent_sessions')
print('unique constraints:', uniqs)
"
```

- [ ] **Step 5: 提交**

```bash
git add alembic/versions/
git commit -m "migrate: sdk_session_id 加 UNIQUE NOT NULL 约束，删除幽灵记录"
```

---

## Task 7: 前端 — 类型 + API + sendMessage hook 适配

**Files:**
- Modify: `frontend/src/types/assistant.ts:12-20,63-70`
- Modify: `frontend/src/api.ts:1063-1181`
- Modify: `frontend/src/hooks/useAssistantSession.ts:394-481`

- [ ] **Step 1: 更新 TypeScript 类型**

`frontend/src/types/assistant.ts` — 移除 `sdk_session_id`:

```typescript
export interface SessionMeta {
  id: string;              // 现在就是 sdk_session_id
  project_name: string;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
}

export interface AssistantSnapshot {
  session_id: string;
  status: SessionStatus;
  turns: Turn[];
  draft_turn: Turn | null;
  pending_questions: PendingQuestion[];
}
```

- [ ] **Step 2: 更新 API 层**

`frontend/src/api.ts` — 移除 `createAssistantSession` 和 `updateAssistantSession`，新增统一 `sendAssistantMessage`：

```typescript
// 移除: createAssistantSession (lines 1063-1071)
// 移除: updateAssistantSession (lines 1157-1169)

// 替换 sendAssistantMessage (lines 1103-1116):
static async sendAssistantMessage(
  projectName: string,
  content: string,
  sessionId?: string | null,
  images?: { data: string; media_type: string }[]
): Promise<{ session_id: string; status: string }> {
  return this.request(`${this.assistantBase(projectName)}/sessions/send`, {
    method: "POST",
    body: JSON.stringify({
      content,
      session_id: sessionId || undefined,
      images: images || [],
    }),
  });
}
```

- [ ] **Step 3: 更新 sendMessage hook**

`frontend/src/hooks/useAssistantSession.ts` — 合并创建和发送流程：

替换 lines 407-467 的核心逻辑：

```typescript
// 不再需要先创建会话
// 直接调统一 send 接口
const imagePayload = images?.map((img) => ({
  data: img.dataUrl.split(",")[1] ?? "",
  media_type: img.mimeType,
}));

// 乐观更新 UI
const optimisticContent = [/* ... 同现有逻辑 ... */];
const optimisticTurn = { /* ... 同现有逻辑 ... */ };
store.getState().setTurns([...store.getState().turns, optimisticTurn]);
statusRef.current = "running";
store.getState().setSessionStatus("running");

// 统一发送
const result = await API.sendAssistantMessage(
  projectName!,
  content,
  sessionId,  // null for new session
  imagePayload,
);

const returnedSessionId = result.session_id;

// 新会话：更新 store
if (!sessionId) {
  const newSession: SessionMeta = {
    id: returnedSessionId,
    project_name: projectName!,
    title: "",  // SDK summary 会在 list_sessions 时注入
    status: "running",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  store.getState().setCurrentSessionId(returnedSessionId);
  store.getState().setSessions([newSession, ...store.getState().sessions]);
  store.getState().setIsDraftSession(false);
  saveLastSessionId(projectName!, returnedSessionId);
}

connectStream(returnedSessionId);
```

错误处理 — catch 分支需回滚乐观更新：

```typescript
catch (err) {
  if (pendingSendVersionRef.current !== sendVersion) return;
  store.getState().setError((err as Error).message ?? "发送失败");
  if (sessionId && optimisticUuid) {
    restoreFailedSend(sessionId, optimisticUuid, previousStatus);
  } else {
    // 新会话创建失败：回滚到 draft 模式
    store.getState().setTurns(store.getState().turns.filter(t => t.uuid !== optimisticUuid));
    store.getState().setIsDraftSession(true);
    store.getState().setCurrentSessionId(null);
    statusRef.current = previousStatus ?? "idle";
    store.getState().setSessionStatus(previousStatus ?? "idle");
    store.getState().setSending(false);
  }
}
```

- [ ] **Step 4: 运行前端类型检查和测试**

```bash
cd frontend && pnpm typecheck && pnpm test
```

- [ ] **Step 5: 提交**

```bash
git add frontend/src/types/assistant.ts frontend/src/api.ts frontend/src/hooks/useAssistantSession.ts
git commit -m "feat(frontend): 统一 send 接口 + 移除 sdk_session_id 字段"
```

---

## Task 8: 前端测试修复

**Files:**
- Modify: `frontend/src/hooks/useAssistantSession.test.tsx`
- Modify: `frontend/src/stores/stores.test.ts`
- Modify: `frontend/src/router.test.tsx`
- Modify: `frontend/src/components/copilot/AgentCopilot.test.tsx`
- Modify: `frontend/src/api.test.ts`

- [ ] **Step 1: 修复所有前端测试中的 `sdk_session_id` 引用**

全局搜索替换：移除测试数据中的 `sdk_session_id` 字段。更新使用 `createAssistantSession` 的 mock 为新的 `sendAssistantMessage` 签名。

- [ ] **Step 2: 运行前端测试**

```bash
cd frontend && pnpm test
```

- [ ] **Step 3: 提交**

```bash
cd frontend && git add -A && git commit -m "test(frontend): 适配 sdk_session_id 移除和统一 send 接口"
```

---

## Task 9: 后端剩余测试修复 + 全量测试

**Files:**
- Modify: `tests/test_transcript_reader.py`
- Modify: `tests/conftest.py` (如有需要)

- [ ] **Step 1: 修复剩余后端测试**

检查并修复所有仍引用旧 API 的测试文件。

- [ ] **Step 2: 全量后端测试**

```bash
uv run python -m pytest -v
```

- [ ] **Step 3: 全量前端测试**

```bash
cd frontend && pnpm check
```

- [ ] **Step 4: 提交修复**

```bash
git add tests/ && git commit -m "test: 修复所有后端测试适配新会话管理架构"
```

---

## Task 10: pyproject.toml 版本约束更新（已完成）

> 此任务已在探索阶段通过 `uv add "claude-agent-sdk>=0.1.50"` 完成。

**Files:**
- Modify: `pyproject.toml`

- [x] **Step 1: 更新依赖版本约束**

已在探索阶段通过 `uv add "claude-agent-sdk>=0.1.50"` 更新。

- [ ] **Step 2: 验证安装**

```bash
uv run python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"
```

预期输出：`0.1.50`

- [ ] **Step 3: 提交**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: bump claude-agent-sdk to >=0.1.50"
```
