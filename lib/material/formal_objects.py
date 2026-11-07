"""Formal commits and formal objects in object storage (``docs/adr/0116``).

**Formal commit entry**: :func:`formal_commit` wraps existing formal-write orchestration (formal
files, version records, artifact manifests), selecting local/object storage for newly written
content versions by global settings. Remote saves occur before version-record writes
(:class:`ObjectContentStorage.persist`); return durable locators only after upload and HEAD
confirmation. Failures preserve existing rollback behavior, leaving old versions/manifests/formal
files unchanged. Only image resources use object storage; videos/audio stay local.

**Objects/ownership**: digest-named keys under :data:`FORMAL_PREFIX` stay separate from relay
copies; equal bytes share objects within connections. Register ``object_storage_formal_object``
before upload; ``object_storage_formal_object_ref`` records in-progress commit claims
(``pending``) and version-history content use (``active``). Rules:

- Retain objects while any reference remains;
- Failure compensation deletes only objects created by this commit and confirmed unreferenced after settlement; never delete reused objects;
- Preserve failed-deletion locators/reasons (``last_error``); :func:`sweep_formal_objects` retries with backoff;
- Settlement (:meth:`ObjectContentStorage.settle`) judges references from current version history/recycle bins under project/version locks, not whether commits raised;
  if processes exit mid-commit, :func:`recover_formal_commits` settles at next startup.

**Retention contract** (``docs/adr/0118``): references belong to holders, either projects (project
names, changed to tombstone names after whole deletion) or nonproject consumers (colon-bearing
keys). Retain objects while referenced; only :func:`collect_unreferenced_objects` transitions
fully unreferenced objects to pending deletion and deletes them. Each lifecycle maintains only
its own references:

- Projects: version-history content is ``active``, recoverable recycle-bin content ``trashed``;
  :func:`reconcile_project_references` recomputes from current state. Call :func:`reconcile_and_collect`
  after permanent deletion, clearing, or expiry; :func:`release_holder` after permanently deleting
  deleted projects. Maintenance (:func:`retain_formal_objects`) periodically reconciles all projects as fallback.
- Cross-project consumers call :func:`add_reference` before use, :func:`remove_reference` after use, and :func:`release_holder` when disappearing entirely.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import uuid
from collections.abc import AsyncGenerator, Callable, Collection, Generator, Iterable
from contextlib import AbstractContextManager, ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import exists, func, select
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

#: Formal-object key prefix, separate from relay copies (``arcreel-relay/``).
FORMAL_PREFIX = "arcreel-materials/"
#: Global setting for new-content storage, :data:`NewContentTarget`, default local. Settings edits through object-storage routes;
#: ``object_storage`` requires a configured connection.
MATERIAL_STORAGE_TARGET_SETTING = "material_storage_target"
NewContentTarget = Literal["local", "object_storage"]
#: Readable/connection-holding object states: these prevent bucket/Endpoint/region changes and deletion. Pending deletions
#: hold connections only while referenced; unreferenced objects are never reread and detach on changes (:func:`detach_orphaned_objects`).
HOLDING_OBJECT_STATES = frozenset({"uploading", "stored"})
#: Detached pending-object reason: bucket/Endpoint changed or connection deleted; never retry deletion against current connections.
CONNECTION_CHANGED = "connection_changed"

_RETRY_BASE = timedelta(minutes=5)
_RETRY_CAP = timedelta(hours=6)
_IMAGE_EXTENSION = ".png"


class RemoteSaveFailedError(BadGatewayError):
    """Object storage did not confirm persistence; this formal commit publishes no new version."""

    def __init__(self, reason: str) -> None:
        super().__init__("material_remote_save_failed", reason=reason)


class ObjectStorageRequiredError(ConflictError):
    def __init__(self) -> None:
        super().__init__("material_storage_object_storage_missing")


class ProjectRetiringError(ConflictError):
    """Project is being deleted entirely or replaced by overwrite import; this commit no longer saves new images as formal objects."""

    def __init__(self) -> None:
        super().__init__("material_project_retiring")


#: Retiring project directories (real paths → active retirement counts), deleted entirely or replaced by overwrite import. Remote versions
#: published between reference backfill and directory moves would lack tombstone holders, so saves fail before upload throughout retirement.
_RETIRING_PROJECTS: dict[str, int] = {}
_RETIRING_GUARD = threading.Lock()


def _retiring_key(project_path: Path) -> str:
    return os.path.realpath(project_path)


def project_is_retiring(project_path: Path) -> bool:
    """Whether ``project_path`` is currently retiring."""
    with _RETIRING_GUARD:
        return _RETIRING_PROJECTS.get(_retiring_key(project_path), 0) > 0


def formal_object_key(digest: str, suffix: str) -> str:
    """Formal-object key named by content digest, without project/resource names."""
    return f"{FORMAL_PREFIX}sha256/{digest[:2]}/{digest}{suffix.lower()}"


def stores_remotely(resource_type: str) -> bool:
    """Whether this resource type’s new content versions follow global object-storage settings (images only)."""
    try:
        return resource_extension(resource_type) == _IMAGE_EXTENSION
    except ValueError:
        return False


async def new_content_target(session: AsyncSession) -> NewContentTarget:
    value = await SystemSettingRepository(session).get(MATERIAL_STORAGE_TARGET_SETTING, "local")
    return "object_storage" if value == "object_storage" else "local"


async def set_new_content_target(session: AsyncSession, target: NewContentTarget) -> None:
    """Change new-content save location without committing; affect future formal commits only, reading existing versions through their own locators."""
    await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, target)


def _default_session_factory() -> async_sessionmaker[AsyncSession]:
    from lib.db import async_session_factory

    return async_session_factory


def _retry_delay(attempts: int) -> timedelta:
    return min(_RETRY_BASE * (2 ** min(max(attempts - 1, 0), 16)), _RETRY_CAP)


_KEY_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
#: This process’s ongoing formal commits not yet settled (single process, ADR 0007).
_LIVE_ATTEMPTS: set[str] = set()
_CONNECTION_LOCKS: dict[int, asyncio.Lock] = {}


def _key_lock(key: str) -> asyncio.Lock:
    """Serialize same-object registration/reuse/deletion in single-process deployments (ADR 0007)."""
    loop_key = (id(asyncio.get_running_loop()), key)
    lock = _KEY_LOCKS.get(loop_key)
    if lock is None:
        lock = _KEY_LOCKS[loop_key] = asyncio.Lock()
    return lock


def connection_change_lock() -> asyncio.Lock:
    """Shared serialization boundary for connection changes/formal-object registration (single process, ADR 0007).

    Bucket/Endpoint/region changes or deletion verify no holding formal objects, detach pending
    deletion objects, and commit under this lock. Formal-object registration also holds it,
    rechecking the connection captured at commit start. Reference-based resurrection holds it
    and resurrects only objects still on the current connection. Lock order: connection before object key.
    """
    loop_key = id(asyncio.get_running_loop())
    lock = _CONNECTION_LOCKS.get(loop_key)
    if lock is None:
        lock = _CONNECTION_LOCKS[loop_key] = asyncio.Lock()
    return lock


@dataclass(frozen=True)
class _ConnectionSnapshot:
    id: int
    kind: str
    region: str
    endpoint: str
    bucket: str
    client: ObjectStorageClient

    @classmethod
    def of(cls, connection: ObjectStorageConnection) -> _ConnectionSnapshot:
        return cls(
            id=connection.id,
            kind=connection.kind,
            region=connection.region,
            endpoint=connection.endpoint,
            bucket=connection.bucket,
            client=ObjectStorageClient(config_from_connection(connection)),
        )

    def is_current(self, connection: ObjectStorageConnection | None) -> bool:
        """``connection`` still matches commit-start capture: no bucket/Endpoint/region/type changes or deletion/recreation."""
        return connection is not None and (
            connection.id,
            connection.kind,
            connection.region,
            connection.endpoint,
            connection.bucket,
        ) == (self.id, self.kind, self.region, self.endpoint, self.bucket)


def same_connection(row: FormalObject, connection: ObjectStorageConnection | None) -> bool:
    return (
        connection is not None
        and connection.id == row.connection_id
        and connection.endpoint == row.endpoint
        and connection.bucket == row.bucket
    )


class ObjectContentStorage:
    """Persist newly written image content versions from one formal commit into object storage.

    Version-manager workers call :meth:`persist`; callers writing records outside version managers
    (archive import) call :meth:`store` directly. Both register objects/references on the event loop
    through :class:`EventLoopBridge` and upload synchronously in the current thread. Invoke
    :meth:`settle` once on the event loop after commit completion, successful or otherwise.
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
        # Register before persisting claims: startup recovery skips claims belonging to this process’s registered ongoing commits.
        _LIVE_ATTEMPTS.add(self.attempt_id)

    # ---- Worker-thread side ------------------------------------------------------------------

    def persist(self, project_path: Path, resource_type: str, source: Path, slot: LocalLocator) -> PersistedContent:
        # Verify under version locks: retirement registers first, then reads under locks; remote versions published afterward cannot reach here.
        if stores_remotely(resource_type) and project_is_retiring(project_path):
            raise ProjectRetiringError()
        local = LocalMaterialStorage(project_path)
        # Write a working version snapshot first, deriving both digest and uploaded bytes from it so recorded digests match remote content.
        digest = local.persist(source, slot)
        if not stores_remotely(resource_type):
            return PersistedContent(digest)
        self.track(project_path)
        return PersistedContent(digest, self.store(Path(project_path) / slot.path, digest))

    def track(self, project_path: Path) -> None:
        """At settlement, judge object references from ``project_path`` history; skip if the path no longer exists."""
        self._project_paths.add(Path(project_path))

    def store(self, work_file: Path, digest: str) -> ObjectLocator:
        """Save an existing local image with ``digest`` as a formal object; return its durable locator after confirmation.

        Do not register projects: callers write referencing version records, then :meth:`track`
        identifies settlement projects. Archive imports use this to save staged images into the
        target instance before installing project directories.
        """
        data = work_file.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise RemoteSaveFailedError("content_changed")
        suffix = work_file.suffix
        key = formal_object_key(digest, suffix)
        needs_upload = self.bridge.run(self._begin(key, digest, len(data)))
        client = self.connection.client
        try:
            # Verify reused saved objects still exist in buckets; console-deleted objects upload again as new content.
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

    # ---- Event-loop side ---------------------------------------------------------------------

    async def _begin(self, key: str, digest: str, size: int) -> bool:
        """Register objects/commit claims and return whether upload is needed; reuse confirmed saved objects without uploading.

        Recheck captured connection under its lock; stale commits fail before upload. Registration
        creates a formal object holding the connection, so later connection changes are rejected.
        """
        async with connection_change_lock(), _key_lock(key), self.session_factory() as session:
            if not self.connection.is_current(await ObjectStorageConnectionRepository(session).get()):
                raise RemoteSaveFailedError(CONNECTION_CHANGED)
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
                # For pending-deletion/deleted/unconfirmed-other-upload objects, this commit reuploads and owns compensation. After Endpoint changes,
                # record the current Endpoint where objects are written; subsequent deletion/connection protection follows it.
                row.state = "uploading"
                row.endpoint = self.connection.endpoint
                row.delete_attempts = 0
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
        """Upload is confirmed in the bucket: mark the object saved.

        Confirmation concerns the object, not just its recorded responsible commit: another same-
        content commit may take over failure compensation during upload. If its later upload fails,
        confirmed objects remain saved, usable for ingest/retrieval, and collectible after references vanish.
        """
        async with self.session_factory() as session:
            row = await _object_row(session, self.connection.id, self.connection.bucket, key)
            if row is not None and row.state == "uploading":
                row.state = "stored"
                await session.commit()

    async def finish(self) -> None:
        """Settle once on commit completion; failures only log for next-startup recovery, never masking commit outcomes."""
        try:
            await run_noninterruptible_async(self.settle())
        except Exception:
            logger.warning("正式提交结算失败，下次启动时补做 attempt=%s", self.attempt_id, exc_info=True)
        finally:
            _LIVE_ATTEMPTS.discard(self.attempt_id)

    async def settle(self) -> None:
        """Settle against current project state: history/recycle objects become project-held references; remove other claims, then compensate deletions."""
        await _settle_attempt(self.session_factory, self.attempt_id, self._project_paths, connection=self.connection)


def _present(client: ObjectStorageClient, key: str, size: int) -> bool:
    try:
        return client.head_sync(key).size == size
    except ObjectStorageError as exc:
        if exc.kind == "not_found":
            return False
        raise


def iter_version_records(data: object) -> Iterable[tuple[str, str, dict[str, Any]]]:
    """Iterate every version record in versions.json content."""
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
    project_paths: Iterable[Path],
    *,
    connection: _ConnectionSnapshot | None,
) -> None:
    """Settle one formal commit: read version history/recycle bins under ``project_paths`` project/version locks and
    convert claims into project-held references within those locks, preventing intervening recycle-bin moves.
    Reclaim used/new objects once unreferenced: unconfirmed saves follow failure compensation, confirmed
    saves unified collection, independent of which commit created them.

    Skip absent directories (deleted/replaced projects). Unreadable recycle records raise, preserving
    claims for next-startup recovery.
    """
    paths = sorted({Path(path) for path in project_paths})
    bridge = EventLoopBridge.capture()

    def _locked() -> list[int]:
        with ExitStack() as stack:
            uses: _Uses = {}
            for project_path in paths:
                data = _enter_project(stack, project_path)
                if data is None:
                    continue
                _add_uses(uses, data, REF_ACTIVE)
                _trash_uses(project_path, uses)
            return bridge.run(_resolve_pending(session_factory, attempt_id, uses))

    candidates = await run_sync_transaction(_locked)
    # Only settlement handles unconfirmed uploads: after removing the last claim, no commit can still confirm it.
    await _collect_unreferenced(
        session_factory, candidates, frozenset({"uploading", "stored"}), connection.client if connection else None
    )


def _enter_project(stack: ExitStack, project_path: Path) -> dict[str, Any] | None:
    """Acquire project/version locks in ``stack`` and return history, or ``None`` if the directory vanished/project was deleted during execution."""
    from lib.artifacts.formal_write import project_metadata_lock
    from lib.artifacts.version_manager import VersionManager
    from lib.project.task_project_claim import ProjectDeletedDuringTaskError

    if not project_path.is_dir():
        return None
    try:
        stack.enter_context(project_metadata_lock(project_path))
    except (FileNotFoundError, ProjectDeletedDuringTaskError):
        return None
    return stack.enter_context(VersionManager(project_path).locked_history())


async def _resolve_pending(
    session_factory: async_sessionmaker[AsyncSession], attempt_id: str, uses: _Uses
) -> list[int]:
    """Convert this commit’s claims to holder references from ``uses`` under the claim’s holder, then remove claims; return object IDs needing compensation review."""
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
            held = uses.get((row.connection_id, row.bucket, row.object_key), set()) if row else set()
            for content_version_id, state in held:
                await _ensure_ref(session, ref.object_id, ref.project_name, content_version_id, state)
            await session.delete(ref)
        await session.commit()
        # Recheck every used/new object: concurrent same-content commits may see others’ claims during early settlement; collect when the last claim is removed.
        return list(objects)


async def _ensure_ref(
    session: AsyncSession, object_id: int, holder: str, content_version_id: str, state: str = "active"
) -> None:
    stmt = select(FormalObjectRef.id).where(
        FormalObjectRef.object_id == object_id,
        FormalObjectRef.project_name == holder,
        FormalObjectRef.content_version_id == content_version_id,
        FormalObjectRef.state == state,
    )
    if (await session.execute(stmt)).first() is None:
        session.add(
            FormalObjectRef(
                object_id=object_id, project_name=holder, content_version_id=content_version_id, state=state
            )
        )
        await session.flush()


async def _collect_unreferenced(
    session_factory: async_sessionmaker[AsyncSession],
    object_ids: Iterable[int],
    states: frozenset[str],
    client: ObjectStorageClient | None,
) -> int:
    """Sole unified-collection implementation: unreferenced ``object_ids`` in ``states`` become pending deletion and are
    deleted immediately where possible; return deletion count. Retain all referenced objects, including active commit claims."""
    deleted = 0
    for object_id in sorted(set(object_ids)):
        async with session_factory() as session:
            row = await session.get(FormalObject, object_id)
            if row is None:
                continue
            key = row.object_key
        async with _key_lock(key), session_factory() as session:
            row = await session.get(FormalObject, object_id)
            if row is None or row.state not in states or await _ref_count(session, object_id):
                continue
            row.state = "orphaned"
            row.next_attempt_at = utc_now()
            await session.commit()
            current = await ObjectStorageConnectionRepository(session).get()
            deleted += await _delete_orphan(session, row, current, client)
            await session.commit()
    return deleted


async def _delete_orphan(
    session: AsyncSession,
    row: FormalObject,
    current: ObjectStorageConnection | None,
    client: ObjectStorageClient | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Delete one pending object; callers hold its key lock and commit. Return whether deletion succeeded."""
    now = now or utc_now()
    row.delete_attempts += 1
    if not same_connection(row, current):
        row.last_error = CONNECTION_CHANGED
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
    """Retry due pending deletions and return this round’s count; recheck references first, restoring referenced objects to saved."""
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
            current = await ObjectStorageConnectionRepository(session).get()
            if not await _ref_count(session, object_id):
                deleted += await _delete_orphan(session, row, current, None, now=now)
            elif same_connection(row, current):
                row.state = "stored"
                row.next_attempt_at = None
            else:
                # Referenced but detached objects are neither deleted nor treated as saved on the current connection.
                row.last_error = CONNECTION_CHANGED
                row.next_attempt_at = now + _RETRY_CAP
            await session.commit()
    return deleted


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def recover_formal_commits(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    project_path_of: Callable[[str], Path] | None = None,
    running: Collection[str] | None = None,
) -> int:
    """Settle previous-process unfinished commits at startup; return settlement count. Startup-only, single-process deployment.

    Recovery runs in background after requests are accepted. Skip ``running`` commits (defaulting
    to this process’s active commits): they are not interrupted and settle themselves on completion.
    """
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
        # Read ongoing commits after claims; any read claim belonging to an ongoing commit was registered before persistence and must appear here.
        for attempt_id in set(_LIVE_ATTEMPTS if running is None else running):
            attempts.pop(attempt_id, None)
    for attempt_id, project_name in attempts.items():
        paths = await asyncio.to_thread(_project_paths_of, project_path_of, project_name) if project_name else []
        await _settle_attempt(session_factory, attempt_id, paths, connection=None)
    return len(attempts)


def _project_paths_of(project_path_of: Callable[[str], Path], project_name: str) -> list[Path]:
    """Holder project directory, or empty for nonprojects (colon-bearing keys) or absent projects."""
    try:
        return [project_path_of(project_name)]
    except (FileNotFoundError, ValueError):
        return []


def _default_project_path(project_name: str) -> Path:
    from lib.project.project_manager import get_project_manager

    return get_project_manager().get_project_path(project_name)


# ---- Retention contract: holders and unified collection (``docs/adr/0118``) ----------------

#: Reference states: ``pending`` active formal-commit claims; ``active`` holder use (project history content
#: or registered cross-project consumer use); ``trashed`` recoverable recycle-bin records containing this version.
REF_PENDING = "pending"
REF_ACTIVE = "active"
REF_TRASHED = "trashed"
_HELD_STATES = frozenset({REF_ACTIVE, REF_TRASHED})

_VERSIONS_FILE = "versions/versions.json"
#: Holder uses: object locator (connection ID, bucket, key) → {(content-version identity, reference state)}.
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
    """Recoverable remote content versions in project recycle bins: deleted historical versions and history within structural-deletion snapshots.

    Structural snapshots include whole pre-deletion histories, including versions still in projects
    afterward. Overcounting only retains objects until records clear; never undercounts. Unreadable
    snapshots raise so callers abandon reconciliation without removing references.
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
    """Replace ``holder`` references in ``states`` with exactly ``uses``; return removed-reference object IDs without deleting objects.

    Do not create references for unregistered objects in ``uses`` (e.g. saved by another instance).
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
    """Reconcile project-held references from current version history/recycle bin; return removed-reference IDs without deleting objects.

    Remote history content becomes ``active``; recoverable recycle content ``trashed``. Read/update
    under project/version locks, preventing interleaving with delete/restore/formal commit and
    reference removal midway through history-to-recycle moves. Missing directories raise without
    removing references. Collect fully unreferenced objects through :func:`collect_unreferenced_objects`
    or call :func:`reconcile_and_collect` directly.
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


@asynccontextmanager
async def retiring_project_references(
    session: AsyncSession, project_name: str, project_path: Path
) -> AsyncGenerator[None]:
    """During whole-project deletion/overwrite-import retirement, fill current actual references in retirement ``session`` without committing;
    new remote saves fail before upload with :class:`ProjectRetiringError` throughout, clearing the guard on exit.

    Register retirement before reading history/recycle under project/version locks: earlier published
    versions appear in the read state, later publications fail, so backfilled references cover all
    tombstoned remote versions. Add missing ``active`` / ``trashed`` references under project names,
    removing none; callers rekey them to tombstone names in the same transaction. Published but
    unsettled commits have only claims, retained under project names for later settlement (overwrite
    import new-image claims thereby attach to the new project). Backfilled references retain old
    project objects with tombstones. Skip absent directories; unreadable recycle records backfill
    from version history alone without blocking retirement.
    """
    bridge = EventLoopBridge.capture()
    project_path = Path(project_path)
    key = _retiring_key(project_path)

    def _locked() -> None:
        with ExitStack() as stack:
            data = _enter_project(stack, project_path)
            if data is None:
                return
            uses: _Uses = {}
            _add_uses(uses, data, REF_ACTIVE)
            try:
                _trash_uses(project_path, uses)
            except Exception:
                logger.warning(
                    "收尾项目时读不出回收站记录，只按版本历史补登引用 project=%s", project_name, exc_info=True
                )
            bridge.run(_add_holder_uses(session, project_name, uses))

    with _RETIRING_GUARD:
        _RETIRING_PROJECTS[key] = _RETIRING_PROJECTS.get(key, 0) + 1
    try:
        await run_sync_transaction(_locked)
        yield
    finally:
        with _RETIRING_GUARD:
            remaining = _RETIRING_PROJECTS.get(key, 0) - 1
            if remaining > 0:
                _RETIRING_PROJECTS[key] = remaining
            else:
                _RETIRING_PROJECTS.pop(key, None)


async def _add_holder_uses(session: AsyncSession, holder: str, uses: _Uses) -> None:
    if not uses:
        return
    rows = (
        await session.execute(
            select(FormalObject.id, FormalObject.connection_id, FormalObject.bucket, FormalObject.object_key).where(
                FormalObject.object_key.in_({key for _cid, _bucket, key in uses})
            )
        )
    ).all()
    for object_id, connection_id, bucket, key in rows:
        for content_version_id, state in uses.get((connection_id, bucket, key), ()):
            await _ensure_ref(session, object_id, holder, content_version_id, state)


async def collect_unreferenced_objects(
    object_ids: Iterable[int], session_factory: async_sessionmaker[AsyncSession] | None = None
) -> int:
    """Unified collection: saved ``object_ids`` with no references become pending deletion and are deleted immediately where possible; return count.

    Retain anything referenced, including active commit claims. Record deletion failures for
    :func:`sweep_formal_objects` backoff; changed buckets record ``connection_changed`` without deletion.
    Unconfirmed uploads belong to their formal commit’s compensation, untouched here.
    """
    session_factory = session_factory or _default_session_factory()
    return await _collect_unreferenced(session_factory, object_ids, frozenset({"stored"}), None)


async def reconcile_and_collect(
    project_name: str,
    project_path: Path,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Reconcile references (:func:`reconcile_project_references`), then collect newly unreferenced objects; return deletion count.

    Call after permanent project-recycle deletion, clearing, or expiry cleanup.
    """
    released = await reconcile_project_references(project_name, project_path, session_factory)
    return await collect_unreferenced_objects(released, session_factory)


async def add_reference(
    locator: ObjectLocator,
    project_name: str,
    content_version_id: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> bool:
    """Register ``project_name`` holder content as using ``locator`` objects; return whether objects are registered and undeleted.

    Cross-project reuse calls before writing target version records: references prevent compensation
    deletion/collection. Projects pass project names; nonproject consumers such as global libraries
    use colon-bearing keys (``asset-library:<id>``), avoiding project/tombstone names and project reconciliation.
    """
    session_factory = session_factory or _default_session_factory()
    # Serialize with connection changes: detached pending objects cannot resurrect as saved; resurrected objects immediately hold connections.
    async with connection_change_lock(), _key_lock(locator.key), session_factory() as session:
        row = await _object_row(session, locator.connection_id, locator.bucket, locator.key)
        if row is None or row.state not in {"stored", "orphaned"}:
            return False
        if row.state == "orphaned" and not same_connection(row, await ObjectStorageConnectionRepository(session).get()):
            return False
        row.state = "stored"
        row.next_attempt_at = None
        await _ensure_ref(session, row.id, project_name, content_version_id)
        await session.commit()
        return True


async def remove_reference(
    locator: ObjectLocator,
    holder: str,
    content_version_id: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Remove ``holder`` content’s ``active`` reference to ``locator``, collect newly unreferenced objects, and return deletion count."""
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
    """When ``holder`` disappears (e.g. permanent deletion of deleted projects), release all its references and collect unreferenced objects; return count.

    Leave active commit claims for their own settlement.
    """
    session_factory = session_factory or _default_session_factory()
    released = await _replace_holder_uses(session_factory, holder, {}, _HELD_STATES)
    return await collect_unreferenced_objects(released, session_factory)


#: After deleted-project retention plus this interval, with directories absent, maintenance releases tombstone references as fallback for failed explicit permanent-deletion release.
_TOMBSTONE_GRACE = timedelta(days=1)


async def retain_formal_objects(
    projects_dir: Path,
    deleted_projects_dir: Path | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Maintenance retention correction: reconcile/collect each project, then release references for long-permanently-deleted projects; return count.

    Individual project failures only log, leaving others unaffected.
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


def _has_ref() -> Any:
    return exists().where(FormalObjectRef.object_id == FormalObject.id)


async def holding_object_count(session: AsyncSession, connection_id: int) -> int:
    """Formal objects still holding this connection: unconfirmed uploads (active commits), saved objects, and referenced pending deletions.

    Exclude fully unreferenced objects awaiting compensation: never read again, they do not block
    connection changes. Callers hold :func:`connection_change_lock`.
    """
    stmt = (
        select(func.count())
        .select_from(FormalObject)
        .where(
            FormalObject.connection_id == connection_id,
            FormalObject.state.in_(HOLDING_OBJECT_STATES) | ((FormalObject.state == "orphaned") & _has_ref()),
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def detach_orphaned_objects(
    session: AsyncSession,
    connection: ObjectStorageConnection,
    *,
    endpoint: str | None = None,
    bucket: str | None = None,
    now: datetime | None = None,
) -> int:
    """Before changing to ``endpoint`` / ``bucket`` (both ``None`` means deletion), detach unreferenced pending-deletion objects:
    retain locators, record :data:`CONNECTION_CHANGED`, and never retry against the changed connection. Return count without committing.

    Region-only changes retain Endpoint/bucket and original deletion locations, so do not detach.
    Callers hold :func:`connection_change_lock` and already verified no holding objects through
    :func:`holding_object_count`.
    """
    if endpoint is not None and bucket is not None and (endpoint, bucket) == (connection.endpoint, connection.bucket):
        return 0
    now = now or utc_now()
    rows = (
        await session.execute(
            select(FormalObject).where(
                FormalObject.connection_id == connection.id,
                FormalObject.state == "orphaned",
                ~_has_ref(),
            )
        )
    ).scalars()
    detached = 0
    for row in rows:
        if endpoint is not None and (row.endpoint, row.bucket) == (endpoint, bucket):
            continue
        row.last_error = CONNECTION_CHANGED
        row.next_attempt_at = now + _RETRY_CAP
        detached += 1
    return detached


async def count_unreleased_objects(session: AsyncSession) -> int:
    """Undeleted, fully unreferenced formal-object count, including detached objects. After bucket/Endpoint changes or connection deletion,
    these no longer auto-delete; objects actually written into buckets require object-storage console cleanup."""
    stmt = select(func.count()).select_from(FormalObject).where(FormalObject.state == "orphaned", ~_has_ref())
    return int((await session.execute(stmt)).scalar_one())


async def stored_object_keys(session: AsyncSession, locators: Iterable[ObjectLocator]) -> set[ObjectLocator]:
    """``locators`` whose remote persistence was confirmed; only these allow local working-file cleanup."""
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
    """Registered object byte sizes among ``locators``, regardless of state and without remote access; omit unregistered locations."""
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
    """Formal commit entry: new image versions written inside this context save according to ``target`` (global setting by default).

    Local storage changes no behavior and yields ``None``. Object storage verifies connection
    existence on entry, yielding this commit’s :class:`ObjectContentStorage`; settle once on exit,
    including failure/cancellation. Settlement failures only log, never masking commit results.
    Synchronous formal writes inside must enter worker threads through context-variable-copying
    mechanisms such as ``asyncio.to_thread``.

    Raises:
        ObjectStorageRequiredError: Object-storage target with no configured instance connection.
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
    """Formal commit for synchronous worker flows: enter/exit ``with`` on the ``bridge`` event loop while its body stays in the calling thread.

    Like :func:`formal_commit`, choose location globally and settle on exit, but do not set new-
    content context variables. Explicitly save through yielded :meth:`ObjectContentStorage.store`,
    then :meth:`ObjectContentStorage.track` identifies settlement projects. Archive imports use
    this after resolving target project names.
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
    "CONNECTION_CHANGED",
    "FORMAL_PREFIX",
    "HOLDING_OBJECT_STATES",
    "MATERIAL_STORAGE_TARGET_SETTING",
    "REF_ACTIVE",
    "REF_PENDING",
    "REF_TRASHED",
    "NewContentTarget",
    "ObjectContentStorage",
    "ObjectStorageRequiredError",
    "ProjectRetiringError",
    "RemoteSaveFailedError",
    "add_reference",
    "collect_unreferenced_objects",
    "connection_change_lock",
    "count_unreleased_objects",
    "detach_orphaned_objects",
    "formal_commit",
    "formal_commit_on",
    "formal_object_key",
    "formal_object_sizes",
    "holding_object_count",
    "iter_version_records",
    "new_content_target",
    "project_is_retiring",
    "reconcile_and_collect",
    "reconcile_project_references",
    "recover_formal_commits",
    "release_holder",
    "remove_reference",
    "retain_formal_objects",
    "retiring_project_references",
    "same_connection",
    "set_new_content_target",
    "stored_object_keys",
    "stores_remotely",
    "sweep_formal_objects",
]
