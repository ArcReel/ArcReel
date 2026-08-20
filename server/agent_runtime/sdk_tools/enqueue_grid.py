"""SDK MCP tool for grid storyboard generation.

Results are addressed by **scene** ID even though several scenes share one grid
image: the caller requests scenes, and one grid's enqueue, task, cut or reuse
outcome is projected onto every scene it covers — including a cut that dropped
an individual cell.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any

from claude_agent_sdk import tool

from lib.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_artifact_episode,
)
from lib.artifact_manifest import ArtifactKey
from lib.generation_queue_client import enqueue_task_only, is_interrupted_wait_error, wait_for_task
from lib.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTaskState,
    ProviderCheckpoint,
    artifact_state_problem,
    normalize_requested_ids,
    observe_artifact_status,
    problem_from_task_failure,
    provider_checkpoint_from_task,
    select_generation_targets,
)
from lib.grid.layout import GridLayout, plan_grid_chunks, video_aspect_ratio_of
from lib.grid.models import GridGeneration, build_grid_task_payload
from lib.grid.prompt_builder import build_grid_prompt
from lib.grid_manager import GridManager
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager, grid_storyboard_enabled
from lib.resource_paths import resource_relative_path
from lib.script_models import get_generated_assets, resolve_content_mode
from lib.script_skeleton import ensure_route_skeleton
from lib.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from server.agent_runtime.sdk_tools._context import (
    ToolContext,
    generation_result_response,
    tool_error,
    validate_script_filename,
)
from server.services.grid_resolution import resolve_large_grid_allowed
from server.services.grid_split import apply_grid_split

_OPERATION = "generate_grid"


def _scene_artifact_key(episode: int, scene_id: str) -> ArtifactKey:
    """本工具的产物是各场景的分镜格；联合图只是共享的载体，不是被请求的 ID。"""

    return ArtifactKey.episode_storyboard(episode, scene_id)


def _fail_scenes(
    builder: GenerationResultBuilder,
    scene_ids: Iterable[str],
    *,
    problem: GenerationProblem,
    episode: int,
    resolver: ArtifactCurrencyResolver,
    task_id: str | None = None,
    task_state: GenerationTaskState = GenerationTaskState.FAILED,
    provider_checkpoint: ProviderCheckpoint | None = None,
    artifact_paths: Mapping[str, str] | None = None,
) -> None:
    """一张宫格的失败落到它覆盖的每个场景：调用方点的是场景，不是宫格。

    ``artifact_paths`` 是剧本里已登记的旧图路径（点名强制路线也会有——旧图存在，
    只是该路线不复用它）。失败不动旧产物，但报告要带上它的路径与状态，否则
    下游分不清「替换失败、旧图还在」和「原本就没有可复用产物」——两者对下一步
    动作（重试 vs 先补分镜图）的建议完全不同。
    """

    for scene_id in scene_ids:
        artifact_path = (artifact_paths or {}).get(scene_id)
        artifact_key = _scene_artifact_key(episode, scene_id)
        artifact_status, _blocker = observe_artifact_status(
            resolver=resolver, key=artifact_key, artifact_path=artifact_path
        )
        builder.fail(
            scene_id,
            problem=problem,
            artifact_key=artifact_key,
            artifact_path=artifact_path,
            artifact_status=artifact_status,
            task_id=task_id,
            task_state=task_state,
            provider_checkpoint=provider_checkpoint,
        )


def _list_groups(
    project: dict,
    script: dict,
    scene_ids: list[str] | None = None,
    *,
    allow_large_grid: bool = False,
) -> list[str]:
    """List grid groups, optionally filtered to groups containing ``scene_ids``.

    ``None`` means "no filter, list all groups"; an explicit list narrows to the
    groups containing those scenes (an empty one is rejected upstream, see
    ``normalize_requested_ids``).

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
        _OPERATION,
        "为已开启宫格装配的 storyboard 项目（generation_mode=storyboard 且 grid_storyboard=true）"
        "生成宫格联合图（按 segment_break 分组），并在每张生成完成后自动执行切分落格，"
        "端到端产出各场景起始分镜图。"
        "list_only=true 时只列出分组不执行生成。scene_ids 过滤包含这些场景的分组；"
        "不传 scene_ids 时只生成仍缺分镜图的分组，已失效但可用的旧图会被复用而不重生。"
        "结果按 requested / succeeded / failed / blocked 逐场景 ID 返回"
        "（同一分组的场景共享一张宫格，该宫格的结果投影到它覆盖的每个场景）。",
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
                    "description": "只生成包含这些场景的分组；不传则只生成仍缺分镜图的分组",
                },
                "list_only": {"type": "boolean", "description": "仅列出分组信息，不入队"},
            },
            "required": ["script"],
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            script_filename = validate_script_filename(args["script"])
            scene_ids = normalize_requested_ids(args.get("scene_ids"), field="scene_ids")
            list_only = bool(args.get("list_only"))

            project = ctx.pm.load_project(ctx.project_name)
            script = ctx.pm.load_script(ctx.project_name, script_filename)
            # 失配剧本在此被拒：按分镜图生视频该读的数组不在剧本里，继续走下去只会
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

            episode = resolve_artifact_episode(
                project=project,
                script=script,
                script_filename=script_filename,
            )
            if episode is None:
                episode = ProjectManager.resolve_episode_from_script(script, script_filename)
            project_path = ctx.project_path
            items, id_field, _, _, _ = get_storyboard_items(script)
            aspect_ratio = video_aspect_ratio_of(project)
            style = project.get("style", "")
            resolver = active_artifact_currency_resolver(project_path, project)
            groups = group_scenes_by_segment_break(items, id_field)
            # 失败落回场景时用来带上旧图路径与状态（见 ``_fail_scenes`` docstring）；
            # 与选择阶段的候选路径同源，取自剧本已登记的 storyboard_image。
            scene_artifact_paths = {
                str(item.get(id_field)): path
                for item in items
                if item.get(id_field) and (path := get_generated_assets(item).get("storyboard_image"))
            }

            explicit = scene_ids is not None
            builder = GenerationResultBuilder(
                _OPERATION,
                GenerationSelectionMode.EXPLICIT if explicit else GenerationSelectionMode.MISSING_ONLY,
            )
            log: list[str] = []
            # 每个已选分组连同它的缺口 ID 集合一起记录：整组仍要一起生成（联合图靠
            # 相邻分镜连续性），但只有缺口 ID 才是这次调用实际请求生成的目标——组内
            # 已复用（``builder.skip`` 已记账）的场景不能进这里，否则宫格结果落盘会
            # 覆盖它们，且成功/失败回填时会撞上"重复记账"。
            selected_groups: list[tuple[list[dict[str, Any]], frozenset[str]]] = []

            if explicit:
                wanted = list(scene_ids or [])
                known = {str(item.get(id_field)) for item in items}
                for sid in wanted:
                    if sid not in known:
                        builder.block(
                            sid,
                            problem=GenerationProblem(
                                code=GenerationProblemCode.UNIT_NOT_FOUND,
                                detail=f"场景 {sid} 不在当前剧本中",
                                action=GenerationAction.FIX_INPUT,
                            ),
                        )
                wanted_set = set(wanted)
                selected_groups = [
                    (g, frozenset(str(item.get(id_field)) for item in g if str(item.get(id_field)) in wanted_set))
                    for g in groups
                    if any(str(item.get(id_field)) in wanted_set for item in g)
                ]
            else:
                for group in groups:
                    # 分组的缺口按成员分镜图判定：整组分镜图都还可用（含 stale）时
                    # 无需再出一张宫格，已付费的旧图照常复用。
                    selection = select_generation_targets(
                        candidates=[
                            GenerationCandidate(
                                unit_id=str(item.get(id_field)),
                                artifact_key=ArtifactKey.episode_storyboard(episode, str(item.get(id_field))),
                                artifact_path=get_generated_assets(item).get("storyboard_image"),
                            )
                            for item in group
                            if item.get(id_field)
                        ],
                        requested_ids=None,
                        resolver=resolver,
                    )
                    if selection.unavailable:
                        unavailable_ids = {state.unit_id for state in selection.unavailable}
                        for state in selection.unavailable:
                            builder.block(
                                state.unit_id,
                                problem=artifact_state_problem(state),
                                artifact_key=state.artifact_key,
                                artifact_path=state.artifact_path,
                                artifact_status=state.status,
                            )
                        # 宫格整组共用一张联合图：同组任一格状态不可读就无法安全出图，
                        # 组内仍缺分镜图的场景（targets）同样被阻塞，逐场景给结论而不是
                        # 留空让调用方猜。已复用的场景（skipped）不受影响——它们各自的
                        # 旧图已确认可用，这次调用本就不会碰它们，"产物状态不可读、需要
                        # 修复"对它们是错误结论，仍按正常复用记账。
                        for state in selection.targets:
                            if state.unit_id in unavailable_ids:
                                continue
                            builder.block(
                                state.unit_id,
                                problem=GenerationProblem(
                                    code=GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE,
                                    detail=f"同组场景 {sorted(unavailable_ids)} 的产物状态不可读，整张宫格无法生成",
                                    action=GenerationAction.REPAIR_ARTIFACT_STATE,
                                ),
                                artifact_key=state.artifact_key,
                                artifact_path=state.artifact_path,
                                artifact_status=state.status,
                            )
                        for state in selection.skipped:
                            builder.skip(state)
                        continue
                    if not selection.targets:
                        # 整组分镜图都还可用：逐场景报告复用，宫格本身不进结果集。
                        for state in selection.skipped:
                            builder.skip(state)
                        continue
                    # 组内混有缺口与可复用成员：可复用的先记复用，联合图仍带整组重绘
                    # 以保连续性，但落格与结果只投影到真正缺口的 ID——可复用成员的
                    # 旧图不被这次调用悄悄覆盖或重复计费。
                    for state in selection.skipped:
                        builder.skip(state)
                    selected_groups.append((group, frozenset(selection.target_ids)))

            gm = GridManager(project_path)
            pending: list[tuple[GridGeneration, str, list[str]]] = []

            for group, target_ids in selected_groups:
                # 超上限分组切为多张宫格逐张入队：每张的场景数与画格数一致
                # （末张不足一档时落小档 + 占位格），与预览、费用估算同源。
                # 空分组（``plan_grid_chunks`` 的唯一空产出）自然跳过循环体。
                plans = plan_grid_chunks(group, aspect_ratio, allow_large_grid=allow_large_grid)
                # 与 WebUI 入队路径同源：重生成该组时清理旧的已完成记录（同脚本
                # 同集、scene_ids 是当前组子集、非在途），前端列表只显示新一代
                # 宫格图。规则唯一定义在 GridManager.cleanup_superseded。
                #
                # 清理范围只能是「这次真的会出新图」的块，不能是整组：超上限分组会
                # 切成多张宫格，某一张可能整张都落在已复用成员上（该块没有缺口 ID，
                # 下面的循环会直接 continue、不生成替代品）。若仍按整组 ID 清理，会
                # 删掉这类块对应的旧记录却不产出新图，产物与 Manifest 记账双双丢失。
                generated_ids: set[str] = set()
                for chunk, _layout in plans:
                    chunk_ids = {item[id_field] for item in chunk}
                    if chunk_ids & target_ids:
                        generated_ids |= chunk_ids
                if generated_ids:
                    gm.cleanup_superseded(script_filename, episode, generated_ids)
                for chunk, layout in plans:
                    chunk_ids = [item[id_field] for item in chunk]
                    # 该张宫格覆盖的场景里，只有落在缺口集合内的才是这次要落格/报告
                    # 的目标；组内混入的可复用成员已在选择阶段记过 skip，这里绝不能
                    # 再碰它们（落格会覆盖旧图，重复记账会撞上 builder 的去重断言）。
                    report_ids = [cid for cid in chunk_ids if cid in target_ids]
                    if not report_ids:
                        # 超上限分组切成多张宫格时，某一张可能整张都落在已复用成员上
                        # （缺口全在另一张里）：没有要报告的目标，不必为它生成宫格。
                        continue
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
                    # 用 ``gm.delete`` 回收孤儿记录，并把该张记为 failed——前面已
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
                        _fail_scenes(
                            builder,
                            report_ids,
                            problem=GenerationProblem(
                                code=GenerationProblemCode.ENQUEUE_FAILED,
                                detail=f"{chunk_ids[0]}..{chunk_ids[-1]} 的宫格入队失败: {exc}",
                                action=GenerationAction.RETRY,
                                params={"grid_id": grid.id},
                            ),
                            episode=episode,
                            resolver=resolver,
                            task_state=GenerationTaskState.NOT_QUEUED,
                            artifact_paths=scene_artifact_paths,
                        )
                        continue
                    pending.append((grid, enqueue_result["task_id"], report_ids))

            if pending:
                # Wait for all queued grids concurrently — image worker channel can run
                # multiple in parallel, so serial wait_for_task would mask that throughput.
                results = await asyncio.gather(
                    *(wait_for_task(task_id) for _, task_id, _ in pending),
                    return_exceptions=True,
                )
                for (grid, task_id, report_ids), result in zip(pending, results, strict=True):
                    if isinstance(result, BaseException):
                        # 直连 wait_for_task（不经 batch_enqueue_and_wait）同样要把「等待被
                        # 打断」与「provider 判定失败」分开：宫格任务此时可能仍在跑，报
                        # 终态失败会诱导重试、造成重复付费提交。
                        interrupted = is_interrupted_wait_error(result)
                        _fail_scenes(
                            builder,
                            report_ids,
                            problem=problem_from_task_failure(str(result), interrupted=interrupted),
                            episode=episode,
                            resolver=resolver,
                            task_id=task_id,
                            task_state=(GenerationTaskState.INTERRUPTED if interrupted else GenerationTaskState.FAILED),
                            artifact_paths=scene_artifact_paths,
                        )
                        continue
                    status_value = result.get("status")
                    if status_value != "succeeded":
                        _fail_scenes(
                            builder,
                            report_ids,
                            problem=problem_from_task_failure(
                                result.get("error_message"),
                                cancelled=status_value == "cancelled",
                            ),
                            episode=episode,
                            resolver=resolver,
                            task_id=task_id,
                            task_state=(
                                GenerationTaskState.CANCELLED
                                if status_value == "cancelled"
                                else GenerationTaskState.FAILED
                            ),
                            provider_checkpoint=provider_checkpoint_from_task(result),
                            artifact_paths=scene_artifact_paths,
                        )
                        continue
                    # 生成任务只产出联合图；分镜格落盘由编排方在此显式调用切分补上，
                    # 端到端语义（分镜格齐备）不变。重载记录取 worker 回填的最新状态。
                    # ``only_scene_ids`` 把落格限定在这次调用的缺口目标：组内混入的
                    # 可复用成员即便也在联合图里，也不被这次切分覆写。
                    try:
                        reloaded = gm.get(grid.id)
                        if reloaded is None:
                            raise RuntimeError(f"grid record missing after generation: {grid.id}")
                        with project_change_source("worker"):
                            split_result = await apply_grid_split(
                                ctx.project_name, reloaded, only_scene_ids=frozenset(report_ids)
                            )
                    except Exception as exc:  # noqa: BLE001
                        # 联合图已生成成功，仅落格失败：独立问题码把它与生成失败区分开，
                        # 可在 WebUI 宫格面板重试切分（无需重新生成、不重复计费）。钱已花
                        # 在联合图上，所以每格都是 failed + task_state=succeeded。action 不
                        # 能用 RETRY——消费者按 action 派发时会理解成重跑 generate_grid，
                        # 那会重新生成联合图并重复计费；这条恢复路径只在宫格面板内可执行，
                        # 不是本工具能派发的下一步，用 NONE 如实表达。
                        _fail_scenes(
                            builder,
                            report_ids,
                            problem=GenerationProblem(
                                code=GenerationProblemCode.POST_PROCESSING_FAILED,
                                detail=f"联合图已生成，但切分落格失败（可在宫格面板重试切分，不要重新生成）: {exc}",
                                action=GenerationAction.NONE,
                                params={"grid_id": grid.id},
                            ),
                            episode=episode,
                            resolver=resolver,
                            task_id=task_id,
                            task_state=GenerationTaskState.SUCCEEDED,
                            provider_checkpoint=provider_checkpoint_from_task(result),
                            artifact_paths=scene_artifact_paths,
                        )
                        continue
                    # 逐格投影：落到盘上的格才算这一场景成功；联合图成功但某格没落盘
                    # （该分镜已不在剧本里）是这一个场景自己的失败，不牵连同组其他场景。
                    cut = set(split_result.updated_scene_ids)
                    for scene_id in report_ids:
                        scene_key = _scene_artifact_key(episode, scene_id)
                        if scene_id not in cut:
                            builder.fail(
                                scene_id,
                                problem=GenerationProblem(
                                    code=GenerationProblemCode.POST_PROCESSING_FAILED,
                                    detail=f"联合图已生成，但分镜 {scene_id} 未落格（已不在剧本中）",
                                    action=GenerationAction.FIX_INPUT,
                                    params={"grid_id": grid.id},
                                ),
                                artifact_key=scene_key,
                                task_id=task_id,
                                task_state=GenerationTaskState.SUCCEEDED,
                                provider_checkpoint=provider_checkpoint_from_task(result),
                            )
                            continue
                        cell_path = resource_relative_path("storyboards", scene_id)
                        cell_status, _blocker = observe_artifact_status(
                            resolver=resolver,
                            key=scene_key,
                            artifact_path=cell_path,
                        )
                        builder.succeed(
                            scene_id,
                            artifact_key=scene_key,
                            artifact_path=cell_path,
                            task_id=task_id,
                            artifact_status=cell_status,
                            provider_checkpoint=provider_checkpoint_from_task(result),
                        )
                    line = f"已切分 {len(split_result.updated_scene_ids)} 格"
                    if split_result.missing_scene_ids:
                        line += f"，跳过已不在剧本的分镜: {split_result.missing_scene_ids}"
                    log.append(f"  {grid.id}: {line}")

            return generation_result_response(builder.build(), log)
        except Exception as exc:  # noqa: BLE001
            return tool_error(_OPERATION, exc)

    return _handler


__all__ = ["generate_grid_tool"]
