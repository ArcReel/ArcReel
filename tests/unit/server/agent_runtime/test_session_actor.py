"""SessionActor 单元测试。

覆盖：命令协议、主循环、SDK 同 task 契约、交织语义、异常传播。actor 不撮合 query 与 result：
用户消息一律立即送入 CLI，消息流跨轮次持续读取。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage

from server.agent_runtime.sdk_frames import CommandLifecycle
from server.agent_runtime.session_actor import (
    MessageStreamClosed,
    SessionActor,
    SessionCommand,
    _ActorClosed,
)
from tests.fakes import (
    FakeSDKClient,
    assistant_frame,
    command_lifecycle_frame,
    result_frame,
    system_frame,
)

_INTERRUPTED_RESULT = result_frame("error_during_execution", is_error=True, uuid="interrupted")


def _message(text: str, uuid: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}, "parent_tool_use_id": None, "uuid": uuid}


def _query(text: str, uuid: str | None = None) -> SessionCommand:
    return SessionCommand(type="query", message=_message(text, uuid or f"u-{text}"))


def test_session_command_default_fields():
    cmd = _query("hello")
    assert cmd.type == "query"
    assert cmd.session_id == "default"
    assert isinstance(cmd.done, asyncio.Event)
    assert not cmd.done.is_set()
    assert cmd.error is None


def test_session_command_interrupt_no_message():
    cmd = SessionCommand(type="interrupt")
    assert cmd.type == "interrupt"
    assert cmd.message is None


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
async def test_fake_client_emits_initial_frames_after_first_query():
    frames = [assistant_frame(uuid="a1"), result_frame(uuid="r1")]
    client = FakeSDKClient(frames=frames)
    async with client:
        await client.query("hi")
        client.close_stream()
        collected = [frame async for frame in client._query.receive_messages()]
    assert collected == frames


@pytest.mark.asyncio
async def test_fake_client_emits_interrupt_frame_on_interrupt():
    client = FakeSDKClient(interrupt_frame=_INTERRUPTED_RESULT)
    async with client:
        await client.interrupt()
        client.close_stream()
        collected = [frame async for frame in client._query.receive_messages()]
    assert collected == [_INTERRUPTED_RESULT]


@pytest.mark.asyncio
async def test_fake_client_connect_error_raises_in_aenter():
    err = RuntimeError("boom")
    client = FakeSDKClient(connect_error=err)
    with pytest.raises(RuntimeError, match="boom"):
        async with client:
            pass


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


class _Collector(list):
    """on_message / on_command_lifecycle 替身：按到达顺序收集，可等到收满 n 条。"""

    def __init__(self) -> None:
        super().__init__()
        self._changed = asyncio.Event()

    def __call__(self, item) -> None:
        self.append(item)
        self._changed.set()

    async def wait_for_count(self, count: int) -> None:
        async def _wait() -> None:
            while len(self) < count:
                self._changed.clear()
                await self._changed.wait()

        await asyncio.wait_for(_wait(), timeout=1.0)


async def _disconnect(actor: SessionActor, *, interrupt_first: bool = False) -> None:
    d = SessionCommand(type="disconnect", interrupt_first=interrupt_first)
    await actor.enqueue(d)
    await d.done.wait()
    await actor.wait()


async def test_query_writes_the_message_frame_with_its_uuid_and_completes_once_sent():
    """消息带 uuid 原样写给 CLI；命令在写入后即完成，不等这一轮的 result。"""
    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()
    try:
        q = _query("hi", uuid="u-1")
        await actor.enqueue(q)
        await asyncio.wait_for(q.done.wait(), timeout=1.0)
        assert q.error is None
        assert [m["uuid"] for m in client.sent_messages] == ["u-1"]
        assert client.sent_queries == ["hi"]
    finally:
        await _disconnect(actor)


async def test_messages_keep_flowing_across_turns_without_new_queries():
    """result 只代表一轮结束：之后 CLI 自主开启的一轮照常读出。"""
    collected = _Collector()
    client = FakeSDKClient(frames=[assistant_frame(uuid="a1"), result_frame(uuid="r1")])
    actor = SessionActor(client_factory=lambda: client, on_message=collected)
    await actor.start()
    try:
        await actor.enqueue(_query("hi"))
        await collected.wait_for_count(2)
        client.push_frame(assistant_frame(uuid="follow-up"))
        client.push_frame(result_frame(uuid="follow-up-result"))
        await collected.wait_for_count(4)
        assert [type(m) for m in collected] == [AssistantMessage, ResultMessage, AssistantMessage, ResultMessage]
        assert [m.uuid for m in collected] == ["a1", "r1", "follow-up", "follow-up-result"]
        assert client.sent_queries == ["hi"]
    finally:
        await _disconnect(actor)


async def test_queries_go_straight_to_the_cli_while_a_turn_is_running():
    """消息排队由 CLI 负责（并入当前轮或之后另开一轮），actor 不暂存也不拒绝。"""
    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()
    try:
        queries = [_query(text) for text in ("first", "second", "third")]
        for q in queries:
            await actor.enqueue(q)
        for q in queries:
            await asyncio.wait_for(q.done.wait(), timeout=1.0)
            assert q.error is None
        assert client.sent_queries == ["first", "second", "third"]
    finally:
        await _disconnect(actor)


@pytest.mark.asyncio
async def test_all_sdk_calls_recorded_on_same_task():
    """契约锁定：connect / query / interrupt / disconnect 都在 actor 主 task 内调用，
    current_task 完全相同。"""
    client = FakeSDKClient(interrupt_frame=_INTERRUPTED_RESULT)
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    q = _query("hi")
    await actor.enqueue(q)
    await q.done.wait()
    i = SessionCommand(type="interrupt")
    await actor.enqueue(i)
    await i.done.wait()
    await _disconnect(actor, interrupt_first=True)

    # 仅锁定 method 调用（原始帧流是 async generator iteration，
    # 其 body 在子 task driven 是 asyncio 允许的，不属于 SDK 同 task 契约）
    sdk_methods = ("connect", "query", "interrupt", "disconnect")
    sdk_tasks: set = set()
    for m in sdk_methods:
        if m in client.method_tasks:
            sdk_tasks.update(client.method_tasks[m])
    assert len(sdk_tasks) == 1, (
        f"SDK methods ran on multiple tasks: { {m: client.method_tasks.get(m) for m in sdk_methods} }"
    )


@pytest.mark.asyncio
async def test_interrupt_during_long_turn_is_immediate():
    """一轮进行中送 interrupt：不必等这一轮流式结束，actor 立即调用 client.interrupt()。"""
    client = FakeSDKClient(interrupt_frame=_INTERRUPTED_RESULT)
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()
    try:
        q = _query("long task")
        await actor.enqueue(q)
        await q.done.wait()
        client.push_frame(assistant_frame(uuid="still-working"))

        t_before = asyncio.get_event_loop().time()
        i = SessionCommand(type="interrupt")
        await actor.enqueue(i)
        await i.done.wait()
        elapsed = asyncio.get_event_loop().time() - t_before
        assert elapsed < 0.3, f"interrupt took too long: {elapsed}s"
        assert client.interrupted
    finally:
        await _disconnect(actor)


async def test_interrupt_always_reaches_the_cli():
    """是否有轮次在途由会话层按 CLI 报告的状态判断，actor 收到 interrupt 就送给 CLI。"""
    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()
    try:
        i = SessionCommand(type="interrupt")
        await actor.enqueue(i)
        await asyncio.wait_for(i.done.wait(), timeout=1.0)
        assert i.error is None
        assert client.interrupted
    finally:
        await _disconnect(actor)


@pytest.mark.parametrize("interrupt_first", [True, False])
async def test_disconnect_interrupts_only_when_asked(interrupt_first):
    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    await _disconnect(actor, interrupt_first=interrupt_first)

    assert client.interrupted is interrupt_first
    assert client.disconnected


async def test_disconnect_completes_even_if_interrupt_fails():
    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    async def _broken_interrupt() -> None:
        raise RuntimeError("transport gone")

    client.interrupt = _broken_interrupt
    d = SessionCommand(type="disconnect", interrupt_first=True)
    await actor.enqueue(d)

    await asyncio.wait_for(d.done.wait(), timeout=1.0)
    await actor.wait()
    assert client.disconnected


class _ExplodingClient(FakeSDKClient):
    async def query(self, prompt, session_id: str = "default") -> None:
        self._record("query")
        raise RuntimeError("sdk boom")


@pytest.mark.asyncio
async def test_actor_error_propagates_to_waiter():
    client = _ExplodingClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    q = _query("hi")
    await actor.enqueue(q)
    await q.done.wait()
    assert isinstance(q.error, RuntimeError)
    assert str(q.error) == "sdk boom"
    assert actor._fatal is q.error

    # actor 已死亡；后续命令应 fast-fail
    q2 = _query("another")
    await actor.enqueue(q2)
    await q2.done.wait()
    assert q2.error is not None

    # 消费异常避免 Task exception was never retrieved 警告
    if actor._task is not None:
        with pytest.raises(RuntimeError):
            await actor._task


@pytest.mark.asyncio
async def test_actor_fatal_drains_queued_commands():
    """actor 异常退出前，队列中的命令也会被 drain，不会挂死。"""
    client = _ExplodingClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    q = _query("hi")
    # 排在后面的命令：在 _run 捕获异常后被 finally 的 drain 清理
    q_queued = _query("queued")
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
    await _disconnect(actor)

    stale = _query("hi")
    await actor.enqueue(stale)
    await stale.done.wait()
    assert stale.error is not None
    assert isinstance(stale.error, (_ActorClosed, BaseException))


@pytest.mark.asyncio
async def test_start_is_not_reentrant():
    """重复 start() 应立即触发断言，避免孤儿 task。"""
    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()
    try:
        with pytest.raises(AssertionError, match="不可重入"):
            await actor.start()
    finally:
        await _disconnect(actor)


@pytest.mark.asyncio
async def test_interrupt_failure_still_wakes_waiter():
    """client.interrupt() 抛异常时仍要 set done 并传递 error，
    避免 ManagedSession.send_interrupt 挂在 cmd.done.wait()。"""

    class _BoomClient(FakeSDKClient):
        async def interrupt(self):
            self._record("interrupt")
            raise RuntimeError("interrupt failed")

    client = _BoomClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()
    try:
        i = SessionCommand(type="interrupt")
        await actor.enqueue(i)
        # interrupt 抛异常，actor crash；cmd 仍应被唤醒并携带 error
        await asyncio.wait_for(i.done.wait(), timeout=1.0)
        assert i.error is not None
    finally:
        # actor 已 crash；cancel 清理
        await actor.cancel_and_wait()


class _HangingCancelClient(FakeSDKClient):
    """撤回请求发出后 CLI 一直不答复。"""

    def __init__(self) -> None:
        super().__init__()
        self.cancel_requested = asyncio.Event()
        self._query = SimpleNamespace(
            receive_messages=self._query.receive_messages, _send_control_request=self._never_answer
        )

    async def _never_answer(self, request: dict) -> dict:
        self.cancel_requested.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


async def test_cancelling_the_actor_while_a_withdrawal_waits_for_the_cli_still_wakes_the_caller():
    """驱逐等不到断开时会取消 actor：正在等 CLI 答复的撤回也要唤醒调用方，不能挂住它。"""
    client = _HangingCancelClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()
    cmd = SessionCommand(type="cancel", message_uuid="u-1")
    await actor.enqueue(cmd)
    await asyncio.wait_for(client.cancel_requested.wait(), timeout=1.0)

    await actor.cancel_and_wait()

    assert cmd.done.is_set()
    assert isinstance(cmd.error, _ActorClosed)


async def test_actor_exits_when_message_stream_closes():
    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    await actor.start()

    client.close_stream()

    task = actor.task
    assert task is not None
    with pytest.raises(MessageStreamClosed):
        await asyncio.wait_for(task, timeout=1.0)
    q = _query("after exit")
    await actor.enqueue(q)
    assert q.done.is_set()
    assert q.error is not None
    assert client.sent_queries == []


# --- 原始帧流 ------------------------------------------------------------------


async def test_command_lifecycle_frames_reach_the_hook_in_frame_order():
    delivered = _Collector()
    client = FakeSDKClient()
    actor = SessionActor(
        client_factory=lambda: client,
        on_message=delivered,
        on_command_lifecycle=delivered,
    )
    await actor.start()
    try:
        q = _query("hi")
        await actor.enqueue(q)
        await q.done.wait()
        client.push_frame(command_lifecycle_frame("u1", "started"))
        client.push_frame(assistant_frame(uuid="a1"))
        client.push_frame(command_lifecycle_frame("u2", "queued"))
        client.push_frame(result_frame(uuid="r1"))
        await delivered.wait_for_count(4)

        assert delivered[0] == CommandLifecycle(command_uuid="u1", state="started")
        assert isinstance(delivered[1], AssistantMessage)
        assert delivered[2] == CommandLifecycle(command_uuid="u2", state="queued")
        assert isinstance(delivered[3], ResultMessage)
    finally:
        await _disconnect(actor)


async def test_unrecognized_frames_are_dropped():
    lifecycles: list = []
    collected = _Collector()
    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=collected, on_command_lifecycle=lifecycles.append)
    await actor.start()
    try:
        client.push_frame({"type": "some_future_frame", "uuid": "unknown"})
        client.push_frame(command_lifecycle_frame("u1", "some_future_state"))
        client.push_frame(system_frame("session_state_changed", state="idle", uuid="known"))
        await collected.wait_for_count(1)

        assert [type(m) for m in collected] == [SystemMessage]
        assert lifecycles == []
    finally:
        await _disconnect(actor)
