"""安装记录写入在独立连接并发时由唯一约束收敛。"""

import asyncio

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.api_errors import ConflictError
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.market_installation import MarketInstallation
from lib.market.installations import write_installation
from tests.factories import custom_endpoint_definition


async def test_concurrent_install_leaves_one_endpoint_and_record(
    concurrent_session_factory: async_sessionmaker[AsyncSession],
):
    definition = custom_endpoint_definition()

    async def install() -> int | None:
        async with concurrent_session_factory() as session:
            try:
                endpoint = await write_installation(
                    session, source_key="url:source", slug="example", definition=definition, overwrite_endpoint_id=None
                )
                await session.commit()
                return endpoint.id
            except (IntegrityError, ConflictError):
                await session.rollback()
                return None

    results = await asyncio.gather(install(), install())
    assert sum(result is not None for result in results) == 1
    async with concurrent_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CustomEndpoint)) == 1
        assert await session.scalar(select(func.count()).select_from(MarketInstallation)) == 1
