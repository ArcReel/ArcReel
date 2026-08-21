"""Croco 角色目录同步 API。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from lib.character_catalog import CharacterCatalogSyncError, sync_character_catalog
from lib.db import get_async_session
from lib.i18n import Translator

router = APIRouter(prefix="/character-catalog", tags=["角色目录"])


@router.post("/sync")
async def sync_catalog(
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await sync_character_catalog(session)
    except CharacterCatalogSyncError as exc:
        if exc.code == "character_catalog_config_missing":
            status_code = 400
        elif exc.code in {
            "character_catalog_invalid_url",
            "character_catalog_invalid_payload",
            "character_catalog_asset_integrity_failed",
            "character_catalog_asset_too_large",
        }:
            status_code = 422
        else:
            status_code = 502
        raise HTTPException(status_code=status_code, detail=_t(exc.code, status=exc.status or "")) from exc
