"""Grid-submission planning/persistence for multi-grid requests, shared by Web routes and Agent tools.

Requests have two phases:

- :func:`plan_grid_submission` gates, selects targets, groups/chunks by segments and chooses per-grid actions: generate,
  reuse in-flight records, skip unsplit composites, no generation needed, or blocked. It writes no records and enqueues
  nothing. ``list_only`` renders this exact plan, sharing preview and submission sources.
- :func:`commit_grid_submission` accepts only unblocked plans, removes superseded records, creates records and produces
  per-grid task payloads. Callers handle enqueueing/waiting and HTTP/tool-result projection.

Submission creates composites only. Splitting requires separate confirmation after user review (:mod:`server.services.grid.grid_split`).

Admission is whole-batch: any blocked shot, including missing selected IDs, unreadable artifact state, partial in-flight
overlap, reference gaps or pending prompts, prevents every task. Healthy shots receive ``generation_batch_admission_withheld``.

Serialize planning through enqueueing per project in :func:`grid_submission_section`, keeping observed grid records
and queue tasks consistent: another request cannot leave visible records not yet enqueued."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver, active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.generation.batch_admission import batch_admission_withheld_problem
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_result import (
    GenerationAction,
    GenerationCandidate,
    GenerationProblem,
    GenerationProblemCode,
    GenerationSelectionMode,
    GenerationTargetState,
    artifact_state_problem,
    observe_artifact_status,
    select_generation_targets,
)
from lib.infra.api_errors import BadRequestError
from lib.prompts.prompt_style import normalize_style_value
from lib.references.reference_admission import admit_storyboard_items
from lib.references.reference_catalog import build_reference_catalog
from lib.script.grid.grid_access import ensure_grid_writable
from lib.script.grid.grid_manager import GridManager
from lib.script.grid.grid_resolution import resolve_large_grid_allowed
from lib.script.grid.layout import GridLayout, plan_grid_chunks, video_aspect_ratio_of
from lib.script.grid.models import GridGeneration, build_grid_task_payload
from lib.script.grid.prompt_builder import build_grid_prompt, pending_grid_prompt_ids
from lib.script.script_models import get_generated_assets, resolve_content_mode
from lib.script.script_skeleton import SkeletonRouteMismatchError, ensure_route_skeleton
from lib.script.storyboard_sequence import get_storyboard_items, group_scenes_by_segment_break
from server.services.admission.reference_admission import reference_admission_problems

GRID_IN_FLIGHT_STATUSES = ("pending", "generating")

ActiveGridTaskProbe = Callable[[list[str]], Awaitable[Collection[str]]]
"""Given grid IDs, return those still owning active (queued / running) queue tasks."""


def queue_active_grid_tasks(
    queue: GenerationQueue, *, project_name: str, script_file: str, user_id: str
) -> ActiveGridTaskProbe:
    """Probe grid tasks in ``queue`` using enqueue deduplication keys and the same project / script_file / user as submission."""

    async def probe(grid_ids: list[str]) -> list[str]:
        tasks = await queue.get_active_tasks_for_resources(
            project_name=project_name,
            task_type="grid",
            resource_ids=grid_ids,
            script_file=script_file,
            user_id=user_id,
        )
        return [str(task["resource_id"]) for task in tasks]

    return probe


async def grid_is_in_flight(
    grid: GridGeneration,
    *,
    queue: GenerationQueue,
    project_name: str,
    user_id: str,
) -> bool:
    """Whether ``grid`` is still represented by an active queue task.

    The record status alone is not enough: a cancelled/restarted task can leave the
    record at ``pending`` or ``generating`` after the queue row is already terminal.
    """

    if grid.status not in GRID_IN_FLIGHT_STATUSES:
        return False
    active_ids = await queue_active_grid_tasks(
        queue,
        project_name=project_name,
        script_file=grid.script_file,
        user_id=user_id,
    )([grid.id])
    return grid.id in active_ids


class GridSubmissionSection:
    """Per-project grid critical section: only one request may read/plan records, write them and enqueue at a time.

    Project-directory records and queue tasks cannot persist atomically together. Keeping writes/enqueueing inside one
    section lets planning reliably identify pending/generating records without active tasks as unattended. Call :meth:`end`
    after enqueueing rather than holding the section while tasks run. The server is single-process, so in-process locks suffice."""

    def __init__(self, lock: asyncio.Lock) -> None:
        self._lock = lock
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    async def __aenter__(self) -> GridSubmissionSection:
        await self._lock.acquire()
        self._held = True
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.end()

    def end(self) -> None:
        """Leave the critical section early; repeated calls have no effect."""

        if self._held:
            self._held = False
            self._lock.release()


# asyncio.Lock contention must stay within one event loop; keep separate lock tables per loop.
_SECTION_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def grid_submission_section(project_name: str) -> GridSubmissionSection:
    """Grid submissions for one project share a critical section; call from an event loop."""

    locks = _SECTION_LOCKS.setdefault(asyncio.get_running_loop(), {})
    return GridSubmissionSection(locks.setdefault(project_name, asyncio.Lock()))


def ensure_grid_submittable(project: dict[str, Any], script: dict[str, Any]) -> None:
    """Grid-generation gate: the project permits grid writes and script skeleton matches generation mode.

    Raises:
        BadRequestError: Ad projects or grids disabled (:func:`ensure_grid_writable`); mismatched script skeleton
            (``grid_script_route_mismatch``: required mode-specific arrays are absent, so no grids can generate)."""

    ensure_grid_writable(project)
    try:
        ensure_route_skeleton(script, resolve_content_mode(script, project), project.get("generation_mode"))
    except SkeletonRouteMismatchError as exc:
        raise BadRequestError("grid_script_route_mismatch") from exc


class GridChunkAction(StrEnum):
    """Planned action for one grid."""

    GENERATE = "generate"
    """Create a record and enqueue it."""
    IN_FLIGHT = "in_flight"
    """The same storyboard group already has a generating grid: reuse its record; resource deduplication joins the in-flight task without another charge."""
    UNSPLIT = "unsplit"
    """In missing-only generation, the same group has a ready but unsplit combined grid image: await user review and split into storyboard slots without regenerating."""
    SKIPPED = "skipped"
    """No generation needed: explicit targets exclude this grid, or all its storyboard images remain usable."""
    BLOCKED = "blocked"
    """This grid has blocked storyboards of its own."""


@dataclass(frozen=True, slots=True)
class GridChunkPlan:
    """One grid’s complete storyboard coverage and requested storyboards requiring results (``report_ids``)."""

    group_index: int
    scenes: tuple[dict[str, Any], ...]
    scene_ids: tuple[str, ...]
    report_ids: tuple[str, ...]
    layout: GridLayout
    action: GridChunkAction
    grid: GridGeneration | None
    """The latest grid record for the same storyboard group; ``IN_FLIGHT`` / ``UNSPLIT`` means the record being reused or awaiting splitting."""

    @property
    def submits(self) -> bool:
        return self.action in (GridChunkAction.GENERATE, GridChunkAction.IN_FLIGHT)


@dataclass(frozen=True, slots=True)
class BlockedScene:
    """A storyboard without a created task and its machine-readable outcome."""

    scene_id: str
    problem: GenerationProblem
    artifact_key: ArtifactKey | None = None
    artifact_path: str | None = None
    artifact_status: ArtifactStatus | None = None


@dataclass(frozen=True, slots=True)
class GridSubmissionPlan:
    project: Mapping[str, Any]
    script_file: str
    episode: int
    id_field: str
    selection: GenerationSelectionMode
    chunks: tuple[GridChunkPlan, ...]
    skipped: tuple[GenerationTargetState, ...]
    """In missing-only mode, storyboards with usable images that are not targets, although their group needs redrawing."""
    blocked: tuple[BlockedScene, ...]
    """Storyboards blocked for their own reasons; any entries prevent creating tasks for the whole batch."""
    withheld: tuple[BlockedScene, ...]
    """Healthy storyboards for which no tasks were created because the same batch contains blockers."""
    admission_items: tuple[dict[str, Any], ...]
    """All storyboards assessed for reference and prompt admission (those covered by each grid to be newly generated)."""
    storyboard_paths: Mapping[str, str]
    """Registered storyboard-image paths in the script; use them to retain old images when reporting failure."""
    abandoned_grid_ids: frozenset[str]
    """Grid records stuck in pending / generating without active tasks; clean them up as finished records at submission."""
    section: GridSubmissionSection
    """The critical section owning planning; it must still be active when applying the plan, or ``abandoned_grid_ids`` is no longer reliable."""

    @property
    def refused(self) -> bool:
        return bool(self.blocked)

    @property
    def submitting(self) -> tuple[GridChunkPlan, ...]:
        return tuple(chunk for chunk in self.chunks if chunk.submits)

    @property
    def unsplit(self) -> tuple[GridChunkPlan, ...]:
        return tuple(chunk for chunk in self.chunks if chunk.action is GridChunkAction.UNSPLIT)


@dataclass(frozen=True, slots=True)
class GridSubmissionTask:
    """A grid ready to enqueue; ``reused`` True means an in-flight record whose enqueue deduplicates to its existing task."""

    grid: GridGeneration
    report_ids: tuple[str, ...]
    payload: dict[str, Any]
    reused: bool


async def plan_grid_submission(
    *,
    project: dict[str, Any],
    project_path: Path,
    script: dict[str, Any],
    script_file: str,
    episode: int,
    scene_ids: Sequence[str] | None,
    section: GridSubmissionSection,
    active_grid_tasks: ActiveGridTaskProbe,
    large_grid_gate: Callable[[dict[str, Any]], Awaitable[bool]] = resolve_large_grid_allowed,
) -> GridSubmissionPlan:
    """Plan grid submission; ``scene_ids`` None means missing-only generation.

    - Named selection regenerates containing grids; IDs absent from the script are blocked.
    - Missing-only generates groups still missing images, skipping usable shots, ready unsplit composites awaiting splitting,
      and reusing stale but usable old images.
    - Reuse in-flight records with identical coverage; partial overlaps block because grids would compete for cells.
      In-flight means pending/generating plus an active task reported by ``active_grid_tasks``. Cancelled, restart-lost or
      never-enqueued tasks leave unchanged records without executor progress; these are not in-flight and are cleaned
      as completed records during submission.

    Call inside ``section`` and persist/enqueue in that same critical section.

    Raises:
        BadRequestError: See :func:`ensure_grid_submittable`.
        ValueError: ``section`` not held."""

    if not section.held:
        raise ValueError("grid submission must be planned inside its submission section")
    ensure_grid_submittable(project, script)
    # Allow 4×4 / 5×5 only at 4K image resolution, sharing predicates with cost estimates/frontend previews.
    allow_large_grid = await large_grid_gate(project)

    def episode_records() -> list[GridGeneration]:
        # Planning writes nothing; absent grid directories mean no records, without GridManager directory creation.
        if not (project_path / "grids").is_dir():
            return []
        return [
            g for g in GridManager(project_path).list_all() if g.script_file == script_file and g.episode == episode
        ]

    records = await asyncio.to_thread(episode_records)
    marked_ids = [g.id for g in records if g.status in GRID_IN_FLIGHT_STATUSES]
    active: set[str] = set()
    if marked_ids:
        active = set(await active_grid_tasks(marked_ids))
        # Executors write terminal records before leaving active tasks; reread after probing so just-finished grids appear terminal, not orphaned.
        records = await asyncio.to_thread(episode_records)
    marked = [g for g in records if g.status in GRID_IN_FLIGHT_STATUSES]
    in_flight = [g for g in marked if g.id in active]
    abandoned = frozenset(g.id for g in marked) - {g.id for g in in_flight}

    # Read-only planning checks currency via manifests, digests and directory listings; run the whole phase in a thread.
    def _plan() -> GridSubmissionPlan:
        return _Planner(
            project=project,
            project_path=project_path,
            script=script,
            script_file=script_file,
            episode=episode,
            allow_large_grid=allow_large_grid,
            records=records,
            in_flight=in_flight,
            abandoned=abandoned,
            section=section,
        ).plan(scene_ids)

    return await asyncio.to_thread(_plan)


def commit_grid_submission(plan: GridSubmissionPlan, project_path: Path) -> tuple[GridSubmissionTask, ...]:
    """Persist an unblocked plan: remove superseded grid records, create replacements and return tasks to enqueue.

    Clean only this group’s records intersecting redrawn grids. Preserve records for entire unchanged chunks in oversized
    groups; remove old records spanning a redrawn chunk and others, such as smaller grids after disabling 4K, because
    they no longer match current chunks. Enqueue returned tasks before the planning critical section ends.

    Raises:
        ValueError: Whole-batch blocked plan or an already-ended planning section."""

    if plan.refused:
        raise ValueError("a refused grid submission plan cannot be committed")
    if not plan.section.held:
        raise ValueError("a grid submission plan must be committed inside the section it was planned in")
    project = plan.project
    aspect_ratio = video_aspect_ratio_of(dict(project))
    style = normalize_style_value(project.get("style"))
    style_description = normalize_style_value(project.get("style_description"))
    group_scene_ids: dict[int, set[str]] = {}
    for planned in plan.chunks:
        group_scene_ids.setdefault(planned.group_index, set()).update(planned.scene_ids)
    gm = GridManager(project_path)
    tasks: list[GridSubmissionTask] = []
    for chunk in plan.submitting:
        layout = chunk.layout
        prompt = build_grid_prompt(
            scenes=list(chunk.scenes),
            id_field=plan.id_field,
            rows=layout.rows,
            cols=layout.cols,
            style=style,
            style_description=style_description,
            aspect_ratio=aspect_ratio,
            grid_aspect_ratio=layout.grid_aspect_ratio,
        )
        if chunk.action is GridChunkAction.IN_FLIGHT and chunk.grid is not None:
            grid = chunk.grid
            reused = True
        else:
            gm.cleanup_superseded(
                plan.script_file,
                plan.episode,
                group_scene_ids[chunk.group_index],
                regenerated=set(chunk.scene_ids),
                abandoned=plan.abandoned_grid_ids,
            )
            # execute_grid_task fills provider/model after image-lane resolution.
            grid = GridGeneration.create(
                episode=plan.episode,
                script_file=plan.script_file,
                scene_ids=list(chunk.scene_ids),
                rows=layout.rows,
                cols=layout.cols,
                grid_size=layout.grid_size,
                provider="",
                model="",
                video_aspect_ratio=aspect_ratio,
                prompt=prompt,
            )
            gm.save(grid)
            reused = False
        tasks.append(
            GridSubmissionTask(
                grid=grid,
                report_ids=chunk.report_ids,
                payload=build_grid_task_payload(
                    prompt=prompt,
                    script_file=plan.script_file,
                    scene_ids=list(chunk.scene_ids),
                    grid_size=layout.grid_size,
                    rows=layout.rows,
                    cols=layout.cols,
                    grid_aspect_ratio=layout.grid_aspect_ratio,
                    video_aspect_ratio=aspect_ratio,
                ),
                reused=reused,
            )
        )
    return tuple(tasks)


def grid_artifact_key(episode: int, grid_id: str) -> ArtifactKey:
    return ArtifactKey.episode_grid(episode, grid_id)


def grid_artifact_path(grid_id: str) -> str:
    return f"grids/{grid_id}.png"


class _Planner:
    def __init__(
        self,
        *,
        project: dict[str, Any],
        project_path: Path,
        script: dict[str, Any],
        script_file: str,
        episode: int,
        allow_large_grid: bool,
        records: list[GridGeneration],
        in_flight: list[GridGeneration],
        abandoned: frozenset[str],
        section: GridSubmissionSection,
    ) -> None:
        self._project = project
        self._script_file = script_file
        self._episode = episode
        self._allow_large_grid = allow_large_grid
        items, id_field, _, _, _ = get_storyboard_items(script)
        self._items: list[dict[str, Any]] = items
        self._id_field: str = id_field
        self._aspect_ratio = video_aspect_ratio_of(project)
        self._storyboard_paths: dict[str, str] = {
            str(item.get(id_field)): path
            for item in items
            if item.get(id_field) and (path := get_generated_assets(item).get("storyboard_image"))
        }
        self._resolver: ArtifactCurrencyResolver = active_artifact_currency_resolver(project_path, project)
        self._catalog = build_reference_catalog(project)
        self._in_flight = in_flight
        self._abandoned = abandoned
        self._section = section
        # list_all orders created_at ascending; later records overwrite earlier ones, retaining only the latest per storyboard group.
        self._latest = {tuple(g.scene_ids): g for g in records}
        self._chunks: list[GridChunkPlan] = []
        self._skipped: list[GenerationTargetState] = []
        self._blocked: list[BlockedScene] = []
        self._admission_items: list[dict[str, Any]] = []

    def _id(self, item: Mapping[str, Any]) -> str:
        return str(item.get(self._id_field))

    def plan(self, scene_ids: Sequence[str] | None) -> GridSubmissionPlan:
        groups = group_scenes_by_segment_break(self._items, self._id_field)
        if scene_ids is not None:
            selection = GenerationSelectionMode.EXPLICIT
            self._plan_explicit(groups, scene_ids)
        else:
            selection = GenerationSelectionMode.MISSING_ONLY
            self._plan_missing_only(groups)
        withheld: list[BlockedScene] = []
        if self._blocked:
            problem = batch_admission_withheld_problem(list(dict.fromkeys(b.scene_id for b in self._blocked)))
            withheld = [
                self._blocked_scene(scene_id, problem)
                for chunk in self._chunks
                if chunk.action is GridChunkAction.GENERATE
                for scene_id in chunk.report_ids
            ]
        return GridSubmissionPlan(
            project=self._project,
            script_file=self._script_file,
            episode=self._episode,
            id_field=self._id_field,
            selection=selection,
            chunks=tuple(self._chunks),
            skipped=tuple(self._skipped),
            blocked=tuple(self._blocked),
            withheld=tuple(withheld),
            admission_items=tuple(self._admission_items),
            storyboard_paths=self._storyboard_paths,
            abandoned_grid_ids=self._abandoned,
            section=self._section,
        )

    def _plan_explicit(self, groups: list[list[dict[str, Any]]], scene_ids: Sequence[str]) -> None:
        wanted = set(scene_ids)
        known = {self._id(item) for item in self._items}
        for scene_id in dict.fromkeys(scene_ids):
            if scene_id not in known:
                self._blocked.append(
                    BlockedScene(
                        scene_id,
                        GenerationProblem(
                            code=GenerationProblemCode.UNIT_NOT_FOUND,
                            detail=f"分镜 {scene_id} 不在当前剧本中",
                            action=GenerationAction.FIX_INPUT,
                        ),
                    )
                )
        for index, group in enumerate(groups):
            if not any(self._id(item) in wanted for item in group):
                continue
            for chunk, layout in self._chunks_of(group):
                report_ids = tuple(self._id(item) for item in chunk if self._id(item) in wanted)
                self._add_chunk(index, chunk, layout, report_ids, missing_only=False)

    def _plan_missing_only(self, groups: list[list[dict[str, Any]]]) -> None:
        for index, group in enumerate(groups):
            # Determine group gaps from member images; all usable, including stale, means no new grid is needed.
            selection = select_generation_targets(
                candidates=[
                    GenerationCandidate(
                        unit_id=self._id(item),
                        artifact_key=ArtifactKey.episode_storyboard(self._episode, self._id(item)),
                        artifact_path=get_generated_assets(item).get("storyboard_image"),
                    )
                    for item in group
                    if item.get(self._id_field)
                ],
                requested_ids=None,
                resolver=self._resolver,
            )
            self._skipped.extend(selection.skipped)
            target_ids = frozenset(selection.target_ids)
            if selection.unavailable:
                unavailable_ids = sorted(state.unit_id for state in selection.unavailable)
                for state in selection.unavailable:
                    self._blocked.append(self._blocked_state(state, artifact_state_problem(state)))
                # Groups share a composite; any unreadable cell state prevents safe generation and also blocks
                # shots still missing images, reporting per storyboard.
                for state in selection.targets:
                    if state.unit_id in unavailable_ids:
                        continue
                    self._blocked.append(
                        self._blocked_state(
                            state,
                            GenerationProblem(
                                code=GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE,
                                detail=f"同组分镜 {unavailable_ids} 的产物状态不可读，整张宫格无法生成",
                                action=GenerationAction.REPAIR_ARTIFACT_STATE,
                            ),
                        )
                    )
                for chunk, layout in self._chunks_of(group):
                    report_ids = tuple(self._id(item) for item in chunk if self._id(item) in target_ids)
                    action = GridChunkAction.BLOCKED if report_ids else GridChunkAction.SKIPPED
                    self._append(index, chunk, layout, report_ids, action)
                continue
            for chunk, layout in self._chunks_of(group):
                report_ids = tuple(self._id(item) for item in chunk if self._id(item) in target_ids)
                self._add_chunk(index, chunk, layout, report_ids, missing_only=True)

    def _chunks_of(self, group: list[dict[str, Any]]) -> list[tuple[list[dict[str, Any]], GridLayout]]:
        return plan_grid_chunks(group, self._aspect_ratio, allow_large_grid=self._allow_large_grid)

    def _append(
        self,
        index: int,
        chunk: list[dict[str, Any]],
        layout: GridLayout,
        report_ids: tuple[str, ...],
        action: GridChunkAction,
        grid: GridGeneration | None = None,
    ) -> None:
        scene_ids = tuple(self._id(item) for item in chunk)
        self._chunks.append(
            GridChunkPlan(
                group_index=index,
                scenes=tuple(chunk),
                scene_ids=scene_ids,
                report_ids=report_ids,
                layout=layout,
                action=action,
                grid=grid if grid is not None else self._latest.get(scene_ids),
            )
        )

    def _add_chunk(
        self,
        index: int,
        chunk: list[dict[str, Any]],
        layout: GridLayout,
        report_ids: tuple[str, ...],
        *,
        missing_only: bool,
    ) -> None:
        if not report_ids:
            self._append(index, chunk, layout, report_ids, GridChunkAction.SKIPPED)
            return
        scene_ids = tuple(self._id(item) for item in chunk)
        members = set(scene_ids)
        for record in self._in_flight:
            if tuple(record.scene_ids) == scene_ids:
                self._append(index, chunk, layout, report_ids, GridChunkAction.IN_FLIGHT, record)
                return
        conflicting = [record for record in self._in_flight if members & set(record.scene_ids)]
        if conflicting:
            grid_ids = [record.id for record in conflicting]
            problem = GenerationProblem(
                code=GenerationProblemCode.ACTIVE_TASK_CONFLICT,
                detail=f"宫格 {grid_ids} 正在生成，与本张宫格覆盖的分镜部分重叠；等它完成后再提交",
                action=GenerationAction.WAIT_FOR_TASK,
                params={"grid_ids": grid_ids},
            )
            self._block_chunk(index, chunk, layout, report_ids, problem)
            return
        latest = self._latest.get(scene_ids)
        if (
            missing_only
            and latest is not None
            and latest.status == "completed"
            and latest.split_at is None
            and self._composite_splittable(latest)
        ):
            self._append(index, chunk, layout, report_ids, GridChunkAction.UNSPLIT, latest)
            return
        self._admission_items.extend(chunk)
        # One composite covers the chunk; any storyboard reference gap prevents the whole image.
        admission = admit_storyboard_items(self._catalog, chunk)
        if not admission.admitted:
            for scene_id in report_ids:
                problems = reference_admission_problems(admission, unit_id=scene_id)
                self._blocked.append(self._blocked_scene(scene_id, problems[0]))
            self._append(index, chunk, layout, report_ids, GridChunkAction.BLOCKED)
            return
        # Composite prompts assemble every cell’s image_prompt; one pending prompt prevents the whole image.
        pending_ids = pending_grid_prompt_ids(chunk, self._id_field)
        if pending_ids:
            problem = GenerationProblem(
                code=GenerationProblemCode.UNIT_REQUEST_INVALID,
                detail=f"同组分镜 {pending_ids} 的 image_prompt 尚未填写，整张宫格无法生成",
                action=GenerationAction.FIX_INPUT,
                params={"pending_ids": pending_ids},
            )
            self._block_chunk(index, chunk, layout, report_ids, problem)
            return
        self._append(index, chunk, layout, report_ids, GridChunkAction.GENERATE)

    def _composite_splittable(self, grid: GridGeneration) -> bool:
        """Whether a composite can split: require available Manifest registration like splitting itself, not merely an image on disk."""

        if not grid.grid_image_path:
            return False
        key = grid_artifact_key(self._episode, grid.id)
        return self._resolver.compare(key, artifact_path=grid.grid_image_path).usable

    def _block_chunk(
        self,
        index: int,
        chunk: list[dict[str, Any]],
        layout: GridLayout,
        report_ids: tuple[str, ...],
        problem: GenerationProblem,
    ) -> None:
        for scene_id in report_ids:
            self._blocked.append(self._blocked_scene(scene_id, problem))
        self._append(index, chunk, layout, report_ids, GridChunkAction.BLOCKED)

    def _blocked_scene(self, scene_id: str, problem: GenerationProblem) -> BlockedScene:
        """Include registered old-image paths/state so downstream distinguishes an existing old image from no original image."""

        key = ArtifactKey.episode_storyboard(self._episode, scene_id)
        path = self._storyboard_paths.get(scene_id)
        status, _blocker = observe_artifact_status(resolver=self._resolver, key=key, artifact_path=path)
        return BlockedScene(scene_id, problem, key, path, status)

    def _blocked_state(self, state: GenerationTargetState, problem: GenerationProblem) -> BlockedScene:
        return BlockedScene(state.unit_id, problem, state.artifact_key, state.artifact_path, state.status)


__all__ = [
    "GRID_IN_FLIGHT_STATUSES",
    "ActiveGridTaskProbe",
    "BlockedScene",
    "GridChunkAction",
    "GridChunkPlan",
    "GridSubmissionPlan",
    "GridSubmissionSection",
    "GridSubmissionTask",
    "commit_grid_submission",
    "ensure_grid_submittable",
    "grid_artifact_key",
    "grid_artifact_path",
    "grid_is_in_flight",
    "grid_submission_section",
    "plan_grid_submission",
    "queue_active_grid_tasks",
]
