"""Structured aliases for global assets."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select

from lib.asset_types import asset_name_comparison_key, validate_asset_name
from lib.db.models.asset import AssetAlias
from lib.db.repositories.base import BaseRepository


class AssetAliasRepository(BaseRepository):
    async def create(
        self,
        *,
        asset_id: str,
        alias: str,
        origin: str = "local",
        sort_order: int = 0,
    ) -> AssetAlias:
        normalized = validate_asset_name(alias)
        if len(normalized) > 200:
            raise ValueError("Asset alias exceeds 200 characters")
        row = AssetAlias(
            id=str(uuid.uuid4()),
            asset_id=asset_id,
            alias=normalized,
            comparison_key=asset_name_comparison_key(normalized),
            origin=origin,
            sort_order=sort_order,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def sync_catalog_aliases(self, asset_id: str, aliases: Iterable[str]) -> bool:
        """Replace catalog-owned aliases while preserving every local alias."""

        current = list(
            (await self.session.execute(select(AssetAlias).where(AssetAlias.asset_id == asset_id))).scalars()
        )
        local_keys = {row.comparison_key for row in current if row.origin != "catalog"}
        catalog_by_key = {row.comparison_key: row for row in current if row.origin == "catalog"}

        desired: dict[str, tuple[str, int]] = {}
        for sort_order, raw_alias in enumerate(aliases):
            try:
                alias = validate_asset_name(raw_alias)
            except ValueError:
                continue
            if len(alias) > 200:
                continue
            key = asset_name_comparison_key(alias)
            if key not in local_keys and key not in desired:
                desired[key] = (alias, sort_order)

        changed = False
        for key, row in catalog_by_key.items():
            if key not in desired:
                await self.session.delete(row)
                changed = True

        for key, (alias, sort_order) in desired.items():
            row = catalog_by_key.get(key)
            if row is None:
                await self.create(
                    asset_id=asset_id,
                    alias=alias,
                    origin="catalog",
                    sort_order=sort_order,
                )
                changed = True
            elif row.alias != alias or row.sort_order != sort_order:
                row.alias = alias
                row.sort_order = sort_order
                changed = True

        if changed:
            await self.session.flush()
        return changed
