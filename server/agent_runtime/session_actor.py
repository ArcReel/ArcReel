"""SessionActor: 每会话一个专属 asyncio task，封装 ClaudeSDKClient 的所有协议调用。

设计决策：docs/adr/0028-session-actor-single-task-serialization.md
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Literal, assert_never

from server.agent_runtime import sdk_frames
from server.agent_runtime.sdk_frames import CommandLifecycle


class _ActorClosed(Exception):
    """Sentinel: actor 已退出（正常或异常），队列中剩余命令以此标记为 error。"""


class MessageStreamClosed(Exception):
    """SDK 消息流已终止（CLI 退出），会话不再可用。"""


async def _single_message(message: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """把一条用户消息帧包成 ``client.query`` 接受的流：只有流形式会把帧（含 uuid）原样写给 CLI。"""
    yield message


@dataclass
class SessionCommand:
    type: Literal["query", "interrupt", "cancel", "disconnect"]
    # query 送入 CLI 的用户消息帧（带服务端分配的 uuid）
    message: dict[str, Any] | None = None
    # cancel 要从 CLI 队列撤回的消息 uuid（送入时携带的那个）
    message_uuid: str | None = None
    # cancel 的结果：True 表示已从 CLI 队列撤回，False 表示 CLI 已取走或从未收到这条消息
    cancelled: bool | None = None
    session_id: str = "default"
    # disconnect 之前先中断当前轮：会话仍在 running 时由会话层置位
    interrupt_first: bool = False
    # 命令处理完毕：query 已写入 CLI 输入流（不代表 CLI 已开始处理它），或 interrupt / cancel / disconnect 已执行
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: BaseException | None = None

    def complete(self, error: BaseException | None = None) -> None:
        """唤醒等待者并可选携带 error；所有完成路径都经此处。"""
        if error is not None:
            self.error = error
        self.done.set()


OnMessage = Callable[[Any], None]
OnCommandLifecycle = Callable[[CommandLifecycle], None]
ClientFactory = Callable[[], AbstractAsyncContextManager[Any]]


class _MessagePump:
    """持续读取 SDK Query 的原始帧流，逐帧解析为 SDK 消息或 CommandLifecycle。

    SDK 解析器不认识的帧被丢弃，与 ``ClaudeSDKClient.receive_messages()`` 一致；
    帧流结束（CLI 退出）时读取抛出 ``MessageStreamClosed``。

    同一时刻至多一个在途读取 task，跨 idle 与轮次复用、只在 actor 退出时取消：
    反复取消重建会把恰好送达的消息丢在被取消的 task 里。
    """

    def __init__(self, client: Any):
        self._client = client
        self._frames: AsyncIterator[dict[str, Any]] | None = None
        self._task: asyncio.Task[Any] | None = None

    def arm(self) -> asyncio.Task[Any]:
        """返回在途读取 task，没有则新建。"""
        if self._task is None:
            self._task = asyncio.create_task(self._next(), name="actor-recv")
        return self._task

    def take(self) -> Any:
        """取走已完成读取 task 的结果：SDK 消息或 CommandLifecycle；读取异常原样抛出。"""
        task, self._task = self._task, None
        assert task is not None
        return task.result()

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _next(self) -> Any:
        frames = self._frames
        if frames is None:
            frames = self._frames = sdk_frames.raw_frames(self._client)
        while True:
            try:
                frame = await anext(frames)
            except StopAsyncIteration:
                # 退出 actor 让会话层收到退出通知、按终态收尾；留着等命令的话，
                # 后续 query 会送进已经没有 CLI 的 client。
                raise MessageStreamClosed("SDK message stream closed") from None
            item = sdk_frames.parse_frame(frame)
            if item is not None:
                return item


class SessionActor:
    """单 task 拥有一个 ClaudeSDKClient，所有 SDK 操作在同一 async context 中执行。

    actor 不撮合 query 与 result：用户消息一律立即送入 CLI，由 CLI 排队、并入当前轮或另开一轮；
    result 只代表一轮结束。轮次是否在途由会话层按 CLI 报告的会话状态判断。
    """

    def __init__(
        self,
        client_factory: ClientFactory,
        on_message: OnMessage,
        on_command_lifecycle: OnCommandLifecycle | None = None,
    ):
        self._client_factory = client_factory
        self._on_message = on_message
        # CLI 报告的用户消息去向（排队、被并入轮次、撤回等），与 on_message 按帧序在 actor task 内交付；
        # 未提供时丢弃。
        self._on_command_lifecycle = on_command_lifecycle
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
            self._drain_pending_commands(self._closed_error())

    def _closed_error(self) -> BaseException:
        """actor 退出后未处理命令的完成值：以异常退出的沿用那个异常，被取消或正常退出的报 actor 已关闭。

        取消是 actor 自己的控制流，不能交给调用方：调用方在另一个 task 里 ``raise cmd.error``，
        ``CancelledError`` 会越过 ``except Exception`` 一路穿出 HTTP 请求。
        """
        fatal = self._fatal
        return _ActorClosed() if fatal is None else self._as_closed_error(fatal)

    @staticmethod
    def _as_closed_error(exc: BaseException) -> BaseException:
        """交给调用方的完成值：取消换成 actor 已关闭，其余异常原样。"""
        return _ActorClosed() if isinstance(exc, asyncio.CancelledError) else exc

    async def _command_loop(self, client: Any) -> None:
        """在同一 task 内交织消费消息流与命令队列，直到 disconnect。

        消息流在会话存续期间持续读取，不随 query 起止：后台任务完成后 CLI 会不经 query
        自主开启新一轮，idle 时停读会让这一轮滞留在 SDK 缓冲里，直到下一次 query 才被读出。
        """
        pump = _MessagePump(client)
        cmd_task: asyncio.Task[SessionCommand] | None = None
        try:
            while True:
                if cmd_task is None:
                    cmd_task = asyncio.create_task(self._cmd_queue.get(), name="actor-cmd")
                msg_task = pump.arm()
                done, _ = await asyncio.wait({cmd_task, msg_task}, return_when=asyncio.FIRST_COMPLETED)

                if msg_task in done:
                    item = pump.take()
                    if isinstance(item, CommandLifecycle):
                        if self._on_command_lifecycle is not None:
                            self._on_command_lifecycle(item)
                    else:
                        self._on_message(item)

                if cmd_task not in done:
                    continue
                cmd, cmd_task = cmd_task.result(), None
                if await self._dispatch(client, cmd):
                    return  # 触发 __aexit__，同 task disconnect
        finally:
            pump.cancel()
            if cmd_task is not None:
                if not cmd_task.done():
                    cmd_task.cancel()
                elif not cmd_task.cancelled() and cmd_task.exception() is None:
                    # 与消息流异常同轮取出、尚未处理的命令：不释放会让调用方挂死
                    cmd_task.result().complete(self._closed_error())

    async def _dispatch(self, client: Any, cmd: SessionCommand) -> bool:
        """执行一条已出队的命令，并保证它在每条退出路径上都被 complete。返回 True 表示 disconnect，命令循环应退出。

        命令出队后队列清理够不着它，处理器抛错或 actor 被取消时只有这里能唤醒等待者。
        处理器自己 complete 过的（撤回的非致命失败）保持原值；其余正常返回按成功收口，
        异常退出挂上那个异常，actor 被取消则报 actor 已关闭，异常照常向上传播让 actor 退出。
        """
        try:
            if cmd.type == "disconnect":
                if cmd.interrupt_first:
                    await client.interrupt()
            elif cmd.type == "interrupt":
                await client.interrupt()
            elif cmd.type == "query":
                assert cmd.message is not None
                await client.query(_single_message(cmd.message), session_id=cmd.session_id)
            elif cmd.type == "cancel":
                await self._cancel_message(client, cmd)
            else:
                assert_never(cmd.type)
        except BaseException as exc:
            if not cmd.done.is_set():
                cmd.complete(self._as_closed_error(exc))
            raise
        if not cmd.done.is_set():
            cmd.complete()
        return cmd.type == "disconnect"

    @staticmethod
    async def _cancel_message(client: Any, cmd: SessionCommand) -> None:
        """撤回失败（CLI 报错、控制请求超时）只回报给调用方：消息流仍在，会话照常可用。"""
        assert cmd.message_uuid is not None
        try:
            cmd.cancelled = await sdk_frames.cancel_async_message(client, cmd.message_uuid)
        except Exception as exc:
            cmd.complete(exc)

    async def enqueue(self, cmd: SessionCommand) -> None:
        if self._task is not None and self._task.done():
            cmd.complete(self._closed_error())
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
