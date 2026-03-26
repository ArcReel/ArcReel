"""Shared fake / stub objects for tests.

Only objects used across multiple test files belong here.
Single-file fakes stay in their respective test modules.
"""

from __future__ import annotations


class FakeSDKClient:
    """Fake Claude Agent SDK client for testing SessionManager.

    Consolidates the three FakeClient variants previously scattered across
    ``test_session_manager_user_input`` and ``test_session_manager_sdk_session_id``.

    Usage::

        # Simple (no streaming):
        client = FakeSDKClient()

        # With pre-loaded streaming messages:
        client = FakeSDKClient(messages=[stream_event, result_msg])

        # Track queries / interrupts:
        await client.query("hello")
        assert client.sent_queries == ["hello"]
    """

    def __init__(self, messages=None):
        self._messages = list(messages) if messages else []
        self.sent_queries: list[str] = []
        self.interrupted = False
        self.disconnected = False

    async def query(self, content: str) -> None:
        self.sent_queries.append(content)

    async def interrupt(self) -> None:
        self.interrupted = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def connect(self) -> None:
        self.disconnected = False

    async def receive_response(self):
        for message in self._messages:
            yield message


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
