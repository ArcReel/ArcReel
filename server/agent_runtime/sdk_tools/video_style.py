"""Agent adapters for the shared project-level Unified Video Style operation."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from lib.project_change_hints import project_change_source
from lib.video_style import VIDEO_STYLE_PROMPT_MAX_LENGTH, UnifiedVideoStylePatch, video_style_summary
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.video_style import VideoStyleService

_STYLE_PROPERTIES: dict[str, Any] = {
    "prompt": {"type": "string", "minLength": 1, "maxLength": VIDEO_STYLE_PROMPT_MAX_LENGTH},
}


def _response(style: Any, *, created: bool | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"video_style": style.model_dump(mode="json")}
    if created is not None:
        payload["created"] = created
    text = f"✅ Unified Video Style: {video_style_summary(style)}\n" + json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], **payload}


def analyze_video_style_tool(ctx: ToolContext):
    @tool(
        "analyze_video_style",
        "读取项目唯一的 Unified Video Style；仅在尚未配置时根据项目概述、视觉风格与已拆分剧本分析并保存。已有配置直接返回，不重复分析。",
        {
            "type": "object",
            "properties": {"episode": {"type": "integer", "minimum": 1}},
            "additionalProperties": False,
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = args.get("episode")
            if episode is not None and (not isinstance(episode, int) or isinstance(episode, bool) or episode < 1):
                raise ValueError("episode must be a positive integer")
            with project_change_source("filesystem"):
                style, created = await VideoStyleService(ctx.pm).ensure(
                    ctx.project_name,
                    preferred_episode=episode,
                )
            return _response(style, created=created)
        except Exception as exc:  # noqa: BLE001 - MCP adapters use a controlled error envelope
            return tool_error("analyze_video_style", exc)

    return _handler


def update_video_style_tool(ctx: ToolContext):
    @tool(
        "update_video_style",
        "按用户要求编辑项目唯一的一段视频风格提示词；该提示词供全部视频单元与 H3 提示词优化共同使用。",
        {
            "type": "object",
            "properties": _STYLE_PROPERTIES,
            "minProperties": 1,
            "additionalProperties": False,
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            patch = UnifiedVideoStylePatch.model_validate(args)
            if not patch.model_fields_set:
                raise ValueError("video style prompt is required")
            with project_change_source("filesystem"):
                style = VideoStyleService(ctx.pm).update(ctx.project_name, patch, source="user")
            return _response(style)
        except Exception as exc:  # noqa: BLE001 - MCP adapters use a controlled error envelope
            return tool_error("update_video_style", exc)

    return _handler


__all__ = ["analyze_video_style_tool", "update_video_style_tool"]
