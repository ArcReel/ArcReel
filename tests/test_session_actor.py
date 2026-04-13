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
