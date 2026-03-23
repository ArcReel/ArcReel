# Claude 子进程内存泄漏修复 — 会话生命周期管理设计

## 背景

Claude SDK 子进程每个占用约 250MB 内存。当前 `SessionManager` 对 `idle` 状态的会话不执行任何清理，导致子进程永驻内存。在多会话场景下内存持续累积，最终 OOM。

### 根因

`session_manager.py` 的 `_finalize_turn()` 中：

```python
if final_status not in ("idle", "running"):
    self._schedule_session_cleanup(managed.session_id)
```

`idle` 状态（正常完成一轮对话）被排除在清理之外。`_schedule_session_cleanup()` 内部也对 `idle` 状态做了二次跳过。结果：idle 会话的 SDK 子进程永远不会被释放。

## 目标

1. idle 会话在可配置的超时后自动释放 SDK 子进程内存
2. 引入最大并发会话上限，防止同时活跃过多子进程
3. 被清理的会话对用户透明恢复（DB 记录保留，再次对话时 `get_or_connect` 重建连接）
4. 超时和并发上限通过智能体配置页可调

## 设计

### 三层防线架构

```
┌─────────────────────────────────────────────────────┐
│  层 1: Idle TTL 定时清理                              │
│  每个会话进入 idle 后启动倒计时（默认 10 分钟）        │
│  到期 → disconnect SDK → 释放内存                     │
│  用户再发消息 → get_or_connect 透明恢复               │
├─────────────────────────────────────────────────────┤
│  层 2: 并发上限 + LRU 淘汰                            │
│  活跃子进程数 ≤ max_concurrent（默认 5）              │
│  新请求到来时，如超限 → 淘汰最久 idle 的会话          │
│  全部 running → 返回 503 友好提示                     │
├─────────────────────────────────────────────────────┤
│  层 3: 定期巡检（安全网）                              │
│  每 5 分钟扫描一次，清理漏网的超时会话                │
│  防止 TTL 计时器丢失导致的泄漏                        │
└─────────────────────────────────────────────────────┘
```

### 层 1：Idle TTL 定时清理

#### ManagedSession 新增字段

```python
idle_since: float | None = None      # monotonic 时间戳，进入 idle 时记录
last_activity: float | None = None   # 每次发送/接收消息时更新
```

#### 触发点：`_finalize_turn()`

当 `final_status == "idle"` 时：

```python
if final_status == "idle":
    managed.idle_since = time.monotonic()
    self._schedule_idle_cleanup(session_id, ttl_seconds)
```

#### `_schedule_idle_cleanup()`

- 延迟 = 从配置读取的 TTL（默认 600 秒 = 10 分钟）
- 到期后检查：如果会话仍为 `idle` **且** `idle_since` 未被刷新 → 执行 `client.disconnect()` + 从 `self.sessions` 移除
- 如果期间用户发了新消息（`idle_since` 已重置为 None），则取消清理

#### 恢复路径

被清理的会话 DB 记录保留（`AgentSession` 行不删除），用户再发消息时走已有的 `get_or_connect()` → 重新创建 `ClaudeSDKClient` → 透明恢复。

### 层 2：并发上限 + LRU 淘汰

#### 检查点

在 `send_new_session()` 和 `get_or_connect()` 中，连接 SDK 客户端**之前**调用 `_ensure_capacity()`。

#### `_ensure_capacity()` 逻辑

```python
async def _ensure_capacity(self) -> None:
    """确保有空余并发槽位，必要时淘汰最久 idle 会话。"""
    max_concurrent = await self._get_max_concurrent()
    active = [s for s in self.sessions.values() if s.client.connected]

    if len(active) < max_concurrent:
        return

    # 找到最久未活跃的 idle 会话
    idle_sessions = sorted(
        [s for s in active if s.status == "idle"],
        key=lambda s: s.last_activity or 0
    )

    if idle_sessions:
        victim = idle_sessions[0]
        await victim.client.disconnect()
        self.sessions.pop(victim.session_id, None)
        return

    # 所有会话都在 running → 拒绝
    raise SessionCapacityError(
        "当前所有智能体会话均在处理中，请稍后再试"
    )
```

#### API 层错误处理

路由层捕获 `SessionCapacityError`，返回：

```json
HTTP 503
{"detail": "当前所有智能体会话均在处理中，请稍后再试"}
```

`SessionCapacityError` 定义为自定义异常，放在 `server/agent_runtime/` 下。

### 层 3：定期巡检

在 `SessionManager` 启动时创建后台 `asyncio.Task`：

```python
async def _patrol_loop(self) -> None:
    """每 5 分钟扫描一次，清理漏网的超时 idle 会话。"""
    while True:
        await asyncio.sleep(300)
        ttl = await self._get_idle_ttl()
        now = time.monotonic()
        for sid, managed in list(self.sessions.items()):
            if managed.status == "idle" and managed.idle_since:
                if now - managed.idle_since > ttl:
                    await managed.client.disconnect()
                    self.sessions.pop(sid, None)
```

在 `shutdown_gracefully()` 中取消此任务。

### 配置读取

SessionManager 新增两个方法，从 `ConfigService` 动态读取（每次读取，配置变更即时生效）：

```python
async def _get_idle_ttl(self) -> int:
    """返回 idle TTL 秒数，默认 600。"""
    val = await self._config_service.get_setting(
        "agent_session_idle_ttl_minutes", "10"
    )
    return int(val) * 60

async def _get_max_concurrent(self) -> int:
    """返回最大并发会话数，默认 5。"""
    val = await self._config_service.get_setting(
        "agent_max_concurrent_sessions", "5"
    )
    return int(val)
```

### 后端配置 API 扩展

#### `SystemConfigPatchRequest` 新增字段

```python
agent_session_idle_ttl_minutes: Optional[int] = None   # 范围 1-60
agent_max_concurrent_sessions: Optional[int] = None     # 范围 1-20
```

#### PATCH 处理

- 范围校验：`1 ≤ idle_ttl ≤ 60`，`1 ≤ max_concurrent ≤ 20`，超出返回 422
- 存储为字符串到 `SystemSetting` 表
- 不需要映射到环境变量（SessionManager 直接通过 ConfigService 读取）

#### GET 响应

新增这两个字段，值从 `ConfigService.get_setting()` 读取，无值时返回默认值（10 和 5）。

### 前端 UI

#### 类型扩展

`SystemConfigSettings` 和 `SystemConfigPatch` 各新增：

```typescript
agent_session_idle_ttl_minutes: number;
agent_max_concurrent_sessions: number;
```

#### AgentConfigTab UI

在现有"模型配置"之后，新增默认折叠的"高级设置"区块：

```
┌─ 智能体配置 ─────────────────────────────────────┐
│  [API 凭证]  Anthropic API Key / Base URL        │
│  [模型配置]  默认模型 + 高级模型路由（折叠）       │
│                                                   │
│  ▶ 高级设置                                       │  ← 默认折叠
│  ┌───────────────────────────────────────────┐    │
│  │  会话空闲超时（分钟）  [  10  ]            │    │
│  │  会话空闲超过此时间后自动释放资源，         │    │
│  │  再次对话时会自动恢复                      │    │
│  │                                           │    │
│  │  最大并发会话数        [   5  ]            │    │
│  │  同时保持活跃的智能体会话上限，超出时       │    │
│  │  自动释放最久未使用的会话（清理的会话       │    │
│  │  会持久化，下次对话时恢复）                 │    │
│  └───────────────────────────────────────────┘    │
│                                                   │
│  [保存]                                           │
└───────────────────────────────────────────────────┘
```

- 输入框 `type="number"`，带 `min`/`max` 约束
- 与现有字段共享同一个"保存"按钮和 `isDirty` 检查
- 不在 `config-status-store` 中添加缺失项检查（有默认值，非必填）

## 涉及文件

| 文件 | 变更 |
|------|------|
| `server/agent_runtime/session_manager.py` | 核心：idle TTL、LRU 淘汰、巡检循环 |
| `server/agent_runtime/service.py` | 注入 ConfigService 依赖 |
| `server/routers/system_config.py` | 新增两个配置字段的 PATCH/GET |
| `server/routers/assistant.py` | 捕获 SessionCapacityError → 503 |
| `server/routers/agent_chat.py` | 捕获 SessionCapacityError → 503 |
| `frontend/src/types/system.ts` | 新增类型字段 |
| `frontend/src/components/pages/AgentConfigTab.tsx` | 高级设置折叠面板 |

## 不变的部分

- `AgentSession` DB 模型不变（无需新增列或迁移）
- `SessionRepository` 不变
- 已有的 `_schedule_session_cleanup()`（处理 completed/error/interrupted）保持不变
- 前端会话列表、对话 UI 不变（清理对用户透明）
