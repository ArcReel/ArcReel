"""Small host-neutral tool metadata used by both host adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from server.tool_runtime import ToolOutcome


@dataclass(frozen=True, slots=True)
class MediaToolResponse:
    content: tuple[dict[str, Any], ...]
    structured: dict[str, Any]
    is_error: bool

    @classmethod
    def from_response(cls, response: dict[str, Any]) -> MediaToolResponse:
        return cls(
            content=tuple(response.get("content", ())),
            structured={
                key: response[key] for key in ("generation_batch", "generation_result", "problem") if key in response
            },
            is_error=bool(response.get("is_error")),
        )

    def to_response(self) -> dict[str, Any]:
        return {"content": list(self.content), "is_error": self.is_error, **self.structured}


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

    async def invoke(self, args: dict[str, Any]) -> ToolOutcome[MediaToolResponse]:
        return ToolOutcome(value=MediaToolResponse.from_response(await self.handler(args)))


def tool(name: str, description: str, input_schema: dict[str, Any]):
    """Declare host-neutral metadata without importing a host SDK."""

    def decorate(handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> ToolDefinition:
        return ToolDefinition(name, description, input_schema, handler)

    return decorate
