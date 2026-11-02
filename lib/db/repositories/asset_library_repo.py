"""供应商素材库账号与推理凭证绑定 repository。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from lib.db.models.asset_library import AssetLibraryAccount, AssetLibraryBinding
from lib.db.repositories.base import BaseRepository


class AssetLibraryRepository(BaseRepository):
    # ── 账号 ──────────────────────────────────────────────────────

    async def list_accounts(self) -> list[AssetLibraryAccount]:
        result = await self.session.execute(select(AssetLibraryAccount).order_by(AssetLibraryAccount.id))
        return list(result.scalars())

    async def get_account(self, account_id: int) -> AssetLibraryAccount | None:
        return await self.session.get(AssetLibraryAccount, account_id)

    async def create_account(
        self,
        *,
        site: str,
        name: str,
        region: str,
        project_name: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> AssetLibraryAccount:
        account = AssetLibraryAccount(
            site=site,
            name=name,
            region=region,
            project_name=project_name,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
        self.session.add(account)
        await self.session.flush()
        return account

    async def delete_account(self, account: AssetLibraryAccount) -> None:
        bindings = await self.list_bindings(account_id=account.id)
        for binding in bindings:
            await self.session.delete(binding)
        await self.session.delete(account)
        await self.session.flush()

    # ── 绑定 ──────────────────────────────────────────────────────

    async def list_bindings(self, *, account_id: int | None = None) -> list[AssetLibraryBinding]:
        stmt = select(AssetLibraryBinding).order_by(AssetLibraryBinding.id)
        if account_id is not None:
            stmt = stmt.where(AssetLibraryBinding.account_id == account_id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get_binding_for_credential(self, credential_type: str, credential_id: int) -> AssetLibraryBinding | None:
        stmt = select(AssetLibraryBinding).where(
            AssetLibraryBinding.credential_type == credential_type,
            AssetLibraryBinding.credential_id == credential_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_binding(
        self,
        *,
        account_id: int,
        credential_type: str,
        credential_id: int,
        fingerprint: str,
        confirmed_at: datetime,
    ) -> AssetLibraryBinding:
        """把推理凭证绑定到账号并记下本次确认的摘要；凭证已绑定其他账号时改绑到这个账号。"""
        binding = await self.get_binding_for_credential(credential_type, credential_id)
        if binding is None:
            binding = AssetLibraryBinding(
                account_id=account_id,
                credential_type=credential_type,
                credential_id=credential_id,
                confirmed_fingerprint=fingerprint,
                confirmed_at=confirmed_at,
            )
            self.session.add(binding)
        else:
            binding.account_id = account_id
            binding.confirmed_fingerprint = fingerprint
            binding.confirmed_at = confirmed_at
        await self.session.flush()
        return binding

    async def delete_binding(self, binding: AssetLibraryBinding) -> None:
        await self.session.delete(binding)
        await self.session.flush()
