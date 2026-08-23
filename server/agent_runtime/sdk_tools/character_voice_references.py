"""Agent tools for character reference-audio candidates."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from claude_agent_sdk import tool

from lib.asset_types import normalize_asset_bucket
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.character_voice_references import (
    confirm_character_voice_reference,
    enqueue_character_voice_reference,
    latest_character_voice_candidate,
)


def _response(payload: object, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]
    }
    if is_error:
        result["is_error"] = True
    return result


def generate_character_voice_references_tool(ctx: ToolContext):
    @tool(
        "generate_character_voice_references",
        "为角色生成可试听的参考音频候选。默认走私有独白视频提取音频，不展示或保留视频；"
        "已有 voice_id/reference_audio/全局声音或待确认候选的角色会跳过。结果仍需确认才写入角色资产。",
        {
            "type": "object",
            "properties": {
                "names": {"type": "array", "items": {"type": "string"}, "description": "角色名；省略表示全部角色"},
                "strategy": {"type": "string", "enum": ["video", "tts"], "default": "video"},
                "text": {"type": "string", "description": "可选独白；省略时按角色名和项目语言生成"},
                "voice": {"type": "string", "description": "仅 TTS 模式必填的音色 ID"},
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            project = await asyncio.to_thread(ctx.pm.load_project, ctx.project_name)
            characters = normalize_asset_bucket(project.get("characters"))
            raw_names = args.get("names")
            if raw_names is None:
                names = list(characters)
            elif isinstance(raw_names, list) and all(isinstance(name, str) for name in raw_names):
                names = raw_names
            else:
                return _response({"error": "names must be an array of character names"}, is_error=True)
            strategy = args.get("strategy", "video")

            async def _one(name: str) -> dict[str, Any]:
                try:
                    result = await enqueue_character_voice_reference(
                        ctx.project_name,
                        name,
                        strategy=strategy,
                        text=args.get("text"),
                        voice=args.get("voice"),
                        source="agent",
                        skip_existing_voice=True,
                        reuse_candidate=True,
                        manager=ctx.pm,
                    )
                    return {"name": name, **result}
                except Exception as exc:
                    return {"name": name, "status": "unavailable", "detail": str(exc)}

            return _response({"candidates": await asyncio.gather(*(_one(name) for name in names))})
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_character_voice_references", exc)

    return _handler


def confirm_character_voice_reference_tool(ctx: ToolContext):
    @tool(
        "confirm_character_voice_reference",
        "将一个已成功生成且用户已试听认可的角色声音候选确认成 reference_audio。"
        "省略 task_id 时使用该角色最新的待确认成功候选。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "角色名"},
                "task_id": {"type": "string", "description": "可选候选任务 ID"},
            },
            "required": ["name"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            name = args["name"]
            task_id = args.get("task_id")
            if not isinstance(task_id, str) or not task_id:
                candidate = await latest_character_voice_candidate(ctx.project_name, name, manager=ctx.pm)
                if candidate is None or candidate.get("status") != "succeeded":
                    return _response({"error": "no succeeded voice candidate", "name": name}, is_error=True)
                task_id = candidate["task_id"]
            saved = await confirm_character_voice_reference(
                ctx.project_name,
                name,
                task_id,
                source="worker",
                manager=ctx.pm,
            )
            return _response({"name": name, "task_id": task_id, **saved})
        except Exception as exc:  # noqa: BLE001
            return tool_error("confirm_character_voice_reference", exc)

    return _handler


__all__ = ["confirm_character_voice_reference_tool", "generate_character_voice_references_tool"]
