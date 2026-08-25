"""Thin Claude SDK adapter for grid generation."""

from server.agent_runtime.sdk_tools._media_adapter import sdk_media_tool
from server.media_tools.context import ToolContext
from server.media_tools.grid import GridBatchWaiter, batch_enqueue_and_wait
from server.media_tools.grid import generate_grid_tool as _definition


def generate_grid_tool(ctx: ToolContext, *, batch_waiter: GridBatchWaiter = batch_enqueue_and_wait):
    return sdk_media_tool(_definition(ctx, batch_waiter=batch_waiter))
