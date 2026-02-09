# ClaudeSDKClient 迁移设计方案

## 概述

将项目从 `query()` 迁移到 `ClaudeSDKClient`，实现原生多轮对话支持、后台持续执行、断线重连等特性。

## 设计决策

| 决策点 | 选择 |
|--------|------|
| 迁移目标 | 完全迁移到 ClaudeSDKClient 原生会话 |
| 历史查询 | UI 展示 + 会话恢复（resume） |
| 消息存储 | SDK session_id + 按需读取 transcript |
| 连接生命周期 | 后台持续执行 + 前端断线重连 |
| 重连机制 | 进入页面时立即尝试重连 SSE |
| 服务重启 | 优雅关闭（等待当前 turn 完成） |
| 消息格式 | 前端直接适配 SDK 消息结构，无转换层 |

---

## 架构设计

### 整体架构

```
当前架构:
┌─────────┐    ┌─────────┐    ┌─────────────┐
│ Frontend│───▶│ FastAPI │───▶│ query()     │ ← 每次新 session
└─────────┘    │ + SQLite│    │ + prompt拼接│
               └─────────┘    └─────────────┘

新架构:
┌─────────┐    ┌─────────────────┐    ┌──────────────────┐
│ Frontend│───▶│ FastAPI         │───▶│ ClaudeSDKClient  │
└─────────┘    │ + SessionManager│    │ (后台持续运行)    │
    ▲          └────────┬────────┘    └────────┬─────────┘
    │                   │                      │
    │ SSE 断线重连      │ 元数据存储           │ transcript
    └───────────────────┴──────────────────────┘
```

### 核心组件

| 组件 | 职责 |
|------|------|
| `SessionManager` | 管理所有活跃的 ClaudeSDKClient 实例，处理生命周期 |
| `SessionMetaStore` | SQLite 存储 session 元数据（id、title、project、status、时间戳） |
| `TranscriptReader` | 读取 SDK 的 transcript 文件，原样返回消息列表 |
| `StreamBridge` | 将 ClaudeSDKClient 的消息流桥接到 SSE |

---

## 数据存储设计

### Transcript 统一存储路径

```
projects/.agent_data/
├── transcripts/                    # 所有会话的 transcript
│   ├── {session_id}.json          # SDK 生成的完整对话记录
│   └── ...
├── sessions.db                     # SQLite 元数据
└── checkpoints/                    # 可选：用于 resume 的检查点数据
```

### ClaudeAgentOptions 配置

```python
options = ClaudeAgentOptions(
    cwd=project_path,
    extra_args={
        "--transcript-dir": str(AGENT_DATA_DIR / "transcripts")
    }
)
```

### SessionMetaStore 表结构

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,              -- 我们生成的 session_id（UUID）
    sdk_session_id TEXT,              -- SDK 返回的 session_id（用于 resume）
    project_name TEXT NOT NULL,
    title TEXT DEFAULT '',
    status TEXT DEFAULT 'running',    -- running | completed | error | interrupted
    transcript_path TEXT,             -- 相对路径：transcripts/{id}.json
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_sessions_project ON sessions(project_name, updated_at DESC);
CREATE INDEX idx_sessions_status ON sessions(status);
```

### Docker 容器化映射

```yaml
volumes:
  - ./data/agent_data:/app/projects/.agent_data
```

---

## SessionManager 设计

### 数据结构

```python
class ManagedSession:
    client: ClaudeSDKClient          # SDK 客户端实例
    sdk_session_id: str              # SDK 返回的 session_id
    status: Literal["running", "completed", "error", "interrupted"]
    message_buffer: list[Message]    # 缓存最近的消息（供重连时回放）
    subscribers: set[asyncio.Queue]  # 当前订阅的 SSE 连接

class SessionManager:
    sessions: dict[str, ManagedSession]  # session_id -> ManagedSession

    async def create_session(project_name: str, options: ClaudeAgentOptions) -> str
    async def send_message(session_id: str, content: str) -> None
    async def subscribe(session_id: str) -> asyncio.Queue  # SSE 订阅
    async def unsubscribe(session_id: str, queue: asyncio.Queue) -> None
    async def get_status(session_id: str) -> SessionStatus
    async def shutdown_gracefully() -> None  # 优雅关闭
```

### 后台运行机制

1. `send_message()` 启动一个后台 task 消费 `client.receive_messages()`
2. 消息同时：推送到所有 `subscribers`、缓存到 `message_buffer`
3. 前端断开时只是 `unsubscribe()`，后台 task 继续运行
4. 前端重连时 `subscribe()`，先回放 buffer 中的消息，再接收新消息

### 消息缓存策略

- `message_buffer` 保留最近 100 条消息
- 会话完成后清空 buffer（历史从 transcript 读取）

---

## 消息格式设计

### 后端直接透传 SDK 消息

```python
class TranscriptReader:
    def read_messages(self, session_id: str) -> list[dict]:
        """读取 transcript，原样返回 SDK 消息列表"""
        transcript_path = self.transcripts_dir / f"{session_id}.json"
        with open(transcript_path) as f:
            data = json.load(f)
        return data.get("messages", [])
```

### 前端适配 SDK 消息类型

| SDK 消息类型 | 关键字段 |
|-------------|---------|
| `UserMessage` | `type: "user"`, `content: str \| ContentBlock[]` |
| `AssistantMessage` | `type: "assistant"`, `content: ContentBlock[]`, `model` |
| `SystemMessage` | `type: "system"`, `subtype`, `data` |
| `ResultMessage` | `type: "result"`, `subtype`, `duration_ms`, `total_cost_usd` |

### ContentBlock 类型

| 类型 | 字段 |
|-----|------|
| `TextBlock` | `type: "text"`, `text` |
| `ThinkingBlock` | `type: "thinking"`, `thinking`, `signature` |
| `ToolUseBlock` | `type: "tool_use"`, `id`, `name`, `input` |
| `ToolResultBlock` | `type: "tool_result"`, `tool_use_id`, `content`, `is_error` |

---

## API 接口设计

### REST API

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/v1/sessions` | POST | 创建会话 |
| `/api/v1/sessions` | GET | 列出会话（支持 project_name 过滤） |
| `/api/v1/sessions/{id}` | GET | 获取会话详情（含 status） |
| `/api/v1/sessions/{id}` | PATCH | 更新会话（title） |
| `/api/v1/sessions/{id}` | DELETE | 删除会话 |
| `/api/v1/sessions/{id}/messages` | GET | 获取历史消息（从 transcript 读取） |
| `/api/v1/sessions/{id}/messages` | POST | 发送消息 |
| `/api/v1/sessions/{id}/stream` | GET | SSE 流（订阅实时消息 + 断线重连） |

### 关键接口详情

```python
# POST /api/v1/sessions
Request:  {"project_name": "my_project", "title": ""}
Response: {"id": "uuid", "status": "running", "created_at": "..."}

# GET /api/v1/sessions/{id}/stream
# SSE 事件流，行为：
# 1. 如果 status=running：先回放 buffer，再实时推送
# 2. 如果 status=completed：返回空流（历史从 /messages 获取）
# 3. 事件格式：直接推送 SDK Message JSON

# POST /api/v1/sessions/{id}/messages
Request:  {"content": "用户输入"}
Response: {"status": "accepted"}  # 立即返回，消息通过 SSE 推送
```

### 与现有 API 的变化

- 移除 `/sessions/{id}/streams/{request_id}` —— 简化为单一 `/stream` 端点
- `/messages` GET 从 transcript 读取而非 SQLite

---

## 前端状态管理重构

### 核心状态

```javascript
// 现有状态（保留）
const [sessions, setSessions] = useState([]);
const [currentSessionId, setCurrentSessionId] = useState("");
const [messages, setMessages] = useState([]);        // 存储 SDK 原始消息
const [streamingMessage, setStreamingMessage] = useState(null);  // 当前流式消息
const [input, setInput] = useState("");
const [sending, setSending] = useState(false);

// 新增状态
const [sessionStatus, setSessionStatus] = useState("idle");  // idle | running | completed | error
```

### 进入会话的流程

```
1. 切换到 session_id
      │
2. GET /sessions/{id} 获取状态
      │
      ├─ status=completed ──▶ GET /messages 加载历史
      │
      └─ status=running ──▶ 连接 SSE /stream
                                  │
                           接收消息追加到 messages
                                  │
                           收到 ResultMessage ──▶ status=completed
```

### SSE 重连逻辑

```javascript
const connectStream = useCallback((sessionId) => {
  const source = new EventSource(`/api/v1/sessions/${sessionId}/stream`);

  source.onmessage = (event) => {
    const message = JSON.parse(event.data);
    setMessages(prev => [...prev, message]);

    if (message.type === "result") {
      setSessionStatus("completed");
      source.close();
    }
  };

  source.onerror = () => {
    // 断线后 3 秒重连
    setTimeout(() => connectStream(sessionId), 3000);
  };
}, []);
```

---

## 优雅关闭与会话恢复

### 服务关闭流程

```python
# 注册 shutdown 信号处理
@app.on_event("shutdown")
async def shutdown():
    await session_manager.shutdown_gracefully()

class SessionManager:
    async def shutdown_gracefully(self):
        for session_id, managed in self.sessions.items():
            if managed.status == "running":
                # 1. 等待当前 turn 完成（最多 30 秒）
                try:
                    await asyncio.wait_for(
                        managed.current_turn_task,
                        timeout=30
                    )
                except asyncio.TimeoutError:
                    # 2. 超时则中断
                    await managed.client.interrupt()

                # 3. 更新状态为 interrupted
                managed.status = "interrupted"
                self.meta_store.update_status(session_id, "interrupted")

                # 4. 断开连接
                await managed.client.disconnect()
```

### 服务重启后恢复

```python
class SessionManager:
    async def resume_session(self, session_id: str) -> ManagedSession:
        meta = self.meta_store.get(session_id)

        # 使用 SDK 的 resume 参数恢复会话
        client = ClaudeSDKClient(options=ClaudeAgentOptions(
            resume=meta.sdk_session_id,  # SDK session_id
            cwd=get_project_path(meta.project_name),
            # ... 其他配置
        ))
        await client.connect()

        managed = ManagedSession(client=client, ...)
        self.sessions[session_id] = managed
        return managed
```

### 前端处理 interrupted 状态

```javascript
// 进入会话时
if (session.status === "interrupted") {
  // 显示提示："会话已中断，是否继续？"
  // 用户确认后 POST /messages 触发 resume
}
```

---

## 文件变更清单

### 后端（Python）

| 文件 | 操作 | 说明 |
|------|------|------|
| `webui/server/agent_runtime/session_manager.py` | 新建 | SessionManager + ManagedSession |
| `webui/server/agent_runtime/session_store.py` | 重写 | 简化为 SessionMetaStore |
| `webui/server/agent_runtime/transcript_reader.py` | 新建 | 读取 SDK transcript |
| `webui/server/agent_runtime/service.py` | 重写 | 使用 SessionManager |
| `webui/server/agent_runtime/models.py` | 简化 | 移除 AgentMessage，只保留 Session 元数据 |
| `webui/server/routers/assistant.py` | 重写 | 新 API 结构 |

### 前端（React）

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/api.js` | 更新 | 新 API 端点 |
| `frontend/src/react/hooks/use-assistant-state.js` | 重写 | 新状态管理逻辑 |
| `frontend/src/react/components/chat/ChatMessage.js` | 更新 | 适配 SDK 消息格式 |

---

## 实现顺序

1. **后端核心组件**
   - SessionMetaStore（SQLite 元数据）
   - TranscriptReader（读取 transcript）
   - SessionManager（管理 ClaudeSDKClient）

2. **后端 API**
   - 新路由结构
   - SSE 流式端点

3. **前端适配**
   - API 调用更新
   - 消息渲染组件适配 SDK 格式
   - 状态管理重构

4. **端到端测试**
   - 新建会话
   - 多轮对话
   - 断线重连
   - 会话恢复
