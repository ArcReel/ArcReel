"""Provider asset links: content versions’ scoped provider assets, including ingest tasks before asset IDs exist.

Link records (:class:`~lib.db.models.asset_library.ProviderAssetLink`) exist from ingest submission:

- ``pending``: selected, awaiting relay upload/submission;
- ``submitting``: CreateAsset has been/is about to be sent; process interruption leaves unknown outcomes;
- ``processing``: provider IDs obtained, still processing;
- ``active``: processing complete, the only state usable for generation;
- ``failed``: ingest failure or pre-submission ArcReel failure (providers created no asset);
- ``uncertain``: unknown submission outcome without IDs; never recreate, recover by submission name in background.

Importing existing images (``origin="import"``) directly creates ``active`` links on newly saved
versions: only processed provider images are accepted, content comes from that asset, and no
ingest task needs advancement.

**Call scopes** use frozen submission/import cloud-account identity, site, region, provider
project, never account-record IDs. Switching records to another cloud account’s Access Key leaves
old scopes intact, inapplicable to new identities and never replaced by new-identity uploads.

**Selection**: at most one ``selected`` record per project/content-version/call-scope, identifying
the generation link or awaited task. Only user upload/unlink changes selection; background
queries change state facts only. Selecting another image during ingest leaves results on original
versions; late results never restore unlinked relationships.

**Copying**: equal-content saves into global libraries or other projects obtain usable-link
copies with original scopes through :func:`copy_usable_links`. Targets independently hold copies:
unlinking/replacing one never changes others. Generation still chooses by actual target inference
credential bindings; copying grants no new-account eligibility.

**Archives** carry usable in-scope version links (:class:`ArchivedLink`) with frozen scopes;
other states depend on source-instance relays/tasks and are excluded. Importers use
:func:`adopt_archived_links` for equal-content versions, sharing copy rules. Retain differently
identified account copies without selecting them for calls; provider account authorization
still determines asset-ID usability.

Generation, existing-asset import, and Agent tools query/change links here, never independently in routes.
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

#: Unresolved states still advanced/queried in background.
IN_FLIGHT_STATES: frozenset[str] = frozenset({"pending", "submitting", "processing", "uncertain"})

#: Uploadable image resources: asset sheets and character-derivative sheets.
UPLOADABLE_RESOURCE_TYPES: frozenset[str] = frozenset(
    {"characters", "scenes", "props", "products", CHARACTER_DERIVATIVE_RESOURCE_TYPE}
)

#: Ingest asset-name limit, provider maximum 64 characters.
_NAME_LIMIT = 64


@dataclass(frozen=True)
class UploadTarget:
    """Upload-frozen content version: identity, digest, durable locator fixed at submission and unchanged by later selection."""

    content_version: ContentVersion


def scope_of(link: ProviderAssetLink) -> AssetLibraryScope:
    """Call scope captured at link submission."""
    return AssetLibraryScope(
        account_id=link.account_id,
        account_identity=link.account_identity,
        site=link.site,
        region=link.region,
        project_name=link.provider_project_name,
    )


def is_usable(link: ProviderAssetLink) -> bool:
    """Whether links are currently generation-usable: still selected, provider-complete, and possessing asset IDs."""
    return bool(link.selected and link.state == "active" and link.provider_asset_id)


def asset_uri(link: ProviderAssetLink) -> str | None:
    """Generation-request asset reference for a usable link."""
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
    """Selected scoped link/task for a content version, or None when no link applies in that scope."""
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
    """Currently selected content-version links/tasks across call scopes."""
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
    """Resource’s selected content version, verifying snapshot digests; backfill identities/digests for early records here.

    Remote content persists in object storage; local work files may be cleaned. Do not read
    local files here; ingestion retrieves them when needed.
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
    """Freeze the resource’s currently selected content version for upload."""
    if resource_type not in UPLOADABLE_RESOURCE_TYPES:
        raise BadRequestError("unsupported_resource_type", resource_type=resource_type)
    content_version = await asyncio.to_thread(_current_content_version, project_path, resource_type, resource_id)
    return UploadTarget(content_version=content_version)


def _submit_name(resource_id: str) -> str:
    """Ingest asset name: recognizable resource names plus random uniqueness suffixes, enabling uncertain-outcome recovery."""
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
    """Create/select frozen-version ingest tasks for background relay/upload/query work; do not commit transactions.

    Reject scopes already containing selected links/tasks; require unlinking before reupload to
    avoid blind duplicate creation on uncertain outcomes and competing selected records.

    Raises:
        NotFoundError: Account absent.
        UnprocessableError: No object-storage connection to prepare provider download URLs.
        ConflictError: This scoped version already has a selected link/task.
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
    """Write selected records; unique indexes catch concurrent same-scope requests between read/write, reporting existing-link conflicts.

    Use SAVEPOINT so conflicts roll back this record only, preserving prior caller-transaction writes.
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
    """Link imported content versions to selected provider assets and select them, without committing.

    ``content_version`` must be newly saved from that asset’s actual content: links prove local/
    provider correspondence, never attach IDs to another image. Only available images can link;
    reject other states without leaving apparently usable records.

    Raises:
        UnprocessableError: Not an available image, or version lacks identity/digest.
        ConflictError: Existing selected link/task for this version/account scope.
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
    """Link holder: one content item in a project or retained global-library holder.

    ``resource_type`` / ``resource_id`` / ``version`` / ``locator_path`` serve display/diagnostics
    only; lookup uses ``project_name`` / ``content_version_id`` exclusively.
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
        """Project content-version holder, or ``None`` for early records lacking identity/digest and thus unable to hold links."""
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
    """Unwritten target-held usable-link copy, retaining provider ID/frozen scope and selected as active."""
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
    """Copy currently usable source links to equal-content targets; return new copies without committing.

    Copy selected/available links only when digests match target content, since links prove exact
    content correspondence. Never copy processing/failed tasks, which would cause duplicate ingest
    submissions. Retain provider IDs/frozen scopes, selected as active; source/target later unlink
    independently. Preserve target-selected links already occupying the same scope.
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


#: Imported links use no local account records for submission/query, so record ID 0; account IDs start at 1 and cannot collide.
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
    """Usable archived link: owning version, proven content, frozen call scope, and provider asset.

    Exclude account-record IDs, secrets, signed URLs, and relays. IDs are source-instance-only;
    imports use :data:`ARCHIVED_LINK_ACCOUNT_ID`. Preserve frozen cloud-account identity, site,
    region, and provider project unchanged rather than rewriting for target accounts. Resource,
    version number, and locator come from target-project version records.
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
        """Archive form of usable links; callers first filter with :func:`is_usable`."""
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
        """Read archived links, returning ``None`` for invalid shape/values (possibly edited packages) for callers to discard."""
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
    """Archive-form usable links for these project content versions (identity → digest).

    Include selected/active links with record-matching digests only. Waiting/submitting/processing/
    uncertain tasks depend on source relays/background queries; failed tasks lack assets, unlinked
    relationships are inactive. Exclude them all.
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
    """Write archived links for equal-content imported versions; return records without committing.

    Callers verified ``holder`` identity/digest/actual content matches links. Preserve frozen
    scopes, selected as active; keep target-selected links already in the same scope. Write
    copies even when account identities differ from target bindings; actual inference scopes
    simply cannot select them during generation.
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
            continue  # Duplicate archived links for one version/scope: retain the first written.
        adopted.append(copy)
    return adopted


async def delete_holder_links(
    session: AsyncSession, *, project_name: str, content_version_ids: Iterable[str] | None = None
) -> None:
    """Delete these content links under holders (all by default), records only, never provider assets/other copies; do not commit.

    Holders no longer owning content (library-entry image replacement/deletion) discard their own copies here.
    """
    condition = ProviderAssetLink.project_name == project_name
    if content_version_ids is not None:
        wanted = set(content_version_ids)
        if not wanted:
            return
        condition = condition & ProviderAssetLink.content_version_id.in_(wanted)
    await session.execute(delete(ProviderAssetLink).where(condition))


async def release_link(session: AsyncSession, link: ProviderAssetLink, *, now: datetime | None = None) -> None:
    """Unlink current selection only; never delete ArcReel images/provider assets, and do not commit.

    For unsubmitted tasks, background workers observe unlinking before CreateAsset and abandon submission.
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
    """Request prompt background requery; resolved active/failed links never requery. Do not commit."""
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
