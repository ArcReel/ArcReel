"""Episode-scoped storyboard-image/video batches: planning shared by Web timeline/grid actions and Agent ``generate_storyboards``.

- **Images**: :func:`plan_storyboard_image_batch` supplies the same plan to ``generate_storyboards`` and Web batches.
  Without names, select only shots lacking usable images. Reject missing prompts/unavailable references per item and
  submit the rest, queueing adjacent shots by their within-batch reference chain.
- **Videos**: :func:`load_storyboard_video_batch_plan` selects shots without usable video, first skipping missing prompts,
  missing images and active generation. Admit remaining shots as a whole through :func:`admit_storyboard_video_request`:
  any failed target rejects the whole batch without creating tasks.

Exclude stale images/videos from batches; users regenerate individually in shot details. Planning is read-only:
selection, admission and cost estimation create no tasks; callers submit through the batch entry point."""

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
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.config.resolver import ConfigResolver
from lib.generation.batch_admission import BatchAdmission, UnitAdmissionTicket
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
from lib.infra.async_thread import run_sync_transaction
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
from server.services.admission.cost_estimation import (
    BatchCostEstimate,
    ImageLane,
    load_video_price_source,
    quote_video_request_from_price,
)
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
    """Why an item lacks a usable artifact yet is excluded from this batch; confirmation dialogs use it for skipped items."""

    MISSING_PROMPT = "missing_prompt"
    MISSING_STORYBOARD = "missing_storyboard"
    GENERATING = "generating"
    #: References are unregistered or their assets lack usable images.
    REFERENCE_UNAVAILABLE = "reference_unavailable"
    #: Unreadable Manifest or invalid entry.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class BatchSkip:
    unit_id: str
    reason: BatchSkipReason


class StoryboardBatchEpisodeNotFound(LookupError):
    """The episode scope identifies an absent ledger episode or one without a formal script."""

    def __init__(self, episode_id: int) -> None:
        self.episode_id = episode_id
        super().__init__(f"episode {episode_id} has no formal script in this project")


class StoryboardBatchRouteMismatch(ValueError):
    """Reference-video projects skip storyboard images, so storyboard batches do not apply."""


@dataclass(frozen=True, slots=True)
class EpisodeStoryboardScript:
    """Project state for episode-scoped batches: project, formal episode script and artifact episode identity."""

    project: dict[str, Any]
    project_path: Path
    script: dict[str, Any]
    script_file: str
    #: Episode identity in the artifact manifest.
    episode: int


def load_episode_storyboard_script(
    projects: ProjectManager, project_name: str, episode_id: int
) -> EpisodeStoryboardScript:
    """Read project/formal script for episode batches and enforce generation-mode/skeleton gates.

    Raises:
        StoryboardBatchEpisodeNotFound: Episode absent or no formal script.
        StoryboardBatchRouteMismatch: Reference-video project.
        SkeletonRouteMismatchError: Script skeleton mismatches project generation mode."""

    project = projects.load_project(project_name)
    if is_reference_video_project(project):
        raise StoryboardBatchRouteMismatch("参考生视频项目没有分镜图步骤")
    entry = find_episode(project, episode_id)
    recorded = entry.get("script_file") if entry is not None else None
    if not isinstance(recorded, str) or not recorded:
        raise StoryboardBatchEpisodeNotFound(episode_id)
    # Tasks/deduplication keys use script filenames like single-item generation; the ledger stores paths under ``scripts/``.
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


# --- Storyboard images ---------------------------------------------------------


def storyboard_image_prompt(segment: dict[str, Any], style: str, style_description: str, id_field: str) -> str:
    """Actual submitted prompt for one storyboard image; raise ``ValueError`` if absent."""

    image_prompt = segment.get("image_prompt", "")
    if not image_prompt:
        raise ValueError(f"分镜 {segment[id_field]} 缺少 image_prompt 字段")
    return render_storyboard_image_prompt(image_prompt, style=style, style_description=style_description)


def _storyboard_image_lane(
    items: Sequence[dict[str, Any]], index: int, reference_fields: Sequence[str | None]
) -> ImageLane:
    """Image lane for a storyboard: references, either asset images or the preceding storyboard, imply image-to-image.

    Evaluate in execution assembly order: product/character/scene/prop references, or a preceding storyboard when not first
    and not a chapter break. Used only for quoting; execution falls back to text-to-image if the preceding image is unavailable."""

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
    """Storyboard-image batch plan: targets, per-item refusals/reuse and the within-batch reference chain."""

    script_file: str
    items: Sequence[dict[str, Any]]
    id_field: str
    #: Submission targets in script order.
    targets: tuple[GenerationTargetState, ...]
    preflight: GenerationBatchResult
    #: Image lane per target for quoting.
    lanes: Mapping[str, ImageLane]
    missing_prompt: frozenset[str] = frozenset()
    reference_blocked: frozenset[str] = frozenset()
    #: Targets with active tasks; reuse those tasks at submission without charging again.
    generating: frozenset[str] = frozenset()

    @property
    def target_ids(self) -> list[str]:
        return [state.unit_id for state in self.targets]

    @property
    def states(self) -> dict[str, GenerationTargetState]:
        return {state.unit_id: state for state in self.targets}

    @property
    def new_task_ids(self) -> list[str]:
        """Targets creating new paid tasks, excluding those already generating."""

        return [unit_id for unit_id in self.target_ids if unit_id not in self.generating]

    @property
    def skips(self) -> tuple[BatchSkip, ...]:
        """Shots lacking usable images but creating no new task in this batch, with reasons."""

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
    """Select image-batch targets and assess per-item admission; ``requested_ids`` None selects only missing storyboard images."""

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
        # Record one problem per shot, the first one. Unregistered precedes missing images because
        # image-generation advice cannot identify an unregistered asset; report the other problem after this one is repaired.
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
    """Read episode state and plan storyboard images, marking targets already generating."""

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

    # Reading scripts may persist migrations.
    loaded, plan = await run_sync_transaction(_read)
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
    """Submit a storyboard-image plan as a durable batch without awaiting results."""

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


# --- Storyboard videos ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoryboardVideoBatchPlan:
    """Storyboard-video batch plan: skipped items, whole-batch admission and specs to enqueue after admission."""

    script_file: str
    selection: GenerationSelection
    states: Mapping[str, GenerationTargetState]
    skips: tuple[BatchSkip, ...]
    specs: tuple[TaskSpec, ...]
    admission: BatchAdmission
    video_request_facts: VideoRequestFacts | VideoRequestFactsFailure | None
    #: Script-planned seconds per target; nonpositive/noninteger values become ``None`` and prevent quoting this batch.
    durations: Mapping[str, int | None] = field(default_factory=dict)
    #: Aspect ratio sent in video requests, resolved identically to generation tasks.
    video_aspect_ratio: str = "16:9"

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
        # Spec construction rejects unavailable end frames/other inputs, accurately reported by whole-batch admission.
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
    """Read episode state, select shots without usable videos, list skips, and admit remaining shots as a whole batch.

    Omitted ``video_request_facts`` resolves current project configuration; admission and quoting share the same facts."""

    # Target/skip selection reads the Manifest, including lock waits, and checks image files; run each whole phase in a worker thread.
    # Target selection reads scripts and may persist migrations, so use a write transaction; skip assessment is read-only.
    def _select() -> tuple[
        EpisodeStoryboardScript,
        str,
        list[UnitAdmissionTicket],
        ArtifactCurrencyResolver,
        dict[str, GenerationTargetState],
        GenerationSelection,
        list[dict[str, Any]],
    ]:
        loaded = load_episode_storyboard_script(projects, project_name, episode_id)
        items, id_field, *_fields = get_storyboard_items(loaded.script)
        clean, screen_refused = screen_storyboard_items(items, id_field, requested_ids=None)
        resolver = active_artifact_currency_resolver(loaded.project_path, loaded.project)
        states = video_target_states(clean, id_field, episode=loaded.episode, resolver=resolver)
        selection = select_generation_targets(
            candidates=[state.candidate for state in states.values()],
            requested_ids=None,
            resolver=resolver,
        )
        target_ids = set(selection.target_ids)
        pending = [item for item in clean if str(storyboard_item_id(item, id_field) or "") in target_ids]
        return loaded, id_field, screen_refused, resolver, states, selection, pending

    loaded, id_field, screen_refused, resolver, states, selection, pending = await run_sync_transaction(_select)
    generating = await _active_resource_ids(
        queue,
        project_name=project_name,
        task_type="video",
        script_file=loaded.script_file,
        resource_ids=[str(storyboard_item_id(item, id_field)) for item in pending],
        user_id=user_id,
    )
    content_mode = resolve_content_mode(loaded.script, loaded.project)
    voice_characters = await resolve_voice_context(loaded.project, content_mode)

    def _build_specs() -> tuple[list[BatchSkip], list[dict[str, Any]], list[TaskSpec], list[UnitAdmissionTicket]]:
        skips: list[BatchSkip] = []
        candidates: list[dict[str, Any]] = []
        for item in pending:
            unit_id = str(storyboard_item_id(item, id_field))
            reason = _video_skip_reason(item, unit_id=unit_id, generating=generating, loaded=loaded, resolver=resolver)
            if reason is None:
                candidates.append(item)
            else:
                skips.append(BatchSkip(unit_id, reason))
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
            voice_characters=voice_characters,
        )
        return skips, candidates, specs, refused

    skips, candidates, specs, refused = await asyncio.to_thread(_build_specs)
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
        # Unreadable-Manifest shots and malformed script entries belong to the request and participate in admission; any blockage prevents all enqueueing.
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
        video_aspect_ratio=resolve_video_aspect_ratio(loaded.project),
    )


async def estimate_storyboard_video_batch_cost(
    plan: StoryboardVideoBatchPlan, session_factory: async_sessionmaker[AsyncSession]
) -> BatchCostEstimate:
    """Estimate video costs by script-planned seconds: total priced units and count unpriced targets.

    Invalid facts, missing planned seconds, database-read failures or unpriceable targets all count as unpriced."""

    facts = plan.video_request_facts
    if not plan.specs:
        return BatchCostEstimate(total={}, unpriced_count=0)
    durations = list(plan.durations.values())
    if not isinstance(facts, VideoRequestFacts):
        return BatchCostEstimate(total={}, unpriced_count=len(durations))
    try:
        async with session_factory() as session:
            source = await load_video_price_source(session, facts.provider_id, facts.model_id)
    except SQLAlchemyError:
        return BatchCostEstimate(total={}, unpriced_count=len(durations))
    total: dict[str, float] = {}
    unpriced = 0
    for duration in durations:
        if duration is None:
            unpriced += 1
            continue
        try:
            quote = quote_video_request_from_price(
                VideoRequestCostFacts(facts, duration, aspect_ratio=plan.video_aspect_ratio, has_first_frame=True),
                source,
            )
        except ValueError:
            unpriced += 1
            continue
        if quote.amount is None or quote.currency is None:
            unpriced += 1
            continue
        total[quote.currency] = round(total.get(quote.currency, 0.0) + quote.amount, 6)
    return BatchCostEstimate(
        total={currency: amount for currency, amount in total.items() if amount > 0}, unpriced_count=unpriced
    )


async def submit_storyboard_video_batch(
    plan: StoryboardVideoBatchPlan,
    *,
    project_name: str,
    queue: GenerationQueue,
    source: str,
    user_id: str,
) -> tuple[GenerationBatchReadModel, list[EnqueuedTask], list[BatchTaskResult]]:
    """Submit an admitted whole-batch plan as a durable batch without awaiting results."""

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
