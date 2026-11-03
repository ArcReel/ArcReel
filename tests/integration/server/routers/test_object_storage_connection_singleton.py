"""并发首次保存对象存储连接只建出一条连接，两个请求都得到明确结果。

两个请求都先读到「还没有连接」再各自插入，是先查后插会留下两条连接的交错；用屏障让两次读取都完成
后再继续，确定性地复现这一交错。``concurrent_session_factory`` 为每个请求提供独立连接，PostgreSQL 与
SQLite（WAL 文件库）上都按真实的唯一索引冲突运行。

同一进程里连接变更经连接锁串行（``connection_change_lock``），这一交错只会发生在锁之外（例如另一个进程）。
用例为每个请求换一把独立的锁，验证唯一索引与重做这道兜底仍然成立。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db import get_async_session
from lib.db.models.object_storage import ObjectStorageConnection
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from server.error_handlers import register_error_handlers
from server.routers import object_storage
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


def _body(bucket: str, suffix: str) -> dict[str, str]:
    return {
        "region": "cn-beijing",
        "endpoint": "tos-cn-beijing.volces.com",
        "bucket": bucket,
        "access_key_id": f"TEST-TOS-AK-{suffix}",
        "secret_access_key": f"TEST-TOS-SECRET-{suffix}",
    }


async def test_concurrent_first_saves_leave_one_connection(
    concurrent_session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
):
    app = FastAPI()

    async def _session():
        async with concurrent_session_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    override_auth(app)
    app.include_router(object_storage.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)

    both_read = asyncio.Barrier(2)
    reads = 0
    original_get = ObjectStorageConnectionRepository.get

    async def _get_after_both_read(self: ObjectStorageConnectionRepository) -> ObjectStorageConnection | None:
        nonlocal reads
        conn = await original_get(self)
        reads += 1
        if reads <= 2:
            assert conn is None
            await both_read.wait()
        return conn

    monkeypatch.setattr(ObjectStorageConnectionRepository, "get", _get_after_both_read)
    monkeypatch.setattr(object_storage, "connection_change_lock", asyncio.Lock)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.wait_for(
            asyncio.gather(
                client.put("/api/v1/object-storage/connection", json=_body("bucket-a", "A")),
                client.put("/api/v1/object-storage/connection", json=_body("bucket-b", "B")),
            ),
            timeout=60,
        )

    assert [resp.status_code for resp in responses] == [200, 200]
    async with concurrent_session_factory() as session:
        count = (await session.execute(select(func.count()).select_from(ObjectStorageConnection))).scalar_one()
        conn = await original_get(ObjectStorageConnectionRepository(session))
    assert count == 1
    assert conn is not None
    # 后提交的请求按更新那一条处理：最终连接整体是其中一个请求的内容，不会混合两份。
    saved = {"bucket-a": "A", "bucket-b": "B"}[conn.bucket]
    assert (conn.access_key_id, conn.secret_access_key) == (f"TEST-TOS-AK-{saved}", f"TEST-TOS-SECRET-{saved}")
