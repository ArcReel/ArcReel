"""SDK MCP tool for storyboard image generation (narration / drama)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from lib.artifact_activation import (
    active_artifact_currency_resolver,
    resolve_artifact_episode,
)
from lib.artifact_manifest import ArtifactKey
from lib.generation_queue_client import (
    TaskSpec,
    batch_enqueue_and_wait,
)
from lib.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    normalize_requested_ids,
    record_batch_outcomes,
    select_generation_targets,
)
from lib.prompt_builders import build_storyboard_prompt
from lib.resource_paths import resource_relative_path
from lib.script_models import get_generated_assets, resolve_content_mode
from lib.script_skeleton import ensure_route_skeleton
from lib.storyboard_sequence import (
    build_storyboard_dependency_plan,
    get_storyboard_items,
)
from server.agent_runtime.sdk_tools._context import (
    ToolContext,
    generation_result_response,
    tool_error,
    validate_script_filename,
)

_OPERATION = "generate_storyboards"


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
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )

    def save(self) -> None:
        if not self.failures:
            return
        with self._lock:
            data = {
                "generated_at": datetime.now(UTC).isoformat(),
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
        raise ValueError(f"分镜 {segment[id_field]} 缺少 image_prompt 字段")
    return build_storyboard_prompt(image_prompt, style, style_description)


def generate_storyboards_tool(ctx: ToolContext):
    @tool(
        _OPERATION,
        "为 narration/剧情演绎剧本生成分镜图。"
        "script 为剧本文件名（如 episode_1.json）；segment_ids 指定要重生的分镜 ID 列表"
        "（不传则只生成缺分镜图的项；已失效但可用的旧图不会被自动重生）。"
        "返回 requested / succeeded / failed / blocked 的逐 ID 结果，每个失败项带稳定 code 与下一步动作。",
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
                    "description": "分镜 ID 列表；不传则只选缺分镜图的项",
                },
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            segment_ids = normalize_requested_ids(args.get("segment_ids"), field="segment_ids")

            script = ctx.pm.load_script(ctx.project_name, script_filename)
            project_dir = ctx.project_path

            try:
                project_data = ctx.pm.load_project(ctx.project_name)
            except FileNotFoundError:
                # project.json 缺失时允许降级到空 dict（style 走默认值）；
                # JSON 损坏 / 权限错误等其他异常应该让外层 tool_error 暴露出来，
                # 否则会用空 style 静默继续入队，丢掉了配置。
                project_data = {}

            if project_data:
                # 失配剧本在此被拒：按分镜图生视频该读的数组不在剧本里，继续走下去
                # 会落进空结果的假成功，把成因埋掉。project.json 缺失时无生成模式可依，
                # 沿用上面的降级放行。
                ensure_route_skeleton(
                    script, resolve_content_mode(script, project_data), project_data.get("generation_mode")
                )

            items, id_field, _char_field, _scene_field, _prop_field = get_storyboard_items(script)
            resolver = active_artifact_currency_resolver(project_dir, project_data)
            episode = (
                resolve_artifact_episode(
                    project=project_data,
                    script=script,
                    script_filename=script_filename,
                )
                or 1
            )
            items_by_id = {str(item[id_field]): item for item in items if item.get(id_field)}
            selection = select_generation_targets(
                candidates=[
                    GenerationCandidate(
                        unit_id=unit_id,
                        artifact_key=ArtifactKey.episode_storyboard(episode, unit_id),
                        artifact_path=get_generated_assets(item).get("storyboard_image"),
                    )
                    for unit_id, item in items_by_id.items()
                ],
                requested_ids=segment_ids,
                resolver=resolver,
            )
            builder = GenerationResultBuilder.from_selection(_OPERATION, selection)

            style = project_data.get("style", "")
            style_description = project_data.get("style_description", "")
            targets = []
            for state in selection.targets:
                try:
                    _build_prompt(items_by_id[state.unit_id], style, style_description, id_field)
                except (KeyError, TypeError, ValueError) as exc:
                    builder.block(
                        state.unit_id,
                        problem=GenerationProblem(
                            code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                            detail=str(exc),
                            action=GenerationAction.FIX_INPUT,
                        ),
                        artifact_key=state.artifact_key,
                        artifact_path=state.artifact_path,
                        artifact_status=state.status,
                    )
                    continue
                targets.append(state)

            by_id = {state.unit_id: state for state in targets}
            plans = build_storyboard_dependency_plan(
                items,
                id_field,
                [state.unit_id for state in targets],
                script_filename,
            )
            specs = [
                TaskSpec.from_request(
                    task_type="storyboard",
                    media_type="image",
                    resource_id=plan.resource_id,
                    prompt=items_by_id[plan.resource_id].get("image_prompt"),
                    script_file=script_filename,
                    dependency_resource_id=plan.dependency_resource_id,
                    dependency_group=plan.dependency_group,
                    dependency_index=plan.dependency_index,
                )
                for plan in plans
            ]

            if specs:
                recorder = _FailureRecorder(project_dir / "storyboards")
                successes, failures = await batch_enqueue_and_wait(
                    project_name=ctx.project_name,
                    specs=specs,
                )
                # narration → segment_id / drama → scene_id：``id_field`` 是脚本里
                # 的规范字段名，``"segment"`` / ``"scene"`` 是对应的资源类型。
                resource_type = "segment" if id_field == "segment_id" else "scene"
                for br in failures:
                    recorder.record(br.resource_id, resource_type, br.error or "unknown")
                recorder.save()

                record_batch_outcomes(
                    builder,
                    successes=successes,
                    failures=failures,
                    states=by_id,
                    resolver=resolver,
                    fallback_path=lambda rid: resource_relative_path("storyboards", rid),
                )

            return generation_result_response(builder.build())
        except Exception as exc:  # noqa: BLE001
            return tool_error(_OPERATION, exc)

    return _handler


__all__ = ["generate_storyboards_tool"]
