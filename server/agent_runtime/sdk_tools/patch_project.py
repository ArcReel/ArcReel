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

    区分新增/合并/无变更让 subagent 能验证策略是否符合预期(分析提取场景应预期合并/无变更=0,
    出现说明遗漏了已存在过滤);显式列出被忽略字段让 LLM 不再重复尝试同样会被丢的字段
    (reference_image 系统管理、sheet_field 资产流水线回写、type/importance 已废弃)。
    name 维度按字母序排序,渲染顺序稳定不依赖 agent 入参 dict 序。
    """
    added: list[str] = sorted(result.get("added") or [])
    merged: list[str] = sorted(result.get("merged") or [])
    noop: list[str] = sorted(result.get("noop") or [])
    dropped_fields: dict[str, list[str]] = result.get("dropped_fields") or {}
    dropped_legacy: dict[str, list[str]] = result.get("dropped_legacy") or {}

    summary_parts: list[str] = []
    if added:
        summary_parts.append(f"新增 {len(added)} 个: {', '.join(added)}")
    if merged:
        summary_parts.append(f"合并改字段 {len(merged)} 个: {', '.join(merged)}")
    if noop:
        # 全字段被白名单/legacy strip 丢空 → no-op:project.json 字节未变,工具不报『合并』
        # 误导 agent。dropped_fields / dropped_legacy 段会详述被丢的字段,agent 据此修参。
        summary_parts.append(f"无可写字段已跳过 {len(noop)} 个: {', '.join(noop)}")
    summary = "; ".join(summary_parts) if summary_parts else "无变更（所有条目均无可写字段）"
    icon = "ℹ️" if (not added and not merged) else "✅"
    lines = [f"{icon} {table}: {summary}"]

    if dropped_fields:
        detail = "; ".join(f"{name}: {', '.join(fields)}" for name, fields in sorted(dropped_fields.items()))
        lines.append(f"⚠️  以下字段不在 agent 可编辑范围,已忽略 → {detail}")
        lines.append("   说明: reference_image 由用户上传/系统管理;")
        lines.append("   character_sheet / scene_sheet / prop_sheet 由资产生成流水线回写,不可手动设置。")
    if dropped_legacy:
        detail = "; ".join(f"{name}: {', '.join(fields)}" for name, fields in sorted(dropped_legacy.items()))
        lines.append(f"ℹ️  以下历史字段已废弃,本次未持久化 → {detail}")
    return "\n".join(lines)


__all__ = ["patch_project_tool"]
