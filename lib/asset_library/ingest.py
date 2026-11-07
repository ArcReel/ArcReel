"""Advance ingest tasks in background: relay upload, CreateAsset submission, status queries, and expired-relay cleanup.

When content is already a formal object in the current bucket, sign it directly. Before signing,
register an ingest-holder reference (:func:`ingest_holder`, ``docs/adr/0118``), releasing after
the same retention rules as relays. Permanently clearing sources during provider retrieval
therefore cannot collect objects.

Pages/Agents only create/query tasks, never wait for provider review; advance everything here,
resuming from DB after restarts. State/selection meanings: :mod:`lib.asset_library.links`.
Each step conditionally updates state facts, never ``selected``.

:func:`run_once` performs one complete round for direct tests; :class:`AssetIngestRunner` loops
within app lifetime, woken early by :func:`wake_asset_ingest` after new task creation.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, get_args

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.version_manager import VersionManager
from lib.asset_library.bindings import account_identity
from lib.asset_library.control_plane import (
    AssetLibraryRequestError,
    create_asset,
    credentials_of,
    ensure_asset_group,
    find_assets_by_name,
    get_asset,
)
from lib.db.base import utc_now
from lib.db.models.asset_library import AssetLibraryAccount, ProviderAssetLink
from lib.db.models.object_storage import FormalObjectRef, ObjectStorageConnection
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import ApiError
from lib.infra.async_thread import run_noninterruptible_async
from lib.infra.logging_utils import redact_diagnostic_text
from lib.infra.task_cancel import raise_if_cancel_absorbed
from lib.material.formal_objects import REF_ACTIVE, add_reference, release_holder
from lib.material.storage import (
    ContentVersion,
    LocalLocator,
    MaterialRef,
    VersionSnapshotCorruptedError,
)
from lib.material.work_files import ensure_content_version_file, local_work_files
from lib.object_storage.client import IMAGE_CONTENT_TYPES, ObjectStorageClient, ObjectStorageError
from lib.object_storage.connection import config_from_connection
from lib.object_storage.relay import (
    RELAY_URL_TTL,
    mark_relay_signed,
    record_relay_object,
    relay_object_key,
    signed_content_retained_until,
    sweep_relay_objects,
)
from lib.project.project_manager import get_project_manager

logger = logging.getLogger(__name__)

#: AIGC group name for ArcReel uploads, one per account/project scope.
ASSET_GROUP_NAME = "ArcReel"
_ASSET_GROUP_DESCRIPTION = "ArcReel 上传的图片素材"

#: Delay before first post-submission status query, then exponential backoff to :data:`CHECK_MAX_INTERVAL`.
CHECK_INITIAL_DELAY = timedelta(seconds=5)
CHECK_MAX_INTERVAL = timedelta(minutes=5)
#: Automatic query duration from submission; afterward retain current state until manual refresh.
POLL_WINDOW = timedelta(hours=72)
#: Name-based recovery window for uncertain submissions: providers cannot fetch after URL expiry.
UNCERTAIN_LOOKUP_WINDOW = RELAY_URL_TTL + timedelta(hours=1)
#: Polling interval when the loop receives no wake-up.
LOOP_INTERVAL_SECONDS = 10.0

_MESSAGE_LIMIT = 500

#: Ingest-holder prefix for directly signed formal objects (nonproject consumer, ``docs/adr/0118``).
INGEST_HOLDER_PREFIX = "ingest:"


def ingest_holder(link_id: int) -> str:
    """Holder key when ingest task ``link_id`` references formal objects."""
    return f"{INGEST_HOLDER_PREFIX}{link_id}"


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
    account_identity: str
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
        account_identity=link.account_identity,
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


def _account_matches(account: AssetLibraryAccount | None, link: _LinkSnapshot) -> bool:
    """Account still covers submission scope. After cloud-account Access Key/site/region/project changes,
    never submit/query through it: new credentials could create assets in another account or
    overwrite links using another account’s results."""
    return (
        account is not None
        and account_identity(account.site, account.access_key_id) == link.account_identity
        and account.site == link.site
        and account.region == link.region
        and account.project_name == link.provider_project_name
    )


def _next_check(now: datetime, attempts: int) -> datetime:
    # Cap exponents before multiplying; accumulated query counts overflow timedelta before min can clamp.
    return now + min(CHECK_INITIAL_DELAY * (2 ** min(max(attempts, 0), 16)), CHECK_MAX_INTERVAL)


def _clip(text: str | None) -> str | None:
    return redact_diagnostic_text(text)[:_MESSAGE_LIMIT] if text else None


async def _update(
    session_factory: async_sessionmaker[AsyncSession], link_id: int, *conditions: Any, **values: Any
) -> bool:
    """Conditionally update link state facts and return whether matched, never touching ``selected``."""
    assert "selected" not in values
    async with session_factory() as session:
        result = await session.execute(
            update(ProviderAssetLink)
            .where(ProviderAssetLink.id == link_id, *conditions)
            .values(updated_at=utc_now(), **values)
        )
        await session.commit()
        return bool(getattr(result, "rowcount", 0))


#: ArcReel-determined pre-submission failures. UI localizes by code rather than treating as provider text; frontend codes
#: are checked bidirectionally through ``frontend/src/test/contracts/provider-asset-local-failures.json``.
LocalFailureCode = Literal[
    "unlinked_before_submit",
    "account_missing",
    "account_scope_changed",
    "object_storage_missing",
    "content_unavailable",
    "content_changed",
    "relay_upload_failed",
]
LOCAL_FAILURE_CODES: frozenset[str] = frozenset(get_args(LocalFailureCode))


async def _fail(
    session_factory: async_sessionmaker[AsyncSession],
    link_id: int,
    now: datetime,
    code: LocalFailureCode,
    message: str | None = None,
) -> None:
    await _fail_before_submit(session_factory, link_id, now, code, message)


async def _fail_before_submit(
    session_factory: async_sessionmaker[AsyncSession],
    link_id: int,
    now: datetime,
    code: str,
    message: str | None = None,
) -> None:
    # Pre-submission failures only: providers created no asset yet. ``code`` may be provider-returned.
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


def _frozen_content_version(project_path: Path, link: _LinkSnapshot) -> ContentVersion:
    # Resolve current locators by content identity: resource renames during ingest move snapshots, invalidating captured paths.
    # Always use submission-frozen digests; reject content differing from the link’s identified bytes.
    current = VersionManager(project_path).find_content_version(link.resource_type, link.content_version_id)
    return ContentVersion(
        material=current.material if current else MaterialRef(link.resource_type, link.resource_id),
        version=current.version if current else link.version,
        id=link.content_version_id,
        digest=link.content_digest,
        locator=current.locator if current else LocalLocator(link.locator_path),
        stored=current.stored if current and current.digest == link.content_digest else None,
    )


def _formal_object_on(connection: ObjectStorageConnection, content_version: ContentVersion) -> str | None:
    """Return object keys for content already stored formally in this connection’s bucket; sign directly without relay copies.

    Digest-named keys fix URLs to this version. These are not relays and relay cleanup never
    deletes them. Before signing, :func:`_hold_formal_object` registers ingest-holder references,
    preventing collection during retention even if source content is permanently cleared.
    """
    stored = content_version.stored
    if stored is None or stored.connection_id != connection.id or stored.bucket != connection.bucket:
        return None
    return stored.key


async def _content_bytes(
    project_path: Path, content_version: ContentVersion, session_factory: async_sessionmaker[AsyncSession]
) -> bytes:
    # Register use throughout fetch/read so cache cleanup cannot delete work files between them.
    async with local_work_files(project_path, [content_version.locator.path], session_factory=session_factory):
        path = await ensure_content_version_file(project_path, content_version, session_factory=session_factory)
        return await asyncio.to_thread(path.read_bytes)


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

    # Read/verify submission-frozen content versions; selection/replacement during ingest never affects this upload.
    client = ObjectStorageClient(config_from_connection(connection))
    try:
        project_path = await asyncio.to_thread(project_path_of, link.project_name)
        content_version = await asyncio.to_thread(_frozen_content_version, project_path, link)
        formal_key = _formal_object_on(connection, content_version)
        if formal_key is not None and not await _hold_formal_object(session_factory, link, content_version, now):
            formal_key = None  # Missing objects or states other than pending deletion: upload through relay copies.
        data = None if formal_key is not None else await _content_bytes(project_path, content_version, session_factory)
    except VersionSnapshotCorruptedError:
        await _fail(session_factory, link.id, now, "content_changed")
        return
    except (OSError, ValueError, ApiError):  # Snapshot absent, project missing/deleted, or remote retrieval failed.
        await _fail(session_factory, link.id, now, "content_unavailable")
        return

    try:
        if formal_key is not None:
            url = await client.presign_get(formal_key, expires_seconds=int(RELAY_URL_TTL.total_seconds()))
        else:
            assert data is not None
            suffix = Path(link.locator_path).suffix or ".png"
            key = relay_object_key(suffix, now=now)
            relay_id = await record_relay_object(session_factory, connection, key, link_id=link.id)
            await client.put_bytes(key, data, content_type=IMAGE_CONTENT_TYPES.get(suffix.lower()))
            url = await client.presign_get(key, expires_seconds=int(RELAY_URL_TTL.total_seconds()))
            await mark_relay_signed(session_factory, relay_id, now + RELAY_URL_TTL)
    except ObjectStorageError as exc:
        await _fail(session_factory, link.id, now, "relay_upload_failed", f"{exc.kind}:{exc.code}")
        return

    credentials = credentials_of(account)
    try:
        group_id = await ensure_asset_group(credentials, ASSET_GROUP_NAME, description=_ASSET_GROUP_DESCRIPTION)
    except AssetLibraryRequestError as exc:
        # Asset groups are containers, not submitted assets; group failures prove ingest has not occurred.
        await _fail_before_submit(session_factory, link.id, now, exc.code or f"request_{exc.kind}", exc.message)
        return
    raise_if_cancel_absorbed()

    # Before CreateAsset, verify tasks remain selected and unprocessed; later process interruption means uncertain outcome.
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
        values: dict[str, Any] = {
            "error_code": (exc.code or f"request_{exc.kind}")[:128],
            "error_message": _clip(exc.message),
        }
        if exc.outcome_unknown:
            values |= {"state": "uncertain", "next_check_at": now + CHECK_INITIAL_DELAY, "check_attempts": 0}
        else:
            values |= {"state": "failed", "settled_at": now, "next_check_at": None}
        await run_noninterruptible_async(
            _update(session_factory, link.id, ProviderAssetLink.state == "submitting", **values)
        )
        return
    # Providers accepted: persist asset IDs without interruption, or restart can only recover uncertain outcomes.
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


async def _hold_formal_object(
    session_factory: async_sessionmaker[AsyncSession],
    link: _LinkSnapshot,
    content_version: ContentVersion,
    now: datetime,
) -> bool:
    """Before signing formal objects, add ingest-holder references and reset retention start to this signing; return continued availability."""
    assert content_version.stored is not None
    holder = ingest_holder(link.id)
    if not await add_reference(content_version.stored, holder, link.content_version_id, session_factory):
        return False
    async with session_factory() as session:
        await session.execute(
            update(FormalObjectRef).where(FormalObjectRef.project_name == holder).values(updated_at=now)
        )
        await session.commit()
    return True


async def release_ingest_holds(session_factory: async_sessionmaker[AsyncSession], *, now: datetime) -> int:
    """Release expired ingest-holder references, collect newly unreferenced formal objects, and return deletion count.

    Match relay retention (:func:`~lib.object_storage.relay.signed_content_retained_until`), measured
    from latest signing: at least one hour after resolution; unresolved tasks (processing, uncertain,
    unlinked, unavailable accounts) or missing task records retain until URL expiry plus one hour.
    Restarts do not affect persisted references/signing times.
    """
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(FormalObjectRef.project_name, FormalObjectRef.updated_at).where(
                    FormalObjectRef.project_name.startswith(INGEST_HOLDER_PREFIX),
                    FormalObjectRef.state == REF_ACTIVE,
                )
            )
        ).all()
        signed: dict[str, datetime] = {}
        for holder, held_at in rows:
            held_at = _aware(held_at) or now
            signed[holder] = max(signed.get(holder, held_at), held_at)
        expired: list[str] = []
        for holder, signed_at in signed.items():
            link_id = holder.removeprefix(INGEST_HOLDER_PREFIX)
            link = await session.get(ProviderAssetLink, int(link_id)) if link_id.isdigit() else None
            if signed_content_retained_until(signed_at, signed_at + RELAY_URL_TTL, link) <= now:
                expired.append(holder)
    deleted = 0
    for holder in expired:
        deleted += await release_holder(holder, session_factory)
    return deleted


def _schedule_after(link: _LinkSnapshot, now: datetime, window: timedelta) -> datetime | None:
    """Next automatic query time; no more automatic queries after the window."""
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
        detail = await get_asset(credentials_of(account), link.provider_asset_id)
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
    """Uncertain submission: recover by unique submission name within groups; found assets become processing, never recreated."""
    async with session_factory() as session:
        account = await session.get(AssetLibraryAccount, link.account_id)
    if not _account_matches(account, link) or not link.provider_group_id:
        await _update(session_factory, link.id, check_error="account_unavailable", next_check_at=None)
        return
    assert account is not None
    try:
        found = await find_assets_by_name(
            credentials_of(account), group_id=link.provider_group_id, name=link.submit_name
        )
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
    """At startup, interrupted submissions become uncertain: providers may have accepted CreateAsset, so never resubmit."""
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
    """Advance due ingest tasks individually, then clean expired relays; one task’s failure leaves others unaffected."""
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
    try:
        await release_ingest_holds(session_factory, now=now)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("撤下到期的入库任务引用失败，等下一轮", exc_info=True)


class AssetIngestRunner:
    """Loop :func:`run_once` within application lifetime."""

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
    """Start ingest background loops: recover interrupted submissions first, then process rounds periodically or when woken."""
    runner = AssetIngestRunner(session_factory)
    runner.start()
    _runner[0] = runner
    return runner


async def stop_asset_ingest(runner: AssetIngestRunner) -> None:
    await runner.stop()
    if _runner[0] is runner:
        _runner[0] = None


def wake_asset_ingest() -> None:
    """Wake background loops early for new tasks/immediate queries; do nothing if no loop runs."""
    runner = _runner[0]
    if runner is not None:
        runner.wake()


__all__ = [
    "ASSET_GROUP_NAME",
    "CHECK_INITIAL_DELAY",
    "INGEST_HOLDER_PREFIX",
    "POLL_WINDOW",
    "AssetIngestRunner",
    "ingest_holder",
    "recover_interrupted",
    "release_ingest_holds",
    "run_once",
    "start_asset_ingest",
    "stop_asset_ingest",
    "wake_asset_ingest",
]
