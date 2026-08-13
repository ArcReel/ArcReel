"""SDK MCP tool for grid storyboard generation."""

from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import tool

from lib.generation_queue_client import enqueue_task_only, wait_for_task
from lib.grid.layout import GridLayout, plan_grid_chunks, video_aspect_ratio_of
from lib.grid.models import GridGeneration, build_grid_task_payload
from lib.grid.prompt_builder import build_grid_prompt
from lib.grid_manager import GridManager
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager, grid_storyboard_enabled
from lib.script_models import resolve_content_mode
from lib.script_skeleton import ensure_route_skeleton
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error, validate_script_filename
from server.services.grid_resolution import resolve_large_grid_allowed
from server.services.grid_split import apply_grid_split


def _list_groups(
    project: dict,
    script: dict,
    scene_ids: list[str] | None = None,
    *,
    allow_large_grid: bool = False,
) -> list[str]:
    """List grid groups, optionally filtered to groups containing ``scene_ids``.

    Empty list (``[]``) and ``None`` carry different intents: ``None`` means
    "no filter, list all groups"; ``[]`` means "filter to zero groups"
    (explicit zero selection). Use ``is not None`` to keep them distinct.

    ``allow_large_grid`` 与实际生成分支同源，非 4K 项目的预览里不会出现 4×4 / 5×5。
    分块与实际入队同源（``plan_grid_chunks``）：超上限分组展示的宫格张数与档位
    即实际生成的张数与档位。
    """
    items, id_field, _, _, _ = get_storyboard_items(script)
    aspect_ratio = video_aspect_ratio_of(project)
    groups = group_scenes_by_segment_break(items, id_field)
    if scene_ids is not None:
        wanted = set(scene_ids)
        groups = [g for g in groups if any(item[id_field] in wanted for item in g)]
    lines = [f"共 {len(groups)} 个分组："]
    for i, group in enumerate(groups):
        ids = [item[id_field] for item in group]
        plans = plan_grid_chunks(group, aspect_ratio, allow_large_grid=allow_large_grid)
        status = _describe_plans(plans)
        lines.append(f"  组 {i + 1}: {ids[0]}..{ids[-1]} ({len(ids)} 场景) → {status}")
    return lines


def _describe_plans(plans: list[tuple[list[dict[str, Any]], GridLayout]]) -> str:
    """把一组的宫格规划渲染为预览文案；单张沿用原格式，多张标注张数。"""
    if not plans:
        return "skip (空分组)"
    parts = [f"{layout.grid_size} ({layout.rows}×{layout.cols})" for _, layout in plans]
    if len(parts) == 1:
        return parts[0]
    return f"{len(parts)} 张宫格: " + " + ".join(parts)


def generate_grid_tool(ctx: ToolContext):
    @tool(
        "generate_grid",
        "为已开启宫格装配的 storyboard 项目（generation_mode=storyboard 且 grid_storyboard=true）"
        "生成宫格联合图（按 segment_break 分组），并在每张生成完成后自动执行切分落格，"
        "端到端产出各场景起始分镜图。"
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
            # 失配剧本在此被拒：按分镜路线该读的数组不在剧本里，继续走下去只会
            # 报"没有匹配的场景组"，把成因埋掉。
            ensure_route_skeleton(script, resolve_content_mode(script, project), project.get("generation_mode"))

            # ``list_only`` 是 ``generate_grid`` 工具的预览模式，与生成分支一样
            # 必须先过宫格开关校验——否则未开宫格的项目靠 ``list_only=true``
            # 就能拿到成功响应，调用方会误以为该工具适用于当前项目。
            if not grid_storyboard_enabled(project):
                return {
                    "content": [{"type": "text", "text": "⚠️  项目未启用宫格分镜（grid_storyboard 未开启）"}],
                    "is_error": True,
                }

            # 4×4 / 5×5 只在图像分辨率档为 4K 时放行；预览与生成共用同一次判定
            allow_large_grid = await resolve_large_grid_allowed(project)

            if list_only:
                lines = _list_groups(project, script, scene_ids, allow_large_grid=allow_large_grid)
                return {"content": [{"type": "text", "text": "\n".join(lines)}]}

            episode = ProjectManager.resolve_episode_from_script(script, script_filename)
            project_path = ctx.project_path
            items, id_field, _, _, _ = get_storyboard_items(script)
            aspect_ratio = video_aspect_ratio_of(project)
            style = project.get("style", "")
            groups = group_scenes_by_segment_break(items, id_field)

            # ``scene_ids is not None`` 区分"不传 = 不过滤"和"传 [] = 过滤到 0
            # 个"——后者是显式空选择，按 ``not groups`` 分支当错误返回。同样
            # 处理 list_only 预览（见 ``_list_groups``）保持一致。
            if scene_ids is not None:
                wanted = set(scene_ids)
                groups = [g for g in groups if any(item[id_field] in wanted for item in g)]

            if not groups:
                # 显式传了 ``scene_ids`` 但全部不命中（或传了 []）→ 错误；
                # 不传 ``scene_ids`` 但脚本本身没有 segment_break 分组也走
                # 这条路（罕见），按信息文案不带 is_error。
                return {
                    "content": [{"type": "text", "text": "没有匹配的场景组"}],
                    "is_error": scene_ids is not None,
                }

            gm = GridManager(project_path)
            pending: list[tuple[GridGeneration, str]] = []
            enqueue_failures: list[tuple[str, list[str], str]] = []

            for group in groups:
                # 超上限分组切为多张宫格逐张入队：每张的场景数与画格数一致
                # （末张不足一档时落小档 + 占位格），与预览、费用估算同源。
                # 空分组（``plan_grid_chunks`` 的唯一空产出）自然跳过循环体。
                plans = plan_grid_chunks(group, aspect_ratio, allow_large_grid=allow_large_grid)
                # 与 WebUI 入队路径同源：重生成该组时清理旧的已完成记录（同脚本
                # 同集、scene_ids 是当前组子集、非在途），前端列表只显示新一代
                # 宫格图。规则唯一定义在 GridManager.cleanup_superseded。
                if plans:
                    gm.cleanup_superseded(script_filename, episode, {item[id_field] for item in group})
                for chunk, layout in plans:
                    chunk_ids = [item[id_field] for item in chunk]
                    prompt = build_grid_prompt(
                        scenes=chunk,
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
                        scene_ids=chunk_ids,
                        rows=layout.rows,
                        cols=layout.cols,
                        grid_size=layout.grid_size,
                        provider="",
                        model="",
                        video_aspect_ratio=aspect_ratio,
                        prompt=prompt,
                    )
                    # 先 save 后 enqueue 给 worker 提供可读的 grid 文件；入队失败时
                    # 用 ``gm.delete`` 回收孤儿记录，并把该张并入 failures——前面已
                    # 入队成功的宫格继续跑，调用方不会被一张失败导致全量重试。
                    gm.save(grid)
                    try:
                        enqueue_result = await enqueue_task_only(
                            project_name=ctx.project_name,
                            task_type="grid",
                            media_type="image",
                            resource_id=grid.id,
                            payload=build_grid_task_payload(
                                prompt=prompt,
                                script_file=script_filename,
                                scene_ids=chunk_ids,
                                grid_size=layout.grid_size,
                                rows=layout.rows,
                                cols=layout.cols,
                                grid_aspect_ratio=layout.grid_aspect_ratio,
                                video_aspect_ratio=aspect_ratio,
                            ),
                            script_file=script_filename,
                            source="skill",
                        )
                    except Exception as exc:  # noqa: BLE001
                        gm.delete(grid.id)
                        enqueue_failures.append((grid.id, chunk_ids, str(exc)))
                        continue
                    pending.append((grid, enqueue_result["task_id"]))

            details: list[str] = []
            for grid_id, group_ids, err in enqueue_failures:
                details.append(f"  ✗ {grid_id}（{group_ids[0]}..{group_ids[-1]}）入队失败: {err}")

            if not pending:
                msg = "\n".join([*details, "没有需要生成的宫格组"])
                return {
                    "content": [{"type": "text", "text": msg}],
                    "is_error": bool(enqueue_failures),
                }

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
                if result.get("status") != "succeeded":
                    err = result.get("error_message") or "unknown"
                    failures.append((grid.id, err))
                    details.append(f"  ✗ {grid.id}: {err}")
                    continue
                # 生成任务只产出联合图；分镜格落盘由编排方在此显式调用切分补上，
                # 端到端语义（分镜格齐备）不变。重载记录取 worker 回填的最新状态。
                try:
                    reloaded = gm.get(grid.id)
                    if reloaded is None:
                        raise RuntimeError(f"grid record missing after generation: {grid.id}")
                    with project_change_source("worker"):
                        split_result = await apply_grid_split(ctx.project_name, reloaded)
                except Exception as exc:  # noqa: BLE001
                    # 联合图已生成成功，仅落格失败：不并入生成失败语义，提示可在
                    # WebUI 宫格面板重试切分（无需重新生成、不重复计费）。
                    failures.append((grid.id, str(exc)))
                    details.append(f"  ✗ {grid.id}: 联合图已生成，但切分落格失败（可在宫格面板重试切分）: {exc}")
                    continue
                successes.append(grid.id)
                line = f"  ✓ {grid.id}（{grid.scene_ids[0]}..{grid.scene_ids[-1]}，已切分 {len(split_result.updated_scene_ids)} 格）"
                if split_result.missing_scene_ids:
                    line += f"，跳过已不在剧本的分镜: {split_result.missing_scene_ids}"
                details.append(line)

            total_failed = len(failures) + len(enqueue_failures)
            header = f"generate_grid summary: {len(successes)} succeeded, {total_failed} failed"
            return {
                "content": [{"type": "text", "text": "\n".join([header, *details])}],
                "is_error": bool(failures) or bool(enqueue_failures),
            }
        except Exception as exc:  # noqa: BLE001
            return tool_error("generate_grid", exc)

    return _handler


__all__ = ["generate_grid_tool"]
