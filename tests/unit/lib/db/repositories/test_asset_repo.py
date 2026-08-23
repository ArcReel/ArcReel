"""AssetRepository 异步 CRUD 测试。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.db.repositories.asset_repo import AssetRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def repo(session):
    return AssetRepository(session)


async def test_create_and_get_by_id(repo):
    asset = await repo.create(
        type="character", name="A", description="d", voice_style="", image_path=None, source_project=None
    )
    fetched = await repo.get_by_id(asset.id)
    assert fetched is not None
    assert fetched.name == "A"


async def test_get_by_type_name_returns_none_when_absent(repo):
    assert await repo.get_by_type_name("scene", "missing") is None


async def test_list_filters_by_type_and_name_fuzzy(repo):
    await repo.create(type="character", name="王小明", description="", voice_style="")
    await repo.create(type="character", name="小师妹", description="", voice_style="")
    await repo.create(type="scene", name="庙宇", description="", voice_style="")

    chars = await repo.list(type="character", q=None, limit=10, offset=0)
    assert len(chars) == 2

    fuzzy = await repo.list(type="character", q="小", limit=10, offset=0)
    assert len(fuzzy) == 2

    scenes = await repo.list(type="scene", q=None, limit=10, offset=0)
    assert len(scenes) == 1


async def test_update_patch_fields(repo):
    asset = await repo.create(type="prop", name="玉佩", description="旧", voice_style="")
    updated = await repo.update(asset.id, description="新")
    assert updated.description == "新"


async def test_delete_removes_row(repo):
    asset = await repo.create(type="prop", name="key", description="", voice_style="")
    await repo.delete(asset.id)
    assert await repo.get_by_id(asset.id) is None


async def test_exists(repo):
    await repo.create(type="prop", name="key", description="", voice_style="")
    assert await repo.exists("prop", "key") is True
    assert await repo.exists("prop", "nope") is False
