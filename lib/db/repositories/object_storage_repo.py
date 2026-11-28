"""对象存储连接 repository。"""

from __future__ import annotations

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from lib.db.models.object_storage import CURRENT_CONNECTION_SLOT, ObjectStorageConnection
from lib.db.repositories.base import BaseRepository
from lib.object_storage.client import (
    DEFAULT_ADDRESSING_STYLE,
    OBJECT_STORAGE_KIND_S3,
    AddressingStyle,
    ObjectStorageKind,
)

_relocation_epoch = 0


def relocation_state_epoch() -> int:
    return _relocation_epoch


@event.listens_for(Session, "after_commit")
def _advance_relocation_epoch(session: Session) -> None:
    global _relocation_epoch
    if session.info.pop("relocation_state_changed", False):
        _relocation_epoch += 1


@event.listens_for(Session, "after_soft_rollback")
def _discard_relocation_epoch_flag(session: Session, _previous_transaction: object) -> None:
    session.info.pop("relocation_state_changed", None)


class ObjectStorageConnectionRepository(BaseRepository):
    """实例至多一条当前对象存储连接；``get`` 取这一条，``save`` 按目标变化原位更新或分配新连接 ID。

    并发首次保存时，后提交的一方在插入时触发唯一索引冲突（``IntegrityError``），由调用方回滚后按更新重做。
    """

    async def get(self) -> ObjectStorageConnection | None:
        stmt = select(ObjectStorageConnection).where(ObjectStorageConnection.slot == CURRENT_CONNECTION_SLOT)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(
        self,
        *,
        kind: ObjectStorageKind = OBJECT_STORAGE_KIND_S3,
        region: str,
        endpoint: str,
        bucket: str,
        addressing_style: AddressingStyle = DEFAULT_ADDRESSING_STYLE,
        access_key_id: str | None,
        secret_access_key: str | None,
        needs_endpoint_review: bool = False,
        clear_relocation: bool = False,
    ) -> ObjectStorageConnection:
        """写入连接配置。

        同目标（Endpoint 与桶未变）时原位更新并保留原连接 ID，密钥留空则沿用已保存值；
        更换 Endpoint 或桶时删除旧槽并分配新的连接 ID，此时两个密钥字段都必须显式给出。
        """
        conn = await self.get()
        if conn is None:
            if not access_key_id or not secret_access_key:
                raise ValueError("access_key_id and secret_access_key are required when creating a connection")
            conn = ObjectStorageConnection(
                kind=kind,
                region=region,
                endpoint=endpoint,
                bucket=bucket,
                addressing_style=addressing_style,
                needs_endpoint_review=needs_endpoint_review,
                relocating=False,
                relocate_delete_remote=None,
                relocate_mode=None,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                slot=CURRENT_CONNECTION_SLOT,
            )
            self.session.add(conn)
        elif (conn.endpoint, conn.bucket) != (endpoint, bucket):
            if not access_key_id or not secret_access_key:
                raise ValueError("access_key_id and secret_access_key are required when changing target")
            await self.session.delete(conn)
            await self.session.flush()
            conn = ObjectStorageConnection(
                kind=kind,
                region=region,
                endpoint=endpoint,
                bucket=bucket,
                addressing_style=addressing_style,
                needs_endpoint_review=needs_endpoint_review,
                relocating=False,
                relocate_delete_remote=None,
                relocate_mode=None,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                slot=CURRENT_CONNECTION_SLOT,
            )
            self.session.add(conn)
        else:
            conn.kind = kind
            conn.region = region
            conn.addressing_style = addressing_style
            conn.needs_endpoint_review = needs_endpoint_review
            if clear_relocation:
                conn.relocating = False
                conn.relocate_delete_remote = None
                conn.relocate_mode = None
            if access_key_id:
                conn.access_key_id = access_key_id
            if secret_access_key:
                conn.secret_access_key = secret_access_key
        self.session.info["relocation_state_changed"] = True
        await self.session.flush()
        return conn

    async def write_relocation_state(
        self, conn: ObjectStorageConnection, relocating: bool, delete_remote: bool | None, mode: str | None
    ) -> None:
        conn.relocating = relocating
        conn.relocate_delete_remote = delete_remote
        conn.relocate_mode = mode
        self.session.info["relocation_state_changed"] = True
        await self.session.flush()

    async def set_relocating(
        self, connection_id: int, *, delete_remote: bool, mode: str
    ) -> ObjectStorageConnection | None:
        conn = await self.get()
        if conn is None or conn.id != connection_id:
            return None
        await self.write_relocation_state(conn, True, delete_remote, mode)
        return conn

    async def clear_relocating(self, connection_id: int | None = None) -> ObjectStorageConnection | None:
        conn = await self.get()
        if conn is None or (connection_id is not None and conn.id != connection_id):
            return None
        await self.write_relocation_state(conn, False, None, None)
        return conn

    async def clear_endpoint_review(self, connection_id: int) -> None:
        conn = await self.get()
        if conn is not None and conn.id == connection_id and conn.needs_endpoint_review:
            conn.needs_endpoint_review = False
            await self.session.flush()

    async def delete(self) -> bool:
        conn = await self.get()
        if conn is None:
            return False
        await self.session.delete(conn)
        await self.session.flush()
        self.session.info["relocation_state_changed"] = True
        return True
