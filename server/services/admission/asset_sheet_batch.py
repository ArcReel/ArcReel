"""Asset-image batches: service command shared by Web episode/gallery actions and Agent ``list_pending_assets`` / ``generate_assets``.

Select targets according to request scope:

- **Type scope**, gallery type pages: this type throughout the project, including character derivatives.
- **Episode scope**, episode next steps / Agent ``episode_id``: referenced assets and derivatives, computed solely by
  :func:`lib.project.episode_asset_references.episode_referenced_assets`.
- **Named selection**, Agent ``names``: explicit targets regenerate even with existing images.

Without names, select only Manifest missing targets; stale/current images are excluded. Report missing descriptions
per target without submission. Bases and derivatives share a batch: submit derivatives directly with usable base images;
if the base is in the batch, depend on its task and execute only after success. Base failure means no derivative provider
submission. Other cases, such as missing base descriptions or absent batch bases, report the base image as not generated.

Planning is read-only: selection, admission and cost estimation create no tasks. Callers submit through the batch entry point."""

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

#: Missing-description codes for bases/derivatives, matching single-image generation refusals.
ASSET_DESCRIPTION_REQUIRED = "asset_description_required"
DERIVATIVE_DESCRIPTION_REQUIRED = "derivative_description_required"
DERIVATIVE_OWNER_SHEET_MISSING = "derivative_owner_sheet_missing"
DESCRIPTION_REQUIRED_CODES = frozenset({ASSET_DESCRIPTION_REQUIRED, DERIVATIVE_DESCRIPTION_REQUIRED})


class AssetBatchEpisodeNotFound(LookupError):
    """The episode scope identifies an episode absent from the ledger."""

    def __init__(self, episode_id: int) -> None:
        self.episode_id = episode_id
        super().__init__(f"episode {episode_id} does not exist in this project")


def asset_unit_id(asset_type: str, name: str) -> str:
    """Asset-image unit ID: ``<类型>/<名字>``; derivatives use ``本体/衍生`` names.

    Requests may span four asset types with duplicate names across types; the per-ID contract requires globally unique batch IDs."""

    return f"{asset_type}/{name}"


def asset_name_of(unit_id: str) -> str:
    """The name in a unit ID, ``本体/衍生`` for derivatives; inverse of :func:`asset_unit_id`."""

    return unit_id.split("/", 1)[1]


@dataclass(frozen=True, slots=True)
class AssetSheetScope:
    """Batch scope: three mutually exclusive scopes, with omission selecting the whole project."""

    asset_type: str | None = None
    episode_id: int | None = None
    #: Selected names, ``本体/衍生`` for derivatives; requires ``asset_type``.
    names: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class AssetSheetUnit:
    """A generatable asset image: a base image or one derivative appearance’s image."""

    asset_type: str
    #: Actual persisted name, ``本体/衍生`` for derivatives.
    name: str
    #: Derivative’s persisted base name; ``None`` for bases.
    owner: str | None
    #: Trimmed description, appearance changes for derivatives; ``None`` if unfilled.
    description: str | None
    #: Whether originals are declared; assets with originals use image-to-image.
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
        """Canonical persisted asset-image path for filling missing paths in task results."""

        if self.owner is not None:
            owner, derivative = split_derivative_artifact_id(self.name)
            return derivative_sheet_relative_path(owner, derivative)
        return f"{ASSET_SPECS[self.asset_type].subdir}/{self.name}.png"

    @property
    def image_to_image(self) -> bool:
        """Derivatives edit base images; bases with declared originals use those originals as references. Both use image-to-image."""

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
    """All generatable project asset images in type order, each base immediately followed by its derivatives."""

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
    """Resolve selected names to canonical unit IDs; preserve unresolved spellings for per-item missing results."""

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
    """An asset-image batch plan: submission targets, per-item refusals/reuse and within-batch dependencies."""

    selection: GenerationSelectionMode
    #: Submission targets, bases before derivatives.
    targets: tuple[GenerationTargetState, ...]
    units: Mapping[str, AssetSheetUnit]
    #: Derivative unit → base unit in the same batch.
    dependencies: Mapping[str, str]
    #: Targets with active tasks; reuse those tasks at submission without charging again.
    generating: frozenset[str]
    preflight: GenerationBatchResult
    states: Mapping[str, GenerationTargetState] = field(default_factory=dict)

    @property
    def target_ids(self) -> list[str]:
        return [state.unit_id for state in self.targets]

    @property
    def new_task_ids(self) -> list[str]:
        """Targets creating new paid tasks, excluding those already generating."""

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
    """Select targets by scope and assess per-item admission; callers supply referenced assets for episode scopes."""

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
    """Formal script for episode scope; missing episodes raise :class:`AssetBatchEpisodeNotFound`, missing formal scripts return ``None``."""

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
    """Units among these that already have active queued/running tasks."""

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
    """Read current project state and plan a batch, returning the source project payload and plan."""

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
    """Manifest state and missing-description flags for every project asset image, including derivatives, for cards/gallery filters.

    Use Manifest decisions only: registered unreadable files are missing/pending; ``blocked`` means the Manifest itself
    is unreadable and is reported unchanged. ``resolver`` None means the entire Manifest is unreadable, blocking every image."""

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
    """Submit one plan as a durable batch without awaiting results; Web callers track terminal batch state through returned member tasks."""

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
