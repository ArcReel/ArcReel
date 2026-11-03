"""供应商素材关联 API：查看图片当前版本的关联与入库状态、上传到素材库、从素材库选择已有图片、解除关联与刷新状态。

上传只创建入库任务并立即返回，中转上传、提交与状态查询由后台循环推进（``lib.asset_library.ingest``），
页面按返回的状态轮询，不等待供应商审核。编排与结果形状在 ``server.services.project.provider_asset_links``，
与 Agent 工具共用；本路由只做参数与响应映射。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db import get_async_session
from lib.project.project_manager import get_project_manager
from server.services.project import provider_asset_links as link_ops
from server.services.project.provider_asset_import import import_provider_asset
from server.services.project.provider_asset_links import (
    ContentVersionInfo,
    LinkPanelResponse,
    LinkResponse,
    link_response,
)

router = APIRouter(prefix="/projects/{project_name}/provider-asset-links", tags=["Provider Asset Links"])


class UploadRequest(BaseModel):
    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=512)
    account_id: int


class ImportRequest(UploadRequest):
    asset_id: str = Field(min_length=1, max_length=256)


class ImportResponse(BaseModel):
    content_version: ContentVersionInfo
    link: LinkResponse


async def _project_path(project_name: str) -> Path:
    return await asyncio.to_thread(get_project_manager().get_project_path, project_name)


@router.get("", response_model=LinkPanelResponse)
async def get_links(
    project_name: str,
    resource_type: str = Query(min_length=1, max_length=64),
    resource_id: str = Query(min_length=1, max_length=512),
    session: AsyncSession = Depends(get_async_session),
) -> LinkPanelResponse:
    """图片当前选中版本在各个素材库范围里的关联与入库状态。改选另一版本后，返回的是那个版本的关联。"""
    link_ops.check_resource_type(resource_type)
    return await link_ops.load_link_panel(
        session,
        project_path=await _project_path(project_name),
        project_name=project_name,
        resource_type=resource_type,
        resource_id=resource_id,
    )


@router.post("", status_code=202, response_model=LinkResponse)
async def upload_to_asset_library(
    project_name: str, body: UploadRequest, session: AsyncSession = Depends(get_async_session)
) -> LinkResponse:
    """把图片当前选中的版本上传到账号对应的素材库。立即返回入库任务，之后按状态轮询。"""
    link_ops.check_resource_type(body.resource_type)
    return await link_ops.start_upload(
        session,
        project_path=await _project_path(project_name),
        project_name=project_name,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        account_id=body.account_id,
    )


@router.post("/import", status_code=201, response_model=ImportResponse)
async def import_from_asset_library(
    project_name: str, body: ImportRequest, session: AsyncSession = Depends(get_async_session)
) -> ImportResponse:
    """从素材库选择一张可用的图片：取回原图保存为资产图的新版本，并为这个版本建立关联。

    取不回内容或保存失败时不建立关联，原选中版本不变。
    """
    imported = await import_provider_asset(
        session,
        project_name=project_name,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        account_id=body.account_id,
        asset_id=body.asset_id,
    )
    content_version = imported.content_version
    return ImportResponse(
        content_version=ContentVersionInfo(
            id=content_version.id, version=content_version.version, digest=content_version.digest
        ),
        link=link_response(imported.link),
    )


@router.delete("/{link_id}", status_code=204)
async def unlink_provider_asset(
    project_name: str, link_id: int, session: AsyncSession = Depends(get_async_session)
) -> Response:
    """解除关联：只解除当前关系，不删除 ArcReel 图片或供应商素材。"""
    await link_ops.release(session, project_name=project_name, link_id=link_id)
    return Response(status_code=204)


@router.post("/{link_id}/refresh", response_model=LinkResponse)
async def refresh(project_name: str, link_id: int, session: AsyncSession = Depends(get_async_session)) -> LinkResponse:
    """请后台立即重新查询状态；已可用或已失败的关联原样返回。"""
    return await link_ops.recheck(session, project_name=project_name, link_id=link_id)
