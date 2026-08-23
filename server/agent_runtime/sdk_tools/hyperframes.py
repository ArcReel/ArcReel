"""SDK MCP adapter for project-local HyperFrames workspaces."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from lib.narration_delivery import POST_PRODUCTION, USE_TTS
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.hyperframes_workspace import HyperframesWorkspaceService


def prepare_hyperframes_episode_tool(ctx: ToolContext):
    @tool(
        "prepare_hyperframes_episode",
        "把当前项目一集已生成的视频物化为项目内 HyperFrames 工程；返回允许编辑的唯一工作区路径。",
        {
            "type": "object",
            "properties": {
                "episode": {"type": "integer", "minimum": 1},
                "narration_delivery": {
                    "type": "string",
                    "enum": [POST_PRODUCTION, USE_TTS],
                    "default": POST_PRODUCTION,
                },
            },
            "required": ["episode"],
            "additionalProperties": False,
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        episode = args.get("episode")
        variant = args.get("narration_delivery", POST_PRODUCTION)
        if type(episode) is not int or episode <= 0:
            return tool_error("prepare_hyperframes_episode", ValueError("episode 必须是正整数"))
        if variant not in {POST_PRODUCTION, USE_TTS}:
            return tool_error(
                "prepare_hyperframes_episode",
                ValueError("narration_delivery 必须是 post_production 或 use_tts"),
            )
        try:
            workspace = await HyperframesWorkspaceService(ctx.pm).prepare(
                ctx.project_name,
                episode,
                variant=variant,
            )
        except Exception as exc:  # noqa: BLE001
            return tool_error("prepare_hyperframes_episode", exc)

        payload = {
            **workspace.to_dict(),
            "editable_root": str(workspace.path),
            "entry_file": str(workspace.path / "index.html"),
            "write_boundary": str(workspace.path),
        }
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "workspace": payload,
        }

    return _handler


__all__ = ["prepare_hyperframes_episode_tool"]
