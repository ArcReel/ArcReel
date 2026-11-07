"""Official-service settings API: master switch, first-notice marker on the market page, and instance-identifier reset.

Separate from ``/system/config``: settings-page and market-notice switches take effect immediately rather than using
configuration-panel draft saves. An empty official-service address makes ``available`` false; the switch value is stored but ineffective."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lib.market.official_service import OfficialServiceGateway, OfficialServiceState, get_official_service_gateway

router = APIRouter(prefix="/official-service", tags=["Official Service"])

Gateway = Annotated[OfficialServiceGateway, Depends(get_official_service_gateway)]


class OfficialServiceStateResponse(BaseModel):
    #: The official-service address is nonempty.
    available: bool
    #: The address is nonempty and the master switch is on; false hides all official-service elements in the frontend.
    enabled: bool
    notice_seen: bool
    #: Instance identifier sent with official-service requests; null until generated.
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
    """Delete the instance identifier; the next official-service request creates a new one, no longer associated with earlier reports or ratings."""
    return _response(await gateway.reset_instance_id())
