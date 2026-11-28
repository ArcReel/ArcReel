"""按持有方枚举并转正对象存储远端内容的统一接口（Spec #4204 / Issue #4211）。

对象存储收敛回本地与失联强制断开共用本模块的持有方抽象：

- :class:`ActiveProjectsHolder`：活跃项目的版本历史（SQLite 与旧版 JSON）与项目回收站
  （单版本删除记录、结构快照版本清单、脚本覆盖仅下载清单）；
- :class:`DeletedProjectsHolder`：已删除项目物理墓碑（严格按 ``deleted_projects/<墓碑名>/`` 定位，
  绝不按原项目名定位，避免误写入同名新建项目）；
- :class:`GlobalAssetLibraryHolder`：全局资产库主图与角色衍生图（在条目锁内复核并清除远端定位）。

后续无版本项目媒体（Spec 3：尾帧、角色/商品原图、风格参考图）可实现 :class:`RemoteContentHolder` 并通过
:func:`register_remote_content_holder_factory` 或 ``extra_holders`` 接入，无需改写收敛编排器。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import VersionManager
from lib.db.models.asset import Asset, AssetDerivative
from lib.db.models.object_storage import FormalObject, FormalObjectRef
from lib.infra.api_errors import ConflictError
from lib.infra.async_thread import run_sync_transaction
from lib.infra.content_digest import sha256_file
from lib.infra.data_root_layout import DataRootLayout, list_project_dirs
from lib.infra.json_io import atomic_write_json
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.material.formal_objects import (
    REF_ACTIVE,
    iter_version_records,
    reconcile_project_references,
)
from lib.material.global_library import GlobalImage, library_entry_lock, library_holder
from lib.material.storage import CONTENT_DIGEST, CONTENT_VERSION_ID, STORAGE, ObjectLocator
from lib.material.work_files import formal_path, hold_work_file, load_project_json
from lib.project import deleted_projects, project_trash
from lib.project.asset_derivatives import derivative_artifact_id
from lib.project.asset_types import ASSET_SPECS, asset_name_namespace_key
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_relative_path
from lib.script.item_ids import item_id_number

_VERSIONS_FILE = "versions/versions.json"
_RECORD_FILE = "record.json"

HolderKind = Literal[
    "project_version",
    "project_trash_version",
    "project_trash_snapshot",
    "project_trash_script_overwrite",
    "deleted_project_version",
    "deleted_project_trash_version",
    "deleted_project_trash_snapshot",
    "deleted_project_trash_script_overwrite",
    "global_asset",
    "global_derivative",
]


class CorruptedTrashRecordError(ConflictError):
    """项目或已删除项目回收站中存在损坏记录，阻断收敛回本地以免静默遗漏远端对象。"""

    def __init__(self, project_label: str, record_id: str) -> None:
        super().__init__(
            "object_storage_relocation_corrupted_trash",
            project=project_label,
            record_id=record_id,
        )
        self.project_label = project_label
        self.record_id = record_id


@dataclass(frozen=True, slots=True)
class RemoteContentItem:
    """一条指向旧对象存储连接的远端内容引用。"""

    holder_kind: str
    holder_key: str
    display_label: str
    path: str
    locator: ObjectLocator
    digest: str
    content_version_id: str | None
    local_paths: tuple[Path, ...]
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PromoteResult:
    """单条远端内容转正为永久本地文件的结果。"""

    promoted: bool
    conflict_preserved: bool = False


class RemoteContentHolder(Protocol):
    """按持有方枚举并转正远端内容的统一协议。"""

    async def enumerate_remote_items(
        self,
        connection_id: int,
        bucket: str,
        *,
        strict_trash: bool = True,
    ) -> list[RemoteContentItem]:
        """枚举该持有方下全部指向 ``(connection_id, bucket)`` 的远端内容条目。"""
        ...

    async def promote_item(self, item: RemoteContentItem, data: bytes) -> PromoteResult:
        """把 ``item`` 落盘并清除其远端定位转为永久本地文件（持有方锁内复核身份与旧定位）。"""
        ...

    async def reconcile_holder(
        self,
        holder_key: str,
        items: Sequence[RemoteContentItem],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """持有方转正后同步撤下其已不再使用的正式对象引用（不主动触发远端删除）。"""
        ...


type RemoteHolderFactory = Callable[
    [DataRootLayout, async_sessionmaker[AsyncSession]],
    RemoteContentHolder,
]

_EXTRA_HOLDER_FACTORIES: list[RemoteHolderFactory] = []


def register_remote_content_holder_factory(factory: RemoteHolderFactory) -> None:
    """为后续无版本项目媒体（Spec 3）预留的持有方扩展注册入口。"""
    if factory not in _EXTRA_HOLDER_FACTORIES:
        _EXTRA_HOLDER_FACTORIES.append(factory)


def _matches_connection(locator: ObjectLocator | None, connection_id: int, bucket: str) -> bool:
    return locator is not None and locator.connection_id == connection_id and locator.bucket == bucket


def _verified_local_file(path: Path, digest: str) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        return sha256_file(path) == digest
    except OSError:
        return False


def _stage_verified_bytes(parent: Path, stem: str, suffix: str, data: bytes) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{stem}.", suffix=f"{suffix}.relocate", dir=parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return tmp
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)  # hard-delete: temp -- 收敛写盘失败的临时文件
        raise


def _place_from_staged(staged: Path, target: Path, digest: str) -> tuple[bool, bool]:
    """把已校验并 fsync 的临时文件落位到 ``target``。返回 ``(written, conflict_preserved)``。"""
    if target.is_symlink():
        return False, True
    if target.exists():
        if target.is_file() and sha256_file(target) == digest:
            return False, False
        return False, True
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged, target)
    return True, False


def _checked_rel(rel: object) -> str:
    if not isinstance(rel, str) or not rel or "\\" in rel:
        raise ValueError(f"invalid relative path: {rel!r}")
    path = PurePosixPath(rel)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in rel.split("/")):
        raise ValueError(f"invalid relative path: {rel!r}")
    return rel


def _load_trash_record_strict(record_dir: Path, project_label: str) -> tuple[project_trash.TrashRecord, dict[str, Any]]:
    if record_dir.is_symlink() or not record_dir.is_dir():
        raise CorruptedTrashRecordError(project_label, record_dir.name)
    record_file = record_dir / _RECORD_FILE
    if record_file.is_symlink() or not record_file.is_file():
        raise CorruptedTrashRecordError(project_label, record_dir.name)
    try:
        raw = json.loads(record_file.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("record.json must be an object")
        obj = raw["object"]
        if not isinstance(obj, dict):
            raise ValueError("record.object must be an object")
        files_raw = raw["files"]
        if not isinstance(files_raw, list):
            raise ValueError("record.files must be a list")
        record = project_trash.TrashRecord(
            id=record_dir.name,
            object=obj,
            files=tuple(_checked_rel(rel) for rel in files_raw),
            schema_version=int(raw["schema_version"]),
            deleted_at=datetime.fromisoformat(raw["deleted_at"]),
            origin=raw["origin"],
            size=int(raw["size"]),
            has_snapshot=bool(raw.get("has_snapshot", False)),
            download_only=bool(raw.get("download_only", False)),
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CorruptedTrashRecordError(project_label, record_dir.name) from exc
    return record, raw


def _read_json_dict_strict(path: Path, project_label: str, record_id: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CorruptedTrashRecordError(project_label, record_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorruptedTrashRecordError(project_label, record_id) from exc
    if not isinstance(data, dict):
        raise CorruptedTrashRecordError(project_label, record_id)
    return data


def _list_trash_records(
    project_dir: Path, project_label: str, *, strict: bool
) -> list[tuple[project_trash.TrashRecord, Path]]:
    root = project_trash.trash_dir(project_dir)
    if root.is_symlink():
        if strict:
            raise CorruptedTrashRecordError(project_label, project_trash.TRASH_DIRNAME)
        return []
    if not root.is_dir():
        return []
    entries: list[tuple[project_trash.TrashRecord, Path]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name.startswith("."):
            continue
        try:
            record, _raw = _load_trash_record_strict(entry, project_label)
        except CorruptedTrashRecordError:
            if strict:
                raise
            continue
        entries.append((record, entry))
    return entries


def _snapshot_owns_resource(
    record: project_trash.TrashRecord,
    resource_type: str,
    resource_id: str,
    file_rel: str,
    snapshot_project: Mapping[str, Any],
) -> bool:
    """判断结构快照版本清单里的某条资源是否属于该删除记录（区分 SQLite 单项与旧 JSON 整表快照）。"""
    if file_rel in record.files:
        return True
    obj = record.object
    kind = str(obj.get("kind") or "")
    if kind == "asset":
        asset_type = str(obj.get("asset_type") or "")
        name = str(obj.get("name") or "")
        spec = ASSET_SPECS.get(asset_type)
        if spec is None or not name:
            return False
        key = asset_name_namespace_key(name)
        if resource_type == spec.bucket_key and asset_name_namespace_key(resource_id) == key:
            return True
        if spec.supports_derivatives and resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE:
            owner_part = resource_id.split("/", 1)[0]
            return asset_name_namespace_key(owner_part) == key
        return False
    if kind == "asset_derivative":
        owner = str(obj.get("owner") or "")
        name = str(obj.get("name") or "")
        if not owner or not name or resource_type != CHARACTER_DERIVATIVE_RESOURCE_TYPE:
            return False
        expected = asset_name_namespace_key(derivative_artifact_id(owner, name))
        return asset_name_namespace_key(resource_id) == expected
    if kind == "episode":
        episode = obj.get("episode")
        if isinstance(episode, int):
            if item_id_number(resource_id, episode) is not None:
                return True
            if resource_type == "grids":
                return True
        return False
    if kind == project_trash.SCRIPT_ENTRY_KIND:
        entry_id = str(obj.get("id") or obj.get("entry_id") or "")
        return bool(entry_id) and resource_id == entry_id
    if kind == "grid":
        grid_id = str(obj.get("id") or "")
        return resource_type == "grids" and bool(grid_id) and resource_id == grid_id
    formal = formal_path(dict(snapshot_project), resource_type, resource_id)
    return formal is not None and formal in record.files


def _enumerate_project_directory(
    project_dir: Path,
    holder_key: str,
    display_prefix: str,
    *,
    is_deleted: bool,
    connection_id: int,
    bucket: str,
    strict_trash: bool,
) -> list[RemoteContentItem]:
    """在项目锁与版本锁内枚举单个项目目录（活跃项目或物理墓碑）的全部远端内容。"""
    if project_dir.is_symlink() or not project_dir.is_dir() or not (project_dir / "project.json").is_file():
        return []
    version_kind = "deleted_project_version" if is_deleted else "project_version"
    trash_version_kind = "deleted_project_trash_version" if is_deleted else "project_trash_version"
    trash_snapshot_kind = "deleted_project_trash_snapshot" if is_deleted else "project_trash_snapshot"
    trash_overwrite_kind = "deleted_project_trash_script_overwrite" if is_deleted else "project_trash_script_overwrite"

    items: list[RemoteContentItem] = []
    with project_metadata_lock(project_dir):
        project_data = load_project_json(project_dir)
        manager = VersionManager(project_dir)
        with manager.locked_rows(remote_only=True) as rows:
            for resource_type, resource_id, current_version, record in rows:
                locator = ObjectLocator.from_record(record.get(STORAGE))
                if not _matches_connection(locator, connection_id, bucket):
                    continue
                assert locator is not None
                digest = record.get(CONTENT_DIGEST)
                file_rel = record.get("file")
                version_num = record.get("version")
                content_version_id = record.get(CONTENT_VERSION_ID)
                if not isinstance(digest, str) or not isinstance(file_rel, str) or not isinstance(version_num, int):
                    continue
                local_candidates: list[Path] = []
                try:
                    snapshot_path = VersionManager.resolve_snapshot_path(project_dir, resource_type, file_rel)
                    local_candidates.append(snapshot_path)
                except Exception:
                    continue
                formal_rel: str | None = None
                if current_version == version_num:
                    formal_rel = formal_path(project_data, resource_type, resource_id)
                    if formal_rel is not None:
                        try:
                            safe_join(project_dir, formal_rel)
                            local_candidates.append(project_dir / formal_rel)
                        except PathTraversalError:
                            formal_rel = None
                items.append(
                    RemoteContentItem(
                        holder_kind=version_kind,
                        holder_key=holder_key,
                        display_label=f"{display_prefix} / {resource_type}/{resource_id} (v{version_num})",
                        path=file_rel,
                        locator=locator,
                        digest=digest,
                        content_version_id=content_version_id if isinstance(content_version_id, str) else None,
                        local_paths=tuple(local_candidates),
                        metadata={
                            "project_dir": str(project_dir),
                            "resource_type": resource_type,
                            "resource_id": resource_id,
                            "version": version_num,
                            "file_rel": file_rel,
                            "formal_rel": formal_rel,
                            "is_current": current_version == version_num,
                        },
                    )
                )

        for record, record_dir in _list_trash_records(project_dir, display_prefix, strict=strict_trash):
            files_root = record_dir / project_trash.FILES_DIRNAME

            if record.object.get("kind") == project_trash.SCRIPT_OVERWRITE_KIND and _VERSIONS_FILE in record.files:
                archived_versions_path = files_root / _VERSIONS_FILE
                try:
                    archived_versions = _read_json_dict_strict(archived_versions_path, display_prefix, record.id)
                except CorruptedTrashRecordError:
                    if strict_trash:
                        raise
                    continue
                for resource_type, resource_id, ver_rec in iter_version_records(archived_versions):
                    locator = ObjectLocator.from_record(ver_rec.get(STORAGE))
                    if not _matches_connection(locator, connection_id, bucket):
                        continue
                    assert locator is not None
                    digest = ver_rec.get(CONTENT_DIGEST)
                    file_rel = ver_rec.get("file")
                    version_num = ver_rec.get("version")
                    content_version_id = ver_rec.get(CONTENT_VERSION_ID)
                    if not isinstance(digest, str) or not isinstance(file_rel, str) or not isinstance(version_num, int):
                        continue
                    bucket_map = archived_versions.get(resource_type, {})
                    res_history = bucket_map.get(resource_id, {}) if isinstance(bucket_map, dict) else {}
                    is_current = isinstance(res_history, dict) and res_history.get("current_version") == version_num
                    local_candidates = [files_root / file_rel]
                    formal_rel = None
                    if is_current:
                        try:
                            candidate_formal = resource_relative_path(resource_type, resource_id)
                        except ValueError:
                            candidate_formal = None
                        if candidate_formal is not None and candidate_formal in record.files:
                            formal_rel = candidate_formal
                            local_candidates.append(files_root / formal_rel)
                    items.append(
                        RemoteContentItem(
                            holder_kind=trash_overwrite_kind,
                            holder_key=holder_key,
                            display_label=(
                                f"{display_prefix} [trash:{record.id}] / {resource_type}/{resource_id} (v{version_num})"
                            ),
                            path=file_rel,
                            locator=locator,
                            digest=digest,
                            content_version_id=content_version_id if isinstance(content_version_id, str) else None,
                            local_paths=tuple(local_candidates),
                            metadata={
                                "project_dir": str(project_dir),
                                "record_id": record.id,
                                "resource_type": resource_type,
                                "resource_id": resource_id,
                                "version": version_num,
                                "file_rel": file_rel,
                                "formal_rel": formal_rel,
                            },
                        )
                    )

            if record.download_only:
                continue

            if record.object.get("kind") == "version":
                meta = record.object.get("metadata")
                if isinstance(meta, Mapping):
                    locator = ObjectLocator.from_record(meta.get(STORAGE))
                    if _matches_connection(locator, connection_id, bucket):
                        assert locator is not None
                        digest = meta.get(CONTENT_DIGEST)
                        content_version_id = meta.get(CONTENT_VERSION_ID)
                        file_rel = record.object.get("path") or (record.files[0] if record.files else None)
                        if isinstance(digest, str) and isinstance(file_rel, str):
                            resource_type = str(record.object.get("resource_type") or "")
                            resource_id = str(record.object.get("resource_id") or "")
                            version_num = record.object.get("version")
                            items.append(
                                RemoteContentItem(
                                    holder_kind=trash_version_kind,
                                    holder_key=holder_key,
                                    display_label=(
                                        f"{display_prefix} [trash:{record.id}] / "
                                        f"{resource_type}/{resource_id} (v{version_num})"
                                    ),
                                    path=file_rel,
                                    locator=locator,
                                    digest=digest,
                                    content_version_id=(
                                        content_version_id if isinstance(content_version_id, str) else None
                                    ),
                                    local_paths=(files_root / file_rel,),
                                    metadata={
                                        "project_dir": str(project_dir),
                                        "record_id": record.id,
                                        "file_rel": file_rel,
                                    },
                                )
                            )

            if record.has_snapshot:
                snapshot_dir = record_dir / project_trash.SNAPSHOT_DIRNAME
                snapshot_versions_path = snapshot_dir / _VERSIONS_FILE
                if snapshot_versions_path.exists() or snapshot_versions_path.is_symlink():
                    try:
                        snapshot_versions = _read_json_dict_strict(snapshot_versions_path, display_prefix, record.id)
                    except CorruptedTrashRecordError:
                        if strict_trash:
                            raise
                        continue
                    snapshot_project = load_project_json(snapshot_dir)
                    for resource_type, resource_id, ver_rec in iter_version_records(snapshot_versions):
                        locator = ObjectLocator.from_record(ver_rec.get(STORAGE))
                        if not _matches_connection(locator, connection_id, bucket):
                            continue
                        assert locator is not None
                        digest = ver_rec.get(CONTENT_DIGEST)
                        file_rel = ver_rec.get("file")
                        version_num = ver_rec.get("version")
                        content_version_id = ver_rec.get(CONTENT_VERSION_ID)
                        if (
                            not isinstance(digest, str)
                            or not isinstance(file_rel, str)
                            or not isinstance(version_num, int)
                        ):
                            continue
                        owned = _snapshot_owns_resource(record, resource_type, resource_id, file_rel, snapshot_project)
                        bucket_map = snapshot_versions.get(resource_type, {})
                        res_history = bucket_map.get(resource_id, {}) if isinstance(bucket_map, dict) else {}
                        is_current = isinstance(res_history, dict) and res_history.get("current_version") == version_num
                        local_candidates = [files_root / file_rel]
                        formal_rel = None
                        if owned and is_current:
                            formal_rel = formal_path(snapshot_project, resource_type, resource_id)
                            if formal_rel is not None:
                                local_candidates.append(files_root / formal_rel)
                        items.append(
                            RemoteContentItem(
                                holder_kind=trash_snapshot_kind,
                                holder_key=holder_key,
                                display_label=(
                                    f"{display_prefix} [trash:{record.id}] / "
                                    f"{resource_type}/{resource_id} (v{version_num})"
                                ),
                                path=file_rel,
                                locator=locator,
                                digest=digest,
                                content_version_id=content_version_id if isinstance(content_version_id, str) else None,
                                local_paths=tuple(local_candidates),
                                metadata={
                                    "project_dir": str(project_dir),
                                    "record_id": record.id,
                                    "resource_type": resource_type,
                                    "resource_id": resource_id,
                                    "version": version_num,
                                    "file_rel": file_rel,
                                    "formal_rel": formal_rel,
                                    "owned": owned,
                                },
                            )
                        )
    return items


def _promote_project_version_sync(item: RemoteContentItem, data: bytes) -> PromoteResult:
    project_dir = Path(item.metadata["project_dir"])
    resource_type = str(item.metadata["resource_type"])
    resource_id = str(item.metadata["resource_id"])
    version_num = int(item.metadata["version"])
    file_rel = str(item.metadata["file_rel"])

    if not (project_dir / "project.json").is_file():
        return PromoteResult(promoted=False)

    snapshot_path = VersionManager.resolve_snapshot_path(project_dir, resource_type, file_rel)
    staged_snapshot: Path | None = None
    staged_formal: Path | None = None
    staged_formal_target: Path | None = None

    if not snapshot_path.exists() and not snapshot_path.is_symlink():
        staged_snapshot = _stage_verified_bytes(snapshot_path.parent, snapshot_path.stem, snapshot_path.suffix, data)
    formal_rel_hint = item.metadata.get("formal_rel")
    if isinstance(formal_rel_hint, str) and formal_rel_hint:
        try:
            safe_join(project_dir, formal_rel_hint)
            candidate = project_dir / formal_rel_hint
            if not candidate.exists() and not candidate.is_symlink():
                staged_formal = _stage_verified_bytes(candidate.parent, candidate.stem, candidate.suffix, data)
                staged_formal_target = candidate
        except PathTraversalError:
            staged_formal_target = None

    try:
        with project_metadata_lock(project_dir):
            if not (project_dir / "project.json").is_file():
                return PromoteResult(promoted=False)
            manager = VersionManager(project_dir)
            with manager._lock:
                all_data: dict[str, Any] | None = None
                if manager.sqlite.is_enabled():
                    batch = manager.sqlite.resources([(resource_type, resource_id)])
                    res_data = batch.get(resource_type, {}).get(resource_id)
                else:
                    all_data = manager._load_versions()
                    res_data = all_data.get(resource_type, {}).get(resource_id)
                    batch = None
                if not isinstance(res_data, dict):
                    return PromoteResult(promoted=False)
                versions = res_data.get("versions")
                if not isinstance(versions, list):
                    return PromoteResult(promoted=False)
                target_rec = next(
                    (
                        rec
                        for rec in versions
                        if isinstance(rec, dict)
                        and rec.get("version") == version_num
                        and rec.get(CONTENT_VERSION_ID) == item.content_version_id
                    ),
                    None,
                )
                if target_rec is None:
                    return PromoteResult(promoted=False)
                if ObjectLocator.from_record(target_rec.get(STORAGE)) != item.locator:
                    return PromoteResult(promoted=False)
                if target_rec.get(CONTENT_DIGEST) != item.digest:
                    return PromoteResult(promoted=False)

                conflict = False
                with hold_work_file(snapshot_path):
                    if staged_snapshot is not None:
                        _written, snap_conflict = _place_from_staged(staged_snapshot, snapshot_path, item.digest)
                        if not snap_conflict and _written:
                            staged_snapshot = None
                        conflict = conflict or snap_conflict
                    elif snapshot_path.is_symlink():
                        conflict = True
                    elif snapshot_path.is_file():
                        if sha256_file(snapshot_path) != item.digest:
                            conflict = True
                    else:
                        extra = _stage_verified_bytes(
                            snapshot_path.parent, snapshot_path.stem, snapshot_path.suffix, data
                        )
                        try:
                            _written, snap_conflict = _place_from_staged(extra, snapshot_path, item.digest)
                            conflict = conflict or snap_conflict
                        finally:
                            with contextlib.suppress(OSError):
                                extra.unlink(missing_ok=True)  # hard-delete: temp -- 锁内补写快照的临时文件

                    if res_data.get("current_version") == version_num:
                        fresh_project = load_project_json(project_dir)
                        fresh_formal_rel = formal_path(fresh_project, resource_type, resource_id)
                        if fresh_formal_rel is not None:
                            try:
                                safe_join(project_dir, fresh_formal_rel)
                                target_formal = project_dir / fresh_formal_rel
                            except PathTraversalError:
                                target_formal = None
                            if target_formal is not None:
                                with hold_work_file(target_formal):
                                    if staged_formal is not None and staged_formal_target == target_formal:
                                        _written, formal_conflict = _place_from_staged(
                                            staged_formal, target_formal, item.digest
                                        )
                                        if not formal_conflict and _written:
                                            staged_formal = None
                                        conflict = conflict or formal_conflict
                                    elif target_formal.is_symlink():
                                        conflict = True
                                    elif target_formal.exists():
                                        if not target_formal.is_file() or sha256_file(target_formal) != item.digest:
                                            conflict = True
                                    else:
                                        extra_formal = _stage_verified_bytes(
                                            target_formal.parent, target_formal.stem, target_formal.suffix, data
                                        )
                                        try:
                                            _written, formal_conflict = _place_from_staged(
                                                extra_formal, target_formal, item.digest
                                            )
                                            conflict = conflict or formal_conflict
                                        finally:
                                            with contextlib.suppress(OSError):
                                                extra_formal.unlink(
                                                    missing_ok=True
                                                )  # hard-delete: temp -- 锁内补写正式文件的临时文件

                    target_rec.pop(STORAGE, None)
                    if batch is not None:
                        manager.sqlite.save(batch)
                    elif all_data is not None:
                        atomic_write_json(manager.versions_file, all_data)
                return PromoteResult(promoted=True, conflict_preserved=conflict)
    finally:
        for leftover in (staged_snapshot, staged_formal):
            if leftover is not None:
                with contextlib.suppress(OSError):
                    leftover.unlink(missing_ok=True)  # hard-delete: temp -- 收敛提交后未使用的暂存文件


def _promote_trash_version_sync(item: RemoteContentItem, data: bytes) -> PromoteResult:
    project_dir = Path(item.metadata["project_dir"])
    record_id = str(item.metadata["record_id"])
    file_rel = str(item.metadata["file_rel"])
    record_dir = project_trash.trash_dir(project_dir) / record_id
    files_root = record_dir / project_trash.FILES_DIRNAME
    safe_join(files_root, file_rel)
    target = files_root / file_rel

    staged: Path | None = None
    if not target.exists() and not target.is_symlink():
        staged = _stage_verified_bytes(target.parent, target.stem, target.suffix, data)
    try:
        with project_metadata_lock(project_dir):
            record_file = record_dir / _RECORD_FILE
            if not record_file.is_file():
                return PromoteResult(promoted=False)
            raw = json.loads(record_file.read_text(encoding="utf-8"))
            obj = raw.get("object")
            meta = obj.get("metadata") if isinstance(obj, dict) else None
            if not isinstance(meta, dict):
                return PromoteResult(promoted=False)
            if meta.get(CONTENT_VERSION_ID) != item.content_version_id:
                return PromoteResult(promoted=False)
            if ObjectLocator.from_record(meta.get(STORAGE)) != item.locator:
                return PromoteResult(promoted=False)
            if meta.get(CONTENT_DIGEST) != item.digest:
                return PromoteResult(promoted=False)

            conflict = False
            written = False
            if staged is not None:
                written, conflict = _place_from_staged(staged, target, item.digest)
                if written:
                    staged = None
            elif target.is_symlink():
                conflict = True
            elif target.is_file():
                if sha256_file(target) != item.digest:
                    conflict = True
            else:
                extra = _stage_verified_bytes(target.parent, target.stem, target.suffix, data)
                try:
                    written, conflict = _place_from_staged(extra, target, item.digest)
                finally:
                    with contextlib.suppress(OSError):
                        extra.unlink(missing_ok=True)  # hard-delete: temp -- 回收站单版本补写临时文件

            meta.pop(STORAGE, None)
            if written:
                raw["size"] = int(raw.get("size") or 0) + len(data)
            atomic_write_json(record_file, raw)
            return PromoteResult(promoted=True, conflict_preserved=conflict)
    finally:
        if staged is not None:
            with contextlib.suppress(OSError):
                staged.unlink(missing_ok=True)  # hard-delete: temp -- 回收站转正未使用的暂存文件


def _promote_trash_versions_manifest_sync(
    item: RemoteContentItem, data: bytes, *, is_script_overwrite: bool
) -> PromoteResult:
    project_dir = Path(item.metadata["project_dir"])
    record_id = str(item.metadata["record_id"])
    resource_type = str(item.metadata["resource_type"])
    resource_id = str(item.metadata["resource_id"])
    version_num = int(item.metadata["version"])
    file_rel = str(item.metadata["file_rel"])
    formal_rel = item.metadata.get("formal_rel")
    should_materialize = True if is_script_overwrite else bool(item.metadata.get("owned", True))

    record_dir = project_trash.trash_dir(project_dir) / record_id
    files_root = record_dir / project_trash.FILES_DIRNAME
    manifest_path = (
        files_root / _VERSIONS_FILE
        if is_script_overwrite
        else record_dir / project_trash.SNAPSHOT_DIRNAME / _VERSIONS_FILE
    )

    staged_targets: list[tuple[str, Path, Path]] = []
    if should_materialize:
        rels_to_place = [file_rel]
        if isinstance(formal_rel, str) and formal_rel and formal_rel != file_rel:
            rels_to_place.append(formal_rel)
        for rel in rels_to_place:
            safe_join(files_root, rel)
            target = files_root / rel
            if not target.exists() and not target.is_symlink():
                staged_targets.append(
                    (rel, target, _stage_verified_bytes(target.parent, target.stem, target.suffix, data))
                )

    try:
        with project_metadata_lock(project_dir):
            record_file = record_dir / _RECORD_FILE
            if not record_file.is_file() or not manifest_path.is_file():
                return PromoteResult(promoted=False)
            versions_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            bucket_map = versions_data.get(resource_type)
            res_data = bucket_map.get(resource_id) if isinstance(bucket_map, dict) else None
            records = res_data.get("versions") if isinstance(res_data, dict) else None
            if not isinstance(records, list):
                return PromoteResult(promoted=False)
            target_rec = next(
                (
                    rec
                    for rec in records
                    if isinstance(rec, dict)
                    and rec.get("version") == version_num
                    and rec.get(CONTENT_VERSION_ID) == item.content_version_id
                ),
                None,
            )
            if target_rec is None:
                return PromoteResult(promoted=False)
            if ObjectLocator.from_record(target_rec.get(STORAGE)) != item.locator:
                return PromoteResult(promoted=False)
            if target_rec.get(CONTENT_DIGEST) != item.digest:
                return PromoteResult(promoted=False)

            conflict = False
            newly_written_rels: list[str] = []
            added_bytes = 0
            remaining_staged: list[tuple[str, Path, Path]] = []
            for rel, target, staged in staged_targets:
                written, item_conflict = _place_from_staged(staged, target, item.digest)
                conflict = conflict or item_conflict
                if written:
                    newly_written_rels.append(rel)
                    added_bytes += len(data)
                else:
                    remaining_staged.append((rel, target, staged))
            staged_targets = remaining_staged

            target_rec.pop(STORAGE, None)
            atomic_write_json(manifest_path, versions_data)

            if newly_written_rels:
                raw = json.loads(record_file.read_text(encoding="utf-8"))
                existing_files = list(raw.get("files") or [])
                for rel in newly_written_rels:
                    if rel not in existing_files:
                        existing_files.append(rel)
                raw["files"] = existing_files
                raw["size"] = int(raw.get("size") or 0) + added_bytes
                atomic_write_json(record_file, raw)

            return PromoteResult(promoted=True, conflict_preserved=conflict)
    finally:
        for _rel, _target, staged in staged_targets:
            with contextlib.suppress(OSError):
                staged.unlink(missing_ok=True)  # hard-delete: temp -- 回收站快照转正未使用的暂存文件


class _BaseProjectDirectoryHolder:
    """活跃项目与已删除项目物理墓碑共用的项目目录持有方基类。"""

    def __init__(self, layout: DataRootLayout, *, is_deleted: bool) -> None:
        self._layout = layout
        self._is_deleted = is_deleted

    def _list_targets(self) -> list[tuple[str, Path, str]]:
        if self._is_deleted:
            return [
                (entry.id, self._layout.deleted_projects_dir / entry.id, f"deleted:{entry.name}({entry.id})")
                for entry in deleted_projects.list_entries(self._layout)
            ]
        return [
            (project_dir.name, project_dir, project_dir.name)
            for project_dir in list_project_dirs(self._layout.projects_dir)
        ]

    async def enumerate_remote_items(
        self,
        connection_id: int,
        bucket: str,
        *,
        strict_trash: bool = True,
    ) -> list[RemoteContentItem]:
        def _scan() -> list[RemoteContentItem]:
            found: list[RemoteContentItem] = []
            for holder_key, project_dir, display_prefix in self._list_targets():
                found.extend(
                    _enumerate_project_directory(
                        project_dir,
                        holder_key,
                        display_prefix,
                        is_deleted=self._is_deleted,
                        connection_id=connection_id,
                        bucket=bucket,
                        strict_trash=strict_trash,
                    )
                )
            return found

        return await asyncio.to_thread(_scan)

    async def promote_item(self, item: RemoteContentItem, data: bytes) -> PromoteResult:
        if item.holder_kind in ("project_version", "deleted_project_version"):
            return await run_sync_transaction(_promote_project_version_sync, item, data)
        if item.holder_kind in ("project_trash_version", "deleted_project_trash_version"):
            return await run_sync_transaction(_promote_trash_version_sync, item, data)
        if item.holder_kind in ("project_trash_snapshot", "deleted_project_trash_snapshot"):
            return await run_sync_transaction(
                _promote_trash_versions_manifest_sync, item, data, is_script_overwrite=False
            )
        if item.holder_kind in ("project_trash_script_overwrite", "deleted_project_trash_script_overwrite"):
            return await run_sync_transaction(
                _promote_trash_versions_manifest_sync, item, data, is_script_overwrite=True
            )
        raise ValueError(f"unsupported project holder kind: {item.holder_kind}")

    async def reconcile_holder(
        self,
        holder_key: str,
        items: Sequence[RemoteContentItem],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        if not items:
            return
        project_dir = Path(items[0].metadata["project_dir"])
        if await asyncio.to_thread(lambda: (project_dir / "project.json").is_file()):
            await reconcile_project_references(holder_key, project_dir, session_factory=session_factory)


class ActiveProjectsHolder(_BaseProjectDirectoryHolder):
    """活跃项目（版本历史 + 项目回收站）。"""

    def __init__(self, layout: DataRootLayout) -> None:
        super().__init__(layout, is_deleted=False)


class DeletedProjectsHolder(_BaseProjectDirectoryHolder):
    """已删除项目物理墓碑（按物理墓碑目录定位，绝不写入同名活动项目）。"""

    def __init__(self, layout: DataRootLayout) -> None:
        super().__init__(layout, is_deleted=True)


class GlobalAssetLibraryHolder:
    """全局资产库（主图 + 角色衍生图）。"""

    def __init__(self, layout: DataRootLayout, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._data_root = layout.root
        self._session_factory = session_factory

    async def enumerate_remote_items(
        self,
        connection_id: int,
        bucket: str,
        *,
        strict_trash: bool = True,
    ) -> list[RemoteContentItem]:
        del strict_trash
        items: list[RemoteContentItem] = []
        async with self._session_factory() as session:
            assets = list((await session.execute(select(Asset).where(Asset.image_storage.is_not(None)))).scalars())
            asset_names = {asset.id: asset.name for asset in assets}
            for asset in assets:
                image = GlobalImage.of(asset)
                if (
                    image is None
                    or not _matches_connection(image.stored, connection_id, bucket)
                    or image.digest is None
                ):
                    continue
                assert image.stored is not None
                items.append(
                    RemoteContentItem(
                        holder_kind="global_asset",
                        holder_key=library_holder(asset.id),
                        display_label=f"global-asset: {asset.type}/{asset.name}",
                        path=image.path,
                        locator=image.stored,
                        digest=image.digest,
                        content_version_id=image.content_id,
                        local_paths=(self._data_root / image.path,),
                        metadata={"model": "asset", "row_id": asset.id, "asset_id": asset.id},
                    )
                )

            derivatives = list(
                (
                    await session.execute(select(AssetDerivative).where(AssetDerivative.image_storage.is_not(None)))
                ).scalars()
            )
            missing_parent_ids = {d.asset_id for d in derivatives if d.asset_id not in asset_names}
            if missing_parent_ids:
                parents = (
                    await session.execute(select(Asset.id, Asset.name).where(Asset.id.in_(missing_parent_ids)))
                ).all()
                asset_names.update((pid, pname) for pid, pname in parents)
            for deriv in derivatives:
                image = GlobalImage.of(deriv)
                if (
                    image is None
                    or not _matches_connection(image.stored, connection_id, bucket)
                    or image.digest is None
                ):
                    continue
                assert image.stored is not None
                parent_name = asset_names.get(deriv.asset_id, deriv.asset_id)
                items.append(
                    RemoteContentItem(
                        holder_kind="global_derivative",
                        holder_key=library_holder(deriv.asset_id),
                        display_label=f"global-derivative: {parent_name}/{deriv.name}",
                        path=image.path,
                        locator=image.stored,
                        digest=image.digest,
                        content_version_id=image.content_id,
                        local_paths=(self._data_root / image.path,),
                        metadata={"model": "derivative", "row_id": deriv.id, "asset_id": deriv.asset_id},
                    )
                )
        return items

    async def promote_item(self, item: RemoteContentItem, data: bytes) -> PromoteResult:
        asset_id = str(item.metadata["asset_id"])
        row_id = str(item.metadata["row_id"])
        model: type[Asset] | type[AssetDerivative] = Asset if item.metadata["model"] == "asset" else AssetDerivative
        target = self._data_root / item.path

        def _stage_if_needed() -> Path | None:
            if not target.exists() and not target.is_symlink():
                return _stage_verified_bytes(target.parent, target.stem, target.suffix, data)
            return None

        staged = await run_sync_transaction(_stage_if_needed)
        try:
            async with library_entry_lock(asset_id), self._session_factory() as session:
                row = await session.get(model, row_id)
                current = GlobalImage.of(row) if row is not None else None
                if (
                    row is None
                    or current is None
                    or current.path != item.path
                    or current.digest != item.digest
                    or current.content_id != item.content_version_id
                    or current.stored != item.locator
                ):
                    return PromoteResult(promoted=False)

                def _place() -> bool:
                    nonlocal staged
                    with hold_work_file(target):
                        if staged is not None:
                            written, conflict = _place_from_staged(staged, target, item.digest)
                            if written:
                                staged = None
                            return conflict
                        if target.is_symlink():
                            return True
                        if target.is_file():
                            return sha256_file(target) != item.digest
                        extra = _stage_verified_bytes(target.parent, target.stem, target.suffix, data)
                        try:
                            _written, conflict = _place_from_staged(extra, target, item.digest)
                            return conflict
                        finally:
                            with contextlib.suppress(OSError):
                                extra.unlink(missing_ok=True)  # hard-delete: temp -- 全局资产补写临时文件

                conflict = await run_sync_transaction(_place)
                row.image_storage = None
                await session.commit()
                return PromoteResult(promoted=True, conflict_preserved=conflict)
        finally:
            if staged is not None:
                await asyncio.to_thread(
                    lambda p: p.unlink(missing_ok=True),  # hard-delete: temp -- 全局资产转正未使用的暂存文件
                    staged,
                )

    async def reconcile_holder(
        self,
        holder_key: str,
        items: Sequence[RemoteContentItem],
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        if not items:
            return
        async with session_factory() as session:
            for item in items:
                if item.content_version_id is None:
                    continue
                obj_stmt = select(FormalObject.id).where(
                    FormalObject.connection_id == item.locator.connection_id,
                    FormalObject.bucket == item.locator.bucket,
                    FormalObject.object_key == item.locator.key,
                )
                object_id = (await session.execute(obj_stmt)).scalar_one_or_none()
                if object_id is None:
                    continue
                refs = (
                    await session.execute(
                        select(FormalObjectRef).where(
                            FormalObjectRef.object_id == object_id,
                            FormalObjectRef.project_name == holder_key,
                            FormalObjectRef.content_version_id == item.content_version_id,
                            FormalObjectRef.state == REF_ACTIVE,
                        )
                    )
                ).scalars()
                for ref in refs:
                    await session.delete(ref)
            await session.commit()


def default_remote_holders(
    layout: DataRootLayout,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    extra_holders: Sequence[RemoteContentHolder] = (),
) -> list[RemoteContentHolder]:
    """返回全部内建与已注册扩展的远端内容持有方。"""
    holders: list[RemoteContentHolder] = [
        ActiveProjectsHolder(layout),
        DeletedProjectsHolder(layout),
        GlobalAssetLibraryHolder(layout, session_factory),
    ]
    holders.extend(factory(layout, session_factory) for factory in _EXTRA_HOLDER_FACTORIES)
    holders.extend(extra_holders)
    return holders


def build_local_digest_index(items: Iterable[RemoteContentItem]) -> dict[str, Path]:
    """跨持有方扫描已存在且 SHA-256 匹配的本地副本，返回 ``digest -> 有效本地路径``。同步磁盘操作。"""
    verified: dict[str, Path] = {}
    for item in items:
        if item.digest in verified:
            continue
        for candidate in item.local_paths:
            if _verified_local_file(candidate, item.digest):
                verified[item.digest] = candidate
                break
    return verified


def verify_and_read_local(path: Path, expected_digest: str) -> bytes | None:
    """读取本地候选副本并核验 SHA-256；缺失或摘要不符返回 ``None``。"""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != expected_digest:
        return None
    return data


__all__ = [
    "ActiveProjectsHolder",
    "CorruptedTrashRecordError",
    "DeletedProjectsHolder",
    "GlobalAssetLibraryHolder",
    "HolderKind",
    "PromoteResult",
    "RemoteContentHolder",
    "RemoteContentItem",
    "RemoteHolderFactory",
    "build_local_digest_index",
    "default_remote_holders",
    "register_remote_content_holder_factory",
    "verify_and_read_local",
]
