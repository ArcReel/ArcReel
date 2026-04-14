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
    # query 的 prompt 已被送入 SDK（不代表整轮响应结束）；非 query 命令与 done 同时置位
    sent: asyncio.Event = field(default_factory=asyncio.Event)
    # query 整轮 receive_response drain 完成；非 query 命令也用它标记处理完毕
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: BaseException | None = None

    def complete(self, error: BaseException | None = None) -> None:
        """唤醒所有等待者（sent + done）并可选携带 error。

        集中定义避免漏置 sent 或 done 导致调用方挂死——历次 review 发现过
        多个 "只 set done 忘了 set sent" 的回归，此 helper 作为单一契约点。
        """
        if error is not None:
            self.error = error
        self.sent.set()
        self.done.set()


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
        assert self._task is None, "SessionActor.start() 不可重入调用"
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
        fatal = self._fatal
        if fatal is not None:
            raise fatal

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
                cmd.complete()
                return  # 触发 __aexit__，同 task disconnect

            if cmd.type == "query":
                try:
                    await client.query(cmd.prompt, session_id=cmd.session_id)
                    # prompt 已送入 SDK：释放 HTTP 路径，actor 继续在后台 drain 消息流
                    cmd.sent.set()
                    deferred_cmd = await self._drive_query(client, cmd)
                except BaseException as exc:
                    cmd.complete(exc)
                    raise
            elif cmd.type == "interrupt":
                # 当前无 query 进行中；interrupt 无操作，但仍 ACK
                cmd.complete()

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
                            # 若 cmd_task 又 race 到下一条命令，回塞到队列避免丢失
                            if cmd_task.done():
                                self._cmd_queue.put_nowait(cmd_task.result())
                            else:
                                cmd_task.cancel()
                            handed_off, pending_query = pending_query, None
                            return handed_off
                        if cmd_task.done():
                            return cmd_task.result()
                        cmd_task.cancel()
                        return None

                if cmd_task in done:
                    next_cmd = cmd_task.result()
                    if next_cmd.type == "interrupt":
                        # 无论 client.interrupt() 成败都要唤醒等待者——失败时
                        # 把异常挂到 cmd.error 上透传给 send_interrupt
                        caught: BaseException | None = None
                        try:
                            await client.interrupt()
                        except BaseException as exc:
                            caught = exc
                        finally:
                            next_cmd.complete(caught)
                        if caught is not None:
                            raise caught
                        cmd_task = asyncio.create_task(self._cmd_queue.get())
                    elif next_cmd.type == "disconnect":
                        # drive_query 内部遇到 disconnect：先 interrupt 让消息流收尾，
                        # 然后把 disconnect 命令携带回 _command_loop 处理。
                        # query_cmd.done 在此分支下永远不会有 StopAsyncIteration 触发，
                        # 显式 set 以兑现 "done 必定转换" 的隐式契约（保护未来调用方）。
                        await client.interrupt()
                        query_cmd.done.set()
                        return next_cmd
                    elif next_cmd.type == "query":
                        if pending_query is not None:
                            # 上层 race 送来第三个 query：拒绝（FIFO 只保留第一个暂存）
                            next_cmd.complete(RuntimeError("session busy: 当前会话已有待执行 query"))
                        else:
                            # 违反 "drain before new query"：暂存，让消息流自然 drain 完成；
                            # 在 StopAsyncIteration 分支返回 pending_query 由下一轮 _command_loop 处理。
                            pending_query = next_cmd
                        cmd_task = asyncio.create_task(self._cmd_queue.get())
        finally:
            if not msg_task.done():
                msg_task.cancel()
            if not cmd_task.done():
                cmd_task.cancel()
            # 异常退出路径下 pending_query 已脱离队列，必须显式释放等待者
            if pending_query is not None and not pending_query.done.is_set():
                pending_query.complete(pending_query.error or _ActorClosed())

    async def enqueue(self, cmd: SessionCommand) -> None:
        if self._task is not None and self._task.done():
            cmd.complete(self._fatal or _ActorClosed())
            return
        await self._cmd_queue.put(cmd)

    def _drain_pending_commands(self, exc: BaseException) -> None:
        while not self._cmd_queue.empty():
            try:
                cmd = self._cmd_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not cmd.done.is_set():
                cmd.complete(exc)

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
            _ = await self._task  # result intentionally discarded; await 的等待副作用才是意图

    async def cancel_and_wait(self) -> None:
        """Cancel the actor task and wait for it to finish."""
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with contextlib.suppress(BaseException):
            _ = await self._task  # result intentionally discarded
