"""ArcReel SDK in-process MCP tools.

Tools registered here run **in the server main process** (not inside the
agent sandbox), so they can read the ArcReel database and call provider
HTTP without poking holes in ``filesystem.denyRead`` / network allowlist.

Each session gets its own MCP server built via :func:`build_arcreel_mcp_server`.
Project-scoped tools are closure-bound to ``project_name``; project entry tools
may list, create, or upload within the same ``data_root``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server

from lib.db.base import DEFAULT_USER_ID
from server.agent_runtime.sdk_tools.asset_inventory import complete_asset_inventory_tool
from server.agent_runtime.sdk_tools.enqueue_assets import (
    generate_assets_tool,
    list_pending_assets_tool,
)
from server.agent_runtime.sdk_tools.enqueue_grid import generate_grid_tool, split_grids_tool
from server.agent_runtime.sdk_tools.enqueue_image_edits import edit_images_tool
from server.agent_runtime.sdk_tools.enqueue_narration_audio import generate_narration_audio_tool
from server.agent_runtime.sdk_tools.enqueue_storyboards import generate_storyboards_tool
from server.agent_runtime.sdk_tools.enqueue_videos import generate_videos_tool
from server.agent_runtime.sdk_tools.episode_planning import (
    plan_episodes_tool,
    reset_episode_planning_tool,
)
from server.agent_runtime.sdk_tools.patch_script import (
    patch_episode_script_tool,
)
from server.agent_runtime.sdk_tools.text_generation import (
    confirm_script_review_tool,
    discard_draft_tool,
    generate_episode_script_tool,
    generate_script_plan_tool,
    open_draft_tool,
    patch_draft_tool,
    promote_draft_tool,
)
from server.agent_runtime.sdk_tools.workflow_status import complete_script_plan_rebuild_tool
from server.agent_toolset.embedded import embedded_server
from server.agent_toolset.toolset import AGENT_TOOLSET, DECLARED_MIGRATION_BLOCKED_TOOL_IDS, DECLARED_TOOL_IDS
from server.media_tools.context import (
    ToolContext,
    migration_failure_for,
    migration_refusal_response,
    tool_services,
)
from server.tool_runtime import CallerContext

__all__ = ["ARCREEL_MCP_TOOL_IDS", "ToolContext", "build_arcreel_mcp_server"]

# The ArcReel MCP tool catalogue: tools still registered by a per-host factory below, followed by
# the tools derived from Agent toolset declarations. Each id is the **short tool name** (without the
# ``mcp__arcreel__`` prefix the SDK adds at registration). Frontend display names live in
# ``frontend/src/i18n/{zh,en,vi}/dashboard.ts`` under the ``tool_name_<id>`` keys;
# ``tests/unit/test_frontend_mcp_tool_i18n.py`` cross-checks that every id here has a translation in
# all locales, so adding a tool without wiring up i18n fails CI.
_FACTORY_TOOL_IDS: tuple[str, ...] = (
    "complete_asset_inventory",
    "complete_script_plan_rebuild",
    "list_pending_assets",
    "generate_assets",
    "generate_storyboards",
    "edit_images",
    "generate_grid",
    "split_grids",
    "generate_videos",
    "generate_narration_audio",
    "generate_episode_script",
    "generate_script_plan",
    "confirm_script_review",
    "open_draft",
    "patch_draft",
    "promote_draft",
    "discard_draft",
    "plan_episodes",
    "reset_episode_planning",
    "patch_episode_script",
)
ARCREEL_MCP_TOOL_IDS: tuple[str, ...] = (*_FACTORY_TOOL_IDS, *DECLARED_TOOL_IDS)

# Factory-registered tools wrapped at registration so they report the verdict instead of running while the
# project's schema migration verdict is a failure. Everything that generates output or
# writes script content is named here. The controlled project/metadata editors
# (``patch_project``, ``patch_episode_meta``, ``rename_asset``) are declared tools exempt from the block,
# with the reason written on their declarations, because repairing is done through them.
# The script batch editors are named here even though their shared
# ``ScriptBatchEditor.execute`` already refuses internally on the same verdict:
# the entry declares the block, the inner check is only a fallback, and an entry
# never skips declaring the block just because some callee happens to check too.
#
# Declared tools carry their own migration policy and are gated by the shared declaration entry;
# ``MIGRATION_BLOCKED_TOOL_IDS`` is the union of both.
#
# ``list_pending_assets`` is outside this set on purpose — it answers the verdict inside its own
# handler via ``migration_failure_for`` and returns the same typed migration problem that the wrapper
# encodes, so this frozenset stays exactly the registration-time blocks.
_FACTORY_MIGRATION_BLOCKED_TOOL_IDS: frozenset[str] = frozenset(
    {
        "complete_asset_inventory",
        "complete_script_plan_rebuild",
        "generate_assets",
        "generate_storyboards",
        "edit_images",
        "generate_grid",
        "split_grids",
        "generate_videos",
        "generate_narration_audio",
        "generate_episode_script",
        "generate_script_plan",
        "confirm_script_review",
        "open_draft",
        "patch_draft",
        "promote_draft",
        "discard_draft",
        "plan_episodes",
        "reset_episode_planning",
        "patch_episode_script",
    }
)
MIGRATION_BLOCKED_TOOL_IDS: frozenset[str] = _FACTORY_MIGRATION_BLOCKED_TOOL_IDS | DECLARED_MIGRATION_BLOCKED_TOOL_IDS


def _refuse_while_migration_failed(sdk_tool: Any, ctx: ToolContext) -> Any:
    """Wrap one tool so it reports the migration verdict instead of running.

    Applied at registration rather than inside each handler: the blocked set is
    one list to keep honest, and no generation tool can forget the check.
    """

    inner = sdk_tool.handler

    async def _guarded(args: Any) -> dict[str, Any]:
        failure = await migration_failure_for(ctx)
        if failure is not None:
            return migration_refusal_response(
                failure,
                text="❌ 项目数据升级未完成，生成与正式写入已全部关闭。请按明细修复后调用 retry_project_migration：",
            )
        return await inner(args)

    return replace(sdk_tool, handler=_guarded)


def build_arcreel_mcp_server(*, project_name: str, data_root: Path, user_id: str = DEFAULT_USER_ID) -> Any:
    """Build the per-session in-process MCP server with all ArcReel tools."""
    ctx = ToolContext(
        project_name=project_name,
        data_root=data_root,
        caller=CallerContext(user_id=user_id, source="embedded"),
    )
    tools = [
        complete_asset_inventory_tool(ctx),
        complete_script_plan_rebuild_tool(ctx),
        list_pending_assets_tool(ctx),
        generate_assets_tool(ctx),
        generate_storyboards_tool(ctx),
        edit_images_tool(ctx),
        generate_grid_tool(ctx),
        split_grids_tool(ctx),
        generate_videos_tool(ctx),
        generate_narration_audio_tool(ctx),
        generate_episode_script_tool(ctx),
        generate_script_plan_tool(ctx),
        confirm_script_review_tool(ctx),
        open_draft_tool(ctx),
        patch_draft_tool(ctx),
        promote_draft_tool(ctx),
        discard_draft_tool(ctx),
        plan_episodes_tool(ctx),
        reset_episode_planning_tool(ctx),
        patch_episode_script_tool(ctx),
    ]
    undeclared = create_sdk_mcp_server(
        name="arcreel",
        version="1.0.0",
        tools=[
            _refuse_while_migration_failed(sdk_tool, ctx)
            if sdk_tool.name in _FACTORY_MIGRATION_BLOCKED_TOOL_IDS
            else sdk_tool
            for sdk_tool in tools
        ],
    )
    return embedded_server(
        AGENT_TOOLSET,
        name="arcreel",
        version="1.0.0",
        scope=ctx.scope,
        caller=ctx.caller,
        services=tool_services(ctx),
        undeclared=undeclared["instance"],
    )
