"""SessionActor: 每会话一个专属 asyncio task，封装 ClaudeSDKClient 的所有协议调用。

设计：docs/superpowers/specs/2026-04-13-session-actor-design.md
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Literal


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
ClientFactory = Callable[[], AbstractAsyncContextManager[Any]]


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

    async def _drive_query(self, client: Any, query_cmd: SessionCommand) -> SessionCommand | None:
        """消费 receive_response 直到 StopAsyncIteration。初版不处理中途命令。"""
        async for msg in client.receive_response():
            self._on_message(msg)
        query_cmd.done.set()
        return None

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
