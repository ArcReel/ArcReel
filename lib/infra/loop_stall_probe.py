"""Event-loop stall probe: log the event-loop thread's stack when a stall exceeds the threshold.

The heartbeat coroutine records time on the event loop once per heartbeat interval; a
watchdog thread wakes at the same interval to check. The loop is expected to remain idle
for one interval after a heartbeat, so stalls are measured from when the next heartbeat
is due. The logged duration is therefore a lower bound. Above the threshold, obtain the
loop thread's current frame with ``sys._current_frames()``, format it and log it. The
watchdog is a normal Python thread: stack access and formatting hold the GIL, avoiding
partially modified frames. Consequently, if the event-loop thread holds the GIL for a
long time in C code, stack capture must wait for its release. Capture only the loop
thread's stack: it suffices to locate stalls and minimizes dump overhead.

Deduplication and rate limiting:

- Log only once per stall (between two heartbeats), regardless of how often the watchdog wakes during it.
- Repeated stalls with the same stack within the repeat window increment a count without
  logging the stack again. At window expiry, combine repeats into one summary; the next
  occurrence gets a new full record. Compare each frame's file and function, excluding
  line numbers: stalls at different lines in the same loop count as the same location.
  Both full records and summaries include a stack ID to correlate them.

Uses only the standard library; the heartbeat itself contains no blocking calls.
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

# Heartbeat interval in seconds; the watchdog checks at the same interval by default.
HEARTBEAT_INTERVAL_SECONDS = 1.0
# Event-loop stall duration in seconds after which to log the stack.
STALL_THRESHOLD_SECONDS = 2.0
# Window in seconds for combining repeated stalls with the same stack.
REPEAT_WINDOW_SECONDS = 300.0

_StackKey = tuple[tuple[str, str], ...]


@dataclass(slots=True)
class _RepeatedStall:
    window_start: float
    stack_id: str
    count: int = 0


class LoopStallProbe:
    """Stall probe for one event loop; created and started by `start_loop_stall_probe`, stopped by `stop_loop_stall_probe`."""

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
        # (heartbeat sequence, heartbeat time); assigned together so the watchdog always reads a consistent pair.
        self._beat: tuple[int, float] = (0, clock())
        self._reported_beat: int | None = None
        self._repeats: dict[_StackKey, _RepeatedStall] = {}
        self._stopped = threading.Event()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._watchdog = threading.Thread(target=self._watch, name="loop-stall-probe", daemon=True)

    def check(self) -> None:
        """One check per watchdog wake-up. Only the watchdog calls it in production; tests may call it directly."""
        now = self._clock()
        self._flush_repeats(now)
        beat, beat_at = self._beat
        # A stall may start anywhere between heartbeats; definite stall time starts only when the next heartbeat was due.
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
    """Start the heartbeat coroutine and watchdog on the current event loop; ``check_interval`` defaults to the heartbeat interval."""
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
    """Stop the watchdog and heartbeat coroutine; the probe logs nothing after returning.

    Signal the watchdog before waiting for the heartbeat: if shutdown itself is cancelled,
    ``await`` raises cancellation again, so notification must precede it.
    """
    probe._stopped.set()
    if probe._heartbeat_task is not None:
        probe._heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await probe._heartbeat_task
    await asyncio.to_thread(probe._watchdog.join)
