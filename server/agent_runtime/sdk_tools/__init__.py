"""ArcReel SDK in-process MCP tools.

Tools registered here run **in the server main process** (not inside the
agent sandbox), so they can read ``projects/.arcreel.db`` and call provider
HTTP without poking holes in ``filesystem.denyRead`` / network allowlist.

Each session gets its own MCP server built via :func:`build_arcreel_mcp_server`
— ``project_name`` is closure-bound, so the agent cannot redirect tools to a
different project via prompt injection.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server

from server.agent_runtime.sdk_tools._context import ToolContext, pending_schema_upgrade_error
from server.agent_runtime.sdk_tools.asset_inventory import complete_asset_inventory_tool
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
from server.agent_runtime.sdk_tools.patch_episode_meta import patch_episode_meta_tool
from server.agent_runtime.sdk_tools.patch_project import patch_project_tool
from server.agent_runtime.sdk_tools.patch_script import (
    get_episode_script_revision_tool,
    insert_segment_tool,
    patch_episode_script_tool,
    remove_segment_tool,
    split_segment_tool,
)
from server.agent_runtime.sdk_tools.rename_asset import rename_asset_tool
from server.agent_runtime.sdk_tools.text_generation import (
    confirm_script_review_tool,
    generate_episode_script_tool,
    get_video_capabilities_tool,
    normalize_drama_script_tool,
    open_reference_step1_for_edit_tool,
    split_narration_segments_tool,
    split_reference_video_units_tool,
    validate_and_promote_reference_draft_tool,
)
from server.agent_runtime.sdk_tools.workflow_status import complete_step1_rebuild_tool, get_workflow_status_tool

__all__ = [
    "build_arcreel_mcp_server",
    "build_arcreel_tools",
    "ToolContext",
    "ARCREEL_MCP_TOOL_IDS",
    "SCHEMA_EXEMPT_TOOL_IDS",
]

# Single source of truth for the ArcReel in-process MCP tool catalogue.
# Each id is the **short tool name** (without the ``mcp__arcreel__`` prefix the
# SDK adds at registration). Frontend display names live in
# ``frontend/src/i18n/{zh,en,vi}/dashboard.ts`` under the ``tool_name_<id>``
# keys; ``tests/test_frontend_mcp_tool_i18n.py`` cross-checks that every id
# here has a translation in all locales, so adding a tool without wiring up
# i18n fails CI.
ARCREEL_MCP_TOOL_IDS: tuple[str, ...] = (
    "complete_asset_inventory",
    "complete_step1_rebuild",
    "get_workflow_status",
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
    "generate_episode_script",
    "confirm_script_review",
    "normalize_drama_script",
    "split_reference_video_units",
    "open_reference_step1_for_edit",
    "validate_and_promote_reference_draft",
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
)


# 未完成数据升级的项目上仍然可用的工具：只查不写、不发起付费调用。用户在这种项目上
# 需要智能体解释现状、说明为何不能生成，读路径掐掉等于连求助渠道一起掐掉。
# 其余工具一律受 ``_guard_pending_schema_upgrade`` 拦截——按旧契约取到的创作类型是兜底值，
# 写入会把新契约的键混进旧结构，付费调用则按错误的创作类型照发照计费。
# 新增工具默认落在受闸一侧；确属只读时才登记到这里。
SCHEMA_EXEMPT_TOOL_IDS: frozenset[str] = frozenset(
    {
        "get_workflow_status",
        "list_pending_assets",
        "get_video_capabilities",
        "get_episode_script_revision",
    }
)


def _guard_pending_schema_upgrade(ctx: ToolContext, tool_def: Any) -> Any:
    """给写入 / 付费类工具套上数据契约版本闸门，与 REST 侧共用同一判定。"""
    if tool_def.name in SCHEMA_EXEMPT_TOOL_IDS:
        return tool_def
    inner = tool_def.handler

    async def _guarded(args: Any) -> dict[str, Any]:
        try:
            schema_error = pending_schema_upgrade_error(ctx)
        except Exception:  # noqa: BLE001
            # 项目文件读不出来时不在这里改写诊断：交给工具自身的错误处理报出成因。
            schema_error = None
        if schema_error is not None:
            return schema_error
        return await inner(args)

    return replace(tool_def, handler=_guarded)


def build_arcreel_tools(ctx: ToolContext) -> list[Any]:
    """Build the full tool catalogue for one session, schema gate already applied."""
    return [
        _guard_pending_schema_upgrade(ctx, tool_def)
        for tool_def in (
            complete_asset_inventory_tool(ctx),
            complete_step1_rebuild_tool(ctx),
            get_workflow_status_tool(ctx),
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
            generate_episode_script_tool(ctx),
            confirm_script_review_tool(ctx),
            normalize_drama_script_tool(ctx),
            split_reference_video_units_tool(ctx),
            open_reference_step1_for_edit_tool(ctx),
            validate_and_promote_reference_draft_tool(ctx),
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
        )
    ]


def build_arcreel_mcp_server(*, project_name: str, projects_root: Path) -> Any:
    """Build the per-session in-process MCP server with all ArcReel tools."""
    ctx = ToolContext(project_name=project_name, projects_root=projects_root)
    return create_sdk_mcp_server(name="arcreel", version="1.0.0", tools=build_arcreel_tools(ctx))
