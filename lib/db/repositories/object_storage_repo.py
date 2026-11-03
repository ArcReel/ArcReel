"""对象存储连接 repository。"""

from __future__ import annotations

from sqlalchemy import select

from lib.db.models.object_storage import ObjectStorageConnection
from lib.db.repositories.base import BaseRepository


class ObjectStorageConnectionRepository(BaseRepository):
    """实例至多一条对象存储连接；``get`` 取这一条，``save`` 新建或原位更新。"""

    async def get(self) -> ObjectStorageConnection | None:
        stmt = select(ObjectStorageConnection).order_by(ObjectStorageConnection.id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(
        self,
        *,
        kind: str,
        region: str,
        endpoint: str,
        bucket: str,
        access_key_id: str | None,
        secret_access_key: str | None,
    ) -> ObjectStorageConnection:
        """写入连接配置。密钥字段传 None 表示保留已保存的值；新建时由调用方保证两个密钥都已给出。"""
        conn = await self.get()
        if conn is None:
            conn = ObjectStorageConnection(
                kind=kind,
                region=region,
                endpoint=endpoint,
                bucket=bucket,
                access_key_id=access_key_id or "",
                secret_access_key=secret_access_key or "",
            )
            self.session.add(conn)
        else:
            conn.kind = kind
            conn.region = region
            conn.endpoint = endpoint
            conn.bucket = bucket
            if access_key_id:
                conn.access_key_id = access_key_id
            if secret_access_key:
                conn.secret_access_key = secret_access_key
        await self.session.flush()
        return conn

    async def delete(self) -> bool:
        conn = await self.get()
        if conn is None:
            return False
        await self.session.delete(conn)
        await self.session.flush()
        return True
