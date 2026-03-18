from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.config import ProviderConfig, SystemSetting


def _mask_value(value: str) -> str:
    """Mask a secret value, showing first 4 and last 4 chars."""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}…{value[-4:]}"


class ProviderConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set(
        self, provider: str, key: str, value: str, *, is_secret: bool = False
    ) -> None:
        stmt = select(ProviderConfig).where(
            ProviderConfig.provider == provider, ProviderConfig.key == key
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            row.value = value
            row.is_secret = is_secret
            row.updated_at = datetime.now(timezone.utc)
        else:
            self.session.add(
                ProviderConfig(
                    provider=provider, key=key, value=value, is_secret=is_secret
                )
            )
        await self.session.flush()

    async def delete(self, provider: str, key: str) -> None:
        stmt = delete(ProviderConfig).where(
            ProviderConfig.provider == provider, ProviderConfig.key == key
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def get_all(self, provider: str) -> dict[str, str]:
        stmt = select(ProviderConfig).where(ProviderConfig.provider == provider)
        result = await self.session.execute(stmt)
        return {row.key: row.value for row in result.scalars()}

    async def get_all_masked(self, provider: str) -> dict[str, dict]:
        stmt = select(ProviderConfig).where(ProviderConfig.provider == provider)
        result = await self.session.execute(stmt)
        out: dict[str, dict] = {}
        for row in result.scalars():
            if row.is_secret:
                out[row.key] = {"is_set": True, "masked": _mask_value(row.value)}
            else:
                out[row.key] = {"is_set": True, "value": row.value}
        return out

    async def get_configured_keys(self, provider: str) -> list[str]:
        stmt = select(ProviderConfig.key).where(ProviderConfig.provider == provider)
        result = await self.session.execute(stmt)
        return [row for row in result.scalars()]


class SystemSettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set(self, key: str, value: str) -> None:
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            row.value = value
            row.updated_at = datetime.now(timezone.utc)
        else:
            self.session.add(SystemSetting(key=key, value=value))
        await self.session.flush()

    async def get(self, key: str, default: str = "") -> str:
        stmt = select(SystemSetting.value).where(SystemSetting.key == key)
        result = await self.session.execute(stmt)
        val = result.scalar_one_or_none()
        return val if val is not None else default

    async def get_all(self) -> dict[str, str]:
        stmt = select(SystemSetting)
        result = await self.session.execute(stmt)
        return {row.key: row.value for row in result.scalars()}
