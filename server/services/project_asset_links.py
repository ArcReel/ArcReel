"""Shared project/global asset link operations for REST and Agent tools."""

from __future__ import annotations

import asyncio
from typing import Any

from lib.asset_types import (
    GLOBAL_ASSET_ID_FIELD,
    GLOBAL_ASSET_IMAGE_USAGE_FIELD,
    GLOBAL_ASSET_IMAGE_USAGES,
    GLOBAL_ASSET_VOICE_SOURCE_FIELD,
    GLOBAL_ASSET_VOICE_SOURCES,
    GLOBAL_LIBRARY_ASSET_TYPES,
    MATCHED_GLOBAL_ASSET_ID_FIELD,
)
from lib.db import async_session_factory
from lib.db.repositories.asset_repo import AssetRepository
from lib.project_change_hints import project_change_source
from lib.project_manager import ProjectManager, get_project_manager


class ProjectAssetLinkError(ValueError):
    pass


class ProjectAssetLinkNotFound(ProjectAssetLinkError):
    pass


async def _asset(asset_id: str, session_factory=None):
    factory = session_factory or async_session_factory
    async with factory() as session:
        return await AssetRepository(session).get_by_id(asset_id)


async def link_project_asset(
    project_name: str,
    resource_type: str,
    resource_id: str,
    asset_id: str,
    *,
    manager: ProjectManager | None = None,
    source: str = "webui",
    session_factory=None,
) -> tuple[dict[str, Any], Any]:
    if resource_type not in GLOBAL_LIBRARY_ASSET_TYPES:
        raise ProjectAssetLinkError("invalid asset type")
    asset = await _asset(asset_id, session_factory)
    if asset is None:
        raise ProjectAssetLinkNotFound(asset_id)
    if asset.type != resource_type:
        raise ProjectAssetLinkError("global asset type does not match project asset type")
    pm = manager or get_project_manager()

    def _sync() -> dict[str, Any]:
        def _mutate(entry: dict[str, Any]) -> None:
            entry[GLOBAL_ASSET_ID_FIELD] = asset.id
            entry[MATCHED_GLOBAL_ASSET_ID_FIELD] = asset.id
            entry[GLOBAL_ASSET_IMAGE_USAGE_FIELD] = "main"
            if resource_type == "character":
                entry[GLOBAL_ASSET_VOICE_SOURCE_FIELD] = (
                    "reference_audio" if asset.audio_path else "voice_id" if asset.voice_id else "none"
                )
        with project_change_source(source):
            return pm.update_asset_entry(resource_type, project_name, resource_id, _mutate)

    return await asyncio.to_thread(_sync), asset


async def unlink_project_asset(
    project_name: str,
    resource_type: str,
    resource_id: str,
    *,
    manager: ProjectManager | None = None,
    source: str = "webui",
) -> dict[str, Any]:
    if resource_type not in GLOBAL_LIBRARY_ASSET_TYPES:
        raise ProjectAssetLinkError("invalid asset type")
    pm = manager or get_project_manager()

    def _sync() -> dict[str, Any]:
        def _mutate(entry: dict[str, Any]) -> None:
            for field in (
                GLOBAL_ASSET_ID_FIELD,
                MATCHED_GLOBAL_ASSET_ID_FIELD,
                GLOBAL_ASSET_IMAGE_USAGE_FIELD,
                GLOBAL_ASSET_VOICE_SOURCE_FIELD,
            ):
                entry.pop(field, None)
        with project_change_source(source):
            return pm.update_asset_entry(resource_type, project_name, resource_id, _mutate)

    return await asyncio.to_thread(_sync)


async def configure_project_asset_link(
    project_name: str,
    resource_type: str,
    resource_id: str,
    *,
    image_usage: str | None = None,
    voice_source: str | None = None,
    manager: ProjectManager | None = None,
    source: str = "webui",
    session_factory=None,
) -> tuple[dict[str, Any], Any]:
    if image_usage is not None and image_usage not in GLOBAL_ASSET_IMAGE_USAGES:
        raise ProjectAssetLinkError("image_usage must be main or reference")
    if voice_source is not None and voice_source not in GLOBAL_ASSET_VOICE_SOURCES:
        raise ProjectAssetLinkError("voice_source must be reference_audio, voice_id, or none")
    pm = manager or get_project_manager()
    project = await asyncio.to_thread(pm.load_project, project_name)
    bucket = {"character": "characters", "scene": "scenes", "prop": "props"}.get(resource_type)
    entry = project.get(bucket, {}).get(resource_id) if bucket else None
    asset_id = (
        entry.get(GLOBAL_ASSET_ID_FIELD) or entry.get(MATCHED_GLOBAL_ASSET_ID_FIELD)
        if isinstance(entry, dict)
        else None
    )
    if not isinstance(asset_id, str) or not asset_id:
        raise ProjectAssetLinkError("project asset is not linked")
    asset = await _asset(asset_id, session_factory)
    if asset is None:
        raise ProjectAssetLinkNotFound(asset_id)
    if voice_source is not None:
        if resource_type != "character":
            raise ProjectAssetLinkError("voice source only applies to characters")
        if voice_source == "reference_audio" and not asset.audio_path:
            raise ProjectAssetLinkError("linked asset has no reference audio")
        if voice_source == "voice_id" and not asset.voice_id:
            raise ProjectAssetLinkError("linked asset has no TTS voice ID")

    def _sync() -> dict[str, Any]:
        def _mutate(current: dict[str, Any]) -> None:
            current[GLOBAL_ASSET_ID_FIELD] = asset.id
            current[MATCHED_GLOBAL_ASSET_ID_FIELD] = asset.id
            if image_usage is not None:
                current[GLOBAL_ASSET_IMAGE_USAGE_FIELD] = image_usage
            if voice_source is not None:
                current[GLOBAL_ASSET_VOICE_SOURCE_FIELD] = voice_source
        with project_change_source(source):
            return pm.update_asset_entry(resource_type, project_name, resource_id, _mutate)

    return await asyncio.to_thread(_sync), asset


__all__ = [
    "ProjectAssetLinkError",
    "ProjectAssetLinkNotFound",
    "configure_project_asset_link",
    "link_project_asset",
    "unlink_project_asset",
]
