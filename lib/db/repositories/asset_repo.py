"""AssetRepository: asynchronous CRUD for global asset-library entries and their derivative child table."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Select, delete, func, select

from lib.db.models.asset import Asset, AssetDerivative
from lib.db.repositories.base import BaseRepository


def _match_name[S: Select](stmt: S, q: str | None) -> S:
    """Search predicate shared by listing and counting: names literally contain ``q``, case-insensitive for ordinary Unicode letters.

    ``icontains`` compiles to ILIKE on PostgreSQL and compares lower() on both sides in
    SQLite. SQLite connections register Unicode lower; locale-specific folding may still
    differ. ``autoescape`` matches ``%`` and ``_`` as literal characters.
    """
    return stmt.where(Asset.name.icontains(q, autoescape=True)) if q else stmt


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
        asset_id: str | None = None,
        **image_fields: Any,
    ) -> Asset:
        """Create an asset; ``image_fields`` supplies image-content columns beyond ``image_path`` (digest, content identity, remote locator).

        Generate ``asset_id`` when omitted; callers saving images before the entry and
        registering references under the entry must allocate it beforehand.
        """
        asset = Asset(
            id=asset_id or str(uuid.uuid4()),
            type=type,
            name=name,
            description=description,
            voice_style=voice_style,
            image_path=image_path,
            audio_path=audio_path,
            source_project=source_project,
            **image_fields,
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
        stmt = _match_name(select(Asset), q)
        if type:
            stmt = stmt.where(Asset.type == type)
        # Use id as a tie-breaker so equal update times retain stable ordering across queries, preventing offset-pagination duplicates or omissions
        stmt = stmt.order_by(Asset.updated_at.desc(), Asset.id.desc()).limit(limit).offset(offset)
        return list((await self.session.execute(stmt)).scalars())

    async def count_by_type(self, *, q: str | None) -> dict[str, int]:
        """Count entries whose names match ``q`` by type; omit types with no matches."""
        stmt = _match_name(select(Asset.type, func.count()), q).group_by(Asset.type)
        return dict((await self.session.execute(stmt)).tuples().all())

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
            # Delete derivative rows explicitly rather than relying on FK CASCADE: it requires foreign_keys
            # pragma. The application engine enables it (engine.py), but direct script/external connections may not.
            await self.session.execute(delete(AssetDerivative).where(AssetDerivative.asset_id == asset_id))
            await self.session.delete(asset)
            await self.session.flush()

    async def exists(self, type: str, name: str) -> bool:
        return await self.get_by_type_name(type, name) is not None

    async def list_derivatives(self, asset_id: str) -> list[AssetDerivative]:
        """Read all derivatives for one asset, sorted by registered name."""
        stmt = select(AssetDerivative).where(AssetDerivative.asset_id == asset_id).order_by(AssetDerivative.name)
        return list((await self.session.execute(stmt)).scalars())

    async def list_derivatives_by_asset_ids(self, asset_ids: Sequence[str]) -> dict[str, list[AssetDerivative]]:
        """Fetch derivatives for multiple assets in one query, grouped by ``asset_id``; omit assets without derivatives.

        A list page may show hundreds of assets; querying each would degrade into N+1 round trips.
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
        image_fields: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[AssetDerivative]:
        """Replace an asset's entire derivative table with the supplied ``(名, 描述, 图片相对路径)`` sequence.

        ``image_fields`` maps derivative names to image-content columns beyond image paths
        (digest, content identity, remote locator).

        Derivatives enter and leave the library with their base, without per-item add/remove
        entry points; overwrite ingestion replaces the whole table. Callers commit old-row
        deletions in the same transaction and clean up their image files after commit.
        """
        await self.session.execute(delete(AssetDerivative).where(AssetDerivative.asset_id == asset_id))
        rows = [
            AssetDerivative(
                id=str(uuid.uuid4()),
                asset_id=asset_id,
                name=name,
                description=description,
                image_path=image_path,
                **(image_fields or {}).get(name, {}),
            )
            for name, description, image_path in derivatives
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows
