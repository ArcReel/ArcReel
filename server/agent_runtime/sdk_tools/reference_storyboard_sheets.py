"""Agent tools for the mandatory reference-video Video Unit Storyboard Sheet gate."""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import tool

from lib.generation_queue_client import batch_enqueue_only
from lib.generation_result import normalize_requested_ids
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename
from server.services.image_model_selection import IMAGE_MODEL_TOOL_PROPERTIES, image_override_from_args
from server.services.reference_keyframe_tasks import reference_keyframe_task_specs
from server.services.reference_storyboard_sheet_tasks import (
    confirm_storyboard_sheet_and_enqueue_keyframes,
    reference_storyboard_sheet_task_specs,
)


def _response(payload: object, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]
    }
    if is_error:
        result["is_error"] = True
    return result


def generate_reference_storyboard_sheets_tool(ctx: ToolContext):
    @tool(
        "generate_reference_storyboard_sheets",
        "为 reference_video 剧本生成必须审阅的 Video Unit Storyboard Sheet（每个单元一张多格叙事预览）。"
        "这不是 generate_storyboards 的逐镜头分镜图。生成后只能处于待确认状态；"
        "不得直接生成关键帧。unit_ids 省略时仅补齐尚无 Sheet 的单元。",
        {
            "type": "object",
            "properties": {
                **IMAGE_MODEL_TOOL_PROPERTIES,
                "script": {"type": "string", "description": "剧本文件名，例如 episode_1.json"},
                "unit_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "指定生成或重生的 Video Unit ID；省略时只补齐缺失项",
                },
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_file = validate_script_filename(args["script"])
            requested = normalize_requested_ids(args.get("unit_ids"), field="unit_ids")
            script = ctx.pm.load_script(ctx.project_name, script_file)
            specs = reference_storyboard_sheet_task_specs(
                script,
                script_file,
                unit_ids=set(requested) if requested is not None else None,
                missing_only=requested is None,
                image_override=image_override_from_args(args),
            )
            enqueued, failures = await batch_enqueue_only(project_name=ctx.project_name, specs=specs)
            return _response(
                {
                    "requested": requested,
                    "tasks": [
                        {"unit_id": item.resource_id, "task_id": item.task_id, "deduped": item.deduped}
                        for item in enqueued
                    ],
                    "failures": [item.model_dump(mode="json") for item in failures],
                    "next_step": "用户必须确认每个当前 Video Unit Storyboard Sheet，确认操作才会批量入队该单元关键帧。",
                },
                is_error=bool(failures),
            )
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_reference_storyboard_sheets", exc)

    return _handler


def confirm_reference_storyboard_sheet_tool(ctx: ToolContext):
    @tool(
        "confirm_reference_storyboard_sheet",
        "确认一个 Video Unit 当前的 Video Unit Storyboard Sheet；这是关键帧生成的强制卡点。"
        "只有用户已明确确认该 Sheet 时才可调用，成功后自动批量入队该单元的全部关键帧。",
        {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "剧本文件名，例如 episode_1.json"},
                "unit_id": {"type": "string", "description": "用户已确认当前 Video Unit Storyboard Sheet 的 Video Unit ID"},
            },
            "required": ["script", "unit_id"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_file = validate_script_filename(args["script"])
            unit_id = str(args["unit_id"]).strip()
            if not unit_id:
                raise ValueError("unit_id 不能为空")
            sheet, task_ids = await confirm_storyboard_sheet_and_enqueue_keyframes(
                ctx.project_name,
                script_file,
                unit_id,
            )
            return _response(
                {
                    "unit_id": unit_id,
                    "storyboard_sheet": sheet,
                    "keyframe_task_ids": task_ids,
                }
            )
        except Exception as exc:  # noqa: BLE001
            return tool_error("confirm_reference_storyboard_sheet", exc)

    return _handler


def generate_reference_keyframes_tool(ctx: ToolContext):
    @tool(
        "generate_reference_keyframes",
        "仅为已确认 Video Unit Storyboard Sheet 的 reference_video 单元生成或重试关键首帧。"
        "这不是 Storyboard 工具；keyframe_ids 省略时只补齐尚无图片的关键帧。",
        {
            "type": "object",
            "properties": {
                **IMAGE_MODEL_TOOL_PROPERTIES,
                "script": {"type": "string", "description": "剧本文件名，例如 episode_1.json"},
                "keyframe_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "指定生成或重生的关键帧 ID；省略时只补齐缺失项",
                },
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_file = validate_script_filename(args["script"])
            requested = normalize_requested_ids(args.get("keyframe_ids"), field="keyframe_ids")
            script = ctx.pm.load_script(ctx.project_name, script_file)
            specs = reference_keyframe_task_specs(
                script,
                script_file,
                keyframe_ids=set(requested) if requested is not None else None,
                missing_only=requested is None,
                image_override=image_override_from_args(args),
            )
            enqueued, failures = await batch_enqueue_only(project_name=ctx.project_name, specs=specs)
            return _response(
                {
                    "requested": requested,
                    "tasks": [
                        {"keyframe_id": item.resource_id, "task_id": item.task_id, "deduped": item.deduped}
                        for item in enqueued
                    ],
                    "failures": [item.model_dump(mode="json") for item in failures],
                },
                is_error=bool(failures),
            )
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_reference_keyframes", exc)

    return _handler


__all__ = [
    "confirm_reference_storyboard_sheet_tool",
    "generate_reference_keyframes_tool",
    "generate_reference_storyboard_sheets_tool",
]
