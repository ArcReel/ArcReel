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
from tests.fakes import FakeSDKClient


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

    # 仅锁定 method 调用（receive_response 是 async generator iteration，
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
    # block_forever=True 下需要显式 None sentinel 结束 q2 的 drain
    client.push_message(None)
    # interrupt 让 receive_response 卡住的协程已结束；第二次 receive_response 会从队列拿
    await asyncio.wait_for(q2.done.wait(), timeout=1.0)

    assert client.sent_queries == ["first", "second"]

    d = SessionCommand(type="disconnect")
    await actor.enqueue(d)
    await d.done.wait()
    if actor._task is not None:
        await actor._task


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
