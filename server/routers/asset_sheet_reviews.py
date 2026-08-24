"""Web boundary for accepting reviewed existing asset sheets as current."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from lib.project_manager import get_project_manager
from server.services.asset_sheet_reviews import (
    AssetSheetReviewError,
    AssetSheetSelection,
    confirm_asset_sheets_current,
)

router = APIRouter()


class AssetSheetSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_type: str
    name: str


class ConfirmAssetSheetsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assets: list[AssetSheetSelectionRequest] | None = None


@router.post("/projects/{project_name}/asset-sheets/confirm-current")
async def confirm_current_asset_sheets(project_name: str, req: ConfirmAssetSheetsRequest):
    selections = (
        None if req.assets is None else [AssetSheetSelection(asset.asset_type, asset.name) for asset in req.assets]
    )
    try:
        return await asyncio.to_thread(
            confirm_asset_sheets_current,
            project_name,
            selections=selections,
            manager=get_project_manager(),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except AssetSheetReviewError as exc:
        status = 409 if exc.code in {"asset_manifest_conflict", "asset_sheet_unprovable"} else 400
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message}) from exc


__all__ = ["router"]
