"""Choose provider asset links for generation references using actual backend inference credentials/bindings.

Map each reference’s actual sent bytes to content versions, then find selected links within this call’s scope:

- No declared asset-reference support for slots, no confirmed credential binding, nonuploadable
  asset/derivative image, bytes differing from current content, or no selected scoped link means
  no applicable link; send image content normally;
- Available selected links replace image content with ``asset://`` references without changing
  positions or prompt image numbers;
- Selected links/tasks still processing, uncertain, or failed are applicable but unavailable:
  fail explicitly, never fall back to image content.

Pre-submission reads (admission, batch previews, quotes) and execution share rules. Readers derive
scopes from currently configured backends, executors from actual backends; after staging images,
verify once more against sent bytes.

Read-only: never backfill early version identities; identity-free records cannot have links.
Queries/state machines: :mod:`lib.asset_library.links`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.version_manager import VersionManager
from lib.asset_library.bindings import AssetLibraryScope, resolve_bound_scope
from lib.asset_library.links import UPLOADABLE_RESOURCE_TYPES, asset_uri, is_usable, selected_link
from lib.backends.provider_assets import REFERENCE_IMAGE_ROLE, inference_credential, provider_asset_roles
from lib.db.models.asset_library import ProviderAssetLink
from lib.infra.image_utils import IMAGE_FILE_EXTENSIONS
from lib.infra.path_safety import safe_join
from lib.material.storage import CONTENT_DIGEST, CONTENT_VERSION_ID
from lib.material.work_files import material_file_digest
from lib.project.resource_paths import resource_of_path


@dataclass(frozen=True)
class ReferenceImageSource:
    """Reference image about to send: project-relative source path, sent-byte digest, and asset/derivative name."""

    locator: str
    sha256: str
    name: str


@dataclass(frozen=True)
class ReferenceAssetChoice:
    """Reference sent as an asset reference: corresponding content version and selected link."""

    asset_uri: str
    content_version_id: str
    link_id: int


class ProviderAssetLinkUnavailableError(RuntimeError):
    """A selected link/ingest task exists within this call’s scope but cannot currently serve generation.

    Persist ``code`` / ``params`` as structured task-failure codes: processing asks users to wait;
    failures give provider reasons and suggest unlinking.
    """

    def __init__(self, link: ProviderAssetLink, *, index: int, name: str) -> None:
        self.link_id = link.id
        self.state = link.state
        params: dict[str, Any] = {"index": index, "name": name}
        if link.state == "failed":
            self.code = "provider_asset_link_failed"
            params["error_code"] = link.error_code or "-"
            if link.error_message:
                params["provider_reason"] = link.error_message
        else:
            self.code = "provider_asset_link_processing"
        self.params = params
        super().__init__(f"{self.code}: image {index} ({name}) link {link.id} is {link.state}")


def reference_image_sources(project_path: Path, images: Sequence[tuple[Path, str]]) -> list[ReferenceImageSource]:
    """Derive sources from current project-image bytes; ``images`` contains (file path, asset/derivative name), in send order.

    Cleaned remote-version caches use recorded digests matching refetched sent bytes. Paths outside
    projects, or neither local nor remote-version files, raise ``FileNotFoundError`` / ``PathTraversalError``.
    """
    root = project_path.resolve()
    sources: list[ReferenceImageSource] = []
    for path, name in images:
        resolved = safe_join(project_path, path)
        sources.append(
            ReferenceImageSource(
                locator=resolved.relative_to(root).as_posix(), sha256=material_file_digest(resolved), name=name
            )
        )
    return sources


async def call_scope(session: AsyncSession, backend: object) -> AssetLibraryScope | None:
    """Backend’s usable library scope, or None when reference slots reject asset references or credentials lack confirmed bindings."""
    if REFERENCE_IMAGE_ROLE not in provider_asset_roles(backend):
        return None
    credential = inference_credential(backend)
    if credential is None:
        return None
    return await resolve_bound_scope(
        session,
        credential_type=credential.credential_type,
        credential_id=credential.credential_id,
        api_key=credential.api_key,
        base_url=credential.base_url,
        default_base_url=credential.default_base_url,
    )


def _uploadable_resource_of(locator: str) -> tuple[str, str] | None:
    """Uploadable resource identified by reference paths. Upload/import retains original sheet formats
    (``IMAGE_FILE_EXTENSIONS``), while canonical version-resource paths always use ``.png``; match
    resources by same-name canonical paths. Callers verify current-content identity by digest."""
    resource = resource_of_path(locator)
    if resource is None:
        stem, dot, suffix = locator.rpartition(".")
        if dot and f".{suffix.lower()}" in IMAGE_FILE_EXTENSIONS:
            resource = resource_of_path(f"{stem}.png")
    if resource is None or resource[0] not in UPLOADABLE_RESOURCE_TYPES:
        return None
    return resource


def _content_version_ids(project_path: Path, sources: Sequence[ReferenceImageSource]) -> list[str | None]:
    """Content identity for each reference’s sent bytes: selected current versions whose digests match those bytes."""
    manager = VersionManager(project_path)
    ids: list[str | None] = []
    for source in sources:
        resource = _uploadable_resource_of(source.locator)
        if resource is None:
            ids.append(None)
            continue
        info = manager.get_versions(*resource)
        current = info.get("current_version", 0)
        record = next((v for v in info.get("versions", []) if v.get("version") == current), None)
        if record is None or record.get(CONTENT_DIGEST) != source.sha256:
            ids.append(None)
            continue
        content_version_id = record.get(CONTENT_VERSION_ID)
        ids.append(content_version_id if isinstance(content_version_id, str) and content_version_id else None)
    return ids


async def choose_reference_image_assets(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    backend: object,
    project_name: str,
    project_path: Path,
    sources: Sequence[ReferenceImageSource],
) -> tuple[ReferenceAssetChoice | None, ...]:
    """Choices matching ``sources`` count/order: available links provide asset references, no applicable links give None.

    Raises:
        ProviderAssetLinkUnavailableError: Applicable link unavailable/ingest failed; report the first in image order.
    """
    if not sources:
        return ()
    scope = await backend_call_scope(session_factory, backend)
    if scope is None:
        return (None,) * len(sources)
    return await choose_in_scope(
        session_factory, scope=scope, project_name=project_name, project_path=project_path, sources=sources
    )


async def backend_call_scope(
    session_factory: async_sessionmaker[AsyncSession], backend: object
) -> AssetLibraryScope | None:
    """:func:`call_scope` variant opening its own session; None scopes need no reference digests because no image has applicable links."""
    async with session_factory() as session:
        return await call_scope(session, backend)


async def choose_in_scope(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    scope: AssetLibraryScope,
    project_name: str,
    project_path: Path,
    sources: Sequence[ReferenceImageSource],
) -> tuple[ReferenceAssetChoice | None, ...]:
    """Choose references within resolved scopes, sharing :func:`choose_reference_image_assets` rules/errors."""
    if not sources:
        return ()
    async with session_factory() as session:
        content_version_ids = await asyncio.to_thread(_content_version_ids, project_path, sources)
        choices: list[ReferenceAssetChoice | None] = []
        for position, (source, content_version_id) in enumerate(zip(sources, content_version_ids, strict=True)):
            if content_version_id is None:
                choices.append(None)
                continue
            link = await selected_link(
                session, project_name=project_name, content_version_id=content_version_id, scope=scope
            )
            if link is None:
                choices.append(None)
                continue
            uri = asset_uri(link)
            if not is_usable(link) or uri is None:
                raise ProviderAssetLinkUnavailableError(link, index=position + 1, name=source.name)
            choices.append(ReferenceAssetChoice(asset_uri=uri, content_version_id=content_version_id, link_id=link.id))
    return tuple(choices)


__all__ = [
    "ProviderAssetLinkUnavailableError",
    "ReferenceAssetChoice",
    "ReferenceImageSource",
    "backend_call_scope",
    "call_scope",
    "choose_in_scope",
    "choose_reference_image_assets",
    "reference_image_sources",
]
