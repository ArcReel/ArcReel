"""内置供应商模型表的读写。"""

from __future__ import annotations

from sqlalchemy import delete, select

from lib.db.models.builtin_provider_model import BuiltinProviderModelState
from lib.db.repositories.base import BaseRepository


class BuiltinProviderModelRepository(BaseRepository):
    """官方收录模型的启用状态与用户添加的模型；调用方负责提交事务。"""

    async def list_all(self) -> list[BuiltinProviderModelState]:
        result = await self.session.execute(select(BuiltinProviderModelState).order_by(BuiltinProviderModelState.id))
        return list(result.scalars())

    async def list_for_provider(self, provider_id: str) -> list[BuiltinProviderModelState]:
        stmt = (
            select(BuiltinProviderModelState)
            .where(BuiltinProviderModelState.provider_id == provider_id)
            .order_by(BuiltinProviderModelState.id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def get(self, provider_id: str, model_id: str) -> BuiltinProviderModelState | None:
        stmt = select(BuiltinProviderModelState).where(
            BuiltinProviderModelState.provider_id == provider_id, BuiltinProviderModelState.model_id == model_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def set_enabled(self, provider_id: str, model_id: str, enabled: bool) -> None:
        """写入启用状态；还没有行时新建一行官方收录模型的状态。"""
        row = await self.get(provider_id, model_id)
        if row is None:
            self.session.add(BuiltinProviderModelState(provider_id=provider_id, model_id=model_id, enabled=enabled))
        else:
            row.enabled = enabled
        await self.session.flush()

    async def add_user_model(
        self, provider_id: str, model_id: str, *, media_type: str, entry_binding: str | None
    ) -> BuiltinProviderModelState:
        row = BuiltinProviderModelState(
            provider_id=provider_id,
            model_id=model_id,
            enabled=True,
            user_added=True,
            media_type=media_type,
            entry_binding=entry_binding,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete(self, provider_id: str, model_id: str) -> None:
        await self.session.execute(
            delete(BuiltinProviderModelState).where(
                BuiltinProviderModelState.provider_id == provider_id,
                BuiltinProviderModelState.model_id == model_id,
            )
        )
        await self.session.flush()
