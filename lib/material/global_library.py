"""全局资产库里的图片：与项目内容版本共用素材存储契约（见 ``docs/adr/0120``）。

库里的每张图片（资产图与衍生图）在条目上记三项事实（列见 ``lib.db.models.asset.GlobalImageColumns``）：

- ``image_digest``：库存储文件的裸 sha256，取回远端内容时据此校验；
- ``image_content_id``：这张图片在全局资产库里的内容身份，每次写入新图片时随机生成；
- ``image_storage``：远端持久定位。有它时 ``image_path`` 只是本地工作文件，可以清理、按需取回。

新写入的图片按全局设置保存在本地或对象存储，经 :func:`global_image_commit` 进入同一个正式提交入口。全局资产库
不是项目，没有版本历史；每个库条目以引用方键 :func:`library_holder`（``asset-library:<条目 ID>``）持有两类以
内容身份为键的记录：

- 正式对象引用：按 ``docs/adr/0118`` 的保留契约，使用前 ``add_reference``，换图或覆盖后对旧图
  ``remove_reference``，条目删除时 ``release_holder``；零引用对象的回收由契约统一完成。
- 供应商素材关联的副本：保存时从来源内容版本复制，应用到项目时再复制给目标的新内容版本。

引用方键带冒号，不会与项目名或已删除项目的墓碑名重名；按项目名重算、改键或结算的读点都不会碰它。

存量条目三列都为空，按库存储里的本地文件解释，照常预览与应用。
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
    fetch_object_content,
    in_use_or_recent,
)
from lib.project.asset_types import ASSET_SPECS

logger = logging.getLogger(__name__)

#: 全局资产库条目作为正式对象引用方与供应商素材关联持有方时的键前缀（``docs/adr/0118`` 的非项目引用方格式）。
LIBRARY_HOLDER_PREFIX = "asset-library:"


def library_holder(asset_id: str) -> str:
    """库条目的引用方键，同时写进 ``object_storage_formal_object_ref`` 与 ``provider_asset_link`` 的 ``project_name``。"""
    return f"{LIBRARY_HOLDER_PREFIX}{asset_id}"


@dataclass(frozen=True)
class GlobalImage:
    """库里一张图片的内容事实。"""

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
        """写进条目的列值。"""
        return {
            "image_path": self.path,
            "image_digest": self.digest,
            "image_content_id": self.content_id,
            "image_storage": self.stored.to_record() if self.stored else None,
        }


#: 条目没有图片时写入的列值。
NO_IMAGE_COLUMNS: dict[str, Any] = {
    "image_path": None,
    "image_digest": None,
    "image_content_id": None,
    "image_storage": None,
}


def _resource_type(asset_type: str) -> str:
    """全局资产类型在版本历史里的资源类型名（决定是否属于随全局设置写远端的图片）。"""
    return ASSET_SPECS[asset_type].bucket_key


@dataclass
class GlobalImageWriter:
    """一个库条目这次写入里新保存的图片。:meth:`save` 是同步磁盘与网络操作，在工作线程里调用。"""

    data_root: Path
    holder: str
    storage: ObjectContentStorage | None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    saved: list[GlobalImage] = field(default_factory=list)
    #: 本次写入落盘的库存储文件（相对数据根），失败时由 :meth:`discard` 删除。
    written: list[str] = field(default_factory=list)

    def save(self, source: Path, asset_type: str, default_ext: str) -> GlobalImage:
        """把 ``source`` 保存为库里的一张新图片：本地写入库存储文件，全局设置为对象存储时再写成正式对象。"""
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
        """把上传的字节保存为库里的一张新图片，语义同 :meth:`save`。"""
        directory = self.data_root / GLOBAL_ASSETS_DIRNAME / asset_type
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".upload.", suffix=ext, dir=directory)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            return self.save(tmp, asset_type, ext)
        finally:
            tmp.unlink(missing_ok=True)  # hard-delete: temp -- 上传字节的暂存文件，内容已保存为库存储文件

    async def hold(self) -> None:
        """为本次保存到远端的图片登记条目的对象引用。在提交条目之前调用：引用在，对象才不会被结算或回收删除。"""
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
        """条目没有写成：撤下本次登记的引用、删除本次落盘的文件。本次新建且无人引用的对象由正式提交的结算删除。"""
        await forget_global_images(self.holder, self.saved, session_factory=self.session_factory)
        await asyncio.to_thread(_delete_files, self.data_root, self.written)


@asynccontextmanager
async def global_image_commit(
    data_root: Path,
    holder: str,
    *,
    saves_images: bool = True,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[GlobalImageWriter]:
    """库条目 ``holder`` 写入新图片的正式提交入口：图片按全局设置保存在本地或对象存储。

    调用方在上下文里经 :meth:`GlobalImageWriter.save` 保存图片、:meth:`GlobalImageWriter.hold` 登记引用后提交条目，
    条目写入失败时调用 :meth:`GlobalImageWriter.discard`。退出时结算本次提交，没有引用的新建对象被删除。
    ``saves_images`` 为 False（这次写入没有图片）时不检查对象存储连接，按本地处理。

    Raises:
        ObjectStorageRequiredError: 全局设置为对象存储而实例没有配置连接。
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
    """条目 ``holder`` 不再使用这些图片（换图或覆盖）：逐张撤下引用，失去全部引用的对象按保留契约统一回收。"""
    for image in images:
        if image is not None and image.stored is not None and image.content_id is not None:
            await remove_reference(image.stored, holder, image.content_id, session_factory=session_factory)


async def release_library_entry(
    holder: str, *, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> None:
    """库条目已删除：撤下它名下的全部引用，失去全部引用的对象按保留契约统一回收。"""
    await release_holder(holder, session_factory=session_factory)


def _delete_files(data_root: Path, rel_paths: Iterable[str]) -> None:
    for rel_path in rel_paths:
        try:
            (data_root / rel_path).unlink()  # hard-delete: out-of-scope -- 全局资产库文件，不属于项目
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("delete global asset file failed: %s", rel_path)


# ---- 读取：工作文件取回 -------------------------------------------------------------------------

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
            tmp.unlink(missing_ok=True)  # hard-delete: temp -- 取回工作文件的临时文件


async def ensure_global_image(
    data_root: Path, rel_path: str, *, session_factory: async_sessionmaker[AsyncSession] | None = None
) -> bool:
    """``rel_path`` 是库里远端图片的工作文件且不在本地时，从对象存储取回并按摘要校验。返回是否做了取回。

    Raises:
        MaterialConnectionUnavailableError: 记录的连接已不存在或已换桶。
        MaterialFetchFailedError: 下载失败或内容与摘要不符。
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


# ---- 清理 -------------------------------------------------------------------------------------


def _evict(target: Path, digest: str, idle_before: float) -> tuple[int, int, int]:
    try:
        stat_result = target.stat()
    except OSError:
        return 0, 0, 0
    if in_use_or_recent(target, stat_result, idle_before):
        return 0, 0, 1
    if sha256_file(target) != digest:
        return 0, 0, 0
    target.unlink()  # hard-delete: derived -- 远端已确认保存的全局资产库图片的本地工作文件，可从对象存储取回
    return 1, stat_result.st_size, 0


async def cleanup_global_work_files(
    data_root: Path,
    *,
    min_idle: timedelta = DEFAULT_MIN_IDLE,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> CacheCleanupResult:
    """删除库里远端图片闲置超过 ``min_idle`` 的本地工作文件，口径同 ``cleanup_work_cache``。

    只删除远端已确认保存（``stored``）、本地内容与摘要一致、此刻没有使用登记的文件；本地图片不受影响。
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
    "library_holder",
    "release_library_entry",
]
