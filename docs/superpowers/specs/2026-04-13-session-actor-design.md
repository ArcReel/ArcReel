# Session Actor 重构设计

- **Issue**：[#159](https://github.com/Pollo3470/ArcReel/issues/159) refactor: SessionManager 引入 Actor 模式，解决 SDK cancel scope 跨 task 问题
- **分支**：`refactor/session-actor-159`
- **日期**：2026-04-13

## 1. 背景

`SessionManager` 当前直接持有 `ClaudeSDKClient` 实例，将其 `connect / query / receive_response / interrupt / disconnect` 方法散落在多个不同 asyncio task 中调用。这违反 Claude Agent SDK 的契约：

> "You cannot use a `ClaudeSDKClient` instance across different async runtime contexts. The client internally maintains a persistent anyio task group for reading messages that remains active from `connect()` until `disconnect()`."
> — `claude_agent_sdk/client.py`

### 当前 task 分布

| 操作 | 当前执行 task |
|---|---|
| `client.connect()` | HTTP handler task（`send_new_session` / `get_or_connect`） |
| `client.query()` | HTTP handler task（`send_new_session` / `send_message` / `answer_user_question`） |
| `client.receive_response()` | 后台 `consumer_task`（由 `asyncio.create_task(_consume_messages)` 创建） |
| `client.interrupt()` | HTTP handler task（`interrupt_session`）或 cleanup task（`_disconnect_session_inner`） |
| `client.disconnect()` | cleanup task（由 `_cleanup_task` / 容量淘汰 / 主动 delete 触发） |

SDK 内部的 `Query` 在 `start()` 时创建 `anyio.create_task_group()` 并进入其 cancel scope，`close()` 时退出。当 `connect` 与 `disconnect` 发生在不同 asyncio task 时，anyio 抛出：

```
Attempted to exit cancel scope in a different task than it was entered in
```

### 现有 workaround（本次一并移除）

- SIGTERM / SIGKILL 兜底链（`_force_close_client_process`）
- 通过私有属性 `client._transport._process` 读取 PID（`_get_client_process` / `_process_pid`）
- 独立的 `consumer_task`（与 SDK 内部 `_tg` 并行管理 anyio/asyncio 两套任务）

## 2. 目标与非目标

**目标**

- 让每个会话的所有 SDK 方法调用严格发生在**同一个 asyncio task** 内，消除 cancel-scope 跨 task 异常的根因
- 一次性移除 SIGTERM/SIGKILL fallback、SDK 私有属性访问、`consumer_task` 三类 workaround
- 保持 `AssistantService` 对外 8 个方法的签名、返回值、SSE 事件格式、`status` 字段取值集合零变更
- 单元测试锁定 "SDK 方法同 task" 契约，避免未来退化

**非目标**

- 不扩展 SDK 命令范围（仅封装项目当前使用的 `connect / query / receive_response / interrupt / disconnect`；`set_model / set_permission_mode / rewind_files` 等未用方法不在本次封装列表）
- 不引入 feature flag 双轨；一次性替换，依靠测试覆盖兜底
- 不调整 SSE 事件格式、`StreamProjector` 逻辑、`session_store` 持久化层
- 不处理多进程 / 多实例场景（项目当前为单进程运行）

## 3. 关键决策

| # | 决策 | 理由摘要 |
|---|---|---|
| D1 | Actor 主循环采用 **`asyncio.wait(FIRST_COMPLETED)` 交织消息消费与命令接收** | 让 interrupt 不被长时间的 `receive_response` 阻塞；所有 SDK 调用在同一 task |
| D2 | Actor 作为 **`ManagedSession` 的内部组件**（方案 B），仅封装 SDK 协议；buffer / subscribers / pending_questions / status 仍留在 `ManagedSession` | 职责分离；SDK 约束只绑定客户端资源本身；SSE 订阅读路径避免跨 task 通信 |
| D3 | 保留 **asyncio 级 cancel 超时**（15s 兜底），**删除进程级 force-kill** 和 SDK 私有属性访问 | 不再对抗 SDK；子进程回收交给 SDK `__aexit__` 与 asyncio 标准行为 |
| D4 | **一次性替换** + 外部零感知 | `AssistantService` 接口稳定；删除旧 workaround 代码无双轨负担 |
| D5 | 升级 `claude-agent-sdk` 至 `>=0.1.58` | 文档明确 "interrupt 后继续 drain 直到 `ResultMessage(subtype="error_during_execution")`" 语义，避免自行取消 `receive_response` |

## 4. 分层架构

```
┌──────────────────────────────────────────────┐
│  SessionManager                              │  注册表 + 容量/cleanup 策略
│    sessions: dict[str, ManagedSession]       │  send_new_session / close_session / _evict_one
└──────────────────────────────────────────────┘
                   │ 持有 (1 : N)
                   ▼
┌──────────────────────────────────────────────┐
│  ManagedSession                              │  会话聚合（业务视图）
│    status / message_buffer / subscribers     │  add_message / broadcast / snapshot
│    pending_questions / _cleanup_task         │  send_query / send_interrupt / send_disconnect
│    actor: SessionActor  ─────┐               │  （三个方法是对 actor 的代理）
└──────────────────────────────│───────────────┘
                               │ 持有 (1 : 1)
                               ▼
┌──────────────────────────────────────────────┐
│  SessionActor                                │  SDK 协议适配器（单 task 资源）
│    _cmd_queue: asyncio.Queue[SessionCommand] │  start / enqueue
│    _on_message: Callable[[dict], None]       │  _run → async with ClaudeSDKClient 主循环
│    _task: asyncio.Task                       │
└──────────────────────────────────────────────┘
                               │
                               ▼
                    ClaudeSDKClient (>=0.1.58)
```

依赖方向严格单向：`SessionManager → ManagedSession → SessionActor → ClaudeSDKClient`。反向数据流通过 `_on_message` 回调函数传递（Actor 不依赖 `ManagedSession` 类型）。

## 5. SessionActor 设计

### 5.1 命令协议

```python
@dataclass
class SessionCommand:
    type: Literal["query", "interrupt", "disconnect"]
    prompt: str | AsyncIterable[dict] | None = None
    session_id: str = "default"
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: BaseException | None = None
```

调用方 `await cmd.done.wait()` 等待回执；异常通过 `error` 字段回传，避免等待者无限挂起。

### 5.2 主循环骨架

```python
class SessionActor:
    def __init__(
        self,
        options: ClaudeAgentOptions,
        on_message: Callable[[dict], None],
    ):
        self._options = options
        self._on_message = on_message
        self._cmd_queue: asyncio.Queue[SessionCommand] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._started = asyncio.Event()
        self._fatal: BaseException | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="session-actor")
        started_task = asyncio.create_task(self._started.wait())
        try:
            await asyncio.wait(
                {started_task, self._task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            started_task.cancel()
        if self._fatal:
            raise self._fatal

    async def _run(self) -> None:
        try:
            async with ClaudeSDKClient(self._options) as client:
                self._started.set()
                await self._command_loop(client)
        except BaseException as exc:
            self._fatal = exc
            raise
        finally:
            # 无论正常退出（disconnect）还是异常退出，都把队列残留命令 drain，
            # 避免 race 中在 _fatal 检查之后 put 进来的命令永久挂起。
            self._drain_pending_commands(self._fatal or _ActorClosed())

    async def enqueue(self, cmd: SessionCommand) -> None:
        if self._fatal is not None or (self._task and self._task.done()):
            cmd.error = self._fatal or _ActorClosed()
            cmd.done.set()
            return
        await self._cmd_queue.put(cmd)

    def _drain_pending_commands(self, exc: BaseException) -> None:
        while not self._cmd_queue.empty():
            cmd = self._cmd_queue.get_nowait()
            cmd.error = exc
            cmd.done.set()


class _ActorClosed(Exception):
    """Sentinel: actor 已退出（正常或异常），队列中剩余命令均以此标记为 error。"""
```

**要点**：
- `async with ClaudeSDKClient` 完全位于 `_run` 这一个 task 内，`connect` 与 `disconnect` 同 task 成立。
- `start()` 用 wait-any 语义等 "connect 成功" 或 "actor fail-fast"，调用方能第一时间拿到 connect 失败异常。
- `_fatal` 记录致命异常；`_drain_pending_commands` 把异常传给所有等待者。

### 5.3 `_command_loop` —— 串行命令处理

```python
async def _command_loop(self, client: ClaudeSDKClient) -> None:
    deferred_cmd: SessionCommand | None = None
    while True:
        cmd = deferred_cmd or await self._cmd_queue.get()
        deferred_cmd = None

        if cmd.type == "disconnect":
            cmd.done.set()
            return  # 触发 __aexit__，同 task disconnect

        if cmd.type == "query":
            try:
                await client.query(cmd.prompt, session_id=cmd.session_id)
                deferred_cmd = await self._drive_query(client, cmd)
            except BaseException as exc:
                cmd.error = exc
                cmd.done.set()
                raise

        elif cmd.type == "interrupt":
            # 当前无 query 进行中；interrupt 无操作，但仍 ACK
            cmd.done.set()
```

### 5.4 `_drive_query` —— 消息与命令并发等待

```python
async def _drive_query(
    self, client: ClaudeSDKClient, query_cmd: SessionCommand
) -> SessionCommand | None:
    """在同一 task 内交织消费 receive_response 与新命令。返回需要下一轮处理的命令。"""
    msg_iter = client.receive_response().__aiter__()
    msg_task = asyncio.create_task(msg_iter.__anext__(), name="actor-recv")
    cmd_task = asyncio.create_task(self._cmd_queue.get(), name="actor-cmd")
    try:
        while True:
            done, _ = await asyncio.wait(
                {msg_task, cmd_task}, return_when=asyncio.FIRST_COMPLETED
            )

            if msg_task in done:
                try:
                    self._on_message(msg_task.result())
                    msg_task = asyncio.create_task(msg_iter.__anext__())
                except StopAsyncIteration:
                    query_cmd.done.set()
                    # cmd_task 若已完成 → 携带命令给下一轮；未完成 → cancel 释放
                    if cmd_task.done():
                        return cmd_task.result()
                    cmd_task.cancel()
                    return None

            if cmd_task in done:
                next_cmd = cmd_task.result()
                if next_cmd.type == "interrupt":
                    await client.interrupt()
                    next_cmd.done.set()
                    # SDK 将在短时间内产出 ResultMessage(error_during_execution)
                    # 循环继续消费至 StopAsyncIteration
                    cmd_task = asyncio.create_task(self._cmd_queue.get())
                elif next_cmd.type == "disconnect":
                    await client.interrupt()  # 让消息流收尾
                    return next_cmd           # 携带到 _command_loop 下一轮退出
                elif next_cmd.type == "query":
                    # 违反 "drain before new query"；保守策略：携带到下一轮
                    # ManagedSession 层保证不会在 running 状态下再发 query
                    return next_cmd
    finally:
        if not msg_task.done():
            msg_task.cancel()
        if not cmd_task.done():
            cmd_task.cancel()
```

**为什么这样设计**：

- `asyncio.wait(FIRST_COMPLETED)` 让 "消息到达" 与 "新命令到达" 同权竞争。interrupt 不会被 `receive_response` 阻塞，即便 LLM 长时间思考也能穿插。
- `client.interrupt()` 发生在 actor 主 task 内，完全符合 SDK 同 task 契约。
- interrupt 后**不取消** `receive_response`。依据 `docs/claude-agent-sdk-docs/python.md` 第 615-619 行、633-635 行：SDK 会自行以 `ResultMessage(subtype="error_during_execution")` 收尾。若强行取消会丢失尾部消息，且下一条 `query` 的响应将与被中断的消息混流。

### 5.5 待处理命令语义

`_drive_query` 的返回值标识 "已从队列取出但未在本次 query 范围内消化" 的命令。`_command_loop` 下一轮使用 `deferred_cmd` 消化，避免 "peek 但不 pop" 的复杂性。

## 6. ManagedSession 设计

### 6.1 数据结构变化

```python
@dataclass
class ManagedSession:
    session_id: str
    project_name: str
    actor: SessionActor                            # 替换原 `client: ClaudeSDKClient`
    status: Literal["running", "idle", "interrupted", "error", "closed"]
    message_buffer: deque[dict]                    # maxsize=100，critical 优先
    subscribers: set[asyncio.Queue[dict]]
    pending_questions: dict[str, PendingQuestion]
    _cleanup_task: asyncio.Task | None = None
    _interrupting: bool = False
    # 移除字段：client、consumer_task
```

### 6.2 消息回调：Actor → ManagedSession

```python
def _on_actor_message(self, msg: dict) -> None:
    """SessionActor 的 on_message 回调。同步，内存操作，不 await。"""
    msg_type = msg.get("type")

    if msg_type == "ask_user_question":
        self._register_pending_question(msg)

    if msg_type == "result":
        subtype = msg.get("subtype")
        if subtype == "error_during_execution":
            self.status = "interrupted"
        elif subtype == "success":
            self.status = "idle"
        elif subtype and subtype.startswith("error"):
            self.status = "error"

    self.add_message(msg)   # 既有逻辑：buffer + broadcast
```

**回调必须同步**：actor 主 task 正是回调的调用者，任何 `await` 都会挂住整条消息循环。`add_message` 本身为 O(1) 内存操作（deque append + `put_nowait` 到订阅者），不 await。"订阅者队列满→critical 强制插入+驱逐非 critical" 策略保留。

### 6.3 对外代理方法

```python
async def send_query(
    self, prompt: str | AsyncIterable[dict], sdk_session_id: str = "default"
) -> None:
    self.status = "running"
    cmd = SessionCommand(type="query", prompt=prompt, session_id=sdk_session_id)
    await self.actor.enqueue(cmd)
    await cmd.done.wait()
    if cmd.error:
        self.status = "error"
        raise cmd.error

async def send_interrupt(self) -> None:
    if self._interrupting:
        return
    self._interrupting = True
    try:
        cmd = SessionCommand(type="interrupt")
        await self.actor.enqueue(cmd)
        await cmd.done.wait()    # actor 发信号后立即 ACK
    finally:
        self._interrupting = False
    # status 由 _on_actor_message 在收到 ResultMessage(error_during_execution) 时推导

async def send_disconnect(self) -> None:
    cmd = SessionCommand(type="disconnect")
    await self.actor.enqueue(cmd)
    await cmd.done.wait()                     # actor 已消费 disconnect 命令
    # 等 actor task 真正结束（__aexit__ 完成），保证 "closed" 状态与资源释放对齐
    if self.actor._task is not None:
        with contextlib.suppress(BaseException):
            await self.actor._task
    self.status = "closed"
```

`send_disconnect` 的总耗时 = `client.__aexit__` 耗时。在上层 `_evict_one` 用 `asyncio.wait_for(..., timeout=15.0)` 包装后，超时即触发 cancel 兜底（见 6.5）。

### 6.4 状态机

| 目标状态 | 触发源 | 触发位置 |
|---|---|---|
| `running` | `send_query` 入队前 | 代理方法 |
| `idle` | 收到 `ResultMessage(subtype="success")` | `_on_actor_message` |
| `interrupted` | 收到 `ResultMessage(subtype="error_during_execution")` | `_on_actor_message` |
| `error` | 收到 `ResultMessage(subtype~"error_*")` 或 `cmd.error` 非空 | `_on_actor_message` / 代理方法 |
| `closed` | `send_disconnect` 完成 | 代理方法 |

状态转换从 "散落在 consumer_task / cleanup / interrupt 多处直接赋值" 收窄为 "发起命令 + 消息回调" 两类触发源。前端看到的状态与 SDK 消息流严格对齐（旧代码中 `interrupted` 状态早于 drain 结束导致的漂移消失）。

### 6.5 容量淘汰与 idle cleanup

```python
# SessionManager 侧
async def _evict_one(self, victim: ManagedSession) -> None:
    try:
        await asyncio.wait_for(victim.send_disconnect(), timeout=15.0)
    except asyncio.TimeoutError:
        if victim.actor._task and not victim.actor._task.done():
            victim.actor._task.cancel()
            with contextlib.suppress(BaseException):
                await victim.actor._task
        victim.status = "closed"
    finally:
        self.sessions.pop(victim.session_id, None)

async def _cleanup_idle(self, session_id: str) -> None:
    managed = self.sessions.get(session_id)
    if managed and managed.status in ("idle", "interrupted", "error"):
        await self._evict_one(managed)
```

容量淘汰与 idle 超时统一走 `_evict_one`。**不再调用** `_force_close_client_process` 与 `_get_client_process`。

## 7. 异常、关停、Q&A、对外契约

### 7.1 Actor 异常传播

- `_run` 捕获所有 `BaseException`，写入 `_fatal`，调用 `_drain_pending_commands` 把异常写入所有等待者的 `cmd.error` 并 `done.set()`
- `ManagedSession` 额外注册 actor task 的 `add_done_callback`。actor 异常结束时立刻：
  - 把 session 状态切 `error`
  - 通过现有 "runtime_status" 消息通道通知订阅者
- 没有任何调用方会因 actor 死亡而无限挂起

### 7.2 `shutdown_gracefully`

```python
async def shutdown_gracefully(self, timeout: float = 30.0) -> None:
    sessions = list(self.sessions.values())
    await asyncio.gather(
        *[self._evict_one(s) for s in sessions],
        return_exceptions=True,
    )
```

比旧代码简化：不再区分 running / 非 running 分支，统一走 `_evict_one`（其内部已有 15s 超时 + cancel 兜底）。

### 7.3 Q&A 与 `answer_user_question`

用户答案本质上是 "下一个 user message"，在 actor 模式下直接复用 `send_query` 命令：

```python
async def answer_user_question(
    self, managed: ManagedSession, question_id: str, answer: str
) -> None:
    question = managed.pending_questions.pop(question_id, None)
    if question is None:
        raise KeyError(f"unknown question: {question_id}")
    prompt = _build_answer_prompt(question, answer)
    await managed.send_query(prompt)
```

不新增 actor 命令类型。`pending_questions` 的登记由 `_on_actor_message`，移除由 `answer_user_question`。

### 7.4 AssistantService 对外契约

| 方法 | 签名 | 返回语义 | 改动 |
|---|---|---|---|
| `send_or_create` | 不变 | 不变 | 内部路径替换 |
| `list_sessions` | 不变 | 不变 | 无 |
| `get_session` | 不变 | 不变 | 无 |
| `delete_session` | 不变 | 不变 | 走 `_evict_one` |
| `get_snapshot` | 不变 | 不变 | 无 |
| `stream_events` | 不变 | 不变 | SSE 订阅链路完整保留 |
| `answer_user_question` | 不变 | 不变 | 走 `send_query` |
| `interrupt_session` | 不变 | `status="interrupted"` 的切换时机从 "发 interrupt 信号后立即" 精确前移到 "收到 `ResultMessage(error_during_execution)`"（语义修正，非 breaking） | 内部路径替换 |

前端 SSE 事件格式、路由响应格式、`status` 字段取值集合完全一致。

## 8. 删除清单

| 删除目标 | 原因 |
|---|---|
| `_get_client_process` / `_process_pid` | 不再访问 SDK 私有属性 |
| `_force_close_client_process` | asyncio cancel + OS 回收替代 SIGTERM/SIGKILL |
| `_cancel_task` 辅助函数 | `_evict_one` 的 `task.cancel()` 直接替代 |
| `_consume_messages` | actor 主循环替代 |
| `ManagedSession.client` | 被 `ManagedSession.actor` 替代 |
| `ManagedSession.consumer_task` | 被 actor 主 task 替代 |
| `_disconnect_session_inner` 中与 consumer_task / interrupt timeout / process kill 相关的约 100 行 | 被 `_evict_one` 取代 |

## 9. 测试策略

### 9.1 新增：SessionActor 单元测试（L1）

文件：`tests/test_session_actor.py`（新增）

`FakeSDKClient` 能力升级（`tests/fakes.py`）：
- 记录每个 SDK 方法被调用时的 `asyncio.current_task()`
- 可注入 "长阻塞消息" 行为模拟慢 LLM
- 可注入 "interrupt 后 `ResultMessage(error_during_execution)` 到达" 行为

关键测试用例：

| 测试 | 验证点 |
|---|---|
| `test_actor_all_sdk_calls_same_task` | `connect/query/interrupt/disconnect` 记录的 `current_task()` 完全相同（契约锁定） |
| `test_interrupt_during_long_query_is_immediate` | query 进行中，100ms 后送 interrupt，验证 `client.interrupt()` 在 <50ms 被调用 |
| `test_drain_after_interrupt` | interrupt 后 fake 返回 `ResultMessage(error_during_execution)`，验证 actor 自然收尾、status 切 `interrupted` |
| `test_disconnect_during_query_defers_exit` | query 中途送 disconnect → actor 先 interrupt、drain 完、再走 `__aexit__` |
| `test_actor_error_propagates_to_waiter` | `client.query` 抛异常 → 等待者 `cmd.error` 非空 |
| `test_actor_fatal_clears_pending_queue` | `_run` 异常退出时，队列中所有未处理命令全部 `done.set()` 且 `error` 非空 |
| `test_two_queries_queued_during_interrupt_drain` | interrupt drain 期间新 query 排队，drain 完成后按序执行 |

### 9.2 改造：ManagedSession / SessionManager 测试（L2-L3）

- `test_session_manager_more.py` 的 35 个用例保留。驱动方式从 "等 consumer_task 消费" 改为 "直接调用 `managed._on_actor_message(msg_dict)`"，断言意图不变。
- 新增状态机迁移表的穷举测试（5 个状态 × 所有触发事件）。
- 新增 `test_evict_timeout_falls_back_to_cancel`：注入 "disconnect 永不返回" 的 FakeSDKClient，验证 15s 超时后 actor task 被 cancel、session 从注册表移除。
- `test_session_lifecycle.py` 中与 SIGTERM/SIGKILL fallback 相关的断言随删除清单一并去除。

### 9.3 回归锚点：AssistantService / SSE（L4）

- `test_assistant_service_streaming.py` **一行不改**，作为 "对外契约未漂移" 的最强证据。
- `test_session_lifecycle.py` 的路由-级端到端断言保留。

### 9.4 人工验证基准（PR 合并前）

开发环境手跑三个场景，确认与 0.8.1 行为一致：

1. **长任务 + interrupt**：创建会话，提问 "写一个 500 行的 Python 游戏"，中途点 interrupt。观察前端状态切换、消息流收尾、再发新 query 响应正常。
2. **idle cleanup**：创建会话后闲置 300s+。后台日志无 `SIGTERM` 告警（因为代码已删除），session 从 `list_sessions` 消失。
3. **服务优雅关停**：带 3 个活跃会话 `Ctrl+C` 停止 `uvicorn`。所有会话 disconnect 日志完整，无 30s 后仍未退出的卡死。

### 9.5 覆盖率

`server/agent_runtime/session_actor.py` 与 `session_manager.py` 单文件覆盖率 **>90%**（CI 既定全局阈值 ≥80%）。

## 10. SDK 升级说明

- `pyproject.toml`：`claude-agent-sdk>=0.1.51` → `>=0.1.58`
- 通过 `uv lock --upgrade-package claude-agent-sdk` 锁定；`uv sync` 已完成
- 核心收益：0.1.58 文档明确 "interrupt 后 drain 至 `ResultMessage(subtype="error_during_execution")`" 语义（`docs/claude-agent-sdk-docs/python.md` 第 594-635 行），使 `_drive_query` 在 interrupt 时无需取消 `receive_response`
- 项目中仅 `session_manager.py:853 / 951` 两处调用 `client.connect()`，均不带 prompt 参数，不受 0.1.52 的 "`connect(prompt=...)` 静默丢失" 修复影响
- 回归：`uv run python -m pytest tests/test_session_manager_more.py` 35 个测试全部通过

## 11. 文件改动范围

| 文件 | 改动类型 |
|---|---|
| `pyproject.toml` / `uv.lock` | 升级 SDK（已完成） |
| `server/agent_runtime/session_actor.py` | **新增** |
| `server/agent_runtime/session_manager.py` | 重写 `send_new_session` / `send_message` / `interrupt_session` / cleanup 路径；删除 workaround 相关函数 |
| `tests/fakes.py` | `FakeSDKClient` 升级（记录 task、注入行为） |
| `tests/test_session_actor.py` | **新增** |
| `tests/test_session_manager_more.py` | 改造消息驱动方式；删除 SIGTERM 相关断言 |
| `tests/test_session_lifecycle.py` | 删除 force-kill 相关断言 |
| `server/agent_runtime/service.py` | 不变 |
| `server/agent_runtime/stream_projector.py` | 不变 |
| `server/agent_runtime/session_store.py` | 不变 |
| `server/routers/assistant.py` | 不变 |
| 前端 | 不变 |
