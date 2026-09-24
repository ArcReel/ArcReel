"""宫格提交：一次「生成多宫格分镜」请求的规划与落地，Web 路由与 Agent 工具共用。

一次请求分两步：

- :func:`plan_grid_submission` 过闸门、选目标、按分段分组切块，并逐张宫格给出动作——新生成、
  沿用在途记录、联合图未切分而跳过、无需生成、受阻。规划不写宫格记录、不入队，``list_only``
  预览渲染的就是这份规划，预告与实际提交因此同源。
- :func:`commit_grid_submission` 只接受未受阻的规划：清理被取代的旧记录、建记录，产出每张宫格
  的任务 payload。入队与等待、以及把结论投影成 HTTP 响应或工具结果，留给各入口。

提交只产出联合图；切分落格是用户审阅联合图后另行确认的动作（见 :mod:`server.services.grid.grid_split`）。

准入是整批的：任一分镜受阻（点名不存在、产物状态不可读、与在途宫格部分重叠、引用缺口、提示词待生成），
整个请求不建任何任务，本身健康的分镜带 ``generation_batch_admission_withheld``。

同一项目的宫格提交从规划到入队在 :func:`grid_submission_section` 里串行执行，规划看到的
宫格记录与队列任务因此总是一致的：不会有另一请求写了记录、还没入队。
"""

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
"""给定宫格 ID，返回队列里仍有活动任务（queued / running）的那些。"""


def queue_active_grid_tasks(
    queue: GenerationQueue, *, project_name: str, script_file: str, user_id: str
) -> ActiveGridTaskProbe:
    """按入队去重键在 ``queue`` 里探测宫格任务；与宫格入队用同一组 project / script_file / user。"""

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


class GridSubmissionSection:
    """同一项目宫格提交的临界区：从读记录规划，到写记录、入队，同一时刻只有一个请求在里面。

    宫格记录写在项目目录、任务写在队列，两者无法原子地一起落地；把写记录与入队都关进临界区，
    区内规划就能把「停在 pending / generating 却没有活动任务」的记录确认为已无人处理。
    入队完成即可 :meth:`end`，等待任务跑完不占着临界区。服务端单进程运行，进程内的锁即可串行化。
    """

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
        """提前离开临界区；重复调用无副作用。"""

        if self._held:
            self._held = False
            self._lock.release()


# asyncio.Lock 只能在一个事件循环里争用，锁表按事件循环分开
_SECTION_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def grid_submission_section(project_name: str) -> GridSubmissionSection:
    """同一项目的宫格提交共用一个临界区；须在事件循环内调用。"""

    locks = _SECTION_LOCKS.setdefault(asyncio.get_running_loop(), {})
    return GridSubmissionSection(locks.setdefault(project_name, asyncio.Lock()))


def ensure_grid_submittable(project: dict[str, Any], script: dict[str, Any]) -> None:
    """宫格出图入口的闸门：项目允许改写宫格，且剧本骨架属于项目生成模式。

    Raises:
        BadRequestError: 广告项目或未启用宫格（见 :func:`ensure_grid_writable`）；剧本骨架与生成模式
            失配（``grid_script_route_mismatch``：按生成模式要读的数组不在剧本里，出不了任何宫格）。
    """

    ensure_grid_writable(project)
    try:
        ensure_route_skeleton(script, resolve_content_mode(script, project), project.get("generation_mode"))
    except SkeletonRouteMismatchError as exc:
        raise BadRequestError("grid_script_route_mismatch") from exc


class GridChunkAction(StrEnum):
    """规划给一张宫格的动作。"""

    GENERATE = "generate"
    """新建记录并入队。"""
    IN_FLIGHT = "in_flight"
    """同一组分镜的宫格正在生成：沿用该记录，入队按资源去重落到在途任务上，不重复计费。"""
    UNSPLIT = "unsplit"
    """缺失即生成时，同一组分镜的联合图已就绪而未切分：等用户审阅后切分落格，不重生成。"""
    SKIPPED = "skipped"
    """本次无需生成：点名未覆盖这张宫格，或它的分镜图都还可用。"""
    BLOCKED = "blocked"
    """这张宫格有自己的受阻分镜。"""


@dataclass(frozen=True, slots=True)
class GridChunkPlan:
    """一张宫格：它覆盖的全部分镜，以及本次请求要为之报告结果的分镜（``report_ids``）。"""

    group_index: int
    scenes: tuple[dict[str, Any], ...]
    scene_ids: tuple[str, ...]
    report_ids: tuple[str, ...]
    layout: GridLayout
    action: GridChunkAction
    grid: GridGeneration | None
    """同一组分镜的最新宫格记录；``IN_FLIGHT`` / ``UNSPLIT`` 时即被沿用或等待切分的那条。"""

    @property
    def submits(self) -> bool:
        return self.action in (GridChunkAction.GENERATE, GridChunkAction.IN_FLIGHT)


@dataclass(frozen=True, slots=True)
class BlockedScene:
    """一个未创建任务的分镜及其机器可读结论。"""

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
    """缺失即生成下，所在分组要重画、但自身分镜图仍可用而不在本次目标里的分镜。"""
    blocked: tuple[BlockedScene, ...]
    """带自身原因受阻的分镜；非空即整批不建任务。"""
    withheld: tuple[BlockedScene, ...]
    """本身健康、因同批受阻而未创建任务的分镜。"""
    admission_items: tuple[dict[str, Any], ...]
    """经过引用与提示词准入判定的全部分镜（待新生成的各张宫格覆盖的分镜）。"""
    storyboard_paths: Mapping[str, str]
    """剧本里各分镜已登记的分镜图路径；报告失败时据此带上旧图。"""
    abandoned_grid_ids: frozenset[str]
    """停在 pending / generating 却已没有活动任务的宫格记录；提交时按已结束的记录清理。"""
    section: GridSubmissionSection
    """规划所在的临界区；落地时它必须仍未结束，否则 ``abandoned_grid_ids`` 已不可信。"""

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
    """一张待入队的宫格；``reused`` 为 True 时记录已在途，入队会去重到既有任务。"""

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
    """规划一次宫格提交；``scene_ids`` 为 ``None`` 表示缺失即生成。

    - 点名：重生成包含这些分镜的宫格；不在剧本里的 ID 受阻。
    - 缺失即生成：只为仍缺分镜图的分组出图，组内已可用的分镜记为跳过；联合图已就绪而未切分的
      宫格跳过、等待切分落格；已失效但可用的旧分镜图照常复用。
    - 与在途宫格覆盖同一组分镜时沿用在途记录；只部分重叠时受阻，两张宫格日后会争抢同一批格子。
      在途指记录停在 pending / generating，且 ``active_grid_tasks`` 报告它仍有活动任务。任务被取消、
      重启丢失或入队失败时执行器没有运行，记录停在原状态；这样的记录不算在途，提交时按已结束的记录清理。

    须在 ``section`` 内调用，并在同一临界区内落地与入队。

    Raises:
        BadRequestError: 见 :func:`ensure_grid_submittable`。
        ValueError: ``section`` 未持有。
    """

    if not section.held:
        raise ValueError("grid submission must be planned inside its submission section")
    ensure_grid_submittable(project, script)
    # 4×4 / 5×5 只在图像分辨率档为 4K 时放行；判定与费用估算、前端预览同源
    allow_large_grid = await large_grid_gate(project)
    # 规划不落任何文件：宫格目录尚不存在时即没有记录，不经 GridManager 建目录
    gm = GridManager(project_path) if (project_path / "grids").is_dir() else None
    records = [
        g for g in (gm.list_all() if gm is not None else []) if g.script_file == script_file and g.episode == episode
    ]
    marked = [g for g in records if g.status in GRID_IN_FLIGHT_STATUSES]
    active = set(await active_grid_tasks([g.id for g in marked])) if marked else set()
    in_flight = [g for g in marked if g.id in active]
    return _Planner(
        project=project,
        project_path=project_path,
        script=script,
        script_file=script_file,
        episode=episode,
        allow_large_grid=allow_large_grid,
        records=records,
        in_flight=in_flight,
        abandoned=frozenset(g.id for g in marked) - {g.id for g in in_flight},
        section=section,
    ).plan(scene_ids)


def commit_grid_submission(plan: GridSubmissionPlan, project_path: Path) -> tuple[GridSubmissionTask, ...]:
    """落地一份未受阻的规划：逐张宫格清理被取代的旧记录、建记录，返回待入队的任务。

    清理限定在本组内、只删与本次重画那张有交集的旧记录：超上限分组里整张都无需重画的那张，
    旧记录必须留下；横跨重画那张与其余分块的旧记录（如 4K 档关闭后改切小宫格）已不合当前分块，一并删除。
    返回的任务须在规划所在的临界区结束前入队。

    Raises:
        ValueError: 规划整批受阻，或规划所在的临界区已结束。
    """

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
            # provider/model 由 execute_grid_task 在 image lane 解析之后回填
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
        # list_all 按 created_at 升序，后写覆盖前写：同一组分镜只留最新一条
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
            # 分组的缺口按成员分镜图判定：整组分镜图都还可用（含 stale）时无需再出一张宫格
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
                # 宫格整组共用一张联合图：同组任一格状态不可读就无法安全出图，组内仍缺分镜图的
                # 分镜同样受阻，逐分镜给结论。
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
        # 一张联合图覆盖整个 chunk：任一分镜的引用有缺口就出不了这张图
        admission = admit_storyboard_items(self._catalog, chunk)
        if not admission.admitted:
            for scene_id in report_ids:
                problems = reference_admission_problems(admission, unit_id=scene_id)
                self._blocked.append(self._blocked_scene(scene_id, problems[0]))
            self._append(index, chunk, layout, report_ids, GridChunkAction.BLOCKED)
            return
        # 联合图的提示词由每格的 image_prompt 拼成，任一格待生成整张图都出不了
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
        """联合图能否切分落格；与切分同一口径：产物清单登记在案且可用，盘上有图不算。"""

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
        """带上该分镜已登记的旧图路径与状态：下游据此分清「旧图还在」与「原本就没有」。"""

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
    "grid_submission_section",
    "plan_grid_submission",
    "queue_active_grid_tasks",
]
