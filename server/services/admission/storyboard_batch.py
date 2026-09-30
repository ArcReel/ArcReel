"""分镜图与分镜视频的集范围批量生成：Web 时间线 / 宫格入口与 Agent 的 ``generate_storyboards`` 共用的规划。

- **分镜图**：:func:`plan_storyboard_image_batch` 是 ``generate_storyboards`` 与 Web 批量入口的同一份
  规划。不点名时只选没有可用分镜图的分镜；缺提示词、引用不可用的分镜逐项拒绝，其余照常提交，
  相邻分镜在批内按参考链排队。
- **分镜视频**：:func:`load_storyboard_video_batch_plan` 选出本集没有可用视频的分镜，先把缺提示词、
  缺分镜图与已在生成中的分镜列为跳过项，其余分镜走 :func:`admit_storyboard_video_request` 整批
  准入：任一目标不满足时整批拒绝，一个任务也不建。

过期的分镜图与视频不进批量，由用户在分镜详情里逐条重生。规划只读：选目标、判准入、算预估费用
都不建任务；提交由调用方经批次入口完成。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    active_artifact_currency_resolver,
    resolve_artifact_episode,
    resolve_usable_storyboard_video_inputs,
)
from lib.artifacts.artifact_manifest import ArtifactKey
from lib.config.resolver import ConfigResolver
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.batch_admission import BatchAdmission
from lib.generation.generation_batch import GenerationBatchReadModel, build_generation_batch_admission
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import (
    BatchTaskResult,
    EnqueuedTask,
    TaskSpec,
    get_active_tasks_for_resources,
    submit_generation_batch,
)
from lib.generation.generation_result import (
    GenerationAction,
    GenerationBatchResult,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationResultBuilder,
    GenerationSelection,
    GenerationSelectionMode,
    GenerationTargetState,
    select_generation_targets,
)
from lib.generation.video_request_facts import VideoRequestCostFacts, VideoRequestFacts, VideoRequestFactsFailure
from lib.infra.schema_guards import is_int
from lib.project.project_manager import ProjectManager, find_episode, is_reference_video_project
from lib.prompts.prompt_builders import render_storyboard_image_prompt
from lib.references.reference_admission import admit_storyboard_item
from lib.references.reference_catalog import build_reference_catalog
from lib.script.script_models import get_generated_assets, resolve_content_mode
from lib.script.script_skeleton import ensure_route_skeleton, resolve_script_kind
from lib.script.storyboard_sequence import (
    StoryboardImageUnavailable,
    build_storyboard_dependency_plan,
    get_storyboard_items,
)
from server.services.admission.cost_estimation import ImageLane, quote_video_request_from_price
from server.services.admission.reference_admission import reference_admission_problems
from server.services.admission.video_batch_admission import (
    admit_storyboard_video_request,
    artifact_state_tickets,
    build_storyboard_video_specs,
    resolve_voice_context,
    screen_storyboard_items,
    storyboard_item_id,
    storyboard_video_request_facts,
    video_target_states,
)

STORYBOARD_BATCH_OPERATION = "generate_storyboards"
VIDEO_BATCH_OPERATION = "generate_videos"


class BatchSkipReason(StrEnum):
    """没有可用产物、却不进这一批的原因；确认框按它列出跳过项。"""

    MISSING_PROMPT = "missing_prompt"
    MISSING_STORYBOARD = "missing_storyboard"
    GENERATING = "generating"
    #: 引用未登记，或引用的资产还没有可用的资产图。
    REFERENCE_UNAVAILABLE = "reference_unavailable"
    #: 产物清单读不出，或条目本身不成立。
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class BatchSkip:
    unit_id: str
    reason: BatchSkipReason


class StoryboardBatchEpisodeNotFound(LookupError):
    """集范围指向的集不在分集账本里，或这一集还没有正式脚本。"""

    def __init__(self, episode_id: int) -> None:
        self.episode_id = episode_id
        super().__init__(f"episode {episode_id} has no formal script in this project")


class StoryboardBatchRouteMismatch(ValueError):
    """参考生视频项目没有分镜图这一步，分镜批量不适用。"""


@dataclass(frozen=True, slots=True)
class EpisodeStoryboardScript:
    """集范围批量读取的项目现状：项目、这一集的正式脚本及其产物集号。"""

    project: dict[str, Any]
    project_path: Path
    script: dict[str, Any]
    script_file: str
    #: 产物清单里这一集的集号。
    episode: int


def load_episode_storyboard_script(
    projects: ProjectManager, project_name: str, episode_id: int
) -> EpisodeStoryboardScript:
    """读取集范围批量的项目与正式脚本，并把守生成模式与剧本骨架。

    Raises:
        StoryboardBatchEpisodeNotFound: 集不存在，或还没有正式脚本。
        StoryboardBatchRouteMismatch: 项目走参考生视频。
        SkeletonRouteMismatchError: 剧本骨架与项目生成模式失配。
    """

    project = projects.load_project(project_name)
    if is_reference_video_project(project):
        raise StoryboardBatchRouteMismatch("参考生视频项目没有分镜图步骤")
    entry = find_episode(project, episode_id)
    recorded = entry.get("script_file") if entry is not None else None
    if not isinstance(recorded, str) or not recorded:
        raise StoryboardBatchEpisodeNotFound(episode_id)
    # 任务与去重键按剧本纯文件名记，与逐条生成入口同一写法；账本里记的是 ``scripts/`` 下的路径。
    script_file = recorded.removeprefix("scripts/")
    try:
        script = projects.load_script(project_name, script_file)
    except FileNotFoundError as exc:
        raise StoryboardBatchEpisodeNotFound(episode_id) from exc
    ensure_route_skeleton(script, resolve_content_mode(script, project), project.get("generation_mode"))
    episode = resolve_artifact_episode(project=project, script=script, script_filename=script_file) or episode_id
    return EpisodeStoryboardScript(
        project=project,
        project_path=projects.get_project_path(project_name),
        script=script,
        script_file=script_file,
        episode=episode,
    )


async def _active_resource_ids(
    queue: GenerationQueue | None,
    *,
    project_name: str,
    task_type: str,
    script_file: str,
    resource_ids: Sequence[str],
    user_id: str,
) -> frozenset[str]:
    if not resource_ids:
        return frozenset()
    tasks = await get_active_tasks_for_resources(
        project_name=project_name,
        task_type=task_type,
        resource_ids=list(resource_ids),
        script_file=script_file,
        user_id=user_id,
        queue=queue,
    )
    return frozenset(str(task["resource_id"]) for task in tasks if task.get("resource_id"))


# --- 分镜图 -------------------------------------------------------------------


def storyboard_image_prompt(segment: dict[str, Any], style: str, style_description: str, id_field: str) -> str:
    """一张分镜图实发的提示词；提示词缺失时抛 ``ValueError``。"""

    image_prompt = segment.get("image_prompt", "")
    if not image_prompt:
        raise ValueError(f"分镜 {segment[id_field]} 缺少 image_prompt 字段")
    return render_storyboard_image_prompt(image_prompt, style=style, style_description=style_description)


def _storyboard_image_lane(
    items: Sequence[dict[str, Any]], index: int, reference_fields: Sequence[str | None]
) -> ImageLane:
    """这张分镜图会走哪个生图通道：有参考图（资产图或上一张分镜图）就是图生图。

    与执行侧的装配同序判定：引用了商品、角色、场景或道具，或者接在上一张分镜图之后
    （不是首张、也不是章节切分点）。只用来报价，上一张分镜图最终不可用时执行侧会改走文生图。
    """

    item = items[index]
    if index > 0 and not item.get("segment_break"):
        return "i2i"
    for field_name in reference_fields:
        value = item.get(field_name) if field_name else None
        if isinstance(value, list) and any(isinstance(name, str) and name for name in value):
            return "i2i"
    return "t2i"


@dataclass(frozen=True, slots=True)
class StoryboardImageBatchPlan:
    """一次分镜图批量生成的规划：要提交的目标、逐项拒绝与复用、批内参考链。"""

    script_file: str
    items: Sequence[dict[str, Any]]
    id_field: str
    #: 要提交的目标，按剧本顺序。
    targets: tuple[GenerationTargetState, ...]
    preflight: GenerationBatchResult
    #: 每个目标的生图通道，供报价。
    lanes: Mapping[str, ImageLane]
    missing_prompt: frozenset[str] = frozenset()
    reference_blocked: frozenset[str] = frozenset()
    #: 目标里已有活动任务的分镜：提交时并入原任务，不再计费。
    generating: frozenset[str] = frozenset()

    @property
    def target_ids(self) -> list[str]:
        return [state.unit_id for state in self.targets]

    @property
    def states(self) -> dict[str, GenerationTargetState]:
        return {state.unit_id: state for state in self.targets}

    @property
    def new_task_ids(self) -> list[str]:
        """会新建付费任务的目标：扣掉已在生成中的。"""

        return [unit_id for unit_id in self.target_ids if unit_id not in self.generating]

    @property
    def skips(self) -> tuple[BatchSkip, ...]:
        """没有可用分镜图、这一批却不会新建任务的分镜及原因。"""

        skips = [
            BatchSkip(unit_id, BatchSkipReason.GENERATING) for unit_id in self.target_ids if unit_id in self.generating
        ]
        for item in self.preflight.items:
            if item.unit_id in self.missing_prompt:
                reason = BatchSkipReason.MISSING_PROMPT
            elif item.unit_id in self.reference_blocked:
                reason = BatchSkipReason.REFERENCE_UNAVAILABLE
            else:
                reason = BatchSkipReason.UNAVAILABLE
            skips.append(BatchSkip(item.unit_id, reason))
        return tuple(skips)

    def task_specs(self, *, source: str) -> list[TaskSpec]:
        items_by_id = {str(item[self.id_field]): item for item in self.items if item.get(self.id_field)}
        return [
            TaskSpec.from_request(
                task_type="storyboard",
                media_type="image",
                resource_id=plan.resource_id,
                prompt=items_by_id[plan.resource_id].get("image_prompt"),
                script_file=self.script_file,
                dependency_resource_id=plan.dependency_resource_id,
                dependency_group=plan.dependency_group,
                dependency_index=plan.dependency_index,
                unit_id=plan.resource_id,
                source=source,
            )
            for plan in build_storyboard_dependency_plan(self.items, self.id_field, self.target_ids, self.script_file)
        ]


def plan_storyboard_image_batch(
    *,
    project: Mapping[str, Any],
    script: dict[str, Any],
    script_file: str,
    episode: int,
    resolver: ArtifactCurrencyResolver,
    requested_ids: Sequence[str] | None = None,
) -> StoryboardImageBatchPlan:
    """选出这一批的分镜图目标并逐项判准入；``requested_ids`` 为 ``None`` 时只选缺分镜图的分镜。"""

    items, id_field, char_field, scene_field, prop_field = get_storyboard_items(script)
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
        requested_ids=requested_ids,
        resolver=resolver,
    )
    builder = GenerationResultBuilder.from_selection(STORYBOARD_BATCH_OPERATION, selection)

    style = project.get("style", "")
    style_description = project.get("style_description", "")
    catalog = build_reference_catalog(project)
    index_by_id = {str(item.get(id_field)): index for index, item in enumerate(items)}
    reference_fields = ("products_in_shot", char_field, scene_field, prop_field)
    targets: list[GenerationTargetState] = []
    missing_prompt: set[str] = set()
    reference_blocked: set[str] = set()
    for state in selection.targets:
        item = items_by_id[state.unit_id]
        # 结果集一个分镜只记一条问题：取首条（未登记排在无资产图之前——名字都没登记时
        # 「去生成资产图」指不出该对谁做），另一条在这条修完后的下一次调用里报出。
        reference_problem = next(
            iter(reference_admission_problems(admit_storyboard_item(catalog, item), unit_id=state.unit_id)),
            None,
        )
        problem = reference_problem
        if problem is not None:
            reference_blocked.add(state.unit_id)
        else:
            try:
                storyboard_image_prompt(item, style, style_description, id_field)
            except (KeyError, TypeError, ValueError) as exc:
                if not item.get("image_prompt"):
                    missing_prompt.add(state.unit_id)
                problem = GenerationProblem(
                    code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                    detail=str(exc),
                    action=GenerationAction.FIX_INPUT,
                )
        if problem is not None:
            builder.block(
                state.unit_id,
                problem=problem,
                artifact_key=state.artifact_key,
                artifact_path=state.artifact_path,
                artifact_status=state.status,
            )
            continue
        targets.append(state)

    return StoryboardImageBatchPlan(
        script_file=script_file,
        items=items,
        id_field=id_field,
        targets=tuple(targets),
        preflight=builder.build(),
        lanes={
            state.unit_id: _storyboard_image_lane(items, index_by_id[state.unit_id], reference_fields)
            for state in targets
        },
        missing_prompt=frozenset(missing_prompt),
        reference_blocked=frozenset(reference_blocked),
    )


async def load_storyboard_image_batch_plan(
    projects: ProjectManager,
    queue: GenerationQueue,
    *,
    project_name: str,
    episode_id: int,
    user_id: str,
) -> tuple[EpisodeStoryboardScript, StoryboardImageBatchPlan]:
    """读取这一集的现状并规划一批分镜图，标出其中已在生成中的目标。"""

    def _read() -> tuple[EpisodeStoryboardScript, StoryboardImageBatchPlan]:
        loaded = load_episode_storyboard_script(projects, project_name, episode_id)
        resolver = active_artifact_currency_resolver(loaded.project_path, loaded.project)
        plan = plan_storyboard_image_batch(
            project=loaded.project,
            script=loaded.script,
            script_file=loaded.script_file,
            episode=loaded.episode,
            resolver=resolver,
        )
        return loaded, plan

    loaded, plan = await asyncio.to_thread(_read)
    generating = await _active_resource_ids(
        queue,
        project_name=project_name,
        task_type="storyboard",
        script_file=loaded.script_file,
        resource_ids=plan.target_ids,
        user_id=user_id,
    )
    return loaded, replace(plan, generating=generating)


async def submit_storyboard_image_batch(
    plan: StoryboardImageBatchPlan,
    *,
    project_name: str,
    queue: GenerationQueue,
    source: str,
    user_id: str,
) -> tuple[GenerationBatchReadModel, list[EnqueuedTask], list[BatchTaskResult]]:
    """把一次分镜图规划作为一个持久批次提交，不等待结果。"""

    requested, blocked = build_generation_batch_admission(
        preflight=plan.preflight,
        pending_ids=plan.target_ids,
        states=plan.states,
    )
    return await submit_generation_batch(
        project_name=project_name,
        operation=STORYBOARD_BATCH_OPERATION,
        requested=requested,
        blocked=blocked,
        specs=plan.task_specs(source=source),
        source=source,
        user_id=user_id,
        queue=queue,
    )


# --- 分镜视频 -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoryboardVideoBatchPlan:
    """一次分镜视频批量生成的规划：跳过项、整批准入结论与准入通过后要入队的 spec。"""

    script_file: str
    selection: GenerationSelection
    states: Mapping[str, GenerationTargetState]
    skips: tuple[BatchSkip, ...]
    specs: tuple[TaskSpec, ...]
    admission: BatchAdmission
    video_request_facts: VideoRequestFacts | VideoRequestFactsFailure | None
    #: 每个目标剧本上的计划秒数；不是正整数时为 ``None``，这一批就报不出价。
    durations: Mapping[str, int | None] = field(default_factory=dict)

    @property
    def target_ids(self) -> list[str]:
        return [spec.resource_id for spec in self.specs]


def _video_skip_reason(
    item: dict[str, Any],
    *,
    unit_id: str,
    generating: frozenset[str],
    loaded: EpisodeStoryboardScript,
    resolver: ArtifactCurrencyResolver,
) -> BatchSkipReason | None:
    if unit_id in generating:
        return BatchSkipReason.GENERATING
    if not item.get("video_prompt"):
        return BatchSkipReason.MISSING_PROMPT
    try:
        resolve_usable_storyboard_video_inputs(
            project_path=loaded.project_path,
            project=loaded.project,
            episode=loaded.episode,
            resource_id=unit_id,
            item=item,
            resolver=resolver,
        )
    except StoryboardImageUnavailable:
        return BatchSkipReason.MISSING_STORYBOARD
    except (OSError, ValueError):
        # 尾帧等其余输入不可用由构造 spec 的那一步拒绝，整批准入如实报出。
        return None
    return None


async def load_storyboard_video_batch_plan(
    projects: ProjectManager,
    queue: GenerationQueue | None,
    *,
    project_name: str,
    episode_id: int,
    user_id: str,
    config_resolver: ConfigResolver | None = None,
    video_request_facts: VideoRequestFacts | VideoRequestFactsFailure | None = None,
) -> StoryboardVideoBatchPlan:
    """读取这一集的现状，选出没有可用视频的分镜，列出跳过项并对其余分镜判整批准入。

    ``video_request_facts`` 缺省时按项目当前配置求值；准入与报价读同一份。
    """

    loaded = await asyncio.to_thread(load_episode_storyboard_script, projects, project_name, episode_id)
    items, id_field, *_fields = get_storyboard_items(loaded.script)
    clean, screen_refused = screen_storyboard_items(items, id_field, requested_ids=None)
    resolver = await asyncio.to_thread(active_artifact_currency_resolver, loaded.project_path, loaded.project)
    states = video_target_states(clean, id_field, episode=loaded.episode, resolver=resolver)
    selection = select_generation_targets(
        candidates=[state.candidate for state in states.values()],
        requested_ids=None,
        resolver=resolver,
    )
    target_ids = set(selection.target_ids)
    pending = [item for item in clean if str(storyboard_item_id(item, id_field) or "") in target_ids]
    generating = await _active_resource_ids(
        queue,
        project_name=project_name,
        task_type="video",
        script_file=loaded.script_file,
        resource_ids=[str(storyboard_item_id(item, id_field)) for item in pending],
        user_id=user_id,
    )

    skips: list[BatchSkip] = []
    candidates: list[dict[str, Any]] = []
    for item in pending:
        unit_id = str(storyboard_item_id(item, id_field))
        reason = _video_skip_reason(item, unit_id=unit_id, generating=generating, loaded=loaded, resolver=resolver)
        if reason is None:
            candidates.append(item)
        else:
            skips.append(BatchSkip(unit_id, reason))

    content_mode = resolve_content_mode(loaded.script, loaded.project)
    specs, refused = build_storyboard_video_specs(
        items=candidates,
        id_field=id_field,
        content_mode=content_mode,
        skeleton_kind=resolve_script_kind(loaded.script),
        script_filename=loaded.script_file,
        project_dir=loaded.project_path,
        project=loaded.project,
        episode=loaded.episode,
        resolver=resolver,
        skip_ids=None,
        voice_characters=await resolve_voice_context(loaded.project, content_mode),
    )
    facts = (
        (video_request_facts or await storyboard_video_request_facts(loaded.project, config_resolver))
        if specs
        else None
    )
    admission = await admit_storyboard_video_request(
        project_name=project_name,
        project=loaded.project,
        script_file=loaded.script_file,
        items=candidates,
        id_field=id_field,
        specs=specs,
        operation=VIDEO_BATCH_OPERATION,
        selection=GenerationSelectionMode.MISSING_ONLY,
        # 产物清单读不出的分镜与剧本里的脏条目同属这次请求，一并参与整批准入：任一条受阻，整批都不入队。
        extra_tickets=[*artifact_state_tickets(selection.unavailable), *screen_refused, *refused],
        user_id=user_id,
        queue=queue,
        config_resolver=config_resolver,
        video_request_facts=facts,
    )
    items_by_id = {str(storyboard_item_id(item, id_field)): item for item in candidates}
    durations: dict[str, int | None] = {}
    for spec in specs:
        duration = items_by_id[spec.resource_id].get("duration_seconds")
        durations[spec.resource_id] = duration if is_int(duration, minimum=1) else None
    return StoryboardVideoBatchPlan(
        script_file=loaded.script_file,
        selection=selection,
        states=states,
        skips=tuple(skips),
        specs=tuple(specs),
        admission=admission,
        video_request_facts=facts,
        durations=durations,
    )


async def estimate_storyboard_video_batch_cost(
    plan: StoryboardVideoBatchPlan, session_factory: async_sessionmaker[AsyncSession]
) -> dict[str, float] | None:
    """这一批视频按剧本计划秒数的预估费用；请求事实不成立或有目标缺秒数时返回 ``None``（算不出）。"""

    facts = plan.video_request_facts
    if not plan.specs:
        return {}
    if not isinstance(facts, VideoRequestFacts) or any(value is None for value in plan.durations.values()):
        return None
    try:
        async with session_factory() as session:
            price = await CustomProviderRepository(session).resolve_price(facts.provider_id, facts.model_id)
        total: dict[str, float] = {}
        for duration in plan.durations.values():
            assert duration is not None
            quote = quote_video_request_from_price(VideoRequestCostFacts(facts, duration), price)
            total[quote.currency] = round(total.get(quote.currency, 0.0) + quote.amount, 6)
    except (SQLAlchemyError, ValueError):
        return None
    return {currency: amount for currency, amount in total.items() if amount > 0}


async def submit_storyboard_video_batch(
    plan: StoryboardVideoBatchPlan,
    *,
    project_name: str,
    queue: GenerationQueue,
    source: str,
    user_id: str,
) -> tuple[GenerationBatchReadModel, list[EnqueuedTask], list[BatchTaskResult]]:
    """把整批准入通过的规划作为一个持久批次提交，不等待结果。"""

    if not plan.admission.admitted:
        raise ValueError("整批准入未通过的规划不能提交")
    builder = GenerationResultBuilder(VIDEO_BATCH_OPERATION, GenerationSelectionMode.MISSING_ONLY)
    for state in plan.selection.skipped:
        builder.skip(state)
    specs = [replace(spec, unit_id=spec.resource_id, source=source) for spec in plan.specs]
    requested, blocked = build_generation_batch_admission(
        preflight=builder.build(),
        pending_ids=[spec.resource_id for spec in specs],
        states=plan.states,
        admission={str(item["unit_id"]): item for item in plan.admission.projections()},
    )
    return await submit_generation_batch(
        project_name=project_name,
        operation=VIDEO_BATCH_OPERATION,
        requested=requested,
        blocked=blocked,
        specs=specs,
        source=source,
        user_id=user_id,
        queue=queue,
    )


__all__ = [
    "STORYBOARD_BATCH_OPERATION",
    "VIDEO_BATCH_OPERATION",
    "BatchSkip",
    "BatchSkipReason",
    "EpisodeStoryboardScript",
    "StoryboardBatchEpisodeNotFound",
    "StoryboardBatchRouteMismatch",
    "StoryboardImageBatchPlan",
    "StoryboardVideoBatchPlan",
    "estimate_storyboard_video_batch_cost",
    "load_episode_storyboard_script",
    "load_storyboard_image_batch_plan",
    "load_storyboard_video_batch_plan",
    "plan_storyboard_image_batch",
    "storyboard_image_prompt",
    "submit_storyboard_image_batch",
    "submit_storyboard_video_batch",
]
