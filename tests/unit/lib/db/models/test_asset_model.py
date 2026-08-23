"""Asset ORM 模型结构测试。"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

import lib.db.models  # noqa: F401 — ensure all models registered for Base.metadata
from lib.db.models.asset import Asset


async def test_asset_create_and_fetch(db_session):
    asset = Asset(
        id="00000000-0000-0000-0000-000000000001",
        type="character",
        name="王小明",
        description="白衣少年",
        voice_style="清亮",
        image_path="_global_assets/character/abc.png",
        source_project="demo",
    )
    db_session.add(asset)
    await db_session.commit()

    row = (await db_session.execute(select(Asset).where(Asset.name == "王小明"))).scalar_one()
    assert row.type == "character"
    assert row.voice_style == "清亮"
    assert row.image_path == "_global_assets/character/abc.png"
    assert row.description == "白衣少年"
    assert row.source_project == "demo"
    assert row.created_at is not None
    assert row.updated_at is not None


async def test_asset_unique_type_name(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as db_session:
        db_session.add(Asset(id="id-1", type="prop", name="玉佩", description=""))
        await db_session.commit()

    async with factory() as db_session:
        db_session.add(Asset(id="id-2", type="prop", name="玉佩", description=""))
        with pytest.raises(IntegrityError):
            await db_session.commit()
