"""Thin Claude SDK adapter for storyboard generation."""

from server.agent_runtime.sdk_tools._media_adapter import sdk_media_tool
from server.media_tools.context import ToolContext
from server.media_tools.storyboards import generate_storyboards_tool as _definition


def generate_storyboards_tool(ctx: ToolContext):
    return sdk_media_tool(_definition(ctx))
