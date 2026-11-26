from __future__ import annotations

import builtins
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
