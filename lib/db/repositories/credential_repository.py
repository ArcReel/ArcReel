"""Provider credential repository."""

from __future__ import annotations

from sqlalchemy import select, update

from lib.config.url_utils import normalize_base_url
from lib.db.models.credential import ProviderCredential
from lib.db.repositories.base import BaseRepository

_UNSET = object()


class CredentialRepository(BaseRepository):
    async def create(
        self,
        provider: str,
        name: str,
        api_key: str | None = None,
        credentials_path: str | None = None,
        base_url: str | None = None,
    ) -> ProviderCredential:
        """创建凭证。若为该供应商的第一条，自动设为活跃。"""
        is_first = not await self.has_active_credential(provider)
        cred = ProviderCredential(
            provider=provider,
            name=name,
            api_key=api_key,
            credentials_path=credentials_path,
            base_url=normalize_base_url(base_url),
            is_active=is_first,
        )
        self.session.add(cred)
        await self.session.flush()
        return cred

    async def get_by_id(self, cred_id: int) -> ProviderCredential | None:
        stmt = select(ProviderCredential).where(ProviderCredential.id == cred_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_provider(self, provider: str) -> list[ProviderCredential]:
        stmt = (
            select(ProviderCredential)
            .where(ProviderCredential.provider == provider)
            .order_by(ProviderCredential.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_active(self, provider: str) -> ProviderCredential | None:
        stmt = select(ProviderCredential).where(
            ProviderCredential.provider == provider,
            ProviderCredential.is_active == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def has_active_credential(self, provider: str) -> bool:
        return await self.get_active(provider) is not None

    async def get_active_credentials_bulk(self) -> dict[str, ProviderCredential]:
        """批量获取所有供应商的活跃凭证。"""
        stmt = select(ProviderCredential).where(
            ProviderCredential.is_active == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return {c.provider: c for c in result.scalars()}

    async def activate(self, cred_id: int, provider: str) -> None:
        """激活指定凭证，同时取消同供应商的其他活跃标记。"""
        await self.session.execute(
            update(ProviderCredential).where(ProviderCredential.provider == provider).values(is_active=False)
        )
        await self.session.execute(
            update(ProviderCredential).where(ProviderCredential.id == cred_id).values(is_active=True)
        )

    async def update(
        self,
        cred_id: int,
        *,
        name: str | None = None,
        api_key: str | None = None,
        credentials_path: str | None = None,
        base_url: str | None | object = _UNSET,
    ) -> None:
        """更新凭证字段。仅更新非 None 参数（base_url 用 _UNSET 表示未传入）。"""
        cred = await self.get_by_id(cred_id)
        if cred is None:
            return
        if name is not None:
            cred.name = name
        if api_key is not None:
            cred.api_key = api_key
        if credentials_path is not None:
            cred.credentials_path = credentials_path
        if base_url is not _UNSET:
            cred.base_url = normalize_base_url(base_url)  # type: ignore[arg-type]

    async def delete(self, cred_id: int) -> None:
        """删除凭证。若删除的是活跃凭证，自动将最早的另一条设为活跃。"""
        cred = await self.get_by_id(cred_id)
        if cred is None:
            return
        provider = cred.provider
        was_active = cred.is_active
        await self.session.delete(cred)
        await self.session.flush()

        if was_active:
            stmt = (
                select(ProviderCredential)
                .where(ProviderCredential.provider == provider)
                .order_by(ProviderCredential.created_at)
                .limit(1)
            )
            result = await self.session.execute(stmt)
            next_cred = result.scalar_one_or_none()
            if next_cred:
                next_cred.is_active = True
