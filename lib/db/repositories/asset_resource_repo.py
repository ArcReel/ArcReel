"""全局资产多媒体资源的异步持久化。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from lib.db.models.asset import AssetResource
from lib.db.repositories.base import BaseRepository


class AssetResourceRepository(BaseRepository):
    async def create(
        self,
        *,
        asset_id: str,
        resource_key: str,
        media_type: str,
        path: str,
        origin: str = "catalog",
        mime_type: str | None = None,
        source_url: str | None = None,
        sha256: str | None = None,
        byte_size: int | None = None,
        revision: str | None = None,
        sort_order: int = 0,
        source_fields_json: str = "[]",
    ) -> AssetResource:
        resource = AssetResource(
            id=str(uuid.uuid4()),
            asset_id=asset_id,
            resource_key=resource_key,
            origin=origin,
            media_type=media_type,
            path=path,
            mime_type=mime_type,
            source_url=source_url,
            sha256=sha256,
            byte_size=byte_size,
            revision=revision,
            sort_order=sort_order,
            source_fields_json=source_fields_json,
        )
        self.session.add(resource)
        await self.session.flush()
        return resource

    async def get_by_asset_key(self, asset_id: str, resource_key: str) -> AssetResource | None:
        return (
            await self.session.execute(
                select(AssetResource).where(
                    AssetResource.asset_id == asset_id,
                    AssetResource.resource_key == resource_key,
                )
            )
        ).scalar_one_or_none()

    async def get_by_id(self, resource_id: str) -> AssetResource | None:
        return (
            await self.session.execute(select(AssetResource).where(AssetResource.id == resource_id))
        ).scalar_one_or_none()

    async def update(self, resource: AssetResource, **fields: Any) -> AssetResource:
        for key, value in fields.items():
            setattr(resource, key, value)
        await self.session.flush()
        return resource

    async def delete(self, resource: AssetResource) -> None:
        await self.session.delete(resource)
        await self.session.flush()
