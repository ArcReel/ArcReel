"""Obtain object-storage clients from saved connection configuration."""

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
    """Current instance object-storage client, or None with no configured connection; callers treat that as disabled storage."""
    conn = await ObjectStorageConnectionRepository(session).get()
    return ObjectStorageClient(config_from_connection(conn)) if conn else None
