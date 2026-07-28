"""Asset ORM 模型结构测试。"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import lib.db.models  # noqa: F401 — ensure all models registered for Base.metadata
from lib.db.base import Base
from lib.db.models.asset import Asset


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


async def test_asset_create_and_fetch(session):
    asset = Asset(
        id="00000000-0000-0000-0000-000000000001",
        type="character",
        name="王小明",
        description="白衣少年",
        voice_style="清亮",
        image_path="_global_assets/character/abc.png",
        source_project="demo",
    )
    session.add(asset)
    await session.commit()

    row = (await session.execute(select(Asset).where(Asset.name == "王小明"))).scalar_one()
    assert row.type == "character"
    assert row.voice_style == "清亮"
    assert row.image_path == "_global_assets/character/abc.png"
    assert row.description == "白衣少年"
    assert row.source_project == "demo"
    assert row.created_at is not None
    assert row.updated_at is not None


async def test_asset_unique_type_name(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Asset(id="id-1", type="prop", name="玉佩", description=""))
        await session.commit()

    async with factory() as session:
        session.add(Asset(id="id-2", type="prop", name="玉佩", description=""))
        with pytest.raises(IntegrityError):
            await session.commit()
