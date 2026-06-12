"""OpenAI-compatible tool definitions for ArcReel MCP tools.

This module provides the bridge between ArcReel's in-process tools and
LiteLLM (or any OpenAI-compatible API). Each tool is registered with its
name, description, JSON Schema parameters, and an async handler function.

Usage::

    from server.agent_runtime.tool_definitions import get_openai_tools, execute_tool

    # Get tools in OpenAI function calling format
    tools = get_openai_tools()

    # Execute a tool by name
    result = await execute_tool("generate_storyboards", {"script": "ep1.json"}, ctx)
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from server.agent_runtime.sdk_tools._context import ToolContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool registry entry
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolDef:
    """Single tool definition: schema + handler factory."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler_factory: Callable[[ToolContext], Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]]
    # Subset of ToolContext fields the handler needs (for documentation only)
    requires: tuple[str, ...] = ("project_name", "projects_root")


# ---------------------------------------------------------------------------
# Tool registry — single source of truth
# ---------------------------------------------------------------------------

# Lazy imports to avoid circular deps at module level
def _import_storyboard_tools():
    from server.agent_runtime.sdk_tools.enqueue_storyboards import generate_storyboards_tool

    return generate_storyboards_tool


def _import_video_tools():
    from server.agent_runtime.sdk_tools.enqueue_videos import (
        generate_video_all_tool,
        generate_video_episode_tool,
        generate_video_scene_tool,
        generate_video_selected_tool,
    )

    return generate_video_episode_tool, generate_video_scene_tool, generate_video_all_tool, generate_video_selected_tool


def _import_grid_tool():
    from server.agent_runtime.sdk_tools.enqueue_grid import generate_grid_tool

    return generate_grid_tool


def _import_asset_tools():
    from server.agent_runtime.sdk_tools.enqueue_assets import generate_assets_tool, list_pending_assets_tool

    return list_pending_assets_tool, generate_assets_tool


def _import_text_tools():
    from server.agent_runtime.sdk_tools.text_generation import (
        generate_episode_script_tool,
        get_video_capabilities_tool,
        normalize_drama_script_tool,
    )

    return generate_episode_script_tool, normalize_drama_script_tool, get_video_capabilities_tool


TOOL_REGISTRY: dict[str, ToolDef] = {}


def _build_registry() -> dict[str, ToolDef]:
    """Build the tool registry. Called once on first access."""
    registry: dict[str, ToolDef] = {}

    # --- Storyboard ---
    gen_story = _import_storyboard_tools()
    registry["generate_storyboards"] = ToolDef(
        name="generate_storyboards",
        description="为 narration/drama 模式剧本生成分镜图。script 为剧本文件名（如 episode_1.json）；segment_ids 指定要重生的片段/场景 ID 列表（不传则生成所有缺图项）。",
        parameters={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "segment_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "片段或场景 ID 列表；不传则扫描所有缺分镜图的项",
                },
            },
            "required": ["script"],
        },
        handler_factory=gen_story,
    )

    # --- Video ---
    gen_vid_ep, gen_vid_scene, gen_vid_all, gen_vid_sel = _import_video_tools()

    registry["generate_video_episode"] = ToolDef(
        name="generate_video_episode",
        description="为剧本对应的整集生成所有场景视频。resume=true 时从 checkpoint 续传。reference_video 模式会自动按 video_units 处理。",
        parameters={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "resume": {"type": "boolean", "description": "是否从上次中断处继续"},
            },
            "required": ["script"],
        },
        handler_factory=gen_vid_ep,
    )

    registry["generate_video_scene"] = ToolDef(
        name="generate_video_scene",
        description="生成单个场景/片段的视频。reference_video 模式会忽略 scene_id 转为整集生成。",
        parameters={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "scene_id": {"type": "string", "description": "场景或片段 ID"},
            },
            "required": ["script", "scene_id"],
        },
        handler_factory=gen_vid_scene,
    )

    registry["generate_video_all"] = ToolDef(
        name="generate_video_all",
        description="为剧本批量生成所有缺视频的场景/片段（独立模式，不拼接）。reference_video 模式等同 episode 模式。",
        parameters={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                }
            },
            "required": ["script"],
        },
        handler_factory=gen_vid_all,
    )

    registry["generate_video_selected"] = ToolDef(
        name="generate_video_selected",
        description="生成指定多个场景的视频（独立 checkpoint，按 scene_ids 哈希）。reference_video 模式会忽略 scene_ids 转整集生成。",
        parameters={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "scene_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "场景或片段 ID 列表",
                },
                "resume": {"type": "boolean", "description": "是否从上次中断处继续"},
            },
            "required": ["script", "scene_ids"],
        },
        handler_factory=gen_vid_sel,
    )

    # --- Grid ---
    gen_grid = _import_grid_tool()
    registry["generate_grid"] = ToolDef(
        name="generate_grid",
        description="为 grid 模式项目生成宫格分镜图（按 segment_break 分组）。list_only=true 时只列出分组不执行生成。scene_ids 过滤包含这些场景的分组。",
        parameters={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "scene_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "只生成包含这些场景的分组",
                },
                "list_only": {"type": "boolean", "description": "仅列出分组信息，不入队"},
            },
            "required": ["script"],
        },
        handler_factory=gen_grid,
    )

    # --- Assets ---
    list_assets, gen_assets = _import_asset_tools()

    registry["list_pending_assets"] = ToolDef(
        name="list_pending_assets",
        description="列出项目内待生成设计图的角色/场景/道具。type 省略则汇总所有类型。",
        parameters={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["character", "scene", "prop"],
                    "description": "资产类型；不传则列出所有类型的 pending",
                },
            },
        },
        handler_factory=list_assets,
    )

    registry["generate_assets"] = ToolDef(
        name="generate_assets",
        description="批量生成角色/场景/道具设计图。type 省略则按 character→scene→prop 顺序每类独立 batch；names 指定具体名称（必须同时给 type）；all=true 表示该 type 的全部 pending。",
        parameters={
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["character", "scene", "prop"],
                    "description": "资产类型；不传等于全部三类",
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "目标资产名称列表；必须配合 type 使用",
                },
                "all": {
                    "type": "boolean",
                    "description": "是否扫描所有 pending（与 names 互斥；默认 false 但当未提供 names 时等同 true）",
                },
            },
        },
        handler_factory=gen_assets,
    )

    # --- Text generation ---
    gen_script, normalize_script, get_caps = _import_text_tools()

    registry["generate_episode_script"] = ToolDef(
        name="generate_episode_script",
        description="调用项目配置的文本模型生成 JSON 剧本。输出固定写入 {project}/scripts/episode_N.json，dry_run=true 时仅返回 prompt 不调用 API。",
        parameters={
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "dry_run": {"type": "boolean", "description": "仅显示 prompt，不调用模型"},
            },
            "required": ["episode"],
        },
        handler_factory=gen_script,
    )

    registry["normalize_drama_script"] = ToolDef(
        name="normalize_drama_script",
        description="把 source/ 小说原文（或指定 source 文件）转化为 Markdown 规范化剧本，保存到 drafts/episode_N/step1_normalized_script.md。dry_run=true 时仅返回 prompt。",
        parameters={
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "description": "剧集编号"},
                "source": {
                    "type": "string",
                    "description": "指定小说源文件路径（相对项目目录）；默认读取 source/ 下所有文本",
                },
                "dry_run": {"type": "boolean", "description": "仅显示 prompt，不调用模型"},
            },
            "required": ["episode"],
        },
        handler_factory=normalize_script,
    )

    registry["get_video_capabilities"] = ToolDef(
        name="get_video_capabilities",
        description="查当前项目的视频模型能力（model 粒度）+ 用户项目偏好。返回 JSON。",
        parameters={"type": "object", "properties": {}},
        handler_factory=get_caps,
    )

    return registry


def _get_registry() -> dict[str, ToolDef]:
    global TOOL_REGISTRY
    if not TOOL_REGISTRY:
        TOOL_REGISTRY = _build_registry()
    return TOOL_REGISTRY


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_openai_tools() -> list[dict[str, Any]]:
    """Return all tool definitions in OpenAI function calling format.

    Returns a list suitable for the ``tools`` parameter of
    ``litellm.acompletion()`` or any OpenAI-compatible API.
    """
    registry = _get_registry()
    return [
        {
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
            },
        }
        for td in registry.values()
    ]


def get_tool_names() -> list[str]:
    """Return all registered tool names."""
    return list(_get_registry().keys())


async def execute_tool(
    name: str,
    args: dict[str, Any],
    ctx: ToolContext,
) -> dict[str, Any]:
    """Execute a tool by name with the given arguments and context.

    Returns the tool result dict (with ``content`` and optional ``is_error``).
    Raises ``KeyError`` if the tool name is not registered.
    """
    registry = _get_registry()
    if name not in registry:
        raise KeyError(f"Unknown tool: {name}. Available: {list(registry.keys())}")

    td = registry[name]
    handler = td.handler_factory(ctx)
    return await handler(args)
