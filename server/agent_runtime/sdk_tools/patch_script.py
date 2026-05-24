"""SDK MCP tools for editing an episode script by id.

把 agent 对 ``scripts/*.json`` 的一切编辑收归这组工具：通用字段编辑（``patch_episode_script``）
+ 结构性增删拆（``insert_segment`` / ``remove_segment`` / ``split_segment``）。每个工具在
``ProjectManager.locked_script`` 读-改-写上下文里调 ``lib.script_editor`` 的纯函数核心改
dict，退出时经写盘统一入口 ``_write_script_unlocked`` 写回——继承「不更坏」结构校验、metadata
重算、加锁与 filename↔episode 一致性。结构错误当场以「不更坏」语义挡下并返回明确错误。

工具返回文本是 agent-facing（免 i18n）；显示名在 ``ARCREEL_MCP_TOOL_IDS`` 注册、补三语。
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.script_editor import (
    insert_segment,
    patch_field,
    remove_segment,
    resolve_items,
    split_segment,
)
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename


def _item_ids(script: dict[str, Any]) -> list[str]:
    items, id_field, _kind = resolve_items(script)
    return [str(it.get(id_field)) for it in items if isinstance(it, dict)]


def patch_episode_script_tool(ctx: ToolContext):
    @tool(
        "patch_episode_script",
        "按分镜 id（segment_id/scene_id/unit_id）编辑剧本的一个字段，支持嵌套路径"
        "（如 image_prompt.scene、duration_seconds、video_prompt.action）。三种内容/生成模式通用。"
        "纯字段 setter，不触碰已生成资产——改了 prompt 须另行重新生成对应分镜图/视频。"
        "叶子字段不存在会被创建（允许补 LLM 漏写的 optional 字段如 video_prompt.dialogue）;"
        "拼写错误（如 image_prompt.scen 应为 image_prompt.scene）会经写盘统一入口的 Pydantic "
        "extra='forbid' 结构校验拒,提交前请确认字段名拼写正确。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名，如 episode_1.json）"},
                "id": {"type": "string", "description": "分镜 id（如 E1S03 / E1U02）"},
                "field": {
                    "type": "string",
                    "description": "字段名或点分嵌套路径（如 duration_seconds、image_prompt.scene）；"
                    "不可改 generated_assets;叶子不存在会创建,但需是合法 schema 字段否则写盘被拒",
                },
                "value": {"description": "新值（类型随字段而定）"},
            },
            "required": ["script", "id", "field", "value"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            item_id = str(args["id"])
            field = str(args["field"])
            value = args["value"]
            with ctx.pm.locked_script(ctx.project_name, script_filename) as script:
                patch_field(script, item_id, field, value)
            return {"content": [{"type": "text", "text": f"✅ 已更新 {item_id} 的 {field}"}]}
        except Exception as exc:  # noqa: BLE001
            return tool_error("patch_episode_script", exc)

    return _handler


def insert_segment_tool(ctx: ToolContext):
    @tool(
        "insert_segment",
        "在指定分镜 id 之后插入一个新分镜（segment/scene/unit）。新分镜由你提供完整内容，"
        "其 id 由系统分配（派生自锚点 id 的稳定后缀，不重排其余分镜），资产为空待生成。"
        "reference 模式插入的是 video_unit（含 shots）。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名）"},
                "after_id": {"type": "string", "description": "在此分镜 id 之后插入"},
                "item": {
                    "type": "object",
                    "description": "新分镜的完整内容对象（除 id/generated_assets 外的所有必填字段；id 由系统分配）",
                },
            },
            "required": ["script", "after_id", "item"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            after_id = str(args["after_id"])
            item = args["item"]
            with ctx.pm.locked_script(ctx.project_name, script_filename) as script:
                insert_segment(script, after_id, item)
                new_ids = _item_ids(script)
            return {
                "content": [{"type": "text", "text": f"✅ 已在 {after_id} 之后插入新分镜\n当前分镜顺序: {new_ids}"}]
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("insert_segment", exc)

    return _handler


def remove_segment_tool(ctx: ToolContext):
    @tool(
        "remove_segment",
        "按 id 删除一个分镜（segment/scene/unit）。其余分镜的 id 不变、不重排，被删分镜的"
        "已生成资产随之失效。reference 模式删除的是 video_unit。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名）"},
                "id": {"type": "string", "description": "要删除的分镜 id"},
            },
            "required": ["script", "id"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            item_id = str(args["id"])
            with ctx.pm.locked_script(ctx.project_name, script_filename) as script:
                remove_segment(script, item_id)
                new_ids = _item_ids(script)
            return {"content": [{"type": "text", "text": f"✅ 已删除分镜 {item_id}\n当前分镜顺序: {new_ids}"}]}
        except Exception as exc:  # noqa: BLE001
            return tool_error("remove_segment", exc)

    return _handler


def split_segment_tool(ctx: ToolContext):
    @tool(
        "split_segment",
        "把一个分镜按你提供的各部分内容拆成多个（≥2 份）。**首份保留原 id 且 generated_assets 不动**"
        "（锚点延续,与 insert_segment 资产保留语义对齐）;其余分配稳定的派生 id 且 generated_assets "
        "清空,需重新生成。只想微调原分镜内容请用 patch_episode_script——split 适合"
        "「这一镜信息量太大,拆成 N 镜分别表达」这类身份变化的场景。reference 模式下各 unit 的 "
        "duration_seconds 须等于其 shots 总时长。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名）"},
                "id": {"type": "string", "description": "要拆分的分镜 id"},
                "parts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "拆分后各部分的完整内容对象（≥2 个;id 由系统分配。首份保留原 id 的 "
                    "generated_assets,其余清空）",
                },
            },
            "required": ["script", "id", "parts"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            item_id = str(args["id"])
            parts = args["parts"]
            with ctx.pm.locked_script(ctx.project_name, script_filename) as script:
                split_segment(script, item_id, parts)
                new_ids = _item_ids(script)
            return {
                "content": [
                    {"type": "text", "text": f"✅ 已把分镜 {item_id} 拆为 {len(parts)} 份\n当前分镜顺序: {new_ids}"}
                ]
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("split_segment", exc)

    return _handler


__all__ = [
    "patch_episode_script_tool",
    "insert_segment_tool",
    "remove_segment_tool",
    "split_segment_tool",
]
