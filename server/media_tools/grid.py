"""Host-neutral tools for grid storyboards: submit composites, then split them on consent.

``generate_grid`` only produces grid composites. Results are addressed by **scene**
ID even though several scenes share one grid: the caller requests scenes, and one
grid's enqueue, task or reuse outcome is projected onto every scene it reports.
A succeeded scene points at its grid composite, not at a storyboard image — the
storyboards are written by ``split_grids``, which the agent calls only after the
user reviewed the composite and agreed to split it.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_artifact_episode,
)
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.config.resolver import ConfigResolver
from lib.generation.generation_queue_client import TaskSpec
from lib.generation.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationTargetState,
    GenerationTaskState,
    GenerationWarning,
    ProviderCheckpoint,
    generation_warnings_from_result,
    normalize_requested_ids,
    observe_artifact_status,
    problem_from_task_failure,
    provider_checkpoint_from_task,
)
from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as translate
from lib.infra.api_errors import ApiError
from lib.project.project_change_hints import project_change_source
from lib.script.grid.grid_access import ensure_grid_writable
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.grid_resolution import resolve_image_resolution
from lib.script.grid.layout import GridLayout, large_grid_allowed
from lib.script.grid.models import GridGeneration
from server.media_tools.context import (
    GenerationToolValue,
    RequestedIds,
    ScriptFilename,
    generation_batch_submission_outcome,
    generation_is_error,
    generation_result_outcome,
    generation_structured,
    generation_summary,
    tool_error,
    tool_problem,
)
from server.services.grid.grid_split import GridImageNotReadyError, apply_grid_split
from server.services.grid.grid_submission import (
    GRID_IN_FLIGHT_STATUSES,
    GridChunkAction,
    GridChunkPlan,
    GridSubmissionPlan,
    commit_grid_submission,
    ensure_grid_submittable,
    grid_artifact_key,
    grid_artifact_path,
    grid_submission_section,
    plan_grid_submission,
    queue_active_grid_tasks,
)
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest, submit_media_generation

logger = logging.getLogger(__name__)

_OPERATION = "generate_grid"
_SPLIT_OPERATION = "split_grids"

_SPLIT_CONSENT_HINT = "请用户在宫格面板审阅联合图；用户明确同意切分后，再以这些 grid_id 调用 split_grids 切分落格"


def _gate_problem(exc: ApiError) -> ToolOutcome[Any]:
    """宫格闸门的领域异常按默认语言转述给 Agent，问题码沿用异常的 i18n key。"""
    return tool_problem(translate(exc.key, DEFAULT_LOCALE, **exc.params), code=exc.key)


def _scene_artifact_key(episode: int, scene_id: str) -> ArtifactKey:
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
    warnings: Sequence[GenerationWarning] = (),
) -> None:
    """一张宫格的失败落到它报告的每个分镜：调用方点的是分镜，不是宫格。

    ``artifact_paths`` 是剧本里已登记的旧分镜图路径。失败不动旧产物，但报告要带上它的
    路径与状态，否则下游分不清「替换失败、旧图还在」和「原本就没有可复用产物」。
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
            warnings=warnings,
        )


def _describe_layouts(layouts: Sequence[GridLayout]) -> str:
    """把一组的宫格规划渲染为预览文案；单张沿用原格式，多张标注张数。"""
    parts = [f"{layout.grid_size} ({layout.rows}×{layout.cols})" for layout in layouts]
    if len(parts) == 1:
        return parts[0]
    return f"{len(parts)} 张宫格: " + " + ".join(parts)


def _record_label(grid: GridGeneration) -> str:
    if grid.status in GRID_IN_FLIGHT_STATUSES:
        return f"{grid.id} 生成中"
    if grid.status == "failed":
        return f"{grid.id} 生成失败"
    return f"{grid.id} {'已切分' if grid.split_at else '联合图已就绪、未切分'}"


_ACTION_LABELS = {
    GridChunkAction.GENERATE: "本次将生成",
    GridChunkAction.IN_FLIGHT: "正在生成，本次沿用、不重复提交",
    GridChunkAction.UNSPLIT: "等待切分落格，本次不重生成",
    GridChunkAction.SKIPPED: "本次无需生成",
    GridChunkAction.BLOCKED: "受阻",
}


def _chunk_line(chunk: GridChunkPlan) -> str:
    record = f"；记录 {_record_label(chunk.grid)}" if chunk.grid is not None else ""
    ids = chunk.scene_ids
    return f"    · {chunk.layout.grid_size} {ids[0]}..{ids[-1]}：{_ACTION_LABELS[chunk.action]}{record}"


def _render_plan(plan: GridSubmissionPlan) -> str:
    """``list_only`` 预览：渲染的就是提交会执行的那份规划。"""
    by_group: dict[int, list[GridChunkPlan]] = {}
    for chunk in plan.chunks:
        by_group.setdefault(chunk.group_index, []).append(chunk)
    lines = [f"共 {len(by_group)} 个分组："]
    for position, chunks in enumerate(by_group.values(), start=1):
        ids = [scene_id for chunk in chunks for scene_id in chunk.scene_ids]
        layouts = [chunk.layout for chunk in chunks]
        lines.append(f"  组 {position}: {ids[0]}..{ids[-1]} ({len(ids)} 分镜) → {_describe_layouts(layouts)}")
        lines.extend(_chunk_line(chunk) for chunk in chunks)
    if plan.refused:
        lines.append("按当前状态提交会整批受阻、不建任何任务：")
        lines.extend(f"  - {b.scene_id}：{b.problem.code} {b.problem.detail}" for b in plan.blocked)
    if plan.unsplit:
        lines.append(_SPLIT_CONSENT_HINT + "：" + "、".join(c.grid.id for c in plan.unsplit if c.grid is not None))
    return "\n".join(lines)


class GenerateGridRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: ScriptFilename = Field(description="剧本纯文件名（不含目录），如 episode_1.json")
    scene_ids: RequestedIds | SkipJsonSchema[None] = Field(
        default=None,
        description="重生成包含这些分镜的宫格；省略则只为仍缺分镜图的分组出图",
    )
    list_only: bool = Field(
        default=False,
        description=(
            "true 时只预览规划（各张宫格的档位、现有记录 grid_id 与状态、本次动作），不入队、不产生费用；"
            "预览立即返回，没有批次可轮询"
        ),
    )


@dataclass(frozen=True, slots=True)
class GridPlanPreview:
    """``list_only`` 的规划预览：与提交会执行的规划同源，但不是生成结果。"""

    plan: str


type GridToolValue = GenerationToolValue | GridPlanPreview


def grid_structured(value: GridToolValue) -> dict[str, Any]:
    """预览放在工具名下；其余与生成类工具同形。"""
    if isinstance(value, GridPlanPreview):
        return {_OPERATION: value.plan}
    return generation_structured(value)


def grid_summary(value: GridToolValue) -> str | None:
    return None if isinstance(value, GridPlanPreview) else generation_summary(value)


def grid_is_error(value: GridToolValue) -> bool:
    return False if isinstance(value, GridPlanPreview) else generation_is_error(value)


async def _large_grid_allowed(capabilities: ConfigResolver, project: dict[str, Any]) -> bool:
    """宫格档位门控：按会话能力解析器取项目 T2I 槽的图像分辨率档，与路由、费用估算同一份解析。"""
    return large_grid_allowed(await resolve_image_resolution(capabilities, project))


async def generate_grid(
    request: ToolRequest[GenerateGridRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[GridToolValue]:
    try:
        script_filename = request.value.script
        scene_ids = normalize_requested_ids(request.value.scene_ids, field="scene_ids")
        project_name = scope.project_name

        project = services.projects.load_project(project_name)
        script = services.projects.load_script(project_name, script_filename)
        # ``list_only`` 与生成同样先过闸门：未开宫格的项目靠预览拿到成功响应，调用方会误以为
        # 该工具适用于当前项目。
        try:
            ensure_grid_submittable(project, script)
        except ApiError as exc:
            return _gate_problem(exc)
        episode = resolve_artifact_episode(
            project=project,
            script=script,
            script_filename=script_filename,
        )
        async with grid_submission_section(project_name) as section:
            plan = await plan_grid_submission(
                project=project,
                project_path=services.projects.get_project_path(project_name),
                script=script,
                script_file=script_filename,
                episode=episode,
                scene_ids=scene_ids,
                section=section,
                active_grid_tasks=queue_active_grid_tasks(
                    services.queue,
                    project_name=project_name,
                    script_file=script_filename,
                    user_id=caller.user_id,
                ),
                large_grid_gate=functools.partial(_large_grid_allowed, services.capabilities),
            )
            if request.value.list_only:
                return ToolOutcome(value=GridPlanPreview(_render_plan(plan)))
            return await _submit(plan, scope, caller, services)
    except Exception as exc:
        return tool_error(_OPERATION, exc)


async def _submit(
    plan: GridSubmissionPlan,
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[GridToolValue]:
    episode = plan.episode
    project_path = services.projects.get_project_path(scope.project_name)
    resolver = active_artifact_currency_resolver(project_path, plan.project)
    builder = GenerationResultBuilder(_OPERATION, plan.selection)
    log: list[str] = []
    for state in plan.skipped:
        builder.skip(state)
    unsplit_ids: list[str] = []
    for chunk in plan.unsplit:
        grid = chunk.grid
        if grid is None:
            continue
        unsplit_ids.append(grid.id)
        key = grid_artifact_key(episode, grid.id)
        path = grid_artifact_path(grid.id)
        status, _blocker = observe_artifact_status(resolver=resolver, key=key, artifact_path=path)
        for scene_id in chunk.report_ids:
            builder.skip_unit(scene_id, artifact_key=key, artifact_path=path, artifact_status=status)
        log.append(f"宫格 {grid.id}（{'、'.join(chunk.report_ids)}）联合图已就绪、未切分，本次不重生成")

    if plan.refused:
        for blocked in (*plan.blocked, *plan.withheld):
            builder.block(
                blocked.scene_id,
                problem=blocked.problem,
                artifact_key=blocked.artifact_key,
                artifact_path=blocked.artifact_path,
                artifact_status=blocked.artifact_status,
            )
        for chunk in plan.chunks:
            if chunk.action is not GridChunkAction.IN_FLIGHT or chunk.grid is None:
                continue
            _report_in_flight_in_refused_batch(builder, chunk.grid, chunk.report_ids, episode, resolver)
            log.append(f"宫格 {chunk.grid.id}（{'、'.join(chunk.report_ids)}）已在生成中，不受本次受阻影响")
        log.append("本次请求整批受阻，未创建任何宫格任务；修复全部缺口后重试即可一次性提交。")
        if unsplit_ids:
            log.append(_SPLIT_CONSENT_HINT + "：" + "、".join(unsplit_ids))
        return generation_result_outcome(builder.build(), log, grid_ids_awaiting_split=unsplit_ids)

    submissions = commit_grid_submission(plan, project_path)
    specs: list[TaskSpec] = []
    states: dict[str, GenerationTargetState] = {}
    report_ids_by_grid: dict[str, tuple[str, ...]] = {}
    grid_id_by_result: dict[str, str] = {}
    reused_grid_ids = {submission.grid.id for submission in submissions if submission.reused}
    for submission in submissions:
        grid_id = submission.grid.id
        report_ids = submission.report_ids
        for scene_id in report_ids:
            states[scene_id] = GenerationTargetState(
                candidate=GenerationCandidate(
                    unit_id=scene_id,
                    artifact_key=grid_artifact_key(episode, grid_id),
                    artifact_path=grid_artifact_path(grid_id),
                )
            )
        report_ids_by_grid[grid_id] = report_ids
        grid_id_by_result[grid_id] = grid_id
        grid_id_by_result[report_ids[0]] = grid_id
        specs.append(
            TaskSpec(
                task_type="grid",
                media_type="image",
                resource_id=grid_id,
                payload=submission.payload,
                script_file=plan.script_file,
                source=caller.source,
                unit_id=report_ids[0],
                batch_unit_ids=report_ids,
            )
        )

    submitted = await submit_media_generation(
        scope=scope,
        caller=caller,
        services=services,
        operation=_OPERATION,
        preflight=builder.build(),
        pending_ids=[scene_id for ids in report_ids_by_grid.values() for scene_id in ids],
        specs=specs,
        states=states,
        # 入队完成即离开提交临界区：等联合图生成期间，同一项目的其他提交照常进行
        embedded_waiter=(
            functools.partial(caller.batch_waiter, on_enqueued=plan.section.end)
            if caller.batch_waiter is not None
            else None
        ),
    )
    if submitted.successes is None or submitted.failures is None:
        return generation_batch_submission_outcome(submitted.batch)

    # resolver 首次比较时按当时的宫格记录规划目标态、此后不再重读；上面观测未切分宫格时已用过它，
    # 本批出图结果须换一个按出图后记录规划的 resolver 判定
    resolver = active_artifact_currency_resolver(project_path, plan.project)
    ready: list[str] = []
    for result in [*submitted.successes, *submitted.failures]:
        grid_id = grid_id_by_result[result.resource_id]
        report_ids = report_ids_by_grid[grid_id]
        # 联合图一次生成，参考图裁剪之类的 warning 属于整张宫格：逐格照录
        grid_warnings = generation_warnings_from_result(result.result)
        if result.status != "succeeded":
            _fail_scenes(
                builder,
                report_ids,
                problem=problem_from_task_failure(
                    result.error,
                    cancelled=result.status == "cancelled",
                    interrupted=result.status == "interrupted",
                ),
                episode=episode,
                resolver=resolver,
                task_id=result.task_id or None,
                task_state=(
                    GenerationTaskState.INTERRUPTED
                    if result.status == "interrupted"
                    else GenerationTaskState.CANCELLED
                    if result.status == "cancelled"
                    else GenerationTaskState.FAILED
                ),
                artifact_paths=plan.storyboard_paths,
                warnings=grid_warnings,
            )
            continue
        key = grid_artifact_key(episode, grid_id)
        path = grid_artifact_path(grid_id)
        status, _blocker = observe_artifact_status(resolver=resolver, key=key, artifact_path=path)
        for scene_id in report_ids:
            builder.succeed(
                scene_id,
                artifact_key=key,
                artifact_path=path,
                task_id=result.task_id,
                artifact_status=status,
                provider_checkpoint=provider_checkpoint_from_task(result.task or {}),
                warnings=grid_warnings,
            )
        ready.append(grid_id)
        reused_note = "沿用已在生成中的任务（未重复提交），" if grid_id in reused_grid_ids else ""
        log.append(f"宫格 {grid_id}（{'、'.join(report_ids)}）{reused_note}联合图已就绪、未切分")
    awaiting_split = [*ready, *unsplit_ids]
    if awaiting_split:
        log.append(_SPLIT_CONSENT_HINT + "：" + "、".join(awaiting_split))
    return generation_result_outcome(
        builder.build(),
        log,
        batch_id=submitted.batch.batch_id,
        grid_ids_awaiting_split=awaiting_split,
    )


def _report_in_flight_in_refused_batch(
    builder: GenerationResultBuilder,
    grid: GridGeneration,
    report_ids: Sequence[str],
    episode: int,
    resolver: ArtifactCurrencyResolver,
) -> None:
    """整批受阻时，已在生成中的宫格照常跑完：逐分镜给「等在途任务」的结论，不让它们从结果里消失。"""

    key = grid_artifact_key(episode, grid.id)
    path = grid_artifact_path(grid.id)
    status, _blocker = observe_artifact_status(resolver=resolver, key=key, artifact_path=path)
    problem = GenerationProblem(
        code=GenerationProblemCode.ACTIVE_TASK_CONFLICT,
        detail=f"宫格 {grid.id} 已在生成中，本次整批受阻未提交它，它照常跑完；完成后请用户审阅联合图",
        action=GenerationAction.WAIT_FOR_TASK,
        params={"grid_ids": [grid.id]},
    )
    for scene_id in report_ids:
        builder.block(scene_id, problem=problem, artifact_key=key, artifact_path=path, artifact_status=status)


class SplitGridsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grid_ids: RequestedIds = Field(description="要切分落格的宫格 ID（如 grid_a1b2c3d4e5f6）")


async def split_grids(
    request: ToolRequest[SplitGridsRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    del caller
    try:
        grid_ids = normalize_requested_ids(request.value.grid_ids, field="grid_ids") or []
        project = services.projects.load_project(scope.project_name)
        try:
            ensure_grid_writable(project)
        except ApiError as exc:
            return _gate_problem(exc)

        gm = GridManager(services.projects.get_project_path(scope.project_name))
        # 逐张顺序切分：每张都在项目元数据锁内提交，并发不会更快
        results = [await _split_one(scope.project_name, gm, grid_id) for grid_id in grid_ids]
        lines = [
            f"- {r['grid_id']}：已切分落格 {len(r['updated_scene_ids'])} 格"
            + (f"，剧本中已不存在而跳过 {'、'.join(r['missing_scene_ids'])}" if r["missing_scene_ids"] else "")
            if r["status"] == "split"
            else f"- {r['grid_id']}：未切分（{r['detail']}）"
            for r in results
        ]
        if all(r["status"] != "split" for r in results):
            return tool_problem("\n".join(["没有宫格被切分落格：", *lines]), params={"results": results})
        return ToolOutcome(value={"results": results, "summary": "\n".join(lines)})
    except Exception as exc:
        return tool_error(_SPLIT_OPERATION, exc)


async def _split_one(project_name: str, gm: GridManager, grid_id: str) -> dict[str, Any]:
    try:
        grid = gm.get(grid_id)
    except Exception as exc:
        # 非法 ID 视同不存在；JSON 损坏、缺字段等记录读不出来时只记这一张失败，同批其余宫格照常切分
        if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
            grid = None
        else:
            logger.exception("宫格记录读取失败: grid_id=%s", grid_id)
            return {"grid_id": grid_id, "status": "failed", "detail": "宫格记录无法读取，分镜图未改动"}
    if grid is None:
        return {"grid_id": grid_id, "status": "not_found", "detail": "宫格不存在"}
    if grid.status in GRID_IN_FLIGHT_STATUSES:
        return {"grid_id": grid_id, "status": "in_progress", "detail": "联合图仍在生成，等它完成并经用户审阅后再切分"}
    try:
        with project_change_source("worker"):
            split = await apply_grid_split(project_name, grid)
    except GridImageNotReadyError:
        return {"grid_id": grid_id, "status": "not_ready", "detail": "尚无可用的联合图，先生成或在面板上传"}
    except Exception:
        # 单张失败不吞掉同批已切分的结果：切分整张原子回滚，逐张报告
        logger.exception("宫格切分落格失败: grid_id=%s", grid_id)
        return {"grid_id": grid_id, "status": "failed", "detail": "切分落格失败，分镜图未改动；可在宫格面板重试切分"}
    return {
        "grid_id": grid_id,
        "status": "split",
        "updated_scene_ids": split.updated_scene_ids,
        "missing_scene_ids": split.missing_scene_ids,
    }


__all__ = [
    "GenerateGridRequest",
    "GridPlanPreview",
    "GridToolValue",
    "SplitGridsRequest",
    "generate_grid",
    "grid_is_error",
    "grid_structured",
    "grid_summary",
    "split_grids",
]
