"""SDK MCP tool for storyboard image generation (narration / drama)."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from lib.generation_queue_client import (
    BatchTaskResult,
    BatchTaskSpec,
    batch_enqueue_and_wait,
)
from lib.prompt_utils import image_prompt_to_yaml, is_structured_image_prompt
from lib.storyboard_sequence import (
    StoryboardTaskPlan,
    build_storyboard_dependency_plan,
    get_storyboard_items,
)
from server.agent_runtime.sdk_tools._context import ToolContext, validate_script_filename


class _FailureRecorder:
    """Records storyboard failures to ``storyboards/generation_failures.json``."""

    def __init__(self, output_dir: Path) -> None:
        self.output_path = output_dir / "generation_failures.json"
        self.failures: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, scene_id: str, error: str, attempts: int = 3) -> None:
        with self._lock:
            self.failures.append(
                {
                    "scene_id": scene_id,
                    "type": "scene",
                    "error": error,
                    "attempts": attempts,
                    "timestamp": datetime.now().isoformat(),
                }
            )

    def save(self) -> None:
        if not self.failures:
            return
        with self._lock:
            data = {
                "generated_at": datetime.now().isoformat(),
                "total_failures": len(self.failures),
                "failures": self.failures,
            }
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_prompt(
    segment: dict[str, Any],
    style: str,
    style_description: str,
    id_field: str,
    content_mode: str,
) -> str:
    image_prompt = segment.get("image_prompt", "")
    if not image_prompt:
        raise ValueError(f"片段/场景 {segment[id_field]} 缺少 image_prompt 字段")

    style_parts: list[str] = []
    if style:
        style_parts.append(f"Style: {style}")
    if style_description:
        style_parts.append(f"Visual style: {style_description}")
    style_prefix = "\n".join(style_parts) + "\n\n" if style_parts else ""

    composition_suffix = ""
    if content_mode == "narration":
        composition_suffix = "\n竖屏构图。" if is_structured_image_prompt(image_prompt) else " 竖屏构图。"

    if is_structured_image_prompt(image_prompt):
        yaml_prompt = image_prompt_to_yaml(image_prompt, style)
        return f"{style_prefix}{yaml_prompt}{composition_suffix}"
    return f"{style_prefix}{image_prompt}{composition_suffix}"


def _select_items(items: list[dict[str, Any]], id_field: str, segment_ids: list[str] | None) -> list[dict[str, Any]]:
    if segment_ids:
        wanted = {str(s) for s in segment_ids}
        return [item for item in items if str(item.get(id_field)) in wanted]
    return [item for item in items if not item.get("generated_assets", {}).get("storyboard_image")]


def _build_specs(
    plans: list[StoryboardTaskPlan],
    items_by_id: dict[str, dict[str, Any]],
    style: str,
    style_description: str,
    id_field: str,
    content_mode: str,
    script_filename: str,
) -> list[BatchTaskSpec]:
    specs: list[BatchTaskSpec] = []
    for plan in plans:
        item = items_by_id[plan.resource_id]
        prompt = _build_prompt(item, style, style_description, id_field, content_mode)
        specs.append(
            BatchTaskSpec(
                task_type="storyboard",
                media_type="image",
                resource_id=plan.resource_id,
                payload={"prompt": prompt, "script_file": script_filename},
                script_file=script_filename,
                dependency_resource_id=plan.dependency_resource_id,
                dependency_group=plan.dependency_group,
                dependency_index=plan.dependency_index,
            )
        )
    return specs


def generate_storyboards_tool(ctx: ToolContext):
    @tool(
        "generate_storyboards",
        "为 narration/drama 模式剧本生成分镜图。"
        "script 为剧本文件名（如 episode_1.json）；segment_ids 指定要重生的片段/场景 ID 列表（不传则生成所有缺图项）。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "segment_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "片段或场景 ID 列表；不传则扫描所有缺分镜图的项",
                },
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            segment_ids = args.get("segment_ids")

            script = ctx.pm.load_script(ctx.project_name, script_filename)
            project_dir = ctx.project_path
            content_mode = script.get("content_mode", "narration")

            try:
                project_data = ctx.pm.load_project(ctx.project_name)
            except Exception:  # noqa: BLE001 — project.json 缺失允许降级
                project_data = {}

            items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
            selected = _select_items(items, id_field, segment_ids)
            if not selected:
                return {"content": [{"type": "text", "text": "✨ 所有片段的分镜图都已生成"}]}

            style = project_data.get("style", "")
            style_description = project_data.get("style_description", "")
            items_by_id = {str(item[id_field]): item for item in items if item.get(id_field)}
            plans = build_storyboard_dependency_plan(
                items,
                id_field,
                [str(item[id_field]) for item in selected],
                script_filename,
            )
            specs = _build_specs(
                plans,
                items_by_id,
                style,
                style_description,
                id_field,
                content_mode,
                script_filename,
            )

            recorder = _FailureRecorder(project_dir / "storyboards")
            successes, failures = await batch_enqueue_and_wait(
                project_name=ctx.project_name,
                specs=specs,
            )
            for f in failures:
                recorder.record(f.resource_id, f.error or "unknown")
            recorder.save()

            details: list[str] = []
            success_map = {s.resource_id: s for s in successes}
            for plan in plans:
                br: BatchTaskResult | None = success_map.get(plan.resource_id)
                if br is None:
                    continue
                result = br.result or {}
                rel = result.get("file_path") or f"storyboards/scene_{plan.resource_id}.png"
                details.append(f"  ✓ {plan.resource_id} → {rel}")
            for f in failures:
                details.append(f"  ✗ {f.resource_id}: {f.error}")

            header = f"generate_storyboards summary: {len(successes)} succeeded, {len(failures)} failed"
            return {
                "content": [{"type": "text", "text": "\n".join([header, *details])}],
                "is_error": bool(failures),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"generate_storyboards 失败: {exc}"}],
                "is_error": True,
            }

    return _handler


__all__ = ["generate_storyboards_tool"]
