"""Host-neutral tools for grid storyboards: submit composites, then split them on consent.

``generate_grid`` only produces grid composites. Results are addressed by **scene**
ID even though several scenes share one grid: the caller requests scenes, and one
grid's enqueue, task or reuse outcome is projected onto every scene it reports.
A succeeded scene points at its grid composite, not at a storyboard image — the
storyboards are written by ``split_grids``, which the agent calls only after the
user reviewed the composite and agreed to split it.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
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
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import BatchTaskResult, TaskSpec
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
from lib.infra.async_thread import run_sync_transaction
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
    grid_is_in_flight,
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
    """Relay grid-gate domain exceptions to the Agent in the default language, retaining their i18n keys as problem codes."""
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
    """Report a grid failure for every reported storyboard, because callers select storyboards rather than grids.

    ``artifact_paths`` contains old storyboard paths registered in the script. Failures leave old artifacts untouched but must
    report their paths and states, distinguishing failed replacement with an existing image from no reusable artifact in the first place."""

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
    """Render a group’s grid plan as preview text; retain the original format for one image and show the count for multiple images."""
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
    """``list_only`` preview: render the same plan that submission would execute."""
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

    script: ScriptFilename = Field(description="Script filename only (no directory), for example episode_1.json")
    scene_ids: RequestedIds | SkipJsonSchema[None] = Field(
        default=None,
        description="Regenerate grids containing these storyboards; when omitted, generate only for groups still missing storyboard images",
    )
    list_only: bool = Field(
        default=False,
        description=(
            "When true, preview planning only (each grid's tier, existing grid_id and state, and action for this call), without enqueueing or charges; return immediately with no batch to poll"
        ),
    )


@dataclass(frozen=True, slots=True)
class GridPlanPreview:
    """``list_only`` planning preview: shares the submission plan’s source but is not a generation result."""

    plan: str


type GridToolValue = GenerationToolValue | GridPlanPreview


def grid_structured(value: GridToolValue) -> dict[str, Any]:
    """Place previews under the tool name; otherwise use the same shape as generation tools."""
    if isinstance(value, GridPlanPreview):
        return {_OPERATION: value.plan}
    return generation_structured(value)


def grid_summary(value: GridToolValue) -> str | None:
    return None if isinstance(value, GridPlanPreview) else generation_summary(value)


def grid_is_error(value: GridToolValue) -> bool:
    return False if isinstance(value, GridPlanPreview) else generation_is_error(value)


async def _large_grid_allowed(capabilities: ConfigResolver, project: dict[str, Any]) -> bool:
    """Gate grid tiers using the session capability resolver’s image-resolution tier for the project T2I slot, sharing resolution with routes and cost estimation."""
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

        def load_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
            return (
                services.projects.load_project(project_name),
                services.projects.load_script(project_name, script_filename),
            )

        project, script = await run_sync_transaction(load_inputs)
        # Gate ``list_only`` like generation: a successful preview for a project without grids would falsely suggest
        # this tool applies to the current project.
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
                project_path=await asyncio.to_thread(services.projects.get_project_path, project_name),
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


@dataclass(slots=True)
class _CommittedSubmission:
    """A batch with persisted grid records, ready to enqueue."""

    project_path: Path
    builder: GenerationResultBuilder
    log: list[str]
    unsplit_ids: list[str]
    specs: list[TaskSpec]
    states: dict[str, GenerationTargetState]
    report_ids_by_grid: dict[str, tuple[str, ...]]
    grid_id_by_result: dict[str, str]
    reused_grid_ids: set[str]


def _commit_submission(
    plan: GridSubmissionPlan, project_name: str, services: Services, caller_source: str
) -> _CommittedSubmission | ToolOutcome[GridToolValue]:
    """Synchronous phase: observe artifact currency; return whole-batch blocking decisions directly, otherwise persist grid records and prepare tasks.

    Project-directory reads, hashing and grid-record writes use synchronous file I/O, so run the whole phase in a worker thread."""

    episode = plan.episode
    project_path = services.projects.get_project_path(project_name)
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
            prior_artifact_key = _scene_artifact_key(episode, scene_id)
            prior_artifact_path = plan.storyboard_paths.get(scene_id)
            prior_artifact_status, _blocker = observe_artifact_status(
                resolver=resolver,
                key=prior_artifact_key,
                artifact_path=prior_artifact_path,
            )
            states[scene_id] = GenerationTargetState(
                candidate=GenerationCandidate(
                    unit_id=scene_id,
                    artifact_key=grid_artifact_key(episode, grid_id),
                    artifact_path=grid_artifact_path(grid_id),
                ),
                prior_artifact_key=prior_artifact_key,
                prior_artifact_path=prior_artifact_path,
                prior_artifact_status=prior_artifact_status,
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
                source=caller_source,
                unit_id=report_ids[0],
                batch_unit_ids=report_ids,
            )
        )

    return _CommittedSubmission(
        project_path=project_path,
        builder=builder,
        log=log,
        unsplit_ids=unsplit_ids,
        specs=specs,
        states=states,
        report_ids_by_grid=report_ids_by_grid,
        grid_id_by_result=grid_id_by_result,
        reused_grid_ids=reused_grid_ids,
    )


def _collect_results(
    plan: GridSubmissionPlan,
    committed: _CommittedSubmission,
    successes: Sequence[BatchTaskResult],
    failures: Sequence[BatchTaskResult],
    batch_id: str,
) -> ToolOutcome[GridToolValue]:
    """Synchronous phase: reassess currency using post-generation grid records and summarize per-storyboard results."""

    episode = plan.episode
    builder = committed.builder
    log = committed.log
    unsplit_ids = committed.unsplit_ids
    report_ids_by_grid = committed.report_ids_by_grid
    grid_id_by_result = committed.grid_id_by_result
    reused_grid_ids = committed.reused_grid_ids
    # The resolver plans target state from grid records at first comparison and never rereads; unsplit-grid observation above used it already,
    # so evaluate this batch with a new resolver planned from post-generation records.
    resolver = active_artifact_currency_resolver(committed.project_path, plan.project)
    ready: list[str] = []
    for result in [*successes, *failures]:
        grid_id = grid_id_by_result[result.resource_id]
        report_ids = report_ids_by_grid[grid_id]
        # Composite generation is one operation; warnings such as reference truncation apply to the whole grid and are copied to every cell.
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
        batch_id=batch_id,
        grid_ids_awaiting_split=awaiting_split,
    )


async def _submit(
    plan: GridSubmissionPlan,
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[GridToolValue]:
    # Persisting grid records writes formal files: settle started thread transactions before responding to cancellation.
    committed = await run_sync_transaction(_commit_submission, plan, scope.project_name, services, caller.source)
    if isinstance(committed, ToolOutcome):
        return committed

    submitted = await submit_media_generation(
        scope=scope,
        # Leave the submission critical section once enqueueing completes, allowing other project submissions while composites generate.
        caller=caller.waiting_with(on_enqueued=plan.section.end),
        services=services,
        operation=_OPERATION,
        preflight=committed.builder.build(),
        pending_ids=[scene_id for ids in committed.report_ids_by_grid.values() for scene_id in ids],
        specs=committed.specs,
        states=committed.states,
    )
    if submitted.successes is None or submitted.failures is None:
        return generation_batch_submission_outcome(submitted.batch)
    return await asyncio.to_thread(
        _collect_results, plan, committed, submitted.successes, submitted.failures, submitted.batch.batch_id
    )


def _report_in_flight_in_refused_batch(
    builder: GenerationResultBuilder,
    grid: GridGeneration,
    report_ids: Sequence[str],
    episode: int,
    resolver: ArtifactCurrencyResolver,
) -> None:
    """When the batch is blocked, generating grids still finish normally: report wait-for-task outcomes per storyboard instead of omitting them."""

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

    grid_ids: RequestedIds = Field(
        description="Grid IDs to split into storyboard images (for example grid_a1b2c3d4e5f6)"
    )


async def split_grids(
    request: ToolRequest[SplitGridsRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    try:
        grid_ids = normalize_requested_ids(request.value.grid_ids, field="grid_ids") or []
        project = await asyncio.to_thread(services.projects.load_project, scope.project_name)
        try:
            ensure_grid_writable(project)
        except ApiError as exc:
            return _gate_problem(exc)

        def open_grids() -> GridManager:
            return GridManager(services.projects.get_project_path(scope.project_name))

        gm = await asyncio.to_thread(open_grids)
        # Split images sequentially: each commits under the project metadata lock, so concurrency is no faster.
        results = [
            await _split_one(
                scope.project_name,
                gm,
                grid_id,
                queue=services.queue,
                user_id=_caller.user_id,
            )
            for grid_id in grid_ids
        ]
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


async def _split_one(
    project_name: str,
    gm: GridManager,
    grid_id: str,
    *,
    queue: GenerationQueue,
    user_id: str,
) -> dict[str, Any]:
    async with grid_submission_section(project_name):
        try:
            grid = await asyncio.to_thread(gm.get, grid_id)
        except Exception as exc:
            # Invalid IDs count as missing; unreadable records (corrupt JSON, missing fields) fail only that grid while the rest split normally.
            if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
                grid = None
            else:
                logger.exception("宫格记录读取失败: grid_id=%s", grid_id)
                return {"grid_id": grid_id, "status": "failed", "detail": "宫格记录无法读取，分镜图未改动"}
        if grid is None:
            return {"grid_id": grid_id, "status": "not_found", "detail": "宫格不存在"}
        if await grid_is_in_flight(grid, queue=queue, project_name=project_name, user_id=user_id):
            return {
                "grid_id": grid_id,
                "status": "in_progress",
                "detail": "联合图仍在生成，等它完成并经用户审阅后再切分",
            }
        try:
            with project_change_source("worker"):
                split = await apply_grid_split(project_name, grid)
        except GridImageNotReadyError:
            return {"grid_id": grid_id, "status": "not_ready", "detail": "尚无可用的联合图，先生成或在面板上传"}
        except Exception:
            # One failure must not hide other split results: roll back each failed grid atomically and report per grid.
            logger.exception("宫格切分落格失败: grid_id=%s", grid_id)
            return {
                "grid_id": grid_id,
                "status": "failed",
                "detail": "切分落格失败，分镜图未改动；可在宫格面板重试切分",
            }
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
