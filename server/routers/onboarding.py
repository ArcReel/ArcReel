"""Onboarding 引导状态 APIs.

首次使用引导的「已看过」标记。刻意与 `/system/config` 分开：那条路由是配置面板的
读写白名单，引导标记既不是用户可配置项，也不该被配置页的批量读写带上。

作用域：端点按「当前用户的引导状态」语义设计，身份走 `get_current_user` 解析；
标记本身今天落在实例级 `SystemSetting`（开源单用户形态）。将来引入多用户时，
替换下面的读写后端即可，端点契约不变。
"""

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
    """引导是否已看过。未设置视为未看过 —— 前端据此决定是否自动弹出。"""
    return OnboardingStatusResponse(seen=await _read_seen(svc))


@router.post("/onboarding/seen", response_model=OnboardingStatusResponse)
async def mark_onboarding_seen(
    svc: Annotated[ConfigService, Depends(get_config_service)],
    session: AsyncSession = Depends(get_async_session),
) -> OnboardingStatusResponse:
    """标记引导已看过。幂等 —— 跳过/看完/关闭三条退出路径都打到这里。"""
    await svc.set_setting(ONBOARDING_SEEN_KEY, "true")
    await session.commit()
    return OnboardingStatusResponse(seen=True)
