"""官方服务实例标识在并发首次请求之间保持一致。"""

import asyncio
from types import TracebackType

import httpx
import respx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.market.official_service import EntryRef, OfficialServiceClient, OfficialServiceGateway


async def test_concurrent_first_requests_keep_the_first_committed_instance_id(
    concurrent_session_factory: async_sessionmaker[AsyncSession],
):
    read_finished = asyncio.Event()
    resume = asyncio.Event()
    first_open: asyncio.Task[OfficialServiceClient | None] | None = None

    class PausedSession(AsyncSession):
        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            await super().__aexit__(exc_type, exc_val, exc_tb)
            if asyncio.current_task() is first_open and not read_finished.is_set():
                # 在空标识的读取会话关闭后暂停，让另一请求先提交标识。
                read_finished.set()
                await resume.wait()

    factory = async_sessionmaker[AsyncSession](
        concurrent_session_factory.kw["bind"], class_=PausedSession, expire_on_commit=False
    )
    ref = EntryRef(type="endpoint", source="github:ArcReel/arcreel-market@HEAD", slug="example")
    async with httpx.AsyncClient() as network:
        gateway = OfficialServiceGateway(factory, base_url="https://official.test", http_client=lambda: network)
        with respx.mock(assert_all_called=True) as remote:
            route = remote.put("https://official.test/api/v1/market/ratings").respond(204)
            async with asyncio.timeout(10):
                first_open = asyncio.create_task(gateway.open())
                await read_finished.wait()
                try:
                    second_client = await gateway.open()
                    assert second_client is not None
                    await second_client.rate(ref, stars=4)
                finally:
                    resume.set()
                first_client = await first_open
                assert first_client is not None
                await first_client.rate(ref, stars=5)
            instance_ids = [call.request.headers["X-ArcReel-Instance"] for call in route.calls]
        assert len(instance_ids) == 2
        assert instance_ids[0] == instance_ids[1] == (await gateway.state()).instance_id


async def test_first_request_regenerates_when_the_winning_instance_id_is_reset_meanwhile(
    concurrent_session_factory: async_sessionmaker[AsyncSession],
):
    read_finished = asyncio.Event()
    rolled_back = asyncio.Event()
    resume_insert = asyncio.Event()
    resume_reread = asyncio.Event()
    first_open: asyncio.Task[OfficialServiceClient | None] | None = None

    class PausedSession(AsyncSession):
        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            await super().__aexit__(exc_type, exc_val, exc_tb)
            if asyncio.current_task() is first_open and not read_finished.is_set():
                # 空标识的读取会话关闭后暂停，让另一请求先提交标识。
                read_finished.set()
                await resume_insert.wait()

        async def rollback(self) -> None:
            await super().rollback()
            if asyncio.current_task() is first_open and not rolled_back.is_set():
                # 插入冲突回滚后、重读之前暂停，让先提交的标识被重置删除。
                rolled_back.set()
                await resume_reread.wait()

    factory = async_sessionmaker[AsyncSession](
        concurrent_session_factory.kw["bind"], class_=PausedSession, expire_on_commit=False
    )
    ref = EntryRef(type="endpoint", source="github:ArcReel/arcreel-market@HEAD", slug="example")
    async with httpx.AsyncClient() as network:
        gateway = OfficialServiceGateway(factory, base_url="https://official.test", http_client=lambda: network)
        with respx.mock(assert_all_called=True) as remote:
            route = remote.put("https://official.test/api/v1/market/ratings").respond(204)
            async with asyncio.timeout(10):
                first_open = asyncio.create_task(gateway.open())
                try:
                    await read_finished.wait()
                    assert await gateway.open() is not None
                    resume_insert.set()
                    await rolled_back.wait()
                    await gateway.reset_instance_id()
                finally:
                    resume_insert.set()
                    resume_reread.set()
                first_client = await first_open
                assert first_client is not None
                await first_client.rate(ref, stars=5)
            header = route.calls.last.request.headers["X-ArcReel-Instance"]
        assert header
        assert header == (await gateway.state()).instance_id
