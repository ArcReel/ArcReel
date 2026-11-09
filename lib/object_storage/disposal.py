"""连接离开桶之前，按登记定位一次性删除或放弃对象，不扫描桶。"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from sqlalchemy import String, cast, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from lib.db.base import utc_now
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.object_storage import FormalObject, FormalObjectRef, ObjectStorageConnection, RelayObject
from lib.material.formal_objects import INGEST_HOLDER_PREFIX, object_key_lock
from lib.object_storage.client import ObjectStorageClient, ObjectStorageError
from lib.object_storage.connection import config_from_connection


def _ingest_links(connection_id: int) -> Select[tuple[int]]:
    formal_ids = select(FormalObject.id).where(FormalObject.connection_id == connection_id)
    holders = select(FormalObjectRef.project_name).where(FormalObjectRef.object_id.in_(formal_ids))
    relay_links = select(RelayObject.link_id).where(RelayObject.connection_id == connection_id)
    return select(ProviderAssetLink.id).where(
        ProviderAssetLink.state.in_({"pending", "submitting", "processing", "uncertain"}),
        (ProviderAssetLink.state == "pending")
        | (ProviderAssetLink.id.in_(relay_links))
        | ((INGEST_HOLDER_PREFIX + cast(ProviderAssetLink.id, String)).in_(holders)),
    )


async def pending_ingest_count(session: AsyncSession, connection_id: int) -> int:
    """仍借用这条连接上对象的未定论入库任务数。"""
    return len((await session.execute(_ingest_links(connection_id))).all())


@asynccontextmanager
async def dispose_connection_objects(
    session: AsyncSession, connection: ObjectStorageConnection, *, delete_remote: bool
) -> AsyncGenerator[dict[str, int]]:
    """调用方持有连接锁并确认没有受保护对象；返回删除失败的原因与数量，不提交事务。

    远端删除失败不保留记录。与后台回收共用对象锁，调用方须在退出上下文前提交连接与记录的变更。
    """
    keys = (
        await session.execute(select(FormalObject.object_key).where(FormalObject.connection_id == connection.id))
    ).scalars()
    async with AsyncExitStack() as stack:
        for key in sorted(set(keys)):
            await stack.enter_async_context(object_key_lock(key))
        formal = list(
            (await session.execute(select(FormalObject).where(FormalObject.connection_id == connection.id))).scalars()
        )
        relays = list(
            (await session.execute(select(RelayObject).where(RelayObject.connection_id == connection.id))).scalars()
        )
        failures: Counter[str] = Counter()
        if delete_remote:
            client: ObjectStorageClient | None = None
            for row in [*formal, *relays]:
                if row.state == "deleted":
                    continue
                # 历史记录可能来自更早的桶；当前凭证不能按新桶解释旧定位。
                if (row.endpoint, row.bucket) != (connection.endpoint, connection.bucket):
                    failures["unavailable"] += 1
                    continue
                try:
                    if client is None:
                        client = ObjectStorageClient(config_from_connection(connection))
                    await client.delete(row.object_key)
                except ObjectStorageError as exc:
                    if exc.kind != "not_found":
                        failures[exc.kind] += 1
        await session.execute(
            update(ProviderAssetLink)
            .where(ProviderAssetLink.id.in_(_ingest_links(connection.id)))
            .values(
                state="failed",
                error_code="object_storage_changed",
                error_message=None,
                settled_at=utc_now(),
                next_check_at=None,
                check_error=None,
            )
        )
        ids = [row.id for row in formal]
        await session.execute(delete(FormalObjectRef).where(FormalObjectRef.object_id.in_(ids)))
        await session.execute(delete(FormalObject).where(FormalObject.id.in_(ids)))
        await session.execute(delete(RelayObject).where(RelayObject.connection_id == connection.id))
        yield dict(failures)
