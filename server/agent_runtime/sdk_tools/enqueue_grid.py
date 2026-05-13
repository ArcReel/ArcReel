"""SDK MCP tool for grid storyboard generation."""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import tool

from lib.generation_queue_client import enqueue_task_only, wait_for_task
from lib.grid.layout import calculate_grid_layout
from lib.grid.models import GridGeneration
from lib.grid.prompt_builder import build_grid_prompt
from lib.grid_manager import GridManager
from lib.project_manager import ProjectManager
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename


def _list_groups(project: dict, script: dict) -> list[str]:
    items, id_field, _, _, _ = get_storyboard_items(script)
    aspect_ratio = project.get("aspect_ratio", "9:16")
    groups = group_scenes_by_segment_break(items, id_field)
    lines = [f"共 {len(groups)} 个分组："]
    for i, group in enumerate(groups):
        ids = [item[id_field] for item in group]
        layout = calculate_grid_layout(len(ids), aspect_ratio)
        status = f"{layout.grid_size} ({layout.rows}×{layout.cols})" if layout else "single (< 4 场景)"
        lines.append(f"  组 {i + 1}: {ids[0]}..{ids[-1]} ({len(ids)} 场景) → {status}")
    return lines


def generate_grid_tool(ctx: ToolContext):
    @tool(
        "generate_grid",
        "为 grid 模式项目生成宫格分镜图（按 segment_break 分组）。"
        "list_only=true 时只列出分组不执行生成。scene_ids 过滤包含这些场景的分组。",
        {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "剧本文件名（如 episode_1.json），必须是纯文件名，禁止任何路径分隔符",
                },
                "scene_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "只生成包含这些场景的分组",
                },
                "list_only": {"type": "boolean", "description": "仅列出分组信息，不入队"},
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            scene_ids = args.get("scene_ids")
            list_only = bool(args.get("list_only"))

            project = ctx.pm.load_project(ctx.project_name)
            script = ctx.pm.load_script(ctx.project_name, script_filename)

            if list_only:
                return {"content": [{"type": "text", "text": "\n".join(_list_groups(project, script))}]}

            if project.get("generation_mode") != "grid":
                return {
                    "content": [{"type": "text", "text": "⚠️  项目未启用宫格模式（generation_mode != 'grid'）"}],
                    "is_error": True,
                }

            episode = ProjectManager.resolve_episode_from_script(script, script_filename)
            project_path = ctx.project_path
            items, id_field, _, _, _ = get_storyboard_items(script)
            aspect_ratio = project.get("aspect_ratio", "9:16")
            style = project.get("style", "")
            groups = group_scenes_by_segment_break(items, id_field)

            if scene_ids:
                wanted = set(scene_ids)
                groups = [g for g in groups if any(item[id_field] in wanted for item in g)]

            if not groups:
                return {"content": [{"type": "text", "text": "没有匹配的场景组"}]}

            gm = GridManager(project_path)
            pending: list[tuple[GridGeneration, str]] = []
            skipped: list[str] = []

            for group in groups:
                group_ids = [item[id_field] for item in group]
                layout = calculate_grid_layout(len(group_ids), aspect_ratio)
                if layout is None:
                    skipped.append(f"⏭️  跳过 {group_ids[0]}..{group_ids[-1]}（{len(group_ids)} 场景，不足 4 个）")
                    continue

                prompt = build_grid_prompt(
                    scenes=group,
                    id_field=id_field,
                    rows=layout.rows,
                    cols=layout.cols,
                    style=style,
                    aspect_ratio=aspect_ratio,
                    grid_aspect_ratio=layout.grid_aspect_ratio,
                )

                grid = GridGeneration.create(
                    episode=episode,
                    script_file=script_filename,
                    scene_ids=group_ids,
                    rows=layout.rows,
                    cols=layout.cols,
                    grid_size=layout.grid_size,
                    provider="",
                    model="",
                    prompt=prompt,
                )
                gm.save(grid)

                enqueue_result = await enqueue_task_only(
                    project_name=ctx.project_name,
                    task_type="grid",
                    media_type="image",
                    resource_id=grid.id,
                    payload={
                        "prompt": prompt,
                        "script_file": script_filename,
                        "scene_ids": group_ids,
                        "grid_size": layout.grid_size,
                        "rows": layout.rows,
                        "cols": layout.cols,
                        "grid_aspect_ratio": layout.grid_aspect_ratio,
                        "video_aspect_ratio": aspect_ratio,
                    },
                    script_file=script_filename,
                    source="skill",
                )
                pending.append((grid, enqueue_result["task_id"]))

            if not pending:
                return {"content": [{"type": "text", "text": "\n".join([*skipped, "没有需要生成的宫格组"])}]}

            details: list[str] = list(skipped)
            successes: list[str] = []
            failures: list[tuple[str, str]] = []
            # Wait for all queued grids concurrently — image worker channel can run
            # multiple in parallel, so serial wait_for_task would mask that throughput.
            results = await asyncio.gather(
                *(wait_for_task(task_id) for _, task_id in pending),
                return_exceptions=True,
            )
            for (grid, _task_id), result in zip(pending, results, strict=True):
                if isinstance(result, BaseException):
                    failures.append((grid.id, str(result)))
                    details.append(f"  ✗ {grid.id}: {result}")
                    continue
                if result.get("status") == "succeeded":
                    successes.append(grid.id)
                    details.append(f"  ✓ {grid.id}（{grid.scene_ids[0]}..{grid.scene_ids[-1]}）")
                else:
                    err = result.get("error_message") or "unknown"
                    failures.append((grid.id, err))
                    details.append(f"  ✗ {grid.id}: {err}")

            header = f"generate_grid summary: {len(successes)} succeeded, {len(failures)} failed"
            return {
                "content": [{"type": "text", "text": "\n".join([header, *details])}],
                "is_error": bool(failures),
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_grid", exc)

    return _handler


__all__ = ["generate_grid_tool"]
