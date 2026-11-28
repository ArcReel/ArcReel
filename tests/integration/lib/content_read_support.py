from __future__ import annotations

import builtins
import os
from collections.abc import Callable
from typing import IO, Any

import pytest


def observe_builtin_open(monkeypatch: pytest.MonkeyPatch, observe: Callable[[Any, IO[Any]], None]) -> None:
    original_open = builtins.open

    def observed_open(file: Any, *args: Any, **kwargs: Any) -> IO[Any]:
        stream = original_open(file, *args, **kwargs)
        observe(file, stream)
        return stream

    monkeypatch.setattr(builtins, "open", observed_open)


def observe_os_fds(
    monkeypatch: pytest.MonkeyPatch,
    *,
    on_open: Callable[[int], None] | None = None,
    on_close: Callable[[int], None] | None = None,
) -> None:
    if on_open is not None:
        original_open = os.open

        def observed_os_open(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            fd = original_open(path, flags, mode, dir_fd=dir_fd)
            on_open(fd)
            return fd

        monkeypatch.setattr(os, "open", observed_os_open)
    if on_close is not None:
        original_close = os.close

        def observed_os_close(fd: int) -> None:
            on_close(fd)
            original_close(fd)

        monkeypatch.setattr(os, "close", observed_os_close)
