"""Onboarding status APIs.

The “seen” marker for first-use onboarding. Deliberately separate from `/system/config`: that route allowlists
configuration-panel reads/writes, while onboarding markers are neither user-configurable settings nor part of
the configuration page’s batch reads/writes.

Scope: endpoints represent the current user’s onboarding state, with identity resolved by `get_current_user`.
The marker is currently stored in instance-level `SystemSetting` for the open-source single-user setup.
Future multi-user support can replace the read/write backend below without changing the endpoint contract."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.service import ConfigService
from lib.db import get_async_session
from server.dependencies import get_config_service

router = APIRouter()

ONBOARDING_SEEN_KEY = "onboarding_seen"


class OnboardingStatusResponse(BaseModel):
    seen: bool


async def _read_seen(svc: ConfigService) -> bool:
    return await svc.get_setting(ONBOARDING_SEEN_KEY) == "true"


@router.get("/onboarding/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    svc: Annotated[ConfigService, Depends(get_config_service)],
) -> OnboardingStatusResponse:
    """Whether onboarding has been seen. Unset means unseen; the frontend uses this to decide whether to open it automatically."""
    return OnboardingStatusResponse(seen=await _read_seen(svc))


@router.post("/onboarding/seen", response_model=OnboardingStatusResponse)
async def mark_onboarding_seen(
    svc: Annotated[ConfigService, Depends(get_config_service)],
    session: AsyncSession = Depends(get_async_session),
) -> OnboardingStatusResponse:
    """Mark onboarding as seen. Idempotent: skip, finish, and close exit paths all call this endpoint."""
    await svc.set_setting(ONBOARDING_SEEN_KEY, "true")
    await session.commit()
    return OnboardingStatusResponse(seen=True)
