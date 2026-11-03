"""供应商取图用的中转副本：写进用户对象存储、签发有时效的下载地址，并在保留期结束后清理。

中转副本与正式素材对象分开管理：对象 key 都在 :data:`RELAY_PREFIX` 下，清理只按
``object_storage_relay_object`` 里的记录进行，不扫描、不前缀删除，所以不会作用于正式对象。

保留期按入库任务判断（见 ``docs/adr/0111``）。供应商不承诺入库时长，CreateAsset 返回素材 ID 时取图可能还没
发生，因此：

- 入库任务有了定论（可用或失败）后，副本至少保留 :data:`RELAY_MIN_RETENTION` 再删；
- 任务一直没有定论（处理中、结果不确定、已解除但仍在处理）时，保留到下载地址过期再加
  :data:`RELAY_EXPIRED_GRACE`，此后供应商已无法再用这个地址取图；
- 从未签发下载地址的副本（写入失败或进程在写入中途退出）在 :data:`RELAY_UNSIGNED_GRACE` 后清理。

删除失败按指数退避重试，原因记在记录上；连接已被改成另一个桶或删除时不删除，等连接恢复。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.base import utc_now
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.object_storage import ObjectStorageConnection, RelayObject
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.object_storage.client import ObjectStorageClient, ObjectStorageError
from lib.object_storage.connection import config_from_connection

logger = logging.getLogger(__name__)

#: 中转副本的对象 key 前缀，与正式素材对象分开。
RELAY_PREFIX = "arcreel-relay/"
#: 签发给供应商的下载地址有效期。供应商文档没有给出取图时限，取一天覆盖排队；TOS 预签名上限为 7 天。
RELAY_URL_TTL = timedelta(hours=24)
#: 入库任务有定论后副本的最短保留时间（自副本写入起算）。
RELAY_MIN_RETENTION = timedelta(hours=1)
#: 下载地址过期之后再保留的时间。
RELAY_EXPIRED_GRACE = timedelta(hours=1)
#: 没有签发下载地址的副本的保留时间。
RELAY_UNSIGNED_GRACE = timedelta(minutes=30)

_RETRY_BASE = timedelta(minutes=5)
_RETRY_CAP = timedelta(hours=6)
_SETTLED_STATES = frozenset({"active", "failed"})


def relay_object_key(suffix: str, *, now: datetime | None = None) -> str:
    """新中转副本的对象 key：按日期分目录，文件名随机，不带项目或资源名。"""
    day = (now or utc_now()).strftime("%Y%m%d")
    return f"{RELAY_PREFIX}{day}/{uuid.uuid4().hex}{suffix.lower()}"


def _retry_delay(attempts: int) -> timedelta:
    # 指数先封顶再乘：重试次数累积多了，timedelta 乘法会溢出，min 来不及截断
    return min(_RETRY_BASE * (2 ** min(max(attempts - 1, 0), 16)), _RETRY_CAP)


def _aware(value: datetime) -> datetime:
    """SQLite 读回的时间不带时区，按 UTC 补上。"""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def relay_deletable_at(relay: RelayObject, link: ProviderAssetLink | None) -> datetime:
    """中转副本最早可以删除的时间。"""
    created = _aware(relay.created_at)
    if relay.url_expires_at is None:
        return created + RELAY_UNSIGNED_GRACE
    return signed_content_retained_until(created, _aware(relay.url_expires_at), link)


def signed_content_retained_until(
    signed_at: datetime, url_expires_at: datetime, link: ProviderAssetLink | None
) -> datetime:
    """签发给入库任务 ``link`` 的内容（中转副本，或直接签名的正式对象）至少保留到的时间。

    任务有了定论后自 ``signed_at`` 起至少保留 :data:`RELAY_MIN_RETENTION`；没有定论或任务记录已不在时保留到
    下载地址过期再加 :data:`RELAY_EXPIRED_GRACE`。
    """
    expired = url_expires_at + RELAY_EXPIRED_GRACE
    if link is None:
        return expired
    if link.state in _SETTLED_STATES:
        settled = _aware(link.settled_at) if link.settled_at else signed_at
        return min(max(signed_at + RELAY_MIN_RETENTION, settled), expired)
    return expired


async def record_relay_object(
    session_factory: async_sessionmaker[AsyncSession],
    connection: ObjectStorageConnection,
    object_key: str,
    *,
    link_id: int | None,
) -> int:
    """在写入对象之前登记中转副本，写入中途失败也能找回定位。"""
    async with session_factory() as session:
        relay = RelayObject(
            connection_id=connection.id,
            endpoint=connection.endpoint,
            bucket=connection.bucket,
            object_key=object_key,
            link_id=link_id,
            state="retained",
            delete_attempts=0,
        )
        session.add(relay)
        await session.commit()
        return relay.id


async def mark_relay_signed(
    session_factory: async_sessionmaker[AsyncSession], relay_id: int, expires_at: datetime
) -> None:
    async with session_factory() as session:
        relay = await session.get(RelayObject, relay_id)
        if relay is not None:
            relay.url_expires_at = expires_at
            await session.commit()


def _same_connection(relay: RelayObject, connection: ObjectStorageConnection | None) -> bool:
    return (
        connection is not None
        and connection.id == relay.connection_id
        and connection.endpoint == relay.endpoint
        and connection.bucket == relay.bucket
    )


async def count_retained_relay_objects(session: AsyncSession) -> int:
    """还没有清理的中转副本数量。"""
    rows = await session.execute(select(RelayObject.id).where(RelayObject.state == "retained"))
    return len(rows.all())


async def sweep_relay_objects(session_factory: async_sessionmaker[AsyncSession], *, now: datetime | None = None) -> int:
    """删除保留期已过的中转副本，返回本轮删除的数量。失败的记下原因，按退避时间重试。"""
    now = now or utc_now()
    async with session_factory() as session:
        stmt = select(RelayObject).where(RelayObject.state == "retained").order_by(RelayObject.id)
        candidates = list((await session.execute(stmt)).scalars())
        connection = await ObjectStorageConnectionRepository(session).get()
        due: list[RelayObject] = []
        for relay in candidates:
            if relay.next_attempt_at is not None and _aware(relay.next_attempt_at) > now:
                continue
            link = await session.get(ProviderAssetLink, relay.link_id) if relay.link_id is not None else None
            if relay_deletable_at(relay, link) <= now:
                due.append(relay)
        if not due:
            return 0
        client = ObjectStorageClient(config_from_connection(connection)) if connection is not None else None
        deleted = 0
        for relay in due:
            relay.delete_attempts += 1
            if client is None or not _same_connection(relay, connection):
                relay.last_error = "connection_changed"
                relay.next_attempt_at = now + _RETRY_CAP
                continue
            try:
                await client.delete(relay.object_key)
            except ObjectStorageError as exc:
                if exc.kind == "not_found":
                    relay.state = "deleted"
                    relay.deleted_at = now
                    relay.last_error = None
                    deleted += 1
                    continue
                relay.last_error = f"{exc.kind}:{exc.code}"[:128]
                relay.next_attempt_at = now + _retry_delay(relay.delete_attempts)
                logger.info("中转副本删除失败，稍后重试 relay=%s reason=%s", relay.id, relay.last_error)
                continue
            relay.state = "deleted"
            relay.deleted_at = now
            relay.last_error = None
            deleted += 1
        await session.commit()
        return deleted


__all__ = [
    "RELAY_EXPIRED_GRACE",
    "RELAY_MIN_RETENTION",
    "RELAY_PREFIX",
    "RELAY_UNSIGNED_GRACE",
    "RELAY_URL_TTL",
    "count_retained_relay_objects",
    "mark_relay_signed",
    "record_relay_object",
    "relay_deletable_at",
    "relay_object_key",
    "signed_content_retained_until",
    "sweep_relay_objects",
]
