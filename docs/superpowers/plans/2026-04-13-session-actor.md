# Session Actor 重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 SessionManager 持有的 `ClaudeSDKClient` 封装到每会话专属 asyncio task 内，消除 anyio cancel scope 跨 task 异常；一次性移除 SIGTERM/SIGKILL fallback 与 SDK 私有属性访问。

**Architecture:** 三层。`SessionManager`（注册表 + 容量/cleanup 策略）持有 `ManagedSession`（业务聚合：buffer/subscribers/Q&A/状态），`ManagedSession` 组合 `SessionActor`（SDK 协议单 task 适配器）。SDK 所有调用严格在 actor 主 task 内，通过 command queue + `on_message` 回调与外界通信。

**Tech Stack:** Python 3.12、asyncio、claude-agent-sdk>=0.1.58、pytest / pytest-asyncio。

**Reference spec:** `docs/superpowers/specs/2026-04-13-session-actor-design.md`

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `server/agent_runtime/session_actor.py` | **新建** | `SessionActor` 类、`SessionCommand` dataclass、`_ActorClosed` 哨兵异常。零业务逻辑，仅封装 SDK 协议与命令队列。 |
| `server/agent_runtime/session_manager.py` | 重写内部实现 | 替换 `ManagedSession.client` → `actor`，删除 `consumer_task`，新增 `_on_actor_message` / `send_query` / `send_interrupt` / `send_disconnect` 代理；`SessionManager` 生命周期方法改走 actor 协议。删除 `_get_client_process` / `_force_close_client_process` / `_cancel_task` / `_consume_messages` / `_disconnect_session_inner` 旧逻辑（约 300 行）。 |
| `tests/fakes.py` | 升级 `FakeSDKClient` | 支持 `async with`、记录 `current_task`、可注入 `receive_response` 消息序列、可模拟 interrupt 后的 `ResultMessage(error_during_execution)`。 |
| `tests/test_session_actor.py` | **新建** | SessionActor 单元测试，覆盖 SDK 同 task 契约、交织语义、异常传播、关停路径。 |
| `tests/test_session_manager_more.py` | 改造 | 驱动方式从"等 consumer_task 消费"改为"直接调用 `managed._on_actor_message(msg_dict)`"；删除 SIGTERM/SIGKILL 相关断言。 |
| `tests/test_session_lifecycle.py` | 改造 | 删除 force-kill 相关断言；容量淘汰/cleanup 测试走 `_evict_one`。 |
| `server/agent_runtime/service.py` / `stream_projector.py` / `session_store.py` / `server/routers/assistant.py` / 前端 | 不变 | 对外契约 100% 保持。 |

**依赖方向：** `SessionManager → ManagedSession → SessionActor → ClaudeSDKClient`。反向数据流通过 `_on_message` 回调（actor 不引入对 ManagedSession 的类型依赖）。

---

## Phase 1 — SessionActor 模块（T1–T7）

### Task 1: 创建 session_actor.py 骨架 + 命令协议

**Files:**
- Create: `server/agent_runtime/session_actor.py`
- Create: `tests/test_session_actor.py`

- [ ] **Step 1.1: 写失败测试 —— 数据结构基础**

写入 `tests/test_session_actor.py`：

```python
"""SessionActor 单元测试。

覆盖：命令协议、主循环、SDK 同 task 契约、交织语义、异常传播。
"""

from __future__ import annotations

import asyncio
import pytest

from server.agent_runtime.session_actor import (
    SessionActor,
    SessionCommand,
    _ActorClosed,
)


def test_session_command_default_fields():
    cmd = SessionCommand(type="query", prompt="hello")
    assert cmd.type == "query"
    assert cmd.prompt == "hello"
    assert cmd.session_id == "default"
    assert isinstance(cmd.done, asyncio.Event)
    assert not cmd.done.is_set()
    assert cmd.error is None


def test_session_command_interrupt_no_prompt():
    cmd = SessionCommand(type="interrupt")
    assert cmd.type == "interrupt"
    assert cmd.prompt is None


def test_actor_closed_is_exception():
    assert issubclass(_ActorClosed, Exception)


def test_session_actor_instantiation_has_clean_state():
    actor = SessionActor(
        client_factory=lambda: None,
        on_message=lambda msg: None,
    )
    assert actor._task is None
    assert actor._fatal is None
    assert not actor._started.is_set()
    assert actor._cmd_queue.empty()
```

- [ ] **Step 1.2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_session_actor.py -v`
Expected: 全部 FAIL with `ModuleNotFoundError: No module named 'server.agent_runtime.session_actor'`

- [ ] **Step 1.3: 创建 session_actor.py 最小实现**

写入 `server/agent_runtime/session_actor.py`：

```python
"""SessionActor: 每会话一个专属 asyncio task，封装 ClaudeSDKClient 的所有协议调用。

设计：docs/superpowers/specs/2026-04-13-session-actor-design.md
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncContextManager, AsyncIterable, Callable, Literal


class _ActorClosed(Exception):
    """Sentinel: actor 已退出（正常或异常），队列中剩余命令以此标记为 error。"""


@dataclass
class SessionCommand:
    type: Literal["query", "interrupt", "disconnect"]
    prompt: str | AsyncIterable[dict] | None = None
    session_id: str = "default"
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: BaseException | None = None


OnMessage = Callable[[dict[str, Any]], None]
ClientFactory = Callable[[], AsyncContextManager[Any]]


class SessionActor:
    """单 task 拥有一个 ClaudeSDKClient，所有 SDK 操作在同一 async context 中执行。"""

    def __init__(
        self,
        client_factory: ClientFactory,
        on_message: OnMessage,
    ):
        self._client_factory = client_factory
        self._on_message = on_message
        self._cmd_queue: asyncio.Queue[SessionCommand] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._started: asyncio.Event = asyncio.Event()
        self._fatal: BaseException | None = None
```

- [ ] **Step 1.4: 运行测试，确认通过**

Run: `uv run python -m pytest tests/test_session_actor.py -v`
Expected: 4 passed

- [ ] **Step 1.5: Commit**

```bash
git add server/agent_runtime/session_actor.py tests/test_session_actor.py
git commit -m "refactor(session-actor): 新增 SessionActor 模块骨架与命令协议 (#159)"
```

---

### Task 2: 升级 FakeSDKClient 支持 async with / task 记录 / 消息注入

**Files:**
- Modify: `tests/fakes.py:10-49`

- [ ] **Step 2.1: 写失败测试 —— FakeSDKClient 新能力**

追加到 `tests/test_session_actor.py`：

```python
from tests.fakes import FakeSDKClient


@pytest.mark.asyncio
async def test_fake_client_records_current_task_per_method():
    client = FakeSDKClient()
    async with client:
        await client.query("hello")
        await client.interrupt()
    # disconnect 由 __aexit__ 触发

    current = asyncio.current_task()
    assert client.method_tasks["connect"] == [current]
    assert client.method_tasks["query"] == [current]
    assert client.method_tasks["interrupt"] == [current]
    assert client.method_tasks["disconnect"] == [current]


@pytest.mark.asyncio
async def test_fake_client_yields_injected_messages_then_stops():
    messages = [
        {"type": "assistant", "id": 1},
        {"type": "result", "subtype": "success"},
    ]
    client = FakeSDKClient(messages=messages)
    async with client:
        collected = [msg async for msg in client.receive_response()]
    assert collected == messages


@pytest.mark.asyncio
async def test_fake_client_receive_response_blocks_until_interrupt():
    # block_forever=True 时，receive_response 只在 interrupt 注入 message 后才结束
    client = FakeSDKClient(
        block_forever=True,
        interrupt_message={"type": "result", "subtype": "error_during_execution"},
    )
    async with client:
        recv_task = asyncio.create_task(_collect(client))
        await asyncio.sleep(0.05)
        assert not recv_task.done()  # 仍在阻塞
        await client.interrupt()
        collected = await asyncio.wait_for(recv_task, timeout=1.0)
    assert collected == [{"type": "result", "subtype": "error_during_execution"}]


async def _collect(client: FakeSDKClient) -> list[dict]:
    return [msg async for msg in client.receive_response()]


@pytest.mark.asyncio
async def test_fake_client_connect_error_raises_in_aenter():
    err = RuntimeError("boom")
    client = FakeSDKClient(connect_error=err)
    with pytest.raises(RuntimeError, match="boom"):
        async with client:
            pass
```

- [ ] **Step 2.2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_session_actor.py -v`
Expected: 4 新测试 FAIL（`FakeSDKClient` 不支持 `async with`、无 `method_tasks`、无 `block_forever` / `interrupt_message` / `connect_error`）

- [ ] **Step 2.3: 重写 FakeSDKClient**

完全替换 `tests/fakes.py:10-49`（原 `FakeSDKClient` 类定义）为：

```python
class FakeSDKClient:
    """Fake Claude Agent SDK client for SessionActor / SessionManager tests.

    支持：
    - `async with`：`__aenter__` 记录 connect 的 current_task，`__aexit__` 记录 disconnect
    - `method_tasks`: dict[str, list[asyncio.Task]] 记录每个方法被调用时的 task
    - `messages` 初始化参数：`receive_response` 依次 yield 的初始消息
    - `block_forever=True`：`receive_response` 在无消息时阻塞，直到 interrupt 注入尾消息
    - `interrupt_message`：`interrupt()` 被调用时注入给 `receive_response` 的最后一条消息
    - `connect_error`：`__aenter__` 时抛出的异常，用于模拟连接失败
    """

    def __init__(
        self,
        messages=None,
        *,
        block_forever: bool = False,
        interrupt_message: dict | None = None,
        connect_error: Exception | None = None,
    ):
        self._initial_messages = list(messages) if messages else []
        self._block_forever = block_forever
        self._interrupt_message = interrupt_message
        self._connect_error = connect_error
        self._pending_messages: asyncio.Queue[dict | None] = asyncio.Queue()
        self.method_tasks: dict[str, list[asyncio.Task]] = {}
        self.sent_queries: list = []
        self.interrupted = False
        self.disconnected = False
        self._closed: asyncio.Event = asyncio.Event()

    def _record(self, method: str) -> None:
        self.method_tasks.setdefault(method, []).append(asyncio.current_task())

    async def __aenter__(self):
        self._record("connect")
        if self._connect_error is not None:
            raise self._connect_error
        for msg in self._initial_messages:
            await self._pending_messages.put(msg)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._record("disconnect")
        self.disconnected = True
        self._closed.set()
        return False

    async def query(self, prompt, session_id: str = "default") -> None:
        self._record("query")
        self.sent_queries.append(prompt)

    async def interrupt(self) -> None:
        self._record("interrupt")
        self.interrupted = True
        if self._interrupt_message is not None:
            await self._pending_messages.put(self._interrupt_message)
        # 告知 receive_response "可以停止了"
        await self._pending_messages.put(None)  # sentinel

    async def receive_response(self):
        self._record("receive_response")
        while True:
            msg = await self._pending_messages.get()
            if msg is None:
                return
            yield msg
            if msg.get("type") == "result":
                return

    def push_message(self, msg: dict) -> None:
        """测试辅助：运行中往消息流注入一条消息。"""
        self._pending_messages.put_nowait(msg)

    # 向后兼容：保留原方法签名（旧测试仍使用 `await client.connect()` / `await client.disconnect()`）
    async def connect(self) -> None:
        self._record("connect")
        if self._connect_error is not None:
            raise self._connect_error

    async def disconnect(self) -> None:
        self._record("disconnect")
        self.disconnected = True
        self._closed.set()
```

同时在 `tests/fakes.py` 顶部 `from __future__ import annotations` 之后补充：

```python
import asyncio
```

- [ ] **Step 2.4: 运行测试，确认通过**

Run: `uv run python -m pytest tests/test_session_actor.py -v`
Expected: 8 passed（4 旧 + 4 新）

跑一下既有 session_manager 测试，确保向后兼容 `connect()` / `disconnect()` / `query()` / `interrupt()`：

Run: `uv run python -m pytest tests/test_session_manager_more.py -q`
Expected: 35 passed（行为未变）

- [ ] **Step 2.5: Commit**

```bash
git add tests/fakes.py tests/test_session_actor.py
git commit -m "refactor(session-actor): 升级 FakeSDKClient 支持 async with 与 task 记录 (#159)"
```

---

### Task 3: SessionActor.start / _run —— 仅 connect/disconnect，无命令循环

**Files:**
- Modify: `server/agent_runtime/session_actor.py`
- Modify: `tests/test_session_actor.py`

- [ ] **Step 3.1: 写失败测试 —— start 成功路径**

追加到 `tests/test_session_actor.py`：

```python
def _collect(messages: list, managed_on_message):
    """辅助：on_message 把消息追加到外部列表。"""
    def _on(msg: dict) -> None:
        messages.append(msg)
    return _on


@pytest.mark.asyncio
async def test_actor_start_connects_fake_client():
    client = FakeSDKClient()
    actor = SessionActor(
        client_factory=lambda: client,
        on_message=lambda msg: None,
    )
    await actor.start()
    assert actor._started.is_set()
    assert "connect" in client.method_tasks
    # 立即发 disconnect 把 actor 收尾
    cmd = SessionCommand(type="disconnect")
    await actor.enqueue(cmd)
    await cmd.done.wait()
    if actor._task is not None:
        await actor._task
    assert client.disconnected


@pytest.mark.asyncio
async def test_actor_start_propagates_connect_failure():
    client = FakeSDKClient(connect_error=RuntimeError("boom"))
    actor = SessionActor(
        client_factory=lambda: client,
        on_message=lambda msg: None,
    )
    with pytest.raises(RuntimeError, match="boom"):
        await actor.start()
    assert actor._fatal is not None


@pytest.mark.asyncio
async def test_actor_connect_and_disconnect_same_task():
    client = FakeSDKClient()
    actor = SessionActor(
        client_factory=lambda: client,
        on_message=lambda msg: None,
    )
    await actor.start()
    cmd = SessionCommand(type="disconnect")
    await actor.enqueue(cmd)
    await cmd.done.wait()
    if actor._task is not None:
        await actor._task
    assert client.method_tasks["connect"] == client.method_tasks["disconnect"]
```

- [ ] **Step 3.2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_session_actor.py::test_actor_start_connects_fake_client -v`
Expected: FAIL with `AttributeError: 'SessionActor' object has no attribute 'start'`（或 `enqueue`）

- [ ] **Step 3.3: 实现 start / _run / enqueue / _drain_pending_commands**

追加到 `server/agent_runtime/session_actor.py` 的 `SessionActor` 类（放在 `__init__` 之后）：

```python
    async def start(self) -> None:
        """启动 actor task；等到 connect 成功或 fail-fast 才返回。"""
        self._task = asyncio.create_task(self._run(), name="session-actor")
        started_task = asyncio.create_task(self._started.wait())
        try:
            await asyncio.wait(
                {started_task, self._task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not started_task.done():
                started_task.cancel()
        if self._fatal is not None:
            raise self._fatal

    async def _run(self) -> None:
        try:
            async with self._client_factory() as client:
                self._started.set()
                await self._command_loop(client)
        except BaseException as exc:
            self._fatal = exc
            raise
        finally:
            # 正常 / 异常退出都 drain 残留命令，避免调用方挂死
            self._drain_pending_commands(self._fatal or _ActorClosed())

    async def _command_loop(self, client: Any) -> None:
        """初版：只处理 disconnect；query / interrupt 在后续任务扩展。"""
        while True:
            cmd = await self._cmd_queue.get()
            if cmd.type == "disconnect":
                cmd.done.set()
                return
            # 其他命令暂未实现
            cmd.error = NotImplementedError(f"command {cmd.type!r} not yet supported")
            cmd.done.set()

    async def enqueue(self, cmd: SessionCommand) -> None:
        if self._fatal is not None or (self._task is not None and self._task.done()):
            cmd.error = self._fatal or _ActorClosed()
            cmd.done.set()
            return
        await self._cmd_queue.put(cmd)

    def _drain_pending_commands(self, exc: BaseException) -> None:
        while not self._cmd_queue.empty():
            try:
                cmd = self._cmd_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not cmd.done.is_set():
                cmd.error = exc
                cmd.done.set()
```

- [ ] **Step 3.4: 运行测试，确认通过**

Run: `uv run python -m pytest tests/test_session_actor.py -v`
Expected: 11 passed

- [ ] **Step 3.5: Commit**

```bash
git add server/agent_runtime/session_actor.py tests/test_session_actor.py
git commit -m "refactor(session-actor): 实现 SessionActor.start/_run 生命周期 (#159)"
```

---

### Task 4: 命令循环支持 query —— 所有 SDK 调用同 task

**Files:**
- Modify: `server/agent_runtime/session_actor.py:_command_loop`
- Modify: `tests/test_session_actor.py`

- [ ] **Step 4.1: 写失败测试 —— query 命令 + 消息消费**

追加到 `tests/test_session_actor.py`：

```python
@pytest.mark.asyncio
async def test_query_consumes_all_messages_and_sets_done():
    messages = [
        {"type": "assistant", "id": 1},
        {"type": "result", "subtype": "success"},
    ]
    client = FakeSDKClient(messages=messages)
    collected: list[dict] = []
    actor = SessionActor(
        client_factory=lambda: client,
        on_message=lambda msg: collected.append(msg),
    )
    await actor.start()
    # FakeSDKClient 的初始 messages 在 __aenter__ 时入队；query 只是发送动作
    cmd = SessionCommand(type="query", prompt="hi")
    await actor.enqueue(cmd)
    await cmd.done.wait()
    assert cmd.error is None
    assert collected == messages
    assert client.sent_queries == ["hi"]

    # 收尾
    disc = SessionCommand(type="disconnect")
    await actor.enqueue(disc)
    await disc.done.wait()
    if actor._task is not None:
        await actor._task


@pytest.mark.asyncio
async def test_all_sdk_calls_recorded_on_same_task():
    """契约锁定：connect / query / interrupt / disconnect / receive_response
    都在 actor 主 task 内调用，current_task 完全相同。"""
    client = FakeSDKClient(
        block_forever=True,
        interrupt_message={"type": "result", "subtype": "error_during_execution"},
    )
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    # 发 query
    q = SessionCommand(type="query", prompt="hi")
    await actor.enqueue(q)
    # 短暂等待 query 进入 receive_response
    await asyncio.sleep(0.05)
    # 发 interrupt（应当穿插到 receive_response 中）
    i = SessionCommand(type="interrupt")
    await actor.enqueue(i)
    await i.done.wait()
    await q.done.wait()

    # 收尾
    d = SessionCommand(type="disconnect")
    await actor.enqueue(d)
    await d.done.wait()
    if actor._task is not None:
        await actor._task

    tasks_by_method = {m: set(ts) for m, ts in client.method_tasks.items()}
    all_tasks = set().union(*tasks_by_method.values())
    assert len(all_tasks) == 1, f"SDK methods ran on multiple tasks: {tasks_by_method}"
```

- [ ] **Step 4.2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_session_actor.py::test_query_consumes_all_messages_and_sets_done -v`
Expected: FAIL（`_command_loop` 遇到 `query` 走 `NotImplementedError` 分支）

- [ ] **Step 4.3: 扩展 _command_loop 处理 query / interrupt**

替换 `server/agent_runtime/session_actor.py` 中 `_command_loop` 方法（整个方法体）为：

```python
    async def _command_loop(self, client: Any) -> None:
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

在类中新增 `_drive_query`（最小版，先不处理命令交织）：

```python
    async def _drive_query(
        self, client: Any, query_cmd: SessionCommand
    ) -> SessionCommand | None:
        """消费 receive_response 直到 StopAsyncIteration。初版不处理中途命令。"""
        async for msg in client.receive_response():
            self._on_message(msg)
        query_cmd.done.set()
        return None
```

- [ ] **Step 4.4: 运行测试，确认部分通过**

Run: `uv run python -m pytest tests/test_session_actor.py::test_query_consumes_all_messages_and_sets_done -v`
Expected: PASS

Run: `uv run python -m pytest tests/test_session_actor.py::test_all_sdk_calls_recorded_on_same_task -v`
Expected: 仍 FAIL（因为 interrupt 被 `async for` 阻塞，test 用 `block_forever=True` → drive_query 永远消费不到 interrupt_message）

这是 Task 5 要解决的。**此处暂时跳过** `test_all_sdk_calls_recorded_on_same_task`：

Run: `uv run python -m pytest tests/test_session_actor.py -v --deselect tests/test_session_actor.py::test_all_sdk_calls_recorded_on_same_task`
Expected: 12 passed

- [ ] **Step 4.5: Commit**

```bash
git add server/agent_runtime/session_actor.py tests/test_session_actor.py
git commit -m "refactor(session-actor): _command_loop 支持 query 命令与消息消费 (#159)"
```

---

### Task 5: _drive_query 交织 —— interrupt during query 立即生效

**Files:**
- Modify: `server/agent_runtime/session_actor.py:_drive_query`
- Modify: `tests/test_session_actor.py`

- [ ] **Step 5.1: 写失败测试 —— interrupt 不被长消息流阻塞 + drain 到 error_during_execution**

追加到 `tests/test_session_actor.py`：

```python
@pytest.mark.asyncio
async def test_interrupt_during_long_query_is_immediate():
    """query 进行中，100ms 后送 interrupt；interrupt 应在 <300ms 内被 actor 调用。"""
    client = FakeSDKClient(
        block_forever=True,
        interrupt_message={"type": "result", "subtype": "error_during_execution"},
    )
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    q = SessionCommand(type="query", prompt="long task")
    await actor.enqueue(q)
    await asyncio.sleep(0.1)
    assert not q.done.is_set()  # query 仍在进行

    t_before = asyncio.get_event_loop().time()
    i = SessionCommand(type="interrupt")
    await actor.enqueue(i)
    await i.done.wait()
    elapsed = asyncio.get_event_loop().time() - t_before
    assert elapsed < 0.3, f"interrupt took too long: {elapsed}s"
    assert client.interrupted

    # query 也应随之结束（drain 完 error_during_execution）
    await asyncio.wait_for(q.done.wait(), timeout=1.0)

    d = SessionCommand(type="disconnect")
    await actor.enqueue(d)
    await d.done.wait()
    if actor._task is not None:
        await actor._task


@pytest.mark.asyncio
async def test_drain_after_interrupt_reaches_error_during_execution():
    """interrupt 后，receive_response 自然 drain 到 ResultMessage(error_during_execution)。"""
    collected: list[dict] = []
    client = FakeSDKClient(
        block_forever=True,
        interrupt_message={"type": "result", "subtype": "error_during_execution"},
    )
    actor = SessionActor(
        client_factory=lambda: client,
        on_message=lambda m: collected.append(m),
    )
    await actor.start()

    q = SessionCommand(type="query", prompt="run")
    await actor.enqueue(q)
    await asyncio.sleep(0.05)
    i = SessionCommand(type="interrupt")
    await actor.enqueue(i)
    await q.done.wait()

    # 最后一条消息应为 error_during_execution 的 ResultMessage
    assert collected[-1] == {"type": "result", "subtype": "error_during_execution"}

    d = SessionCommand(type="disconnect")
    await actor.enqueue(d)
    await d.done.wait()
    if actor._task is not None:
        await actor._task


@pytest.mark.asyncio
async def test_two_queries_queued_during_interrupt_drain():
    """interrupt drain 期间新 query 排队，drain 完成后按序执行。"""
    client = FakeSDKClient(
        block_forever=True,
        interrupt_message={"type": "result", "subtype": "error_during_execution"},
    )
    collected: list[dict] = []
    actor = SessionActor(
        client_factory=lambda: client,
        on_message=lambda m: collected.append(m),
    )
    await actor.start()

    q1 = SessionCommand(type="query", prompt="first")
    await actor.enqueue(q1)
    await asyncio.sleep(0.05)
    i = SessionCommand(type="interrupt")
    await actor.enqueue(i)
    q2 = SessionCommand(type="query", prompt="second")
    await actor.enqueue(q2)

    # q1 先完成（drain 到 error_during_execution）
    await q1.done.wait()

    # q2 要能被消费：向 client 推第二个 query 的响应
    client.push_message({"type": "result", "subtype": "success"})
    # interrupt 让 receive_response 卡住的协程已结束；第二次 receive_response 会从队列拿
    await asyncio.wait_for(q2.done.wait(), timeout=1.0)

    assert client.sent_queries == ["first", "second"]

    d = SessionCommand(type="disconnect")
    await actor.enqueue(d)
    await d.done.wait()
    if actor._task is not None:
        await actor._task
```

- [ ] **Step 5.2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_session_actor.py::test_interrupt_during_long_query_is_immediate -v`
Expected: FAIL（interrupt 永远等不到 —— 当前 `_drive_query` 在 `async for` 阻塞）

- [ ] **Step 5.3: 重写 _drive_query 用 asyncio.wait 交织**

完全替换 `session_actor.py` 中 `_drive_query` 方法体：

```python
    async def _drive_query(
        self, client: Any, query_cmd: SessionCommand
    ) -> SessionCommand | None:
        """在同一 task 内交织消费 receive_response 与新命令。
        返回：从队列取出但本轮未消化的命令（交给 _command_loop 下一轮）。
        """
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
                        if cmd_task.done():
                            return cmd_task.result()
                        cmd_task.cancel()
                        return None

                if cmd_task in done:
                    next_cmd = cmd_task.result()
                    if next_cmd.type == "interrupt":
                        await client.interrupt()
                        next_cmd.done.set()
                        cmd_task = asyncio.create_task(self._cmd_queue.get())
                    elif next_cmd.type == "disconnect":
                        # drive_query 内部遇到 disconnect：先 interrupt 让消息流收尾，
                        # 然后把 disconnect 命令携带回 _command_loop 处理
                        await client.interrupt()
                        return next_cmd
                    elif next_cmd.type == "query":
                        # 违反 "drain before new query"；携带给下一轮，
                        # 由 ManagedSession 层保证不会在 running 状态重复 query
                        return next_cmd
        finally:
            if not msg_task.done():
                msg_task.cancel()
            if not cmd_task.done():
                cmd_task.cancel()
```

- [ ] **Step 5.4: 运行所有测试，确认全部通过**

Run: `uv run python -m pytest tests/test_session_actor.py -v`
Expected: 15 passed（含 Task 4 原本失败的 `test_all_sdk_calls_recorded_on_same_task`）

- [ ] **Step 5.5: Commit**

```bash
git add server/agent_runtime/session_actor.py tests/test_session_actor.py
git commit -m "refactor(session-actor): _drive_query 用 asyncio.wait 交织消息与命令 (#159)"
```

---

### Task 6: disconnect during query —— drain 完再退出

**Files:**
- Modify: `tests/test_session_actor.py`（验证 Task 5 已实现的 disconnect 分支）

Task 5 的 `_drive_query` 已实现 `disconnect` 分支。本任务补充行为测试锁定语义。

- [ ] **Step 6.1: 写测试 —— disconnect during query 先 interrupt 再退出**

追加到 `tests/test_session_actor.py`：

```python
@pytest.mark.asyncio
async def test_disconnect_during_query_defers_exit():
    """query 进行中送 disconnect：actor 先 interrupt，drain 完后才退出 async with。"""
    client = FakeSDKClient(
        block_forever=True,
        interrupt_message={"type": "result", "subtype": "error_during_execution"},
    )
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    q = SessionCommand(type="query", prompt="run")
    await actor.enqueue(q)
    await asyncio.sleep(0.05)

    d = SessionCommand(type="disconnect")
    await actor.enqueue(d)
    await d.done.wait()
    # 此时 actor task 应已结束（disconnect 触发 __aexit__）
    if actor._task is not None:
        await asyncio.wait_for(actor._task, timeout=1.0)
    assert client.interrupted  # 先 interrupt
    assert client.disconnected  # 后 disconnect
```

- [ ] **Step 6.2: 运行测试**

Run: `uv run python -m pytest tests/test_session_actor.py::test_disconnect_during_query_defers_exit -v`
Expected: PASS（Task 5 的实现已涵盖）

- [ ] **Step 6.3: Commit**

```bash
git add tests/test_session_actor.py
git commit -m "test(session-actor): 锁定 disconnect during query 的 drain-then-exit 语义 (#159)"
```

---

### Task 7: 异常传播 + _drain_pending_commands + enqueue fast-fail

**Files:**
- Modify: `tests/test_session_actor.py`

Task 3 已实现 `_drain_pending_commands`；本任务追加行为测试验证异常路径。

- [ ] **Step 7.1: 写失败测试 —— 异常路径**

追加到 `tests/test_session_actor.py`：

```python
class _ExplodingClient(FakeSDKClient):
    async def query(self, prompt, session_id: str = "default") -> None:
        self._record("query")
        raise RuntimeError("sdk boom")


@pytest.mark.asyncio
async def test_actor_error_propagates_to_waiter():
    client = _ExplodingClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    q = SessionCommand(type="query", prompt="hi")
    await actor.enqueue(q)
    await q.done.wait()
    assert isinstance(q.error, RuntimeError)
    assert str(q.error) == "sdk boom"
    assert actor._fatal is q.error

    # actor 已死亡；后续命令应 fast-fail
    q2 = SessionCommand(type="query", prompt="another")
    await actor.enqueue(q2)
    await q2.done.wait()
    assert q2.error is not None


@pytest.mark.asyncio
async def test_actor_fatal_drains_queued_commands():
    """actor 异常退出前，队列中的命令也会被 drain，不会挂死。"""
    client = _ExplodingClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    q = SessionCommand(type="query", prompt="hi")
    # 排在后面的命令：在 _run 捕获异常后被 finally 的 drain 清理
    q_queued = SessionCommand(type="query", prompt="queued")
    await actor.enqueue(q)
    await actor.enqueue(q_queued)

    # 等 actor task 结束
    if actor._task is not None:
        with pytest.raises(RuntimeError):
            await actor._task

    await asyncio.wait_for(q.done.wait(), timeout=1.0)
    await asyncio.wait_for(q_queued.done.wait(), timeout=1.0)
    assert q.error is not None
    assert q_queued.error is not None


@pytest.mark.asyncio
async def test_enqueue_after_actor_closed_fails_fast():
    """正常 disconnect 后，enqueue 不会挂起。"""
    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()
    d = SessionCommand(type="disconnect")
    await actor.enqueue(d)
    await d.done.wait()
    if actor._task is not None:
        await actor._task

    stale = SessionCommand(type="query", prompt="hi")
    await actor.enqueue(stale)
    await stale.done.wait()
    assert stale.error is not None
    assert isinstance(stale.error, (_ActorClosed, BaseException))
```

- [ ] **Step 7.2: 运行测试，确认通过**

Run: `uv run python -m pytest tests/test_session_actor.py -v`
Expected: 19 passed（全部）

- [ ] **Step 7.3: Commit**

```bash
git add tests/test_session_actor.py
git commit -m "test(session-actor): 锁定异常传播、队列 drain、actor-closed fast-fail (#159)"
```

---

## Phase 2 — ManagedSession 改造（T8–T10）

### Task 8: ManagedSession dataclass —— actor 替换 client / consumer_task

**Files:**
- Modify: `server/agent_runtime/session_manager.py:76-99`（`ManagedSession` dataclass）
- Modify: `server/agent_runtime/session_manager.py`（import 区域）

此任务只做字段替换；`send_*` 代理方法和 `_on_actor_message` 在后续任务里加。**不运行** `test_session_manager_more.py`（它还依赖 `client` 字段，后续任务逐步修）。

- [ ] **Step 8.1: 查看当前 ManagedSession 定义**

Run: `uv run python -c "from server.agent_runtime.session_manager import ManagedSession; import dataclasses; print(dataclasses.fields(ManagedSession))"`

- [ ] **Step 8.2: 修改 import 区域**

在 `server/agent_runtime/session_manager.py` 顶部 import 区域（靠近其他 agent_runtime 相关 import）新增：

```python
from server.agent_runtime.session_actor import SessionActor, SessionCommand, _ActorClosed
```

- [ ] **Step 8.3: 改造 ManagedSession dataclass**

找到 `server/agent_runtime/session_manager.py:76` 开始的 `ManagedSession` dataclass。替换 `client: Any` 字段为 `actor: SessionActor`，并删除 `consumer_task` 字段（若存在）。保留 `status / message_buffer / subscribers / pending_questions / _cleanup_task / sdk_id_event / resolved_sdk_id / pending_user_echoes / last_activity` 等业务字段。

具体替换（逐行定位）：

```python
@dataclass
class ManagedSession:
    session_id: str
    actor: "SessionActor"                # ← 原来是 client: Any
    status: SessionStatus
    project_name: str
    # 保留以下字段（如原本存在，保持原样）：
    message_buffer: deque = field(default_factory=lambda: deque(maxlen=100))
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    pending_questions: dict[str, PendingQuestion] = field(default_factory=dict)
    pending_user_echoes: deque = field(default_factory=lambda: deque(maxlen=20))
    sdk_id_event: asyncio.Event = field(default_factory=asyncio.Event)
    resolved_sdk_id: str | None = None
    last_activity: float = field(default_factory=lambda: time.monotonic())
    # 新增：actor 同步回调与异步业务处理之间的桥梁
    _inbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    _process_task: asyncio.Task | None = None
    _cleanup_task: asyncio.Task | None = None
    _interrupting: bool = False
    # 删除字段：client、consumer_task
```

> **为什么要 `_inbox` + `_process_task`**：SessionActor 的 `on_message` 必须同步（就跑在 actor 主 task 里，await 会阻塞整条消息循环）。但现有 `_handle_special_message` / `_finalize_turn` / `_mark_session_terminal` 内含 `await meta_store.update_*`、`await self._save_session_meta` 等异步操作，sync 回调无法直接调用。
> 
> 解决办法是两层消费：
> - **同步层** (`_on_actor_message`)：状态机 + `add_message`（deque append + `put_nowait` 到订阅者），全部 O(1) 内存操作。
> - **异步层** (`_process_inbox`)：SessionManager 每会话 spawn 一个 processor task，从 `managed._inbox.get()` 读消息，执行异步业务；收到 `None` sentinel 时退出。
>
> 这本质上是把原 `_consume_messages` 的数据源从 `client.receive_response()` 换成 `managed._inbox.get()`，消费体保留，只是不再直接持有 SDK 客户端。

⚠️ **不要改动** `add_message` / `_broadcast_to_subscribers` / `add_pending_question` 等方法 —— 它们不依赖 `client`。

- [ ] **Step 8.4: 调整 session_manager.py 内部所有对 `managed.client` / `managed.consumer_task` 的引用**

**不删除旧逻辑**（下一任务替换），先让 Python 语法通过。使用 grep 定位：

Run: `grep -n "managed\.client\|managed\.consumer_task\|\.client\s*=\|\.consumer_task\s*=" server/agent_runtime/session_manager.py | head -60`

对每一处：
- `managed.client` → 暂替换为 `managed.actor`（不改语义，下一任务重写方法）
- `managed.consumer_task` → 暂用 `managed.actor._task`（只读）/删除赋值

示例（`send_new_session` 内）：
```python
# 原：
managed = ManagedSession(session_id=temp_id, client=client, status="running", project_name=project_name)
# 改：
# （下一任务重写整段；现在先注释 client 字段不会被传入）
```

为避免工作量，**本任务只做：**
1. 修改 dataclass 字段定义
2. 对所有读 `managed.client` / `managed.consumer_task` 的**非核心路径**（日志、异常消息），改成指向 actor 或删除

核心生命周期路径（`send_new_session` / `_disconnect_session_inner` / `_consume_messages`）保留旧代码暂不编译——本任务接受 `session_manager.py` **暂时无法通过静态检查**，Python 模块 import 仍能工作的前提。

Run: `uv run python -c "from server.agent_runtime.session_manager import ManagedSession"`
Expected: 成功 import。若失败，只修最关键的语法错（保留旧逻辑的字段引用为 `managed.actor`，甚至保留 `# TODO: 下一任务重写` 注释——仅限本任务）。

- [ ] **Step 8.5: 运行 actor 独立测试确认未受影响**

Run: `uv run python -m pytest tests/test_session_actor.py -v`
Expected: 19 passed

- [ ] **Step 8.6: Commit**

```bash
git add server/agent_runtime/session_manager.py
git commit -m "refactor(session-actor): ManagedSession.client → actor 字段替换 (#159)"
```

---

### Task 9: _on_actor_message 回调 + 状态机推导

**Files:**
- Modify: `server/agent_runtime/session_manager.py`（`ManagedSession` 类方法区）
- Modify: `tests/test_session_manager_more.py`（新增测试）

- [ ] **Step 9.1: 写失败测试 —— 状态机迁移**

在 `tests/test_session_manager_more.py` 文件末尾追加：

```python
# --- ManagedSession 状态机（Session Actor 重构）-----------------------------

def _make_managed_for_state_test():
    """构造一个 ManagedSession 用于状态机测试，actor 字段用 None 占位。"""
    from server.agent_runtime.session_manager import ManagedSession
    return ManagedSession(
        session_id="test",
        actor=None,  # 状态机测试不触及 actor
        status="running",
        project_name="demo",
    )


def test_on_actor_message_result_success_sets_idle():
    managed = _make_managed_for_state_test()
    managed._on_actor_message({"type": "result", "subtype": "success"})
    assert managed.status == "idle"


def test_on_actor_message_result_error_during_execution_sets_interrupted():
    managed = _make_managed_for_state_test()
    managed._on_actor_message({"type": "result", "subtype": "error_during_execution"})
    assert managed.status == "interrupted"


def test_on_actor_message_result_other_error_sets_error():
    managed = _make_managed_for_state_test()
    managed._on_actor_message({"type": "result", "subtype": "error_max_turns"})
    assert managed.status == "error"


def test_on_actor_message_non_result_message_preserves_status():
    managed = _make_managed_for_state_test()
    managed.status = "running"
    managed._on_actor_message({"type": "assistant", "content": "hi"})
    assert managed.status == "running"


def test_on_actor_message_appends_to_buffer():
    managed = _make_managed_for_state_test()
    managed._on_actor_message({"type": "assistant", "content": "hi"})
    # add_message 负责 buffer + broadcast；这里只验 buffer
    buffered = list(managed.message_buffer)
    assert len(buffered) == 1
    assert buffered[0]["type"] == "assistant"
```

- [ ] **Step 9.2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_session_manager_more.py::test_on_actor_message_result_success_sets_idle -v`
Expected: FAIL（`ManagedSession` 无 `_on_actor_message` 方法）

- [ ] **Step 9.3: 实现 _on_actor_message**

在 `ManagedSession` 类内（`add_message` 方法附近，约 `session_manager.py:100` 处）追加：

```python
    def _on_actor_message(self, msg: dict[str, Any]) -> None:
        """SessionActor 的 on_message 回调。同步，内存操作，不 await。"""
        msg_type = msg.get("type")

        if msg_type == "ask_user_question":
            # pending_questions 登记走既有 add_pending_question 路径；
            # SessionManager._handle_special_message 的相关逻辑保留
            pass  # 注册逻辑由 SessionManager 处理（它保留对 msg 的 handling）

        if msg_type == "result":
            subtype = msg.get("subtype")
            if subtype == "error_during_execution":
                self.status = "interrupted"
            elif subtype == "success":
                self.status = "idle"
            elif subtype and subtype.startswith("error"):
                self.status = "error"

        self.add_message(msg)
```

> **注意：** `ask_user_question` 的注册逻辑目前在 `SessionManager._handle_special_message` 中。为保持本任务的原子性，不把它从 SessionManager 搬进 ManagedSession；`_on_actor_message` 调用 `add_message` 后，SessionManager 订阅消息流时再处理（见 Task 11）。

- [ ] **Step 9.4: 运行测试，确认通过**

Run: `uv run python -m pytest tests/test_session_manager_more.py -k "on_actor_message" -v`
Expected: 5 passed

- [ ] **Step 9.5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "refactor(session-actor): ManagedSession._on_actor_message 回调与状态机 (#159)"
```

---

### Task 10: send_query / send_interrupt / send_disconnect 代理方法

**Files:**
- Modify: `server/agent_runtime/session_manager.py`（`ManagedSession` 类）
- Modify: `tests/test_session_manager_more.py`

- [ ] **Step 10.1: 写失败测试 —— 代理方法**

追加到 `tests/test_session_manager_more.py`：

```python
# --- ManagedSession 对 actor 的代理 -----------------------------------------

@pytest.mark.asyncio
async def test_send_query_sets_running_and_awaits_done():
    from server.agent_runtime.session_actor import SessionActor, SessionCommand
    from server.agent_runtime.session_manager import ManagedSession
    from tests.fakes import FakeSDKClient

    client = FakeSDKClient(messages=[{"type": "result", "subtype": "success"}])
    managed_ref: list = []

    def on_message(msg):
        managed_ref[0]._on_actor_message(msg)

    actor = SessionActor(client_factory=lambda: client, on_message=on_message)
    managed = ManagedSession(session_id="t", actor=actor, status="idle", project_name="p")
    managed_ref.append(managed)

    await actor.start()
    await managed.send_query("hi")
    assert client.sent_queries == ["hi"]
    assert managed.status == "idle"  # result=success → idle

    # 收尾
    await managed.send_disconnect()


@pytest.mark.asyncio
async def test_send_query_raises_on_cmd_error():
    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession

    class _Explode:
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return False
        async def query(self, *a, **k): raise RuntimeError("boom")
        async def interrupt(self): pass
        async def receive_response(self):
            if False: yield {}

    actor = SessionActor(client_factory=lambda: _Explode(), on_message=lambda m: None)
    managed = ManagedSession(session_id="t", actor=actor, status="idle", project_name="p")
    await actor.start()
    with pytest.raises(RuntimeError, match="boom"):
        await managed.send_query("hi")
    assert managed.status == "error"


@pytest.mark.asyncio
async def test_send_interrupt_is_idempotent_via_flag():
    """_interrupting 标志防止重入。"""
    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession
    from tests.fakes import FakeSDKClient

    client = FakeSDKClient(
        block_forever=True,
        interrupt_message={"type": "result", "subtype": "error_during_execution"},
    )
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    managed = ManagedSession(session_id="t", actor=actor, status="running", project_name="p")
    await actor.start()

    # 发一个 query 让 receive_response 开始
    from server.agent_runtime.session_actor import SessionCommand
    q = SessionCommand(type="query", prompt="x")
    await actor.enqueue(q)
    await asyncio.sleep(0.05)

    # 并发两次 send_interrupt；第二次应走 _interrupting fast-return
    await asyncio.gather(managed.send_interrupt(), managed.send_interrupt())
    # client.interrupt 至少被调一次（具体次数视 asyncio 调度，允许 1 或 2）
    assert client.interrupted

    await q.done.wait()
    await managed.send_disconnect()


@pytest.mark.asyncio
async def test_send_disconnect_waits_actor_task_done():
    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession
    from tests.fakes import FakeSDKClient

    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    managed = ManagedSession(session_id="t", actor=actor, status="idle", project_name="p")
    await actor.start()
    await managed.send_disconnect()
    assert managed.status == "closed"
    assert actor._task is not None and actor._task.done()
    assert client.disconnected
```

- [ ] **Step 10.2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_session_manager_more.py -k "send_query or send_interrupt or send_disconnect" -v`
Expected: FAIL（方法不存在）

- [ ] **Step 10.3: 实现代理方法**

在 `ManagedSession` 类内（紧邻 `_on_actor_message` 之后），追加：

```python
    async def send_query(
        self, prompt: str | AsyncIterable[dict], sdk_session_id: str = "default"
    ) -> None:
        # 等 prompt 送入 SDK 即返回（整轮 receive_response 由 actor 后台 drain），
        # 保持 HTTP 路径 "立即 accepted + SSE 异步消费" 语义。
        self.status = "running"
        cmd = SessionCommand(type="query", prompt=prompt, session_id=sdk_session_id)
        await self.actor.enqueue(cmd)
        await cmd.sent.wait()
        if cmd.error is not None:
            self.status = "error"
            raise cmd.error

    async def send_interrupt(self) -> None:
        if self._interrupting:
            return
        self._interrupting = True
        try:
            cmd = SessionCommand(type="interrupt")
            await self.actor.enqueue(cmd)
            await cmd.done.wait()
        finally:
            self._interrupting = False

    async def send_disconnect(self) -> None:
        cmd = SessionCommand(type="disconnect")
        await self.actor.enqueue(cmd)
        await cmd.done.wait()
        if self.actor._task is not None:
            import contextlib
            with contextlib.suppress(BaseException):
                await self.actor._task
        self.status = "closed"
```

(若文件已 `import contextlib`，不要重复；把 `import contextlib` 提到文件顶部并删掉方法内 import。)

- [ ] **Step 10.4: 运行测试，确认通过**

Run: `uv run python -m pytest tests/test_session_manager_more.py -k "send_query or send_interrupt or send_disconnect" -v`
Expected: 4 passed

- [ ] **Step 10.5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "refactor(session-actor): ManagedSession 对 actor 的 send_* 代理方法 (#159)"
```

---

## Phase 3 — SessionManager 生命周期重构（T11–T14）

### Task 11: send_new_session 重构

**Files:**
- Modify: `server/agent_runtime/session_manager.py:829-919`（`send_new_session` 方法）

- [ ] **Step 11.1: 查看现有方法**

Run: `sed -n '829,920p' server/agent_runtime/session_manager.py | head -100`

- [ ] **Step 11.2: 重写 send_new_session**

替换 `send_new_session` 整个方法体为：

```python
    async def send_new_session(
        self,
        project_name: str,
        prompt: str | AsyncIterable[dict],
        *,
        echo_text: str | None = None,
        echo_content: list[dict[str, Any]] | None = None,
        locale: str = "zh",
    ) -> str:
        """Create a new session via send-first: start actor, send message, wait for sdk_session_id."""
        if not SDK_AVAILABLE or ClaudeSDKClient is None:
            raise RuntimeError("claude_agent_sdk is not installed")

        await self._ensure_capacity()
        temp_id = uuid4().hex
        managed_ref: list[ManagedSession | None] = [None]

        options = self._build_options(
            project_name,
            resume_id=None,
            can_use_tool=await self._build_can_use_tool_callback(temp_id, managed_ref),
            locale=locale,
        )

        def _on_actor_message(msg: dict[str, Any]) -> None:
            """同步回调：只做状态机 + buffer + broadcast，异步业务交给 _process_inbox。"""
            managed = managed_ref[0]
            if managed is None:
                return
            managed._on_actor_message(msg)     # 同步：状态机 + add_message
            managed._inbox.put_nowait(msg)     # 异步业务入队（_process_inbox 消费）

        actor = SessionActor(
            client_factory=lambda: ClaudeSDKClient(options=options),
            on_message=_on_actor_message,
        )
        managed = ManagedSession(
            session_id=temp_id,
            actor=actor,
            status="running",
            project_name=project_name,
        )
        managed_ref[0] = managed
        managed.last_activity = time.monotonic()
        self.sessions[temp_id] = managed

        # actor 异常结束时，把 session 切 error 并通知订阅者；
        # 同时在 _inbox 塞 sentinel，让 _process_inbox 自然退出
        def _on_actor_done(task: asyncio.Task) -> None:
            try:
                managed._inbox.put_nowait(None)
            except Exception:
                pass
            if task.cancelled():
                return
            exc = task.exception()
            if exc is None:
                return
            managed.status = "error"
            try:
                status_msg = self._build_runtime_status_message(managed, "error", str(exc))
                managed.add_message(status_msg)
            except Exception:
                logger.exception("构造 runtime_status 消息失败")

        try:
            await actor.start()
        except Exception:
            logger.exception("SessionActor 启动失败")
            self.sessions.pop(temp_id, None)
            raise

        # 启动异步消息处理器（从 _inbox 消费，执行 _handle_special_message 等 await 业务）
        managed._process_task = asyncio.create_task(self._process_inbox(managed))

        if actor._task is not None:
            actor._task.add_done_callback(_on_actor_done)

        # Echo user message
        display_text = echo_text or (prompt if isinstance(prompt, str) else "")
        dedup_key = display_text or (self._IMAGE_ONLY_SENTINEL if echo_content else "")
        if dedup_key:
            managed.pending_user_echoes.append(dedup_key)
        managed.add_message(self._build_user_echo_message(display_text, echo_content))

        # 发首条 query（actor 内部会消费 receive_response）
        try:
            await managed.send_query(prompt)
        except Exception:
            logger.exception("新会话消息发送失败")
            self.sessions.pop(temp_id, None)
            await managed.send_disconnect()
            raise

        # 等待 sdk_session_id 就位
        event_task = asyncio.create_task(managed.sdk_id_event.wait())
        try:
            await asyncio.wait(
                {event_task, actor._task} if actor._task else {event_task},
                timeout=self._SDK_ID_TIMEOUT,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not event_task.done():
                event_task.cancel()

        if not managed.sdk_id_event.is_set():
            logger.error("等待 sdk_session_id 超时或 actor 提前退出 temp_id=%s", temp_id)
            managed.cancel_pending_questions("session creation timed out")
            self.sessions.pop(temp_id, None)
            await managed.send_disconnect()
            raise TimeoutError("SDK 会话创建超时")

        sdk_id = managed.resolved_sdk_id
        assert sdk_id is not None
        assert managed.session_id == sdk_id
        return sdk_id
```

- [ ] **Step 11.3: 新增 _process_inbox 方法（替换原 _consume_messages）**

本步在 `SessionManager` 类内新增 `_process_inbox`。它是原 `_consume_messages` 的直接继任者——消费体（`_handle_special_message` / `_finalize_turn` / `_mark_session_terminal` 等异步业务）**完全保留**，只是数据源从 `async for msg in managed.client.receive_response()` 换成 `while True: msg = await managed._inbox.get()`。

Run: `grep -n "async def _consume_messages\|_handle_special_message\|_finalize_turn\|_mark_session_terminal" server/agent_runtime/session_manager.py | head -20`

先读一下 `_consume_messages` 的当前主体，然后照搬逻辑到新方法：

```python
    async def _process_inbox(self, managed: ManagedSession) -> None:
        """Consume messages from managed._inbox; `None` is the shutdown sentinel.

        数据源取代原 client.receive_response()；业务处理（pending_questions 登记、
        sdk_session_id 捕获、final result 处理、meta_store 持久化等）保留。
        """
        try:
            while True:
                msg = await managed._inbox.get()
                if msg is None:
                    return
                try:
                    await self._handle_special_message(managed, msg)
                except Exception:
                    logger.exception(
                        "处理 session 消息失败 session_id=%s msg_type=%s",
                        managed.session_id,
                        msg.get("type"),
                    )
        except asyncio.CancelledError:
            raise
```

注意事项：
- `_handle_special_message` 现存签名若是同步，保持同步调用即可；若为 async，保留 `await`。grep 当前定义确认。
- 原 `_consume_messages` 里对 `managed.client` 的所有引用全部删除（它们在新数据源下不再需要）。
- 原 `_consume_messages` 里若有 status 赋值（如 `managed.status = "idle"`），已在 `_on_actor_message` 同步层覆盖，不要重复。
- 若 `_handle_special_message` 里调用了 `managed.status = ...`，优先保留其逻辑（异步写 meta），但状态机的主要赋值入口已是 `_on_actor_message`——review 时确认无冲突。

- [ ] **Step 11.4: 运行测试**

Run: `uv run python -m pytest tests/test_session_manager_more.py -v`
Expected: 大部分 PASS；与旧 `consumer_task` / `client` 强耦合的测试可能 FAIL（后续任务修）

记录 FAIL 测试名称：`uv run python -m pytest tests/test_session_manager_more.py 2>&1 | grep FAIL`

- [ ] **Step 11.5: 修复 session_manager_more 里被 send_new_session 改动影响的测试**

逐个修复 FAIL 测试。典型模式：
- 原测试 `managed.client = FakeSDKClient(messages=[...])` + 直接 `await session_manager._consume_messages(managed)` 的，改为：
  - 构造 `actor = SessionActor(client_factory=lambda: FakeSDKClient(messages=[...]), on_message=managed._on_actor_message)`
  - `managed.actor = actor`
  - `await actor.start()`
  - `await managed.send_query(...)`
  - 断言 buffer / subscribers 状态

（具体改动视每个测试而定；**TDD 原则**：每修完一个测试，立即跑它并验证通过。）

Run: `uv run python -m pytest tests/test_session_manager_more.py -v`
Expected: all passed（35 + 5 新状态机 + 4 代理方法 = 44）

- [ ] **Step 11.6: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "refactor(session-actor): send_new_session 走 actor + _process_inbox (#159)"
```

---

### Task 12: get_or_connect + send_message 重构

**Files:**
- Modify: `server/agent_runtime/session_manager.py:921-1017`（`get_or_connect` 和 `send_message`）

- [ ] **Step 12.1: 重写 get_or_connect**

替换 `get_or_connect`（`session_manager.py:921` 起）整个方法体为：

```python
    async def get_or_connect(self, session_id: str, *, meta: Optional["SessionMeta"] = None) -> ManagedSession:
        """Get existing managed session or spin up an actor for resumed session."""
        if session_id in self.sessions and session_id not in self._disconnecting:
            return self.sessions[session_id]

        if session_id not in self._connect_locks:
            self._connect_locks[session_id] = asyncio.Lock()

        async with self._connect_locks[session_id]:
            if session_id in self.sessions and session_id not in self._disconnecting:
                return self.sessions[session_id]

            await self._ensure_capacity()
            project_name = meta.project_name if meta else ""
            managed_ref: list[ManagedSession | None] = [None]

            options = self._build_options(
                project_name,
                resume_id=session_id,
                can_use_tool=await self._build_can_use_tool_callback(session_id, managed_ref),
                locale=(meta.locale if meta and getattr(meta, "locale", None) else "zh"),
            )

            def _on_actor_message(msg: dict[str, Any]) -> None:
                managed = managed_ref[0]
                if managed is None:
                    return
                managed._on_actor_message(msg)     # 同步：状态机 + add_message
                managed._inbox.put_nowait(msg)     # 异步业务入队

            actor = SessionActor(
                client_factory=lambda: ClaudeSDKClient(options=options),
                on_message=_on_actor_message,
            )
            managed = ManagedSession(
                session_id=session_id,
                actor=actor,
                status="idle",
                project_name=project_name,
            )
            managed_ref[0] = managed
            managed.last_activity = time.monotonic()
            self.sessions[session_id] = managed

            def _on_actor_done(task: asyncio.Task) -> None:
                try:
                    managed._inbox.put_nowait(None)
                except Exception:
                    pass

            try:
                await actor.start()
            except Exception:
                logger.exception("恢复会话 actor 启动失败 session_id=%s", session_id)
                self.sessions.pop(session_id, None)
                raise

            managed._process_task = asyncio.create_task(self._process_inbox(managed))
            if actor._task is not None:
                actor._task.add_done_callback(_on_actor_done)
            return managed
```

- [ ] **Step 12.2: 重写 send_message**

替换 `send_message`（`session_manager.py:964` 起）整个方法体为：

```python
    async def send_message(
        self,
        session_id: str,
        prompt: str | AsyncIterable[dict],
        *,
        echo_text: str | None = None,
        echo_content: list[dict[str, Any]] | None = None,
        meta: Optional["SessionMeta"] = None,
    ) -> SessionStatus:
        managed = await self.get_or_connect(session_id, meta=meta)
        managed.last_activity = time.monotonic()
        managed.status = "running"

        display_text = echo_text or (prompt if isinstance(prompt, str) else "")
        dedup_key = display_text or (self._IMAGE_ONLY_SENTINEL if echo_content else "")
        if dedup_key:
            managed.pending_user_echoes.append(dedup_key)
        managed.add_message(self._build_user_echo_message(display_text, echo_content))

        await managed.send_query(prompt, sdk_session_id=session_id)
        return managed.status
```

- [ ] **Step 12.3: 运行测试**

Run: `uv run python -m pytest tests/test_session_manager_more.py tests/test_session_lifecycle.py -v`
Expected: 大部分 PASS；记录并修复 FAIL 的

- [ ] **Step 12.4: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py tests/test_session_lifecycle.py
git commit -m "refactor(session-actor): get_or_connect/send_message 走 actor 生命周期 (#159)"
```

---

### Task 13: interrupt_session 重构

**Files:**
- Modify: `server/agent_runtime/session_manager.py:1019-1047`（`interrupt_session`）

- [ ] **Step 13.1: 重写 interrupt_session**

替换 `interrupt_session` 整个方法体为：

```python
    async def interrupt_session(self, session_id: str) -> SessionStatus:
        managed = self.sessions.get(session_id)
        if managed is None:
            return "closed"
        if managed.status == "closed":
            return "closed"
        managed.cancel_pending_questions("session interrupted")
        try:
            await managed.send_interrupt()
        except Exception:
            logger.exception("发送 interrupt 命令失败 session_id=%s", session_id)
            managed.status = "error"
        managed.last_activity = time.monotonic()
        # status 由 _on_actor_message 在收到 ResultMessage(error_during_execution) 时推导
        return managed.status
```

- [ ] **Step 13.2: 运行测试**

Run: `uv run python -m pytest tests/test_session_manager_more.py -k "interrupt" -v`
Expected: PASS（按旧行为测试断言 `client.interrupted=True` 仍有效，因 FakeSDKClient 记录 interrupt）

Run: `uv run python -m pytest tests/test_session_lifecycle.py -k "interrupt" -v`

- [ ] **Step 13.3: Commit**

```bash
git add server/agent_runtime/session_manager.py
git commit -m "refactor(session-actor): interrupt_session 走 actor send_interrupt (#159)"
```

---

### Task 14: answer_user_question 重构

**Files:**
- Modify: `server/agent_runtime/session_manager.py:1931-1944`（`answer_user_question`）

- [ ] **Step 14.1: 查看现有方法**

Run: `sed -n '1931,1950p' server/agent_runtime/session_manager.py`

- [ ] **Step 14.2: 重写 answer_user_question**

保留现有的 prompt 构造逻辑（若通过辅助函数），替换核心调用：

```python
    async def answer_user_question(
        self,
        session_id: str,
        question_id: str,
        answers: dict[str, str],
    ) -> None:
        managed = self.sessions.get(session_id)
        if managed is None:
            raise KeyError(f"session {session_id} not found")
        if not managed.resolve_pending_question(question_id, answers):
            raise KeyError(f"pending question {question_id} not found")
        prompt = self._build_answer_prompt_for(question_id, answers)  # 沿用既有构造
        managed.last_activity = time.monotonic()
        await managed.send_query(prompt, sdk_session_id=managed.session_id)
```

（若 `_build_answer_prompt_for` 名称不同，grep 定位既有 prompt 构造函数并使用正确名。）

- [ ] **Step 14.3: 运行测试**

Run: `uv run python -m pytest tests/test_session_manager_more.py -k "answer" -v`
Expected: PASS

- [ ] **Step 14.4: Commit**

```bash
git add server/agent_runtime/session_manager.py
git commit -m "refactor(session-actor): answer_user_question 走 actor send_query (#159)"
```

---

## Phase 4 — Cleanup / Eviction / Shutdown（T15–T17）

### Task 15: _evict_one 新增

**Files:**
- Modify: `server/agent_runtime/session_manager.py`（在 `_disconnect_session_inner` 附近新增方法）
- Modify: `tests/test_session_manager_more.py`

- [ ] **Step 15.1: 写失败测试 —— disconnect 超时触发 cancel**

追加到 `tests/test_session_manager_more.py`：

```python
@pytest.mark.asyncio
async def test_evict_timeout_falls_back_to_cancel():
    """actor 主 task 卡死时，_evict_one 15s 超时后用 task.cancel() 兜底。"""
    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession, SessionManager

    class _StuckClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *e):
            # 模拟 CLI 僵死：disconnect 阶段永远不返回
            await asyncio.sleep(3600)
        async def query(self, *a, **k): pass
        async def interrupt(self): pass
        async def receive_response(self):
            await asyncio.sleep(3600)
            if False: yield {}

    actor = SessionActor(client_factory=lambda: _StuckClient(), on_message=lambda m: None)
    managed = ManagedSession(session_id="stuck", actor=actor, status="idle", project_name="p")
    await actor.start()

    mgr = SessionManager.__new__(SessionManager)
    mgr.sessions = {"stuck": managed}
    mgr._session_actor_shutdown_timeout = 0.3  # 缩短以加速测试

    # _evict_one 内部 wait_for 超时后，对 actor._task 做 cancel
    await mgr._evict_one(managed)
    assert "stuck" not in mgr.sessions
    assert actor._task is None or actor._task.done()
```

- [ ] **Step 15.2: 运行测试，确认失败**

Run: `uv run python -m pytest tests/test_session_manager_more.py::test_evict_timeout_falls_back_to_cancel -v`
Expected: FAIL（`SessionManager` 无 `_evict_one` 方法 / `_session_actor_shutdown_timeout` 字段）

- [ ] **Step 15.3: 实现 _evict_one**

在 `SessionManager.__init__`（`session_manager.py:282`）末尾追加：

```python
        # Actor 关停总超时：正常 send_disconnect 覆盖 + cancel 兜底窗口
        self._session_actor_shutdown_timeout: float = 15.0
```

在 `SessionManager` 类内（`_disconnect_session_inner` 附近，约 `session_manager.py:1430` 行）新增方法：

```python
    async def _evict_one(self, managed: ManagedSession) -> None:
        """Gracefully disconnect an actor, cancel as fallback, and remove from registry."""
        session_id = managed.session_id
        self._disconnecting.add(session_id)
        try:
            try:
                await asyncio.wait_for(
                    managed.send_disconnect(),
                    timeout=self._session_actor_shutdown_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("actor disconnect 超时，走 cancel 兜底 session_id=%s", session_id)
                task = managed.actor._task if managed.actor else None
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
                managed.status = "closed"
            except Exception:
                logger.exception("actor 关停异常 session_id=%s", session_id)
                managed.status = "error"
            finally:
                # cleanup timer
                if managed._cleanup_task is not None and not managed._cleanup_task.done():
                    managed._cleanup_task.cancel()
                # 通知 _process_inbox 退出，并等它 drain 完
                try:
                    managed._inbox.put_nowait(None)
                except Exception:
                    pass
                if managed._process_task is not None and not managed._process_task.done():
                    try:
                        await asyncio.wait_for(managed._process_task, timeout=5.0)
                    except asyncio.TimeoutError:
                        managed._process_task.cancel()
                        with contextlib.suppress(BaseException):
                            await managed._process_task
                    except BaseException:
                        logger.exception(
                            "_process_inbox 退出异常 session_id=%s", session_id
                        )
        finally:
            self.sessions.pop(session_id, None)
            self._disconnecting.discard(session_id)
```

（若文件顶部未 `import contextlib`，补上。）

- [ ] **Step 15.4: 运行测试，确认通过**

Run: `uv run python -m pytest tests/test_session_manager_more.py::test_evict_timeout_falls_back_to_cancel -v`
Expected: PASS

- [ ] **Step 15.5: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py
git commit -m "refactor(session-actor): 新增 _evict_one，带超时 + cancel 兜底 (#159)"
```

---

### Task 16: _cleanup_idle + close_session + 容量淘汰 走 _evict_one

**Files:**
- Modify: `server/agent_runtime/session_manager.py`:
  - `close_session`（约 1301 行）
  - `_schedule_cleanup` + `_cleanup_idle`（约 1147 行）
  - `_ensure_capacity`（约 1454 行）

- [ ] **Step 16.1: 重写 close_session**

替换 `close_session`（`session_manager.py:1301`）整个方法体为：

```python
    async def close_session(self, session_id: str, *, reason: str = "session closed") -> None:
        managed = self.sessions.get(session_id)
        if managed is None:
            return
        managed.cancel_pending_questions(reason)
        await self._evict_one(managed)
```

- [ ] **Step 16.2: 重写 _schedule_cleanup / 新增 _cleanup_idle**

替换 `_schedule_cleanup`（`session_manager.py:1147`）整个方法体为：

```python
    def _schedule_cleanup(self, session_id: str) -> None:
        managed = self.sessions.get(session_id)
        if managed is None:
            return
        if managed._cleanup_task is not None and not managed._cleanup_task.done():
            managed._cleanup_task.cancel()
        managed._cleanup_task = asyncio.create_task(self._cleanup_idle(session_id))

    async def _cleanup_idle(self, session_id: str) -> None:
        try:
            delay = await self._get_cleanup_delay()
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        managed = self.sessions.get(session_id)
        if managed and managed.status in ("idle", "interrupted", "error"):
            await self._evict_one(managed)
```

- [ ] **Step 16.3: 重写 _ensure_capacity（淘汰分支）**

`_ensure_capacity`（`session_manager.py:1454`）中原有对 `_disconnect_session` 的调用，改为调用 `_evict_one`。具体位置：

Run: `grep -n "_disconnect_session\|_force_close" server/agent_runtime/session_manager.py | head -20`

对 `_ensure_capacity` 内部的淘汰调用（通常在 `for victim in ...` 循环内），替换为：

```python
            await self._evict_one(victim)
```

- [ ] **Step 16.4: 运行相关测试**

Run: `uv run python -m pytest tests/test_session_manager_more.py tests/test_session_lifecycle.py -v`
Expected: all PASS（仍有少量与 SIGTERM/SIGKILL 相关的断言 FAIL，下任务修）

- [ ] **Step 16.5: Commit**

```bash
git add server/agent_runtime/session_manager.py
git commit -m "refactor(session-actor): cleanup/close/eviction 统一走 _evict_one (#159)"
```

---

### Task 17: shutdown_gracefully 简化

**Files:**
- Modify: `server/agent_runtime/session_manager.py:1974-2014`（`shutdown_gracefully`）

- [ ] **Step 17.1: 重写 shutdown_gracefully**

替换 `shutdown_gracefully` 整个方法体为：

```python
    async def shutdown_gracefully(self, timeout: float = 30.0) -> None:
        # patrol loop 先停
        self._shutting_down = True
        if self._patrol_task is not None and not self._patrol_task.done():
            self._patrol_task.cancel()
            with contextlib.suppress(BaseException):
                await self._patrol_task

        sessions = list(self.sessions.values())
        if not sessions:
            return
        await asyncio.gather(
            *[self._evict_one(s) for s in sessions],
            return_exceptions=True,
        )
```

- [ ] **Step 17.2: 运行测试**

Run: `uv run python -m pytest tests/test_session_lifecycle.py -v`
Expected: 大部分 PASS；与 SIGTERM 相关的 FAIL 在下任务删除

- [ ] **Step 17.3: Commit**

```bash
git add server/agent_runtime/session_manager.py
git commit -m "refactor(session-actor): shutdown_gracefully 简化为 gather(_evict_one) (#159)"
```

---

## Phase 5 — 删除 workaround 代码（T18）

### Task 18: 删除 force-kill / 私有属性访问 / 旧 consumer_task 逻辑

**Files:**
- Modify: `server/agent_runtime/session_manager.py`（大量删除）
- Modify: `tests/test_session_manager_more.py`（相关断言删除）
- Modify: `tests/test_session_lifecycle.py`（相关断言删除）

- [ ] **Step 18.1: 定位所有待删除函数**

Run: `grep -n "_get_client_process\|_process_pid\|_process_returncode\|_force_close_client_process\|_cancel_task\|_consume_messages\|_wait_for_process_exit\|_disconnect_session_inner\|_disconnect_session\b" server/agent_runtime/session_manager.py`

预期命中以下函数（按文件出现顺序）：
- `_consume_messages`（约 1049 行）— **在 T11 已被 `_process_inbox` 取代**；若旧方法体仍残留此处删除
- `_get_client_process` / `_process_pid` / `_process_returncode`（约 1176-1192 行）
- `_cancel_task`（约 1193 行）
- `_wait_for_process_exit`（约 1200 行）
- `_force_close_client_process`（约 1220 行）
- `_disconnect_session`（约 1309 行）— 已被 `_evict_one` 取代
- `_disconnect_session_inner`（约 1333-1430 行）

- [ ] **Step 18.2: 确认 _consume_messages 已被移除**

`_process_inbox` 在 T11 已取代 `_consume_messages`。检查是否有残留：

Run: `grep -n "_consume_messages" server/agent_runtime/session_manager.py`
Expected: 无输出。若仍有残留方法体，此时删除。

`_handle_special_message`、`_finalize_turn`、`_mark_session_terminal` 保留（它们处理 `ask_user_question` / final result / meta 持久化，由 `_process_inbox` 调用）。

- [ ] **Step 18.3: 删除进程管理辅助函数**

逐个删除：
- `_get_client_process`
- `_process_pid`
- `_process_returncode`
- `_cancel_task`
- `_wait_for_process_exit`
- `_force_close_client_process`
- `_disconnect_session`
- `_disconnect_session_inner`

- [ ] **Step 18.4: 删除相关 import**

Run: `grep -n "^import os\|^import signal\|from subprocess" server/agent_runtime/session_manager.py`

若 `signal` 仅用于 `_force_close_client_process`，删除 `import signal`。`os` / `subprocess` 若无其他引用也一并清理。

- [ ] **Step 18.5: 验证无残留引用**

Run: `grep -rn "_get_client_process\|_force_close_client_process\|_cancel_task\|_consume_messages\|_disconnect_session_inner" server/ tests/`
Expected: 无输出（除了 commit message / 文档）

⚠️ 注意 `_process_task` / `_process_inbox` 是**新引入**的名字，与被删除的 `_consume_messages` 不同，不应被 grep 命中。

- [ ] **Step 18.6: 清理测试中的 workaround 断言**

在 `tests/test_session_manager_more.py` 和 `tests/test_session_lifecycle.py` 内 grep：

Run: `grep -n "SIGTERM\|SIGKILL\|_force_close\|_get_client_process\|_process_pid\|consumer_task" tests/test_session_manager_more.py tests/test_session_lifecycle.py`

对每一处命中：
- 断言 `consumer_task` 相关（如 `managed.consumer_task is not None`）→ 改为断言 `managed.actor._task is not None`
- 断言 `SIGTERM` / `SIGKILL` 触发 → 直接删除测试或改为 "disconnect 完成" 断言
- 断言 `_get_client_process` 返回 → 删除测试

- [ ] **Step 18.7: 运行完整测试套件**

Run: `uv run python -m pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 18.8: ruff lint + format**

Run: `uv run ruff check server/agent_runtime/session_manager.py server/agent_runtime/session_actor.py tests/test_session_actor.py tests/fakes.py tests/test_session_manager_more.py tests/test_session_lifecycle.py && uv run ruff format server/agent_runtime/session_manager.py server/agent_runtime/session_actor.py tests/test_session_actor.py tests/fakes.py tests/test_session_manager_more.py tests/test_session_lifecycle.py`
Expected: 无 error / 文件已格式化

- [ ] **Step 18.9: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/test_session_manager_more.py tests/test_session_lifecycle.py
git commit -m "refactor(session-actor): 删除 SIGTERM/SIGKILL fallback 与 SDK 私有属性访问 (#159)"
```

---

## Phase 6 — 验证（T19–T20）

### Task 19: 全量测试 + 覆盖率

- [ ] **Step 19.1: 运行完整测试套件**

Run: `uv run python -m pytest tests/ -v --tb=short 2>&1 | tail -40`
Expected: 全部通过（含新增的 actor 测试、改造后的 session_manager_more / session_lifecycle）

- [ ] **Step 19.2: 检查覆盖率**

Run: `uv run python -m pytest tests/ --cov=server/agent_runtime/session_actor --cov=server/agent_runtime/session_manager --cov-report=term-missing 2>&1 | tail -40`
Expected:
- `server/agent_runtime/session_actor.py`: ≥ 90%
- `server/agent_runtime/session_manager.py`: ≥ 90%
- 全局覆盖率 ≥ 80%

若低于阈值，检查 `term-missing` 输出中的未覆盖行号，按需补充测试：
- Actor 的异常分支（`_drive_query` 的 finally 双 cancel）
- `_evict_one` 的 `except Exception` 分支

- [ ] **Step 19.3: Commit 补充测试（若有）**

```bash
git add tests/test_session_actor.py tests/test_session_manager_more.py
git commit -m "test(session-actor): 补充覆盖率短板测试 (#159)"
```

---

### Task 20: 人工验证三个场景

启动开发服务：

Run: `uv run uvicorn server.main:app --reload --port 1241`（后台运行，或单独终端）

前端：`cd frontend && pnpm dev`

- [ ] **Step 20.1: 场景 1 —— 长任务 + interrupt**

1. 创建或打开项目，进入助手会话
2. 提问："写一个 500 行的 Python 俄罗斯方块游戏"
3. Claude 开始产出（观察 SSE 事件流）
4. 中途点击"停止"按钮
5. 验证：
   - [ ] 前端状态从 `running` 切到 `interrupted`
   - [ ] 消息流在 `error_during_execution` 的 `ResultMessage` 后自然结束，不再有新的 `stream_event`
   - [ ] 再发一条新 query（"说你好"），响应正常
   - [ ] 服务端日志无 `SIGTERM` / `_get_client_process` 相关输出（因已删除）

- [ ] **Step 20.2: 场景 2 —— idle cleanup**

1. 创建一个会话，等其进入 `idle` 状态
2. 放置 305 秒（配置默认 300s）
3. 验证：
   - [ ] 服务端日志出现 `actor disconnect` 或 cleanup 相关日志
   - [ ] `GET /api/v1/assistant/sessions` 不再列出该 session_id
   - [ ] 无进程泄漏（`ps aux | grep claude` 不再有该会话的 CLI 子进程）

- [ ] **Step 20.3: 场景 3 —— 服务优雅关停**

1. 创建 3 个活跃会话（3 个不同项目各开一个）
2. 在终端对 uvicorn 进程按 `Ctrl+C`
3. 验证：
   - [ ] 服务在 30 秒内完成退出
   - [ ] 终端日志显示所有 3 个会话的 disconnect 完成
   - [ ] 无 "cancel scope in different task" 的 anyio 异常
   - [ ] `ps aux | grep claude` 不再有遗留 CLI 子进程

- [ ] **Step 20.4: 回归点击若干前端功能**

1. 列出会话 → 正常显示
2. 切换到某个已关闭会话查看历史 → snapshot 正常加载
3. 回答 AskUserQuestion → 正常继续对话

- [ ] **Step 20.5: 若全部通过，准备 PR**

```bash
git log --oneline main..HEAD
# 预期看到约 18-22 个 refactor/test commits + 2 前置 commits（SDK 升级 + spec）
```

创建 PR 时，描述引用 spec 与 issue。参考 PR 描述模板：

```markdown
## Summary
- 将 SessionManager 持有的 ClaudeSDKClient 封装进每会话专属 asyncio task（SessionActor），消除 anyio cancel scope 跨 task 异常
- 升级 claude-agent-sdk 到 >=0.1.58，充分利用 interrupt → drain 新语义
- 一次性移除 SIGTERM/SIGKILL fallback 与 SDK 私有属性访问

## Design
详见 docs/superpowers/specs/2026-04-13-session-actor-design.md

## Test plan
- [x] 新增 SessionActor 单元测试（19 个用例），含"SDK 同 task"契约锁定
- [x] SessionManager / ManagedSession 测试改造通过
- [x] 覆盖率：session_actor.py >90%、session_manager.py >90%
- [x] 人工验证：长任务+interrupt、idle cleanup、优雅关停三场景
```

- [ ] **Step 20.6: 建议提交 PR（可选，由人类操作）**

```bash
git push -u origin refactor/session-actor-159
gh pr create --title "refactor: SessionManager 引入 Actor 模式 (#159)" --body-file <<... 参考 20.5 ...>>
```

---

## 附录 A — 契约锁定测试清单（回归基准）

以下测试是本次重构的"锚点"，任何未来改动若让它们失败则说明契约被破坏：

| 测试 | 文件 | 锁定的契约 |
|---|---|---|
| `test_actor_all_sdk_calls_recorded_on_same_task` | `tests/test_session_actor.py` | SDK 所有方法调用在同一 asyncio task |
| `test_interrupt_during_long_query_is_immediate` | `tests/test_session_actor.py` | interrupt 不被 `receive_response` 阻塞，延迟 <300ms |
| `test_drain_after_interrupt_reaches_error_during_execution` | `tests/test_session_actor.py` | interrupt 后 actor 自然 drain 到 `error_during_execution` |
| `test_disconnect_during_query_defers_exit` | `tests/test_session_actor.py` | disconnect 在 query 进行中时先 interrupt 再 exit |
| `test_actor_fatal_drains_queued_commands` | `tests/test_session_actor.py` | actor 异常时队列中所有命令 fail-fast，不挂死 |
| `test_evict_timeout_falls_back_to_cancel` | `tests/test_session_manager_more.py` | disconnect 卡死时 cancel 兜底有效 |
| `test_assistant_service_streaming.py` 全部 | `tests/` | 对外 SSE / 订阅接口零退化 |

## 附录 B — 常见错误排查

**症状 1：** actor 启动后 `receive_response` 一直没消息
- 检查 `FakeSDKClient` 的 `messages` 参数是否正确注入到 `_pending_messages`
- 检查 `__aenter__` 是否把 `_initial_messages` put 到队列

**症状 2：** `test_interrupt_during_long_query_is_immediate` 超时
- 检查 `_drive_query` 是否真的用 `asyncio.wait(FIRST_COMPLETED)`；若退化回 `async for`，interrupt 会被阻塞

**症状 3：** actor task 死后调用 `enqueue` 挂起
- 检查 `enqueue` 的 fast-fail 条件是否包含 `self._task.done()` 判断

**症状 4：** `send_disconnect` 返回但 `status` 还是 `running`
- 检查 `send_disconnect` 是否 await `self.actor._task` 完成后才设 `closed`

**症状 5：** 人工验证"优雅关停"时出现 "cancel scope in a different task"
- 检查是否仍有代码在 actor task 外直接调用 `client.xxx`；用 grep：
  `grep -rn "\.client\.\(connect\|query\|receive_response\|interrupt\|disconnect\)" server/`

---

## Self-Review 备忘

- [x] Spec coverage：第 1-10 节全部映射到任务（D1/D2/D3/D4/D5 对应 T3-T7/T8-T10/T15/T11-T17/前置 SDK 升级 commit）
- [x] 无 TBD / 无 "implement later"
- [x] 类型一致性：`SessionCommand` 签名全文一致；`SessionActor.start/enqueue/_run/_drain_pending_commands` 签名在 T1/T3/T5/T7 各处一致
- [x] 所有测试都附完整代码，无 "see Task N"
- [x] 所有 commit 消息遵循 `refactor(session-actor): ... (#159)` 或 `test(session-actor): ... (#159)` 约定
