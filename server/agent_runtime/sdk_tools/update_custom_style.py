"""SDK MCP tool for editing one reusable custom-style card."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from claude_agent_sdk import tool

from lib.db import async_session_factory
from lib.path_safety import safe_join
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.custom_styles import CustomStyleImage, update_custom_style


def update_custom_style_tool(ctx: ToolContext):
    @tool(
        "update_custom_style",
        "编辑全局自定义风格卡片的名称、提示词和参考图。已有项目保存的是独立快照，编辑风格库不会反向修改项目；"
        "use_current_project_image=true 时用当前项目的风格参考图替换卡片图片。先调用 list_global_assets 获取 style_id。",
        {
            "type": "object",
            "properties": {
                "style_id": {"type": "string", "description": "自定义风格卡片 ID"},
                "name": {"type": "string", "description": "完整的新名称"},
                "description": {"type": "string", "description": "完整的新风格提示词，可为空但此时必须保留参考图"},
                "remove_image": {"type": "boolean", "description": "移除卡片参考图，默认 false"},
                "use_current_project_image": {
                    "type": "boolean",
                    "description": "用当前项目的 style_image 替换卡片参考图，默认 false",
                },
            },
            "required": ["style_id", "name", "description"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            remove_image = args.get("remove_image") is True
            use_project_image = args.get("use_current_project_image") is True
            if remove_image and use_project_image:
                raise ValueError("remove_image 与 use_current_project_image 不能同时为 true")

            replacement: CustomStyleImage | None = None
            if use_project_image:
                project = await asyncio.to_thread(ctx.pm.load_project, ctx.project_name)
                relative_path = project.get("style_image")
                if not isinstance(relative_path, str) or not relative_path:
                    raise ValueError("当前项目没有可用的风格参考图")
                source = safe_join(ctx.project_path, relative_path, require_file=True)
                replacement = CustomStyleImage(
                    content=await asyncio.to_thread(source.read_bytes),
                    extension=source.suffix.lower(),
                )

            style = await update_custom_style(
                str(args["style_id"]),
                name=str(args["name"]),
                description=str(args["description"]),
                replacement_image=replacement,
                remove_image=remove_image,
                session_factory=async_session_factory,
                projects_root=ctx.projects_root,
            )
            payload = style.serialize()
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "自定义风格已更新；已有项目快照未改变。\n"
                        + json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    }
                ],
                "style": payload,
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("update_custom_style", exc)

    return _handler


__all__ = ["update_custom_style_tool"]
