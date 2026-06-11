"""SDK MCP tool for editing project.json assets by table + name 或顶层 settings 字段。

把 agent 对 ``project.json`` 角色/场景/道具的写入收归 ``patch_project``：按 table
（characters/scenes/props）+ name **upsert**（不存在则加、存在则改字段），经
``ProjectManager.upsert_assets`` 在单一文件锁内 read-modify-write，apply 后落盘前做结构
校验，非法则不写。取代脆弱的单行 CLI-JSON 脚本 ``add_assets.py``（且把「只能加」扩为「可改」）。

同一工具同时承担顶层 ``settings`` 字段写入（白名单驱动），首期支持 ``episode_target_units``，
以及项目概述 ``overview``（synopsis/genre/theme/world_setting，merge 语义）的编辑。
``table + entries`` / ``settings`` / ``overview`` 三选一,在 ``update_project`` 锁内 RMW 同源。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from server.agent_runtime.sdk_tools._context import ToolContext, tool_error

_TABLES = ("characters", "scenes", "props")

# 顶层 settings 白名单。新增项 append 到 tuple,并在 _validate_setting_value 加分支。
# source_language: overview 生成是非必经路径(generate_overview=false / overview 失败时
# 源语言不会落盘),需要给 agent 在用户确认后写入的恢复通道,带 zh/en/vi enum 校验防乱填。
# planning_window_chars / planning_max_episodes: 分集规划工具的窗口字数与每批集数覆盖项,
# null 时回退工具内部默认。
_SETTINGS_WHITELIST = (
    "episode_target_units",
    "source_language",
    "brief",
    "planning_window_chars",
    "planning_max_episodes",
)
_SOURCE_LANGUAGE_VALUES = ("zh", "en", "vi")
_POSITIVE_INT_SETTINGS = ("episode_target_units", "planning_window_chars", "planning_max_episodes")

# 项目概述（project["overview"]）可经本工具编辑的字段白名单。merge 语义:只改传入字段。
_OVERVIEW_FIELDS = ("synopsis", "genre", "theme", "world_setting")


def patch_project_tool(ctx: ToolContext):
    @tool(
        "patch_project",
        "新增或修改 project.json:(1) 资产 upsert(传 table+entries),按 table+name upsert "
        "(name 不存在则新增、存在则合并改字段);(2) 顶层 settings 写入(传 settings),"
        f"白名单字段 {list(_SETTINGS_WHITELIST)},值为 null 时清除;(3) 项目概述编辑(传 overview),"
        f"白名单字段 {list(_OVERVIEW_FIELDS)},merge 语义只改传入字段、概述不存在时创建。"
        "三种形态三选一,同时给出多个或都不给会被拒。结构非法时不落盘并报错。",
        {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "enum": list(_TABLES),
                    "description": "(资产 upsert 分支)资产表:characters / scenes / props",
                },
                "entries": {
                    "type": "object",
                    "description": "(资产 upsert 分支){ 名称: { description, voice_style 等字段 } } 映射;至少一条",
                },
                "settings": {
                    "type": "object",
                    "description": (
                        "(settings 写入分支)顶层字段映射,key 必须在白名单内 "
                        f"{list(_SETTINGS_WHITELIST)},值为 null 时清除该字段"
                    ),
                },
                "overview": {
                    "type": "object",
                    "description": (
                        "(项目概述分支)概述字段映射,key 必须在白名单内 "
                        f"{list(_OVERVIEW_FIELDS)};merge 语义(只更新传入字段),概述不存在时创建"
                    ),
                },
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            has_upsert = "table" in args or "entries" in args
            has_settings = "settings" in args
            has_overview = "overview" in args
            if sum((has_upsert, has_settings, has_overview)) > 1:
                raise ValueError("table/entries、settings、overview 三选一,不能同时给出多个")
            if not (has_upsert or has_settings or has_overview):
                raise ValueError("必须提供 table+entries(资产 upsert)、settings(顶层字段)或 overview(项目概述)之一")

            if has_overview:
                overview = args["overview"]
                if not isinstance(overview, dict) or not overview:
                    raise ValueError("overview 必须是非空 { 字段名: 值 } 映射")
                updated_overview = _apply_overview(ctx, overview)
                return {"content": [{"type": "text", "text": _format_overview_result(updated_overview)}]}

            if has_settings:
                settings = args["settings"]
                if not isinstance(settings, dict) or not settings:
                    raise ValueError("settings 必须是非空 { 字段名: 值 } 映射")
                updated = _apply_settings(ctx, settings)
                return {"content": [{"type": "text", "text": _format_settings_result(updated)}]}

            if "table" not in args or "entries" not in args:
                raise ValueError("资产 upsert 分支必须同时提供 table 和 entries")
            table = str(args["table"])
            entries = args["entries"]
            if not isinstance(entries, dict) or not entries:
                raise ValueError("entries 必须是非空 { 名称: 字段对象 } 映射")
            result = ctx.pm.upsert_assets(ctx.project_name, table, entries)
            return {"content": [{"type": "text", "text": _format_upsert_result(table, result)}]}
        except Exception as exc:  # noqa: BLE001
            return tool_error("patch_project", exc)

    return _handler


def _apply_settings(ctx: ToolContext, settings: dict[str, Any]) -> dict[str, Any]:
    """在 update_project 锁内 RMW 顶层 settings 字段。

    返回 { field: ('set', new_value) | ('clear', None) | ('noop', current_value) } 诊断 dict,
    供 _format_settings_result 渲染。整体在校验失败时不落盘(ValueError 冒到 handler 走 tool_error)。
    """
    for key, value in settings.items():
        if key not in _SETTINGS_WHITELIST:
            raise ValueError(f"settings 字段 {key!r} 不在白名单 {list(_SETTINGS_WHITELIST)} 内")
        _validate_setting_value(key, value)

    diagnostics: dict[str, tuple[str, Any]] = {}

    def _mutate(project: dict[str, Any]) -> None:
        # brief 仅广告/短片项目可用（与 DataValidator / 路由层同一约束），
        # 在持锁读到 content_mode 后门控，整体失败不落盘
        if "brief" in settings and project.get("content_mode") != "ad":
            raise ValueError("brief 仅广告/短片项目（content_mode=ad）可用")
        for key, value in settings.items():
            current = project.get(key)
            if value is None:
                if key in project:
                    del project[key]
                    diagnostics[key] = ("clear", None)
                else:
                    diagnostics[key] = ("noop", None)
            elif current == value:
                diagnostics[key] = ("noop", current)
            else:
                project[key] = value
                diagnostics[key] = ("set", value)

    ctx.pm.update_project(ctx.project_name, _mutate)
    return diagnostics


def _apply_overview(ctx: ToolContext, overview: dict[str, Any]) -> dict[str, str]:
    """在 update_project 锁内 merge 项目概述四字段(只改传入字段,概述不存在时创建)。

    返回 { field: 'set' | 'noop' } 诊断 dict,供 _format_overview_result 渲染。与
    PATCH /projects/{name}/overview 端点行为一致(merge、不清除未传字段)。
    """
    for key, value in overview.items():
        if key not in _OVERVIEW_FIELDS:
            raise ValueError(f"overview 字段 {key!r} 不在白名单 {list(_OVERVIEW_FIELDS)} 内")
        if not isinstance(value, str):
            raise ValueError(f"overview 字段 {key!r} 的值必须是字符串,收到 {value!r}")

    diagnostics: dict[str, str] = {}

    def _mutate(project: dict[str, Any]) -> None:
        existing = project.get("overview")
        if not isinstance(existing, dict):
            existing = {}
            project["overview"] = existing
        for key, value in overview.items():
            if existing.get(key) == value:
                diagnostics[key] = "noop"
            else:
                existing[key] = value
                diagnostics[key] = "set"

    ctx.pm.update_project(ctx.project_name, _mutate)
    return diagnostics


def _format_overview_result(updated: dict[str, str]) -> str:
    """overview 分支结果文本,风格对齐 _format_settings_result。"""
    set_items = [k for k, op in updated.items() if op == "set"]
    noop_items = [k for k, op in updated.items() if op == "noop"]

    parts: list[str] = []
    if set_items:
        parts.append("已更新 " + ", ".join(set_items))
    if noop_items:
        parts.append("无变更 " + ", ".join(noop_items))

    icon = "ℹ️" if not set_items else "✅"
    summary = "; ".join(parts) if parts else "无变更"
    return f"{icon} overview: {summary}"


def _validate_setting_value(key: str, value: Any) -> None:
    """settings 字段值类型校验。新增白名单字段时在此 dispatch。"""
    if key in _POSITIVE_INT_SETTINGS:
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} 必须是正整数或 null,收到 {value!r}")
        return
    if key == "source_language":
        if value is None:
            return
        if not isinstance(value, str) or value not in _SOURCE_LANGUAGE_VALUES:
            raise ValueError(f"source_language 必须是 {list(_SOURCE_LANGUAGE_VALUES)} 之一或 null,收到 {value!r}")
        return
    if key == "brief":
        if value is None:
            return
        if not isinstance(value, str):
            raise ValueError(f"brief 必须是字符串或 null,收到 {value!r}")
        return
    # 不应到这,白名单校验在调用前
    raise ValueError(f"settings 字段 {key!r} 缺类型校验")


def _format_settings_result(updated: dict[str, tuple[str, Any]]) -> str:
    """settings 分支结果文本,风格对齐 _format_upsert_result。"""
    set_items = [(k, v) for k, (op, v) in updated.items() if op == "set"]
    clear_items = [k for k, (op, _) in updated.items() if op == "clear"]
    noop_items = [k for k, (op, _) in updated.items() if op == "noop"]

    parts: list[str] = []
    if set_items:
        parts.append("已更新 " + ", ".join(f"{k}={v}" for k, v in set_items))
    if clear_items:
        parts.append("已清除 " + ", ".join(clear_items))
    if noop_items:
        parts.append("无变更 " + ", ".join(noop_items))

    icon = "ℹ️" if (not set_items and not clear_items) else "✅"
    summary = "; ".join(parts) if parts else "无变更"
    return f"{icon} settings: {summary}"


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
