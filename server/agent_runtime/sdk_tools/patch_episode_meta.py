"""SDK MCP tool for editing an episode script's **top-level** metadata fields.

``patch_episode_script`` 只能按分镜 id 改分镜数组里的字段（经 ``resolve_items``），剧本顶层
字段（如 ``title``）对它不可触达。本工具补齐这条通路：在 ``ProjectManager.locked_script`` 读-改-
写上下文里直接写剧本顶层白名单字段，退出时经写盘统一入口 ``_write_script_unlocked``
（``sync_project=True`` 默认）自动把集元数据镜像到 project.json 的 ``episodes[].title``。

剧本顶层刻意无 ``extra='forbid'``（要容纳运行时注入的 ``episode``/``metadata``/
``generation_mode``，见 ``lib/script_models.py``），故必须靠显式白名单兜底，防 agent 写任意键。

工具返回文本是 agent-facing（免 i18n）；显示名在 ``ARCREEL_MCP_TOOL_IDS`` 注册、补三语。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename

# 可经本工具编辑的剧本顶层字段白名单。新增字段时 append,并在 _handler 补对应值校验。
_META_WHITELIST = ("title",)


def patch_episode_meta_tool(ctx: ToolContext):
    @tool(
        "patch_episode_meta",
        "编辑剧本的顶层元数据字段（非分镜级）。本期仅支持 field=title 改分集标题——"
        "分集标题以剧本顶层 title 为唯一真相源，改后自动镜像到 project.json 供 WebUI 分集列表显示。"
        f"白名单字段 {list(_META_WHITELIST)};改某个分镜内部字段请用 patch_episode_script。"
        "title 须为非空字符串（首尾空白会被裁剪）。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名，如 episode_1.json）"},
                "field": {
                    "type": "string",
                    "enum": list(_META_WHITELIST),
                    "description": f"要编辑的顶层字段名，必须在白名单内 {list(_META_WHITELIST)}",
                },
                "value": {"type": "string", "description": "新值（title 为非空字符串）"},
            },
            "required": ["script", "field", "value"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            field = str(args["field"])
            if field not in _META_WHITELIST:
                raise ValueError(f"field {field!r} 不在白名单 {list(_META_WHITELIST)} 内")
            value = args["value"]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} 必须是非空字符串")
            new_value = value.strip()
            with ctx.pm.locked_script(ctx.project_name, script_filename) as script:
                script[field] = new_value
            return {"content": [{"type": "text", "text": f"✅ 已更新分集{field}为「{new_value}」"}]}
        except Exception as exc:  # noqa: BLE001
            return tool_error("patch_episode_meta", exc)

    return _handler


__all__ = ["patch_episode_meta_tool"]
