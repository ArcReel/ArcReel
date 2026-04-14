"""SessionActor: 每会话一个专属 asyncio task，封装 ClaudeSDKClient 的所有协议调用。

设计：docs/superpowers/specs/2026-04-13-session-actor-design.md
"""

from __future__ import annotations

import asyncio
import contextlib
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
        """在同一 task 内交织消费 receive_response 与新命令。
        返回：从队列取出但本轮未消化的命令（交给 _command_loop 下一轮）。
        """
        msg_iter = client.receive_response().__aiter__()
        msg_task = asyncio.create_task(msg_iter.__anext__(), name="actor-recv")
        cmd_task = asyncio.create_task(self._cmd_queue.get(), name="actor-cmd")
        pending_query: SessionCommand | None = None
        try:
            while True:
                done, _ = await asyncio.wait({msg_task, cmd_task}, return_when=asyncio.FIRST_COMPLETED)

                if msg_task in done:
                    try:
                        self._on_message(msg_task.result())
                        msg_task = asyncio.create_task(msg_iter.__anext__())
                    except StopAsyncIteration:
                        query_cmd.done.set()
                        if pending_query is not None:
                            if not cmd_task.done():
                                cmd_task.cancel()
                            return pending_query
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
                        # 违反 "drain before new query"：暂存，让消息流自然 drain 完成；
                        # 在 StopAsyncIteration 分支返回 pending_query 由下一轮 _command_loop 处理。
                        # 由 ManagedSession 层保证不会在 running 状态重复 query。
                        pending_query = next_cmd
                        cmd_task = asyncio.create_task(self._cmd_queue.get())
        finally:
            if not msg_task.done():
                msg_task.cancel()
            if not cmd_task.done():
                cmd_task.cancel()

    async def enqueue(self, cmd: SessionCommand) -> None:
        if self._task is not None and self._task.done():
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

    # --- Public accessors (avoid leaking _task to callers) -----------------

    @property
    def task(self) -> asyncio.Task | None:
        """Underlying actor task; None before start()."""
        return self._task

    def add_done_callback(self, callback: Callable[[asyncio.Task], None]) -> None:
        """Register a callback on the actor task. No-op if task not started yet."""
        if self._task is not None:
            self._task.add_done_callback(callback)

    async def wait(self) -> None:
        """Await actor task completion, swallowing any raised exception."""
        if self._task is None:
            return
        with contextlib.suppress(BaseException):
            await self._task

    async def cancel_and_wait(self) -> None:
        """Cancel the actor task and wait for it to finish."""
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with contextlib.suppress(BaseException):
            await self._task
