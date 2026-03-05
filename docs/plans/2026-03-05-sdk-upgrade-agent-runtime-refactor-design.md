# SDK v0.1.46 升级 + Agent Runtime 重构设计

日期: 2026-03-05
状态: 待实施

## 背景

升级 `claude-agent-sdk` 至 v0.1.46，利用两个新 PR 的功能重构 agent_runtime 模块：

- **PR #621**: Typed Task Messages — `TaskStartedMessage`/`TaskProgressMessage`/`TaskNotificationMessage` 作为 `SystemMessage` 子类
- **PR #622**: `get_session_messages()` / `list_sessions()` — 基于 `parentUuid` 链路重建的会话读取

## 实测数据

### `get_session_messages()` 行为

| 维度 | 结果 |
|------|------|
| tool_result user 消息 | **保留** — 不过滤 system-injected user 消息 |
| 分支/sidechain 消息 | **过滤** — 通过 parentUuid 链路重建只保留主线 |
| result 条目 | JSONL 中不存在（SessionManager 管理的会话不写入 result） |
| compaction | **正确处理** — 1487 条目 → 12 条 post-compaction 消息 |
| UUID 可用性 | 所有 SessionMessage 都有 uuid（必填字段） |

### SDK 流式消息 UUID 可用性

| SDK 消息类 | uuid | session_id |
|-----------|:----:|:----------:|
| UserMessage | ❌ | ❌ |
| AssistantMessage | ❌ | ❌ |
| SystemMessage | ❌ | ❌ |
| TaskStartedMessage (PR #621) | ✅ | ✅ |
| TaskProgressMessage (PR #621) | ✅ | ✅ |
| TaskNotificationMessage (PR #621) | ✅ | ✅ |
| StreamEvent | ✅ | ✅ |
| ResultMessage | ❌ | ✅ |

**核心约束**: `UserMessage` 和 `AssistantMessage`（最需要去重的类型）没有 uuid。content-based dedup 不可消除，但可简化。

## 设计方案：多 Pass 管线 + SDK 整合 + 务实去重

### 1. TranscriptReader → SdkTranscriptAdapter

**替换** `transcript_reader.py` 为 `sdk_transcript_adapter.py`。

```python
class SdkTranscriptAdapter:
    """用 SDK get_session_messages() 替代手写 JSONL 解析。"""

    def read_raw_messages(self, sdk_session_id: str) -> list[dict]:
        if not sdk_session_id:
            return []
        try:
            sdk_messages = get_session_messages(sdk_session_id)
        except Exception:
            return []
        return [self._adapt(msg) for msg in sdk_messages]

    def _adapt(self, msg: SessionMessage) -> dict:
        result = {
            "type": msg.type,  # "user" | "assistant"
            "content": msg.message.get("content", ""),
            "uuid": msg.uuid,
        }
        if msg.parent_tool_use_id:
            result["parent_tool_use_id"] = msg.parent_tool_use_id
        return result

    def exists(self, sdk_session_id: str) -> bool:
        if not sdk_session_id:
            return False
        try:
            messages = get_session_messages(sdk_session_id, limit=1)
            return len(messages) > 0
        except Exception:
            return False
```

**优势对比**:
- 链路重建（parentUuid）：正确处理分支对话、compaction ← TranscriptReader 的线性读取不处理
- 过滤 sidechain/branch 消息 ← TranscriptReader 全部包含
- 减少约 180 行自维护代码

**注意事项**:
- `get_session_messages()` 是同步函数。当前 TranscriptReader 也是同步调用（`_build_projector` 中），不引入新问题
- 它不返回 result 消息。JSONL 中也没有 result 条目，所以不存在功能退化
- Skill content 消息在非主分支上可能被过滤（实测 2/6 被过滤的消息是 skill content）。这对 turn grouping 影响很小，因为这些 skill content 本身就是分支上的冗余内容

### 2. turn_grouper 多 Pass 重构

将 `group_messages_into_turns` 从单函数拆分为多 pass 管线。

#### Pass 1: Classify（分类）

识别每条消息的语义类型。**保留** `_is_system_injected_user_message` 和 `_has_subagent_user_metadata`，因为 SDK 不过滤 tool_result user 消息。

```python
def classify_message(msg: dict) -> str:
    """返回: real_user | system_inject | assistant | task_progress | result"""
    msg_type = msg.get("type", "")
    if msg_type == "assistant":
        return "assistant"
    if msg_type == "result":
        return "result"
    if msg_type == "system":
        subtype = msg.get("subtype", "")
        if subtype in ("task_started", "task_progress", "task_notification"):
            return "task_progress"
        return "system_other"  # compact_boundary 等，忽略
    if msg_type == "user":
        content = msg.get("content", "")
        if _is_system_injected_user_message(content) or _has_subagent_user_metadata(msg):
            return "system_inject"
        return "real_user"
    return "ignore"
```

#### Pass 2: Pair（配对）

将 tool_result/skill_content/task_progress 附加到对应的 assistant blocks。

- `tool_result` → 匹配 `tool_use.id` 并附加 result/is_error（逻辑不变）
- `skill_content` → 附加到最近的 Skill tool_use block（逻辑不变）
- `task_progress` → 转换为 `task_progress` block 附加到当前 assistant turn（新增）

#### Pass 3: Group（分组）

连续 assistant 合并，real_user 开始新 turn。

**消除 result turn**: result 消息仅 flush current_turn，不创建独立 turn。

```python
if classification == "result":
    if current_turn:
        turns.append(current_turn)
        current_turn = None
    continue  # 不创建 result turn
```

**安全性**: JSONL 中不存在 result 条目，result 只出现在 runtime buffer 中。前端 result turn 没有任何渲染内容，移除无 UI 影响。

### 3. 去重策略简化

**现有复杂度**: ~100 行（`_build_seen_sets` + `_content_key` + `_is_duplicate` + `_should_skip_local_echo` + round-scoping）

**简化为**: ~50 行

```python
def _build_projector(self, meta, session_id, replayed_messages=None):
    # Step 1: SDK 读取 transcript（正确链、全有 UUID）
    transcript_msgs = self._adapter.read_raw_messages(meta.sdk_session_id)

    # Step 2: UUID 集合
    transcript_uuids = {m["uuid"] for m in transcript_msgs if m.get("uuid")}

    # Step 3: 当前轮次内容指纹（最后一个 real_user 之后的消息）
    tail_fps = self._fingerprint_tail(transcript_msgs)

    # Step 4: 初始化投影器
    projector = AssistantStreamProjector(initial_messages=transcript_msgs)

    # Step 5: 应用 buffer 并去重
    buffer = replayed_messages or self.session_manager.get_buffered_messages(session_id)
    for msg in buffer:
        if not isinstance(msg, dict):
            continue
        msg_type = msg.get("type", "")

        # 非 groupable 直接通过
        if msg_type not in {"user", "assistant", "result"}:
            projector.apply_message(msg)
            continue

        # ① UUID 去重
        uuid = msg.get("uuid")
        if uuid and uuid in transcript_uuids:
            continue

        # ② Local echo 去重
        if msg.get("local_echo") and self._echo_in_transcript(msg, transcript_msgs):
            continue

        # ③ 内容指纹去重（兜底）
        if not uuid and msg_type in {"assistant", "result"}:
            fp = self._fingerprint(msg)
            if fp and fp in tail_fps:
                continue

        projector.apply_message(msg)

    return projector
```

**被消除的复杂度**:

| 被删除 | 原因 |
|--------|------|
| `_build_seen_sets` (last_user_idx 追踪) | SDK 返回的 user 都是真实用户，`_fingerprint_tail` 直接找最后一个 user |
| `_content_key` (MD5 哈希 thinking blocks) | 替换为 `_fingerprint`：truncate 到 200 字符即可 |
| Round-scoping (`seen_content_keys.clear()`) | 自然限定到 tail（最后一个 user 之后的消息） |
| `_is_system_injected_user_message` 在去重中的调用 | 不需要 — SDK 的 user 都是真实用户，无需判断 |

**保留的逻辑**:

| 保留 | 说明 |
|------|------|
| UUID 集合去重 | 主路径，不变 |
| Local echo 去重 | 简化 — 只在 transcript 中搜索匹配文本 |
| 内容指纹去重 | 仅 assistant/result，truncate 替代 MD5 |

### 4. PR #621: 子代理任务进度

#### 后端消息处理

在 `session_manager.py` 的 `_MESSAGE_TYPE_MAP` 中新增映射：

```python
_MESSAGE_TYPE_MAP = {
    "UserMessage": "user",
    "AssistantMessage": "assistant",
    "ResultMessage": "result",
    "SystemMessage": "system",
    "StreamEvent": "stream_event",
    "TaskStartedMessage": "system",
    "TaskProgressMessage": "system",
    "TaskNotificationMessage": "system",
}
```

在 `_message_to_dict` 中注入精确 subtype：

```python
_TASK_MESSAGE_SUBTYPES = {
    "TaskStartedMessage": "task_started",
    "TaskProgressMessage": "task_progress",
    "TaskNotificationMessage": "task_notification",
}

def _message_to_dict(self, message):
    msg_dict = self._serialize_value(message)
    if isinstance(msg_dict, dict) and "type" not in msg_dict:
        msg_type = self._infer_message_type(message)
        if msg_type:
            msg_dict["type"] = msg_type
    # 注入 typed task message subtype
    class_name = type(message).__name__
    subtype = self._TASK_MESSAGE_SUBTYPES.get(class_name)
    if subtype and isinstance(msg_dict, dict):
        msg_dict["subtype"] = subtype
    return msg_dict
```

#### turn_grouper 处理

在 Pass 2 (Pair) 中，task_progress 类型消息转换为 block：

```python
task_progress_block = {
    "type": "task_progress",
    "task_id": msg.get("task_id"),
    "status": msg.get("subtype"),  # task_started | task_progress | task_notification
    "description": msg.get("description", ""),
    "summary": msg.get("summary"),       # TaskNotificationMessage
    "task_status": msg.get("status"),     # completed | failed | stopped
    "usage": msg.get("usage"),            # TaskUsage dict
}
```

附加到当前 assistant turn 的 content 中。如果没有当前 assistant turn，创建一个 type="system" turn。

#### 前端渲染

新增 `TaskProgressBlock` 组件：

```
ContentBlock.type 新增: "task_progress"

TaskProgressBlock 渲染:
  task_started  → "🔄 子任务开始: {description}"
  task_progress → "⏳ {description} (tokens: {usage.total_tokens})"
  task_notification(completed) → "✅ 子任务完成: {summary}"
  task_notification(failed)    → "❌ 子任务失败: {summary}"
```

### 5. 前端类型变更

```typescript
// Turn.type: 移除 "result"
export interface Turn {
  type: "user" | "assistant" | "system";
  content: ContentBlock[];
  uuid?: string;
  timestamp?: string;
}

// ContentBlock.type: 新增 "task_progress"
export interface ContentBlock {
  type: "text" | "thinking" | "tool_use" | "tool_result" | "skill_content" | "task_progress";
  // ... existing fields ...
  // 新增 task_progress 字段
  task_id?: string;
  status?: string;
  description?: string;
  summary?: string;
  task_status?: string;
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number };
}
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `pyproject.toml` | 修改 | ✅ 已完成：`>=0.1.44` → `>=0.1.46` |
| `server/agent_runtime/transcript_reader.py` | 删除 | 替换为 sdk_transcript_adapter.py |
| `server/agent_runtime/sdk_transcript_adapter.py` | 新建 | SDK get_session_messages() 封装 |
| `server/agent_runtime/turn_grouper.py` | 重构 | 多 Pass 管线 + 消除 result turn + task_progress 处理 |
| `server/agent_runtime/session_manager.py` | 修改 | _MESSAGE_TYPE_MAP 新增 + _message_to_dict 增强 |
| `server/agent_runtime/service.py` | 修改 | 替换 TranscriptReader 引用 + 简化去重 |
| `server/agent_runtime/models.py` | 修改 | SessionStatus 保持不变 |
| `frontend/src/types/assistant.ts` | 修改 | Turn.type 移除 result + ContentBlock 新增 task_progress |
| `frontend/src/components/copilot/chat/ContentBlockRenderer.tsx` | 修改 | 新增 task_progress case |
| `frontend/src/components/copilot/chat/TaskProgressBlock.tsx` | 新建 | 子代理进度渲染组件 |
| `tests/test_turn_grouper.py` | 修改 | 新增 multi-pass 测试 + task_progress 测试 |
| `tests/test_sdk_transcript_adapter.py` | 新建 | SdkTranscriptAdapter 单元测试 |

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| SDK `get_session_messages()` 过滤了主分支上有意义的 skill content | 实测仅 2/6 被过滤且都是分支冗余内容；即使偶尔过滤，turn grouping 不受影响（skill content 仅作装饰性附加） |
| content-based dedup 的 truncate 策略可能误匹配 | 限定到当前轮次 tail，collision 概率极低；仅用于 UUID 缺失的 buffer 消息 |
| 消除 result turn 可能影响前端逻辑 | 实测 result turn 无渲染内容；前端只需移除 type 判断，无功能退化 |
| `get_session_messages()` 同步阻塞事件循环 | 现有 TranscriptReader 也是同步调用，不引入新问题；后续可包装 run_in_executor |
