"""SDK MCP tool for instruction-based image editing (image edit, see ``docs/adr/0049``).

Editing forks the **image**, not the prompt: the current image is the sole reference,
the user's instruction is the sole prompt, ``image_prompt`` is never rewritten. This is
the tool-facing entry point for that flow — the fail-fast i2i check and resource
resolution reuse the same helpers the HTTP endpoint (``server/routers/generate.py``)
uses, so the two entry points can't diverge (see ``server/services/image_edit_tasks.py``).
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.generation_queue_client import TaskSpec, batch_enqueue_and_wait
from lib.path_safety import safe_exists
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename
from server.services.image_edit_tasks import EDITABLE_RESOURCE_TYPES, resolve_current_image_rel

# Display label for tool output only; storyboard isn't an ASSET_SPECS member so this
# can't reuse that dict directly (mirrors enqueue_assets._EMOJI's separate table).
_LABEL_ZH: dict[str, str] = {
    "character": "角色",
    "scene": "场景",
    "prop": "道具",
    "product": "产品",
    "storyboard": "分镜图",
}


async def _i2i_provider_available(project: dict[str, Any]) -> bool:
    """项目 i2i 槽解析不出可用供应商时返回 False——与 HTTP 端点入队前 fail-fast 同一判断点
    （见 ``server/routers/generate.py::_require_i2i_image_provider_configured``），批量编辑
    只需要一次「是否可用」的项目级判断，不像端点那样需要拿到 provider_id 传给入队。
    """
    try:
        await ConfigResolver(async_session_factory).resolve_image_backend(project, None, capability="i2i")
    except ValueError:
        return False
    return True


def _build_specs(
    project: dict[str, Any],
    project_path: Any,
    resource_type: str,
    edits: list[Any],
    script: dict[str, Any] | None,
    script_filename: str | None,
    warnings: list[str],
) -> list[TaskSpec]:
    label = _LABEL_ZH[resource_type]
    specs: list[TaskSpec] = []
    seen_ids: set[str] = set()
    for edit in edits:
        if not isinstance(edit, dict):
            warnings.append(f"⚠️  edits 中存在非法条目（须为对象），跳过: {edit!r}")
            continue
        resource_id = str(edit.get("id") or "").strip()
        instruction = str(edit.get("instruction") or "").strip()
        if not resource_id:
            warnings.append("⚠️  edits 中存在缺少 id 的条目，跳过")
            continue
        if resource_id in seen_ids:
            warnings.append(f"⚠️  {label} '{resource_id}' 在 edits 中重复出现，仅保留第一条编辑指令")
            continue
        seen_ids.add(resource_id)
        if not instruction:
            warnings.append(f"⚠️  {label} '{resource_id}' 缺少编辑指令，跳过")
            continue
        try:
            current_rel = resolve_current_image_rel(project, resource_type, resource_id, script)
        except KeyError:
            warnings.append(f"⚠️  {label} '{resource_id}' 不存在，跳过")
            continue
        if not (current_rel and safe_exists(project_path, current_rel)):
            warnings.append(f"⚠️  {label} '{resource_id}' 没有可编辑的当前图，跳过")
            continue
        specs.append(
            TaskSpec.from_request(
                task_type="image_edit",
                media_type="image",
                resource_id=resource_id,
                prompt=instruction,
                script_file=script_filename if resource_type == "storyboard" else None,
                extra_payload={"resource_type": resource_type},
            )
        )
    return specs


def edit_images_tool(ctx: ToolContext):
    @tool(
        "edit_images",
        "对已生成的设计图/分镜图做指令式局部编辑：保持原图大体不变，仅按指令修改不满意的部分"
        "（如换发色、去掉背景杂物、调整光线氛围），支持同类型批量下发。"
        "与「重新生成」的区别：编辑=保底图微调、不改变原 image_prompt，重生成会作废本次编辑效果"
        "（仍按原 prompt 重画）；重新生成=按原 prompt 整图重画，会推翻已满意的部分。"
        "用户只想改局部时用编辑；用户想推翻构图/内容重来、或原 image_prompt 本身要改时用重新生成。"
        "resource_type 支持 character/scene/prop/product/storyboard 五类，storyboard 必须带 script_file。"
        "编辑必然走图生图（i2i）；当前项目图片供应商不支持 i2i 时直接返回错误，不创建任何任务。",
        {
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "enum": list(EDITABLE_RESOURCE_TYPES),
                    "description": "编辑目标类型",
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "资产名称，或 storyboard 的 segment_id/scene_id",
                            },
                            "instruction": {"type": "string", "description": "编辑指令（自然语言，描述要修改的部分）"},
                        },
                        "required": ["id", "instruction"],
                    },
                    "minItems": 1,
                    "description": "批量编辑列表，每项一个 id + instruction",
                },
                "script_file": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json）；resource_type=storyboard 时必填，"
                    "必须是纯文件名，禁止任何路径分隔符",
                },
            },
            "required": ["resource_type", "edits"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            resource_type = args.get("resource_type")
            if resource_type not in EDITABLE_RESOURCE_TYPES:
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": f"resource_type 必须是以下之一: {', '.join(EDITABLE_RESOURCE_TYPES)}",
                        }
                    ],
                    "is_error": True,
                }

            edits = args.get("edits")
            if not isinstance(edits, list) or not edits:
                return {"content": [{"type": "text", "text": "edits 不能为空"}], "is_error": True}

            is_storyboard = resource_type == "storyboard"
            script_filename: str | None = None
            script: dict[str, Any] | None = None
            if is_storyboard:
                raw_script = args.get("script_file")
                if not raw_script:
                    return {
                        "content": [{"type": "text", "text": "resource_type=storyboard 时 script_file 必填"}],
                        "is_error": True,
                    }
                script_filename = validate_script_filename(raw_script)
                script = ctx.pm.load_script(ctx.project_name, script_filename)

            project = ctx.pm.load_project(ctx.project_name)
            project_path = ctx.project_path

            if not await _i2i_provider_available(project):
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": "❌ 当前项目图片供应商不支持图生图（i2i），无法执行编辑；"
                            "请提示用户前往设置更换支持 i2i 的供应商",
                        }
                    ],
                    "is_error": True,
                }

            warnings: list[str] = []
            specs = _build_specs(project, project_path, resource_type, edits, script, script_filename, warnings)
            if not specs:
                return {
                    "content": [{"type": "text", "text": "\n".join([*warnings, "没有可执行的编辑任务"])}],
                    "is_error": True,
                }

            successes, failures = await batch_enqueue_and_wait(
                project_name=ctx.project_name,
                specs=specs,
            )

            details: list[str] = []
            for br in successes:
                result = br.result or {}
                version = result.get("version")
                version_text = f" (v{version})" if version is not None else ""
                file_path = result.get("file_path") or br.resource_id
                details.append(f"  ✓ {br.resource_id} → {file_path}{version_text}")
            for br in failures:
                details.append(f"  ✗ {br.resource_id}: {br.error}")

            header = f"edit_images summary: {len(successes)} succeeded, {len(failures)} failed"
            return {
                "content": [{"type": "text", "text": "\n".join([*warnings, header, *details])}],
                "is_error": bool(failures),
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("edit_images", exc)

    return _handler


__all__ = ["edit_images_tool"]
