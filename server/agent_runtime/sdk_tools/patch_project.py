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
            if not isinstance(entries, dict) or not entries:
                raise ValueError("entries 必须是非空 { 名称: 字段对象 } 映射")
            result = ctx.pm.upsert_assets(ctx.project_name, table, entries)
            return {"content": [{"type": "text", "text": _format_upsert_result(table, result)}]}
        except Exception as exc:  # noqa: BLE001
            return tool_error("patch_project", exc)

    return _handler


def _format_upsert_result(table: str, result: dict[str, Any]) -> str:
    """把 upsert_assets 的诊断 dict 渲染为 agent 可读文本。

    区分新增/合并让 subagent 能验证『严格 skip 已存在』策略是否实际执行,而非凭推测;
    显式列出被忽略字段让 LLM 不再重复尝试同样会被丢的字段（reference_image 系统管理、
    sheet_field 资产流水线回写、type/importance 已废弃）。
    """
    added: list[str] = result.get("added") or []
    merged: list[str] = result.get("merged") or []
    dropped_fields: dict[str, list[str]] = result.get("dropped_fields") or {}
    dropped_legacy: dict[str, list[str]] = result.get("dropped_legacy") or {}

    summary_parts: list[str] = []
    if added:
        summary_parts.append(f"新增 {len(added)} 个: {', '.join(added)}")
    if merged:
        summary_parts.append(f"合并改字段 {len(merged)} 个: {', '.join(merged)}")
    summary = "; ".join(summary_parts) if summary_parts else "无变更（所有条目均无可写字段）"
    lines = [f"✅ {table}: {summary}"]

    if dropped_fields:
        detail = "; ".join(f"{name}: {', '.join(fields)}" for name, fields in dropped_fields.items())
        lines.append(f"⚠️  以下字段不在 agent 可编辑范围,已忽略 → {detail}")
        lines.append("   说明: reference_image 由用户上传/系统管理;")
        lines.append("   character_sheet / scene_sheet / prop_sheet 由资产生成流水线回写,不可手动设置。")
    if dropped_legacy:
        detail = "; ".join(f"{name}: {', '.join(fields)}" for name, fields in dropped_legacy.items())
        lines.append(f"ℹ️  以下历史字段已废弃,本次未持久化 → {detail}")
    return "\n".join(lines)


__all__ = ["patch_project_tool"]
