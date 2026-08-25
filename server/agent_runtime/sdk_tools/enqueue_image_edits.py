"""Thin Claude SDK adapter for image edits."""

from server.agent_runtime.sdk_tools._media_adapter import sdk_media_tool
from server.media_tools.context import ToolContext
from server.media_tools.image_edits import edit_images_tool as _definition


def edit_images_tool(ctx: ToolContext):
    return sdk_media_tool(_definition(ctx))
