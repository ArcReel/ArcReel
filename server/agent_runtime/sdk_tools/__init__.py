"""ArcReel SDK in-process MCP tools.

Tools registered here run **in the server main process** (not inside the
agent sandbox), so they can read ``projects/.arcreel.db`` and call provider
HTTP without poking holes in ``filesystem.denyRead`` / network allowlist.

Each session gets its own MCP server built via :func:`build_arcreel_mcp_server`
— ``project_name`` is closure-bound, so the agent cannot redirect tools to a
different project via prompt injection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server

from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.enqueue_assets import (
    generate_assets_tool,
    list_pending_assets_tool,
)
from server.agent_runtime.sdk_tools.enqueue_grid import generate_grid_tool
from server.agent_runtime.sdk_tools.enqueue_narration_audio import generate_narration_audio_tool
from server.agent_runtime.sdk_tools.enqueue_storyboards import generate_storyboards_tool
from server.agent_runtime.sdk_tools.enqueue_videos import (
    generate_video_all_tool,
    generate_video_episode_tool,
    generate_video_scene_tool,
    generate_video_selected_tool,
)
from server.agent_runtime.sdk_tools.patch_episode_meta import patch_episode_meta_tool
from server.agent_runtime.sdk_tools.patch_project import patch_project_tool
from server.agent_runtime.sdk_tools.patch_script import (
    insert_segment_tool,
    patch_episode_script_tool,
    remove_segment_tool,
    split_segment_tool,
)
from server.agent_runtime.sdk_tools.text_generation import (
    generate_episode_script_tool,
    get_video_capabilities_tool,
    normalize_drama_script_tool,
)

__all__ = ["build_arcreel_mcp_server", "ToolContext", "ARCREEL_MCP_TOOL_IDS"]

# Single source of truth for the ArcReel in-process MCP tool catalogue.
# Each id is the **short tool name** (without the ``mcp__arcreel__`` prefix the
# SDK adds at registration). Frontend display names live in
# ``frontend/src/i18n/{zh,en,vi}/dashboard.ts`` under the ``tool_name_<id>``
# keys; ``tests/test_frontend_mcp_tool_i18n.py`` cross-checks that every id
# here has a translation in all locales, so adding a tool without wiring up
# i18n fails CI.
ARCREEL_MCP_TOOL_IDS: tuple[str, ...] = (
    "list_pending_assets",
    "generate_assets",
    "generate_storyboards",
    "generate_grid",
    "generate_video_episode",
    "generate_video_scene",
    "generate_video_all",
    "generate_video_selected",
    "generate_narration_audio",
    "generate_episode_script",
    "normalize_drama_script",
    "get_video_capabilities",
    "patch_episode_script",
    "patch_episode_meta",
    "insert_segment",
    "remove_segment",
    "split_segment",
    "patch_project",
)


def build_arcreel_mcp_server(*, project_name: str, projects_root: Path) -> Any:
    """Build the per-session in-process MCP server with all ArcReel tools."""
    ctx = ToolContext(project_name=project_name, projects_root=projects_root)
    return create_sdk_mcp_server(
        name="arcreel",
        version="1.0.0",
        tools=[
            list_pending_assets_tool(ctx),
            generate_assets_tool(ctx),
            generate_storyboards_tool(ctx),
            generate_grid_tool(ctx),
            generate_video_episode_tool(ctx),
            generate_video_scene_tool(ctx),
            generate_video_all_tool(ctx),
            generate_video_selected_tool(ctx),
            generate_narration_audio_tool(ctx),
            generate_episode_script_tool(ctx),
            normalize_drama_script_tool(ctx),
            get_video_capabilities_tool(ctx),
            patch_episode_script_tool(ctx),
            patch_episode_meta_tool(ctx),
            insert_segment_tool(ctx),
            remove_segment_tool(ctx),
            split_segment_tool(ctx),
            patch_project_tool(ctx),
        ],
    )
