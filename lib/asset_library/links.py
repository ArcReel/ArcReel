"""供应商素材关联：内容版本在某个调用范围里对应的供应商素材，连同取得素材 ID 之前的入库任务。

一条关联记录（:class:`~lib.db.models.asset_library.ProviderAssetLink`）从提交入库起就存在，状态如下：

- ``pending``：已选定，等待中转上传与提交入库；
- ``submitting``：CreateAsset 请求已经或即将发出，进程在此中断时结果未知；
- ``processing``：已取得供应商素材 ID，供应商仍在处理；
- ``active``：供应商处理完成，只有这一状态可用于生成；
- ``failed``：入库失败，或 ArcReel 在提交前失败（此时供应商侧没有创建素材）；
- ``uncertain``：提交请求的结果未知，还没有素材 ID。不重新创建，由后台按提交时的名称查回。

从素材库导入已有图片（``origin="import"``）时，关联在导入保存的新内容版本上直接以 ``active`` 建立：
导入只接受供应商已处理完成的图片素材，内容取自该素材本身，没有入库任务要推进。

**调用范围**按提交或导入时冻结的云账号身份、站点、区域与供应商项目判断，不按账号记录 ID：账号记录
改成另一个云账号的 Access Key 后，旧关联保留原范围，不适用于新身份，也不会被新身份的上传顶替。

**选定**：同一项目、同一内容版本、同一调用范围至多一条 ``selected``
记录，即生成时要用的关联或要等待的入库任务。选定只由用户的上传或解除关联改变；后台查询结果只更新状态
事实，不改变选定，所以入库期间改选了另一张图片，结果仍归属提交时的内容版本，已解除的关系也不会因
迟到的结果恢复。

**复制**：同一实例里以相同内容保存到全局资产库或应用到另一个项目时，目标经 :func:`copy_usable_links` 得到来源
可用关联的副本，连同原调用范围。副本由目标独立持有：解除或替换任一副本不改写其他副本；生成时仍按目标实际
推理凭证的绑定范围选用，复制不授予新账号资格。

**归档**：项目归档携带范围内内容版本的可用关联（:class:`ArchivedLink`），连同冻结的调用范围；其他状态依赖源实例的
中转副本或后台任务，不携带。导入实例经 :func:`adopt_archived_links` 为内容一致的内容版本写入副本，规则同复制：
账号身份不同的副本保留但不被选用，素材 ID 能否使用仍由供应商按账号鉴权。

生成、导入已有素材与 Agent 工具都经本模块查询与改变关联，不在路由里各自实现。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.artifacts.version_manager import VersionManager
from lib.asset_library.bindings import AssetLibraryScope, account_scope
from lib.asset_library.control_plane import ProviderAssetDetail
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
        account_identity=link.account_identity,
        site=link.site,
        region=link.region,
        project_name=link.provider_project_name,
    )


def is_usable(link: ProviderAssetLink) -> bool:
    """关联此刻能否用于生成：仍被选定、供应商处理完成且有素材 ID。"""
    return bool(link.selected and link.state == "active" and link.provider_asset_id)


def asset_uri(link: ProviderAssetLink) -> str | None:
    """可用关联在生成请求里的素材引用。"""
    return f"asset://{link.provider_asset_id}" if is_usable(link) else None


def _scope_filter(scope: AssetLibraryScope):
    return (
        ProviderAssetLink.account_identity == scope.account_identity,
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
    """资源当前选中的内容版本，按记录的摘要校验快照；早期记录在这里补写身份与摘要。

    远端内容版本的持久内容在对象存储里，本地工作文件可能已清理，这里不读本地文件，由入库时取用。
    """
    manager = VersionManager(project_path)
    current = manager.get_current_version(resource_type, resource_id)
    if current <= 0:
        raise UnprocessableError("provider_asset_upload_no_image")
    content_version = manager.ensure_content_version(resource_type, resource_id, current)
    if content_version.stored is not None:
        return content_version
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
        account_identity=scope.account_identity,
        site=scope.site,
        region=scope.region,
        provider_project_name=scope.project_name,
        state="pending",
        submit_name=_submit_name(material.resource_id),
        selected=True,
        next_check_at=now or utc_now(),
        check_attempts=0,
    )
    await _insert_selected(session, link)
    return link


async def _insert_selected(session: AsyncSession, link: ProviderAssetLink) -> None:
    """写入一条选定记录。先查后写之间并发的同范围请求由唯一索引拦下，按已有关联报冲突。

    写入放在 SAVEPOINT 里：冲突只回滚这一条，调用方事务里之前的写入仍可继续使用。
    """
    try:
        async with session.begin_nested():
            session.add(link)
            await session.flush()
    except IntegrityError as exc:
        raise ConflictError("provider_asset_link_exists") from exc


async def record_imported_link(
    session: AsyncSession,
    *,
    project_name: str,
    content_version: ContentVersion,
    account: AssetLibraryAccount,
    asset: ProviderAssetDetail,
    now: datetime | None = None,
) -> ProviderAssetLink:
    """为导入得到的内容版本建立与所选供应商素材的关联并选定它。不提交事务。

    ``content_version`` 必须是用该素材的实际内容刚保存的新版本：关联证明的是本地内容与供应商素材对应，
    不能把素材 ID 贴到另一张图片上。只有可用的图片素材能建立关联，其余状态在这里拒绝，不会留下
    看似可用的记录。

    Raises:
        UnprocessableError: 素材不是图片、还不可用，或内容版本没有身份与摘要。
        ConflictError: 该内容版本在账号范围里已有选定的关联或入库任务。
    """
    if asset.asset_type != "Image":
        raise UnprocessableError("provider_asset_import_not_image")
    if asset.status != "active":
        raise UnprocessableError("provider_asset_import_not_active")
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
    settled = now or utc_now()
    material = content_version.material
    link = ProviderAssetLink(
        project_name=project_name,
        content_version_id=content_version.id,
        content_digest=content_version.digest,
        resource_type=material.resource_type,
        resource_id=material.resource_id,
        version=content_version.version,
        locator_path=content_version.locator.path,
        origin="import",
        account_id=scope.account_id,
        account_identity=scope.account_identity,
        site=scope.site,
        region=scope.region,
        provider_project_name=scope.project_name,
        state="active",
        provider_asset_id=asset.id,
        provider_group_id=asset.group_id or None,
        submit_name=(asset.name or asset.id)[:_NAME_LIMIT],
        selected=True,
        settled_at=settled,
        next_check_at=None,
        check_attempts=0,
    )
    await _insert_selected(session, link)
    return link


@dataclass(frozen=True)
class LinkHolder:
    """持有关联的一方：某个项目（或全局资产库的保留持有方）里的一个内容。

    ``resource_type`` / ``resource_id`` / ``version`` / ``locator_path`` 只供展示与诊断，查找关联只按
    ``project_name`` 与 ``content_version_id``。
    """

    project_name: str
    content_version_id: str
    content_digest: str
    resource_type: str
    resource_id: str
    version: int
    locator_path: str

    @classmethod
    def of(cls, project_name: str, content_version: ContentVersion) -> LinkHolder | None:
        """项目里一个内容版本作为持有方；早期没有身份或摘要的记录不能持有关联，返回 ``None``。"""
        if content_version.id is None or content_version.digest is None:
            return None
        material = content_version.material
        return cls(
            project_name=project_name,
            content_version_id=content_version.id,
            content_digest=content_version.digest,
            resource_type=material.resource_type,
            resource_id=material.resource_id,
            version=content_version.version,
            locator_path=content_version.locator.path,
        )


def _usable_copy(source: ProviderAssetLink | ArchivedLink, target: LinkHolder, *, account_id: int) -> ProviderAssetLink:
    """一条可用关联交给目标持有的副本：保留供应商素材 ID 与冻结的调用范围，以可用状态选定，尚未写入。"""
    return ProviderAssetLink(
        project_name=target.project_name,
        content_version_id=target.content_version_id,
        content_digest=target.content_digest,
        resource_type=target.resource_type,
        resource_id=target.resource_id,
        version=target.version,
        locator_path=target.locator_path,
        origin=source.origin,
        account_id=account_id,
        account_identity=source.account_identity,
        site=source.site,
        region=source.region,
        provider_project_name=source.provider_project_name,
        state="active",
        provider_asset_id=source.provider_asset_id,
        provider_group_id=source.provider_group_id,
        submit_name=source.submit_name,
        selected=True,
        submitted_at=source.submitted_at,
        settled_at=source.settled_at,
        next_check_at=None,
        check_attempts=0,
    )


async def copy_usable_links(
    session: AsyncSession, *, source_project: str, source_content_version_id: str, target: LinkHolder
) -> list[ProviderAssetLink]:
    """把来源内容当前可用的关联复制给内容相同的目标，返回新建的副本。不提交事务。

    只复制选定、可用且摘要与目标内容一致的关联：关联证明的是这份内容与供应商素材对应，内容不同时不复制。
    入库中或失败的任务不复制，否则后台会为同一张图重复提交入库。副本保留来源的供应商素材 ID 与冻结的调用范围，
    以可用状态选定，之后与来源各自解除、互不影响。目标在同一范围已有选定的关联时保留目标自己的。
    """
    copies: list[ProviderAssetLink] = []
    for link in await selected_links(
        session, project_name=source_project, content_version_id=source_content_version_id
    ):
        if not is_usable(link) or link.content_digest != target.content_digest:
            continue
        existing = await selected_link(
            session,
            project_name=target.project_name,
            content_version_id=target.content_version_id,
            scope=scope_of(link),
        )
        if existing is not None:
            continue
        copy = _usable_copy(link, target, account_id=link.account_id)
        await _insert_selected(session, copy)
        copies.append(copy)
    return copies


#: 归档导入的关联不经本实例的任何账号记录提交或查询，账号记录 ID 记为 0；账号记录 ID 从 1 起，不会与它相等。
ARCHIVED_LINK_ACCOUNT_ID = 0

_ARCHIVED_ORIGINS = frozenset({"upload", "import"})


def _bounded_text(payload: dict[str, Any], key: str, limit: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(key)
    return value


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(key)
    return value


def _optional_time(payload: dict[str, Any], key: str) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(key)
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).isoformat()


@dataclass(frozen=True)
class ArchivedLink:
    """项目归档携带的一条可用关联：它属于哪个内容版本、证明的是哪份内容，以及冻结的调用范围与供应商素材。

    不含账号记录 ID、密钥、签名地址或中转副本：账号记录 ID 只在源实例里有意义，导入后记为
    :data:`ARCHIVED_LINK_ACCOUNT_ID`；调用范围按冻结的云账号身份、站点、区域与供应商项目原样保留，不按目标实例的
    账号改写。资源、版本号与定位取目标项目里的版本记录。
    """

    content_version_id: str
    content_digest: str
    origin: str
    account_identity: str
    site: str
    region: str
    provider_project_name: str
    provider_asset_id: str
    provider_group_id: str | None
    submit_name: str
    submitted_at: datetime | None
    settled_at: datetime | None

    @classmethod
    def of(cls, link: ProviderAssetLink) -> ArchivedLink:
        """可用关联的归档形式；调用方先用 :func:`is_usable` 筛选。"""
        if link.provider_asset_id is None:
            raise ValueError(f"provider asset link {link.id} has no provider asset id")
        return cls(
            content_version_id=link.content_version_id,
            content_digest=link.content_digest,
            origin=link.origin,
            account_identity=link.account_identity,
            site=link.site,
            region=link.region,
            provider_project_name=link.provider_project_name,
            provider_asset_id=link.provider_asset_id,
            provider_group_id=link.provider_group_id,
            submit_name=link.submit_name,
            submitted_at=link.submitted_at,
            settled_at=link.settled_at,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "content_version_id": self.content_version_id,
            "content_digest": self.content_digest,
            "origin": self.origin,
            "account_identity": self.account_identity,
            "site": self.site,
            "region": self.region,
            "provider_project_name": self.provider_project_name,
            "provider_asset_id": self.provider_asset_id,
            "provider_group_id": self.provider_group_id,
            "submit_name": self.submit_name,
            "submitted_at": _iso(self.submitted_at),
            "settled_at": _iso(self.settled_at),
        }

    @classmethod
    def from_payload(cls, payload: object) -> ArchivedLink | None:
        """读取归档里的一条关联；形状或取值不合法（包可能被手工改过）时返回 ``None``，由调用方丢弃。"""
        if not isinstance(payload, dict):
            return None
        try:
            digest = _bounded_text(payload, "content_digest", 64)
            origin = _bounded_text(payload, "origin", 16)
            if (
                len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
                or origin not in _ARCHIVED_ORIGINS
            ):
                return None
            return cls(
                content_version_id=_bounded_text(payload, "content_version_id", 64),
                content_digest=digest,
                origin=origin,
                account_identity=_bounded_text(payload, "account_identity", 64),
                site=_bounded_text(payload, "site", 16),
                region=_bounded_text(payload, "region", 64),
                provider_project_name=_bounded_text(payload, "provider_project_name", 128),
                provider_asset_id=_bounded_text(payload, "provider_asset_id", 1024),
                provider_group_id=_optional_text(payload, "provider_group_id"),
                submit_name=_bounded_text(payload, "submit_name", _NAME_LIMIT),
                submitted_at=_optional_time(payload, "submitted_at"),
                settled_at=_optional_time(payload, "settled_at"),
            )
        except ValueError:
            return None

    @property
    def scope(self) -> AssetLibraryScope:
        return AssetLibraryScope(
            account_id=ARCHIVED_LINK_ACCOUNT_ID,
            account_identity=self.account_identity,
            site=self.site,
            region=self.region,
            project_name=self.provider_project_name,
        )


async def archivable_links(
    session: AsyncSession, *, project_name: str, content_digests: dict[str, str]
) -> list[ArchivedLink]:
    """项目里这些内容版本（内容版本身份 → 摘要）当前可用的关联，按归档形式给出。

    只带选定且可用、摘要与版本记录一致的关联。等待上传、提交中、处理中与结果待确认的任务依赖源实例的中转副本
    或后台查询，失败的任务没有素材，已解除的关系不再生效，这些都不带。
    """
    if not content_digests:
        return []
    stmt = (
        select(ProviderAssetLink)
        .where(
            ProviderAssetLink.project_name == project_name,
            ProviderAssetLink.selected.is_(True),
            ProviderAssetLink.state == "active",
        )
        .order_by(ProviderAssetLink.id)
    )
    return [
        ArchivedLink.of(link)
        for link in (await session.execute(stmt)).scalars()
        if is_usable(link) and content_digests.get(link.content_version_id) == link.content_digest
    ]


async def adopt_archived_links(
    session: AsyncSession, links: Iterable[tuple[ArchivedLink, LinkHolder]]
) -> list[ProviderAssetLink]:
    """为导入项目里内容一致的内容版本写入归档带来的关联，返回写入的记录。不提交事务。

    调用方已核对 ``holder`` 的内容版本身份、摘要与实际内容都和关联一致。副本原样保留冻结的调用范围，以可用状态
    选定；目标在同一范围已有选定的关联时保留目标自己的。账号身份与目标实例的绑定不一致的副本照样写入，
    生成时按实际推理凭证的绑定范围选不中它。
    """
    adopted: list[ProviderAssetLink] = []
    for archived, holder in links:
        if archived.content_digest != holder.content_digest or archived.content_version_id != holder.content_version_id:
            continue
        existing = await selected_link(
            session,
            project_name=holder.project_name,
            content_version_id=holder.content_version_id,
            scope=archived.scope,
        )
        if existing is not None:
            continue
        copy = _usable_copy(archived, holder, account_id=ARCHIVED_LINK_ACCOUNT_ID)
        try:
            await _insert_selected(session, copy)
        except ConflictError:
            continue  # 包里同一内容版本在同一范围有两条关联：先写入的一条为准
        adopted.append(copy)
    return adopted


async def delete_holder_links(
    session: AsyncSession, *, project_name: str, content_version_ids: Iterable[str] | None = None
) -> None:
    """删除持有方名下这些内容（缺省为全部内容）的关联记录，只删记录，不涉及供应商素材与其他副本。不提交事务。

    供不再持有某个内容的一方（全局资产库条目换图或删除）丢弃它那份副本。
    """
    condition = ProviderAssetLink.project_name == project_name
    if content_version_ids is not None:
        wanted = set(content_version_ids)
        if not wanted:
            return
        condition = condition & ProviderAssetLink.content_version_id.in_(wanted)
    await session.execute(delete(ProviderAssetLink).where(condition))


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
    "ARCHIVED_LINK_ACCOUNT_ID",
    "IN_FLIGHT_STATES",
    "UPLOADABLE_RESOURCE_TYPES",
    "ArchivedLink",
    "LinkHolder",
    "LinkState",
    "UploadTarget",
    "account_scope",
    "adopt_archived_links",
    "archivable_links",
    "asset_uri",
    "copy_usable_links",
    "delete_holder_links",
    "get_link",
    "is_usable",
    "record_imported_link",
    "release_link",
    "request_recheck",
    "request_upload",
    "resolve_upload_target",
    "scope_of",
    "selected_link",
    "selected_links",
]
