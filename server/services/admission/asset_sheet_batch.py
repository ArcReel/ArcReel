"""资产图批量生成：Web 集层 / 画廊入口与 Agent 的 ``list_pending_assets`` / ``generate_assets`` 共用的服务命令。

一次请求按范围选出目标：

- **类型范围**（画廊各类型页）：该类型 × 全项目；角色类型含其衍生。
- **集范围**（集层下一步、Agent 的 ``episode_id``）：本集引用的资产与衍生
  （:func:`lib.project.episode_asset_references.episode_referenced_assets`，唯一的计算点）。
- **点名**（Agent 的 ``names``）：显式选择，已有资产图的也照常重生。

不点名时只选待生成（产物清单判为 missing）的目标；过期与现行的图不进批量。缺描述的目标
不提交、逐项报告。衍生随本体进同一批：本体资产图可用时衍生直接提交；本体也在这一批里时，
衍生在批内依赖本体，本体任务成功后队列才执行它，本体失败则衍生从未提交给供应商。其余情形
（本体缺描述、不在这一批）衍生报「本体资产图未生成」。

规划只读：选目标、判准入、算预估费用都不建任务；提交由调用方经批次入口完成。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver, active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.generation.generation_batch import GenerationBatchReadModel, build_generation_batch_admission
from lib.generation.generation_queue import GenerationQueue
from lib.generation.generation_queue_client import EnqueuedTask, TaskSpec, submit_generation_batch
from lib.generation.generation_result import (
    GenerationAction,
    GenerationBatchResult,
    GenerationCandidate,
    GenerationProblem,
    GenerationResultBuilder,
    GenerationSelectionMode,
    GenerationTargetState,
    observe_artifact_status,
    select_generation_targets,
)
from lib.project.asset_derivatives import (
    DERIVATIVE_TASK_TYPE,
    derivative_artifact_id,
    derivative_artifact_key,
    derivative_sheet_relative_path,
    derivative_table,
    split_derivative_artifact_id,
)
from lib.project.asset_types import ASSET_SPECS, asset_name_comparison_key, resolve_asset_key
from lib.project.episode_asset_references import ReferencedAsset, episode_referenced_assets
from lib.project.project_manager import ProjectManager, find_episode
from lib.prompts.prompt_builders import build_character_derivative_prompt

ASSET_BATCH_OPERATION = "generate_assets"

#: 缺描述的两种拒绝码：本体与衍生各一，与单张生成入口拒绝时同码。
ASSET_DESCRIPTION_REQUIRED = "asset_description_required"
DERIVATIVE_DESCRIPTION_REQUIRED = "derivative_description_required"
DERIVATIVE_OWNER_SHEET_MISSING = "derivative_owner_sheet_missing"
DESCRIPTION_REQUIRED_CODES = frozenset({ASSET_DESCRIPTION_REQUIRED, DERIVATIVE_DESCRIPTION_REQUIRED})


class AssetBatchEpisodeNotFound(LookupError):
    """集范围指向的集不在分集账本里。"""

    def __init__(self, episode_id: int) -> None:
        self.episode_id = episode_id
        super().__init__(f"episode {episode_id} does not exist in this project")


def asset_unit_id(asset_type: str, name: str) -> str:
    """资产图单元 ID：``<类型>/<名字>``；衍生的名字是 ``本体/衍生``。

    一次请求可跨四类资产，而类型之间可能重名，逐 ID 的结果契约要求批内 ID 全局唯一。
    """

    return f"{asset_type}/{name}"


def asset_name_of(unit_id: str) -> str:
    """单元 ID 里的名字（衍生为 ``本体/衍生``）——:func:`asset_unit_id` 的逆。"""

    return unit_id.split("/", 1)[1]


@dataclass(frozen=True, slots=True)
class AssetSheetScope:
    """一次批量生成的范围；三种范围互斥，都不给即全项目。"""

    asset_type: str | None = None
    episode_id: int | None = None
    #: 点名的资产名（衍生写作 ``本体/衍生``）；必须配合 ``asset_type``。
    names: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class AssetSheetUnit:
    """一张可生成的资产图：本体资产图或一套衍生的资产图。"""

    asset_type: str
    #: 落盘真名；衍生为 ``本体/衍生``。
    name: str
    #: 衍生所属本体的落盘真名；本体为 ``None``。
    owner: str | None
    #: 去掉两端空白后的描述；衍生是外观变化描述。未填写为 ``None``。
    description: str | None
    #: 是否声明了原图：声明了原图的资产图按图生图生成。
    has_originals: bool
    candidate: GenerationCandidate

    @property
    def unit_id(self) -> str:
        return self.candidate.unit_id

    @property
    def is_derivative(self) -> bool:
        return self.owner is not None

    @property
    def owner_unit_id(self) -> str | None:
        return asset_unit_id(self.asset_type, self.owner) if self.owner is not None else None

    @property
    def default_sheet_path(self) -> str:
        """这张资产图的规范落盘路径，供任务结果缺路径时回填。"""

        if self.owner is not None:
            owner, derivative = split_derivative_artifact_id(self.name)
            return derivative_sheet_relative_path(owner, derivative)
        return f"{ASSET_SPECS[self.asset_type].subdir}/{self.name}.png"

    @property
    def image_to_image(self) -> bool:
        """衍生是对本体资产图的编辑，声明了原图的本体以原图为参考：两者都走图生图。"""

        return self.is_derivative or self.has_originals


def _clean_description(entry: object) -> str | None:
    description = entry.get("description") if isinstance(entry, Mapping) else None
    return description.strip() if isinstance(description, str) and description.strip() else None


def _declares_originals(entry: object, asset_type: str) -> bool:
    if not isinstance(entry, Mapping):
        return False
    for field_name in ASSET_SPECS[asset_type].original_image_fields:
        value = entry.get(field_name)
        if (isinstance(value, str) and value) or (isinstance(value, list) and any(value)):
            return True
    return False


def project_asset_sheet_units(project: Mapping[str, Any], asset_type: str | None = None) -> list[AssetSheetUnit]:
    """项目里全部可生成的资产图，按类型顺序排列，每个本体之后紧跟它的衍生。"""

    units: list[AssetSheetUnit] = []
    for spec in ASSET_SPECS.values():
        if asset_type is not None and spec.asset_type != asset_type:
            continue
        bucket = project.get(spec.bucket_key)
        if not isinstance(bucket, Mapping):
            continue
        for name, entry in bucket.items():
            if not isinstance(name, str) or not isinstance(entry, Mapping):
                continue
            sheet = entry.get(spec.sheet_field)
            units.append(
                AssetSheetUnit(
                    asset_type=spec.asset_type,
                    name=name,
                    owner=None,
                    description=_clean_description(entry),
                    has_originals=_declares_originals(entry, spec.asset_type),
                    candidate=GenerationCandidate(
                        unit_id=asset_unit_id(spec.asset_type, name),
                        artifact_key=ArtifactKey.asset_sheet(spec.asset_type, asset_name_comparison_key(name)),
                        artifact_path=sheet if isinstance(sheet, str) and sheet else None,
                    ),
                )
            )
            if not spec.supports_derivatives:
                continue
            for derivative_name, derivative in derivative_table(entry).items():
                if not isinstance(derivative, Mapping):
                    continue
                derivative_sheet = derivative.get(spec.sheet_field)
                units.append(
                    AssetSheetUnit(
                        asset_type=spec.asset_type,
                        name=derivative_artifact_id(name, derivative_name),
                        owner=name,
                        description=_clean_description(derivative),
                        has_originals=False,
                        candidate=GenerationCandidate(
                            unit_id=asset_unit_id(spec.asset_type, derivative_artifact_id(name, derivative_name)),
                            artifact_key=derivative_artifact_key(
                                asset_name_comparison_key(name), asset_name_comparison_key(derivative_name)
                            ),
                            artifact_path=(
                                derivative_sheet if isinstance(derivative_sheet, str) and derivative_sheet else None
                            ),
                        ),
                    )
                )
    return units


def _resolve_named_unit_id(project: Mapping[str, Any], asset_type: str, name: str) -> str:
    """把点名的名字解析成规范单元 ID；解析不到时保留原拼写，让结果逐项报告「不存在」。"""

    bucket = project.get(ASSET_SPECS[asset_type].bucket_key)
    owner_name, separator, derivative_name = name.partition("/")
    owner_key = resolve_asset_key(bucket, owner_name)
    if owner_key is None:
        return asset_unit_id(asset_type, name)
    if not separator:
        return asset_unit_id(asset_type, owner_key)
    table = derivative_table(bucket.get(owner_key) if isinstance(bucket, Mapping) else None)
    derivative_key = resolve_asset_key(table, derivative_name)
    if derivative_key is None:
        return asset_unit_id(asset_type, name)
    return asset_unit_id(asset_type, derivative_artifact_id(owner_key, derivative_key))


def _referenced_unit_ids(referenced: frozenset[ReferencedAsset]) -> frozenset[str]:
    return frozenset(asset_unit_id(item.asset_type, item.name) for item in referenced)


@dataclass(frozen=True, slots=True)
class AssetSheetBatchPlan:
    """一次资产图批量生成的规划结果：要提交的目标、逐项拒绝与复用、批内依赖。"""

    selection: GenerationSelectionMode
    #: 要提交的目标，本体在前、衍生在后。
    targets: tuple[GenerationTargetState, ...]
    units: Mapping[str, AssetSheetUnit]
    #: 衍生单元 → 同批内的本体单元。
    dependencies: Mapping[str, str]
    #: 目标里已有活动任务的单元：提交时并入原任务，不再计费。
    generating: frozenset[str]
    preflight: GenerationBatchResult
    states: Mapping[str, GenerationTargetState] = field(default_factory=dict)

    @property
    def target_ids(self) -> list[str]:
        return [state.unit_id for state in self.targets]

    @property
    def new_task_ids(self) -> list[str]:
        """会新建付费任务的目标：扣掉已在生成中的。"""

        return [unit_id for unit_id in self.target_ids if unit_id not in self.generating]

    @property
    def missing_description_ids(self) -> list[str]:
        return [
            item.unit_id
            for item in self.preflight.items
            if item.problem is not None and item.problem.code in DESCRIPTION_REQUIRED_CODES
        ]

    def task_specs(self, *, source: str) -> list[TaskSpec]:
        specs: list[TaskSpec] = []
        for state in self.targets:
            unit = self.units[state.unit_id]
            if unit.is_derivative:
                dependency = self.dependencies.get(unit.unit_id)
                specs.append(
                    TaskSpec.from_request(
                        task_type=DERIVATIVE_TASK_TYPE,
                        media_type="image",
                        resource_id=unit.name,
                        prompt=build_character_derivative_prompt(unit.description or ""),
                        unit_id=unit.unit_id,
                        source=source,
                        dependency_resource_id=asset_name_of(dependency) if dependency is not None else None,
                    )
                )
                continue
            specs.append(
                TaskSpec.from_request(
                    task_type=unit.asset_type,
                    media_type="image",
                    resource_id=unit.name,
                    unit_id=unit.unit_id,
                    source=source,
                )
            )
        return specs


def _problem(code: str, detail: str, action: GenerationAction, **params: Any) -> GenerationProblem:
    return GenerationProblem(code=code, detail=detail, action=action, params=params)


def plan_asset_sheet_batch(
    project: Mapping[str, Any],
    resolver: ArtifactCurrencyResolver,
    scope: AssetSheetScope,
    *,
    referenced: frozenset[ReferencedAsset] | None = None,
) -> AssetSheetBatchPlan:
    """按范围选出这一批的目标并逐项判准入；集范围由调用方传入本集引用的资产。"""

    if scope.names is not None and scope.asset_type is None:
        raise ValueError("names 必须配合 type 使用")
    if scope.episode_id is not None and (scope.asset_type is not None or scope.names is not None):
        raise ValueError("episode_id 不能与 type / names 同时使用")

    units = project_asset_sheet_units(project, scope.asset_type)
    if scope.episode_id is not None:
        wanted = _referenced_unit_ids(referenced or frozenset())
        units = [unit for unit in units if unit.unit_id in wanted]
    by_id = {unit.unit_id: unit for unit in units}

    requested_ids: list[str] | None = None
    if scope.names is not None:
        assert scope.asset_type is not None
        requested_ids = list(
            dict.fromkeys(_resolve_named_unit_id(project, scope.asset_type, name) for name in scope.names)
        )
    selection = select_generation_targets(
        candidates=[unit.candidate for unit in units],
        requested_ids=requested_ids,
        resolver=resolver,
    )
    builder = GenerationResultBuilder.from_selection(ASSET_BATCH_OPERATION, selection)

    accepted: list[GenerationTargetState] = []
    accepted_ids: set[str] = set()
    dependencies: dict[str, str] = {}
    for state in selection.targets:
        unit = by_id[state.unit_id]
        label = ASSET_SPECS[unit.asset_type].label_zh
        if unit.description is None:
            builder.block(
                unit.unit_id,
                problem=(
                    _problem(
                        DERIVATIVE_DESCRIPTION_REQUIRED,
                        f"衍生 '{unit.name}' 缺少外观变化描述，无法生成资产图",
                        GenerationAction.FIX_INPUT,
                        name=unit.name,
                    )
                    if unit.is_derivative
                    else _problem(
                        ASSET_DESCRIPTION_REQUIRED,
                        f"{label} '{unit.name}' 缺少 description，无法生成资产图",
                        GenerationAction.FIX_INPUT,
                        name=unit.name,
                    )
                ),
                artifact_key=state.artifact_key,
                artifact_path=state.artifact_path,
                artifact_status=state.status,
            )
            continue
        owner_unit_id = unit.owner_unit_id
        if owner_unit_id is not None and owner_unit_id not in accepted_ids:
            owner_status = _owner_status(project, resolver, unit)
            if owner_status not in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}:
                builder.block(
                    unit.unit_id,
                    problem=_problem(
                        DERIVATIVE_OWNER_SHEET_MISSING,
                        f"角色 '{unit.owner}' 还没有可用的资产图，衍生 '{unit.name}' 无从生成",
                        GenerationAction.GENERATE_DEPENDENCY,
                        name=unit.owner,
                    ),
                    artifact_key=state.artifact_key,
                    artifact_path=state.artifact_path,
                    artifact_status=state.status,
                )
                continue
        elif owner_unit_id is not None:
            dependencies[unit.unit_id] = owner_unit_id
        accepted.append(state)
        accepted_ids.add(unit.unit_id)

    return AssetSheetBatchPlan(
        selection=selection.mode,
        targets=tuple(accepted),
        units=by_id,
        dependencies=dependencies,
        generating=frozenset(),
        preflight=builder.build(),
        states={state.unit_id: state for state in accepted},
    )


def _owner_status(
    project: Mapping[str, Any], resolver: ArtifactCurrencyResolver, unit: AssetSheetUnit
) -> ArtifactStatus | None:
    assert unit.owner is not None
    spec = ASSET_SPECS[unit.asset_type]
    bucket = project.get(spec.bucket_key)
    entry = bucket.get(unit.owner) if isinstance(bucket, Mapping) else None
    sheet = entry.get(spec.sheet_field) if isinstance(entry, Mapping) else None
    status, _blocker = observe_artifact_status(
        resolver=resolver,
        key=ArtifactKey.asset_sheet(unit.asset_type, asset_name_comparison_key(unit.owner)),
        artifact_path=sheet,
    )
    return status


def load_episode_script(projects: ProjectManager, project_name: str, episode_id: int) -> dict[str, Any] | None:
    """集范围要读的正式脚本；集不存在抛 :class:`AssetBatchEpisodeNotFound`，还没有正式脚本返回 ``None``。"""

    project = projects.load_project(project_name)
    entry = find_episode(project, episode_id)
    if entry is None:
        raise AssetBatchEpisodeNotFound(episode_id)
    script_file = entry.get("script_file")
    if not isinstance(script_file, str) or not script_file:
        return None
    try:
        return projects.load_script_readonly(project_name, script_file)
    except FileNotFoundError:
        return None


async def active_asset_unit_ids(
    queue: GenerationQueue,
    *,
    project_name: str,
    units: Sequence[AssetSheetUnit],
    user_id: str,
) -> frozenset[str]:
    """这些单元里已有活动任务（排队或执行中）的那些。"""

    by_task_type: dict[str, dict[str, str]] = {}
    for unit in units:
        task_type = DERIVATIVE_TASK_TYPE if unit.is_derivative else unit.asset_type
        by_task_type.setdefault(task_type, {})[unit.name] = unit.unit_id
    active: set[str] = set()
    for task_type, names in by_task_type.items():
        tasks = await queue.get_active_tasks_for_resources(
            project_name=project_name,
            task_type=task_type,
            resource_ids=list(names),
            user_id=user_id,
        )
        for task in tasks:
            unit_id = names.get(str(task.get("resource_id")))
            if unit_id is not None:
                active.add(unit_id)
    return frozenset(active)


async def load_asset_sheet_batch_plan(
    projects: ProjectManager,
    queue: GenerationQueue,
    *,
    project_name: str,
    scope: AssetSheetScope,
    user_id: str,
) -> tuple[dict[str, Any], AssetSheetBatchPlan]:
    """读取项目现状并规划一批；返回规划所依据的项目载荷与规划结果。"""

    def _read() -> tuple[dict[str, Any], ArtifactCurrencyResolver, frozenset[ReferencedAsset] | None]:
        project = projects.load_project(project_name)
        resolver = active_artifact_currency_resolver(projects.get_project_path(project_name), project)
        referenced = None
        if scope.episode_id is not None:
            referenced = episode_referenced_assets(
                project, load_episode_script(projects, project_name, scope.episode_id)
            )
        return project, resolver, referenced

    project, resolver, referenced = await asyncio.to_thread(_read)
    draft = await asyncio.to_thread(plan_asset_sheet_batch, project, resolver, scope, referenced=referenced)
    active = await active_asset_unit_ids(
        queue,
        project_name=project_name,
        units=[draft.units[unit_id] for unit_id in draft.target_ids],
        user_id=user_id,
    )
    return project, replace(draft, generating=active)


def asset_sheet_statuses(project: Mapping[str, Any], resolver: ArtifactCurrencyResolver | None) -> list[dict[str, Any]]:
    """项目里每张资产图（含衍生）的产物清单状态与是否缺描述，供卡片状态与画廊筛选。

    状态只取产物清单的判定：登记了文件却读不到的判 missing（待生成），``blocked`` 是清单本身
    读不出，原样报告。``resolver`` 为 ``None`` 表示整份清单读不出，每张图都报告 ``blocked``。
    """

    rows: list[dict[str, Any]] = []
    for unit in project_asset_sheet_units(project):
        status, _blocker = (
            (ArtifactStatus.BLOCKED, None)
            if resolver is None
            else observe_artifact_status(
                resolver=resolver,
                key=unit.candidate.artifact_key,
                artifact_path=unit.candidate.artifact_path,
            )
        )
        owner, derivative = split_derivative_artifact_id(unit.name) if unit.is_derivative else (unit.name, None)
        rows.append(
            {
                "unit_id": unit.unit_id,
                "asset_type": unit.asset_type,
                "name": owner,
                "derivative": derivative,
                "status": (status or ArtifactStatus.MISSING).value,
                "description_missing": unit.description is None,
                "image_to_image": unit.image_to_image,
            }
        )
    return rows


async def submit_asset_sheet_batch(
    plan: AssetSheetBatchPlan,
    *,
    project_name: str,
    queue: GenerationQueue,
    source: str,
    user_id: str,
) -> tuple[GenerationBatchReadModel, list[EnqueuedTask]]:
    """把一次规划作为一个持久批次提交，不等待结果；Web 入口据返回的成员任务跟踪整批终态。"""

    requested, blocked = build_generation_batch_admission(
        preflight=plan.preflight,
        pending_ids=plan.target_ids,
        states=plan.states,
        dependencies=plan.dependencies,
    )
    batch, enqueued, _failures = await submit_generation_batch(
        project_name=project_name,
        operation=ASSET_BATCH_OPERATION,
        requested=requested,
        blocked=blocked,
        specs=plan.task_specs(source=source),
        source=source,
        user_id=user_id,
        queue=queue,
    )
    return batch, enqueued


__all__ = [
    "ASSET_BATCH_OPERATION",
    "DESCRIPTION_REQUIRED_CODES",
    "AssetBatchEpisodeNotFound",
    "AssetSheetBatchPlan",
    "AssetSheetScope",
    "AssetSheetUnit",
    "active_asset_unit_ids",
    "asset_name_of",
    "asset_sheet_statuses",
    "asset_unit_id",
    "load_asset_sheet_batch_plan",
    "load_episode_script",
    "plan_asset_sheet_batch",
    "project_asset_sheet_units",
    "submit_asset_sheet_batch",
]
