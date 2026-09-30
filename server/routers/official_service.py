"""官方服务设置 API：总开关、市场页首次告知标记与实例标识重置。

与 ``/system/config`` 分开：开关由设置页与市场页的告知条即时生效，不走配置面板的草稿保存。
官方服务地址为空时 ``available`` 为 false，开关存值但不生效。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lib.market.official_service import OfficialServiceGateway, OfficialServiceState, get_official_service_gateway

router = APIRouter(prefix="/official-service", tags=["Official Service"])

Gateway = Annotated[OfficialServiceGateway, Depends(get_official_service_gateway)]


class OfficialServiceStateResponse(BaseModel):
    #: 官方服务地址非空。
    available: bool
    #: 地址非空且总开关为开；为 false 时前端不展示任何官方服务元素。
    enabled: bool
    notice_seen: bool
    #: 随请求发往官方服务的实例标识；尚未生成时为 null。
    instance_id: str | None


class UpdateOfficialServiceRequest(BaseModel):
    enabled: bool | None = None
    notice_seen: bool | None = None


def _response(state: OfficialServiceState) -> OfficialServiceStateResponse:
    return OfficialServiceStateResponse(
        available=state.available, enabled=state.enabled, notice_seen=state.notice_seen, instance_id=state.instance_id
    )


@router.get("", response_model=OfficialServiceStateResponse)
async def get_official_service(gateway: Gateway) -> OfficialServiceStateResponse:
    return _response(await gateway.state())


@router.patch("", response_model=OfficialServiceStateResponse)
async def update_official_service(body: UpdateOfficialServiceRequest, gateway: Gateway) -> OfficialServiceStateResponse:
    return _response(await gateway.update(enabled=body.enabled, notice_seen=body.notice_seen))


@router.post("/instance-id/reset", response_model=OfficialServiceStateResponse)
async def reset_instance_id(gateway: Gateway) -> OfficialServiceStateResponse:
    """删除实例标识；下次请求官方服务时生成新的，与此前的上报和评分不再关联。"""
    return _response(await gateway.reset_instance_id())
