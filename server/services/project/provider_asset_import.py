"""从供应商素材库选择已有图片：取回实际内容，按手动上传成品保存为资产图的新版本，并建立关联。

一次导入只有两种结果：

- 成功：资产图、元数据指针、``manual_upload`` 版本、产物登记与关联记录一起生效，关联属于这次保存的
  新内容版本，以可用状态选定；
- 失败：查询、下载、校验或正式写入任一步失败，原选中版本与版本历史保持不变，也没有可用的关联。

关联记录在正式写入的提交钩子里落库：钩子在资产图、版本与登记都已写好之后最后执行，落库失败时
正式写入整体回滚。落库成功后正式写入的收尾步骤仍可能失败，此时回滚后的版本历史里没有这个内容版本，
关联指向的身份不再存在、不会被任何查询选中，这里再尽力删掉它。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from lib.asset_library import links as link_service
from lib.asset_library.importing import fetch_provider_image
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.infra.api_errors import BadRequestError, NotFoundError, UnprocessableError
from lib.infra.async_thread import EventLoopBridge, run_sync_transaction
from lib.infra.image_utils import normalize_uploaded_image
from lib.material.storage import ContentVersion
from lib.project.asset_types import ASSET_SPECS, resolve_asset_key, validate_asset_name
from lib.project.project_change_hints import ProjectChangeSource, project_change_source
from lib.project.project_manager import ProjectManager, get_project_manager
from server.services.currency.upload_finalize import install_manual_asset_sheet_upload

logger = logging.getLogger(__name__)

#: 可以导入的资源类型（版本历史里的名称）→ 资产类型。
IMPORTABLE_ASSET_TYPES: dict[str, str] = {spec.bucket_key: asset_type for asset_type, spec in ASSET_SPECS.items()}

_NOT_FOUND_KEYS = {
    "character": "character_not_found",
    "scene": "project_scene_not_found",
    "prop": "prop_not_found",
    "product": "product_not_found",
}


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


async def _discard_orphan(session: AsyncSession, link: ProviderAssetLink) -> None:
    try:
        await session.rollback()
        orphan = await session.get(ProviderAssetLink, link.id)
        if orphan is not None:
            await session.delete(orphan)
            await session.commit()
    except Exception:
        # 留下的记录指向不存在的内容版本，不会被选中；只影响诊断，不影响图片与其他关联
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
    """把账号范围里的一张可用图片素材导入为资产图的新版本，并为该版本建立可用关联。提交事务。

    ``manager`` 缺省为进程级项目管理器；``change_source`` 标记这次正式写入的来源（Web 为 ``webui``，
    Agent 工具为 ``worker``）。

    Raises:
        BadRequestError: 资源类型不支持导入，或资产名无效。
        NotFoundError: 项目、资产或素材库账号不存在。
        UnprocessableError: 素材不是可用的图片，或内容不是可导入的图片。
        BadGatewayError: 控制面请求失败，或取不回素材内容。
    """
    asset_type = IMPORTABLE_ASSET_TYPES.get(resource_type)
    if asset_type is None:
        raise BadRequestError("unsupported_resource_type", resource_type=resource_type)
    try:
        name = validate_asset_name(resource_id)
    except ValueError as exc:
        raise BadRequestError("asset_invalid_name", name=resource_id) from exc
    account = await AssetLibraryRepository(session).get_account(account_id)
    if account is None:
        raise NotFoundError("asset_library_account_not_found")
    manager = manager or get_project_manager()
    await asyncio.to_thread(_require_asset, manager, project_name, asset_type, name)

    image = await fetch_provider_image(account, asset_id)
    try:
        content, suffix = await asyncio.to_thread(normalize_uploaded_image, image.content, image.suffix)
    except ValueError as exc:
        raise UnprocessableError("provider_asset_import_invalid_image") from exc
    sheet_path = f"{ASSET_SPECS[asset_type].subdir}/{name}{suffix}"

    bridge = EventLoopBridge.capture()
    recorded: list[ProviderAssetLink] = []

    async def _record_link(content_version: ContentVersion) -> ProviderAssetLink:
        link = await link_service.record_imported_link(
            session, project_name=project_name, content_version=content_version, account=account, asset=image.asset
        )
        await session.commit()
        return link

    def _on_commit(content_version: ContentVersion) -> None:
        recorded.append(bridge.run(_record_link(content_version)))

    def _install() -> ContentVersion | None:
        with project_change_source(change_source):
            return install_manual_asset_sheet_upload(
                project_manager=manager,
                project_name=project_name,
                asset_type=asset_type,
                name=name,
                sheet_path=sheet_path,
                content=content,
                original_filename=image.asset.name or None,
                on_commit=_on_commit,
            )

    try:
        content_version = await run_sync_transaction(_install)
    except BaseException:
        if recorded:
            await _discard_orphan(session, recorded[0])
        else:
            await session.rollback()
        raise
    if content_version is None or not recorded:
        # 资产在写入前被并发删除：没有保存新版本，也没有建立关联
        raise NotFoundError(_NOT_FOUND_KEYS[asset_type], name=name)
    return ImportedProviderAsset(content_version=content_version, link=recorded[0])


__all__ = ["IMPORTABLE_ASSET_TYPES", "ImportedProviderAsset", "import_provider_asset"]
