"""SessionClient protocol — unified interface for assistant providers.

Both the Claude Agent SDK adapter and the LiteLLM adapter implement this
protocol so ``SessionManager`` can route transparently.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class AssistantEvent:
    """A single event emitted during assistant response generation.

    Mirrors the SSE event types the frontend already understands.
    """

    event_type: str  # "text_delta", "tool_use", "tool_result", "error", "done", "status"
    data: dict[str, Any]


OnEvent = Callable[[AssistantEvent], None]


@runtime_checkable
class SessionClient(Protocol):
    """Unified interface for chat session providers.

    Implementations must be stateful per-session (conversation history,
    pending tool calls, etc.) but stateless across sessions — session
    lifecycle is managed by ``SessionManager``.
    """

    async def create_session(self, project_name: str) -> str:
        """Create a new session and return its ID."""
        ...

    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        on_event: OnEvent | None = None,
    ) -> dict[str, Any]:
        """Send a user message and run the assistant loop.

        Streams events via ``on_event`` callback. Returns the final
        response dict with at least ``{"status": "completed"}`` or
        ``{"status": "error", "error": "..."}``.
        """
        ...

    async def interrupt(self, session_id: str) -> None:
        """Request interruption of an in-progress generation."""
        ...

    async def delete_session(self, session_id: str) -> None:
        """Delete session and release all resources."""
        ...

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Return conversation history for a session (for snapshot/debug)."""
        ...
