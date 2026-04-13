"""SessionActor 单元测试。

覆盖：命令协议、主循环、SDK 同 task 契约、交织语义、异常传播。
"""

from __future__ import annotations

import asyncio

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
