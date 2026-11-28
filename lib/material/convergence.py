"""对象存储切换与删除的「收敛回本地」与旧桶失联强制断开编排（Spec #4204 / Issue #4211）。

保持单一活跃对象存储连接，不引入只读退役连接列表：

1. **影响评估**（:func:`inspect_relocation_plan`）：遍历全部持有方，按跨持有方 SHA-256 摘要识别可本地复用的副本，
   统计在用对象数、需从旧桶下载数量与预计体积、受影响旧桶入库租约数及无法拉回清单；
2. **幂等收敛**（:func:`run_relocation`）：先在短连接锁内持久化 ``relocating`` 与 ``delete_remote`` 选择，
   释放连接锁后再按对象推进（优先本地复用，其次下载校验 SHA-256，各持有方在其自身锁内复核身份与旧定位后转正）；
   中途网络中断、磁盘满或进程重启均不破坏旧连接与已完成转正的本地文件，可继续完成或取消；
3. **旧桶失联强制断开**（:func:`force_disconnect_connection`）：先按 SHA-256 摘要跨持有方抢救所有尚存有效本地工作文件
   转为永久本地文件，再在短连接锁内清理登记记录、移除旧连接并将新增图片保存位置切回本地。
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.asset import Asset, AssetDerivative
from lib.db.models.object_storage import FormalObject, FormalObjectRef, ObjectStorageConnection
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import ConflictError, NotFoundError, UnprocessableError
from lib.infra.async_thread import run_noninterruptible_async
from lib.infra.data_root_layout import DataRootLayout, list_project_dirs
from lib.material.formal_objects import (
    INGEST_HOLDER_PREFIX,
    NewContentTarget,
    connection_change_lock,
    formal_object_sizes,
    holding_object_count,
    in_flight_upload_count,
    new_content_target,
    reconcile_project_references,
    set_new_content_target,
)
from lib.material.global_library import LIBRARY_HOLDER_PREFIX, GlobalImage
from lib.material.remote_holders import (
    RemoteContentHolder,
    RemoteContentItem,
    build_local_digest_index,
    default_remote_holders,
    verify_and_read_local,
)
from lib.material.storage import ObjectLocator
from lib.object_storage.client import (
    DEFAULT_ADDRESSING_STYLE,
    OBJECT_STORAGE_KIND_S3,
    AddressingStyle,
    ObjectStorageClient,
    ObjectStorageError,
    ObjectStorageKind,
    normalize_endpoint_url,
)
from lib.object_storage.connection import config_from_connection
from lib.object_storage.disposal import DISPOSAL_NETWORK_FAILURE_LIMIT, dispose_connection_objects, pending_ingest_count
from lib.object_storage.probe import sanitize_probe_diagnostic
from lib.project import deleted_projects

logger = logging.getLogger(__name__)

RelocationMode = Literal["switch", "delete"]
RelocationPhase = Literal["idle", "scanning", "converging", "finalizing", "completed", "failed"]


@dataclass(frozen=True, slots=True)
class RelocationTargetConfig:
    region: str
    endpoint: str
    bucket: str
    kind: ObjectStorageKind = OBJECT_STORAGE_KIND_S3
    addressing_style: AddressingStyle = DEFAULT_ADDRESSING_STYLE
    access_key_id: str | None = None
    secret_access_key: str | None = None


@dataclass(frozen=True, slots=True)
class UnrecoverableItem:
    holder_kind: str
    holder_label: str
    path: str
    digest: str
    object_key: str
    size: int


@dataclass
class RelocationProgress:
    running: bool = False
    phase: RelocationPhase = "idle"
    mode: RelocationMode | None = None
    delete_remote_objects: bool = True
    total_objects: int = 0
    completed_objects: int = 0
    downloaded_objects: int = 0
    reused_local_objects: int = 0
    total_references: int = 0
    promoted_references: int = 0
    conflicts_preserved: int = 0
    failed_objects: int = 0
    last_error_kind: str | None = None
    last_error_message: str | None = None
    unreachable: bool = False

    def snapshot(self) -> RelocationProgress:
        return RelocationProgress(
            running=self.running,
            phase=self.phase,
            mode=self.mode,
            delete_remote_objects=self.delete_remote_objects,
            total_objects=self.total_objects,
            completed_objects=self.completed_objects,
            downloaded_objects=self.downloaded_objects,
            reused_local_objects=self.reused_local_objects,
            total_references=self.total_references,
            promoted_references=self.promoted_references,
            conflicts_preserved=self.conflicts_preserved,
            failed_objects=self.failed_objects,
            last_error_kind=self.last_error_kind,
            last_error_message=self.last_error_message,
            unreachable=self.unreachable,
        )


@dataclass(frozen=True, slots=True)
class RelocationPlan:
    connection_id: int
    in_use_object_count: int
    in_use_reference_count: int
    download_object_count: int
    estimated_download_bytes: int
    pending_ingest_tasks: int
    in_flight_uploads: int
    relocating: bool
    relocate_delete_remote: bool | None
    relocate_mode: RelocationMode | None
    unrecoverable_items: tuple[UnrecoverableItem, ...]
    progress: RelocationProgress


@dataclass(frozen=True, slots=True)
class RelocationRunResult:
    status: Literal["completed", "incomplete"]
    mode: RelocationMode
    delete_remote_objects: bool
    connection: ObjectStorageConnection | None
    new_image_target: NewContentTarget
    cleanup_failures: dict[str, int]
    total_objects: int
    completed_objects: int
    downloaded_objects: int
    reused_local_objects: int
    total_references: int
    promoted_references: int
    conflicts_preserved: int
    failed_objects: int
    error_kind: str | None
    error_message: str | None
    unreachable: bool
    unrecoverable_items: tuple[UnrecoverableItem, ...]


@dataclass(frozen=True, slots=True)
class ForceDisconnectResult:
    salvaged_objects: int
    salvaged_references: int
    conflicts_preserved: int
    unrecoverable_items: tuple[UnrecoverableItem, ...]
    new_image_target: NewContentTarget


_RUN_LOCKS: dict[int, asyncio.Lock] = {}
_PROGRESS_BY_LOOP: dict[int, RelocationProgress] = {}


def _run_lock() -> asyncio.Lock:
    loop_key = id(asyncio.get_running_loop())
    lock = _RUN_LOCKS.get(loop_key)
    if lock is None:
        lock = _RUN_LOCKS[loop_key] = asyncio.Lock()
    return lock


def _progress() -> RelocationProgress:
    loop_key = id(asyncio.get_running_loop())
    state = _PROGRESS_BY_LOOP.get(loop_key)
    if state is None:
        state = _PROGRESS_BY_LOOP[loop_key] = RelocationProgress()
    return state


def get_relocation_progress() -> RelocationProgress:
    return _progress().snapshot()


def _same_endpoint(existing_endpoint: str, raw_endpoint: str, normalized_endpoint: str) -> bool:
    if existing_endpoint == raw_endpoint:
        return True
    try:
        return normalize_endpoint_url(existing_endpoint) == normalized_endpoint
    except ObjectStorageError:
        return False


def _validate_switch_target(
    existing: ObjectStorageConnection, target: RelocationTargetConfig | None
) -> tuple[str, bool]:
    if target is None:
        raise UnprocessableError("object_storage_fields_required")
    if not target.region or not target.endpoint or not target.bucket:
        raise UnprocessableError("object_storage_fields_required")
    try:
        normalized_endpoint = normalize_endpoint_url(target.endpoint)
    except ObjectStorageError as exc:
        raise UnprocessableError("object_storage_invalid_config") from exc
    same_endpoint = _same_endpoint(existing.endpoint, target.endpoint, normalized_endpoint)
    target_changed = (not same_endpoint) or (existing.bucket != target.bucket)
    if target_changed and (not target.access_key_id or not target.secret_access_key):
        raise UnprocessableError("object_storage_target_keys_required")
    return (normalized_endpoint if target_changed else existing.endpoint), target_changed


async def _enumerate_all(
    holders: Sequence[RemoteContentHolder],
    connection_id: int,
    bucket: str,
    *,
    strict_trash: bool = True,
) -> list[tuple[RemoteContentHolder, RemoteContentItem]]:
    collected: list[tuple[RemoteContentHolder, RemoteContentItem]] = []
    for holder in holders:
        items = await holder.enumerate_remote_items(connection_id, bucket, strict_trash=strict_trash)
        collected.extend((holder, item) for item in items)
    return collected


def _build_unrecoverable(
    items: Sequence[RemoteContentItem],
    local_index: Mapping[str, object],
    sizes: dict[ObjectLocator, int],
) -> tuple[UnrecoverableItem, ...]:
    missing: list[UnrecoverableItem] = []
    for item in items:
        if item.digest in local_index:
            continue
        missing.append(
            UnrecoverableItem(
                holder_kind=item.holder_kind,
                holder_label=item.display_label,
                path=item.path,
                digest=item.digest,
                object_key=item.locator.key,
                size=sizes.get(item.locator, 0),
            )
        )
    return tuple(missing)


async def inspect_relocation_plan(
    layout: DataRootLayout,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    extra_holders: Sequence[RemoteContentHolder] = (),
) -> RelocationPlan:
    """扫描所有持有方，评估当前连接收敛回本地所需下载量与受影响租约。"""
    async with session_factory() as session:
        conn = await ObjectStorageConnectionRepository(session).get()
        if conn is None:
            raise NotFoundError("object_storage_not_configured")
        conn_id = conn.id
        conn_bucket = conn.bucket
        relocating = bool(conn.relocating)
        relocate_delete_remote = conn.relocate_delete_remote
        relocate_mode: RelocationMode | None = (
            "switch" if conn.relocate_mode == "switch" else "delete" if conn.relocate_mode == "delete" else None
        )
        pending_ingest = await pending_ingest_count(session, conn_id)
        in_flight = await in_flight_upload_count(session, conn_id)
        db_holding = await holding_object_count(session, conn_id)

    holders = default_remote_holders(layout, session_factory, extra_holders=extra_holders)
    pairs = await _enumerate_all(holders, conn_id, conn_bucket, strict_trash=True)
    items = [item for _holder, item in pairs]
    local_index = await asyncio.to_thread(build_local_digest_index, items)
    unique_objects: dict[ObjectLocator, str] = {}
    for item in items:
        unique_objects.setdefault(item.locator, item.digest)
    sizes = await formal_object_sizes(unique_objects.keys(), session_factory=session_factory)

    download_locators = [loc for loc, digest in unique_objects.items() if digest not in local_index]
    estimated_bytes = sum(sizes.get(loc, 0) for loc in download_locators)
    unrecoverable = _build_unrecoverable(items, local_index, sizes)
    in_use_objects = max(len(unique_objects), db_holding)

    return RelocationPlan(
        connection_id=conn_id,
        in_use_object_count=in_use_objects,
        in_use_reference_count=len(items),
        download_object_count=len(download_locators),
        estimated_download_bytes=estimated_bytes,
        pending_ingest_tasks=pending_ingest,
        in_flight_uploads=in_flight,
        relocating=relocating,
        relocate_delete_remote=relocate_delete_remote,
        relocate_mode=relocate_mode,
        unrecoverable_items=unrecoverable,
        progress=get_relocation_progress(),
    )


async def _reconcile_remaining_db_refs(
    layout: DataRootLayout,
    session_factory: async_sessionmaker[AsyncSession],
    connection_id: int,
) -> None:
    """在终态检查前，对数据库里仍持有旧连接引用的全部活跃项目、物理墓碑与全局资产条目做一次对账。"""
    async with session_factory() as session:
        stmt = (
            select(FormalObjectRef.project_name)
            .join(FormalObject, FormalObject.id == FormalObjectRef.object_id)
            .where(
                FormalObject.connection_id == connection_id,
                ~FormalObjectRef.project_name.startswith(INGEST_HOLDER_PREFIX),
                FormalObjectRef.state.in_(("active", "trashed")),
            )
            .distinct()
        )
        holders_in_db = list((await session.execute(stmt)).scalars())

    active_dirs = {p.name: p for p in await asyncio.to_thread(list_project_dirs, layout.projects_dir)}
    deleted_dirs = {
        entry.id: layout.deleted_projects_dir / entry.id
        for entry in await asyncio.to_thread(deleted_projects.list_entries, layout)
    }

    for holder in holders_in_db:
        if holder in active_dirs:
            await reconcile_project_references(holder, active_dirs[holder], session_factory=session_factory)
        elif holder in deleted_dirs:
            await reconcile_project_references(holder, deleted_dirs[holder], session_factory=session_factory)
        elif holder.startswith(LIBRARY_HOLDER_PREFIX):
            asset_id = holder.removeprefix(LIBRARY_HOLDER_PREFIX)
            async with session_factory() as session:
                asset = await session.get(Asset, asset_id)
                derivs = list(
                    (
                        await session.execute(select(AssetDerivative).where(AssetDerivative.asset_id == asset_id))
                    ).scalars()
                )
                active_content_ids: set[str] = set()
                for row in (asset, *derivs):
                    if row is None:
                        continue
                    img = GlobalImage.of(row)
                    if (
                        img is not None
                        and img.stored is not None
                        and img.stored.connection_id == connection_id
                        and img.content_id
                    ):
                        active_content_ids.add(img.content_id)
                refs = list(
                    (
                        await session.execute(
                            select(FormalObjectRef)
                            .join(FormalObject, FormalObject.id == FormalObjectRef.object_id)
                            .where(
                                FormalObject.connection_id == connection_id,
                                FormalObjectRef.project_name == holder,
                                FormalObjectRef.state == "active",
                            )
                        )
                    ).scalars()
                )
                for ref in refs:
                    if ref.content_version_id not in active_content_ids:
                        await session.delete(ref)
                await session.commit()


async def run_relocation(
    layout: DataRootLayout,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mode: RelocationMode,
    delete_remote_objects: bool,
    target: RelocationTargetConfig | None = None,
    extra_holders: Sequence[RemoteContentHolder] = (),
) -> RelocationRunResult:
    """幂等执行旧桶收敛回本地并在全部转正且零残留引用后原子切换或删除连接。"""
    lock = _run_lock()
    if lock.locked():
        raise ConflictError("object_storage_relocation_in_progress")

    async with lock:
        progress = _progress()
        async with connection_change_lock(), session_factory() as session:
            repo = ObjectStorageConnectionRepository(session)
            existing = await repo.get()
            if existing is None:
                raise NotFoundError("object_storage_not_configured")
            next_endpoint: str | None = None
            target_changed = False
            if mode == "switch":
                next_endpoint, target_changed = _validate_switch_target(existing, target)
            conn_id = existing.id
            conn_bucket = existing.bucket
            conn_config = config_from_connection(existing)
            await repo.set_relocating(conn_id, delete_remote=delete_remote_objects, mode=mode)
            await session.commit()
            in_flight = await in_flight_upload_count(session, conn_id)
            current_target = await new_content_target(session)

        progress.running = True
        progress.phase = "scanning"
        progress.mode = mode
        progress.delete_remote_objects = delete_remote_objects
        progress.total_objects = 0
        progress.completed_objects = 0
        progress.downloaded_objects = 0
        progress.reused_local_objects = 0
        progress.total_references = 0
        progress.promoted_references = 0
        progress.conflicts_preserved = 0
        progress.failed_objects = 0
        progress.last_error_kind = None
        progress.last_error_message = None
        progress.unreachable = False

        try:
            if in_flight > 0:
                progress.phase = "failed"
                progress.last_error_kind = "in_flight_uploads"
                progress.last_error_message = f"{in_flight} in-flight uploads"
                return RelocationRunResult(
                    status="incomplete",
                    mode=mode,
                    delete_remote_objects=delete_remote_objects,
                    connection=None,
                    new_image_target=current_target,
                    cleanup_failures={},
                    total_objects=0,
                    completed_objects=0,
                    downloaded_objects=0,
                    reused_local_objects=0,
                    total_references=0,
                    promoted_references=0,
                    conflicts_preserved=0,
                    failed_objects=in_flight,
                    error_kind="in_flight_uploads",
                    error_message=None,
                    unreachable=False,
                    unrecoverable_items=(),
                )

            holders = default_remote_holders(layout, session_factory, extra_holders=extra_holders)
            pairs = await _enumerate_all(holders, conn_id, conn_bucket, strict_trash=True)
            items = [item for _holder, item in pairs]
            local_index = await asyncio.to_thread(build_local_digest_index, items)

            groups: dict[tuple[ObjectLocator, str], list[tuple[RemoteContentHolder, RemoteContentItem]]] = defaultdict(
                list
            )
            for holder, item in pairs:
                groups[(item.locator, item.digest)].append((holder, item))

            ordered_groups = sorted(
                groups.items(),
                key=lambda entry: (0 if entry[0][1] in local_index else 1, entry[0][0].key),
            )
            progress.phase = "converging"
            progress.total_objects = len(ordered_groups)
            progress.total_references = len(pairs)

            client: ObjectStorageClient | None = None
            network_failures = 0
            bucket_unreachable = False
            disk_aborted = False
            touched_holders: dict[tuple[int, str], tuple[RemoteContentHolder, list[RemoteContentItem]]] = {}

            for (locator, digest), group_entries in ordered_groups:
                if disk_aborted:
                    progress.failed_objects += 1
                    continue

                data: bytes | None = None
                reused_local = False
                local_path = local_index.get(digest)
                if local_path is not None:
                    data = await asyncio.to_thread(verify_and_read_local, local_path, digest)
                    if data is not None:
                        reused_local = True

                if data is None:
                    if bucket_unreachable:
                        progress.failed_objects += 1
                        continue
                    try:
                        if client is None:
                            client = ObjectStorageClient(conn_config)
                        downloaded = await client.get_bytes(locator.key)
                    except ObjectStorageError as exc:
                        progress.failed_objects += 1
                        progress.last_error_kind = exc.kind
                        progress.last_error_message = sanitize_probe_diagnostic(exc.message or exc.code)
                        progress.unreachable = True
                        if exc.kind in ("auth", "denied"):
                            bucket_unreachable = True
                        elif exc.kind == "network":
                            network_failures += 1
                            if network_failures >= DISPOSAL_NETWORK_FAILURE_LIMIT:
                                bucket_unreachable = True
                        continue
                    actual_digest = await asyncio.to_thread(lambda b: hashlib.sha256(b).hexdigest(), downloaded)
                    if actual_digest != digest:
                        progress.failed_objects += 1
                        progress.last_error_kind = "digest_mismatch"
                        progress.last_error_message = "downloaded object digest mismatch"
                        progress.unreachable = True
                        continue
                    data = downloaded

                group_failed = False
                for holder, item in group_entries:
                    try:
                        res = await holder.promote_item(item, data)
                    except OSError as exc:
                        group_failed = True
                        disk_aborted = True
                        progress.last_error_kind = "disk_full" if exc.errno == errno.ENOSPC else "io_error"
                        progress.last_error_message = exc.strerror or str(exc)
                        progress.unreachable = False
                        break
                    if res.promoted:
                        progress.promoted_references += 1
                        if res.conflict_preserved:
                            progress.conflicts_preserved += 1
                        bucket_key = (id(holder), item.holder_key)
                        touched_holders.setdefault(bucket_key, (holder, []))[1].append(item)
                if group_failed:
                    progress.failed_objects += 1
                    continue

                progress.completed_objects += 1
                if reused_local:
                    progress.reused_local_objects += 1
                else:
                    progress.downloaded_objects += 1

            for holder, promoted_items in touched_holders.values():
                await holder.reconcile_holder(promoted_items[0].holder_key, promoted_items, session_factory)

            if progress.failed_objects > 0:
                progress.phase = "failed"
                remaining_pairs = await _enumerate_all(holders, conn_id, conn_bucket, strict_trash=True)
                remaining_items = [item for _holder, item in remaining_pairs]
                remaining_local = await asyncio.to_thread(build_local_digest_index, remaining_items)
                sizes = await formal_object_sizes(
                    {item.locator for item in remaining_items}, session_factory=session_factory
                )
                unrecoverable = _build_unrecoverable(remaining_items, remaining_local, sizes)
                return RelocationRunResult(
                    status="incomplete",
                    mode=mode,
                    delete_remote_objects=delete_remote_objects,
                    connection=None,
                    new_image_target=current_target,
                    cleanup_failures={},
                    total_objects=progress.total_objects,
                    completed_objects=progress.completed_objects,
                    downloaded_objects=progress.downloaded_objects,
                    reused_local_objects=progress.reused_local_objects,
                    total_references=progress.total_references,
                    promoted_references=progress.promoted_references,
                    conflicts_preserved=progress.conflicts_preserved,
                    failed_objects=progress.failed_objects,
                    error_kind=progress.last_error_kind,
                    error_message=progress.last_error_message,
                    unreachable=progress.unreachable,
                    unrecoverable_items=unrecoverable,
                )

            progress.phase = "finalizing"
            await _reconcile_remaining_db_refs(layout, session_factory, conn_id)
            remaining_after = await _enumerate_all(holders, conn_id, conn_bucket, strict_trash=True)
            if remaining_after:
                progress.phase = "failed"
                progress.last_error_kind = "remaining_references"
                progress.last_error_message = f"{len(remaining_after)} remote references remain"
                return RelocationRunResult(
                    status="incomplete",
                    mode=mode,
                    delete_remote_objects=delete_remote_objects,
                    connection=None,
                    new_image_target=current_target,
                    cleanup_failures={},
                    total_objects=progress.total_objects,
                    completed_objects=progress.completed_objects,
                    downloaded_objects=progress.downloaded_objects,
                    reused_local_objects=progress.reused_local_objects,
                    total_references=progress.total_references,
                    promoted_references=progress.promoted_references,
                    conflicts_preserved=progress.conflicts_preserved,
                    failed_objects=len(remaining_after),
                    error_kind="remaining_references",
                    error_message=progress.last_error_message,
                    unreachable=False,
                    unrecoverable_items=(),
                )

            async def _finalize() -> tuple[ObjectStorageConnection | None, NewContentTarget, dict[str, int], int]:
                async with connection_change_lock(), AsyncExitStack() as stack, session_factory() as session:
                    repo = ObjectStorageConnectionRepository(session)
                    current = await repo.get()
                    if current is None or current.id != conn_id or not current.relocating:
                        raise ConflictError("object_storage_relocating")
                    remaining_db = await holding_object_count(session, current.id)
                    if remaining_db > 0:
                        return None, await new_content_target(session), {}, remaining_db

                    effective_delete = bool(current.relocate_delete_remote)
                    failures = await stack.enter_async_context(
                        dispose_connection_objects(session, current, delete_remote=effective_delete)
                    )

                    if mode == "switch":
                        assert target is not None
                        assert next_endpoint is not None
                        saved = await repo.save(
                            kind=target.kind,
                            region=target.region,
                            endpoint=next_endpoint,
                            bucket=target.bucket,
                            addressing_style=target.addressing_style,
                            access_key_id=target.access_key_id or None,
                            secret_access_key=target.secret_access_key or None,
                            needs_endpoint_review=False if target_changed else bool(current.needs_endpoint_review),
                            clear_relocation=True,
                        )
                        final_target = await new_content_target(session)
                        await session.commit()
                        return saved, final_target, failures, 0

                    await repo.delete()
                    await set_new_content_target(session, "local")
                    await session.commit()
                    return None, "local", failures, 0

            final_conn, final_target, cleanup_failures, remaining_db = await run_noninterruptible_async(_finalize())
            if remaining_db > 0:
                progress.phase = "failed"
                progress.last_error_kind = "remaining_references"
                progress.last_error_message = f"{remaining_db} formal objects remain held"
                return RelocationRunResult(
                    status="incomplete",
                    mode=mode,
                    delete_remote_objects=delete_remote_objects,
                    connection=None,
                    new_image_target=final_target,
                    cleanup_failures={},
                    total_objects=progress.total_objects,
                    completed_objects=progress.completed_objects,
                    downloaded_objects=progress.downloaded_objects,
                    reused_local_objects=progress.reused_local_objects,
                    total_references=progress.total_references,
                    promoted_references=progress.promoted_references,
                    conflicts_preserved=progress.conflicts_preserved,
                    failed_objects=remaining_db,
                    error_kind="remaining_references",
                    error_message=progress.last_error_message,
                    unreachable=False,
                    unrecoverable_items=(),
                )

            progress.phase = "completed"
            return RelocationRunResult(
                status="completed",
                mode=mode,
                delete_remote_objects=delete_remote_objects,
                connection=final_conn,
                new_image_target=final_target,
                cleanup_failures=cleanup_failures,
                total_objects=progress.total_objects,
                completed_objects=progress.completed_objects,
                downloaded_objects=progress.downloaded_objects,
                reused_local_objects=progress.reused_local_objects,
                total_references=progress.total_references,
                promoted_references=progress.promoted_references,
                conflicts_preserved=progress.conflicts_preserved,
                failed_objects=0,
                error_kind=None,
                error_message=None,
                unreachable=False,
                unrecoverable_items=(),
            )
        finally:
            progress.running = False


async def cancel_relocation(session_factory: async_sessionmaker[AsyncSession]) -> bool:
    """取消未完成的收敛回本地流程：保留已转正的本地文件与旧连接，恢复旧连接正常使用。"""
    lock = _run_lock()
    if lock.locked():
        raise ConflictError("object_storage_relocation_in_progress")
    async with lock, connection_change_lock(), session_factory() as session:
        repo = ObjectStorageConnectionRepository(session)
        conn = await repo.get()
        if conn is None:
            raise NotFoundError("object_storage_not_configured")
        was_relocating = bool(conn.relocating)
        await repo.clear_relocating(conn.id)
        await session.commit()
    progress = _progress()
    progress.running = False
    progress.phase = "idle"
    progress.mode = None
    progress.last_error_kind = None
    progress.last_error_message = None
    progress.unreachable = False
    return was_relocating


async def force_disconnect_connection(
    layout: DataRootLayout,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    extra_holders: Sequence[RemoteContentHolder] = (),
) -> ForceDisconnectResult:
    """旧桶永久不可达时的强制断开：先按 SHA-256 跨持有方抢救全部尚存有效本地工作文件，再移除旧连接。"""
    lock = _run_lock()
    if lock.locked():
        raise ConflictError("object_storage_relocation_in_progress")

    async with lock:
        async with connection_change_lock(), session_factory() as session:
            repo = ObjectStorageConnectionRepository(session)
            existing = await repo.get()
            if existing is None:
                raise NotFoundError("object_storage_not_configured")
            conn_id = existing.id
            conn_bucket = existing.bucket
            await repo.set_relocating(conn_id, delete_remote=False, mode="delete")
            await session.commit()

        holders = default_remote_holders(layout, session_factory, extra_holders=extra_holders)
        pairs = await _enumerate_all(holders, conn_id, conn_bucket, strict_trash=False)
        items = [item for _holder, item in pairs]
        local_index = await asyncio.to_thread(build_local_digest_index, items)
        sizes = await formal_object_sizes({item.locator for item in items}, session_factory=session_factory)

        groups: dict[tuple[ObjectLocator, str], list[tuple[RemoteContentHolder, RemoteContentItem]]] = defaultdict(list)
        for holder, item in pairs:
            groups[(item.locator, item.digest)].append((holder, item))

        salvaged_objects = 0
        salvaged_references = 0
        conflicts_preserved = 0
        unrecoverable_items: list[UnrecoverableItem] = []

        for (locator, digest), group_entries in groups.items():
            local_path = local_index.get(digest)
            data = (
                await asyncio.to_thread(verify_and_read_local, local_path, digest) if local_path is not None else None
            )
            if data is None:
                for _holder, item in group_entries:
                    unrecoverable_items.append(
                        UnrecoverableItem(
                            holder_kind=item.holder_kind,
                            holder_label=item.display_label,
                            path=item.path,
                            digest=item.digest,
                            object_key=item.locator.key,
                            size=sizes.get(locator, 0),
                        )
                    )
                continue
            group_salvaged = False
            for holder, item in group_entries:
                res = await holder.promote_item(item, data)
                if res.promoted:
                    salvaged_references += 1
                    group_salvaged = True
                    if res.conflict_preserved:
                        conflicts_preserved += 1
            if group_salvaged:
                salvaged_objects += 1

        async def _remove_connection() -> None:
            async with connection_change_lock(), AsyncExitStack() as stack, session_factory() as session:
                repo = ObjectStorageConnectionRepository(session)
                current = await repo.get()
                if current is not None and current.id == conn_id:
                    await stack.enter_async_context(dispose_connection_objects(session, current, delete_remote=False))
                    await repo.delete()
                await set_new_content_target(session, "local")
                await session.commit()

        await run_noninterruptible_async(_remove_connection())

        progress = _progress()
        progress.running = False
        progress.phase = "idle"
        progress.mode = None
        progress.last_error_kind = None
        progress.last_error_message = None
        progress.unreachable = False

        return ForceDisconnectResult(
            salvaged_objects=salvaged_objects,
            salvaged_references=salvaged_references,
            conflicts_preserved=conflicts_preserved,
            unrecoverable_items=tuple(unrecoverable_items),
            new_image_target="local",
        )


__all__ = [
    "ForceDisconnectResult",
    "RelocationMode",
    "RelocationPhase",
    "RelocationPlan",
    "RelocationProgress",
    "RelocationRunResult",
    "RelocationTargetConfig",
    "UnrecoverableItem",
    "cancel_relocation",
    "force_disconnect_connection",
    "get_relocation_progress",
    "inspect_relocation_plan",
    "run_relocation",
]
