"""从已保存的连接配置得到对象存储客户端。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.object_storage import ObjectStorageConnection
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.object_storage.client import ObjectStorageClient, ObjectStorageConfig


def config_from_connection(conn: ObjectStorageConnection) -> ObjectStorageConfig:
    return ObjectStorageConfig(
        kind=conn.kind,
        region=conn.region,
        endpoint=conn.endpoint,
        bucket=conn.bucket,
        access_key_id=conn.access_key_id,
        secret_access_key=conn.secret_access_key,
    )


async def load_object_storage_client(session: AsyncSession) -> ObjectStorageClient | None:
    """实例当前的对象存储客户端；没有配置连接时返回 None，调用方按未启用对象存储处理。"""
    conn = await ObjectStorageConnectionRepository(session).get()
    return ObjectStorageClient(config_from_connection(conn)) if conn else None
