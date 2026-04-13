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
    - `receive_response` 默认在 yield `type="result"` 后结束；
    - `block_forever=True` 时，仅在 `interrupt()` 注入 None sentinel 后才结束（用于测试 interrupt 中断 query 的场景）
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
            if msg.get("type") == "result" and not self._block_forever:
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


async def build_managed_with_actor(
    *,
    session_id: str = "s1",
    project_name: str = "demo",
    status: str = "idle",
    messages: list[dict] | None = None,
    block_forever: bool = False,
    on_message_hook=None,
):
    """测试辅助：围绕 FakeSDKClient 创建 SessionActor + ManagedSession，并启动 actor。

    返回 (managed, actor, client)。测试完成后调用 `await managed.send_disconnect()`
    清理，或由调用方自行管理生命周期。
    """
    from contextlib import asynccontextmanager

    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession

    client = FakeSDKClient(messages=messages, block_forever=block_forever)

    @asynccontextmanager
    async def _factory_cm():
        async with client as c:
            yield c

    managed_ref: list = [None]

    def _on_message(msg):
        m = managed_ref[0]
        if m is None:
            return
        if on_message_hook is not None:
            on_message_hook(m, msg)
        else:
            m._on_actor_message(msg)

    actor = SessionActor(client_factory=_factory_cm, on_message=_on_message)
    managed = ManagedSession(
        session_id=session_id,
        actor=actor,
        status=status,  # type: ignore[arg-type]
        project_name=project_name,
    )
    managed_ref[0] = managed
    await actor.start()
    return managed, actor, client


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
