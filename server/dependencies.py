"""Shared FastAPI dependency factories."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.service import ConfigService
from lib.db import get_async_session


def get_config_service(
    session: AsyncSession = Depends(get_async_session),
) -> ConfigService:
    return ConfigService(session)
