"""Concurrent first object-storage saves create one connection and give both requests explicit outcomes.

Both reads first see no connection, then insert, reproducing check-then-insert duplicates deterministically through barriers
after both reads. ``concurrent_session_factory`` supplies independent connections, exercising actual unique-index conflicts on PostgreSQL and
SQLite WAL files.

Process-local ``connection_change_lock`` serializes changes, so races occur outside it, such as other processes.
Give requests independent locks to verify unique-index/retry safeguards.
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
    # Later requests update existing connections; final state equals one complete request, never mixed contents.
    saved = {"bucket-a": "A", "bucket-b": "B"}[conn.bucket]
    assert (conn.access_key_id, conn.secret_access_key) == (f"TEST-TOS-AK-{saved}", f"TEST-TOS-SECRET-{saved}")
