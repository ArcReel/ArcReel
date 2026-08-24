"""SDK MCP tool for fresh, targeted reference-keyframe generation."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.generation_queue_client import batch_enqueue_and_wait
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error
from server.services.image_model_selection import IMAGE_MODEL_TOOL_PROPERTIES, image_override_from_args
from server.services.reference_keyframe_tasks import reference_keyframe_task_specs

_OPERATION = "generate_reference_keyframes"


def generate_reference_keyframes_tool(ctx: ToolContext):
    @tool(
        _OPERATION,
        "按正式剧本中的关键分镜描述，从空白全新生成指定关键首帧。"
        "该工具会使用描述里的 @[资产名] 作为参考，但绝不把现有关键帧图片作为编辑底图；"
        "需要定点重生成一张或多张关键帧时调用它，不要为此重新调用 generate_episode_script，"
        "也不要调用 edit_images。Web UI 的“重新生成关键首帧”与此工具使用同一任务服务。",
        {
            "type": "object",
            "properties": {
                **IMAGE_MODEL_TOOL_PROPERTIES,
                "episode": {"type": "integer", "minimum": 1},
                "keyframe_ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "description": "要从空白全新生成的关键分镜 ID，例如 E1U01K01。",
                },
            },
            "required": ["episode", "keyframe_ids"],
            "additionalProperties": False,
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            episode = int(args["episode"])
            raw_ids = args.get("keyframe_ids") or []
            keyframe_ids = list(dict.fromkeys(str(value).strip() for value in raw_ids if str(value).strip()))
            if not keyframe_ids:
                raise ValueError("keyframe_ids 至少包含一个非空 ID")

            script_file = f"episode_{episode}.json"
            script = ctx.pm.load_script(ctx.project_name, script_file)
            specs = reference_keyframe_task_specs(
                script,
                script_file,
                keyframe_ids=set(keyframe_ids),
                image_override=image_override_from_args(args),
            )
            found = {spec.resource_id for spec in specs}
            missing = [value for value in keyframe_ids if value not in found]
            if missing:
                raise ValueError(f"关键分镜不存在或缺少描述：{'、'.join(missing)}")

            successes, failures = await batch_enqueue_and_wait(
                project_name=ctx.project_name,
                specs=specs,
            )
            succeeded_ids = [result.resource_id for result in successes]
            failed_items = [
                {"id": result.resource_id, "error": result.error or "unknown"} for result in failures
            ]
            text = f"✅ 已从空白全新生成 {len(succeeded_ids)} 张关键首帧：{'、'.join(succeeded_ids)}。"
            if failed_items:
                text += f" 另有 {len(failed_items)} 张失败。"
            return {
                "content": [{"type": "text", "text": text}],
                "is_error": bool(failed_items),
                "succeeded": succeeded_ids,
                "failed": failed_items,
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error(_OPERATION, exc)

    return _handler


__all__ = ["generate_reference_keyframes_tool"]
