"""从供应商素材库选择已有图片：取回实际内容，按手动上传成品保存为图片的新版本，并建立关联。

资产图（四类资产）与角色衍生图都可以导入，各自走自己的手动上传成品路径：资产图经
``install_manual_asset_sheet_upload``，衍生图经 ``install_manual_derivative_upload``（即 Web 衍生上传的
``commit_manual_derivative_upload``）。关联的建立与回滚两者共用本模块。

一次导入只有两种结果：

- 成功：图片、元数据指针、``manual_upload`` 版本、产物登记与关联记录一起生效，关联属于这次保存的
  新内容版本，以可用状态选定；
- 失败：查询、下载、校验或正式写入任一步失败，原选中版本与版本历史保持不变，也没有可用的关联。

关联记录在正式写入的提交钩子里落库：钩子在图片、版本与登记都已写好之后最后执行，落库失败时
正式写入整体回滚。落库成功后正式写入的收尾步骤仍可能失败，此时回滚后的版本历史里没有这个内容版本，
关联指向的身份不再存在、不会被任何查询选中，这里再尽力删掉它。

保存的内容与供应商素材一致：资产图保留下载到的原始字节；衍生图的规范路径固定为 ``.png``，PNG 原样保存，
JPEG 与 WebP 按解码后的原像素换成 PNG 封装，不缩放、不做有损压缩。新内容版本与手动上传一样经正式提交入口
按全局存储选择保存到本地或对象存储；远端没有确认保存时不发布版本，钩子不会执行，也就不建立关联。
"""

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

#: 可以导入的资产图类型（版本历史里的名称）→ 资产类型。角色衍生图另见 ``CHARACTER_DERIVATIVE_RESOURCE_TYPE``。
IMPORTABLE_ASSET_TYPES: dict[str, str] = {spec.bucket_key: asset_type for asset_type, spec in ASSET_SPECS.items()}

_NOT_FOUND_KEYS = {
    "character": "character_not_found",
    "scene": "project_scene_not_found",
    "prop": "prop_not_found",
    "product": "product_not_found",
}

type _CommitHook = Callable[[ContentVersion], None]
#: 把取回的图片按手动上传成品写入，最后在同一次提交里调用钩子。资源在写入前被并发删除时抛 NotFoundError。
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
    """衍生图要保存的 PNG：已是规范 PNG 时原样返回，其余格式按原像素转成 PNG，不缩放。"""
    try:
        return normalize_storyboard_upload(content, max_long_edge=None)
    except ValueError as exc:
        raise UnprocessableError("provider_asset_import_invalid_image") from exc


def _parse_target(resource_type: str, resource_id: str) -> tuple[str, ...]:
    """校验资源类型与名称：资产图返回 (资产名,)，衍生图返回 (本体名, 衍生名)。"""
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
        # 下载边界已校验图片；保留原始字节，确保此版本与关联的供应商素材是同一内容。
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

        # 取图期间资产可能被删除：项目锁内复核，不存在时什么也不写。新版本按全局存储选择保存
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
    """把账号范围里的一张可用图片素材导入为资产图或角色衍生图的新版本，并为该版本建立可用关联。提交事务。

    ``resource_type`` 为版本历史里的名称；角色衍生图为 ``character_derivatives``，``resource_id`` 为
    「本体名/衍生名」。``manager`` 缺省为进程级项目管理器；``change_source`` 标记这次正式写入的来源
    （Web 为 ``webui``，Agent 工具为 ``worker``）。

    Raises:
        BadRequestError: 资源类型不支持导入，或资源名无效。
        NotFoundError: 项目、资产、衍生或素材库账号不存在。
        UnprocessableError: 素材不是可用的图片，或内容不是可导入的图片。
        BadGatewayError: 控制面请求失败，或取不回素材内容。
    """
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
        # 正式写入到返回一并结算，避免取消把已生效的关联送进孤儿清理。
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
