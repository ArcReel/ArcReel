"""Host-independent ArcReel tool handlers and their typed call contract."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lib.config.resolver import ConfigResolver
from lib.project_manager import ProjectManager
from lib.script_batch_edit import (
    ScriptBatchEditCommand,
    ScriptBatchEditLocation,
    ScriptBatchEditor,
    ScriptBatchEditProblem,
    ScriptBatchEditResult,
    script_revision,
)
from lib.script_editor import (
    ScriptEditError,
    insert_segment,
    patch_field,
    remove_segment,
    resolve_items,
    split_segment,
)
from lib.workflow_plan import WorkflowPlan, WorkflowPlanRequest
from lib.workflow_state import WorkflowRequestError
from server.services.video_caps import annotate_reference_unit_tiers
from server.services.workflow_planner import WorkflowPlanner


@dataclass(frozen=True, slots=True)
class ToolRequest[RequestT]:
    value: RequestT


@dataclass(frozen=True, slots=True)
class ProjectScope:
    project_name: str
    projects_root: Path


@dataclass(frozen=True, slots=True)
class CallerContext:
    user_id: str
    source: Literal["embedded", "mcp"]


@dataclass(frozen=True, slots=True)
class Services:
    projects: ProjectManager
    workflow_planner: WorkflowPlanner
    capabilities: ConfigResolver


@dataclass(frozen=True, slots=True)
class ToolProblem:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ToolOutcome[ResultT]:
    value: ResultT | None = None
    problem: ToolProblem | None = None


class PatchUpdateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["update"]
    id: str = Field(min_length=1)
    fields: dict[str, Any] = Field(min_length=1)


class PatchInsertOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["insert"]
    after_id: str = Field(min_length=1)
    item: dict[str, Any]


class PatchRemoveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["remove"]
    id: str = Field(min_length=1)


class PatchSplitOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["split"]
    id: str = Field(min_length=1)
    parts: list[dict[str, Any]] = Field(min_length=2)


PatchEpisodeScriptOperation = Annotated[
    PatchUpdateOperation | PatchInsertOperation | PatchRemoveOperation | PatchSplitOperation,
    Field(discriminator="op"),
]


class PatchEpisodeScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(min_length=1)
    base_revision: str = Field(pattern=r"^sha256-v1:[0-9a-f]{64}$")
    operations: list[PatchEpisodeScriptOperation] = Field(min_length=1)


async def get_workflow_plan(
    request: ToolRequest[WorkflowPlanRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[WorkflowPlan]:
    try:
        plan = await services.workflow_planner.get_plan(scope.project_name, request.value)
    except WorkflowRequestError as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except Exception as exc:  # noqa: BLE001
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_workflow_plan 失败: {exc}"))
    return ToolOutcome(value=plan)


async def get_video_capabilities(
    _request: ToolRequest[None],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    try:
        project = services.projects.load_project(scope.project_name)
        payload = await services.capabilities.video_capabilities_for_project(project)
        await annotate_reference_unit_tiers(payload, project, config_resolver=services.capabilities)
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("project_not_found", f"项目未找到或缺 project.json: {exc}"))
    except ValueError as exc:
        return ToolOutcome(problem=ToolProblem("capabilities_unresolved", f"无法解析视频模型能力: {exc}"))
    except Exception as exc:  # noqa: BLE001
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_video_capabilities 失败: {exc}"))
    return ToolOutcome(value=payload)


def _script_edit_failure(
    request: PatchEpisodeScriptRequest,
    current: dict[str, Any],
    *,
    code: str,
    reason: str,
    next_action: str,
    operation_index: int | None = None,
    unit_id: str | None = None,
    location: tuple[str | int, ...] = (),
) -> ScriptBatchEditResult:
    revision = script_revision(current)
    episode = current.get("episode")
    return ScriptBatchEditResult(
        success=False,
        script=request.script,
        episode=episode if isinstance(episode, int) and not isinstance(episode, bool) else None,
        before_revision=revision,
        revision=revision,
        problems=(
            ScriptBatchEditProblem(
                code=code,
                operation_index=operation_index,
                unit_id=unit_id,
                locations=(ScriptBatchEditLocation(path=location),) if location else (),
                reason=reason,
                next_action=next_action,
            ),
        ),
    )


def _project_patch_operations(
    script: dict[str, Any],
    operations: list[PatchEpisodeScriptOperation],
) -> tuple[list[dict[str, Any]], list[int], frozenset[int]]:
    """Project public structural ops onto the existing transactional command."""
    preview = copy.deepcopy(script)
    projected: list[dict[str, Any]] = []
    source_indexes: list[int] = []
    fresh_insert_indexes: set[int] = set()

    def append(operation: dict[str, Any], source_index: int, *, fresh_insert: bool = False) -> None:
        if fresh_insert:
            fresh_insert_indexes.add(len(projected))
        projected.append(operation)
        source_indexes.append(source_index)

    for index, operation in enumerate(operations):

        def apply(
            action: Callable[[], Any],
            *,
            location: tuple[str | int, ...],
            unit_id: str | None = None,
        ) -> Any:
            try:
                return action()
            except ScriptEditError as exc:
                exc.params.update(operation_index=index, location=location, unit_id=unit_id)
                raise

        if isinstance(operation, PatchUpdateOperation):
            item_id = operation.id
            for field, value in operation.fields.items():
                apply(
                    lambda field=field, value=value: patch_field(preview, item_id, field, value),
                    location=("fields", *field.split(".")),
                    unit_id=item_id,
                )
            append(operation.model_dump(mode="python"), index)
            continue

        if isinstance(operation, PatchInsertOperation):
            insert_after_id = operation.after_id
            new_item = operation.item
            apply(
                lambda: insert_segment(preview, insert_after_id, new_item),
                location=("after_id",),
                unit_id=insert_after_id,
            )
            items, id_field, _kind = resolve_items(preview)
            anchor = next(i for i, item in enumerate(items) if str(item.get(id_field)) == insert_after_id)
            append(
                {"op": "insert_after", "after_id": insert_after_id, "item": copy.deepcopy(items[anchor + 1])},
                index,
                fresh_insert=True,
            )
            continue

        if isinstance(operation, PatchRemoveOperation):
            item_id = operation.id
            apply(lambda: remove_segment(preview, item_id), location=("id",), unit_id=item_id)
            append(operation.model_dump(mode="python"), index)
            continue

        item_id = operation.id
        parts = operation.parts
        items, id_field, _kind = resolve_items(preview)
        original_index = next(
            (i for i, item in enumerate(items) if str(item.get(id_field)) == item_id),
            None,
        )
        if original_index is None:
            raise ScriptEditError(
                f"未找到 id={item_id!r} 的分镜",
                operation_index=index,
                location=("id",),
                unit_id=item_id,
            )
        previous_id = str(items[original_index - 1].get(id_field)) if original_index else None
        apply(
            lambda: split_segment(preview, item_id, parts),
            location=("parts",),
            unit_id=item_id,
        )
        items, id_field, _kind = resolve_items(preview)
        anchor = next(i for i, item in enumerate(items) if str(item.get(id_field)) == item_id)
        generated = items[anchor : anchor + len(parts)]
        append({"op": "remove", "id": item_id}, index)
        split_after_id = previous_id
        for part_index, item in enumerate(generated):
            append(
                {"op": "insert_after", "after_id": split_after_id, "item": copy.deepcopy(item)},
                index,
                fresh_insert=part_index > 0,
            )
            split_after_id = str(item[id_field])

    return projected, source_indexes, frozenset(fresh_insert_indexes)


def _remap_operation_indexes(result: ScriptBatchEditResult, source_indexes: list[int]) -> ScriptBatchEditResult:
    if not result.problems:
        return result
    remapped: list[ScriptBatchEditProblem] = []
    for problem in result.problems:
        internal_index = problem.operation_index
        public_index = (
            source_indexes[internal_index]
            if internal_index is not None and 0 <= internal_index < len(source_indexes)
            else internal_index
        )
        locations: list[ScriptBatchEditLocation] = []
        for location in problem.locations:
            path = location.path
            if len(path) >= 2 and path[0] == "operations" and isinstance(path[1], int):
                path = (path[0], public_index if public_index is not None else path[1], *path[2:])
            locations.append(location.model_copy(update={"path": path}))
        remapped.append(problem.model_copy(update={"operation_index": public_index, "locations": tuple(locations)}))
    return result.model_copy(update={"problems": tuple(remapped)})


async def patch_episode_script(
    request: ToolRequest[PatchEpisodeScriptRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ScriptBatchEditResult]:
    try:
        current = services.projects.load_script(scope.project_name, request.value.script)
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("script_not_found", str(exc)))
    except Exception as exc:  # noqa: BLE001
        return ToolOutcome(problem=ToolProblem("internal_error", f"patch_episode_script 失败: {exc}"))

    if request.value.base_revision != script_revision(current):
        return ToolOutcome(
            value=_script_edit_failure(
                request.value,
                current,
                code="revision_conflict",
                reason="revision_mismatch",
                next_action="refresh_script",
            )
        )

    try:
        projected, source_indexes, fresh_insert_indexes = _project_patch_operations(current, request.value.operations)
    except ScriptEditError as exc:
        latest = services.projects.load_script(scope.project_name, request.value.script)
        if request.value.base_revision != script_revision(latest):
            return ToolOutcome(
                value=_script_edit_failure(
                    request.value,
                    latest,
                    code="revision_conflict",
                    reason="revision_mismatch",
                    next_action="refresh_script",
                )
            )
        raw_operation_index = exc.params.get("operation_index")
        operation_index = raw_operation_index if isinstance(raw_operation_index, int) else None
        raw_location = exc.params.get("location")
        operation_location = raw_location if isinstance(raw_location, tuple) else ()
        raw_unit_id = exc.params.get("unit_id")
        unit_id = raw_unit_id if isinstance(raw_unit_id, str) else None
        return ToolOutcome(
            value=_script_edit_failure(
                request.value,
                current,
                code="operation_invalid",
                reason="operation_invalid",
                next_action="fix_operation",
                operation_index=operation_index,
                unit_id=unit_id,
                location=("operations", operation_index, *operation_location) if operation_index is not None else (),
            )
        )

    command = ScriptBatchEditCommand.model_validate(
        {
            "script": request.value.script,
            "expected_revision": request.value.base_revision,
            "operations": projected,
        }
    )
    result = ScriptBatchEditor(services.projects).execute(
        scope.project_name,
        command,
        fresh_insert_indexes=fresh_insert_indexes,
    )
    return ToolOutcome(value=_remap_operation_indexes(result, source_indexes))


__all__ = [
    "CallerContext",
    "PatchEpisodeScriptRequest",
    "ProjectScope",
    "Services",
    "ToolOutcome",
    "ToolProblem",
    "ToolRequest",
    "get_video_capabilities",
    "get_workflow_plan",
    "patch_episode_script",
]
