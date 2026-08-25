"""Thin Claude SDK adapter for unified video generation."""

from server.agent_runtime.sdk_tools._media_adapter import sdk_media_tool
from server.media_tools.context import ToolContext
from server.media_tools.videos import generate_videos_tool as _definition


def generate_videos_tool(ctx: ToolContext):
    return sdk_media_tool(_definition(ctx))
