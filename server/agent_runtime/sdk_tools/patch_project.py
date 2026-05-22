"""SDK MCP tool for editing project.json assets by table + name.

把 agent 对 ``project.json`` 角色/场景/道具的写入收归 ``patch_project``：按 table
（characters/scenes/props）+ name **upsert**（不存在则加、存在则改字段），经
``ProjectManager.upsert_assets`` 在单一文件锁内 read-modify-write，apply 后落盘前做结构
校验，非法则不写。取代脆弱的单行 CLI-JSON 脚本 ``add_assets.py``（且把「只能加」扩为「可改」）。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_error

_TABLES = ("characters", "scenes", "props")


def patch_project_tool(ctx: ToolContext):
    @tool(
        "patch_project",
        "新增或修改 project.json 里的角色/场景/道具（按 table + name upsert）。name 不存在则新增、"
        "存在则合并改字段（如改 description / voice_style）。可一次提交多条。结构非法时不落盘并报错。",
        {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "enum": list(_TABLES),
                    "description": "资产表：characters / scenes / props",
                },
                "entries": {
                    "type": "object",
                    "description": "{ 名称: { description, voice_style 等字段 } } 映射；至少一条",
                },
            },
            "required": ["table", "entries"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            table = str(args["table"])
            entries = args["entries"]
            if not isinstance(entries, dict):
                raise ValueError("entries 必须是 { 名称: 字段对象 } 映射")
            ctx.pm.upsert_assets(ctx.project_name, table, entries)
            names = ", ".join(entries.keys())
            return {"content": [{"type": "text", "text": f"✅ 已写入 {table}: {names}"}]}
        except Exception as exc:  # noqa: BLE001
            return tool_error("patch_project", exc)

    return _handler


__all__ = ["patch_project_tool"]
