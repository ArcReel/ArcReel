"""ArcReel SDK in-process MCP tools.

Tools registered here run **in the server main process** (not inside the
agent sandbox), so they can read ``projects/.arcreel.db`` and call provider
HTTP without poking holes in ``filesystem.denyRead`` / network allowlist.

Each session gets its own MCP server built via :func:`build_arcreel_mcp_server`
— ``project_name`` is closure-bound, so the agent cannot redirect tools to a
different project via prompt injection.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server

from lib.project_migration_guard import project_migration_failure
from server.agent_runtime.sdk_tools._context import ToolContext, migration_refusal_response
from server.agent_runtime.sdk_tools.asset_inventory import complete_asset_inventory_tool
from server.agent_runtime.sdk_tools.delete_project_asset import delete_project_asset_tool
from server.agent_runtime.sdk_tools.enqueue_assets import (
    generate_assets_tool,
    list_pending_assets_tool,
)
from server.agent_runtime.sdk_tools.enqueue_grid import generate_grid_tool
from server.agent_runtime.sdk_tools.enqueue_image_edits import edit_images_tool
from server.agent_runtime.sdk_tools.enqueue_narration_audio import generate_narration_audio_tool
from server.agent_runtime.sdk_tools.enqueue_storyboards import generate_storyboards_tool
from server.agent_runtime.sdk_tools.enqueue_videos import (
    generate_video_all_tool,
    generate_video_episode_tool,
    generate_video_scene_tool,
    generate_video_selected_tool,
)
from server.agent_runtime.sdk_tools.episode_planning import (
    plan_episodes_tool,
    reset_episode_planning_tool,
)
from server.agent_runtime.sdk_tools.global_assets import list_global_assets_tool
from server.agent_runtime.sdk_tools.h3_prompt_optimization import (
    confirm_h3_video_prompts_tool,
    optimize_h3_video_prompts_tool,
)
from server.agent_runtime.sdk_tools.patch_episode_meta import patch_episode_meta_tool
from server.agent_runtime.sdk_tools.patch_project import patch_project_tool
from server.agent_runtime.sdk_tools.patch_script import (
    get_episode_script_revision_tool,
    insert_segment_tool,
    patch_episode_script_tool,
    remove_segment_tool,
    split_segment_tool,
)
from server.agent_runtime.sdk_tools.project_asset_links import manage_project_asset_link_tool
from server.agent_runtime.sdk_tools.rename_asset import rename_asset_tool
from server.agent_runtime.sdk_tools.retry_project_migration import retry_project_migration_tool
from server.agent_runtime.sdk_tools.text_generation import (
    confirm_script_review_tool,
    generate_episode_script_tool,
    get_video_capabilities_tool,
    normalize_drama_script_tool,
    open_step1_for_edit_tool,
    split_narration_segments_tool,
    split_reference_video_units_tool,
    validate_and_promote_draft_tool,
)
from server.agent_runtime.sdk_tools.workflow_plan import get_workflow_plan_tool
from server.agent_runtime.sdk_tools.workflow_status import complete_step1_rebuild_tool

__all__ = ["build_arcreel_mcp_server", "ToolContext", "ARCREEL_MCP_TOOL_IDS"]

# Single source of truth for the ArcReel in-process MCP tool catalogue.
# Each id is the **short tool name** (without the ``mcp__arcreel__`` prefix the
# SDK adds at registration). Frontend display names live in
# ``frontend/src/i18n/{zh,en,vi}/dashboard.ts`` under the ``tool_name_<id>``
# keys; ``tests/test_frontend_mcp_tool_i18n.py`` cross-checks that every id
# here has a translation in all locales, so adding a tool without wiring up
# i18n fails CI.
ARCREEL_MCP_TOOL_IDS: tuple[str, ...] = (
    "list_global_assets",
    "complete_asset_inventory",
    "complete_step1_rebuild",
    "get_workflow_plan",
    "list_pending_assets",
    "generate_assets",
    "generate_storyboards",
    "edit_images",
    "generate_grid",
    "generate_video_episode",
    "generate_video_scene",
    "generate_video_all",
    "generate_video_selected",
    "generate_narration_audio",
    "optimize_h3_video_prompts",
    "confirm_h3_video_prompts",
    "generate_episode_script",
    "confirm_script_review",
    "normalize_drama_script",
    "split_reference_video_units",
    "open_step1_for_edit",
    "validate_and_promote_draft",
    "split_narration_segments",
    "get_video_capabilities",
    "plan_episodes",
    "reset_episode_planning",
    "get_episode_script_revision",
    "patch_episode_script",
    "patch_episode_meta",
    "insert_segment",
    "remove_segment",
    "split_segment",
    "patch_project",
    "rename_asset",
    "delete_project_asset",
    "manage_project_asset_link",
    "retry_project_migration",
)

# Tools refused outright while the project's schema migration verdict is a failure.
# Everything that generates output or writes formal content is closed; the read-only
# tools stay open so the agent can still diagnose what needs repairing, and the
# controlled script/project editors stay open because repairing is done through them.
MIGRATION_BLOCKED_TOOL_IDS: frozenset[str] = frozenset(
    {
        "complete_asset_inventory",
        "complete_step1_rebuild",
        "manage_project_asset_link",
        "generate_assets",
        "generate_storyboards",
        "edit_images",
        "generate_grid",
        "generate_video_episode",
        "generate_video_scene",
        "generate_video_all",
        "generate_video_selected",
        "generate_narration_audio",
        "optimize_h3_video_prompts",
        "confirm_h3_video_prompts",
        "generate_episode_script",
        "confirm_script_review",
        "normalize_drama_script",
        "split_reference_video_units",
        "open_step1_for_edit",
        "validate_and_promote_draft",
        "split_narration_segments",
        "plan_episodes",
        "reset_episode_planning",
    }
)


def _refuse_while_migration_failed(sdk_tool: Any, ctx: ToolContext) -> Any:
    """Wrap one tool so it reports the migration verdict instead of running.

    Applied at registration rather than inside each handler: the blocked set is
    one list to keep honest, and no generation tool can forget the check.
    """

    inner = sdk_tool.handler

    async def _guarded(args: Any) -> dict[str, Any]:
        failure = await asyncio.to_thread(project_migration_failure, ctx.project_name, ctx.pm)
        if failure is not None:
            return migration_refusal_response(
                failure,
                text="❌ 项目数据升级未完成，生成与正式写入已全部关闭。请按明细修复后调用 retry_project_migration：",
            )
        return await inner(args)

    return replace(sdk_tool, handler=_guarded)


def build_arcreel_mcp_server(*, project_name: str, projects_root: Path) -> Any:
    """Build the per-session in-process MCP server with all ArcReel tools."""
    ctx = ToolContext(project_name=project_name, projects_root=projects_root)
    tools = [
        list_global_assets_tool(ctx),
        complete_asset_inventory_tool(ctx),
        complete_step1_rebuild_tool(ctx),
        get_workflow_plan_tool(ctx),
        list_pending_assets_tool(ctx),
        generate_assets_tool(ctx),
        generate_storyboards_tool(ctx),
        edit_images_tool(ctx),
        generate_grid_tool(ctx),
        generate_video_episode_tool(ctx),
        generate_video_scene_tool(ctx),
        generate_video_all_tool(ctx),
        generate_video_selected_tool(ctx),
        generate_narration_audio_tool(ctx),
        optimize_h3_video_prompts_tool(ctx),
        confirm_h3_video_prompts_tool(ctx),
        generate_episode_script_tool(ctx),
        confirm_script_review_tool(ctx),
        normalize_drama_script_tool(ctx),
        split_reference_video_units_tool(ctx),
        open_step1_for_edit_tool(ctx),
        validate_and_promote_draft_tool(ctx),
        split_narration_segments_tool(ctx),
        get_video_capabilities_tool(ctx),
        plan_episodes_tool(ctx),
        reset_episode_planning_tool(ctx),
        get_episode_script_revision_tool(ctx),
        patch_episode_script_tool(ctx),
        patch_episode_meta_tool(ctx),
        insert_segment_tool(ctx),
        remove_segment_tool(ctx),
        split_segment_tool(ctx),
        patch_project_tool(ctx),
        rename_asset_tool(ctx),
        delete_project_asset_tool(ctx),
        manage_project_asset_link_tool(ctx),
        retry_project_migration_tool(ctx),
    ]
    return create_sdk_mcp_server(
        name="arcreel",
        version="1.0.0",
        tools=[
            _refuse_while_migration_failed(sdk_tool, ctx) if sdk_tool.name in MIGRATION_BLOCKED_TOOL_IDS else sdk_tool
            for sdk_tool in tools
        ],
    )
