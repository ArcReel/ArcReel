"""Claude SDK adapter for host-neutral media tool definitions."""

import json
from typing import Any

from claude_agent_sdk import tool

from server.media_tools.definition import ToolDefinition, media_outcome_payload
from server.tool_runtime import ToolOutcome


def _response(definition: ToolDefinition, outcome: ToolOutcome[Any]) -> dict[str, Any]:
    payload, summary, is_error = media_outcome_payload(definition, outcome)
    return {
        "content": [{"type": "text", "text": summary or json.dumps(payload, ensure_ascii=False)}],
        "is_error": is_error,
        **payload,
    }


def sdk_media_tool(definition: ToolDefinition):
    async def handler(args):
        outcome = await definition.invoke(args)
        return _response(definition, outcome)

    return tool(definition.name, definition.description, definition.input_schema)(handler)
