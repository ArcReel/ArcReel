"""
版本管理模块

管理分镜图、视频、角色图、场景设计图、道具设计图、宫格图的历史版本。
支持版本备份、切换当前版本、记录和查询。
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from lib.api_errors import BadRequestError, NotFoundError
from lib.json_io import atomic_write_bytes, atomic_write_json
from lib.resource_paths import RESOURCE_TYPES as _RESOURCE_TYPES
from lib.resource_paths import resource_extension

_LOCKS_GUARD = threading.Lock()
_LOCKS_BY_VERSIONS_FILE: dict[str, threading.RLock] = {}
_PREVIOUS_CURRENT_VERSION = "_previous_current_version"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PaidVersionCommit:
    """Outcome of recording paid media and optionally selecting it as current."""

    version: int
    selected: bool


def _get_versions_file_lock(versions_file: Path) -> threading.RLock:
    key = str(Path(versions_file).resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS_BY_VERSIONS_FILE.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS_BY_VERSIONS_FILE[key] = lock
        return lock


def _unlink_paths(*paths: Path | None) -> list[tuple[Path, OSError]]:
    """Attempt every unlink and return failures without masking an active operation."""

    failures: list[tuple[Path, OSError]] = []
    for path in paths:
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            failures.append((path, exc))
    return failures


def _report_cleanup_failures(
    failures: list[tuple[Path, OSError]],
    *,
    active_failure: BaseException | None,
) -> None:
    for path, cleanup_failure in failures:
        message = f"failed to remove temporary version file {path}: {cleanup_failure}"
        if active_failure is not None:
            active_failure.add_note(message)
        else:
            logger.warning("failed to remove temporary version file %s: %s", path, cleanup_failure)


class VersionManager:
    """版本管理器"""

    # 支持的资源类型与扩展名均派生自单一真相源 lib.resource_paths，避免副本漂移。
    RESOURCE_TYPES = _RESOURCE_TYPES
    EXTENSIONS = {rt: resource_extension(rt) for rt in _RESOURCE_TYPES}

    def __init__(self, project_path: Path):
        """
        初始化版本管理器

        Args:
            project_path: 项目根目录路径
        """
        self.project_path = Path(project_path)
        self.versions_dir = self.project_path / "versions"
        self.versions_file = self.versions_dir / "versions.json"
        self._lock = _get_versions_file_lock(self.versions_file)

    def _ensure_dirs(self) -> None:
        """确保版本目录结构存在。

        由写路径按需调用，不在 ``__init__`` 里建：只读用法（含改名的 ``dry_run`` 预演）
        构造本类时不应在项目下留下空的 ``versions/`` 目录树。
        """
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        for resource_type in self.RESOURCE_TYPES:
            (self.versions_dir / resource_type).mkdir(exist_ok=True)

    def _load_versions(self) -> dict:
        """加载版本元数据"""
        if not self.versions_file.exists():
            return {rt: {} for rt in self.RESOURCE_TYPES}

        with open(self.versions_file, encoding="utf-8") as f:
            return json.load(f)

    def _save_versions(self, data: dict) -> None:
        """保存版本元数据"""
        self._ensure_dirs()
        atomic_write_json(self.versions_file, data)

    def _generate_timestamp(self) -> str:
        """生成时间戳字符串（用于文件名）"""
        return datetime.now().strftime("%Y%m%dT%H%M%S")

    def _generate_iso_timestamp(self) -> str:
        """生成 ISO 格式时间戳（用于元数据）"""
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_versions(self, resource_type: str, resource_id: str) -> dict:
        """
        获取资源的所有版本信息

        Args:
            resource_type: 资源类型 (storyboards, videos, characters, clues)
            resource_id: 资源 ID (如 E1S01, 姜月茴)

        Returns:
            版本信息字典，包含 current_version 和 versions 列表
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise BadRequestError("unsupported_resource_type", resource_type=resource_type)

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)

            if not resource_data:
                return {"current_version": 0, "versions": []}

            # 添加 is_current 和 file_url 字段
            versions = []
            for v in resource_data.get("versions", []):
                version_info = v.copy()
                version_info["is_current"] = v["version"] == resource_data["current_version"]
                version_info["file_url"] = f"/api/v1/files/{self.project_path.name}/{v['file']}"
                versions.append(version_info)

            return {"current_version": resource_data.get("current_version", 0), "versions": versions}

    def get_current_version(self, resource_type: str, resource_id: str) -> int:
        """
        获取当前版本号

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID

        Returns:
            当前版本号，无版本时返回 0
        """
        info = self.get_versions(resource_type, resource_id)
        return info["current_version"]

    def add_version(
        self, resource_type: str, resource_id: str, prompt: str, source_file: Path | None = None, **metadata
    ) -> int:
        """
        添加新版本记录

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            prompt: 生成该版本使用的 prompt
            source_file: 源文件路径（用于复制到版本目录）
            **metadata: 额外的元数据（如 aspect_ratio, duration_seconds）

        Returns:
            新版本号
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        with self._lock:
            data = self._load_versions()

            # 确保资源类型存在
            if resource_type not in data:
                data[resource_type] = {}

            # 获取或创建资源记录
            if resource_id not in data[resource_type]:
                data[resource_type][resource_id] = {"current_version": 0, "versions": []}

            resource_data = data[resource_type][resource_id]
            existing_versions = resource_data.get("versions", [])
            previous_current_version = resource_data.get("current_version", 0)
            if not isinstance(previous_current_version, int) or isinstance(previous_current_version, bool):
                previous_current_version = 0
            max_version = max(
                (item.get("version", 0) for item in existing_versions),
                default=0,
            )
            new_version = max_version + 1

            # 生成版本文件名和路径
            timestamp = self._generate_timestamp()
            ext = self.EXTENSIONS.get(resource_type, ".png")
            version_filename = f"{resource_id}_v{new_version}_{timestamp}{ext}"
            version_rel_path = f"versions/{resource_type}/{version_filename}"
            version_abs_path = self.project_path / version_rel_path

            # 如果有源文件，复制到版本目录
            if source_file and Path(source_file).exists():
                self._ensure_dirs()
                shutil.copy2(source_file, version_abs_path)

            # 创建版本记录
            version_record = {
                "version": new_version,
                "file": version_rel_path,
                "prompt": prompt,
                "created_at": self._generate_iso_timestamp(),
                **metadata,
                _PREVIOUS_CURRENT_VERSION: previous_current_version,
            }

            resource_data["versions"].append(version_record)
            resource_data["current_version"] = new_version

            self._save_versions(data)
            return new_version

    def commit_staged_version(
        self,
        resource_type: str,
        resource_id: str,
        prompt: str,
        *,
        staged_file: Path,
        current_file: Path,
        on_commit: Callable[[], None] | None = None,
        **metadata,
    ) -> int:
        """Atomically activate staged media with its version record.

        The caller may append one final synchronous registration through
        ``on_commit``.  If activation, metadata persistence, or that registration
        fails (including cancellation raised by the callback), the prior formal
        file and current-version pointer are restored.  An untracked prior formal
        file is first copied into history in the same transaction.
        """

        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")
        staged_file = Path(staged_file)
        current_file = Path(current_file)
        if not staged_file.is_file():
            raise FileNotFoundError(f"staged version file does not exist: {staged_file}")

        with self._lock:
            versions_existed = self.versions_file.is_file()
            versions_snapshot = self.versions_file.read_bytes() if versions_existed else None
            data = self._load_versions()
            bucket = data.setdefault(resource_type, {})
            resource_data = bucket.setdefault(resource_id, {"current_version": 0, "versions": []})
            records = resource_data.setdefault("versions", [])
            created_snapshots: list[Path] = []
            current_backup: Path | None = None
            activation_succeeded = False

            def _append_version(source: Path, version_prompt: str, version_metadata: dict) -> int:
                previous_current_version = resource_data.get("current_version", 0)
                if not isinstance(previous_current_version, int) or isinstance(previous_current_version, bool):
                    previous_current_version = 0
                new_version = max((item.get("version", 0) for item in records), default=0) + 1
                timestamp = self._generate_timestamp()
                ext = self.EXTENSIONS.get(resource_type, ".png")
                filename = f"{resource_id}_v{new_version}_{timestamp}{ext}"
                rel_path = f"versions/{resource_type}/{filename}"
                abs_path = self.project_path / rel_path
                self._ensure_dirs()
                shutil.copy2(source, abs_path)
                created_snapshots.append(abs_path)
                records.append(
                    {
                        "version": new_version,
                        "file": rel_path,
                        "prompt": version_prompt,
                        "created_at": self._generate_iso_timestamp(),
                        **version_metadata,
                        _PREVIOUS_CURRENT_VERSION: previous_current_version,
                    }
                )
                resource_data["current_version"] = new_version
                return new_version

            try:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                if current_file.is_file():
                    fd, backup_name = tempfile.mkstemp(
                        prefix=f".{current_file.stem}.",
                        suffix=f"{current_file.suffix}.rollback",
                        dir=current_file.parent,
                    )
                    os.close(fd)
                    current_backup = Path(backup_name)
                    shutil.copy2(current_file, current_backup)
                    if not resource_data.get("current_version"):
                        _append_version(current_file, "", {})

                new_version = _append_version(staged_file, prompt, metadata)
                os.replace(staged_file, current_file)
                self._save_versions(data)
                if on_commit is not None:
                    on_commit()
                activation_succeeded = True
                return new_version
            except BaseException as failure:
                rollback_errors: list[OSError] = []
                try:
                    if current_backup is None:
                        current_file.unlink(missing_ok=True)
                    else:
                        os.replace(current_backup, current_file)
                except OSError as exc:
                    rollback_errors.append(exc)
                try:
                    if versions_snapshot is None:
                        self.versions_file.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(self.versions_file, versions_snapshot)
                except OSError as exc:
                    rollback_errors.append(exc)
                for path in created_snapshots:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as exc:
                        rollback_errors.append(exc)
                if rollback_errors:
                    rollback_errors[0].__cause__ = failure
                    raise RuntimeError(
                        "version activation failed and durable rollback was incomplete"
                    ) from rollback_errors[0]
                raise
            finally:
                if activation_succeeded:
                    _report_cleanup_failures(_unlink_paths(current_backup), active_failure=sys.exception())

    def commit_staged_paid_version(
        self,
        resource_type: str,
        resource_id: str,
        prompt: str,
        *,
        staged_file: Path,
        current_file: Path,
        select_current: bool | Callable[[], bool],
        expected_current_version: int | None = None,
        on_select: Callable[[], None] | None = None,
        **metadata,
    ) -> PaidVersionCommit:
        """Record paid output and decide current selection inside one version lock.

        A non-selected result is copied into history without changing the formal
        file or current pointer. A callable selection guard runs after the paid
        snapshot is durable and while the version lock remains held. When
        selection is requested, the formal file, pointer, and ``on_select``
        registration form one guarded transition. A selection failure restores
        the old formal selection while retaining the paid result as a
        non-current historical version.
        """

        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")
        if not isinstance(select_current, bool) and not callable(select_current):
            raise TypeError("select_current must be a boolean or a callable returning one")
        if expected_current_version is not None and (
            type(expected_current_version) is not int or expected_current_version < 0
        ):
            raise ValueError("expected_current_version must be a non-negative integer or null")
        staged_file = Path(staged_file)
        current_file = Path(current_file)
        if not staged_file.is_file():
            raise FileNotFoundError(f"staged version file does not exist: {staged_file}")

        with self._lock:
            versions_existed = self.versions_file.is_file()
            versions_snapshot = self.versions_file.read_bytes() if versions_existed else None
            data = self._load_versions()
            bucket = data.setdefault(resource_type, {})
            resource_data = bucket.setdefault(resource_id, {"current_version": 0, "versions": []})
            records = resource_data.setdefault("versions", [])
            created_snapshots: list[Path] = []

            def _append_snapshot(source: Path, version_prompt: str, version_metadata: dict) -> int:
                previous = resource_data.get("current_version", 0)
                if not isinstance(previous, int) or isinstance(previous, bool) or previous < 0:
                    previous = 0
                version = max((item.get("version", 0) for item in records), default=0) + 1
                timestamp = self._generate_timestamp()
                ext = self.EXTENSIONS.get(resource_type, ".png")
                rel_path = f"versions/{resource_type}/{resource_id}_v{version}_{timestamp}{ext}"
                abs_path = self.project_path / rel_path
                self._ensure_dirs()
                shutil.copy2(source, abs_path)
                created_snapshots.append(abs_path)
                records.append(
                    {
                        "version": version,
                        "file": rel_path,
                        "prompt": version_prompt,
                        "created_at": self._generate_iso_timestamp(),
                        **version_metadata,
                        _PREVIOUS_CURRENT_VERSION: previous,
                    }
                )
                return version

            try:
                submitted_current = resource_data.get("current_version", 0)
                if (
                    not isinstance(submitted_current, int)
                    or isinstance(submitted_current, bool)
                    or submitted_current < 0
                ):
                    submitted_current = 0
                if current_file.is_file() and not resource_data.get("current_version"):
                    tracked = _append_snapshot(current_file, "", {})
                    resource_data["current_version"] = tracked
                prior_current = resource_data.get("current_version", 0)
                if not isinstance(prior_current, int) or isinstance(prior_current, bool) or prior_current < 0:
                    prior_current = 0
                    resource_data["current_version"] = 0
                version = _append_snapshot(staged_file, prompt, metadata)
                # Persist paid history before any selection callback can fail.
                self._save_versions(data)
            except BaseException as failure:
                history_rollback_errors: list[OSError] = []
                try:
                    if versions_snapshot is None:
                        self.versions_file.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(self.versions_file, versions_snapshot)
                except OSError as exc:
                    history_rollback_errors.append(exc)
                history_rollback_errors.extend(
                    cleanup_failure for _path, cleanup_failure in _unlink_paths(*created_snapshots, staged_file)
                )
                if history_rollback_errors:
                    history_rollback_errors[0].__cause__ = failure
                    raise RuntimeError(
                        "paid version history commit failed and rollback was incomplete"
                    ) from history_rollback_errors[0]
                raise

            try:
                should_select = (
                    False
                    if expected_current_version is not None and submitted_current != expected_current_version
                    else select_current()
                    if callable(select_current)
                    else select_current
                )
                if not isinstance(should_select, bool):
                    raise TypeError("select_current callback must return a boolean")
            except BaseException as failure:
                _report_cleanup_failures(_unlink_paths(staged_file), active_failure=failure)
                raise

            if not should_select:
                _report_cleanup_failures(_unlink_paths(staged_file), active_failure=None)
                return PaidVersionCommit(version=version, selected=False)

            current_backup: Path | None = None
            selection_succeeded = False
            try:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                if current_file.is_file():
                    fd, backup_name = tempfile.mkstemp(
                        prefix=f".{current_file.stem}.",
                        suffix=f"{current_file.suffix}.rollback",
                        dir=current_file.parent,
                    )
                    os.close(fd)
                    current_backup = Path(backup_name)
                    shutil.copy2(current_file, current_backup)
                os.replace(staged_file, current_file)
                resource_data["current_version"] = version
                self._save_versions(data)
                if on_select is not None:
                    on_select()
                selection_succeeded = True
                return PaidVersionCommit(version=version, selected=True)
            except BaseException as failure:
                selection_rollback_errors: list[OSError] = []
                try:
                    if current_backup is None:
                        current_file.unlink(missing_ok=True)
                    else:
                        os.replace(current_backup, current_file)
                except OSError as exc:
                    selection_rollback_errors.append(exc)
                resource_data["current_version"] = prior_current
                try:
                    self._save_versions(data)
                except OSError as exc:
                    selection_rollback_errors.append(exc)
                if selection_rollback_errors:
                    selection_rollback_errors[0].__cause__ = failure
                    raise RuntimeError(
                        "paid version selection failed and durable rollback was incomplete"
                    ) from selection_rollback_errors[0]
                raise
            finally:
                cleanup_failures = _unlink_paths(staged_file, current_backup if selection_succeeded else None)
                _report_cleanup_failures(cleanup_failures, active_failure=sys.exception())

    def reject_current_version(
        self,
        resource_type: str,
        resource_id: str,
        *,
        rejected_version: int,
        restore_version: int | None = None,
        current_file: Path,
        on_reject: Callable[[], None] | None = None,
    ) -> bool:
        """Keep a rejected paid result in history while restoring its prior selection.

        New records retain the current-version coordinate that was selected when
        they were appended.  This matters when a user restored an older version
        before regenerating: numeric adjacency is not the same as the selected
        predecessor.  ``restore_version`` may override that recorded coordinate
        for callers that captured it explicitly.

        ``on_reject`` extends the rollback boundary to a final synchronous
        registration update. If it raises, media and the version pointer return
        to the rejected result.

        """

        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")
        current_file = Path(current_file)
        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)
            if not isinstance(resource_data, dict) or resource_data.get("current_version") != rejected_version:
                return False
            records = resource_data.get("versions")
            if not isinstance(records, list):
                return False
            rejected_record = next(
                (
                    record
                    for record in records
                    if isinstance(record, dict) and record.get("version") == rejected_version
                ),
                None,
            )
            if rejected_record is None:
                return False
            if restore_version is None:
                recorded_previous = rejected_record.get(_PREVIOUS_CURRENT_VERSION)
                if (
                    isinstance(recorded_previous, int)
                    and not isinstance(recorded_previous, bool)
                    and recorded_previous >= 0
                ):
                    restore_version = recorded_previous
                else:
                    legacy_previous = max(
                        (
                            record["version"]
                            for record in records
                            if isinstance(record, dict)
                            and isinstance(record.get("version"), int)
                            and not isinstance(record.get("version"), bool)
                            and record["version"] < rejected_version
                        ),
                        default=0,
                    )
                    restore_version = legacy_previous
            if not isinstance(restore_version, int) or isinstance(restore_version, bool) or restore_version < 0:
                raise ValueError("restore_version must be a non-negative integer")
            previous = next(
                (record for record in records if isinstance(record, dict) and record.get("version") == restore_version),
                None,
            )
            if restore_version > 0 and previous is None:
                raise ValueError(f"restore version does not exist: {restore_version}")
            versions_snapshot = self.versions_file.read_bytes()
            current_backup: Path | None = None
            replacement: Path | None = None
            rejection_succeeded = False
            try:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                if current_file.is_file():
                    fd, backup_name = tempfile.mkstemp(
                        prefix=f".{current_file.stem}.",
                        suffix=f"{current_file.suffix}.rollback",
                        dir=current_file.parent,
                    )
                    os.close(fd)
                    current_backup = Path(backup_name)
                    shutil.copy2(current_file, current_backup)
                if previous is None:
                    current_file.unlink(missing_ok=True)
                    resource_data["current_version"] = 0
                else:
                    previous_file = self.project_path / previous["file"]
                    if not previous_file.is_file():
                        raise FileNotFoundError(f"版本文件不存在: {previous_file}")
                    fd, replacement_name = tempfile.mkstemp(
                        prefix=f".{current_file.stem}.",
                        suffix=current_file.suffix,
                        dir=current_file.parent,
                    )
                    os.close(fd)
                    replacement = Path(replacement_name)
                    shutil.copy2(previous_file, replacement)
                    os.replace(replacement, current_file)
                    replacement = None
                    resource_data["current_version"] = previous["version"]
                self._save_versions(data)
                if on_reject is not None:
                    on_reject()
                rejection_succeeded = True
                return True
            except BaseException as failure:
                rollback_errors: list[OSError] = []
                try:
                    if current_backup is None:
                        current_file.unlink(missing_ok=True)
                    else:
                        os.replace(current_backup, current_file)
                except OSError as exc:
                    rollback_errors.append(exc)
                try:
                    atomic_write_bytes(self.versions_file, versions_snapshot)
                except OSError as exc:
                    rollback_errors.append(exc)
                if rollback_errors:
                    rollback_errors[0].__cause__ = failure
                    raise RuntimeError(
                        "version rejection failed and durable rollback was incomplete"
                    ) from rollback_errors[0]
                raise
            finally:
                cleanup_failures = _unlink_paths(
                    replacement,
                    current_backup if rejection_succeeded else None,
                )
                _report_cleanup_failures(cleanup_failures, active_failure=sys.exception())

    def rename_resource(self, resource_type: str, old_id: str, new_id: str, *, dry_run: bool = False) -> int:
        """把资源的版本历史整体迁移到新 id：re-key 元数据、重命名快照文件、改写记录内路径。

        资产重命名的版本时光机迁移入口。resource_id 与快照文件名都以资产名为前缀
        （``{name}_v{n}_{timestamp}{ext}``），名字判等走比对坐标系（NFC）——存量记录
        与文件名可能以 NFD 落盘。返回涉及的快照文件数；旧 id 无版本记录时返回 0
        （幂等：级联事务中途失败后重跑安全）。``dry_run=True`` 只统计、不迁移。

        新 id 下已有他人的版本历史时整体拒绝：资产删除只删资产桶 key、版本记录与快照留存，
        迁移过去会不可恢复地覆盖它。拒绝发生在 ``dry_run`` 分支之前，因此级联事务的扫描
        阶段就能拦下，零写入承诺不破。

        Raises:
            AssetRenameHistoryCollisionError: 新 id 下已有属于别的资产的版本历史。
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        from lib.asset_rename import AssetRenameHistoryCollisionError
        from lib.asset_types import normalize_asset_name, rekey_equivalent_entries, resolve_asset_key

        with self._lock:
            data = self._load_versions()
            bucket = data.get(resource_type)
            if not isinstance(bucket, dict):
                return 0
            key = resolve_asset_key(bucket, old_id)
            if key is None or not isinstance(bucket.get(key), dict):
                return 0
            record = bucket[key]
            # 解析到 key 自身说明只是编码形式或大小写变化，那是同一份历史，放行。
            existing_new_key = resolve_asset_key(bucket, new_id)
            if existing_new_key is not None and existing_new_key != key:
                raise AssetRenameHistoryCollisionError(new_id)
            versions = [v for v in record.get("versions", []) if isinstance(v, dict) and isinstance(v.get("file"), str)]
            if dry_run:
                return len(versions)

            self._ensure_dirs()
            prefix = f"{normalize_asset_name(old_id)}_v"
            for version in versions:
                basename = normalize_asset_name(PurePosixPath(version["file"].replace("\\", "/")).name)
                if not basename.startswith(prefix):
                    continue
                new_basename = f"{new_id}_v{basename[len(prefix) :]}"
                src = self.project_path / version["file"]
                dst = self.versions_dir / resource_type / new_basename
                if src.exists():
                    src.replace(dst)
                version["file"] = f"versions/{resource_type}/{new_basename}"
            # 视觉同名的等价 key（NFC / NFD 并存）一并收编到新 id 下，留一条会顶着旧名残留、
            # 之后被重建的同名资产接上。被合并掉的那条记录，其快照文件会留在盘上成为孤儿——
            # 与资产删除只删桶 key、快照留存是同一口径，宁可留下也不静默删除用户的历史。
            rekey_equivalent_entries(bucket, old_id, new_id)
            self._save_versions(data)
            return len(versions)

    def backup_current(
        self, resource_type: str, resource_id: str, current_file: Path, prompt: str, **metadata
    ) -> int | None:
        """
        将当前文件备份到版本目录

        如果当前文件不存在，不执行任何操作。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            current_file: 当前文件路径
            prompt: 当前版本的 prompt
            **metadata: 额外的元数据

        Returns:
            备份的版本号，如果未备份则返回 None
        """
        current_file = Path(current_file)
        if not current_file.exists():
            return None

        return self.add_version(
            resource_type=resource_type, resource_id=resource_id, prompt=prompt, source_file=current_file, **metadata
        )

    def ensure_current_tracked(
        self, resource_type: str, resource_id: str, current_file: Path, prompt: str, **metadata
    ) -> int | None:
        """
        确保“当前文件”至少有一个版本记录

        用于升级/迁移场景：磁盘上已有 current_file，但 versions.json 还没有记录。
        若该资源已存在版本记录（current_version > 0）则不会重复写入。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            current_file: 当前文件路径
            prompt: 当前文件对应的 prompt（用于记录）
            **metadata: 额外元数据

        Returns:
            新增的版本号；若无需新增或文件不存在则返回 None
        """
        current_file = Path(current_file)
        if not current_file.exists():
            return None

        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        with self._lock:
            if self.get_current_version(resource_type, resource_id) > 0:
                return None
            return self.add_version(
                resource_type=resource_type,
                resource_id=resource_id,
                prompt=prompt,
                source_file=current_file,
                **metadata,
            )

    def restore_version(
        self,
        resource_type: str,
        resource_id: str,
        version: int,
        current_file: Path,
        *,
        on_restore: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict:
        """
        切换到指定版本

        将指定版本复制到当前路径，并将 current_version 指向该版本。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 要还原的版本号
            current_file: 当前文件路径

        Returns:
            切换信息，包含 restored_version, current_version, prompt
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise BadRequestError("unsupported_resource_type", resource_type=resource_type)

        current_file = Path(current_file)

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)

            if not resource_data:
                raise NotFoundError("version_resource_not_found", resource_type=resource_type, resource_id=resource_id)

            target_version = None
            for v in resource_data["versions"]:
                if v["version"] == version:
                    target_version = v
                    break

            if not target_version:
                raise NotFoundError("version_not_found", version=version)

            target_file = self.project_path / target_version["file"]
            if not target_file.exists():
                raise FileNotFoundError(f"版本文件不存在: {target_file}")

            versions_existed = self.versions_file.is_file()
            versions_snapshot = self.versions_file.read_bytes() if versions_existed else None
            current_backup: Path | None = None
            restore_succeeded = False
            try:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                if current_file.is_file():
                    fd, backup_name = tempfile.mkstemp(
                        prefix=f".{current_file.stem}.",
                        suffix=f"{current_file.suffix}.rollback",
                        dir=current_file.parent,
                    )
                    os.close(fd)
                    current_backup = Path(backup_name)
                    shutil.copy2(current_file, current_backup)
                shutil.copy2(target_file, current_file)
                resource_data["current_version"] = version
                self._save_versions(data)
                if on_restore is not None:
                    on_restore(dict(target_version))
                restore_succeeded = True
            except BaseException as failure:
                rollback_errors: list[OSError] = []
                try:
                    if current_backup is None:
                        current_file.unlink(missing_ok=True)
                    else:
                        os.replace(current_backup, current_file)
                except OSError as exc:
                    rollback_errors.append(exc)
                try:
                    if versions_snapshot is None:
                        self.versions_file.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(self.versions_file, versions_snapshot)
                except OSError as exc:
                    rollback_errors.append(exc)
                if rollback_errors:
                    rollback_errors[0].__cause__ = failure
                    raise RuntimeError(
                        "version restore failed and durable rollback was incomplete"
                    ) from rollback_errors[0]
                raise
            finally:
                if restore_succeeded:
                    _report_cleanup_failures(_unlink_paths(current_backup), active_failure=sys.exception())

        restored_prompt = target_version.get("prompt", "")
        return {
            "restored_version": version,
            "current_version": version,
            "prompt": restored_prompt,
        }

    def update_version_metadata(self, resource_type: str, resource_id: str, version: int, **metadata) -> bool:
        """为既有版本记录补写元数据键（覆盖同名键）。

        供生成 finalize 在版本入库后回填只有 finalize 阶段才确定的元数据（如参考
        单元产物的来源签名）。目标记录不存在时返回 False 不抛错：档案补写是
        best-effort，缺失只降级为将来还原时拿不到该元数据。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 版本号
            **metadata: 要写入的元数据键值

        Returns:
            是否找到记录并写入
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)
            if not resource_data:
                return False
            for record in resource_data.get("versions", []):
                if record.get("version") == version:
                    record.update(metadata)
                    self._save_versions(data)
                    return True
            return False

    def get_version_metadata(self, resource_type: str, resource_id: str, version: int, key: str) -> Any | None:
        """读取指定版本记录上的元数据键，记录或键不存在时返回 None。"""
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get(key)
        return None

    def get_version_file_url(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """
        获取指定版本的文件 URL

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 版本号

        Returns:
            文件 URL，不存在时返回 None
        """
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get("file_url")
        return None

    def get_version_prompt(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """
        获取指定版本的 prompt

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 版本号

        Returns:
            prompt 文本，不存在时返回 None
        """
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get("prompt")
        return None

    def get_version_created_at(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """
        获取指定版本的入库时间（ISO8601）

        还原历史版本时用于把该版本的原始生成时间写回 generated_assets，而不是戳成
        「现在」——还原回来的是旧内容，声音等派生判定须按旧时间成立。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 版本号

        Returns:
            ISO8601 时间戳，不存在时返回 None
        """
        value = self.get_version_metadata(resource_type, resource_id, version, "created_at")
        return value if isinstance(value, str) else None

    def has_versions(self, resource_type: str, resource_id: str) -> bool:
        """
        检查资源是否有版本记录

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID

        Returns:
            是否有版本记录
        """
        return self.get_current_version(resource_type, resource_id) > 0
