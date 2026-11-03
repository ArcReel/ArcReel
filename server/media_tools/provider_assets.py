"""Host-neutral supplier asset library tools: query an image's links, browse a library account, upload the current
image version, select an existing library image, and release a link.

Every handler calls the same orchestration the Web routes use (``server.services.project.provider_asset_links`` and
``provider_asset_import``), so link, account and scope rules live only in ``lib.asset_library.links``. Upload and
recheck only record the job and wake the background loop; they return at once and never wait for the supplier.
Results carry state facts only: no control-plane secrets, relay objects or signed download URLs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy.ext.asyncio import AsyncSession

from lib.asset_library.links import IN_FLIGHT_STATES
from lib.i18n import DEFAULT_LOCALE
from lib.i18n import _ as translate
from lib.infra.api_errors import ApiError
from server.media_tools.context import tool_error, tool_problem
from server.services.project import provider_asset_links as link_ops
from server.services.project.provider_asset_import import import_provider_asset
from server.services.tasks.image_edit_tasks import edit_version_resource_type
from server.tool_runtime import CallerContext, ProjectScope, Services, ToolOutcome, ToolRequest

#: 可上传到素材库、也可从素材库选择已有图片的类型（与 inspect_images 同一套单数名称）。
LinkableImageType = Literal["character", "scene", "prop", "product", "character_derivative"]

_RESOURCE_ID = "资源 ID：资产为资产名称；character_derivative 为「本体名/衍生名」"
_ACCOUNT_ID = "素材库账号 ID，取自 get_provider_asset_links 返回的 accounts[].id"


class ProviderAssetLinksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: LinkableImageType = Field(description="图片类型")
    resource_id: str = Field(min_length=1, max_length=512, description=_RESOURCE_ID)
    recheck: StrictBool = Field(
        default=False,
        description="为 true 时请后台立即重新查询这张图片仍在处理中的入库任务；只在 polling=false 且状态未定时需要",
    )


class ListProviderLibraryAssetsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: StrictInt = Field(ge=1, description=_ACCOUNT_ID)
    page: StrictInt = Field(default=1, ge=1, le=200, description="页码，从 1 起")
    page_size: StrictInt = Field(default=20, ge=1, le=100, description="每页条数")


class UploadToProviderAssetLibraryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: LinkableImageType = Field(description="图片类型")
    resource_id: str = Field(min_length=1, max_length=512, description=_RESOURCE_ID)
    account_id: StrictInt = Field(ge=1, description=_ACCOUNT_ID)
    user_requested: Literal[True] = Field(
        description=(
            "用户在对话中明确要求把这张图片上传到供应商素材库时才传 true。"
            "生成被拒、你认为上传会有帮助、或用户只是询问素材库，都不构成要求，此时不要调用本工具"
        ),
    )


class SelectProviderLibraryAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: LinkableImageType = Field(description="要保存到的图片类型")
    resource_id: str = Field(min_length=1, max_length=512, description=_RESOURCE_ID)
    account_id: StrictInt = Field(ge=1, description=_ACCOUNT_ID)
    asset_id: str = Field(
        min_length=1, max_length=256, description="供应商素材 ID，取自 list_provider_library_assets 的 items[].id"
    )


class ReleaseProviderAssetLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    link_id: StrictInt = Field(ge=1, description="关联 ID，取自 get_provider_asset_links 返回的 links[].id")


async def _project_path(services: Services, scope: ProjectScope) -> Path:
    return await asyncio.to_thread(services.projects.get_project_path, scope.project_name)


async def _run(operation: str, services: Services, func: Callable[[AsyncSession], Awaitable[Any]]) -> ToolOutcome[Any]:
    """在一个数据库会话里执行操作；领域异常按默认语言转述，问题码沿用异常的 i18n key。"""
    try:
        async with services.sessions() as session:
            return ToolOutcome(value=await func(session))
    except ApiError as exc:
        return tool_problem(translate(exc.key, DEFAULT_LOCALE, **exc.params), code=exc.key, params=exc.params or None)
    except Exception as exc:
        return tool_error(operation, exc)


async def get_provider_asset_links(
    request: ToolRequest[ProviderAssetLinksRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[link_ops.LinkPanelResponse]:
    value = request.value
    resource_type = edit_version_resource_type(value.resource_type)

    async def query(session: AsyncSession) -> link_ops.LinkPanelResponse:
        panel = await link_ops.load_link_panel(
            session,
            project_path=await _project_path(services, scope),
            project_name=scope.project_name,
            resource_type=resource_type,
            resource_id=value.resource_id,
        )
        if not value.recheck:
            return panel
        rechecked = [
            await link_ops.recheck(session, project_name=scope.project_name, link_id=link.id)
            if link.state in IN_FLIGHT_STATES
            else link
            for link in panel.links
        ]
        return panel.model_copy(update={"links": rechecked})

    return await _run("get_provider_asset_links", services, query)


async def list_provider_library_assets(
    request: ToolRequest[ListProviderLibraryAssetsRequest],
    _scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[link_ops.ProviderAssetListResponse]:
    value = request.value
    return await _run(
        "list_provider_library_assets",
        services,
        lambda session: link_ops.browse_provider_assets(
            session, account_id=value.account_id, page=value.page, page_size=value.page_size
        ),
    )


async def upload_to_provider_asset_library(
    request: ToolRequest[UploadToProviderAssetLibraryRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[link_ops.LinkResponse]:
    value = request.value

    async def upload(session: AsyncSession) -> link_ops.LinkResponse:
        return await link_ops.start_upload(
            session,
            project_path=await _project_path(services, scope),
            project_name=scope.project_name,
            resource_type=edit_version_resource_type(value.resource_type),
            resource_id=value.resource_id,
            account_id=value.account_id,
        )

    return await _run("upload_to_provider_asset_library", services, upload)


async def select_provider_library_asset(
    request: ToolRequest[SelectProviderLibraryAssetRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    value = request.value

    async def select(session: AsyncSession) -> dict[str, Any]:
        imported = await import_provider_asset(
            session,
            project_name=scope.project_name,
            resource_type=edit_version_resource_type(value.resource_type),
            resource_id=value.resource_id,
            account_id=value.account_id,
            asset_id=value.asset_id,
            manager=services.projects,
            change_source="worker",
        )
        content_version = imported.content_version
        return {
            "content_version": link_ops.ContentVersionInfo(
                id=content_version.id, version=content_version.version, digest=content_version.digest
            ),
            "link": link_ops.link_response(imported.link),
        }

    return await _run("select_provider_library_asset", services, select)


async def release_provider_asset_link(
    request: ToolRequest[ReleaseProviderAssetLinkRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[link_ops.LinkResponse]:
    link_id = request.value.link_id
    return await _run(
        "release_provider_asset_link",
        services,
        lambda session: link_ops.release(session, project_name=scope.project_name, link_id=link_id),
    )


def upload_summary(link: link_ops.LinkResponse) -> str:
    return (
        f"已创建入库任务（关联 {link.id}），后台会完成上传与审核查询，本次调用不等待结果。"
        "稍后用 get_provider_asset_links 查询状态，状态为可用后再重新生成"
    )


def select_summary(value: dict[str, Any]) -> str:
    return f"已把素材库图片保存为第 {value['content_version'].version} 版，关联 {value['link'].id} 可用"


__all__ = [
    "LinkableImageType",
    "ListProviderLibraryAssetsRequest",
    "ProviderAssetLinksRequest",
    "ReleaseProviderAssetLinkRequest",
    "SelectProviderLibraryAssetRequest",
    "UploadToProviderAssetLibraryRequest",
    "get_provider_asset_links",
    "list_provider_library_assets",
    "release_provider_asset_link",
    "select_provider_library_asset",
    "select_summary",
    "upload_summary",
    "upload_to_provider_asset_library",
]
