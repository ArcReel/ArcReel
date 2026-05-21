# Agent 对话 Turn 统一规范化设计

## 问题

Agent 对话的加载有 3 种场景：实时对话、历史对话、正在进行的对话重连。由于数据来源不同（JSONL transcript、SDK 内存 buffer、流式 DraftProjector），输出的 Turn 结构存在系统性差异，导致渲染不一致。

### 数据结构差异全景

| 差异维度 | JSONL Transcript | SDK Buffer | Draft (流式) |
|---------|-----------------|------------|-------------|
| `uuid` | 始终存在 | User 有, Assistant/Result **无** | `"draft-{id}"` 合成 |
| `timestamp` | 始终存在 | **始终缺失** | **缺失** |
| `tool_use.result` | 由 turn_grouper 附加 | 由 turn_grouper 附加 | **永远缺失** |
| `tool_use.skill_content` | 由 turn_grouper 附加 | 由 turn_grouper 附加 | **永远缺失** |
| `tool_use.id` | 真实字符串 | 真实字符串 | 初始为 `null` |
| `result` turn | **不存在**（JSONL 无此类型） | 存在 | 不适用 |
| Content 格式 | string 或 array | string 或 array | 始终 array |
| block `type` | 通常存在 | 可能缺失 | 始终存在 |

### 根因

Normalization 散落在 4 处，各自只解决部分问题：
1. `turn_grouper._normalize_block()` — block type 推断
2. `stream_projector._normalize_block()` — 字段默认值
3. `service._build_initial_raw_messages()` — 去重过滤
4. 前端 `ChatMessage.normalizeContent()` — string→array 转换

### 重连消息丢失

`_build_initial_raw_messages()` 在 service.py:451 过滤掉缺少 uuid 的 assistant/result 消息，导致最近一轮尚未写入 JSONL 的 assistant 回复在重连时丢失。

---

## 方案：统一 Projector 内部规范化

从根源让 `turn_grouper` 和 `stream_projector` 共享相同的 normalization 逻辑。

### Turn Contract（输出规范）

```python
Turn = {
    "type": "user" | "assistant" | "system" | "result",
    "content": list[ContentBlock],   # 始终为 array，永不为 string
    "uuid": str | None,
    "timestamp": str | None,
}

ContentBlock = {
    "type": str,                     # 始终存在
    "text": str,                     # Optional, type=text/skill_content
    "thinking": str,                 # Optional, type=thinking
    "id": str | None,                # Optional, type=tool_use（流式初期可为 None）
    "name": str,                     # Optional, type=tool_use（流式初期可为 ""）
    "input": dict,                   # Optional, type=tool_use（始终为 dict）
    "result": str,                   # Optional, type=tool_use（已完成的工具调用）
    "is_error": bool,                # Optional, type=tool_use
    "skill_content": str,            # Optional, type=tool_use 且 name=Skill
}
```

### 共享模块：`turn_schema.py`

新建 `server/agent_runtime/turn_schema.py`，提取共享的规范化逻辑：

```python
def infer_block_type(block: dict) -> str:
    """推断缺失的 block type。"""

def normalize_block(block: dict) -> dict:
    """统一的 block 规范化。"""

def normalize_content(content: Any) -> list[dict]:
    """content 始终转为 list[dict]。"""

def normalize_turn(turn: dict) -> dict:
    """确保 Turn 满足 contract。"""

def normalize_turns(turns: list[dict]) -> list[dict]:
    """批量规范化。"""
```

---

## 实现步骤

### Step 1: 新建 `turn_schema.py`
- 从 `turn_grouper.py` 提取 `_infer_block_type()`、`_normalize_block()`、`_normalize_content()`
- 新增 `normalize_turn()`、`normalize_turns()`

### Step 2: 重构 `turn_grouper.py`
- 删除本地的 `_infer_block_type()`、`_normalize_block()`、`_normalize_content()`
- 改为 `from turn_schema import` 调用
- `group_messages_into_turns()` 输出前对每个 turn 调用 `normalize_turn()`

### Step 3: 重构 `stream_projector.py`
- `DraftAssistantProjector._normalize_block()` 替换为共享实现
- `build_turn()` 输出前调用 `normalize_turn()`
- 保留 `_ensure_block()` 的 streaming 特有逻辑（创建空壳 block）

### Step 4: 修复 `service.py` 重连消息丢失
- 修改 `_build_initial_raw_messages()` 中的过滤逻辑
- 允许 buffer 中位于 transcript 末尾之后的 assistant/result 消息通过
- `build_snapshot()` 和 `_emit_running_snapshot()` 输出前调用 `normalize_turns()` 作为最终关卡

### Step 5: 精简前端冗余 normalization
- `ChatMessage.tsx` 简化 `normalizeContent()`，移除 JSON parse 分支
- `ContentBlockRenderer.tsx` 移除 silent fallback（`block.type || "text"`）
- 可选：dev-only Turn contract 验证

---

## 涉及文件

| 文件 | 操作 | 风险 |
|------|------|------|
| `server/agent_runtime/turn_schema.py` | **新建** | 低 |
| `server/agent_runtime/turn_grouper.py` | 重构（提取→导入） | 中 |
| `server/agent_runtime/stream_projector.py` | 重构（替换 normalize） | 中 |
| `server/agent_runtime/service.py` | 修改过滤逻辑 + 输出规范化 | 中 |
| `frontend/src/components/copilot/chat/ChatMessage.tsx` | 简化 | 低 |
| `frontend/src/components/copilot/chat/ContentBlockRenderer.tsx` | 简化 | 低 |

## 测试策略

- 现有 turn_grouper 测试应继续通过（行为不变，代码位置迁移）
- 新增 `test_turn_schema.py` 覆盖各种输入格式的规范化
- 手动验证三种场景：历史加载、实时流、重连
