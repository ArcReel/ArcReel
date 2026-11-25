"""Project asset admission and occupancy (ADR 0126)."""

import json
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.task import BatchTask, Task
from lib.db.repositories.task_repo import ACTIVE_TASK_STATUSES
from lib.generation.generation_admission import admission_file_lock
from lib.infra.api_errors import ConflictError, NotFoundError
from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE, derivative_table, split_derivative_artifact_id
from lib.project.asset_types import BUCKET_KEY, normalize_asset_name, resolve_asset_key
from lib.project.project_manager import ProjectManager, get_project_manager

AssetDimension = Literal["image", "derivative_image", "voice", "whole_asset"]
ASSET_RESOURCE_BUSY_CODE = "asset_resource_busy"


class AssetResourceBusy(ConflictError):
    def __init__(self) -> None:
        super().__init__(ASSET_RESOURCE_BUSY_CODE)


@dataclass(frozen=True)
class AssetTarget:
    asset_type: str
    name: str
    dimension: AssetDimension
    derivative: str | None = None

    @property
    def owner(self) -> tuple[str, str]:
        return self.asset_type, normalize_asset_name(self.name)

    def overlaps(self, active: "AssetTarget") -> bool:
        if self.owner != active.owner:
            return False
        if self.dimension == "whole_asset":
            return True
        if "voice" in (self.dimension, active.dimension):
            return self.dimension == active.dimension
        if self.derivative is not None and active.derivative is not None:
            return normalize_asset_name(self.derivative) == normalize_asset_name(active.derivative)
        return True

    def require_host(self, project_name: str, manager: ProjectManager | None) -> None:
        try:
            project = (manager or get_project_manager()).load_project(project_name)
        except FileNotFoundError as exc:
            raise NotFoundError("project_not_found", name=project_name) from exc
        bucket = project.get(BUCKET_KEY[self.asset_type], {})
        key = resolve_asset_key(bucket, self.name)
        if key is None:
            raise NotFoundError("asset_not_found", name=self.name)
        if self.derivative is not None and resolve_asset_key(derivative_table(bucket[key]), self.derivative) is None:
            raise NotFoundError("asset_derivative_not_found", name=self.derivative)


def asset_task_target(task_type: str, resource_type: str | None, resource_id: str) -> AssetTarget | None:
    kind = resource_type if task_type == "image_edit" else task_type
    if kind == DERIVATIVE_TASK_TYPE:
        owner, derivative = split_derivative_artifact_id(resource_id)
        return AssetTarget("character", owner, "derivative_image", derivative)
    if task_type == "voice_sample":
        return AssetTarget("character", resource_id, "voice")
    if kind in BUCKET_KEY:
        return AssetTarget(kind, resource_id, "image")
    return None


@asynccontextmanager
async def asset_admission_locks(project_name: str, *targets: AssetTarget) -> AsyncGenerator[None]:
    project_name = ProjectManager.normalize_project_name(project_name)
    async with AsyncExitStack() as stack:
        for kind, name in sorted({target.owner for target in targets}):
            await stack.enter_async_context(admission_file_lock(project_name, kind, name))
        yield


async def require_asset_idle(
    session: AsyncSession,
    project_name: str,
    target: AssetTarget,
    *,
    new_task_id: str | None = None,
    parent_task_id: str | None = None,
    batch_id: str | None = None,
) -> None:
    project_name = ProjectManager.normalize_project_name(project_name)
    kinds = [target.asset_type, "image_edit"]
    if target.asset_type == "character":
        kinds.extend([DERIVATIVE_TASK_TYPE, "voice_sample"])
    legacy_edit_payload = case(((Task.task_type == "image_edit") & Task.resource_type.is_(None), Task.payload_json))
    matching_project_names = Task.project_name.contains(project_name, autoescape=True)
    query = select(
        Task.project_name, Task.task_id, Task.task_type, Task.resource_type, Task.resource_id, legacy_edit_payload
    ).where(
        matching_project_names,
        Task.status.in_(ACTIVE_TASK_STATUSES),
        Task.task_type.in_(kinds),
    )
    if new_task_id is not None:
        query = query.where(Task.task_id != new_task_id)
    for task_project, task_id, kind, resource_type, resource_id, legacy_payload in await session.execute(query):
        if task_project.strip() != project_name:
            continue
        if legacy_payload:
            resource_type = json.loads(legacy_payload).get("resource_type")
        active = asset_task_target(kind, resource_type, resource_id)
        if active is None or not target.overlaps(active):
            continue
        if kind == "character" and task_id == parent_task_id and batch_id is not None:
            member = await session.scalar(
                select(BatchTask.task_id).where(BatchTask.batch_id == batch_id, BatchTask.task_id == parent_task_id)
            )
            if member is not None:
                continue
        raise AssetResourceBusy()


async def check_asset_write(
    project_name: str, *targets: AssetTarget, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> None:
    """Check occupancy while the caller holds the admission locks for all targets."""
    from lib.db import async_session_factory

    async with (session_factory or async_session_factory)() as session:
        for target in targets:
            await require_asset_idle(session, project_name, target)


@asynccontextmanager
async def idle_asset_write(
    project_name: str, *targets: AssetTarget, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> AsyncGenerator[None]:
    if not targets:
        yield
        return
    async with asset_admission_locks(project_name, *targets):
        await check_asset_write(project_name, *targets, session_factory=session_factory)
        yield
