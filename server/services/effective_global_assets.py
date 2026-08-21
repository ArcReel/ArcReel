"""Resolve project-selected global media for generation consumers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.asset_types import GLOBAL_ASSET_ID_FIELD, GLOBAL_ASSET_VOICE_SOURCE_FIELD, normalize_asset_bucket
from lib.db import async_session_factory
from lib.db.repositories.asset_repo import AssetRepository
from lib.path_safety import safe_exists


async def resolve_linked_global_reference_audio_paths(
    project: dict[str, Any], projects_root: Path, *, session_factory=None
) -> dict[str, Path]:
    characters = normalize_asset_bucket(project.get("characters"))
    requested = {
        name: entry.get(GLOBAL_ASSET_ID_FIELD)
        for name, entry in characters.items()
        if isinstance(entry, dict) and entry.get(GLOBAL_ASSET_VOICE_SOURCE_FIELD) == "reference_audio"
    }
    ids = sorted({asset_id for asset_id in requested.values() if isinstance(asset_id, str) and asset_id})
    if not ids:
        return {}
    factory = session_factory or async_session_factory
    async with factory() as session:
        assets = await AssetRepository(session).get_by_ids(ids)
    by_id = {asset.id: asset for asset in assets if asset.type == "character"}
    resolved: dict[str, Path] = {}
    for name, asset_id in requested.items():
        asset = by_id.get(asset_id)
        if asset is not None and asset.audio_path and safe_exists(projects_root, asset.audio_path):
            resolved[name] = projects_root / asset.audio_path
    return resolved


__all__ = ["resolve_linked_global_reference_audio_paths"]
