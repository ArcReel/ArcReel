"""后台推进入库任务：中转上传、提交 CreateAsset、查询处理状态，并清理到期的中转副本。

页面与 Agent 只创建入库任务并查询状态，不等待供应商审核；推进全部在这里进行，进程重启后从数据库继续。
状态与选定的含义见 :mod:`lib.asset_library.links`。每一步都只按条件更新状态事实，不改变 ``selected``。

:func:`run_once` 是一轮完整的处理，测试直接调用它；:class:`AssetIngestRunner` 在应用生命周期里循环调用，
新任务创建后由 :func:`wake_asset_ingest` 提前唤醒。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.asset_library.control_plane import (
    AssetLibraryRequestError,
    ControlPlaneCredentials,
    create_asset,
    ensure_asset_group,
    find_assets_by_name,
    get_asset,
)
from lib.db.base import utc_now
from lib.db.models.asset_library import AssetLibraryAccount, ProviderAssetLink
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import ApiError
from lib.infra.async_thread import run_noninterruptible_async
from lib.infra.logging_utils import redact_diagnostic_text
from lib.infra.task_cancel import raise_if_cancel_absorbed
from lib.material.storage import (
    ContentVersion,
    LocalLocator,
    LocalMaterialStorage,
    MaterialRef,
    VersionSnapshotCorruptedError,
)
from lib.object_storage.client import ObjectStorageClient, ObjectStorageError
from lib.object_storage.connection import config_from_connection
from lib.object_storage.relay import (
    RELAY_URL_TTL,
    mark_relay_signed,
    record_relay_object,
    relay_object_key,
    sweep_relay_objects,
)
from lib.project.project_manager import get_project_manager

logger = logging.getLogger(__name__)

#: ArcReel 上传的素材所在的 AIGC 素材组名称，每个账号项目范围一个。
ASSET_GROUP_NAME = "ArcReel"
_ASSET_GROUP_DESCRIPTION = "ArcReel 上传的图片素材"

#: 提交后第一次查询状态的等待时间，之后按指数退避到 :data:`CHECK_MAX_INTERVAL`。
CHECK_INITIAL_DELAY = timedelta(seconds=5)
CHECK_MAX_INTERVAL = timedelta(minutes=5)
#: 自提交起自动查询的时长；之后停在当前状态，由用户手动刷新。
POLL_WINDOW = timedelta(hours=72)
#: 结果不确定的任务按名称查回素材的时长：下载地址过期后供应商已无法再取图。
UNCERTAIN_LOOKUP_WINDOW = RELAY_URL_TTL + timedelta(hours=1)
#: 循环在没有被唤醒时的轮询间隔。
LOOP_INTERVAL_SECONDS = 10.0

_MESSAGE_LIMIT = 500
# 不用 mimetypes：它首次调用时读系统文件，会阻塞事件循环。
_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _default_project_path(project_name: str) -> Path:
    return get_project_manager().get_project_path(project_name)


@dataclass(frozen=True)
class _LinkSnapshot:
    id: int
    project_name: str
    content_version_id: str
    content_digest: str
    resource_type: str
    resource_id: str
    version: int
    locator_path: str
    account_id: int
    site: str
    region: str
    provider_project_name: str
    state: str
    selected: bool
    provider_asset_id: str | None
    provider_group_id: str | None
    submit_name: str
    submitted_at: datetime | None
    check_attempts: int


def _snapshot(link: ProviderAssetLink) -> _LinkSnapshot:
    return _LinkSnapshot(
        id=link.id,
        project_name=link.project_name,
        content_version_id=link.content_version_id,
        content_digest=link.content_digest,
        resource_type=link.resource_type,
        resource_id=link.resource_id,
        version=link.version,
        locator_path=link.locator_path,
        account_id=link.account_id,
        site=link.site,
        region=link.region,
        provider_project_name=link.provider_project_name,
        state=link.state,
        selected=link.selected,
        provider_asset_id=link.provider_asset_id,
        provider_group_id=link.provider_group_id,
        submit_name=link.submit_name,
        submitted_at=_aware(link.submitted_at),
        check_attempts=link.check_attempts,
    )


def _credentials(account: AssetLibraryAccount) -> ControlPlaneCredentials:
    return ControlPlaneCredentials(
        site=account.site,
        region=account.region,
        project_name=account.project_name,
        access_key_id=account.access_key_id,
        secret_access_key=account.secret_access_key,
    )


def _account_matches(account: AssetLibraryAccount | None, link: _LinkSnapshot) -> bool:
    """账号仍覆盖提交时的范围；账号被改到另一个站点、区域或项目后，不再用它查询或提交。"""
    return (
        account is not None
        and account.site == link.site
        and account.region == link.region
        and account.project_name == link.provider_project_name
    )


def _next_check(now: datetime, attempts: int) -> datetime:
    return now + min(CHECK_INITIAL_DELAY * (2 ** max(attempts, 0)), CHECK_MAX_INTERVAL)


def _clip(text: str | None) -> str | None:
    return redact_diagnostic_text(text)[:_MESSAGE_LIMIT] if text else None


async def _update(
    session_factory: async_sessionmaker[AsyncSession], link_id: int, *conditions: Any, **values: Any
) -> bool:
    """按条件更新一条关联的状态事实，返回是否命中。不触碰 ``selected``。"""
    assert "selected" not in values
    async with session_factory() as session:
        result = await session.execute(
            update(ProviderAssetLink)
            .where(ProviderAssetLink.id == link_id, *conditions)
            .values(updated_at=utc_now(), **values)
        )
        await session.commit()
        return bool(getattr(result, "rowcount", 0))


async def _fail(
    session_factory: async_sessionmaker[AsyncSession],
    link_id: int,
    now: datetime,
    code: str,
    message: str | None = None,
) -> None:
    # 只用于提交之前的失败：此时供应商侧没有创建素材。
    await _update(
        session_factory,
        link_id,
        ProviderAssetLink.state == "pending",
        state="failed",
        error_code=code[:128],
        error_message=_clip(message),
        settled_at=now,
        next_check_at=None,
    )


def _read_content(project_path_of: Callable[[str], Path], link: _LinkSnapshot) -> bytes:
    project_path = project_path_of(link.project_name)
    content_version = ContentVersion(
        material=MaterialRef(link.resource_type, link.resource_id),
        version=link.version,
        id=link.content_version_id,
        digest=link.content_digest,
        locator=LocalLocator(link.locator_path),
    )
    return LocalMaterialStorage(project_path).open(content_version).read_bytes()


async def _submit(
    session_factory: async_sessionmaker[AsyncSession],
    link: _LinkSnapshot,
    now: datetime,
    project_path_of: Callable[[str], Path],
) -> None:
    if not link.selected:
        await _fail(session_factory, link.id, now, "unlinked_before_submit")
        return
    async with session_factory() as session:
        account = await session.get(AssetLibraryAccount, link.account_id)
        connection = await ObjectStorageConnectionRepository(session).get()
    if account is None:
        await _fail(session_factory, link.id, now, "account_missing")
        return
    if not _account_matches(account, link):
        await _fail(session_factory, link.id, now, "account_scope_changed")
        return
    if connection is None:
        await _fail(session_factory, link.id, now, "object_storage_missing")
        return

    # 内容按提交时冻结的内容版本读取并校验摘要：入库期间改选或替换图片不影响这次上传。
    try:
        data = await asyncio.to_thread(_read_content, project_path_of, link)
    except VersionSnapshotCorruptedError:
        await _fail(session_factory, link.id, now, "content_changed")
        return
    except (OSError, ValueError, ApiError):  # 快照不在盘上，或项目已不存在、已删除
        await _fail(session_factory, link.id, now, "content_unavailable")
        return

    suffix = Path(link.locator_path).suffix or ".png"
    key = relay_object_key(suffix, now=now)
    relay_id = await record_relay_object(session_factory, connection, key, link_id=link.id)
    client = ObjectStorageClient(config_from_connection(connection))
    try:
        await client.put_bytes(key, data, content_type=_CONTENT_TYPES.get(suffix.lower()))
        url = await client.presign_get(key, expires_seconds=int(RELAY_URL_TTL.total_seconds()))
    except ObjectStorageError as exc:
        await _fail(session_factory, link.id, now, "relay_upload_failed", f"{exc.kind}:{exc.code}")
        return
    await mark_relay_signed(session_factory, relay_id, now + RELAY_URL_TTL)

    credentials = _credentials(account)
    try:
        group_id = await ensure_asset_group(credentials, ASSET_GROUP_NAME, description=_ASSET_GROUP_DESCRIPTION)
    except AssetLibraryRequestError as exc:
        # 素材组只是容器，没有提交素材，失败即可判定入库未发生。
        await _fail(session_factory, link.id, now, exc.code or f"request_{exc.kind}", exc.message)
        return
    raise_if_cancel_absorbed()

    # 发出 CreateAsset 前确认任务仍被选定且未被处理过；之后进程中断，按结果不确定处理。
    started = await _update(
        session_factory,
        link.id,
        ProviderAssetLink.state == "pending",
        ProviderAssetLink.selected.is_(True),
        state="submitting",
        submitted_at=now,
        provider_group_id=group_id,
    )
    if not started:
        current = await _load(session_factory, link.id)
        if current is not None and current.state == "pending":
            await _fail(session_factory, link.id, now, "unlinked_before_submit")
        return

    try:
        asset_id = await create_asset(credentials, group_id=group_id, url=url, name=link.submit_name)
    except AssetLibraryRequestError as exc:
        values: dict[str, Any] = {"error_code": exc.code or f"request_{exc.kind}", "error_message": _clip(exc.message)}
        if exc.outcome_unknown:
            values |= {"state": "uncertain", "next_check_at": now + CHECK_INITIAL_DELAY, "check_attempts": 0}
        else:
            values |= {"state": "failed", "settled_at": now, "next_check_at": None}
        await run_noninterruptible_async(
            _update(session_factory, link.id, ProviderAssetLink.state == "submitting", **values)
        )
        return
    # 供应商已经接受：素材 ID 落账不可中断，否则重启后只能按结果不确定去查回。
    await run_noninterruptible_async(
        _update(
            session_factory,
            link.id,
            ProviderAssetLink.state == "submitting",
            state="processing",
            provider_asset_id=asset_id,
            error_code=None,
            error_message=None,
            next_check_at=now + CHECK_INITIAL_DELAY,
            check_attempts=0,
        )
    )
    raise_if_cancel_absorbed()


def _schedule_after(link: _LinkSnapshot, now: datetime, window: timedelta) -> datetime | None:
    """下一次自动查询的时间；超出窗口后不再自动查询。"""
    started = link.submitted_at or now
    if now - started >= window:
        return None
    return _next_check(now, link.check_attempts)


async def _check_processing(
    session_factory: async_sessionmaker[AsyncSession], link: _LinkSnapshot, now: datetime
) -> None:
    async with session_factory() as session:
        account = await session.get(AssetLibraryAccount, link.account_id)
    if not _account_matches(account, link):
        await _update(
            session_factory,
            link.id,
            check_error="account_unavailable",
            next_check_at=None,
        )
        return
    assert account is not None
    assert link.provider_asset_id is not None
    try:
        detail = await get_asset(_credentials(account), link.provider_asset_id)
    except AssetLibraryRequestError as exc:
        await _update(
            session_factory,
            link.id,
            check_error=(exc.code or f"request_{exc.kind}")[:128],
            check_attempts=link.check_attempts + 1,
            next_check_at=_schedule_after(link, now, POLL_WINDOW),
        )
        return
    raise_if_cancel_absorbed()
    if detail.status == "active":
        await _update(session_factory, link.id, state="active", settled_at=now, next_check_at=None, check_error=None)
    elif detail.status == "failed":
        await _update(
            session_factory,
            link.id,
            state="failed",
            error_code=(detail.error_code or "provider_failed")[:128],
            error_message=_clip(detail.error_message),
            settled_at=now,
            next_check_at=None,
            check_error=None,
        )
    else:
        await _update(
            session_factory,
            link.id,
            state="processing",
            check_error=None,
            check_attempts=link.check_attempts + 1,
            next_check_at=_schedule_after(link, now, POLL_WINDOW),
        )


async def _check_uncertain(
    session_factory: async_sessionmaker[AsyncSession], link: _LinkSnapshot, now: datetime
) -> None:
    """提交结果不确定：按提交时的唯一名称在素材组里查回素材，查到即转为处理中，不重新创建。"""
    async with session_factory() as session:
        account = await session.get(AssetLibraryAccount, link.account_id)
    if not _account_matches(account, link) or not link.provider_group_id:
        await _update(session_factory, link.id, check_error="account_unavailable", next_check_at=None)
        return
    assert account is not None
    try:
        found = await find_assets_by_name(_credentials(account), group_id=link.provider_group_id, name=link.submit_name)
    except AssetLibraryRequestError as exc:
        await _update(
            session_factory,
            link.id,
            check_error=(exc.code or f"request_{exc.kind}")[:128],
            check_attempts=link.check_attempts + 1,
            next_check_at=_schedule_after(link, now, UNCERTAIN_LOOKUP_WINDOW),
        )
        return
    raise_if_cancel_absorbed()
    if len(found) == 1:
        await _update(
            session_factory,
            link.id,
            ProviderAssetLink.state == "uncertain",
            state="processing",
            provider_asset_id=found[0].id,
            check_error=None,
            check_attempts=0,
            next_check_at=now,
        )
        return
    await _update(
        session_factory,
        link.id,
        check_error="ambiguous_match" if found else "not_found_yet",
        check_attempts=link.check_attempts + 1,
        next_check_at=None if found else _schedule_after(link, now, UNCERTAIN_LOOKUP_WINDOW),
    )


async def _load(session_factory: async_sessionmaker[AsyncSession], link_id: int) -> _LinkSnapshot | None:
    async with session_factory() as session:
        link = await session.get(ProviderAssetLink, link_id)
        return _snapshot(link) if link is not None else None


async def _due_links(session_factory: async_sessionmaker[AsyncSession], now: datetime) -> list[_LinkSnapshot]:
    async with session_factory() as session:
        stmt = (
            select(ProviderAssetLink)
            .where(ProviderAssetLink.next_check_at.is_not(None), ProviderAssetLink.next_check_at <= now)
            .order_by(ProviderAssetLink.next_check_at, ProviderAssetLink.id)
        )
        return [_snapshot(link) for link in (await session.execute(stmt)).scalars()]


async def recover_interrupted(session_factory: async_sessionmaker[AsyncSession], *, now: datetime | None = None) -> int:
    """启动时把中断在提交中的任务改为结果不确定：CreateAsset 可能已经被供应商接受，不能重新提交。"""
    now = now or utc_now()
    async with session_factory() as session:
        result = await session.execute(
            update(ProviderAssetLink)
            .where(ProviderAssetLink.state == "submitting")
            .values(state="uncertain", next_check_at=now, check_attempts=0, updated_at=now)
        )
        await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)


async def run_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    project_path_of: Callable[[str], Path] = _default_project_path,
) -> None:
    """处理一轮：到期的入库任务逐条推进，然后清理到期的中转副本。单条失败不影响其余。"""
    now = now or utc_now()
    for link in await _due_links(session_factory, now):
        try:
            if link.state == "pending":
                await _submit(session_factory, link, now, project_path_of)
            elif link.state == "processing" and link.provider_asset_id:
                await _check_processing(session_factory, link, now)
            elif link.state == "uncertain":
                if link.provider_asset_id:
                    await _check_processing(session_factory, link, now)
                else:
                    await _check_uncertain(session_factory, link, now)
            else:
                await _update(session_factory, link.id, next_check_at=None)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("推进入库任务失败，稍后重试 link=%s", link.id, exc_info=True)
            await _update(
                session_factory,
                link.id,
                check_attempts=link.check_attempts + 1,
                next_check_at=_next_check(now, link.check_attempts + 1),
            )
    try:
        await sweep_relay_objects(session_factory, now=now)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("清理中转副本失败，等下一轮", exc_info=True)


class AssetIngestRunner:
    """在应用生命周期里循环调用 :func:`run_once`。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        interval_seconds: float = LOOP_INTERVAL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._interval = interval_seconds
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        try:
            await recover_interrupted(self._session_factory)
        except Exception:
            logger.warning("恢复中断的入库任务失败", exc_info=True)
        while True:
            self._wake.clear()
            try:
                await run_once(self._session_factory)
            except Exception:
                logger.warning("入库任务处理异常，等下一轮", exc_info=True)
            raise_if_cancel_absorbed()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)

    def start(self) -> asyncio.Task[None]:
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="asset-library-ingest")
        return self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None


_runner: list[AssetIngestRunner | None] = [None]


def start_asset_ingest(session_factory: async_sessionmaker[AsyncSession]) -> AssetIngestRunner:
    """启动后台入库循环：启动时先恢复中断的提交，之后每隔一段时间或被唤醒时处理一轮。"""
    runner = AssetIngestRunner(session_factory)
    runner.start()
    _runner[0] = runner
    return runner


async def stop_asset_ingest(runner: AssetIngestRunner) -> None:
    await runner.stop()
    if _runner[0] is runner:
        _runner[0] = None


def wake_asset_ingest() -> None:
    """有新任务或需要立即查询时提前唤醒后台循环；循环没有运行时不做任何事。"""
    runner = _runner[0]
    if runner is not None:
        runner.wake()


__all__ = [
    "ASSET_GROUP_NAME",
    "CHECK_INITIAL_DELAY",
    "POLL_WINDOW",
    "AssetIngestRunner",
    "recover_interrupted",
    "run_once",
    "start_asset_ingest",
    "stop_asset_ingest",
    "wake_asset_ingest",
]
