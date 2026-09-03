"""AssetRepository: 全局资产库条目与其衍生子表的异步 CRUD。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select

from lib.db.models.asset import Asset, AssetDerivative
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
        audio_path: str | None = None,
        source_project: str | None = None,
    ) -> Asset:
        asset = Asset(
            id=str(uuid.uuid4()),
            type=type,
            name=name,
            description=description,
            voice_style=voice_style,
            image_path=image_path,
            audio_path=audio_path,
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

    async def get_by_ids(self, asset_ids: list[str]) -> list[Asset]:
        if not asset_ids:
            return []
        return list((await self.session.execute(select(Asset).where(Asset.id.in_(asset_ids)))).scalars())

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
            # 显式删衍生行而非依赖 FK CASCADE：级联只在开启 foreign_keys pragma 的连接上
            # 生效，应用引擎虽统一开启（engine.py），脚本或外部工具直连时不保证。
            await self.session.execute(delete(AssetDerivative).where(AssetDerivative.asset_id == asset_id))
            await self.session.delete(asset)
            await self.session.flush()

    async def exists(self, type: str, name: str) -> bool:
        return await self.get_by_type_name(type, name) is not None

    async def list_derivatives(self, asset_id: str) -> list[AssetDerivative]:
        """按登记名排序读出一条资产的全部衍生。"""
        stmt = select(AssetDerivative).where(AssetDerivative.asset_id == asset_id).order_by(AssetDerivative.name)
        return list((await self.session.execute(stmt)).scalars())

    async def list_derivatives_by_asset_ids(self, asset_ids: Sequence[str]) -> dict[str, list[AssetDerivative]]:
        """一次查出多条资产的衍生，按 ``asset_id`` 归组；没有衍生的资产不出现在结果里。

        列表页一屏可能有上百条资产，逐条查会退化成 N+1 次往返。
        """
        if not asset_ids:
            return {}
        stmt = (
            select(AssetDerivative)
            .where(AssetDerivative.asset_id.in_(asset_ids))
            .order_by(AssetDerivative.asset_id, AssetDerivative.name)
        )
        grouped: dict[str, list[AssetDerivative]] = {}
        for row in (await self.session.execute(stmt)).scalars():
            grouped.setdefault(row.asset_id, []).append(row)
        return grouped

    async def replace_derivatives(
        self,
        asset_id: str,
        derivatives: Sequence[tuple[str, str, str | None]],
    ) -> list[AssetDerivative]:
        """把一条资产的衍生整表换成给定的 ``(名, 描述, 图片相对路径)`` 序列。

        衍生随本体整套进出资产库，没有逐条增删的入口；覆盖入库即整表替换。删除旧行留给
        调用方在同一事务里提交，旧行指向的图片文件由调用方在 commit 后清理。
        """
        await self.session.execute(delete(AssetDerivative).where(AssetDerivative.asset_id == asset_id))
        rows = [
            AssetDerivative(
                id=str(uuid.uuid4()),
                asset_id=asset_id,
                name=name,
                description=description,
                image_path=image_path,
            )
            for name, description, image_path in derivatives
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows
