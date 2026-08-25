"""Claude SDK adapter for host-neutral media tool definitions."""

from claude_agent_sdk import tool

from server.media_tools.definition import ToolDefinition


def sdk_media_tool(definition: ToolDefinition):
    async def handler(args):
        outcome = await definition.invoke(args)
        assert outcome.value is not None
        return outcome.value.to_response()

    return tool(definition.name, definition.description, definition.input_schema)(handler)
