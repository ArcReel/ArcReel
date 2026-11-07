"""SessionActor: one dedicated asyncio task per session encapsulating every ClaudeSDKClient protocol operation.

Design decision: docs/adr/0028-session-actor-single-task-serialization.md"""

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
    """Sentinel for an actor that exited normally or exceptionally, marking remaining queued commands as errors."""


class MessageStreamClosed(Exception):
    """The SDK message stream ended after CLI exit, making the session unavailable."""


async def _single_message(message: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Wrap a user-message frame as a ``client.query`` stream; only streaming writes frames including uuid unchanged to the CLI."""
    yield message


@dataclass
class SessionCommand:
    type: Literal["query", "interrupt", "cancel", "disconnect"]
    # query user-message frame sent to CLI with a server-assigned uuid.
    message: dict[str, Any] | None = None
    # cancel message uuid to withdraw from the CLI queue, the original submitted uuid.
    message_uuid: str | None = None
    # cancel result: True for queue withdrawal, False if CLI already took the message or never received it.
    cancelled: bool | None = None
    session_id: str = "default"
    # Interrupt the current turn before disconnect; the session layer sets this while running.
    interrupt_first: bool = False
    # Command completed: query written to CLI input, not necessarily processing yet, or interrupt/cancel/disconnect executed.
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: BaseException | None = None

    def complete(self, error: BaseException | None = None) -> None:
        """Wake waiters with an optional error; every completion path passes here."""
        if error is not None:
            self.error = error
        self.done.set()


OnMessage = Callable[[Any], None]
OnCommandLifecycle = Callable[[CommandLifecycle], None]
ClientFactory = Callable[[], AbstractAsyncContextManager[Any]]


class _MessagePump:
    """Continuously read SDK Query raw frames, parsing SDK messages or CommandLifecycle one frame at a time.

    Drop unknown frames like ``ClaudeSDKClient.receive_messages()``. Ended frame streams after CLI exit raise
    ``MessageStreamClosed``.

    Maintain at most one in-flight read task, reused across idle periods/turns and cancelled only on actor exit:
    repeated cancellation/recreation could lose messages arriving just as their task is cancelled."""

    def __init__(self, client: Any):
        self._client = client
        self._frames: AsyncIterator[dict[str, Any]] | None = None
        self._task: asyncio.Task[Any] | None = None

    def arm(self) -> asyncio.Task[Any]:
        """Return the in-flight read task, creating one if absent."""
        if self._task is None:
            self._task = asyncio.create_task(self._next(), name="actor-recv")
        return self._task

    def take(self) -> Any:
        """Take a completed read task’s SDK-message/CommandLifecycle result, propagating read exceptions unchanged."""
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
                # Exit the actor so session layers receive terminal notifications; waiting for more commands
                # would send later queries to a client without a CLI.
                raise MessageStreamClosed("SDK message stream closed") from None
            item = sdk_frames.parse_frame(frame)
            if item is not None:
                return item


class SessionActor:
    """One task owns one ClaudeSDKClient, executing all SDK operations in the same async context.

    Actors do not pair query/result: user messages immediately enter the CLI, which queues them, joins current turns
    or starts new ones. Results indicate only turn completion; the session layer determines in-flight turns from CLI state."""

    def __init__(
        self,
        client_factory: ClientFactory,
        on_message: OnMessage,
        on_command_lifecycle: OnCommandLifecycle | None = None,
    ):
        self._client_factory = client_factory
        self._on_message = on_message
        # Deliver CLI user-message lifecycle events, queued/joined/withdrawn etc., with on_message in frame order inside the actor task;
        # discard if no callback is supplied.
        self._on_command_lifecycle = on_command_lifecycle
        self._cmd_queue: asyncio.Queue[SessionCommand] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._started: asyncio.Event = asyncio.Event()
        self._fatal: BaseException | None = None

    async def start(self) -> None:
        """Start the actor task and return only after successful connect or fail-fast."""
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
            # Drain remaining commands on normal/exceptional exit to prevent hung callers.
            self._drain_pending_commands(self._closed_error())

    def _closed_error(self) -> BaseException:
        """Completion for unhandled commands after actor exit: preserve exceptional exits; cancellation/normal exit reports actor closed.

        Cancellation is actor-local control flow and cannot reach callers: another task’s ``raise cmd.error`` with
        ``CancelledError`` would bypass ``except Exception`` and escape the HTTP request."""
        fatal = self._fatal
        return _ActorClosed() if fatal is None else self._as_closed_error(fatal)

    @staticmethod
    def _as_closed_error(exc: BaseException) -> BaseException:
        """Caller completion: replace cancellation with actor-closed, preserving other exceptions."""
        return _ActorClosed() if isinstance(exc, asyncio.CancelledError) else exc

    async def _command_loop(self, client: Any) -> None:
        """Interleave message-stream and command-queue consumption in one task until disconnect.

        Read throughout session lifetime, independently of query boundaries: completed background tasks may make the CLI
        start turns without queries. Pausing reads while idle would strand such turns in SDK buffers until the next query."""
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
                    return  # Trigger __aexit__, disconnecting in the same task.
        finally:
            pump.cancel()
            if cmd_task is not None:
                if not cmd_task.done():
                    cmd_task.cancel()
                elif not cmd_task.cancelled() and cmd_task.exception() is None:
                    # Complete unhandled commands dequeued alongside stream errors, or callers would hang.
                    cmd_task.result().complete(self._closed_error())

    async def _dispatch(self, client: Any, cmd: SessionCommand) -> bool:
        """Execute a dequeued command and complete it on every exit path; True indicates disconnect and ends the command loop.

        Queue cleanup cannot reach dequeued commands, so only this point wakes waiters on handler errors/actor cancellation.
        Preserve handler-completed outcomes, including nonfatal withdrawal failures. Other normal returns succeed; exceptional
        returns retain their exception, actor cancellation reports actor closed, and errors propagate to terminate the actor."""
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
        """Withdrawal failures, CLI errors or control timeouts, affect only callers; the message stream/session remains usable."""
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
            _ = await self._task  # Result intentionally discarded; awaiting itself is the intended side effect.

    async def cancel_and_wait(self) -> None:
        """Cancel the actor task and wait for it to finish."""
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with contextlib.suppress(BaseException):
            _ = await self._task  # result intentionally discarded
