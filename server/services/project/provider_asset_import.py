"""Select existing provider-library images: retrieve content, save new image versions as finished manual uploads, and establish links.

All four asset-image types and character derivatives support import through their respective manual-upload paths:
``install_manual_asset_sheet_upload`` for assets, ``install_manual_derivative_upload`` for derivatives (Web derivative
upload’s ``commit_manual_derivative_upload``). Both share link creation and rollback here.

Each import has only two outcomes:

- Success: image, metadata pointer, ``manual_upload`` version, artifact registration and link take effect together.
  The link belongs to this new content version and is selected as available.
- Failure: any query, download, validation or formal-write failure leaves the old selection/history unchanged, with no available link.

Persist links in the formal-write commit hook, last after image/version/registration writes; database failure rolls back
the whole formal write. Finalization may still fail after database success. Then rolled-back history lacks the linked content
identity, preventing any query from selecting it; make a best-effort attempt to delete the orphan link.

Saved content matches the provider asset: asset images retain downloaded bytes. Derivative canonical paths use ``.png``;
retain PNG unchanged, and repackage decoded JPEG/WebP pixels as PNG without resizing or lossy compression. New versions use
formal submission and global local/object-storage settings like manual uploads. Without confirmed remote persistence,
publish no version, run no hook and create no link."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from lib.asset_library import links as link_service
from lib.asset_library.importing import ProviderImage, fetch_provider_image
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.infra.api_errors import BadRequestError, NotFoundError, UnprocessableError
from lib.infra.async_thread import EventLoopBridge, run_noninterruptible_async, run_sync_transaction
from lib.infra.image_utils import normalize_storyboard_upload
from lib.material.formal_objects import formal_commit
from lib.material.storage import ContentVersion
from lib.project.asset_derivatives import split_derivative_artifact_id
from lib.project.asset_types import ASSET_SPECS, resolve_asset_key, validate_asset_name
from lib.project.project_change_hints import ProjectChangeSource, project_change_source
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from server.services.currency.upload_finalize import (
    install_manual_asset_sheet_upload,
    install_manual_derivative_upload,
    resolve_derivative_upload_target,
)

logger = logging.getLogger(__name__)

#: Importable asset-image version-history names → asset types. See ``CHARACTER_DERIVATIVE_RESOURCE_TYPE`` for derivatives.
IMPORTABLE_ASSET_TYPES: dict[str, str] = {spec.bucket_key: asset_type for asset_type, spec in ASSET_SPECS.items()}

_NOT_FOUND_KEYS = {
    "character": "character_not_found",
    "scene": "project_scene_not_found",
    "prop": "prop_not_found",
    "product": "product_not_found",
}

type _CommitHook = Callable[[ContentVersion], None]
#: Persist retrieved images as finished manual uploads, calling the hook last in the same commit; concurrent pre-write deletion raises NotFoundError.
type _Install = Callable[[ProviderImage, _CommitHook], Awaitable[None]]


@dataclass(frozen=True)
class ImportedProviderAsset:
    content_version: ContentVersion
    link: ProviderAssetLink


def _require_asset(manager: ProjectManager, project_name: str, asset_type: str, name: str) -> None:
    try:
        project = manager.load_project(project_name)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    if resolve_asset_key(project.get(ASSET_SPECS[asset_type].bucket_key), name) is None:
        raise NotFoundError(_NOT_FOUND_KEYS[asset_type], name=name)


def _derivative_png(content: bytes) -> bytes:
    """PNG for derivative persistence: return canonical PNG unchanged; convert other formats at original pixel size without resizing."""
    try:
        return normalize_storyboard_upload(content, max_long_edge=None)
    except ValueError as exc:
        raise UnprocessableError("provider_asset_import_invalid_image") from exc


def _parse_target(resource_type: str, resource_id: str) -> tuple[str, ...]:
    """Validate resource type/name: return (asset name,) for assets or (base name, derivative name) for derivatives."""
    if resource_type != CHARACTER_DERIVATIVE_RESOURCE_TYPE and resource_type not in IMPORTABLE_ASSET_TYPES:
        raise BadRequestError("unsupported_resource_type", resource_type=resource_type)
    try:
        if resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE:
            return tuple(validate_asset_name(part) for part in split_derivative_artifact_id(resource_id))
        return (validate_asset_name(resource_id),)
    except ValueError as exc:
        raise BadRequestError("asset_invalid_name", name=resource_id) from exc


async def _asset_sheet_install(
    manager: ProjectManager, project_name: str, asset_type: str, name: str, change_source: ProjectChangeSource
) -> _Install:
    await asyncio.to_thread(_require_asset, manager, project_name, asset_type, name)

    async def install(image: ProviderImage, on_commit: _CommitHook) -> None:
        # The download boundary validated images; retain original bytes so versions match linked provider content.
        sheet_path = f"{ASSET_SPECS[asset_type].subdir}/{name}{image.suffix}"

        def _install() -> ContentVersion | None:
            with project_change_source(change_source):
                return install_manual_asset_sheet_upload(
                    project_manager=manager,
                    project_name=project_name,
                    asset_type=asset_type,
                    name=name,
                    sheet_path=sheet_path,
                    content=image.content,
                    original_filename=image.asset.name or None,
                    on_commit=on_commit,
                    require_asset=True,
                )

        # Assets may disappear during retrieval; recheck under the project lock and write nothing if absent. New versions follow global storage settings.
        async with formal_commit(project_name):
            installed = await run_sync_transaction(_install)
        if installed is None:
            raise NotFoundError(_NOT_FOUND_KEYS[asset_type], name=name)

    return install


async def _derivative_install(
    manager: ProjectManager,
    project_name: str,
    owner_name: str,
    derivative_name: str,
    change_source: ProjectChangeSource,
) -> _Install:
    upload = await asyncio.to_thread(
        resolve_derivative_upload_target, manager, project_name, owner_name, derivative_name
    )

    async def install(image: ProviderImage, on_commit: _CommitHook) -> None:
        png_bytes = await asyncio.to_thread(_derivative_png, image.content)
        with project_change_source(change_source):
            await install_manual_derivative_upload(
                project_name=project_name,
                upload=upload,
                png_bytes=png_bytes,
                original_filename=image.asset.name or None,
                project_manager=manager,
                on_commit=on_commit,
            )

    return install


async def _discard_orphan(session: AsyncSession, link: ProviderAssetLink) -> None:
    try:
        await session.rollback()
        orphan = await session.get(ProviderAssetLink, link.id)
        if orphan is not None:
            await session.delete(orphan)
            await session.commit()
    except Exception:
        # Remaining records point to nonexistent content versions and cannot be selected; only diagnostics are affected, not images or other links.
        logger.warning("导入失败后未能删除关联记录 %s", link.id, exc_info=True)


async def import_provider_asset(
    session: AsyncSession,
    *,
    project_name: str,
    resource_type: str,
    resource_id: str,
    account_id: int,
    asset_id: str,
    manager: ProjectManager | None = None,
    change_source: ProjectChangeSource = "webui",
) -> ImportedProviderAsset:
    """Import an available account-scoped library image as a new asset/character-derivative version, create an available link for it, and commit.

    ``resource_type`` uses version-history names; character derivatives use ``character_derivatives`` with base/derivative
    ``resource_id``. ``manager`` defaults to the process-level project manager; ``change_source`` identifies formal writes
    as ``webui`` for Web or ``worker`` for Agent tools.

    Raises:
        BadRequestError: Unsupported import resource type or invalid name.
        NotFoundError: Missing project, asset, derivative or library account.
        UnprocessableError: Unavailable/nonimage material or nonimportable image content.
        BadGatewayError: Failed control-plane request or unretrievable material content."""
    names = _parse_target(resource_type, resource_id)
    account = await AssetLibraryRepository(session).get_account(account_id)
    if account is None:
        raise NotFoundError("asset_library_account_not_found")
    manager = manager or get_project_manager()
    if resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE:
        install = await _derivative_install(manager, project_name, names[0], names[1], change_source)
    else:
        install = await _asset_sheet_install(
            manager, project_name, IMPORTABLE_ASSET_TYPES[resource_type], names[0], change_source
        )

    image = await fetch_provider_image(account, asset_id)

    bridge = EventLoopBridge.capture()
    recorded: list[ImportedProviderAsset] = []

    async def _record_link(content_version: ContentVersion) -> ImportedProviderAsset:
        link = await link_service.record_imported_link(
            session, project_name=project_name, content_version=content_version, account=account, asset=image.asset
        )
        await session.commit()
        return ImportedProviderAsset(content_version=content_version, link=link)

    def _on_commit(content_version: ContentVersion) -> None:
        recorded.append(bridge.run(_record_link(content_version)))

    try:
        # Settle formal writes through return together so cancellation cannot send an effective link into orphan cleanup.
        await run_noninterruptible_async(install(image, _on_commit))
    except BaseException:
        if recorded:
            await _discard_orphan(session, recorded[0].link)
        else:
            await session.rollback()
        raise
    if not recorded:
        raise RuntimeError("provider asset import committed without recording its link")
    return recorded[0]


__all__ = ["IMPORTABLE_ASSET_TYPES", "ImportedProviderAsset", "import_provider_asset"]
