"""Guard the project asset identity transactions shared by Web and Agent."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.generation.asset_admission import AssetTarget, idle_asset_write
from lib.infra.async_thread import run_sync_transaction
from lib.project.asset_merge import AssetMergeReport
from lib.project.asset_rename import AssetRenameReport
from lib.project.asset_types import asset_name_comparison_key, resolve_asset_type, validate_asset_name
from lib.project.project_manager import ProjectManager


async def rename_project_asset(
    manager: ProjectManager,
    project_name: str,
    table: str,
    old_name: str,
    new_name: str,
    *,
    dry_run: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AssetRenameReport:
    asset_type = resolve_asset_type(table)
    targets = (
        ()
        if dry_run
        else (
            AssetTarget(asset_type, old_name, "whole_asset"),
            AssetTarget(asset_type, validate_asset_name(new_name), "whole_asset"),
        )
    )
    async with idle_asset_write(project_name, *targets, session_factory=session_factory):
        return await run_sync_transaction(
            manager.rename_asset, project_name, table, old_name, new_name, dry_run=dry_run
        )


async def merge_project_asset(
    manager: ProjectManager,
    project_name: str,
    table: str,
    source: str,
    target: str,
    *,
    as_derivative: bool = False,
    dry_run: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AssetMergeReport:
    asset_type = resolve_asset_type(table)
    targets = (
        ()
        if dry_run
        else tuple(AssetTarget(asset_type, asset_name_comparison_key(name), "whole_asset") for name in (source, target))
    )
    async with idle_asset_write(project_name, *targets, session_factory=session_factory):
        return await run_sync_transaction(
            manager.merge_asset, project_name, table, source, target, as_derivative=as_derivative, dry_run=dry_run
        )


async def delete_project_asset(manager: ProjectManager, project_name: str, table: str, name: str) -> None:
    target = AssetTarget(resolve_asset_type(table), name, "whole_asset")
    async with idle_asset_write(project_name, target):
        await run_sync_transaction(manager.delete_asset, project_name, table, name)
