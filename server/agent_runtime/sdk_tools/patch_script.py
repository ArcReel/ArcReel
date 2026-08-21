"""SDK MCP adapters for the shared transactional episode-script editor."""

from __future__ import annotations

import copy
from typing import Any

from claude_agent_sdk import tool

from lib.script_batch_edit import (
    ScriptBatchEditCommand,
    ScriptBatchEditor,
    ScriptBatchEditResult,
    script_revision,
)
from lib.script_editor import (
    ScriptEditError,
    insert_segment,
    resolve_items,
    split_segment,
)
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename

_OPERATIONS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "items": {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "op": {"const": "update"},
                    "id": {"type": "string"},
                    "fields": {"type": "object", "minProperties": 1},
                },
                "required": ["op", "id", "fields"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "op": {"const": "insert_after"},
                    "after_id": {"type": ["string", "null"]},
                    "item": {"type": "object"},
                },
                "required": ["op", "after_id", "item"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "op": {"const": "move_after"},
                    "id": {"type": "string"},
                    "after_id": {"type": ["string", "null"]},
                },
                "required": ["op", "id", "after_id"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"op": {"const": "remove"}, "id": {"type": "string"}},
                "required": ["op", "id"],
                "additionalProperties": False,
            },
        ]
    },
}


def _item_ids(script: dict[str, Any]) -> list[str]:
    items, id_field, _kind = resolve_items(script)
    return [str(it.get(id_field)) for it in items if isinstance(it, dict)]


def _result_payload(result: ScriptBatchEditResult) -> dict[str, Any]:
    return result.model_dump(mode="json")


def _tool_edit_result(name: str, result: ScriptBatchEditResult) -> dict[str, Any]:
    payload = _result_payload(result)
    if result.success:
        ids = ", ".join(result.affected_ids) or "无"
        text = f"✅ {name} 已原子提交；revision={result.revision}；affected_ids={ids}"
        return {"content": [{"type": "text", "text": text}], "script_edit": payload}
    problem = result.problems[0]
    locations = ", ".join(
        ".".join(str(part) for part in location.path) for location in problem.locations if location.path
    )
    text = (
        f"{name} 失败: code={problem.code}, operation_index={problem.operation_index}, "
        f"unit_id={problem.unit_id}, location={locations or None}, next_action={problem.next_action}"
    )
    output: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "is_error": True,
        "script_edit": payload,
    }
    speech_codes = {"mixed_speech", "needs_replan", "parse_failed", "empty_speaker"}
    if problem.code in speech_codes:
        matching = [entry for entry in result.problems if entry.unit_id == problem.unit_id]
        output["speech_admission"] = {
            "allowed": False,
            "unit_id": problem.unit_id,
            "mode": None,
            "problems": [
                {
                    "code": entry.code,
                    "unit_id": entry.unit_id,
                    "locations": [location.model_dump(mode="json") for location in entry.locations],
                    "reason": entry.reason,
                    "action": entry.next_action,
                }
                for entry in matching
            ],
        }
    return output


def _execute_current(
    ctx: ToolContext,
    script_filename: str,
    operations: list[dict[str, Any]],
    *,
    expected_revision: str | None = None,
) -> ScriptBatchEditResult:
    if expected_revision is None:
        current = ctx.pm.load_script(ctx.project_name, script_filename)
        expected_revision = script_revision(current)
    command = ScriptBatchEditCommand.model_validate(
        {
            "script": script_filename,
            "expected_revision": expected_revision,
            "operations": operations,
        }
    )
    return ScriptBatchEditor(ctx.pm).execute(ctx.project_name, command)


def get_episode_script_revision_tool(ctx: ToolContext):
    @tool(
        "get_episode_script_revision",
        "读取剧本当前 canonical JSON revision。提交 patch_episode_script 前先调用，并把返回值原样作为 "
        "expected_revision；并发编辑后旧 revision 会被原子拒绝。",
        {
            "type": "object",
            "properties": {"script": {"type": "string", "description": "剧本文件名（纯文件名）"}},
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            revision = script_revision(ctx.pm.load_script(ctx.project_name, script_filename))
            return {
                "content": [{"type": "text", "text": f"{script_filename} revision={revision}"}],
                "script": script_filename,
                "revision": revision,
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("get_episode_script_revision", exc)

    return _handler


def patch_episode_script_tool(ctx: ToolContext):
    @tool(
        "patch_episode_script",
        "按 canonical revision 原子执行有序剧本 operations。支持 update / insert_after / move_after / remove；"
        "先在内存形成完整 candidate，再统一校验 schema、项目引用、Artifact Manifest 与 SpeechComposition。"
        "任一问题整批零写入，并返回稳定 code、operation_index、unit/field location 与 next_action。"
        "不会删除或清空已有付费媒体；修改 prompt 后仍需显式重新生成。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（纯文件名，如 episode_1.json）；单集单文件，多集编辑每集一次调用",
                },
                "expected_revision": {
                    "type": "string",
                    "pattern": "^sha256-v1:[0-9a-f]{64}$",
                    "description": "get_episode_script_revision 返回的当前 revision",
                },
                "operations": _OPERATIONS_SCHEMA,
            },
            "required": ["script", "expected_revision", "operations"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            if "operations" in args:
                command_args = {
                    "script": script_filename,
                    "expected_revision": args.get("expected_revision"),
                    "operations": args["operations"],
                }
            else:
                # Replayed tool calls may use the internal ``edits`` shape; the published MCP schema
                # exposes only the revisioned operations form.
                edits = args.get("edits")
                if not isinstance(edits, dict) or not edits:
                    raise ScriptEditError("edits 必须是非空映射")
                operations = [
                    {"op": "update", "id": str(item_id), "fields": fields} for item_id, fields in edits.items()
                ]
                command_args = {
                    "script": script_filename,
                    "expected_revision": script_revision(ctx.pm.load_script(ctx.project_name, script_filename)),
                    "operations": operations,
                }
            command = ScriptBatchEditCommand.model_validate(command_args)
            result = ScriptBatchEditor(ctx.pm).execute(ctx.project_name, command)
            output = _tool_edit_result("patch_episode_script", result)
            if result.success:
                regen_ids = [
                    operation.id
                    for operation in command.operations
                    if operation.op == "update"
                    and any(field.split(".", 1)[0] in {"image_prompt", "video_prompt"} for field in operation.fields)
                ]
                if regen_ids:
                    output["content"][0]["text"] += (
                        f"\n⚠️  改了 prompt 的分镜（{', '.join(dict.fromkeys(regen_ids))}）须紧接着重新生成对应图/视频。"
                    )
            return output
        except Exception as exc:  # noqa: BLE001
            return tool_error("patch_episode_script", exc)

    return _handler


def insert_segment_tool(ctx: ToolContext):
    @tool(
        "insert_segment",
        "在指定条目 id 之后插入一个新条目（分镜或视频单元，机器结构为 segment/scene/unit）。"
        "新条目由你提供完整内容，其 id 由系统分配（派生自锚点 id 的稳定后缀，不重排其余条目），"
        "资产为空待生成。参考生视频插入的是 video_unit（正文写在 text 字段）。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名）"},
                "after_id": {"type": "string", "description": "在此条目 id 之后插入"},
                "item": {
                    "type": "object",
                    "description": "新条目的完整内容对象（除 id/generated_assets 外的所有必填字段；id 由系统分配）",
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
            current = ctx.pm.load_script(ctx.project_name, script_filename)
            expected_revision = script_revision(current)
            preview = copy.deepcopy(current)
            insert_segment(preview, after_id, item)
            items, id_field, _kind = resolve_items(preview)
            anchor_index = next(i for i, entry in enumerate(items) if str(entry.get(id_field)) == after_id)
            inserted = items[anchor_index + 1]
            result = _execute_current(
                ctx,
                script_filename,
                [{"op": "insert_after", "after_id": after_id, "item": inserted}],
                expected_revision=expected_revision,
            )
            output = _tool_edit_result("insert_segment", result)
            if result.success:
                new_ids = _item_ids(ctx.pm.load_script(ctx.project_name, script_filename))
                output["content"][0]["text"] += f"\n当前条目顺序: {new_ids}"
            return output
        except Exception as exc:  # noqa: BLE001
            return tool_error("insert_segment", exc)

    return _handler


def remove_segment_tool(ctx: ToolContext):
    @tool(
        "remove_segment",
        "按 id 删除一个条目（分镜或视频单元，机器结构为 segment/scene/unit）。其余条目的 id "
        "不变、不重排，被删条目的已生成资产随之失效。参考生视频删除的是 video_unit。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名）"},
                "id": {"type": "string", "description": "要删除的条目 id"},
            },
            "required": ["script", "id"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            item_id = str(args["id"])
            result = _execute_current(ctx, script_filename, [{"op": "remove", "id": item_id}])
            output = _tool_edit_result("remove_segment", result)
            if result.success:
                new_ids = _item_ids(ctx.pm.load_script(ctx.project_name, script_filename))
                output["content"][0]["text"] += f"\n当前条目顺序: {new_ids}"
            return output
        except Exception as exc:  # noqa: BLE001
            return tool_error("remove_segment", exc)

    return _handler


def split_segment_tool(ctx: ToolContext):
    @tool(
        "split_segment",
        "把一个条目（分镜或视频单元）按你提供的各部分内容拆成多个（≥2 份）。**首份保留原 id "
        "且 generated_assets 不动**（锚点延续,与 insert_segment 资产保留语义对齐）;其余分配稳定的"
        "派生 id 且 generated_assets 清空,需重新生成。只想微调原条目内容请用 patch_episode_script。"
        "参考生视频下 unit 的 "
        "duration_seconds 是独立字段（不由正文长度派生），拆分后每份都要给出符合模型档位的值。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名（纯文件名）"},
                "id": {"type": "string", "description": "要拆分的条目 id"},
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
            current = ctx.pm.load_script(ctx.project_name, script_filename)
            expected_revision = script_revision(current)
            preview = copy.deepcopy(current)
            before_items, before_id_field, _before_kind = resolve_items(preview)
            original_index = next(
                (index for index, item in enumerate(before_items) if str(item.get(before_id_field)) == item_id),
                None,
            )
            if original_index is None:
                raise ScriptEditError(f"未找到 id={item_id!r} 的分镜")
            previous_id = str(before_items[original_index - 1].get(before_id_field)) if original_index > 0 else None
            split_segment(preview, item_id, parts)
            items, id_field, _kind = resolve_items(preview)
            anchor_index = next(i for i, item in enumerate(items) if str(item.get(id_field)) == item_id)
            generated = items[anchor_index : anchor_index + len(parts)]
            operations: list[dict[str, Any]] = [{"op": "remove", "id": item_id}]
            after_id = previous_id
            for generated_item in generated:
                operations.append({"op": "insert_after", "after_id": after_id, "item": generated_item})
                after_id = str(generated_item[id_field])
            result = _execute_current(
                ctx,
                script_filename,
                operations,
                expected_revision=expected_revision,
            )
            output = _tool_edit_result("split_segment", result)
            if result.success:
                new_ids = _item_ids(ctx.pm.load_script(ctx.project_name, script_filename))
                output["content"][0]["text"] += f"\n当前分镜顺序: {new_ids}"
            return output
        except Exception as exc:  # noqa: BLE001
            return tool_error("split_segment", exc)

    return _handler


__all__ = [
    "get_episode_script_revision_tool",
    "patch_episode_script_tool",
    "insert_segment_tool",
    "remove_segment_tool",
    "split_segment_tool",
]
