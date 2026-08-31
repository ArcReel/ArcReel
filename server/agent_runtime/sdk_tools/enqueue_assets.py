"""Thin Claude SDK adapter for asset media tools."""

from server.agent_runtime.sdk_tools._media_adapter import sdk_media_tool
from server.media_tools.assets import generate_assets_tool as _generate_assets_definition
from server.media_tools.assets import list_pending_assets_tool as _list_pending_assets_definition
from server.media_tools.context import ToolContext


def list_pending_assets_tool(ctx: ToolContext):
    return sdk_media_tool(_list_pending_assets_definition(ctx))


def generate_assets_tool(ctx: ToolContext):
    return sdk_media_tool(_generate_assets_definition(ctx))
