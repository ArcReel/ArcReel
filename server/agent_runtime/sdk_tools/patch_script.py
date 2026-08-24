"""SDK MCP adapters for the shared transactional episode-script editor."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from lib.script_batch_edit import ScriptBatchEditResult, script_revision
from lib.script_editor import ScriptEditError
from server.agent_runtime.sdk_tools._context import (
    ToolContext,
    migration_failure_for,
    migration_refusal_response,
    tool_error,
    tool_services,
    validate_script_filename,
)
from server.tool_runtime import (
    PatchEpisodeScriptRequest,
    ToolRequest,
    patch_episode_script,
)

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
                    "op": {"const": "insert"},
                    "after_id": {"type": "string"},
                    "item": {"type": "object"},
                },
                "required": ["op", "after_id", "item"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "op": {"const": "remove"},
                    "id": {"type": "string"},
                },
                "required": ["op", "id"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "op": {"const": "split"},
                    "id": {"type": "string"},
                    "parts": {"type": "array", "minItems": 2, "items": {"type": "object"}},
                },
                "required": ["op", "id", "parts"],
                "additionalProperties": False,
            },
        ]
    },
}


def _result_payload(result: ScriptBatchEditResult) -> dict[str, Any]:
    return result.model_dump(mode="json")


def _tool_edit_result(name: str, result: ScriptBatchEditResult) -> dict[str, Any]:
    payload = _result_payload(result)
    if result.success:
        ids = ", ".join(result.affected_ids) or "无"
        text = f"✅ {name} 已原子提交；revision={result.revision}；affected_ids={ids}"
        return {
            "content": [
                {"type": "text", "text": text},
                {"type": "text", "text": json.dumps({"script_edit": payload}, ensure_ascii=False)},
            ],
            "script_edit": payload,
        }
    problem = result.problems[0]
    locations = ", ".join(
        ".".join(str(part) for part in location.path) for location in problem.locations if location.path
    )
    text = (
        f"{name} 失败: code={problem.code}, operation_index={problem.operation_index}, "
        f"unit_id={problem.unit_id}, location={locations or None}, next_action={problem.next_action}"
    )
    output: dict[str, Any] = {
        "content": [
            {"type": "text", "text": text},
            {"type": "text", "text": json.dumps({"script_edit": payload}, ensure_ascii=False)},
        ],
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


def get_episode_script_revision_tool(ctx: ToolContext):
    @tool(
        "get_episode_script_revision",
        "读取剧本当前 canonical JSON revision。提交 patch_episode_script 前先调用，并把返回值原样作为 "
        "base_revision；并发编辑后旧 revision 会被原子拒绝。",
        {
            "type": "object",
            "properties": {"script": {"type": "string", "description": "剧本文件名（纯文件名）"}},
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            # revision 的唯一用途是当 patch_episode_script 的并发令牌，而剧本写入工具在阻断期
            # 全部关闭：签发出去无处可用，故直接答复裁决，省掉一轮必然被拒的写入往返。
            failure = await migration_failure_for(ctx)
            if failure is not None:
                return migration_refusal_response(
                    failure,
                    text="❌ 项目数据升级未完成，剧本编辑已全部关闭，revision 不再签发。"
                    "请按明细修复后调用 retry_project_migration：",
                )
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
        "按 canonical revision 原子执行有序剧本 operations。支持 update / insert / remove / split；"
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
                "base_revision": {
                    "type": "string",
                    "pattern": "^sha256-v1:[0-9a-f]{64}$",
                    "description": "get_episode_script_revision 返回的当前 revision",
                },
                "operations": _OPERATIONS_SCHEMA,
            },
            "required": ["script", "base_revision", "operations"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            if "operations" in args:
                request_args = {
                    "script": script_filename,
                    "base_revision": args.get("base_revision"),
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
                request_args = {
                    "script": script_filename,
                    "base_revision": script_revision(ctx.pm.load_script(ctx.project_name, script_filename)),
                    "operations": operations,
                }
            request = PatchEpisodeScriptRequest.model_validate(request_args)
            outcome = await patch_episode_script(ToolRequest(request), ctx.scope, ctx.caller, tool_services(ctx))
            if outcome.problem is not None:
                raise ScriptEditError(outcome.problem.detail)
            if outcome.value is None:
                raise ScriptEditError("patch_episode_script 未返回结果")
            result = outcome.value
            output = _tool_edit_result("patch_episode_script", result)
            if result.success:
                regen_ids = [
                    operation.id
                    for operation in request.operations
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


__all__ = [
    "get_episode_script_revision_tool",
    "patch_episode_script_tool",
]
