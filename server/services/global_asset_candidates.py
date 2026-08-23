"""Register project-generated media as candidates on linked global assets."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError

from lib.asset_types import (
    GLOBAL_ASSET_ID_FIELD,
    MATCHED_GLOBAL_ASSET_ID_FIELD,
    resolve_asset_key,
)
from lib.db import async_session_factory
from lib.db.repositories.asset_repo import AssetRepository
from lib.db.repositories.asset_resource_repo import AssetResourceRepository
from lib.json_io import atomic_write_bytes
from lib.path_safety import safe_join
from lib.project_manager import ProjectManager, get_project_manager


@dataclass(frozen=True, slots=True)
class GlobalAssetCandidateRegistration:
    asset_id: str
    resource_id: str
    path: str
    created: bool


def _linked_character_asset_id(project: dict[str, Any], character_name: str) -> str | None:
    characters = project.get("characters")
    key = resolve_asset_key(characters, character_name)
    if not isinstance(characters, dict) or key is None:
        return None
    entry = characters.get(key)
    if not isinstance(entry, dict):
        return None
    asset_id = entry.get(GLOBAL_ASSET_ID_FIELD) or entry.get(MATCHED_GLOBAL_ASSET_ID_FIELD)
    return asset_id if isinstance(asset_id, str) and asset_id else None


def _resource_mime_type(path: str | Path) -> str | None:
    mime_type, _encoding = mimetypes.guess_type(str(path))
    return mime_type


def _resource_key(digest: str) -> str:
    return f"local:generated-image:{digest}"


async def _file_metadata(path: Path) -> tuple[str | None, int | None]:
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError:
        return None, None
    return hashlib.sha256(data).hexdigest(), len(data)


async def _ensure_primary_image_resource(
    *,
    asset: Any,
    resource_repo: AssetResourceRepository,
    manager: ProjectManager,
    sort_order: int,
) -> int:
    primary_path = asset.image_path
    if not isinstance(primary_path, str) or not primary_path:
        return sort_order
    if any(resource.media_type == "image" and resource.path == primary_path for resource in asset.resources):
        return sort_order

    digest: str | None = None
    byte_size: int | None = None
    try:
        primary_file = safe_join(manager.projects_root, primary_path)
    except ValueError:
        primary_file = None
    if primary_file is not None:
        digest, byte_size = await _file_metadata(primary_file)

    key_identity = hashlib.sha256(primary_path.encode("utf-8")).hexdigest()
    await resource_repo.create(
        asset_id=asset.id,
        resource_key=f"local:primary-image:{key_identity}",
        origin="local",
        media_type="image",
        mime_type=_resource_mime_type(primary_path),
        path=primary_path,
        sha256=digest,
        byte_size=byte_size,
        sort_order=sort_order,
    )
    return sort_order + 1


async def register_linked_character_image_candidate(
    project_name: str,
    character_name: str,
    sheet_path: str,
    *,
    manager: ProjectManager | None = None,
    session_factory=None,
) -> GlobalAssetCandidateRegistration | None:
    """Copy the selected project sheet into its linked asset without changing the global primary."""

    pm = manager or get_project_manager()
    factory = session_factory or async_session_factory
    project = await asyncio.to_thread(pm.load_project, project_name)
    asset_id = _linked_character_asset_id(project, character_name)
    if asset_id is None:
        return None

    project_path = pm.get_project_path(project_name)
    source = safe_join(project_path, sheet_path)
    data = await asyncio.to_thread(source.read_bytes)
    digest = hashlib.sha256(data).hexdigest()
    resource_key = _resource_key(digest)

    for attempt in range(2):
        target: Path | None = None
        async with factory() as session:
            asset = await AssetRepository(session).get_by_id(asset_id)
            if asset is None or asset.type != "character":
                return None

            existing = next(
                (
                    resource
                    for resource in asset.resources
                    if resource.media_type == "image"
                    and (resource.sha256 == digest or resource.resource_key == resource_key)
                ),
                None,
            )
            if existing is not None:
                return GlobalAssetCandidateRegistration(
                    asset_id=asset.id,
                    resource_id=existing.id,
                    path=existing.path,
                    created=False,
                )

            suffix = source.suffix.lower() or ".png"
            filename = f"{uuid.uuid4().hex}{suffix}"
            target = pm.get_global_assets_root() / "character" / filename
            await asyncio.to_thread(atomic_write_bytes, target, data)
            relative_path = f"_global_assets/character/{filename}"

            latest_project = await asyncio.to_thread(pm.load_project, project_name)
            if _linked_character_asset_id(latest_project, character_name) != asset_id:
                await asyncio.to_thread(target.unlink, missing_ok=True)
                return None

            resource_repo = AssetResourceRepository(session)
            try:
                sort_order = await _ensure_primary_image_resource(
                    asset=asset,
                    resource_repo=resource_repo,
                    manager=pm,
                    sort_order=len(asset.resources),
                )
                candidate = await resource_repo.create(
                    asset_id=asset.id,
                    resource_key=resource_key,
                    origin="local",
                    media_type="image",
                    mime_type=_resource_mime_type(source),
                    path=relative_path,
                    sha256=digest,
                    byte_size=len(data),
                    sort_order=sort_order,
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                await asyncio.to_thread(target.unlink, missing_ok=True)
                if attempt == 0:
                    continue
                raise
            except BaseException:
                await session.rollback()
                await asyncio.to_thread(target.unlink, missing_ok=True)
                raise

            return GlobalAssetCandidateRegistration(
                asset_id=asset.id,
                resource_id=candidate.id,
                path=relative_path,
                created=True,
            )

    raise RuntimeError("linked global image candidate registration did not converge")


__all__ = [
    "GlobalAssetCandidateRegistration",
    "register_linked_character_image_candidate",
]
