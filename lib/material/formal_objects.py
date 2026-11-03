"""正式提交与对象存储里的正式素材对象（见 ``docs/adr/0116``）。

**正式提交入口** 是 :func:`formal_commit`：调用方在其中执行既有的正式写入编排（写正式文件、版本记录、产物清单），
入口按全局设置决定这次新写入的内容版本保存在本地还是对象存储。远端保存发生在版本管理器写版本记录之前
（:class:`ObjectContentStorage.persist`）：对象写入并经 HEAD 确认后才返回持久定位，失败时版本管理器与调用方
照旧回滚，旧版本、产物清单与正式文件不变。只有图片类资源写进对象存储，视频与音频照旧保存在本地。

**对象与归属**：对象 key 按内容摘要命名（:data:`FORMAL_PREFIX`，与中转副本的前缀分开），同一连接里同字节的内容
版本共用一个对象。``object_storage_formal_object`` 在上传前登记对象，``object_storage_formal_object_ref`` 记录
引用：进行中的提交占用（``pending``）与版本历史中的内容版本使用（``active``）。规则：

- 对象只要还有任何引用就不删除；
- 失败补偿只删除本次提交新建、结算后确认没有任何引用的对象，复用的对象一律不删；
- 删除失败保留定位与原因（``last_error``），由 :func:`sweep_formal_objects` 按退避重试；
- 结算（:meth:`ObjectContentStorage.settle`）以项目的版本历史为准判断引用，不以提交是否抛错为准；
  进程在提交中途退出时，下次启动由 :func:`recover_formal_commits` 补做结算。

**保留契约**（见 ``docs/adr/0118``）：引用记在引用方名下，引用方是项目（项目名；整体删除后改挂墓碑名），
或不是项目的消费者（带冒号的引用方键）。对象只要还有任何引用就保留，失去全部引用后由
:func:`collect_unreferenced_objects` 这一处统一转为待删除并删除；各生命周期只维护自己名下的引用：

- 项目：版本历史里的内容版本记 ``active``，项目回收站里仍可恢复的记 ``trashed``，由
  :func:`reconcile_project_references` 按项目此刻的状态重算；回收站彻底删除、清空、过期后调用
  :func:`reconcile_and_collect`，已删除项目彻底删除后调用 :func:`release_holder`。维护循环
  （:func:`retain_formal_objects`）定期对所有项目重算一遍兜底。
- 跨项目消费者：使用前 :func:`add_reference`，不再使用时 :func:`remove_reference`，整体消失时 :func:`release_holder`。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Callable, Generator, Iterable
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.repository import SystemSettingRepository
from lib.db.base import utc_now
from lib.db.models.object_storage import FormalObject, FormalObjectRef, ObjectStorageConnection
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import BadGatewayError, ConflictError
from lib.infra.async_thread import EventLoopBridge, run_noninterruptible_async, run_sync_transaction
from lib.material.storage import (
    CONTENT_VERSION_ID,
    STORAGE,
    LocalLocator,
    LocalMaterialStorage,
    ObjectLocator,
    PersistedContent,
    use_new_content_storage,
)
from lib.object_storage.client import IMAGE_CONTENT_TYPES, ObjectStorageClient, ObjectStorageError
from lib.object_storage.connection import config_from_connection
from lib.project.resource_paths import resource_extension

logger = logging.getLogger(__name__)

#: 正式素材对象的 key 前缀，与中转副本（``arcreel-relay/``）分开。
FORMAL_PREFIX = "arcreel-materials/"
#: 新内容版本保存位置的全局设置键；取值 :data:`NewContentTarget`，缺省本地。普通 UI 与 API 暂不开放修改。
MATERIAL_STORAGE_TARGET_SETTING = "material_storage_target"
NewContentTarget = Literal["local", "object_storage"]
#: 仍占用连接的对象状态：这些对象在，连接就不能换桶、换 Endpoint 或区域，也不能删除。
LIVE_OBJECT_STATES = frozenset({"uploading", "stored", "orphaned"})

_RETRY_BASE = timedelta(minutes=5)
_RETRY_CAP = timedelta(hours=6)
_IMAGE_EXTENSION = ".png"


class RemoteSaveFailedError(BadGatewayError):
    """对象存储没有确认保存，这次正式提交不发布新版本。"""

    def __init__(self, reason: str) -> None:
        super().__init__("material_remote_save_failed", reason=reason)


class ObjectStorageRequiredError(ConflictError):
    def __init__(self) -> None:
        super().__init__("material_storage_object_storage_missing")


def formal_object_key(digest: str, suffix: str) -> str:
    """正式素材对象的 key：按内容摘要命名，不带项目或资源名。"""
    return f"{FORMAL_PREFIX}sha256/{digest[:2]}/{digest}{suffix.lower()}"


def stores_remotely(resource_type: str) -> bool:
    """该资源类型的新内容版本是否随全局设置保存到对象存储（只有图片）。"""
    try:
        return resource_extension(resource_type) == _IMAGE_EXTENSION
    except ValueError:
        return False


async def new_content_target(session: AsyncSession) -> NewContentTarget:
    value = await SystemSettingRepository(session).get(MATERIAL_STORAGE_TARGET_SETTING, "local")
    return "object_storage" if value == "object_storage" else "local"


def _default_session_factory() -> async_sessionmaker[AsyncSession]:
    from lib.db import async_session_factory

    return async_session_factory


def _retry_delay(attempts: int) -> timedelta:
    return min(_RETRY_BASE * (2 ** min(max(attempts - 1, 0), 16)), _RETRY_CAP)


_KEY_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def _key_lock(key: str) -> asyncio.Lock:
    """同一对象的登记、复用与删除串行进行（单进程部署，见 ADR 0007）。"""
    loop_key = (id(asyncio.get_running_loop()), key)
    lock = _KEY_LOCKS.get(loop_key)
    if lock is None:
        lock = _KEY_LOCKS[loop_key] = asyncio.Lock()
    return lock


@dataclass(frozen=True)
class _ConnectionSnapshot:
    id: int
    endpoint: str
    bucket: str
    client: ObjectStorageClient

    @classmethod
    def of(cls, connection: ObjectStorageConnection) -> _ConnectionSnapshot:
        return cls(
            id=connection.id,
            endpoint=connection.endpoint,
            bucket=connection.bucket,
            client=ObjectStorageClient(config_from_connection(connection)),
        )


def same_connection(row: FormalObject, connection: ObjectStorageConnection | None) -> bool:
    return (
        connection is not None
        and connection.id == row.connection_id
        and connection.endpoint == row.endpoint
        and connection.bucket == row.bucket
    )


class ObjectContentStorage:
    """把一次正式提交里新写入的图片内容版本保存到对象存储。

    :meth:`persist` 在版本管理器的工作线程里调用，:meth:`store` 由不经版本管理器写记录的调用方（归档导入）直接调用；
    两者经 :class:`EventLoopBridge` 在事件循环上登记对象与引用，在当前线程里同步上传。:meth:`settle` 在提交结束后
    （无论成败）于事件循环上调用一次。
    """

    def __init__(
        self,
        *,
        project_name: str,
        connection: _ConnectionSnapshot,
        session_factory: async_sessionmaker[AsyncSession],
        bridge: EventLoopBridge,
    ) -> None:
        self.project_name = project_name
        self.connection = connection
        self.session_factory = session_factory
        self.bridge = bridge
        self.attempt_id = uuid.uuid4().hex
        self._project_paths: set[Path] = set()

    # ---- 工作线程一侧 -------------------------------------------------------------------------

    def persist(self, project_path: Path, resource_type: str, source: Path, slot: LocalLocator) -> PersistedContent:
        local = LocalMaterialStorage(project_path)
        # 版本快照位置先写一份工作文件：摘要取自它，上传的也是它，记下的摘要与远端字节一致。
        digest = local.persist(source, slot)
        if not stores_remotely(resource_type):
            return PersistedContent(digest)
        self.track(project_path)
        return PersistedContent(digest, self.store(Path(project_path) / slot.path, digest))

    def track(self, project_path: Path) -> None:
        """结算时按 ``project_path`` 的版本历史判断这次提交的对象是否被引用；路径届时不存在时跳过。"""
        self._project_paths.add(Path(project_path))

    def store(self, work_file: Path, digest: str) -> ObjectLocator:
        """把本地已有、摘要为 ``digest`` 的图片保存为正式对象，确认后返回持久定位。

        不登记项目：调用方写好引用它的版本记录后，经 :meth:`track` 指明结算读取的项目。
        归档导入用它把暂存目录里的图片保存到目标实例，再安装到项目目录。
        """
        data = work_file.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise RemoteSaveFailedError("content_changed")
        suffix = work_file.suffix
        key = formal_object_key(digest, suffix)
        needs_upload = self.bridge.run(self._begin(key, digest, len(data)))
        client = self.connection.client
        try:
            # 复用已保存的对象也先确认它仍在桶里：被人在控制台删掉时按新内容重新上传
            if not needs_upload and not _present(client, key, len(data)):
                needs_upload = True
            if needs_upload:
                client.put_bytes_sync(key, data, content_type=IMAGE_CONTENT_TYPES.get(suffix.lower()))
                if not _present(client, key, len(data)):
                    raise RemoteSaveFailedError("not_confirmed")
        except ObjectStorageError as exc:
            raise RemoteSaveFailedError(exc.kind) from exc
        if needs_upload:
            self.bridge.run(self._confirm(key))
        return ObjectLocator(self.connection.id, self.connection.bucket, key)

    # ---- 事件循环一侧 -------------------------------------------------------------------------

    async def _begin(self, key: str, digest: str, size: int) -> bool:
        """登记对象与本次提交的占用，返回是否需要上传。对象已确认保存时复用，不再上传。"""
        async with _key_lock(key), self.session_factory() as session:
            row = await _object_row(session, self.connection.id, self.connection.bucket, key)
            needs_upload = row is None or row.state != "stored"
            if row is None:
                row = FormalObject(
                    connection_id=self.connection.id,
                    endpoint=self.connection.endpoint,
                    bucket=self.connection.bucket,
                    object_key=key,
                    content_digest=digest,
                    size=size,
                    state="uploading",
                    created_attempt=self.attempt_id,
                    delete_attempts=0,
                )
                session.add(row)
                await session.flush()
            elif needs_upload:
                # 等待删除、已删除或另一次上传未确认的对象：由本次提交重新上传并负责失败补偿
                row.state = "uploading"
                row.created_attempt = self.attempt_id
                row.next_attempt_at = None
                row.last_error = None
                row.deleted_at = None
            session.add(
                FormalObjectRef(
                    object_id=row.id, project_name=self.project_name, attempt_id=self.attempt_id, state="pending"
                )
            )
            await session.commit()
            return needs_upload

    async def _confirm(self, key: str) -> None:
        async with self.session_factory() as session:
            row = await _object_row(session, self.connection.id, self.connection.bucket, key)
            if row is not None and row.created_attempt == self.attempt_id:
                row.state = "stored"
                await session.commit()

    async def finish(self) -> None:
        """提交结束时结算一次；结算失败只记日志，由下次启动补做，不掩盖提交本身的结果。"""
        try:
            await run_noninterruptible_async(self.settle())
        except Exception:
            logger.warning("正式提交结算失败，下次启动时补做 attempt=%s", self.attempt_id, exc_info=True)

    async def settle(self) -> None:
        """按版本历史结算本次提交：被内容版本使用的转为 ``active`` 引用，其余占用撤下，再补偿删除。"""
        referenced: dict[tuple[int, str, str], set[str]] = {}
        for project_path in self._project_paths:
            if not await asyncio.to_thread(project_path.is_dir):
                continue
            for locator, versions in (await asyncio.to_thread(_referenced_objects, project_path)).items():
                referenced.setdefault(locator, set()).update(versions)
        await _settle_attempt(self.session_factory, self.attempt_id, referenced, connection=self.connection)


def _present(client: ObjectStorageClient, key: str, size: int) -> bool:
    try:
        return client.head_sync(key).size == size
    except ObjectStorageError as exc:
        if exc.kind == "not_found":
            return False
        raise


def _referenced_objects(project_path: Path) -> dict[tuple[int, str, str], set[str]]:
    """项目版本历史里远端内容版本使用的对象：(连接 ID, 桶, key) → 内容版本身份集合。"""
    from lib.artifacts.version_manager import VersionManager

    result: dict[tuple[int, str, str], set[str]] = {}
    with VersionManager(project_path).locked_history() as data:
        for _resource_type, _resource_id, record in iter_version_records(data):
            locator = ObjectLocator.from_record(record.get(STORAGE))
            content_version_id = record.get(CONTENT_VERSION_ID)
            if locator is not None and isinstance(content_version_id, str):
                result.setdefault((locator.connection_id, locator.bucket, locator.key), set()).add(content_version_id)
    return result


def iter_version_records(data: object) -> Iterable[tuple[str, str, dict[str, Any]]]:
    """遍历 versions.json 内容里的每条版本记录。"""
    if not isinstance(data, dict):
        return
    for resource_type, bucket in data.items():
        if not isinstance(bucket, dict):
            continue
        for resource_id, history in bucket.items():
            records = history.get("versions") if isinstance(history, dict) else None
            for record in records if isinstance(records, list) else ():
                if isinstance(record, dict):
                    yield resource_type, resource_id, record


async def _object_row(session: AsyncSession, connection_id: int, bucket: str, key: str) -> FormalObject | None:
    stmt = select(FormalObject).where(
        FormalObject.connection_id == connection_id, FormalObject.bucket == bucket, FormalObject.object_key == key
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _ref_count(session: AsyncSession, object_id: int) -> int:
    stmt = select(func.count()).select_from(FormalObjectRef).where(FormalObjectRef.object_id == object_id)
    return int((await session.execute(stmt)).scalar_one())


async def _settle_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: str,
    referenced: dict[tuple[int, str, str], set[str]],
    *,
    connection: _ConnectionSnapshot | None,
) -> None:
    async with session_factory() as session:
        pending = list(
            (await session.execute(select(FormalObjectRef).where(FormalObjectRef.attempt_id == attempt_id))).scalars()
        )
        object_ids = {ref.object_id for ref in pending}
        objects = {
            row.id: row
            for row in (
                await session.execute(
                    select(FormalObject).where(
                        (FormalObject.id.in_(object_ids)) | (FormalObject.created_attempt == attempt_id)
                    )
                )
            ).scalars()
        }
        for ref in pending:
            row = objects.get(ref.object_id)
            versions = referenced.get((row.connection_id, row.bucket, row.object_key), set()) if row else set()
            for content_version_id in versions:
                await _ensure_active_ref(session, ref.object_id, ref.project_name, content_version_id)
            await session.delete(ref)
        await session.commit()
        created = [row for row in objects.values() if row.created_attempt == attempt_id]
    for row in created:
        await _orphan_if_unreferenced(session_factory, row.id, attempt_id, connection=connection)


async def _ensure_active_ref(session: AsyncSession, object_id: int, project_name: str, content_version_id: str) -> None:
    stmt = select(FormalObjectRef.id).where(
        FormalObjectRef.object_id == object_id,
        FormalObjectRef.project_name == project_name,
        FormalObjectRef.content_version_id == content_version_id,
        FormalObjectRef.state == "active",
    )
    if (await session.execute(stmt)).first() is None:
        session.add(
            FormalObjectRef(
                object_id=object_id, project_name=project_name, content_version_id=content_version_id, state="active"
            )
        )
        await session.flush()


async def _orphan_if_unreferenced(
    session_factory: async_sessionmaker[AsyncSession],
    object_id: int,
    attempt_id: str,
    *,
    connection: _ConnectionSnapshot | None,
) -> None:
    """本次提交新建的对象没有任何引用时转入待删除并尝试删除；仍被引用（含其他提交占用）时保留。"""
    async with session_factory() as session:
        row = await session.get(FormalObject, object_id)
        if row is None:
            return
        key = row.object_key
    async with _key_lock(key), session_factory() as session:
        row = await session.get(FormalObject, object_id)
        if row is None or row.created_attempt != attempt_id or row.state not in {"uploading", "stored"}:
            return
        if await _ref_count(session, object_id):
            return
        row.state = "orphaned"
        row.next_attempt_at = utc_now()
        await session.commit()
        current = await ObjectStorageConnectionRepository(session).get()
        await _delete_orphan(session, row, current, connection.client if connection else None)
        await session.commit()


async def _delete_orphan(
    session: AsyncSession,
    row: FormalObject,
    current: ObjectStorageConnection | None,
    client: ObjectStorageClient | None,
    *,
    now: datetime | None = None,
) -> bool:
    """删除一个待删除对象；调用方持有该对象的 key 锁并负责提交。返回是否已删除。"""
    now = now or utc_now()
    row.delete_attempts += 1
    if not same_connection(row, current):
        row.last_error = "connection_changed"
        row.next_attempt_at = now + _RETRY_CAP
        return False
    if client is None:
        assert current is not None
        client = ObjectStorageClient(config_from_connection(current))
    try:
        await client.delete(row.object_key)
    except ObjectStorageError as exc:
        if exc.kind != "not_found":
            row.last_error = f"{exc.kind}:{exc.code}"[:128]
            row.next_attempt_at = now + _retry_delay(row.delete_attempts)
            logger.info("正式素材对象删除失败，稍后重试 object=%s reason=%s", row.id, row.last_error)
            return False
    row.state = "deleted"
    row.deleted_at = now
    row.next_attempt_at = None
    row.last_error = None
    return True


async def sweep_formal_objects(
    session_factory: async_sessionmaker[AsyncSession] | None = None, *, now: datetime | None = None
) -> int:
    """重试删除到期的待删除对象，返回本轮删除的数量。删除前再次确认没有任何引用，有引用时恢复为已保存。"""
    session_factory = session_factory or _default_session_factory()
    now = now or utc_now()
    async with session_factory() as session:
        stmt = select(FormalObject.id, FormalObject.object_key, FormalObject.next_attempt_at).where(
            FormalObject.state == "orphaned"
        )
        candidates = [
            (object_id, key)
            for object_id, key, next_at in (await session.execute(stmt)).all()
            if next_at is None or _aware(next_at) <= now
        ]
    deleted = 0
    for object_id, key in candidates:
        async with _key_lock(key), session_factory() as session:
            row = await session.get(FormalObject, object_id)
            if row is None or row.state != "orphaned":
                continue
            if await _ref_count(session, object_id):
                row.state = "stored"
                row.next_attempt_at = None
            else:
                current = await ObjectStorageConnectionRepository(session).get()
                deleted += await _delete_orphan(session, row, current, None, now=now)
            await session.commit()
    return deleted


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def recover_formal_commits(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    project_path_of: Callable[[str], Path] | None = None,
) -> int:
    """启动时结算上一个进程未结算的正式提交，返回结算的提交数。只在应用启动时调用（单进程部署）。"""
    session_factory = session_factory or _default_session_factory()
    project_path_of = project_path_of or _default_project_path
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(FormalObjectRef.attempt_id, FormalObjectRef.project_name)
                .where(FormalObjectRef.state == "pending", FormalObjectRef.attempt_id.is_not(None))
                .distinct()
            )
        ).all()
        stale_uploads = (
            await session.execute(
                select(FormalObject.created_attempt).where(
                    FormalObject.state == "uploading", FormalObject.created_attempt.is_not(None)
                )
            )
        ).scalars()
        attempts: dict[str, str | None] = {str(attempt_id): project for attempt_id, project in rows}
        for attempt_id in stale_uploads:
            if attempt_id is not None:
                attempts.setdefault(attempt_id, None)
    for attempt_id, project_name in attempts.items():
        referenced = (
            await asyncio.to_thread(_referenced_in_project, project_path_of, project_name) if project_name else {}
        )
        await _settle_attempt(session_factory, attempt_id, referenced, connection=None)
    return len(attempts)


def _referenced_in_project(
    project_path_of: Callable[[str], Path], project_name: str
) -> dict[tuple[int, str, str], set[str]]:
    try:
        project_path = project_path_of(project_name)
    except (FileNotFoundError, ValueError):
        return {}
    return _referenced_objects(project_path) if project_path.is_dir() else {}


def _default_project_path(project_name: str) -> Path:
    from lib.project.project_manager import get_project_manager

    return get_project_manager().get_project_path(project_name)


# ---- 保留契约：引用方与统一回收（见 ``docs/adr/0118``） ------------------------------------------

#: 引用的状态。``pending`` 进行中的正式提交占用；``active`` 引用方正在使用（项目版本历史里的内容版本，
#: 或跨项目消费者登记的使用）；``trashed`` 项目回收站里的删除记录仍可恢复出这个内容版本。
REF_PENDING = "pending"
REF_ACTIVE = "active"
REF_TRASHED = "trashed"
_HELD_STATES = frozenset({REF_ACTIVE, REF_TRASHED})

_VERSIONS_FILE = "versions/versions.json"
#: 引用方持有的使用：对象定位 (连接 ID, 桶, key) → {(内容版本身份, 引用状态)}。
type _Uses = dict[tuple[int, str, str], set[tuple[str, str]]]


def _add_uses(uses: _Uses, data: object, state: str) -> None:
    for _resource_type, _resource_id, record in iter_version_records(data):
        _add_use(uses, record, state)


def _add_use(uses: _Uses, record: object, state: str) -> None:
    if not isinstance(record, dict):
        return
    locator = ObjectLocator.from_record(record.get(STORAGE))
    content_version_id = record.get(CONTENT_VERSION_ID)
    if locator is not None and isinstance(content_version_id, str):
        uses.setdefault((locator.connection_id, locator.bucket, locator.key), set()).add((content_version_id, state))


def _trash_uses(project_path: Path, uses: _Uses) -> None:
    """项目回收站里仍可恢复的远端内容版本：删除的历史版本，以及结构删除记录快照里的版本历史。

    结构快照收的是删除前整份版本历史，其中也有删除后仍留在项目里的版本；多算只会让对象多保留到记录清除，
    不会少算。快照读不出时抛错，调用方放弃这次重算，不据此撤下引用。
    """
    from lib.project import project_trash

    for record in project_trash.list_records(project_path):
        if record.download_only:
            continue
        if record.object.get("kind") == "version":
            _add_use(uses, record.object.get("metadata"), REF_TRASHED)
        if record.has_snapshot:
            snapshot = (
                project_trash.trash_dir(project_path) / record.id / project_trash.SNAPSHOT_DIRNAME / _VERSIONS_FILE
            )
            if snapshot.is_file():
                _add_uses(uses, json.loads(snapshot.read_text(encoding="utf-8")), REF_TRASHED)


async def _replace_holder_uses(
    session_factory: async_sessionmaker[AsyncSession], holder: str, uses: _Uses, states: frozenset[str]
) -> set[int]:
    """把引用方 ``holder`` 在 ``states`` 里的引用改成恰好是 ``uses``，返回被撤下引用的对象 ID。不删除对象。

    ``uses`` 里没有登记的对象（例如另一个实例保存的）不建引用。
    """
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(FormalObject.id, FormalObject.connection_id, FormalObject.bucket, FormalObject.object_key)
            )
        ).all()
        objects = {(cid, bucket, key): object_id for object_id, cid, bucket, key in rows}
        wanted = {
            (objects[locator], content_version_id, state)
            for locator, items in uses.items()
            if locator in objects
            for content_version_id, state in items
            if state in states
        }
        existing = list(
            (
                await session.execute(
                    select(FormalObjectRef).where(
                        FormalObjectRef.project_name == holder, FormalObjectRef.state.in_(states)
                    )
                )
            ).scalars()
        )
        have: set[tuple[int, str | None, str]] = set()
        released: set[int] = set()
        for ref in existing:
            identity = (ref.object_id, ref.content_version_id, ref.state)
            if identity in wanted and identity not in have:
                have.add(identity)
            else:
                released.add(ref.object_id)
                await session.delete(ref)
        for object_id, content_version_id, state in wanted - have:
            session.add(
                FormalObjectRef(
                    object_id=object_id, project_name=holder, content_version_id=content_version_id, state=state
                )
            )
        await session.commit()
        return released


async def reconcile_project_references(
    project_name: str,
    project_path: Path,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> set[int]:
    """按项目此刻的版本历史与项目回收站重算它持有的引用，返回被撤下引用的对象 ID。不删除对象。

    版本历史里的远端内容版本记为 ``active``，回收站里仍可恢复的记为 ``trashed``。读取与改写引用都在项目锁与
    版本锁内进行，与删除、恢复、正式提交互不交错：不会在内容版本从版本历史搬进回收站的途中撤下它的引用。
    项目目录不在时抛错，不撤下任何引用。失去全部引用的对象经 :func:`collect_unreferenced_objects` 回收，
    或直接调用 :func:`reconcile_and_collect`。
    """
    from lib.artifacts.formal_write import project_metadata_lock
    from lib.artifacts.version_manager import VersionManager

    session_factory = session_factory or _default_session_factory()
    bridge = EventLoopBridge.capture()
    project_path = Path(project_path)

    def _locked() -> set[int]:
        if not (project_path / "project.json").is_file():
            raise FileNotFoundError(project_path)
        with project_metadata_lock(project_path), VersionManager(project_path).locked_history() as data:
            uses: _Uses = {}
            _add_uses(uses, data, REF_ACTIVE)
            _trash_uses(project_path, uses)
            return bridge.run(_replace_holder_uses(session_factory, project_name, uses, _HELD_STATES))

    return await run_sync_transaction(_locked)


async def collect_unreferenced_objects(
    object_ids: Iterable[int], session_factory: async_sessionmaker[AsyncSession] | None = None
) -> int:
    """统一回收规则：``object_ids`` 中已没有任何引用的已保存对象转为待删除并立即尝试删除，返回删除的数量。

    仍有任何引用（含进行中提交的占用）的对象保留；删除失败记下原因，由 :func:`sweep_formal_objects` 退避重试；
    连接已换桶时记 ``connection_changed``、不删除。上传未确认的对象归它的正式提交补偿，这里不碰。
    """
    session_factory = session_factory or _default_session_factory()
    deleted = 0
    for object_id in sorted(set(object_ids)):
        async with session_factory() as session:
            row = await session.get(FormalObject, object_id)
            if row is None:
                continue
            key = row.object_key
        async with _key_lock(key), session_factory() as session:
            row = await session.get(FormalObject, object_id)
            if row is None or row.state != "stored" or await _ref_count(session, object_id):
                continue
            row.state = "orphaned"
            row.next_attempt_at = utc_now()
            await session.commit()
            current = await ObjectStorageConnectionRepository(session).get()
            deleted += await _delete_orphan(session, row, current, None)
            await session.commit()
    return deleted


async def reconcile_and_collect(
    project_name: str,
    project_path: Path,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """重算项目持有的引用（:func:`reconcile_project_references`），再回收因此失去全部引用的对象；返回删除的数量。

    项目回收站彻底删除、清空或过期清理之后调用。
    """
    released = await reconcile_project_references(project_name, project_path, session_factory)
    return await collect_unreferenced_objects(released, session_factory)


async def add_reference(
    locator: ObjectLocator,
    project_name: str,
    content_version_id: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> bool:
    """为引用方 ``project_name`` 的内容版本登记它使用 ``locator`` 指向的对象，返回对象是否已登记且未删除。

    供跨项目复用同一对象的操作在写入目标版本记录前调用：对象有了引用才不会被补偿删除或回收。
    引用方是项目时传项目名；不是项目的消费者（如全局资产库）用带冒号的引用方键（如 ``asset-library:<id>``），
    它不会与项目名或已删除项目的墓碑名重名，项目的重算也不会碰它。
    """
    session_factory = session_factory or _default_session_factory()
    async with _key_lock(locator.key), session_factory() as session:
        row = await _object_row(session, locator.connection_id, locator.bucket, locator.key)
        if row is None or row.state not in {"stored", "orphaned"}:
            return False
        row.state = "stored"
        row.next_attempt_at = None
        await _ensure_active_ref(session, row.id, project_name, content_version_id)
        await session.commit()
        return True


async def remove_reference(
    locator: ObjectLocator,
    holder: str,
    content_version_id: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """撤下引用方 ``holder`` 的内容版本对 ``locator`` 的 ``active`` 引用，并回收因此失去全部引用的对象；返回删除的数量。"""
    session_factory = session_factory or _default_session_factory()
    async with session_factory() as session:
        row = await _object_row(session, locator.connection_id, locator.bucket, locator.key)
        if row is None:
            return 0
        refs = (
            await session.execute(
                select(FormalObjectRef).where(
                    FormalObjectRef.object_id == row.id,
                    FormalObjectRef.project_name == holder,
                    FormalObjectRef.content_version_id == content_version_id,
                    FormalObjectRef.state == REF_ACTIVE,
                )
            )
        ).scalars()
        for ref in refs:
            await session.delete(ref)
        await session.commit()
        object_id = row.id
    return await collect_unreferenced_objects([object_id], session_factory)


async def release_holder(holder: str, session_factory: async_sessionmaker[AsyncSession] | None = None) -> int:
    """引用方 ``holder`` 不复存在（如已删除项目被彻底删除）：撤下它的全部引用并回收失去全部引用的对象；返回删除的数量。

    进行中提交的占用不撤，由该提交结算。
    """
    session_factory = session_factory or _default_session_factory()
    released = await _replace_holder_uses(session_factory, holder, {}, _HELD_STATES)
    return await collect_unreferenced_objects(released, session_factory)


#: 已删除项目超过保留期这么久、目录也已不在时，维护循环撤下仍挂在墓碑名下的引用（彻底删除后的显式撤下失败时兜底）。
_TOMBSTONE_GRACE = timedelta(days=1)


async def retain_formal_objects(
    projects_dir: Path,
    deleted_projects_dir: Path | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """维护循环的保留校正：逐个项目重算引用并回收，再撤下早已彻底删除的已删除项目的引用；返回删除的数量。

    单个项目失败只记日志，不影响其余项目。
    """
    from lib.db.repositories.project_records import parse_deleted_project_name
    from lib.infra.data_root_layout import list_project_dirs
    from lib.project.project_trash import RETENTION

    session_factory = session_factory or _default_session_factory()
    deleted = 0
    for project_dir in await asyncio.to_thread(list_project_dirs, Path(projects_dir)):
        try:
            deleted += await reconcile_and_collect(project_dir.name, project_dir, session_factory)
        except Exception:
            logger.warning("重算项目的正式对象引用失败 project=%s", project_dir.name, exc_info=True)
    if deleted_projects_dir is None:
        return deleted
    cutoff = (now or utc_now()) - RETENTION - _TOMBSTONE_GRACE
    async with session_factory() as session:
        holders = (
            await session.execute(
                select(FormalObjectRef.project_name).where(FormalObjectRef.state.in_(_HELD_STATES)).distinct()
            )
        ).scalars()
        expired = [
            holder
            for holder in holders
            if (parsed := parse_deleted_project_name(holder)) is not None and parsed[1] <= cutoff
        ]
    for holder in expired:
        if not await asyncio.to_thread((Path(deleted_projects_dir) / holder).exists):
            deleted += await release_holder(holder, session_factory)
    return deleted


async def live_object_count(session: AsyncSession, connection_id: int) -> int:
    """仍占用这条连接的正式素材对象数量（含上传未确认与等待删除的）。"""
    stmt = (
        select(func.count())
        .select_from(FormalObject)
        .where(FormalObject.connection_id == connection_id, FormalObject.state.in_(LIVE_OBJECT_STATES))
    )
    return int((await session.execute(stmt)).scalar_one())


async def stored_object_keys(session: AsyncSession, locators: Iterable[ObjectLocator]) -> set[ObjectLocator]:
    """``locators`` 中远端已确认保存的那些；本地工作文件只在这种情况下可以清理。"""
    wanted = set(locators)
    if not wanted:
        return set()
    stmt = select(FormalObject.connection_id, FormalObject.bucket, FormalObject.object_key).where(
        FormalObject.state == "stored", FormalObject.object_key.in_({loc.key for loc in wanted})
    )
    return {ObjectLocator(cid, bucket, key) for cid, bucket, key in (await session.execute(stmt)).all()} & wanted


async def formal_object_sizes(
    locators: Iterable[ObjectLocator], *, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> dict[ObjectLocator, int]:
    """``locators`` 中已登记对象的字节数（不论状态，不访问对象存储）；没有登记的定位不出现。"""
    wanted = set(locators)
    if not wanted:
        return {}
    stmt = select(FormalObject.connection_id, FormalObject.bucket, FormalObject.object_key, FormalObject.size).where(
        FormalObject.object_key.in_({loc.key for loc in wanted})
    )
    async with (session_factory or _default_session_factory())() as session:
        rows = (await session.execute(stmt)).all()
    sizes = {ObjectLocator(cid, bucket, key): size for cid, bucket, key, size in rows}
    return {locator: sizes[locator] for locator in wanted if locator in sizes}


async def _open_formal_commit(
    project_name: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None,
    target: NewContentTarget | None,
) -> ObjectContentStorage | None:
    session_factory = session_factory or _default_session_factory()
    async with session_factory() as session:
        resolved = target or await new_content_target(session)
        connection = await ObjectStorageConnectionRepository(session).get() if resolved == "object_storage" else None
    if resolved == "local":
        return None
    if connection is None:
        raise ObjectStorageRequiredError()
    return ObjectContentStorage(
        project_name=project_name,
        connection=_ConnectionSnapshot.of(connection),
        session_factory=session_factory,
        bridge=EventLoopBridge.capture(),
    )


@asynccontextmanager
async def formal_commit(
    project_name: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    target: NewContentTarget | None = None,
) -> AsyncGenerator[ObjectContentStorage | None]:
    """正式提交入口：这段上下文里写入的新图片内容版本按 ``target``（缺省取全局设置）保存。

    本地时不改变任何行为，产出 ``None``。对象存储时进入前确认连接存在，产出这次提交的
    :class:`ObjectContentStorage`；退出时（无论成败、含取消）结算一次，结算失败只记日志，不掩盖提交本身的结果。
    在上下文里调用的同步正式写入须经 ``asyncio.to_thread`` 一类会复制上下文变量的方式进入工作线程。

    Raises:
        ObjectStorageRequiredError: 目标是对象存储而实例没有配置连接。
    """
    storage = await _open_formal_commit(project_name, session_factory=session_factory, target=target)
    if storage is None:
        yield None
        return
    try:
        with use_new_content_storage(storage):
            yield storage
    finally:
        await storage.finish()


def formal_commit_on(
    bridge: EventLoopBridge, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> Callable[[str], AbstractContextManager[ObjectContentStorage | None]]:
    """给工作线程里的同步流程用的正式提交：进出 ``with`` 块时投回 ``bridge`` 的事件循环，块本身留在调用线程。

    与 :func:`formal_commit` 同样按全局设置选择保存位置、退出时结算，但不设置新内容版本的上下文变量：
    块内经产出的 :meth:`ObjectContentStorage.store` 显式保存，再用 :meth:`ObjectContentStorage.track`
    指明结算读取的项目。归档导入在确定目标项目名之后使用。
    """

    @contextmanager
    def _commit(project_name: str) -> Generator[ObjectContentStorage | None]:
        storage = bridge.run(_open_formal_commit(project_name, session_factory=session_factory, target=None))
        if storage is None:
            yield None
            return
        try:
            yield storage
        finally:
            bridge.run(storage.finish())

    return _commit


__all__ = [
    "FORMAL_PREFIX",
    "LIVE_OBJECT_STATES",
    "MATERIAL_STORAGE_TARGET_SETTING",
    "REF_ACTIVE",
    "REF_PENDING",
    "REF_TRASHED",
    "NewContentTarget",
    "ObjectContentStorage",
    "ObjectStorageRequiredError",
    "RemoteSaveFailedError",
    "add_reference",
    "collect_unreferenced_objects",
    "formal_commit",
    "formal_commit_on",
    "formal_object_key",
    "formal_object_sizes",
    "iter_version_records",
    "live_object_count",
    "new_content_target",
    "reconcile_and_collect",
    "reconcile_project_references",
    "recover_formal_commits",
    "release_holder",
    "remove_reference",
    "retain_formal_objects",
    "same_connection",
    "stored_object_keys",
    "stores_remotely",
    "sweep_formal_objects",
]
