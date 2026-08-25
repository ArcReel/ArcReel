"""Thin Claude SDK adapter for narration audio generation."""

from server.agent_runtime.sdk_tools._media_adapter import sdk_media_tool
from server.media_tools.context import ToolContext
from server.media_tools.narration_audio import generate_narration_audio_tool as _definition


def generate_narration_audio_tool(ctx: ToolContext):
    return sdk_media_tool(_definition(ctx))
