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
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename


class _FailureRecorder:
    """Records storyboard failures to ``storyboards/generation_failures.json``."""

    def __init__(self, output_dir: Path) -> None:
        self.output_path = output_dir / "generation_failures.json"
        self.failures: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, resource_id: str, resource_type: str, error: str, attempts: int = 3) -> None:
        """Append a failure entry. ``resource_type`` is ``segment`` (narration)
        or ``scene`` (drama) — driven by the script's ``id_field``."""
        with self._lock:
            self.failures.append(
                {
                    "resource_id": resource_id,
                    "type": resource_type,
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

    if is_structured_image_prompt(image_prompt):
        yaml_prompt = image_prompt_to_yaml(image_prompt, style)
        return f"{style_prefix}{yaml_prompt}"
    return f"{style_prefix}{image_prompt}"


def _select_items(items: list[dict[str, Any]], id_field: str, segment_ids: list[str] | None) -> list[dict[str, Any]]:
    # ``None`` 和 ``[]`` 含义不同：``None`` = "不传过滤，默认扫所有缺图项"；
    # ``[]`` = "显式空选择，应当返回空列表交由 handler 报错"。
    if segment_ids is not None:
        wanted = {str(s) for s in segment_ids}
        return [item for item in items if str(item.get(id_field)) in wanted]
    return [item for item in items if not item.get("generated_assets", {}).get("storyboard_image")]


def _build_specs(
    plans: list[StoryboardTaskPlan],
    items_by_id: dict[str, dict[str, Any]],
    style: str,
    style_description: str,
    id_field: str,
    script_filename: str,
) -> list[BatchTaskSpec]:
    specs: list[BatchTaskSpec] = []
    for plan in plans:
        item = items_by_id[plan.resource_id]
        prompt = _build_prompt(item, style, style_description, id_field)
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

            try:
                project_data = ctx.pm.load_project(ctx.project_name)
            except FileNotFoundError:
                # project.json 缺失时允许降级到空 dict（style 走默认值）；
                # JSON 损坏 / 权限错误等其他异常应该让外层 tool_error 暴露出来，
                # 否则会用空 style 静默继续入队，丢掉了配置。
                project_data = {}

            items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
            selected = _select_items(items, id_field, segment_ids)
            if not selected:
                # 区分两种零结果：调用方显式传了 segment_ids（None vs []，None 即
                # "未传"，[] 与不命中等价都按错误处理）vs 全部已生成（真无事可做）。
                if segment_ids is not None:
                    return {
                        "content": [
                            {
                                "type": "text",
                                "text": f"❌ 没有找到匹配的片段/场景：segment_ids={segment_ids}",
                            }
                        ],
                        "is_error": True,
                    }
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
                script_filename,
            )

            recorder = _FailureRecorder(project_dir / "storyboards")
            successes, failures = await batch_enqueue_and_wait(
                project_name=ctx.project_name,
                specs=specs,
            )
            # narration → segment_id / drama → scene_id：``id_field`` 是脚本里
            # 的规范字段名，``"segment"`` / ``"scene"`` 是对应的资源类型。
            resource_type = "segment" if id_field == "segment_id" else "scene"
            for f in failures:
                recorder.record(f.resource_id, resource_type, f.error or "unknown")
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
            return tool_error("generate_storyboards", exc)

    return _handler


__all__ = ["generate_storyboards_tool"]
