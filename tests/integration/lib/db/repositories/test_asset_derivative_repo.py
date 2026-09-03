"""AssetRepository 的衍生子表读写：方言敏感，PostgreSQL 与 SQLite 上同一套判据。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lib.db.models.asset import Asset, AssetDerivative
from lib.db.repositories.asset_repo import AssetRepository


async def _character(session, name: str) -> Asset:
    asset = await AssetRepository(session).create(type="character", name=name, description="")
    await session.flush()
    return asset


@pytest.mark.asyncio
async def test_replace_derivatives_round_trips_name_description_and_image(async_session) -> None:
    repo = AssetRepository(async_session)
    asset = await _character(async_session, "王")

    await repo.replace_derivatives(
        asset.id,
        [("战斗装", "黑甲", "_global_assets/character/aa.png"), ("便装", "布衣", None)],
    )
    await async_session.flush()

    stored = [(d.name, d.description, d.image_path) for d in await repo.list_derivatives(asset.id)]
    assert stored == [
        ("便装", "布衣", None),
        ("战斗装", "黑甲", "_global_assets/character/aa.png"),
    ]


@pytest.mark.asyncio
async def test_replace_derivatives_drops_rows_absent_from_the_new_table(async_session) -> None:
    """整表替换：库里不再有的衍生连同它的图片登记一起消失。"""
    repo = AssetRepository(async_session)
    asset = await _character(async_session, "王")
    await repo.replace_derivatives(asset.id, [("战斗装", "黑甲", None), ("便装", "布衣", None)])
    await async_session.flush()

    await repo.replace_derivatives(asset.id, [("战斗装", "改写后的黑甲", None)])
    await async_session.flush()

    stored = [(d.name, d.description) for d in await repo.list_derivatives(asset.id)]
    assert stored == [("战斗装", "改写后的黑甲")]


@pytest.mark.asyncio
async def test_replace_derivatives_leaves_other_assets_untouched(async_session) -> None:
    repo = AssetRepository(async_session)
    mine = await _character(async_session, "王")
    other = await _character(async_session, "李")
    await repo.replace_derivatives(other.id, [("战斗装", "别人的", None)])
    await async_session.flush()

    await repo.replace_derivatives(mine.id, [])
    await async_session.flush()

    assert [d.description for d in await repo.list_derivatives(other.id)] == ["别人的"]


@pytest.mark.asyncio
async def test_list_derivatives_by_asset_ids_groups_and_skips_assets_without_any(async_session) -> None:
    repo = AssetRepository(async_session)
    with_derivatives = await _character(async_session, "王")
    without = await _character(async_session, "李")
    await repo.replace_derivatives(with_derivatives.id, [("战斗装", "黑甲", None)])
    await async_session.flush()

    grouped = await repo.list_derivatives_by_asset_ids([with_derivatives.id, without.id])

    assert set(grouped) == {with_derivatives.id}
    assert [d.name for d in grouped[with_derivatives.id]] == ["战斗装"]


@pytest.mark.asyncio
async def test_list_derivatives_by_asset_ids_returns_empty_for_no_ids(async_session) -> None:
    assert await AssetRepository(async_session).list_derivatives_by_asset_ids([]) == {}


@pytest.mark.asyncio
async def test_deleting_an_asset_removes_its_derivative_rows(async_session) -> None:
    """删本体即清子表；不依赖库级外键动作，两种方言同行为。"""
    repo = AssetRepository(async_session)
    asset = await _character(async_session, "王")
    survivor = await _character(async_session, "李")
    await repo.replace_derivatives(asset.id, [("战斗装", "黑甲", None)])
    await repo.replace_derivatives(survivor.id, [("便装", "布衣", None)])
    await async_session.flush()

    await repo.delete(asset.id)
    await async_session.flush()

    remaining = (await async_session.execute(select(AssetDerivative))).scalars().all()
    assert [(d.asset_id, d.name) for d in remaining] == [(survivor.id, "便装")]
