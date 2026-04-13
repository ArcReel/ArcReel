"""SessionActor: 每会话一个专属 asyncio task，封装 ClaudeSDKClient 的所有协议调用。

设计：docs/superpowers/specs/2026-04-13-session-actor-design.md
"""

from __future__ import annotations

import asyncio
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
