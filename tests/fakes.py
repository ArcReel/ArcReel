"""Shared fake / stub objects for tests.

Only objects used across multiple test files belong here.
Single-file fakes stay in their respective test modules.
"""

from __future__ import annotations

import asyncio


class FakeSDKClient:
    """Fake Claude Agent SDK client for SessionActor / SessionManager tests.

    支持：
    - `async with`：`__aenter__` 记录 connect 的 current_task，`__aexit__` 记录 disconnect
    - `method_tasks`: dict[str, list[asyncio.Task]] 记录每个方法被调用时的 task
    - `messages` 初始化参数：`receive_response` 依次 yield 的初始消息
    - `block_forever=True`：`receive_response` 在无消息时阻塞，直到 interrupt 注入尾消息
    - `interrupt_message`：`interrupt()` 被调用时注入给 `receive_response` 的最后一条消息
    - `connect_error`：`__aenter__` 时抛出的异常，用于模拟连接失败
    """

    def __init__(
        self,
        messages=None,
        *,
        block_forever: bool = False,
        interrupt_message: dict | None = None,
        connect_error: Exception | None = None,
    ):
        self._initial_messages = list(messages) if messages else []
        self._block_forever = block_forever
        self._interrupt_message = interrupt_message
        self._connect_error = connect_error
        self._pending_messages: asyncio.Queue[dict | None] = asyncio.Queue()
        self.method_tasks: dict[str, list[asyncio.Task]] = {}
        self.sent_queries: list = []
        self.interrupted = False
        self.disconnected = False
        self._closed: asyncio.Event = asyncio.Event()

    def _record(self, method: str) -> None:
        self.method_tasks.setdefault(method, []).append(asyncio.current_task())

    async def __aenter__(self):
        self._record("connect")
        if self._connect_error is not None:
            raise self._connect_error
        for msg in self._initial_messages:
            await self._pending_messages.put(msg)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._record("disconnect")
        self.disconnected = True
        self._closed.set()
        return False

    async def query(self, prompt, session_id: str = "default") -> None:
        self._record("query")
        self.sent_queries.append(prompt)

    async def interrupt(self) -> None:
        self._record("interrupt")
        self.interrupted = True
        if self._interrupt_message is not None:
            await self._pending_messages.put(self._interrupt_message)
        # 告知 receive_response "可以停止了"
        await self._pending_messages.put(None)  # sentinel

    async def receive_response(self):
        self._record("receive_response")
        while True:
            msg = await self._pending_messages.get()
            if msg is None:
                return
            yield msg
            if msg.get("type") == "result":
                return

    def push_message(self, msg: dict) -> None:
        """测试辅助：运行中往消息流注入一条消息。"""
        self._pending_messages.put_nowait(msg)

    # 向后兼容：保留原方法签名（旧测试仍使用 `await client.connect()` / `await client.disconnect()`）
    async def connect(self) -> None:
        self._record("connect")
        if self._connect_error is not None:
            raise self._connect_error

    async def disconnect(self) -> None:
        self._record("disconnect")
        self.disconnected = True
        self._closed.set()


from lib.image_backends.base import ImageCapability, ImageGenerationRequest, ImageGenerationResult


class FakeImageBackend:
    """Fake image backend for testing."""

    def __init__(self, *, provider: str = "fake", model: str = "fake-model"):
        self._provider = provider
        self._model = model

    @property
    def name(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[ImageCapability]:
        return {ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}

    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        # Minimal valid PNG (1x1 pixel)
        request.output_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
            b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
            b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return ImageGenerationResult(
            image_path=request.output_path,
            provider=self._provider,
            model=self._model,
        )
