"""Global-library images share project content-version storage contracts (``docs/adr/0120``).

Each asset/derivative image stores three facts on its entry (``lib.db.models.asset.GlobalImageColumns``):

- ``image_digest``: raw sha256 of library-stored files, verifying fetched remote content;
- ``image_content_id``: library content identity, randomly regenerated for every new image write;
- ``image_storage``: remote durable locator; when present, ``image_path`` is only a cleanable, refetchable local work file.

New images follow global local/object-storage settings via :func:`global_image_commit`, the same
formal commit entry. Global libraries are not projects and have no history. Each entry uses
:func:`library_holder` (``asset-library:<条目 ID>``) for two content-identity-keyed record types:

- Formal-object references follow ``docs/adr/0118``: ``add_reference`` before use,
  ``remove_reference`` for old images after replacements/overwrites, ``release_holder`` on entry
  deletion; the contract centrally collects zero-reference objects.
- Provider asset-link copies transfer from source content at save, then to newly created target
  content versions when applied to projects.

Colon-bearing holder keys cannot collide with project/tombstone names; project-name-based
reconciliation, rekeying, and settlement readers never touch them.

Existing entries with all three columns empty use local library files, previewing/applying normally.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import tempfile
import time
import uuid
from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.models.asset import Asset, AssetDerivative
from lib.infra.async_thread import run_noninterruptible_async
from lib.infra.content_digest import sha256_file
from lib.infra.data_root_layout import GLOBAL_ASSETS_DIRNAME
from lib.material.formal_objects import (
    ObjectContentStorage,
    RemoteSaveFailedError,
    add_reference,
    formal_commit,
    release_holder,
    remove_reference,
    stored_object_keys,
    stores_remotely,
)
from lib.material.storage import LocalLocator, LocalMaterialStorage, ObjectLocator, new_content_version_id
from lib.material.work_files import (
    DEFAULT_MIN_IDLE,
    CacheCleanupResult,
    MaterialFetchFailedError,
    evict_idle_work_file,
    fetch_object_content,
    in_use_or_recent,
)
from lib.project.asset_types import ASSET_SPECS

logger = logging.getLogger(__name__)

#: Library-holder prefix for formal-object references/provider links, following ``docs/adr/0118`` nonproject key format.
LIBRARY_HOLDER_PREFIX = "asset-library:"


def library_holder(asset_id: str) -> str:
    """Library-holder key, written into ``project_name`` for both ``object_storage_formal_object_ref`` and ``provider_asset_link``."""
    return f"{LIBRARY_HOLDER_PREFIX}{asset_id}"


_ENTRY_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def library_entry_lock(asset_id: str) -> asyncio.Lock:
    """Serialize same-entry replacement/overwrite/deletion (single process, ADR 0007).

    Each write locks, reads current images, commits new images, then releases those actually replaced.
    Concurrent replacements therefore release their own displaced images rather than both releasing
    the same old image and leaking a reference to an image no longer on the entry.
    """
    key = (id(asyncio.get_running_loop()), asset_id)
    lock = _ENTRY_LOCKS.get(key)
    if lock is None:
        lock = _ENTRY_LOCKS[key] = asyncio.Lock()
    return lock


@dataclass(frozen=True)
class GlobalImage:
    """Content facts for one library image."""

    path: str
    digest: str | None = None
    content_id: str | None = None
    stored: ObjectLocator | None = None

    @classmethod
    def of(cls, row: Asset | AssetDerivative) -> GlobalImage | None:
        if not row.image_path:
            return None
        return cls(
            path=row.image_path,
            digest=row.image_digest,
            content_id=row.image_content_id,
            stored=ObjectLocator.from_record(row.image_storage),
        )

    def columns(self) -> dict[str, Any]:
        """Column values written into entries."""
        return {
            "image_path": self.path,
            "image_digest": self.digest,
            "image_content_id": self.content_id,
            "image_storage": self.stored.to_record() if self.stored else None,
        }


#: Column values for entries without images.
NO_IMAGE_COLUMNS: dict[str, Any] = {
    "image_path": None,
    "image_digest": None,
    "image_content_id": None,
    "image_storage": None,
}


def _resource_type(asset_type: str) -> str:
    """Version-history resource type for global assets, determining whether images follow global remote-save settings."""
    return ASSET_SPECS[asset_type].bucket_key


@dataclass
class GlobalImageWriter:
    """Images newly saved by this entry write; :meth:`save` performs synchronous disk/network operations in worker threads."""

    data_root: Path
    holder: str
    storage: ObjectContentStorage | None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    saved: list[GlobalImage] = field(default_factory=list)
    #: Library file written this time, relative to data root; :meth:`discard` removes it on failure.
    written: list[str] = field(default_factory=list)
    _committed: bool = field(default=False, init=False)

    def save(self, source: Path, asset_type: str, default_ext: str) -> GlobalImage:
        """Save ``source`` as a new library image: write library-local files and, when globally selected, formal remote objects."""
        ext = source.suffix.lower() or default_ext
        rel_path = f"{GLOBAL_ASSETS_DIRNAME}/{asset_type}/{uuid.uuid4().hex}{ext}"
        self.written.append(rel_path)
        digest = LocalMaterialStorage(self.data_root).persist(source, LocalLocator(rel_path))
        stored = None
        if self.storage is not None and stores_remotely(_resource_type(asset_type)):
            stored = self.storage.store(self.data_root / rel_path, digest)
        image = GlobalImage(rel_path, digest, new_content_version_id(), stored)
        self.saved.append(image)
        return image

    def save_bytes(self, content: bytes, asset_type: str, ext: str) -> GlobalImage:
        """Save uploaded bytes as a new library image, with :meth:`save` semantics."""
        directory = self.data_root / GLOBAL_ASSETS_DIRNAME / asset_type
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".upload.", suffix=ext, dir=directory)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            return self.save(tmp, asset_type, ext)
        finally:
            tmp.unlink(missing_ok=True)  # hard-delete: temp -- Upload staging file; bytes saved to library

    async def hold(self) -> None:
        """Register entry references for images saved remotely in this write, before committing entries; references prevent settlement/collection deletion."""
        for image in self.saved:
            if (
                image.stored is not None
                and image.content_id is not None
                and not await add_reference(
                    image.stored, self.holder, image.content_id, session_factory=self.session_factory
                )
            ):
                raise RemoteSaveFailedError("not_confirmed")

    async def discard(self) -> None:
        """On failed entry writes, release references/files; post-commit response/read failures never revoke saved images."""
        if self._committed:
            return
        await forget_global_images(self.holder, self.saved, session_factory=self.session_factory)
        await asyncio.to_thread(_delete_files, self.data_root, self.written)

    async def commit(self, session: AsyncSession) -> None:
        """Commit entries and close compensation windows, preventing cancellation between DB commit and image revocability."""

        async def _commit() -> None:
            await session.commit()
            self._committed = True

        await run_noninterruptible_async(_commit())


@asynccontextmanager
async def global_image_commit(
    data_root: Path,
    holder: str,
    *,
    saves_images: bool = True,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[GlobalImageWriter]:
    """Formal entry ``holder`` image-write context, saving locally/remotely by global settings.

    Call :meth:`GlobalImageWriter.save` for images, :meth:`GlobalImageWriter.hold` for references,
    then :meth:`GlobalImageWriter.commit` for the entry. If entry writes fail, call
    :meth:`GlobalImageWriter.discard`. Settle on exit, deleting unreferenced newly created objects.
    With ``saves_images`` False (no images written), skip connection checks and treat as local.

    Raises:
        ObjectStorageRequiredError: Global target is object storage without a configured connection.
    """
    target = None if saves_images else "local"
    async with formal_commit(holder, session_factory=session_factory, target=target) as storage:
        yield GlobalImageWriter(Path(data_root), holder, storage, session_factory)


async def forget_global_images(
    holder: str,
    images: Iterable[GlobalImage | None],
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Entry ``holder`` stops using these images after replacements/overwrites: release each reference and centrally collect unreferenced objects."""
    for image in images:
        if image is not None and image.stored is not None and image.content_id is not None:
            await remove_reference(image.stored, holder, image.content_id, session_factory=session_factory)


async def release_library_entry(
    holder: str, *, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> None:
    """Library entry deleted: release all its references and centrally collect fully unreferenced objects."""
    await release_holder(holder, session_factory=session_factory)


def _delete_files(data_root: Path, rel_paths: Iterable[str]) -> None:
    for rel_path in rel_paths:
        try:
            (data_root / rel_path).unlink()  # hard-delete: out-of-scope -- Global-library file, outside projects
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("delete global asset file failed: %s", rel_path)


# ---- Read: fetch working files ----------------------------------------------------------

_FETCH_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}


def _fetch_lock(path: Path) -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), os.path.abspath(path))
    lock = _FETCH_LOCKS.get(key)
    if lock is None:
        lock = _FETCH_LOCKS[key] = asyncio.Lock()
    return lock


def _default_session_factory() -> async_sessionmaker[AsyncSession]:
    from lib.db import async_session_factory

    return async_session_factory


async def _remote_image(session_factory: async_sessionmaker[AsyncSession], rel_path: str) -> GlobalImage | None:
    async with session_factory() as session:
        for model in (Asset, AssetDerivative):
            stmt = select(model).where(model.image_path == rel_path, model.image_storage.is_not(None)).limit(1)
            row = (await session.execute(stmt)).scalars().first()
            if row is not None:
                return GlobalImage.of(row)
    return None


def _install(target: Path, digest: str | None, data: bytes) -> None:
    if digest is None or hashlib.sha256(data).hexdigest() != digest:
        raise MaterialFetchFailedError("content_mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=f"{target.suffix}.fetch", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if not target.exists():
            os.replace(tmp, target)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)  # hard-delete: temp -- Temporary working-file download


async def ensure_global_image(
    data_root: Path, rel_path: str, *, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> bool:
    """If ``rel_path`` is a remote library image’s missing work file, fetch/verify by digest; return whether fetched.

    Raises:
        MaterialConnectionUnavailableError: Recorded connection absent or changed buckets.
        MaterialFetchFailedError: Download failed or digest mismatched.
    """
    target = Path(data_root) / rel_path
    if await asyncio.to_thread(target.is_file):
        return False
    session_factory = session_factory or _default_session_factory()
    image = await _remote_image(session_factory, rel_path)
    if image is None or image.stored is None:
        return False
    async with _fetch_lock(target):
        if await asyncio.to_thread(target.is_file):
            return False
        data = await fetch_object_content(image.stored, session_factory=session_factory)
        await asyncio.to_thread(_install, target, image.digest, data)
    return True


# ---- Cleanup ---------------------------------------------------------------------------


def _evict(target: Path, digest: str, idle_before: float) -> tuple[int, int, int]:
    try:
        stat_result = target.stat()
    except OSError:
        return 0, 0, 0
    if in_use_or_recent(target, stat_result, idle_before):
        return 0, 0, 1
    if sha256_file(target) != digest:
        return 0, 0, 0
    return evict_idle_work_file(target, stat_result, idle_before)


async def cleanup_global_work_files(
    data_root: Path,
    *,
    min_idle: timedelta = DEFAULT_MIN_IDLE,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> CacheCleanupResult:
    """Remove remote library-image work files idle beyond ``min_idle``, following ``cleanup_work_cache``.

    Delete only confirmed-remote (``stored``), digest-matching local files without current use
    registrations. Local images remain untouched.
    """
    session_factory = session_factory or _default_session_factory()
    idle_before = time.time() - min_idle.total_seconds()
    async with session_factory() as session:
        images = [
            image
            for model in (Asset, AssetDerivative)
            for row in (await session.execute(select(model).where(model.image_storage.is_not(None)))).scalars()
            if (image := GlobalImage.of(row)) is not None and image.stored is not None and image.digest is not None
        ]
        stored = await stored_object_keys(session, {image.stored for image in images if image.stored})
    removed = freed = kept = 0
    for image in images:
        if image.stored not in stored or image.digest is None:
            continue
        count, size, in_use = await asyncio.to_thread(_evict, Path(data_root) / image.path, image.digest, idle_before)
        removed += count
        freed += size
        kept += in_use
    if removed:
        logger.info("清理全局资产库远端图片工作文件 %d 个，释放 %d 字节", removed, freed)
    return CacheCleanupResult(removed=removed, freed_bytes=freed, kept_in_use=kept)


__all__ = [
    "LIBRARY_HOLDER_PREFIX",
    "NO_IMAGE_COLUMNS",
    "GlobalImage",
    "GlobalImageWriter",
    "cleanup_global_work_files",
    "ensure_global_image",
    "forget_global_images",
    "global_image_commit",
    "library_entry_lock",
    "library_holder",
    "release_library_entry",
]
