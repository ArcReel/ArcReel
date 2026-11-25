"""Signal-driven pre-shutdown for the application lifespan."""

import asyncio
import logging
import signal
import threading
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from types import FrameType, MethodType

from uvicorn import Server
from uvicorn.server import HANDLED_SIGNALS

logger = logging.getLogger(__name__)


class ShutdownController:
    def __init__(self) -> None:
        self.requested = asyncio.Event()
        self._callbacks: list[Callable[[], None]] = []

    def on_shutdown(self, callback: Callable[[], None]) -> None:
        if self.requested.is_set():
            callback()
        else:
            self._callbacks.append(callback)

    def request(self) -> None:
        if self.requested.is_set():
            return
        self.requested.set()
        for callback in self._callbacks:
            try:
                callback()
            except Exception:
                logger.exception("预关停回调执行失败")

    @contextmanager
    def capture_signals(self) -> Generator[None]:
        loop = asyncio.get_running_loop()
        with ExitStack() as restore:
            if threading.current_thread() is threading.main_thread():
                for sig in HANDLED_SIGNALS:
                    previous = signal.getsignal(sig)
                    server = getattr(previous, "__self__", None)
                    if not isinstance(server, Server) or not callable(previous):
                        continue

                    def handle(server: Server, signum: int, frame: FrameType | None, original=previous) -> None:
                        loop.call_soon_threadsafe(self.request)
                        original(signum, frame)

                    signal.signal(sig, MethodType(handle, server))
                    restore.callback(signal.signal, sig, previous)
                    if server.should_exit:
                        self.request()
            yield
