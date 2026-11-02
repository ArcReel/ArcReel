"""供应商素材关联：内容版本在某个调用范围里对应的供应商素材，连同取得素材 ID 之前的入库任务。

一条关联记录（:class:`~lib.db.models.asset_library.ProviderAssetLink`）从提交入库起就存在，状态如下：

- ``pending``：已选定，等待中转上传与提交入库；
- ``submitting``：CreateAsset 请求已经或即将发出，进程在此中断时结果未知；
- ``processing``：已取得供应商素材 ID，供应商仍在处理；
- ``active``：供应商处理完成，只有这一状态可用于生成；
- ``failed``：入库失败，或 ArcReel 在提交前失败（此时供应商侧没有创建素材）；
- ``uncertain``：提交请求的结果未知，还没有素材 ID。不重新创建，由后台按提交时的名称查回。

**选定**：同一项目、同一内容版本、同一调用范围（账号、站点、区域、供应商项目）至多一条 ``selected``
记录，即生成时要用的关联或要等待的入库任务。选定只由用户的上传或解除关联改变；后台查询结果只更新状态
事实，不改变选定，所以入库期间改选了另一张图片，结果仍归属提交时的内容版本，已解除的关系也不会因
迟到的结果恢复。

生成（#3563）、导入已有素材（#3564）与 Agent 工具（#3565）都经本模块查询与改变关联，不在路由里各自实现。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.artifacts.version_manager import VersionManager
from lib.asset_library.bindings import AssetLibraryScope
from lib.db.base import utc_now
from lib.db.models.asset_library import AssetLibraryAccount, ProviderAssetLink
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.material.storage import ContentVersion, LocalMaterialStorage
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE

LinkState = Literal["pending", "submitting", "processing", "active", "failed", "uncertain"]

#: 还没有定论、后台仍会推进或查询的状态。
IN_FLIGHT_STATES: frozenset[str] = frozenset({"pending", "submitting", "processing", "uncertain"})

#: 可以上传到供应商素材库的图片资源类型：资产图与角色衍生图。
UPLOADABLE_RESOURCE_TYPES: frozenset[str] = frozenset(
    {"characters", "scenes", "props", "products", CHARACTER_DERIVATIVE_RESOURCE_TYPE}
)

#: 入库请求里素材名称的上限（供应商限制 64 个字符）。
_NAME_LIMIT = 64


@dataclass(frozen=True)
class UploadTarget:
    """一次上传冻结的内容版本：身份、摘要与持久定位，在提交请求时确定，之后不随改选变化。"""

    content_version: ContentVersion


def scope_of(link: ProviderAssetLink) -> AssetLibraryScope:
    """关联提交时的调用范围。"""
    return AssetLibraryScope(
        account_id=link.account_id,
        site=link.site,
        region=link.region,
        project_name=link.provider_project_name,
    )


def account_scope(account: AssetLibraryAccount) -> AssetLibraryScope:
    return AssetLibraryScope(
        account_id=account.id, site=account.site, region=account.region, project_name=account.project_name
    )


def is_usable(link: ProviderAssetLink) -> bool:
    """关联此刻能否用于生成：仍被选定、供应商处理完成且有素材 ID。"""
    return bool(link.selected and link.state == "active" and link.provider_asset_id)


def asset_uri(link: ProviderAssetLink) -> str | None:
    """可用关联在生成请求里的素材引用。"""
    return f"asset://{link.provider_asset_id}" if is_usable(link) else None


def _scope_filter(scope: AssetLibraryScope):
    return (
        ProviderAssetLink.account_id == scope.account_id,
        ProviderAssetLink.site == scope.site,
        ProviderAssetLink.region == scope.region,
        ProviderAssetLink.provider_project_name == scope.project_name,
    )


async def selected_link(
    session: AsyncSession, *, project_name: str, content_version_id: str, scope: AssetLibraryScope
) -> ProviderAssetLink | None:
    """内容版本在调用范围里当前选定的关联或入库任务；没有时返回 None，即该范围没有适用关联。"""
    stmt = select(ProviderAssetLink).where(
        ProviderAssetLink.project_name == project_name,
        ProviderAssetLink.content_version_id == content_version_id,
        ProviderAssetLink.selected.is_(True),
        *_scope_filter(scope),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def selected_links(
    session: AsyncSession, *, project_name: str, content_version_id: str
) -> list[ProviderAssetLink]:
    """内容版本在各个调用范围里当前选定的关联或入库任务。"""
    stmt = (
        select(ProviderAssetLink)
        .where(
            ProviderAssetLink.project_name == project_name,
            ProviderAssetLink.content_version_id == content_version_id,
            ProviderAssetLink.selected.is_(True),
        )
        .order_by(ProviderAssetLink.id)
    )
    return list((await session.execute(stmt)).scalars())


async def get_link(session: AsyncSession, *, project_name: str, link_id: int) -> ProviderAssetLink:
    link = await session.get(ProviderAssetLink, link_id)
    if link is None or link.project_name != project_name:
        raise NotFoundError("provider_asset_link_not_found")
    return link


def _current_content_version(project_path: Path, resource_type: str, resource_id: str) -> ContentVersion:
    """资源当前选中的内容版本，按记录的摘要校验快照；早期记录在这里补写身份与摘要。"""
    manager = VersionManager(project_path)
    current = manager.get_current_version(resource_type, resource_id)
    if current <= 0:
        raise UnprocessableError("provider_asset_upload_no_image")
    content_version = manager.ensure_content_version(resource_type, resource_id, current)
    try:
        LocalMaterialStorage(project_path).open(content_version)
    except FileNotFoundError as exc:
        raise UnprocessableError("provider_asset_upload_no_image") from exc
    return content_version


async def resolve_upload_target(project_path: Path, resource_type: str, resource_id: str) -> UploadTarget:
    """冻结要上传的内容版本：资源此刻选中的版本。"""
    if resource_type not in UPLOADABLE_RESOURCE_TYPES:
        raise BadRequestError("unsupported_resource_type", resource_type=resource_type)
    content_version = await asyncio.to_thread(_current_content_version, project_path, resource_type, resource_id)
    return UploadTarget(content_version=content_version)


def _submit_name(resource_id: str) -> str:
    """入库请求里的素材名称：资源名便于在供应商控制台辨认，随机后缀让名称唯一，结果不确定时据此查回。"""
    token = f"arc-{uuid.uuid4().hex[:16]}"
    label = resource_id.replace("/", "-").strip()[: _NAME_LIMIT - len(token) - 1]
    return f"{label}-{token}" if label else token


async def request_upload(
    session: AsyncSession,
    *,
    project_name: str,
    target: UploadTarget,
    account_id: int,
    now: datetime | None = None,
) -> ProviderAssetLink:
    """为冻结的内容版本创建入库任务并选定它，由后台完成中转上传、提交与状态查询。不提交事务。

    同一调用范围里已有选定的关联或入库任务时拒绝：重新上传前先解除当前关系，避免结果不确定时
    盲目重复创建，也避免两条记录争当选定。

    Raises:
        NotFoundError: 账号不存在。
        UnprocessableError: 没有配置对象存储连接，无法为供应商准备下载地址。
        ConflictError: 该内容版本在这个范围里已有选定的关联或入库任务。
    """
    account = await AssetLibraryRepository(session).get_account(account_id)
    if account is None:
        raise NotFoundError("asset_library_account_not_found")
    if await ObjectStorageConnectionRepository(session).get() is None:
        raise UnprocessableError("provider_asset_upload_needs_object_storage")
    content_version = target.content_version
    if content_version.id is None or content_version.digest is None:
        raise UnprocessableError("provider_asset_upload_no_image")
    scope = account_scope(account)
    existing = await selected_link(
        session, project_name=project_name, content_version_id=content_version.id, scope=scope
    )
    if existing is not None:
        raise ConflictError("provider_asset_link_exists").with_diagnostic(
            {"link_id": existing.id, "state": existing.state}
        )
    material = content_version.material
    link = ProviderAssetLink(
        project_name=project_name,
        content_version_id=content_version.id,
        content_digest=content_version.digest,
        resource_type=material.resource_type,
        resource_id=material.resource_id,
        version=content_version.version,
        locator_path=content_version.locator.path,
        origin="upload",
        account_id=scope.account_id,
        site=scope.site,
        region=scope.region,
        provider_project_name=scope.project_name,
        state="pending",
        submit_name=_submit_name(material.resource_id),
        selected=True,
        next_check_at=now or utc_now(),
        check_attempts=0,
    )
    session.add(link)
    await session.flush()
    return link


async def release_link(session: AsyncSession, link: ProviderAssetLink, *, now: datetime | None = None) -> None:
    """解除关联：只解除当前选定关系，不删除 ArcReel 图片或供应商素材。不提交事务。

    入库任务尚未提交时，后台在发出 CreateAsset 前会看到已解除而放弃提交。
    """
    if not link.selected:
        return
    await session.execute(
        update(ProviderAssetLink)
        .where(ProviderAssetLink.id == link.id)
        .values(selected=False, unlinked_at=now or utc_now(), updated_at=now or utc_now())
    )
    await session.refresh(link)


async def request_recheck(session: AsyncSession, link: ProviderAssetLink, *, now: datetime | None = None) -> None:
    """让后台尽快重新查询这条关联的状态；已有定论（可用或失败）的不再查询。不提交事务。"""
    if link.state not in IN_FLIGHT_STATES:
        return
    link.next_check_at = now or utc_now()
    await session.flush()


__all__ = [
    "IN_FLIGHT_STATES",
    "UPLOADABLE_RESOURCE_TYPES",
    "LinkState",
    "UploadTarget",
    "account_scope",
    "asset_uri",
    "get_link",
    "is_usable",
    "release_link",
    "request_recheck",
    "request_upload",
    "resolve_upload_target",
    "scope_of",
    "selected_link",
    "selected_links",
]
