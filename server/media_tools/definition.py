"""Small host-neutral tool metadata used by both host adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from lib.generation_batch import GenerationBatchReadModel
from lib.generation_result import GenerationBatchResult
from server.tool_runtime import ToolOutcome


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[ToolOutcome[Any]]]

    async def invoke(self, args: dict[str, Any]) -> ToolOutcome[Any]:
        return await self.handler(args)


def json_value(value: Any) -> Any:
    """Project typed domain values into JSON-safe values for host adapters."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {key: json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_value(item) for item in value]
    return value


def media_outcome_payload(
    definition: ToolDefinition, outcome: ToolOutcome[Any]
) -> tuple[dict[str, Any], str | None, bool]:
    """Project one typed media outcome; each host still owns its envelope."""
    if outcome.problem is not None:
        return {"problem": json_value(outcome.problem)}, None, True
    if isinstance(outcome.value, GenerationBatchReadModel):
        return {"generation_batch": json_value(outcome.value)}, None, False
    if isinstance(outcome.value, dict) and "generation_result" in outcome.value:
        payload = json_value(outcome.value)
        summary = payload.pop("summary", None)
        result = outcome.value["generation_result"]
        admission = outcome.value.get("batch_admission")
        is_error = (
            isinstance(result, GenerationBatchResult)
            and not result.ok
            and not (isinstance(admission, dict) and admission.get("decision") == "confirmation_required")
        )
        return payload, summary, is_error
    return {definition.name: json_value(outcome.value)}, None, False


def tool(name: str, description: str, input_schema: dict[str, Any]):
    """Declare host-neutral metadata without importing a host SDK."""

    def decorate(handler: Callable[[dict[str, Any]], Awaitable[ToolOutcome[Any]]]) -> ToolDefinition:
        return ToolDefinition(name, description, input_schema, handler)

    return decorate
