"""SessionActor: 每会话一个专属 asyncio task，封装 ClaudeSDKClient 的所有协议调用。

设计决策：docs/adr/0028-session-actor-single-task-serialization.md
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterable, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Literal

from server.agent_runtime.message_serialization import is_main_turn_activity


class _ActorClosed(Exception):
    """Sentinel: actor 已退出（正常或异常），队列中剩余命令以此标记为 error。"""


class MessageStreamClosed(Exception):
    """SDK 消息流已终止（CLI 退出），会话不再可用。"""


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
    # 仅在 client.query() 正常返回后置真；与 sent 分开，因为失败 complete()
    # 也会唤醒 sent，但那不代表请求已被 SDK 受理。
    accepted: bool = False
    # query 送入 SDK 的那一刻在 actor task 内同步回调：被暂存的 query 晚于调用方
    # 的 await 才送入，按送入时刻记账只能在这里做。
    on_accepted: Callable[[], None] | None = None

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

# _MessagePump 在一次 receive_response() 迭代结束（读到 result）时产出的标记
_TURN_END = object()


class _MessagePump:
    """持续读取 SDK 消息流，一轮接一轮地重开 receive_response()。

    同一时刻至多一个在途读取 task，跨 idle 与轮次复用、只在 actor 退出时取消：
    反复取消重建会把恰好送达的消息丢在被取消的 task 里。
    """

    def __init__(self, client: Any):
        self._client = client
        self._iter: AsyncIterator[Any] | None = None
        self._yielded = False
        # 消息流已关闭（CLI 退出）：一次迭代未产出任何消息即结束
        self._closed = False
        self._task: asyncio.Task[Any] | None = None

    def arm(self) -> asyncio.Task[Any] | None:
        """返回在途读取 task，没有则新建；流已关闭时返回 None。"""
        if self._task is None and not self._closed:
            self._task = asyncio.create_task(self._next(), name="actor-recv")
        return self._task

    def take(self) -> Any:
        """取走已完成读取 task 的结果：一条消息或 _TURN_END；读取异常原样抛出。"""
        task, self._task = self._task, None
        assert task is not None
        return task.result()

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _next(self) -> Any:
        iterator = self._iter
        if iterator is None:
            iterator = self._iter = self._client.receive_response().__aiter__()
            self._yielded = False
        try:
            msg = await iterator.__anext__()
        except StopAsyncIteration:
            self._iter = None
            if not self._yielded:
                self._closed = True
            return _TURN_END
        self._yielded = True
        return msg


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
        """在同一 task 内交织消费消息流与命令队列，直到 disconnect。

        消息流在会话存续期间持续读取，不随 query 起止：result 只代表一轮结束，
        后台任务完成后 CLI 会不经 query 自主开启新一轮，idle 时停读会让这一轮
        滞留在 SDK 缓冲里，直到下一次 query 才被读出。
        """
        pump = _MessagePump(client)
        cmd_task: asyncio.Task[SessionCommand] | None = None
        # 已送入 SDK、等待本轮 result 的 query
        active_query: SessionCommand | None = None
        # 有轮次在途时送来的 query：暂存到本轮 result 后再送入 SDK
        pending_query: SessionCommand | None = None
        # 无 query 在途时收到过主线程帧：CLI 自主开启的一轮尚未见到 result
        unsolicited_open = False
        try:
            while True:
                if cmd_task is None:
                    cmd_task = asyncio.create_task(self._cmd_queue.get(), name="actor-cmd")
                msg_task = pump.arm()
                if msg_task is None:
                    # 退出 actor 让会话层收到退出通知、按终态收尾；留着等命令的话，
                    # 后续 query 会送进已经没有 CLI 的 client，轮次永远等不到 result。
                    raise MessageStreamClosed("SDK message stream closed")
                done, _ = await asyncio.wait({cmd_task, msg_task}, return_when=asyncio.FIRST_COMPLETED)

                if msg_task in done:
                    item = pump.take()
                    if item is _TURN_END:
                        unsolicited_open = False
                        if active_query is not None:
                            active_query.complete()
                            active_query = None
                        if pending_query is not None:
                            active_query, pending_query = pending_query, None
                            await self._send_query(client, active_query)
                    else:
                        # 只认主线程帧：result 之后的 system 帧、后台子智能体的消息
                        # 之后不会再有 result 来收尾，据此开轮会把后续 query 永远暂存
                        if active_query is None and is_main_turn_activity(item):
                            unsolicited_open = True
                        self._on_message(item)

                if cmd_task not in done:
                    continue
                cmd, cmd_task = cmd_task.result(), None

                if cmd.type == "disconnect":
                    # cmd 已出队，interrupt 抛错时 finally 与队列清理都够不着它，在此兜底释放
                    try:
                        if active_query is not None or unsolicited_open:
                            # 先 interrupt 让在途轮次收尾；这一轮的 result 不会再被读取，
                            # 显式 complete 以兑现 "done 必定转换" 的契约。
                            await client.interrupt()
                            if active_query is not None:
                                active_query.complete()
                                active_query = None
                    finally:
                        cmd.complete()
                    return  # 触发 __aexit__，同 task disconnect
                if cmd.type == "interrupt":
                    if active_query is None and not unsolicited_open:
                        # 没有在途轮次；interrupt 无操作，但仍 ACK
                        cmd.complete()
                        continue
                    # 无论 client.interrupt() 成败都要唤醒等待者——常规失败时
                    # 把异常挂到 cmd.error 透传给 send_interrupt；CancelledError 等
                    # 控制流异常不拦截，但 finally 仍保证 cmd 被 complete 避免挂死。
                    caught: Exception | None = None
                    try:
                        await client.interrupt()
                    except Exception as exc:
                        caught = exc
                    finally:
                        cmd.complete(caught)
                    if caught is not None:
                        raise caught
                elif cmd.type == "query":
                    if active_query is None and not unsolicited_open:
                        active_query = cmd
                        await self._send_query(client, cmd)
                    elif pending_query is None:
                        # 有轮次在途（自己的 query 或 CLI 自主开启的一轮）：暂存，等本轮
                        # result 后再送入。插进自主轮次会让它的 result 被当成这条 query 的。
                        pending_query = cmd
                    else:
                        # 上层 race 送来第三个 query：拒绝（FIFO 只保留第一个暂存）
                        cmd.complete(RuntimeError("session busy: 当前会话已有待执行 query"))
        except BaseException as exc:
            if active_query is not None:
                active_query.complete(active_query.error or exc)
            raise
        finally:
            pump.cancel()
            if cmd_task is not None:
                if not cmd_task.done():
                    cmd_task.cancel()
                elif not cmd_task.cancelled() and cmd_task.exception() is None:
                    # 与消息流异常同轮取出、尚未处理的命令：不释放会让调用方挂死
                    cmd_task.result().complete(_ActorClosed())
            # 异常退出路径下 pending_query 已脱离队列，必须显式释放等待者
            if pending_query is not None and not pending_query.done.is_set():
                pending_query.complete(pending_query.error or _ActorClosed())

    @staticmethod
    async def _send_query(client: Any, cmd: SessionCommand) -> None:
        try:
            await client.query(cmd.prompt, session_id=cmd.session_id)
        except BaseException as exc:
            cmd.complete(exc)
            raise
        # prompt 已送入 SDK：释放 HTTP 路径，actor 继续在后台 drain 消息流
        cmd.accepted = True
        if cmd.on_accepted is not None:
            cmd.on_accepted()
        cmd.sent.set()

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
