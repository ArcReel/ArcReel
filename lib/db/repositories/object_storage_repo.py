"""Object-storage connection repository."""

from __future__ import annotations

from sqlalchemy import select

from lib.db.models.object_storage import CURRENT_CONNECTION_SLOT, ObjectStorageConnection
from lib.db.repositories.base import BaseRepository


class ObjectStorageConnectionRepository(BaseRepository):
    """At most one current object-storage connection per instance; ``get`` reads it and ``save`` creates or updates it in place.

    Concurrent first saves cause a unique-index conflict (``IntegrityError``) for the later insert;
    the caller rolls back and retries as an update.
    """

    async def get(self) -> ObjectStorageConnection | None:
        stmt = select(ObjectStorageConnection).where(ObjectStorageConnection.slot == CURRENT_CONNECTION_SLOT)
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
        """Write connection configuration. None secret fields retain saved values; callers must supply both secrets when creating a connection."""
        conn = await self.get()
        if conn is None:
            conn = ObjectStorageConnection(
                kind=kind,
                region=region,
                endpoint=endpoint,
                bucket=bucket,
                access_key_id=access_key_id or "",
                secret_access_key=secret_access_key or "",
                slot=CURRENT_CONNECTION_SLOT,
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
