"""Version management.

Manage historical storyboard, video, character, scene-sheet, prop-sheet, and grid-image versions.
Support version backups, current-version switching, recording, and queries."""

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable, Collection, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, ClassVar, assert_never

from lib.artifacts.formal_write import formal_write_transaction
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError
from lib.infra.content_digest import sha256_file
from lib.infra.json_io import atomic_write_bytes, atomic_write_json
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.schema_guards import is_bool
from lib.material.storage import (
    CONTENT_DIGEST,
    CONTENT_VERSION_ID,
    STORAGE,
    ContentVersion,
    LocalLocator,
    LocalMaterialStorage,
    MaterialRef,
    ObjectLocator,
    PersistedContent,
    active_new_content_storage,
    new_content_version_id,
    verify_content,
)
from lib.project.asset_types import resolve_asset_key
from lib.project.project_activity import record_project_activity
from lib.project.resource_paths import RESOURCE_TYPES as _RESOURCE_TYPES
from lib.project.resource_paths import (
    resource_extension,
    resource_id_segments,
    version_snapshot_dir,
    version_snapshot_relative_path,
)
from lib.project.task_project_claim import ensure_task_project_claim

if TYPE_CHECKING:
    from lib.project.project_trash import TrashRecord

_LOCKS_GUARD = threading.Lock()
_LOCKS_BY_VERSIONS_FILE: dict[str, threading.RLock] = {}
_PREVIOUS_CURRENT_VERSION = "_previous_current_version"
MANUAL_UPLOAD_VERSION_SOURCE = "manual_upload"
# Bare sha256 hexdigest of this version's output snapshot, the content-version digest; see ``lib.material.storage``.
VERSION_CONTENT_DIGEST = CONTENT_DIGEST
logger = logging.getLogger(__name__)


class UnmanagedSnapshotPathError(BadRequestError):
    """A version record points outside its typed history bucket; the operation is refused."""

    def __init__(self, resource_type: str) -> None:
        super().__init__("version_snapshot_path_unmanaged", resource_type=resource_type)


def _content_version_of(resource_type: str, resource_id: str, record: Mapping[str, Any]) -> ContentVersion:
    """Read a version record as a content version; callers must already verify ``file`` is a managed snapshot path."""

    content_version_id = record.get(CONTENT_VERSION_ID)
    digest = record.get(CONTENT_DIGEST)
    return ContentVersion(
        material=MaterialRef(resource_type, resource_id),
        version=record["version"],
        id=content_version_id if isinstance(content_version_id, str) else None,
        digest=digest if isinstance(digest, str) else None,
        locator=LocalLocator(record["file"]),
        stored=ObjectLocator.from_record(record.get(STORAGE)),
    )


def verify_version_snapshot(resource_type: str, resource_id: str, record: Mapping[str, Any], snapshot: Path) -> None:
    """Check a version snapshot against its recorded content digest before the snapshot is used.

    shortcut: a record without a digest is used unverified. Every version write records one; the
    schema 18 migration backfilled selected video and audio versions, so the records lacking one are
    image versions written before content versions and older unselected media versions.
    ``VersionManager.ensure_content_version`` records it for one of them once it is referenced.
    """

    verify_content(_content_version_of(resource_type, resource_id, record), snapshot)


def _new_version_record(
    *,
    version: int,
    rel_path: str,
    digest: str | None,
    prompt: str,
    created_at: str,
    metadata: Mapping[str, Any],
    previous_current_version: int,
    content_version_id: str | None = None,
    stored: ObjectLocator | None = None,
) -> dict[str, Any]:
    """Build one version record: a content version with its identity, digest and persistent locator.

    A fresh identity is minted unless ``content_version_id`` carries one forward (a version restored
    from the trash keeps its identity); identity, digest and storage keys in ``metadata`` never override
    them. ``digest`` is ``None`` only for a record without a persisted snapshot. ``stored`` records a
    remote persistent locator; without it ``rel_path`` is the local one.
    """

    record = {"version": version, "file": rel_path, "prompt": prompt, "created_at": created_at, **metadata}
    record[CONTENT_VERSION_ID] = content_version_id or new_content_version_id()
    record.pop(CONTENT_DIGEST, None)
    record.pop(STORAGE, None)
    if digest is not None:
        record[CONTENT_DIGEST] = digest
    if stored is not None:
        record[STORAGE] = stored.to_record()
    record[_PREVIOUS_CURRENT_VERSION] = previous_current_version
    return record


@dataclass(frozen=True, slots=True)
class PaidVersionCommit:
    """Outcome of recording paid media and optionally selecting it as current."""

    version: int
    selected: bool


@dataclass(frozen=True, slots=True)
class StagedVersionCommit:
    """One media selection participating in a multi-resource version commit."""

    resource_type: str
    resource_id: str
    prompt: str
    staged_file: Path
    current_file: Path
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class InstalledVersionCommit:
    """One already-installed formal file participating in a multi-resource version commit."""

    resource_type: str
    resource_id: str
    prompt: str
    current_file: Path
    metadata: Mapping[str, Any]


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
            # hard-delete: temp -- snapshots removed after staging/rollback/restore failure; data deletion always uses project trash
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


def _install_snapshot(snapshot: Path, current_file: Path) -> None:
    """Copy a version snapshot over the formal file via a durable temporary file and one atomic replace.

    An interruption leaves the formal file either wholly old or wholly restored, never half written.
    """

    fd, replacement_name = tempfile.mkstemp(
        prefix=f".{current_file.stem}.", suffix=current_file.suffix, dir=current_file.parent
    )
    os.close(fd)
    replacement = Path(replacement_name)
    try:
        shutil.copy2(snapshot, replacement)
        with replacement.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(replacement, current_file)
    except BaseException as failure:
        _report_cleanup_failures(_unlink_paths(replacement), active_failure=failure)
        raise


def _create_rollback_backup(current_file: Path) -> Path:
    """Copy current media to a valid rollback file, removing an incomplete candidate on failure."""

    fd, backup_name = tempfile.mkstemp(
        prefix=f".{current_file.stem}.",
        suffix=f"{current_file.suffix}.rollback",
        dir=current_file.parent,
    )
    os.close(fd)
    candidate = Path(backup_name)
    try:
        shutil.copy2(current_file, candidate)
    except BaseException as failure:
        _report_cleanup_failures(_unlink_paths(candidate), active_failure=failure)
        raise
    return candidate


class VersionManager:
    """Version manager.

    Version-record reads/writes, commits, and file copies are synchronous disk operations holding a thread
    lock shared by versions.json. Async callers invoke them only in worker threads, never waiting for locks
    on the event loop. Construction itself does not touch the filesystem and may run on the event loop."""

    # Resource types/extensions derive from the sole source lib.project.resource_paths, avoiding copy drift.
    RESOURCE_TYPES = _RESOURCE_TYPES
    EXTENSIONS: ClassVar[dict[str, str]] = {rt: resource_extension(rt) for rt in _RESOURCE_TYPES}

    def __init__(self, project_path: Path):
        """Initialize the version manager.

        Args:
            project_path: Project root-directory path."""
        self.project_path = Path(project_path)
        self.versions_dir = self.project_path / "versions"
        self.versions_file = self.versions_dir / "versions.json"
        self._versions_lock: threading.RLock | None = None

    @property
    def _lock(self) -> threading.RLock:
        """Thread lock shared by canonical versions.json path.

        Resolving canonical paths requires stat; defer until first acquisition, already in a worker thread,
        keeping construction nonblocking."""
        lock = self._versions_lock
        if lock is None:
            lock = self._versions_lock = _get_versions_file_lock(self.versions_file)
        return lock

    def _ensure_dirs(self) -> None:
        """Ensure version-directory structure exists.

        Called on demand by writes, not ``__init__``: read-only use (including rename ``dry_run`` previews)
        must not leave an empty ``versions/`` tree on construction. If deletion occurred during task execution,
        abort writes here with ``ProjectDeletedDuringTaskError``, avoiding a new same-name project."""
        ensure_task_project_claim(self.project_path.name)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        for resource_type in self.RESOURCE_TYPES:
            # Snapshot buckets use the path authority; multisegment IDs (character derivatives) use nested directories.
            (self.project_path / version_snapshot_dir(resource_type)).mkdir(parents=True, exist_ok=True)

    def _persist_snapshot(
        self, resource_type: str, source: Path, rel_path: str, created_snapshots: list[Path]
    ) -> PersistedContent:
        """Save ``source`` as a new content version; return saved content digest and durable location.

        Default local material storage saves snapshot ``rel_path``. If formal commits specify new-content
        storage (``lib.material.storage.use_new_content_storage``), delegate and return only after remote
        confirmation. Register snapshot paths in ``created_snapshots`` first, letting callers' rollback remove
        partial snapshots on persistence failure."""

        created_snapshots.append(self.project_path / rel_path)
        self._ensure_dirs()
        storage = active_new_content_storage()
        if storage is not None:
            return storage.persist(self.project_path, resource_type, source, LocalLocator(rel_path))
        return PersistedContent(LocalMaterialStorage(self.project_path).persist(source, LocalLocator(rel_path)))

    def _retain_snapshot(
        self, source: Path, rel_path: str, created_snapshots: list[Path], stored: ObjectLocator, digest: str
    ) -> PersistedContent:
        """Restore remote content version ``stored``: write a local working file at the snapshot location, retaining original durable location when bytes match its recorded digest.

        Do not reupload or resave through formal-commit storage: this remains the same original object's
        content version. Mismatched bytes are not that object's content; save as a local version."""

        created_snapshots.append(self.project_path / rel_path)
        self._ensure_dirs()
        actual = LocalMaterialStorage(self.project_path).persist(source, LocalLocator(rel_path))
        return PersistedContent(actual, stored if actual == digest else None)

    @classmethod
    def is_managed_snapshot_path(cls, resource_type: str, relative_path: object) -> bool:
        """Whether a record points to a canonical file in its typed history bucket."""

        if (
            resource_type not in cls.RESOURCE_TYPES
            or not isinstance(relative_path, str)
            or "\\" in relative_path
            or "\x00" in relative_path
        ):
            return False
        path = PurePosixPath(relative_path)
        bucket = PurePosixPath(version_snapshot_dir(resource_type)).parts
        return (
            relative_path == path.as_posix()
            and not path.is_absolute()
            and len(path.parts) == len(bucket) + resource_id_segments(resource_type)
            and path.parts[: len(bucket)] == bucket
            and all(part not in {"", ".", ".."} for part in path.parts[len(bucket) :])
            and path.suffix == cls.EXTENSIONS[resource_type]
        )

    @classmethod
    def resolve_snapshot_path(cls, project_path: Path, resource_type: str, relative_path: object) -> Path:
        """Return the recorded snapshot path under ``project_path`` once it is known to be managed.

        The record must name a canonical file in its typed history bucket and must
        still resolve inside the project after symlinks are followed.  Anything
        else raises :class:`UnmanagedSnapshotPathError` before the filesystem is
        touched; the caller refuses the operation and leaves its data unchanged.
        The returned path is the recorded one, not its symlink target, so unlink
        and rename act on the snapshot entry itself.
        """

        if isinstance(relative_path, str) and cls.is_managed_snapshot_path(resource_type, relative_path):
            try:
                safe_join(project_path, relative_path)
            except PathTraversalError as exc:
                raise UnmanagedSnapshotPathError(resource_type) from exc
            return Path(project_path) / relative_path
        raise UnmanagedSnapshotPathError(resource_type)

    def _resolve_snapshot(self, resource_type: str, record: Mapping[str, Any]) -> Path:
        return self.resolve_snapshot_path(self.project_path, resource_type, record.get("file"))

    def _load_versions(self) -> dict:
        """Load version metadata."""
        if not self.versions_file.exists():
            return {rt: {} for rt in self.RESOURCE_TYPES}

        with open(self.versions_file, encoding="utf-8") as f:
            return json.load(f)

    def _save_versions(self, data: dict) -> None:
        """Save version metadata."""
        self._ensure_dirs()
        atomic_write_json(self.versions_file, data)

    def _generate_timestamp(self) -> str:
        """Generate timestamp strings for filenames."""
        return datetime.now().strftime("%Y%m%dT%H%M%S")

    def _generate_iso_timestamp(self) -> str:
        """Generate ISO timestamp strings for metadata."""
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_versions(self, resource_type: str, resource_id: str) -> dict:
        """Get all version information for a resource.

        Args:
            resource_type: Resource type (storyboards, videos, characters, clues).
            resource_id: Resource ID, e.g. E1S01, 姜月茴.

        Returns:
            Version-information dictionary containing current_version and a versions list."""
        if resource_type not in self.RESOURCE_TYPES:
            raise BadRequestError("unsupported_resource_type", resource_type=resource_type)

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)

            if not resource_data:
                return {"current_version": 0, "versions": []}

            # Add is_current and file_url fields
            versions = []
            for v in resource_data.get("versions", []):
                version_info = v.copy()
                version_info["is_current"] = v["version"] == resource_data["current_version"]
                version_info["file_url"] = f"/api/v1/files/{self.project_path.name}/{v['file']}"
                versions.append(version_info)

            return {"current_version": resource_data.get("current_version", 0), "versions": versions}

    def get_current_version(self, resource_type: str, resource_id: str) -> int:
        """Get the current version number.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.

        Returns:
            Current version number, 0 without versions."""
        info = self.get_versions(resource_type, resource_id)
        return info["current_version"]

    @contextmanager
    def locked_history(self) -> Generator[dict[str, Any]]:
        """Read full history under the version lock, read-only: readers verify records then modify working files under the lock without interleaving formal commits."""

        with self._lock:
            yield self._load_versions()

    @contextmanager
    def locked_version_snapshot(self, resource_type: str, resource_id: str) -> Generator[dict[str, Any]]:
        """Keep one resource selection stable while a dependent read-model commit runs."""

        with self._lock:
            yield self.get_versions(resource_type, resource_id)

    def add_version(
        self, resource_type: str, resource_id: str, prompt: str, source_file: Path | None = None, **metadata
    ) -> int:
        """Add a new version record.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.
            prompt: Prompt generating this version.
            source_file: Source-file path copied to the version directory.
            **metadata: Extra metadata, e.g. aspect_ratio, duration_seconds.

        Returns:
            New version number."""
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        with self._lock:
            data = self._load_versions()

            # Ensure resource type exists
            if resource_type not in data:
                data[resource_type] = {}

            # Get/create resource record
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

            # Generate version filename/path
            timestamp = self._generate_timestamp()
            version_rel_path = version_snapshot_relative_path(
                resource_type, resource_id, version=new_version, timestamp=timestamp
            )

            # Save source files as version snapshots when supplied
            persisted: PersistedContent | None = None
            if source_file and Path(source_file).exists():
                persisted = self._persist_snapshot(resource_type, Path(source_file), version_rel_path, [])

            # Create version record
            version_record = _new_version_record(
                version=new_version,
                rel_path=version_rel_path,
                digest=persisted.digest if persisted else None,
                stored=persisted.stored if persisted else None,
                prompt=prompt,
                created_at=self._generate_iso_timestamp(),
                metadata=metadata,
                previous_current_version=previous_current_version,
            )

            resource_data["versions"].append(version_record)
            resource_data["current_version"] = new_version

            self._save_versions(data)
            record_project_activity(self.project_path)
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
            created_snapshots: list[Path] = []
            current_backup: Path | None = None
            current_existed = current_file.is_file()
            activation_succeeded = False

            try:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                if current_existed:
                    current_backup = _create_rollback_backup(current_file)
                    if not resource_data.get("current_version"):
                        self._append_selected_snapshot(
                            resource_type, resource_id, resource_data, current_file, "", {}, created_snapshots
                        )

                new_version = self._append_selected_snapshot(
                    resource_type, resource_id, resource_data, staged_file, prompt, metadata, created_snapshots
                )
                os.replace(staged_file, current_file)
                self._save_versions(data)
                if on_commit is not None:
                    on_commit()
                activation_succeeded = True
                record_project_activity(self.project_path)
                return new_version
            except BaseException as failure:
                rollback_errors: list[OSError] = []
                try:
                    if current_backup is None:
                        if not current_existed:
                            # hard-delete: rollback -- remove current file created by this failed commit
                            current_file.unlink(missing_ok=True)
                    else:
                        os.replace(current_backup, current_file)
                except OSError as exc:
                    rollback_errors.append(exc)
                try:
                    if versions_snapshot is None:
                        # hard-delete: rollback -- remove versions.json created by this failed commit
                        self.versions_file.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(self.versions_file, versions_snapshot)
                except OSError as exc:
                    rollback_errors.append(exc)
                for path in created_snapshots:
                    try:
                        path.unlink(
                            missing_ok=True
                        )  # hard-delete: rollback -- remove snapshots created by this failed commit
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

    def _append_selected_snapshot(
        self,
        resource_type: str,
        resource_id: str,
        resource_data: dict,
        source: Path,
        prompt: str,
        metadata: dict,
        created_snapshots: list[Path],
        *,
        content_version_id: str | None = None,
        retained: tuple[ObjectLocator, str] | None = None,
    ) -> int:
        """Snapshot ``source`` into history as a new record and select it in ``resource_data``.

        The snapshot path is appended to ``created_snapshots`` before copying so
        the caller's rollback also removes a partially written snapshot. ``retained`` is the remote
        persistent locator and digest of a content version being added back (see :meth:`_retain_snapshot`).
        """

        records = resource_data.setdefault("versions", [])
        previous_current_version = resource_data.get("current_version", 0)
        if not isinstance(previous_current_version, int) or isinstance(previous_current_version, bool):
            previous_current_version = 0
        new_version = max((item.get("version", 0) for item in records), default=0) + 1
        rel_path = version_snapshot_relative_path(
            resource_type, resource_id, version=new_version, timestamp=self._generate_timestamp()
        )
        persisted = (
            self._persist_snapshot(resource_type, source, rel_path, created_snapshots)
            if retained is None
            else self._retain_snapshot(source, rel_path, created_snapshots, *retained)
        )
        records.append(
            _new_version_record(
                version=new_version,
                rel_path=rel_path,
                digest=persisted.digest,
                stored=persisted.stored,
                prompt=prompt,
                created_at=self._generate_iso_timestamp(),
                metadata=metadata,
                previous_current_version=previous_current_version,
                content_version_id=content_version_id,
            )
        )
        resource_data["current_version"] = new_version
        return new_version

    def commit_installed_version(
        self,
        resource_type: str,
        resource_id: str,
        prompt: str,
        *,
        current_file: Path,
        on_commit: Callable[[], None] | None = None,
        **metadata,
    ) -> int:
        """Record a formal file the caller already installed as the new selected version.

        Single-resource form of :meth:`commit_installed_versions`.
        """

        commit = InstalledVersionCommit(
            resource_type=resource_type,
            resource_id=resource_id,
            prompt=prompt,
            current_file=Path(current_file),
            metadata=metadata,
        )
        return self.commit_installed_versions((commit,), on_commit=on_commit)[(resource_type, resource_id)]

    def commit_installed_versions(
        self,
        commits: Sequence[InstalledVersionCommit],
        *,
        on_commit: Callable[[], None] | None = None,
    ) -> dict[tuple[str, str], int]:
        """Record formal files the caller already installed as new selected versions, all or none.

        The caller owns the formal bytes and their rollback; this method snapshots
        each of them into history, selects those versions, then runs ``on_commit``.
        If any snapshot, the metadata write, or ``on_commit`` fails, the version
        metadata and every new snapshot are restored to their prior state.  An
        empty batch returns ``{}`` without running ``on_commit``.
        """

        batch = tuple(commits)
        if not batch:
            return {}
        identities: set[tuple[str, str]] = set()
        for commit in batch:
            if commit.resource_type not in self.RESOURCE_TYPES:
                raise ValueError(f"不支持的资源类型: {commit.resource_type}")
            identity = (commit.resource_type, commit.resource_id)
            if identity in identities:
                raise ValueError(f"duplicate installed version identity: {identity!r}")
            identities.add(identity)
            if not Path(commit.current_file).is_file():
                raise FileNotFoundError(f"installed version file does not exist: {commit.current_file}")

        with self._lock:
            versions_snapshot = self.versions_file.read_bytes() if self.versions_file.is_file() else None
            data = self._load_versions()
            created_snapshots: list[Path] = []
            result: dict[tuple[str, str], int] = {}
            try:
                for commit in batch:
                    resource_data = data.setdefault(commit.resource_type, {}).setdefault(
                        commit.resource_id, {"current_version": 0, "versions": []}
                    )
                    result[(commit.resource_type, commit.resource_id)] = self._append_selected_snapshot(
                        commit.resource_type,
                        commit.resource_id,
                        resource_data,
                        Path(commit.current_file),
                        commit.prompt,
                        dict(commit.metadata),
                        created_snapshots,
                    )
                self._save_versions(data)
                if on_commit is not None:
                    on_commit()
                record_project_activity(self.project_path)
                return result
            except BaseException as failure:
                rollback_errors: list[OSError] = []
                try:
                    if versions_snapshot is None:
                        # hard-delete: rollback -- remove versions.json created by this failed commit
                        self.versions_file.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(self.versions_file, versions_snapshot)
                except OSError as exc:
                    rollback_errors.append(exc)
                rollback_errors.extend(cleanup_failure for _path, cleanup_failure in _unlink_paths(*created_snapshots))
                if rollback_errors:
                    rollback_errors[0].__cause__ = failure
                    raise RuntimeError(
                        "installed version commit failed and durable rollback was incomplete"
                    ) from rollback_errors[0]
                raise

    def commit_staged_versions(
        self,
        commits: Sequence[StagedVersionCommit],
        *,
        on_commit: Callable[[], None] | None = None,
    ) -> dict[tuple[str, str], int]:
        """Atomically select a batch of staged media files and version pointers.

        The complete ``versions.json`` plus every canonical file is restored if
        any activation or the final sidecar hook fails. Resource identities and
        canonical paths must be unique so rollback has one unambiguous owner.
        """

        batch = tuple(commits)
        if not batch:
            return {}
        identities: set[tuple[str, str]] = set()
        current_paths: set[Path] = set()
        for commit in batch:
            if commit.resource_type not in self.RESOURCE_TYPES:
                raise ValueError(f"不支持的资源类型: {commit.resource_type}")
            identity = (commit.resource_type, commit.resource_id)
            if identity in identities:
                raise ValueError(f"duplicate staged version identity: {identity!r}")
            identities.add(identity)
            staged_file = Path(commit.staged_file)
            current_file = Path(commit.current_file)
            if not staged_file.is_file():
                raise FileNotFoundError(f"staged version file does not exist: {staged_file}")
            normalized_current = current_file.resolve(strict=False)
            if normalized_current in current_paths:
                raise ValueError(f"duplicate staged version target: {current_file}")
            current_paths.add(normalized_current)

        with self._lock:
            versions_existed = self.versions_file.is_file()
            versions_snapshot = self.versions_file.read_bytes() if versions_existed else None
            data = self._load_versions()
            created_snapshots: list[Path] = []
            current_states: list[tuple[Path, bool, Path | None]] = []
            result: dict[tuple[str, str], int] = {}
            activation_succeeded = False

            def _append_version(
                *,
                commit: StagedVersionCommit,
                resource_data: dict[str, Any],
                source: Path,
                prompt: str,
                metadata: Mapping[str, Any],
            ) -> int:
                records = resource_data.setdefault("versions", [])
                previous = resource_data.get("current_version", 0)
                if not isinstance(previous, int) or isinstance(previous, bool) or previous < 0:
                    previous = 0
                version = max((item.get("version", 0) for item in records), default=0) + 1
                timestamp = self._generate_timestamp()
                rel_path = version_snapshot_relative_path(
                    commit.resource_type, commit.resource_id, version=version, timestamp=timestamp
                )
                persisted = self._persist_snapshot(commit.resource_type, source, rel_path, created_snapshots)
                records.append(
                    _new_version_record(
                        version=version,
                        rel_path=rel_path,
                        digest=persisted.digest,
                        stored=persisted.stored,
                        prompt=prompt,
                        created_at=self._generate_iso_timestamp(),
                        metadata=metadata,
                        previous_current_version=previous,
                    )
                )
                resource_data["current_version"] = version
                return version

            try:
                for commit in batch:
                    staged_file = Path(commit.staged_file)
                    current_file = Path(commit.current_file)
                    current_file.parent.mkdir(parents=True, exist_ok=True)
                    bucket = data.setdefault(commit.resource_type, {})
                    resource_data = bucket.setdefault(
                        commit.resource_id,
                        {"current_version": 0, "versions": []},
                    )
                    current_existed = current_file.is_file()
                    backup = _create_rollback_backup(current_file) if current_existed else None
                    current_states.append((current_file, current_existed, backup))
                    if current_existed and not resource_data.get("current_version"):
                        _append_version(
                            commit=commit,
                            resource_data=resource_data,
                            source=current_file,
                            prompt="",
                            metadata={},
                        )
                    version = _append_version(
                        commit=commit,
                        resource_data=resource_data,
                        source=staged_file,
                        prompt=commit.prompt,
                        metadata=commit.metadata,
                    )
                    os.replace(staged_file, current_file)
                    result[(commit.resource_type, commit.resource_id)] = version

                self._save_versions(data)
                if on_commit is not None:
                    on_commit()
                activation_succeeded = True
                record_project_activity(self.project_path)
                return result
            except BaseException as failure:
                rollback_errors: list[OSError] = []
                for current_file, current_existed, backup in reversed(current_states):
                    try:
                        if backup is None:
                            if not current_existed:
                                # hard-delete: rollback -- remove current file created by this failed commit
                                current_file.unlink(missing_ok=True)
                        else:
                            os.replace(backup, current_file)
                    except OSError as exc:
                        rollback_errors.append(exc)
                try:
                    if versions_snapshot is None:
                        # hard-delete: rollback -- remove versions.json created by this failed commit
                        self.versions_file.unlink(missing_ok=True)
                    else:
                        atomic_write_bytes(self.versions_file, versions_snapshot)
                except OSError as exc:
                    rollback_errors.append(exc)
                for path in created_snapshots:
                    try:
                        path.unlink(
                            missing_ok=True
                        )  # hard-delete: rollback -- remove snapshots created by this failed commit
                    except OSError as exc:
                        rollback_errors.append(exc)
                if rollback_errors:
                    rollback_errors[0].__cause__ = failure
                    raise RuntimeError(
                        "batch version activation failed and durable rollback was incomplete"
                    ) from rollback_errors[0]
                raise
            finally:
                if activation_succeeded:
                    _report_cleanup_failures(
                        _unlink_paths(*(backup for _path, _existed, backup in current_states)),
                        active_failure=sys.exception(),
                    )

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
            assert_never(select_current)
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
                rel_path = version_snapshot_relative_path(
                    resource_type, resource_id, version=version, timestamp=timestamp
                )
                persisted = self._persist_snapshot(resource_type, source, rel_path, created_snapshots)
                records.append(
                    _new_version_record(
                        version=version,
                        rel_path=rel_path,
                        digest=persisted.digest,
                        stored=persisted.stored,
                        prompt=version_prompt,
                        created_at=self._generate_iso_timestamp(),
                        metadata=version_metadata,
                        previous_current_version=previous,
                    )
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
                        # hard-delete: rollback -- remove versions.json created by this failed commit
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

            # Paid history is persisted; even if selection fails afterward, historical content was still added.
            record_project_activity(self.project_path)
            try:
                should_select = (
                    False
                    if expected_current_version is not None and submitted_current != expected_current_version
                    else select_current()
                    if callable(select_current)
                    else select_current
                )
                if not is_bool(should_select):
                    raise TypeError("select_current callback must return a boolean")
            except BaseException as failure:
                _report_cleanup_failures(_unlink_paths(staged_file), active_failure=failure)
                raise

            if not should_select:
                _report_cleanup_failures(_unlink_paths(staged_file), active_failure=None)
                return PaidVersionCommit(version=version, selected=False)

            current_backup: Path | None = None
            current_existed = current_file.is_file()
            selection_succeeded = False
            try:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                if current_existed:
                    current_backup = _create_rollback_backup(current_file)
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
                        if not current_existed:
                            # hard-delete: rollback -- remove current file created by this failed selection
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
        record_activity: bool = True,
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

        ``record_activity=False`` leaves the activity stamp to an enclosing
        batch, which records once after the whole batch commits.

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
            previous_file = self._resolve_snapshot(resource_type, previous) if previous is not None else None
            versions_snapshot = self.versions_file.read_bytes()
            current_backup: Path | None = None
            current_existed = current_file.is_file()
            rejection_succeeded = False
            try:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                if current_existed:
                    current_backup = _create_rollback_backup(current_file)
                if previous is None or previous_file is None:
                    # hard-delete: derived -- reject the sole version: its snapshot remains in history, current file is merely a copy
                    current_file.unlink(missing_ok=True)
                    resource_data["current_version"] = 0
                else:
                    if not previous_file.is_file():
                        raise FileNotFoundError(f"版本文件不存在: {previous_file}")
                    verify_version_snapshot(resource_type, resource_id, previous, previous_file)
                    _install_snapshot(previous_file, current_file)
                    resource_data["current_version"] = previous["version"]
                self._save_versions(data)
                if on_reject is not None:
                    on_reject()
                rejection_succeeded = True
                if record_activity:
                    record_project_activity(self.project_path)
                return True
            except BaseException as failure:
                rollback_errors: list[OSError] = []
                try:
                    if current_backup is None:
                        if not current_existed:
                            # hard-delete: rollback -- remove current file created by this failed rejection
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
                cleanup_failures = _unlink_paths(current_backup if rejection_succeeded else None)
                _report_cleanup_failures(cleanup_failures, active_failure=sys.exception())

    def reject_current_versions(
        self,
        rejections: Mapping[tuple[str, str], tuple[int, Path]],
        *,
        on_reject: Callable[[frozenset[tuple[str, str]]], None] | None = None,
    ) -> frozenset[tuple[str, str]]:
        """Atomically reject the requested selections that are still current."""

        with self._lock:
            data = self._load_versions()
            current = frozenset(
                identity
                for identity, (version, _path) in rejections.items()
                if isinstance(data.get(identity[0], {}).get(identity[1]), dict)
                and data[identity[0]][identity[1]].get("current_version") == version
            )
            if not current:
                return current
            # The ledger is project-global and excluded from rollback: individual rejections do not record activity; record once after batch commit.
            with formal_write_transaction(
                self.versions_file,
                *(Path(rejections[identity][1]) for identity in current),
            ):
                for resource_type, resource_id in sorted(current):
                    version, current_file = rejections[(resource_type, resource_id)]
                    if not self.reject_current_version(
                        resource_type,
                        resource_id,
                        rejected_version=version,
                        current_file=current_file,
                        record_activity=False,
                    ):
                        raise RuntimeError("version selection changed during batch rejection")
                if on_reject is not None:
                    on_reject(current)
            record_project_activity(self.project_path)
            return current

    def rename_resource(self, resource_type: str, old_id: str, new_id: str, *, dry_run: bool = False) -> int:
        """Move a resource's entire version history to a new ID: rekey metadata, rename snapshots, and rewrite record paths.

        Version time-machine migration entry for asset rename. resource_id and snapshot filenames use asset-name
        prefixes (``{name}_v{n}_{timestamp}{ext}``). Compare names in NFC coordinates since existing records/files
        may be NFD. Return affected snapshot count, 0 without old-ID records (idempotent, safe after interrupted
        cascade transactions). ``dry_run=True`` counts only without migration.

        Reject the whole operation if another asset's history exists under the new ID. Records/snapshots may
        remain without asset-table entries, and migrating there would irrecoverably overwrite them. Reject
        before ``dry_run`` so cascade scanning catches conflicts without breaking zero-write guarantees.

        Raises:
            AssetRenameHistoryCollisionError: New-ID version history belongs to another asset."""
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        from lib.project.asset_rename import AssetRenameHistoryCollisionError
        from lib.project.asset_types import normalize_asset_name, rekey_equivalent_entries

        with self._lock:
            data = self._load_versions()
            bucket = data.get(resource_type)
            if not isinstance(bucket, dict):
                return 0
            key = resolve_asset_key(bucket, old_id)
            if key is None or not isinstance(bucket.get(key), dict):
                return 0
            record = bucket[key]
            # Resolving to the key itself means only encoding/case changed, so this is the same history and is allowed.
            existing_new_key = resolve_asset_key(bucket, new_id)
            if existing_new_key is not None and existing_new_key != key:
                raise AssetRenameHistoryCollisionError(new_id)
            versions = [v for v in record.get("versions", []) if isinstance(v, dict) and isinstance(v.get("file"), str)]
            sources = [self._resolve_snapshot(resource_type, version) for version in versions]
            if dry_run:
                return len(versions)

            self._ensure_dirs()
            # Snapshot filenames use only the final ID segment (preceding segments are directories), so prefixes/new names use that segment.
            old_stem = normalize_asset_name(old_id).rpartition("/")[2]
            new_parent, _, new_stem = new_id.rpartition("/")
            bucket_dir = self.project_path / version_snapshot_dir(resource_type)
            new_dir = bucket_dir / new_parent if new_parent else bucket_dir
            prefix = f"{old_stem}_v"
            for version, src in zip(versions, sources, strict=True):
                basename = normalize_asset_name(PurePosixPath(version["file"]).name)
                if not basename.startswith(prefix):
                    continue
                new_basename = f"{new_stem}_v{basename[len(prefix) :]}"
                dst = new_dir / new_basename
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    src.replace(dst)
                version["file"] = dst.relative_to(self.project_path).as_posix()
            # Adopt visually equivalent NFC/NFD keys into the new ID together; leaving one under the old name would attach its history
            # to a future recreated same-name asset. Leave merged-record snapshots on disk to avoid silently deleting user history.
            rekey_equivalent_entries(bucket, old_id, new_id)
            self._save_versions(data)
            record_project_activity(self.project_path)
            return len(versions)

    def backup_current(
        self, resource_type: str, resource_id: str, current_file: Path, prompt: str, **metadata
    ) -> int | None:
        """Back up the current file to the version directory.

        Do nothing if the current file is absent.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.
            current_file: Current-file path.
            prompt: Current version prompt.
            **metadata: Extra metadata.

        Returns:
            Backup version number, None if not backed up."""
        current_file = Path(current_file)
        if not current_file.exists():
            return None

        return self.add_version(
            resource_type=resource_type, resource_id=resource_id, prompt=prompt, source_file=current_file, **metadata
        )

    def ensure_current_tracked(
        self, resource_type: str, resource_id: str, current_file: Path, prompt: str, **metadata
    ) -> int | None:
        """Ensure the current file has at least one version record.

        For upgrades/migrations with current_file on disk but no versions.json records. Existing resource
        records (current_version > 0) prevent duplicate writes.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.
            current_file: Current-file path.
            prompt: Prompt corresponding to current file, for recording.
            **metadata: Extra metadata.

        Returns:
            Added version number, None if unnecessary or the file is absent."""
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

    @contextmanager
    def history_transaction(self) -> Generator[None]:
        """Undo added history snapshots/version registrations on bulk-restore failure; callers hold the project lock, leaving current files unchanged."""

        def snapshots() -> set[Path]:
            data = self._load_versions()
            return {
                self._resolve_snapshot(resource_type, version)
                for resource_type in self.RESOURCE_TYPES
                for resource in data.get(resource_type, {}).values()
                for version in resource.get("versions", [])
            }

        with self._lock:
            before = snapshots()
            with formal_write_transaction(self.versions_file):
                try:
                    yield
                except BaseException as failure:
                    cleanup = _unlink_paths(*(snapshots() - before))
                    if cleanup:
                        raise RuntimeError("history restore rollback was incomplete") from failure
                    raise

    def add_history_version(
        self,
        resource_type: str,
        resource_id: str,
        source_file: Path,
        *,
        current_file: Path,
        prompt: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Restore ``source_file`` as a historical resource version without changing current version; return its new project-relative snapshot path.

        ``metadata`` carries the deleted version's original metadata (generation basis, etc.) so restored versions
        remain selectable normally. Preserve content-version identity: this is still the same content version.
        For remote versions with durable location/digest, matching bytes preserve original location, with
        ``source_file`` becoming only its working file.

        For deletion-record restoration: current files already at original paths are not overwritten. If current
        files lack version records, register them first; otherwise restored versions would be the sole records
        and appear current."""
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        with self._lock:
            self.ensure_current_tracked(resource_type, resource_id, current_file, prompt)
            data = self._load_versions()
            resource_data = data.setdefault(resource_type, {}).setdefault(
                resource_id, {"current_version": 0, "versions": []}
            )
            current_version = resource_data.get("current_version", 0)
            created: list[Path] = []
            restored_metadata = dict(metadata or {})
            content_version_id = restored_metadata.pop(CONTENT_VERSION_ID, None)
            stored = ObjectLocator.from_record(restored_metadata.pop(STORAGE, None))
            digest = restored_metadata.pop(CONTENT_DIGEST, None)
            retained = (stored, digest) if stored is not None and isinstance(digest, str) else None
            try:
                self._append_selected_snapshot(
                    resource_type,
                    resource_id,
                    resource_data,
                    Path(source_file),
                    prompt,
                    restored_metadata,
                    created,
                    content_version_id=content_version_id if isinstance(content_version_id, str) else None,
                    retained=retained,
                )
                resource_data["current_version"] = current_version
                self._save_versions(data)
            except BaseException as exc:
                _report_cleanup_failures(_unlink_paths(*created), active_failure=exc)
                raise
            record_project_activity(self.project_path)
            return resource_data["versions"][-1]["file"]

    def delete_history_version(self, snapshot_path: str) -> "TrashRecord":
        """Move the historical version with snapshot path ``snapshot_path`` into project trash, returning a deletion record; current versions cannot be deleted this way.

        Callers hold the project lock (``project_metadata_lock``). Restoration adds it back as historical resource version."""
        # Trash depends on this module for restoring historical versions, so import only here.
        from lib.project import project_trash

        with self._lock:
            data = self._load_versions()
            for resource_type, resources in data.items():
                for resource_id, resource_data in resources.items() if isinstance(resources, dict) else ():
                    versions = resource_data.get("versions", []) if isinstance(resource_data, dict) else []
                    for index, record in enumerate(versions):
                        if not isinstance(record, dict) or record.get("file") != snapshot_path:
                            continue
                        if record.get("version") == resource_data.get("current_version"):
                            raise ConflictError("version_delete_current", path=snapshot_path)
                        self._resolve_snapshot(resource_type, record)
                        original = self.versions_file.read_bytes()
                        del versions[index]
                        self._save_versions(data)
                        obj = {
                            "kind": "version",
                            "path": snapshot_path,
                            "resource_type": resource_type,
                            "resource_id": resource_id,
                            "version": record.get("version"),
                            "prompt": record.get("prompt", ""),
                            "metadata": {
                                key: value
                                for key, value in record.items()
                                # Preserve digests with records to verify restored remote-version bytes still belong to the original object.
                                if key not in {"version", "file", "prompt", _PREVIOUS_CURRENT_VERSION}
                            },
                        }
                        try:
                            trashed = project_trash.record_deletion(self.project_path, obj, [snapshot_path])
                        except BaseException:
                            atomic_write_bytes(self.versions_file, original)
                            raise
                        record_project_activity(self.project_path)
                        return trashed
        raise NotFoundError("version_not_found", version=snapshot_path)

    def resource_records(self, resources: Iterable[tuple[str, str]]) -> dict[str, dict[str, Any]]:
        """Version records for these resources, bucketed by type; omit resources without records."""
        data = self._load_versions()
        found: dict[str, dict[str, Any]] = {}
        for resource_type, resource_id in resources:
            record = data.get(resource_type, {}).get(resource_id)
            if isinstance(record, dict):
                found.setdefault(resource_type, {})[resource_id] = record
        return found

    def snapshot_files(self, records: Mapping[str, Mapping[str, Any]]) -> list[str]:
        """Project-relative version snapshot paths from records; snapshot files may no longer be on disk."""
        return [
            version["file"]
            for resource_type, bucket in records.items()
            for record in bucket.values()
            for version in record.get("versions", [])
            if isinstance(version, dict) and self.is_managed_snapshot_path(resource_type, version.get("file"))
        ]

    def detach_resources(self, resources: Iterable[tuple[str, str]]) -> None:
        """Detach resource history records without touching snapshots already moved into project trash; callers hold the project lock."""
        with self._lock:
            data = self._load_versions()
            removed = [data[rt].pop(rid) for rt, rid in resources if rid in data.get(rt, {})]
            if removed:
                self._save_versions(data)
                record_project_activity(self.project_path)

    def attach_resources(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        """Restore whole history records after snapshots return to original paths; preserve resources with existing records. Callers hold the project lock."""
        with self._lock:
            data = self._load_versions()
            added = False
            for resource_type, bucket in records.items():
                target = data.setdefault(resource_type, {})
                for resource_id, record in bucket.items():
                    if resource_id not in target:
                        target[resource_id] = dict(record)
                        added = True
            if added:
                self._save_versions(data)
                record_project_activity(self.project_path)

    def restore_version(
        self,
        resource_type: str,
        resource_id: str,
        version: int,
        current_file: Path,
        *,
        on_restore: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict:
        """Switch to a specified version.

        Copy that version to the current path and point current_version to it.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.
            version: Version number to restore.
            current_file: Current-file path.

        Returns:
            Switch information containing restored_version, current_version, prompt."""
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

            self._resolve_snapshot(resource_type, target_version)
            target_file = LocalMaterialStorage(self.project_path).open(
                _content_version_of(resource_type, resource_id, target_version)
            )

            versions_existed = self.versions_file.is_file()
            versions_snapshot = self.versions_file.read_bytes() if versions_existed else None
            current_backup: Path | None = None
            current_existed = current_file.is_file()
            restore_succeeded = False
            try:
                current_file.parent.mkdir(parents=True, exist_ok=True)
                if current_existed:
                    current_backup = _create_rollback_backup(current_file)
                _install_snapshot(target_file, current_file)
                resource_data["current_version"] = version
                self._save_versions(data)
                if on_restore is not None:
                    on_restore(dict(target_version))
                restore_succeeded = True
                record_project_activity(self.project_path)
            except BaseException as failure:
                rollback_errors: list[OSError] = []
                try:
                    if current_backup is None:
                        if not current_existed:
                            # hard-delete: rollback -- remove current file created by this failed restoration
                            current_file.unlink(missing_ok=True)
                    else:
                        os.replace(current_backup, current_file)
                except OSError as exc:
                    rollback_errors.append(exc)
                try:
                    if versions_snapshot is None:
                        # hard-delete: rollback -- remove versions.json created by this failed restoration
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

    def verify_snapshots(self, resource_type: str, snapshot_paths: Collection[str]) -> None:
        """Verify the version snapshots at ``snapshot_paths`` (project-relative) against their recorded digests.

        For a consumer about to read the snapshots directly (final cut).  Raises
        :class:`VersionSnapshotCorruptedError` for the first mismatch; paths no record names are left alone.
        """

        with self._lock:
            bucket = self._load_versions().get(resource_type)
        for resource_id, history in bucket.items() if isinstance(bucket, dict) else ():
            records = history.get("versions") if isinstance(history, dict) else None
            for record in records if isinstance(records, list) else ():
                if isinstance(record, dict) and record.get("file") in snapshot_paths:
                    verify_version_snapshot(resource_type, resource_id, record, self.project_path / record["file"])

    def ensure_content_version(self, resource_type: str, resource_id: str, version: int) -> ContentVersion:
        """Return a record's content version, first backfilling missing identity/digest.

        Backfill only records predating content-version identity: create identity and derive digest from the
        snapshot now, never changing them afterward. Existing identity/digest records cause no writes.

        Raises:
            NotFoundError: Resource/version absent.
            UnmanagedSnapshotPathError: Record points outside managed snapshot buckets.
            FileNotFoundError: Digest backfill needs an absent snapshot."""

        if resource_type not in self.RESOURCE_TYPES:
            raise BadRequestError("unsupported_resource_type", resource_type=resource_type)

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)
            if not isinstance(resource_data, dict):
                raise NotFoundError("version_resource_not_found", resource_type=resource_type, resource_id=resource_id)
            record = next(
                (
                    item
                    for item in resource_data.get("versions", [])
                    if isinstance(item, dict) and item.get("version") == version
                ),
                None,
            )
            if record is None:
                raise NotFoundError("version_not_found", version=version)
            snapshot = self._resolve_snapshot(resource_type, record)
            changed = False
            if not isinstance(record.get(CONTENT_VERSION_ID), str):
                record[CONTENT_VERSION_ID] = new_content_version_id()
                changed = True
            if not isinstance(record.get(CONTENT_DIGEST), str):
                record[CONTENT_DIGEST] = sha256_file(snapshot)
                changed = True
            if changed:
                self._save_versions(data)
            return _content_version_of(resource_type, resource_id, record)

    def find_content_version(self, resource_type: str, content_version_id: str) -> ContentVersion | None:
        """Find current records by content-version identity; resource rename moves snapshots/rewrites records without changing identity.

        Return ``None`` when absent (history deleted).

        Raises:
            UnmanagedSnapshotPathError: Record points outside managed snapshot buckets."""

        if resource_type not in self.RESOURCE_TYPES:
            raise BadRequestError("unsupported_resource_type", resource_type=resource_type)

        with self._lock:
            bucket = self._load_versions().get(resource_type)
        for resource_id, history in bucket.items() if isinstance(bucket, dict) else ():
            records = history.get("versions") if isinstance(history, dict) else None
            for record in records if isinstance(records, list) else ():
                if isinstance(record, dict) and record.get(CONTENT_VERSION_ID) == content_version_id:
                    self._resolve_snapshot(resource_type, record)
                    return _content_version_of(resource_type, resource_id, record)
        return None

    def update_version_metadata(self, resource_type: str, resource_id: str, version: int, **metadata) -> bool:
        """Backfill metadata keys on existing records, overwriting same-name keys.

        Generation finalize uses this after version insertion for metadata known only then (e.g. reference-unit
        artifact provenance signatures). Return False without raising if records are absent: best-effort archive
        backfill degrades only to missing metadata on future restoration.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.
            version: Version number.
            **metadata: Metadata key/value pairs to write.

        Returns:
            Whether a record was found and written."""
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")
        # Content-version facts are set only on save; metadata backfill changes none of the three.
        protected = {CONTENT_VERSION_ID, CONTENT_DIGEST, STORAGE} & metadata.keys()
        if protected:
            raise ValueError(f"version metadata cannot overwrite {sorted(protected)}")

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)
            if not resource_data:
                return False
            for record in resource_data.get("versions", []):
                if record.get("version") == version:
                    record.update(metadata)
                    self._save_versions(data)
                    record_project_activity(self.project_path)
                    return True
            return False

    def get_version_metadata(self, resource_type: str, resource_id: str, version: int, key: str) -> Any | None:
        """Read a metadata key on a specified version; return None for missing records/keys."""
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get(key)
        return None

    def get_version_file_url(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """Get a specified version's file URL.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.
            version: Version number.

        Returns:
            File URL, None if absent."""
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get("file_url")
        return None

    def get_version_prompt(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """Get a specified version's prompt.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.
            version: Version number.

        Returns:
            Prompt text, None if absent."""
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get("prompt")
        return None

    def get_version_created_at(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """Get a specified version's insertion time (ISO8601).

        Historical restoration writes this original generation time back into generated_assets, not now:
        restored content is old and derived judgments such as voice freshness must use its old time.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.
            version: Version number.

        Returns:
            ISO8601 timestamp, None if absent."""
        value = self.get_version_metadata(resource_type, resource_id, version, "created_at")
        return value if isinstance(value, str) else None

    def has_versions(self, resource_type: str, resource_id: str) -> bool:
        """Check whether a resource has version records.

        Args:
            resource_type: Resource type.
            resource_id: Resource ID.

        Returns:
            Whether version records exist."""
        return self.get_current_version(resource_type, resource_id) > 0


def selected_manual_upload_snapshot(history: object, resource_type: str) -> str | None:
    """The managed snapshot of a resource's selected version when that version is a manual upload.

    ``history`` is one resource's entry in ``versions.json``.  Returns ``None``
    when nothing is selected, the selected record is not a manual upload, or its
    snapshot path lies outside the resource type's history bucket.  Whether the
    snapshot still matches the formal file is the caller's comparison.
    """

    if not isinstance(history, Mapping):
        return None
    selected_version = history.get("current_version")
    if type(selected_version) is not int or selected_version <= 0:
        return None
    records = history.get("versions")
    if not isinstance(records, list):
        return None
    selected = next(
        (record for record in records if isinstance(record, Mapping) and record.get("version") == selected_version),
        None,
    )
    if selected is None or selected.get("source") != MANUAL_UPLOAD_VERSION_SOURCE:
        return None
    snapshot_rel = selected.get("file")
    if not isinstance(snapshot_rel, str) or not VersionManager.is_managed_snapshot_path(resource_type, snapshot_rel):
        return None
    return snapshot_rel
