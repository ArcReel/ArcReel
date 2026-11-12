from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
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

    async def upsert(self, key: str, value: str) -> None:
        """单条语句写入：键不存在时插入，存在时覆盖。并发写同一个键不会因唯一约束失败，后写者生效。"""
        insert = pg_insert if self.session.get_bind().dialect.name == "postgresql" else sqlite_insert
        stmt = insert(SystemSetting).values(key=key, value=value, updated_at=datetime.now(UTC))
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[SystemSetting.key],
                set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
            )
        )

    async def insert_if_absent(self, key: str, value: str) -> None:
        """单条语句写入：键不存在时插入，已存在时什么都不改。"""
        insert = pg_insert if self.session.get_bind().dialect.name == "postgresql" else sqlite_insert
        stmt = insert(SystemSetting).values(key=key, value=value, updated_at=datetime.now(UTC))
        await self.session.execute(stmt.on_conflict_do_nothing(index_elements=[SystemSetting.key]))

    async def get_prefixed(self, prefix: str) -> dict[str, str]:
        """键以 ``prefix`` 开头的全部设置。"""
        stmt = select(SystemSetting.key, SystemSetting.value).where(
            SystemSetting.key.startswith(prefix, autoescape=True)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row.key: row.value for row in rows}

    async def delete(self, key: str) -> None:
        stmt = delete(SystemSetting).where(SystemSetting.key == key)
        await self.session.execute(stmt)
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
