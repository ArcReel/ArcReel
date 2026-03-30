from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.config import ProviderConfig, SystemSetting


def mask_secret(value: str) -> str:
    """Mask a secret value, showing first 4 and last 4 chars."""
    raw = value.strip()
    if len(raw) <= 8:
        return "••••"
    return f"{raw[:4]}…{raw[-4:]}"


class ProviderConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set(
        self,
        provider: str,
        key: str,
        value: str,
        *,
        is_secret: bool = False,
        flush: bool = True,
    ) -> None:
        stmt = select(ProviderConfig).where(ProviderConfig.provider == provider, ProviderConfig.key == key)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            row.value = value
            row.is_secret = is_secret
            row.updated_at = datetime.now(UTC)
        else:
            self.session.add(ProviderConfig(provider=provider, key=key, value=value, is_secret=is_secret))
        if flush:
            await self.session.flush()

    async def delete(self, provider: str, key: str, *, flush: bool = True) -> None:
        stmt = delete(ProviderConfig).where(ProviderConfig.provider == provider, ProviderConfig.key == key)
        await self.session.execute(stmt)
        if flush:
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
                out[row.key] = {"is_set": True, "masked": mask_secret(row.value)}
            else:
                out[row.key] = {"is_set": True, "value": row.value}
        return out

    async def get_configured_keys(self, provider: str) -> list[str]:
        stmt = select(ProviderConfig.key).where(ProviderConfig.provider == provider)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_all_configured_keys_bulk(self) -> dict[str, list[str]]:
        """Fetch configured keys for ALL providers in a single query."""
        stmt = select(ProviderConfig.provider, ProviderConfig.key)
        result = await self.session.execute(stmt)
        out: dict[str, list[str]] = {}
        for provider, key in result:
            out.setdefault(provider, []).append(key)
        return out

    async def get_all_configs_bulk(self) -> dict[str, dict[str, str]]:
        """Fetch all config key-value pairs for ALL providers in a single query."""
        stmt = select(ProviderConfig)
        result = await self.session.execute(stmt)
        out: dict[str, dict[str, str]] = {}
        for row in result.scalars():
            out.setdefault(row.provider, {})[row.key] = row.value
        return out


class SystemSettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set(self, key: str, value: str) -> None:
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            row.value = value
            row.updated_at = datetime.now(UTC)
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
