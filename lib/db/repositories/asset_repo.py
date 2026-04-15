"""AssetRepository: 异步 CRUD。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from lib.db.models.asset import Asset
from lib.db.repositories.base import BaseRepository


class AssetRepository(BaseRepository):
    async def create(
        self,
        *,
        type: str,
        name: str,
        description: str = "",
        voice_style: str = "",
        image_path: str | None = None,
        source_project: str | None = None,
    ) -> Asset:
        asset = Asset(
            id=str(uuid.uuid4()),
            type=type,
            name=name,
            description=description,
            voice_style=voice_style,
            image_path=image_path,
            source_project=source_project,
        )
        self.session.add(asset)
        await self.session.flush()
        return asset

    async def get_by_id(self, asset_id: str) -> Asset | None:
        return (await self.session.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()

    async def get_by_type_name(self, type: str, name: str) -> Asset | None:
        return (
            await self.session.execute(select(Asset).where(Asset.type == type, Asset.name == name))
        ).scalar_one_or_none()

    async def list(
        self,
        *,
        type: str | None,
        q: str | None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Asset]:
        stmt = select(Asset)
        if type:
            stmt = stmt.where(Asset.type == type)
        if q:
            stmt = stmt.where(Asset.name.contains(q))
        stmt = stmt.order_by(Asset.updated_at.desc()).limit(limit).offset(offset)
        return list((await self.session.execute(stmt)).scalars())

    async def update(self, asset_id: str, **fields: Any) -> Asset:
        asset = await self.get_by_id(asset_id)
        if asset is None:
            raise ValueError(f"Asset not found: {asset_id}")
        for k, v in fields.items():
            setattr(asset, k, v)
        await self.session.flush()
        return asset

    async def delete(self, asset_id: str) -> None:
        asset = await self.get_by_id(asset_id)
        if asset:
            await self.session.delete(asset)
            await self.session.flush()

    async def exists(self, type: str, name: str) -> bool:
        return await self.get_by_type_name(type, name) is not None
