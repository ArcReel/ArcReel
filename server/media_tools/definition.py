"""Small host-neutral tool metadata used by both host adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from lib.generation.generation_batch import GenerationBatchReadModel
from server.agent_toolset.envelope import json_value
from server.media_tools.context import generation_is_error, generation_structured, generation_summary
from server.tool_runtime import ToolOutcome


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[ToolOutcome[Any]]]

    async def invoke(self, args: dict[str, Any]) -> ToolOutcome[Any]:
        return await self.handler(args)


def media_outcome_payload(
    definition: ToolDefinition, outcome: ToolOutcome[Any]
) -> tuple[dict[str, Any], str | None, bool]:
    """Project one typed media outcome; each host still owns its envelope."""
    if outcome.problem is not None:
        return {"problem": json_value(outcome.problem)}, None, True
    value = outcome.value
    if isinstance(value, GenerationBatchReadModel) or (isinstance(value, dict) and "generation_result" in value):
        return generation_structured(value), generation_summary(value), generation_is_error(value)
    return {definition.name: json_value(value)}, None, False


def tool(name: str, description: str, input_schema: dict[str, Any]):
    """Declare host-neutral metadata without importing a host SDK."""

    def decorate(handler: Callable[[dict[str, Any]], Awaitable[ToolOutcome[Any]]]) -> ToolDefinition:
        return ToolDefinition(name, description, input_schema, handler)

    return decorate
