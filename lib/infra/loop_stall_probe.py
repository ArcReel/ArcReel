"""事件循环卡顿探针：事件循环停滞超过阈值时，把事件循环线程的调用栈写进日志。

心跳协程每隔一个心跳间隔在事件循环上记一次时间；守护线程按同样的间隔醒来检查。心跳之后事件循环
本就要空闲一个心跳间隔，停滞从下一次心跳该到的时刻算起，日志里的停滞时长因此是下限。停滞超过阈值时，
用 ``sys._current_frames()`` 取事件循环线程的当前帧，格式化后经 logger 写出。守护线程是普通
Python 线程，取栈与格式化都在持有 GIL 时进行，不会读到其他线程改到一半的帧。代价是事件循环线程
在 C 代码里长时间不释放 GIL 时，取栈要等到它释放之后。只取事件循环线程一份栈：定位卡顿只需要
这一份，转储本身的开销也最小。

去重与限频：

- 一次停滞（两次心跳之间）只记一条，无论守护线程在停滞期间醒来几次。
- 同一调用栈在重复窗口内再次停滞时不重复写栈，只计数；窗口到期时有重复的话合并成一条汇总，
  之后再出现按新的一次完整记录。调用栈按每一帧的文件与函数名比对，不比较行号：停在同一段循环里
  不同行的停滞算同一处。完整记录与汇总都带调用栈编号，用来把汇总对回完整的调用栈。

只用标准库，心跳本身不含阻塞调用。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 心跳间隔（秒），守护线程默认按同样的间隔醒来检查。
HEARTBEAT_INTERVAL_SECONDS = 1.0
# 事件循环停滞多久后记录调用栈（秒）。
STALL_THRESHOLD_SECONDS = 2.0
# 同一调用栈的重复停滞在这段时间内合并计数（秒）。
REPEAT_WINDOW_SECONDS = 300.0

_StackKey = tuple[tuple[str, str], ...]


@dataclass(slots=True)
class _RepeatedStall:
    window_start: float
    stack_id: str
    count: int = 0


class LoopStallProbe:
    """一个事件循环的卡顿探针；由 `start_loop_stall_probe` 创建并启动，`stop_loop_stall_probe` 收尾。"""

    def __init__(
        self,
        *,
        loop_thread_id: int,
        stall_threshold: float,
        heartbeat_interval: float,
        check_interval: float,
        repeat_window: float,
        clock: Callable[[], float],
    ) -> None:
        self._loop_thread_id = loop_thread_id
        self._stall_threshold = stall_threshold
        self._heartbeat_interval = heartbeat_interval
        self._check_interval = check_interval
        self._repeat_window = repeat_window
        self._clock = clock
        # (心跳序号, 心跳时刻)；整体赋值，守护线程读到的两项总是同一次心跳的。
        self._beat: tuple[int, float] = (0, clock())
        self._reported_beat: int | None = None
        self._repeats: dict[_StackKey, _RepeatedStall] = {}
        self._stopped = threading.Event()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._watchdog = threading.Thread(target=self._watch, name="loop-stall-probe", daemon=True)

    def check(self) -> None:
        """守护线程每次醒来做的一次检查。生产中只由守护线程调用，测试可以直接调用来驱动。"""
        now = self._clock()
        self._flush_repeats(now)
        beat, beat_at = self._beat
        # 停滞可能始于两次心跳之间的任一时刻，确定的停滞只从下一次心跳该到时算起。
        stalled_for = now - beat_at - self._heartbeat_interval
        if stalled_for <= self._stall_threshold or beat == self._reported_beat:
            return
        self._reported_beat = beat
        frame = sys._current_frames().get(self._loop_thread_id)
        if frame is None:
            return
        stack = traceback.extract_stack(frame)
        del frame
        key = tuple((entry.filename, entry.name) for entry in stack)
        repeated = self._repeats.get(key)
        if repeated is not None:
            repeated.count += 1
            return
        stack_id = hashlib.sha1(repr(key).encode(), usedforsecurity=False).hexdigest()[:8]
        self._repeats[key] = _RepeatedStall(window_start=now, stack_id=stack_id)
        logger.warning(
            "事件循环已停滞至少 %.1f 秒，事件循环线程的调用栈 %s（最近的调用在最后）：\n%s",
            stalled_for,
            stack_id,
            "".join(stack.format()).rstrip("\n"),
        )

    def _flush_repeats(self, now: float) -> None:
        for key, repeated in list(self._repeats.items()):
            if now - repeated.window_start < self._repeat_window:
                continue
            del self._repeats[key]
            if repeated.count:
                logger.warning(
                    "过去 %.0f 秒内，事件循环又在调用栈 %s 上停滞了 %d 次；完整调用栈见该编号的上一条停滞日志",
                    now - repeated.window_start,
                    repeated.stack_id,
                    repeated.count,
                )

    def _watch(self) -> None:
        while not self._stopped.wait(self._check_interval):
            self.check()

    async def _heartbeat(self) -> None:
        while True:
            beat, _ = self._beat
            self._beat = (beat + 1, self._clock())
            await asyncio.sleep(self._heartbeat_interval)


def start_loop_stall_probe(
    *,
    stall_threshold: float = STALL_THRESHOLD_SECONDS,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
    check_interval: float | None = None,
    repeat_window: float = REPEAT_WINDOW_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> LoopStallProbe:
    """在当前事件循环上启动心跳协程与守护线程；``check_interval`` 缺省时与心跳间隔相同。"""
    loop = asyncio.get_running_loop()
    probe = LoopStallProbe(
        loop_thread_id=threading.get_ident(),
        stall_threshold=stall_threshold,
        heartbeat_interval=heartbeat_interval,
        check_interval=heartbeat_interval if check_interval is None else check_interval,
        repeat_window=repeat_window,
        clock=clock,
    )
    probe._heartbeat_task = loop.create_task(probe._heartbeat(), name="loop-stall-probe")
    probe._watchdog.start()
    return probe


async def stop_loop_stall_probe(probe: LoopStallProbe) -> None:
    """停掉守护线程与心跳协程，返回后探针不再写日志。

    先通知守护线程停止再等心跳退出：关停本身被取消时，``await`` 处会再次抛出取消，通知不能排在它后面。
    """
    probe._stopped.set()
    if probe._heartbeat_task is not None:
        probe._heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await probe._heartbeat_task
    await asyncio.to_thread(probe._watchdog.join)
