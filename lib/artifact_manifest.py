"""Project-local artifact identity, provenance, and manifest storage."""

from __future__ import annotations

import base64
import binascii
import contextlib
import ctypes
import ctypes.wintypes as wintypes
import errno
import hashlib
import json
import math
import os
import secrets
import stat
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple, Protocol, Self, cast

import portalocker

from lib.asset_types import ASSET_TYPES, asset_name_comparison_key
from lib.content_digest import (
    CONTENT_DIGEST_RE,
    HASH_ALGORITHM,
    PREFIXED_DIGEST_RE,
    canonical_json_bytes,
    digest_stream,
    prefixed,
)

_KEY_PREFIX = "artifact-key-v1:"
MANIFEST_FILENAME = ".arcreel_artifacts.json"
LOCK_FILENAME = ".artifact_manifest.lock"
MANIFEST_SCHEMA_VERSION = 1
ARCHIVE_MANIFEST_SCHEMA_VERSION = 2
LOCK_TIMEOUT_SECONDS = 10.0
_RESERVED_ARTIFACT_PATHS = frozenset({MANIFEST_FILENAME, LOCK_FILENAME})
_WINDOWS_RESERVED_ARTIFACT_PATHS = frozenset(path.casefold() for path in _RESERVED_ARTIFACT_PATHS)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class ArtifactKind(StrEnum):
    """Kinds supported by the project artifact manifest schema."""

    ASSET_SHEET = "asset-sheet"
    EPISODE_SCRIPT_PLAN = "episode-script-plan"
    EPISODE_SCRIPT = "episode-script"
    EPISODE_GRID = "episode-grid"
    EPISODE_STORYBOARD = "episode-storyboard"
    EPISODE_VIDEO = "episode-video"
    EPISODE_AUDIO = "episode-audio"
    EPISODE_SUBTITLE = "episode-subtitle"
    EPISODE_PRESENTATION = "episode-presentation"


class ArtifactStatus(StrEnum):
    """Currency of a formal artifact relative to its current direct-input basis."""

    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"
    BLOCKED = "blocked"


class ArtifactManifestError(RuntimeError):
    """Manifest storage cannot be read or safely updated."""


class ArtifactRegistrationError(ArtifactManifestError):
    """A basis cannot be registered before its formal artifact is safely present."""


@dataclass(frozen=True, slots=True)
class ArtifactBlocker:
    code: str
    path: str
    detail: str


@dataclass(frozen=True, slots=True)
class ArtifactComparison:
    status: ArtifactStatus
    artifact_path: str
    blocker: ArtifactBlocker | None = None

    @property
    def usable(self) -> bool:
        return self.status in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}


@dataclass(frozen=True, slots=True)
class ArtifactManifestEntry:
    artifact_path: str
    basis_digest: str


@dataclass(frozen=True, slots=True)
class ArtifactManifestArchiveSnapshot:
    """Complete portable claims bound to the exported formal artifact bytes."""

    entries: Mapping[ArtifactKey, ArtifactManifestEntry]
    content_digests: Mapping[ArtifactKey, str]


@dataclass(frozen=True, slots=True)
class ArtifactObservation:
    artifact_path: str
    present: bool
    blocker: ArtifactBlocker | None = None
    content_digest: str | None = None
    content_bytes: bytes | None = None


class ArtifactManifestAdapter(Protocol):
    """Storage seam used by the artifact manifest domain module."""

    def inspect_artifact(self, artifact_path: str) -> ArtifactObservation:
        raise NotImplementedError

    def get_entry(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        raise NotImplementedError

    def snapshot_entries(self) -> Mapping[ArtifactKey, ArtifactManifestEntry]:
        """Return one decoded snapshot of the complete Manifest."""

        raise NotImplementedError

    def put_entry(self, key: ArtifactKey, entry: ArtifactManifestEntry) -> bool:
        raise NotImplementedError

    def delete_entry(self, key: ArtifactKey) -> bool:
        raise NotImplementedError

    def replace_entries_if_matches_atomically(
        self,
        *,
        expected: Mapping[ArtifactKey, ArtifactManifestEntry | None],
        replacements: Mapping[ArtifactKey, ArtifactManifestEntry | None],
    ) -> bool:
        """Compare-and-swap a scoped set of claims in one storage commit."""

        raise NotImplementedError

    def replace_snapshot_if_matches_atomically(
        self,
        *,
        expected: Mapping[ArtifactKey, ArtifactManifestEntry],
        replacement: Mapping[ArtifactKey, ArtifactManifestEntry],
    ) -> bool:
        """Replace a complete snapshot only when the complete current state matches."""

        raise NotImplementedError

    def replace_entries_atomically(
        self,
        entries: Mapping[ArtifactKey, ArtifactManifestEntry],
    ) -> bool:
        """Replace the complete manifest target state in one storage commit."""

        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ArtifactEntryRekeyReceipt:
    """Committed claim rekey that can restore its exact prior key state."""

    adapter: ArtifactManifestAdapter
    before: Mapping[ArtifactKey, ArtifactManifestEntry | None]
    after: Mapping[ArtifactKey, ArtifactManifestEntry | None]
    changed: bool

    def matches_current(self) -> bool:
        return _entries_match(self.adapter, self.after)

    def compensate(self) -> bool:
        if not self.changed:
            return False
        if self.adapter.replace_entries_if_matches_atomically(
            expected=self.after,
            replacements=self.before,
        ):
            return True
        if _entries_match(self.adapter, self.before):
            return False
        raise ArtifactManifestError("artifact claim rekey changed concurrently and could not be restored")


@dataclass(frozen=True, slots=True)
class ArtifactEntryRekeyPlan:
    """Preflighted, whole-Manifest claim rekey used by identity transactions."""

    adapter: ArtifactManifestAdapter
    before: Mapping[ArtifactKey, ArtifactManifestEntry | None]
    after: Mapping[ArtifactKey, ArtifactManifestEntry | None]
    changed: bool

    def commit(self) -> ArtifactEntryRekeyReceipt:
        receipt = ArtifactEntryRekeyReceipt(
            adapter=self.adapter,
            before=self.before,
            after=self.after,
            changed=self.changed,
        )
        if not self.changed:
            return receipt
        try:
            changed = self.adapter.replace_entries_if_matches_atomically(
                expected=self.before,
                replacements=self.after,
            )
        except BaseException as original_error:
            try:
                restored = self.adapter.replace_entries_if_matches_atomically(
                    expected=self.after,
                    replacements=self.before,
                )
                if not restored and not _entries_match(self.adapter, self.before):
                    raise ArtifactManifestError("artifact claim rekey did not leave a recoverable state")
            except BaseException as rollback_error:
                rollback_error.__cause__ = original_error
                raise RuntimeError("artifact claim rekey failed and rollback was incomplete") from rollback_error
            raise
        if not changed:
            raise ArtifactManifestError("artifact claims changed after the rekey preflight")
        return receipt


class ArtifactManifest:
    """Register and compare canonical artifact bases through one storage seam."""

    def __init__(self, adapter: ArtifactManifestAdapter) -> None:
        self._adapter = adapter

    def register(self, key: ArtifactKey, *, artifact_path: str, basis: ArtifactBasis) -> bool:
        if not isinstance(basis, ArtifactBasis):
            raise TypeError("basis must be an ArtifactBasis")
        return self.register_descriptor(
            key,
            artifact_path=artifact_path,
            basis=ArtifactBasisDescriptor.from_basis(basis),
        )

    def register_descriptor(
        self,
        key: ArtifactKey,
        *,
        artifact_path: str,
        basis: ArtifactBasisDescriptor,
    ) -> bool:
        """Register source evidence frozen before the formal artifact commit.

        Checkpoints and version metadata intentionally carry only strict
        descriptors. Accepting that portable form avoids reconstructing or
        guessing the original input payload during resume and restore.
        """

        if not isinstance(basis, ArtifactBasisDescriptor):
            raise TypeError("basis must be an ArtifactBasisDescriptor")
        observation = self._adapter.inspect_artifact(artifact_path)
        if observation.blocker is not None:
            raise ArtifactRegistrationError(observation.blocker.detail)
        if not observation.present:
            raise ArtifactRegistrationError(f"artifact is not present: {observation.artifact_path}")
        return self._adapter.put_entry(
            key,
            ArtifactManifestEntry(
                artifact_path=observation.artifact_path,
                basis_digest=basis.digest,
            ),
        )

    def register_descriptor_transactionally(
        self,
        key: ArtifactKey,
        *,
        artifact_path: str,
        basis: ArtifactBasisDescriptor,
    ) -> bool:
        """Register a descriptor while restoring the exact prior entry on error."""

        if not isinstance(basis, ArtifactBasisDescriptor):
            raise TypeError("basis must be an ArtifactBasisDescriptor")
        previous = self._adapter.get_entry(key)
        expected = ArtifactManifestEntry(
            artifact_path=normalize_artifact_path(artifact_path),
            basis_digest=basis.digest,
        )
        try:
            return self.register_descriptor(key, artifact_path=artifact_path, basis=basis)
        except BaseException as original_error:
            try:
                current = self._adapter.get_entry(key)
                if current == expected:
                    if previous is None:
                        self._adapter.delete_entry(key)
                    else:
                        self._adapter.put_entry(key, previous)
            except BaseException as rollback_error:
                rollback_error.__cause__ = original_error
                raise RuntimeError("artifact basis registration failed and rollback was incomplete") from rollback_error
            raise

    def register_entry_transactionally(
        self,
        key: ArtifactKey,
        entry: ArtifactManifestEntry,
    ) -> bool:
        """Register an already resolved target entry with exact rollback.

        Target-state resolvers sometimes possess only a frozen digest (for
        example a selected paid-media version), not the complete basis payload.
        This seam preserves the same artifact-presence and rollback guarantees
        without inventing a synthetic basis kind merely to carry that digest.
        """

        _encode_target_entries({key: entry})
        observation = self._adapter.inspect_artifact(entry.artifact_path)
        if observation.blocker is not None:
            raise ArtifactRegistrationError(observation.blocker.detail)
        if not observation.present:
            raise ArtifactRegistrationError(f"artifact is not present: {observation.artifact_path}")
        expected = ArtifactManifestEntry(
            artifact_path=observation.artifact_path,
            basis_digest=entry.basis_digest,
        )
        previous = self._adapter.get_entry(key)
        try:
            return self._adapter.put_entry(key, expected)
        except BaseException as original_error:
            try:
                current = self._adapter.get_entry(key)
                if current == expected:
                    if previous is None:
                        self._adapter.delete_entry(key)
                    else:
                        self._adapter.put_entry(key, previous)
            except BaseException as rollback_error:
                rollback_error.__cause__ = original_error
                raise RuntimeError("artifact entry registration failed and rollback was incomplete") from rollback_error
            raise

    def forget_entry_transactionally(self, key: ArtifactKey) -> bool:
        """Remove one current claim with exact rollback on storage failure."""

        previous = self._adapter.get_entry(key)
        if previous is None:
            return False
        try:
            return self._adapter.delete_entry(key)
        except BaseException as original_error:
            try:
                if self._adapter.get_entry(key) is None:
                    self._adapter.put_entry(key, previous)
            except BaseException as rollback_error:
                rollback_error.__cause__ = original_error
                raise RuntimeError("artifact entry removal failed and rollback was incomplete") from rollback_error
            raise

    def forget_entries_transactionally(self, keys: Sequence[ArtifactKey]) -> bool:
        """Remove a set of claims through one scoped compare-and-swap commit."""

        unique = tuple(dict.fromkeys(keys))
        before = {key: entry for key in unique if (entry := self._adapter.get_entry(key)) is not None}
        if not before:
            return False
        receipt = ArtifactEntryRekeyPlan(
            adapter=self._adapter,
            before=before,
            after={key: None for key in before},
            changed=True,
        ).commit()
        return receipt.changed

    def plan_entry_rekey(
        self,
        source_key: ArtifactKey,
        target_key: ArtifactKey,
        *,
        artifact_path_rewrites: Mapping[str, str] | None = None,
    ) -> ArtifactEntryRekeyPlan:
        """Preflight an identity rename without reconstructing its frozen basis.

        The source claim's digest is immutable evidence.  Only the key and a
        caller-proven formal path move are changed.  A target claim is a hard
        collision: silently replacing it could attach another artifact's basis
        to the renamed identity.
        """

        rewrites = {
            normalize_artifact_path(source): normalize_artifact_path(target)
            for source, target in (artifact_path_rewrites or {}).items()
        }
        source_entry = self._adapter.get_entry(source_key)
        if source_key == target_key:
            if source_entry is None:
                return ArtifactEntryRekeyPlan(self._adapter, {}, {}, False)
            replacement = ArtifactManifestEntry(
                artifact_path=rewrites.get(source_entry.artifact_path, source_entry.artifact_path),
                basis_digest=source_entry.basis_digest,
            )
            if replacement == source_entry:
                return ArtifactEntryRekeyPlan(self._adapter, {}, {}, False)
            return ArtifactEntryRekeyPlan(
                self._adapter,
                {source_key: source_entry},
                {source_key: replacement},
                True,
            )

        target_entry = self._adapter.get_entry(target_key)
        if target_entry is not None:
            raise ArtifactManifestError(f"artifact claim already exists for target key: {target_key.encode()}")
        if source_entry is None:
            return ArtifactEntryRekeyPlan(self._adapter, {}, {}, False)
        replacement = ArtifactManifestEntry(
            artifact_path=rewrites.get(source_entry.artifact_path, source_entry.artifact_path),
            basis_digest=source_entry.basis_digest,
        )
        return ArtifactEntryRekeyPlan(
            self._adapter,
            {source_key: source_entry, target_key: None},
            {source_key: None, target_key: replacement},
            True,
        )

    def compare(self, key: ArtifactKey, *, artifact_path: str, basis: ArtifactBasis) -> ArtifactComparison:
        if not isinstance(basis, ArtifactBasis):
            raise TypeError("basis must be an ArtifactBasis")
        return self.compare_entry(
            key,
            artifact_path=artifact_path,
            expected=ArtifactManifestEntry(
                artifact_path=artifact_path,
                basis_digest=basis.digest,
            ),
        )

    def compare_entry(
        self,
        key: ArtifactKey,
        *,
        artifact_path: str,
        expected: ArtifactManifestEntry | None,
    ) -> ArtifactComparison:
        """Compare against a canonical target that may carry only a frozen digest."""

        observation = self._adapter.inspect_artifact(artifact_path)
        if observation.blocker is not None:
            return ArtifactComparison(
                status=ArtifactStatus.BLOCKED,
                artifact_path=observation.artifact_path,
                blocker=observation.blocker,
            )
        if not observation.present:
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=observation.artifact_path)
        try:
            entry = self._adapter.get_entry(key)
        except ArtifactManifestError as exc:
            blocker = ArtifactBlocker(
                code="manifest_unreadable",
                path=observation.artifact_path,
                detail=str(exc),
            )
            return ArtifactComparison(
                status=ArtifactStatus.BLOCKED,
                artifact_path=observation.artifact_path,
                blocker=blocker,
            )
        if entry is None:
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=observation.artifact_path)
        # A claim is evidence for one exact formal path.  Reusing that claim for
        # a different pointer would turn an unregistered file into a stale-but-
        # usable artifact.  Digest drift is stale; path drift has no claim.
        if entry.artifact_path != observation.artifact_path:
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=observation.artifact_path)
        normalized_expected = (
            None
            if expected is None
            else ArtifactManifestEntry(
                artifact_path=normalize_artifact_path(expected.artifact_path),
                basis_digest=expected.basis_digest,
            )
        )
        status = ArtifactStatus.CURRENT if entry == normalized_expected else ArtifactStatus.STALE
        return ArtifactComparison(status=status, artifact_path=observation.artifact_path)


class InMemoryArtifactManifestAdapter:
    """Thread-safe in-memory adapter for isolated domain tests and ephemeral callers."""

    def __init__(self, *, artifacts: set[str] | None = None) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, ArtifactManifestEntry] = {}
        self._artifacts = {normalize_artifact_path(path) for path in artifacts or set()}

    def inspect_artifact(self, artifact_path: str) -> ArtifactObservation:
        try:
            normalized = normalize_artifact_path(artifact_path)
        except ValueError as exc:
            blocker = ArtifactBlocker(code="artifact_path_invalid", path=str(artifact_path), detail=str(exc))
            return ArtifactObservation(artifact_path=str(artifact_path), present=False, blocker=blocker)
        with self._lock:
            return ArtifactObservation(artifact_path=normalized, present=normalized in self._artifacts)

    def get_entry(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        with self._lock:
            return self._entries.get(key.encode())

    def snapshot_entries(self) -> Mapping[ArtifactKey, ArtifactManifestEntry]:
        with self._lock:
            return {ArtifactKey.decode(encoded): entry for encoded, entry in self._entries.items()}

    def put_entry(self, key: ArtifactKey, entry: ArtifactManifestEntry) -> bool:
        with self._lock:
            encoded = key.encode()
            if self._entries.get(encoded) == entry:
                return False
            updated = {**self._entries, encoded: entry}
            _assert_unique_artifact_paths(updated)
            self._entries = updated
            return True

    def delete_entry(self, key: ArtifactKey) -> bool:
        with self._lock:
            return self._entries.pop(key.encode(), None) is not None

    def replace_entries_if_matches_atomically(
        self,
        *,
        expected: Mapping[ArtifactKey, ArtifactManifestEntry | None],
        replacements: Mapping[ArtifactKey, ArtifactManifestEntry | None],
    ) -> bool:
        encoded_expected = _encode_optional_entries(expected)
        encoded_replacements = _encode_optional_entries(replacements)
        with self._lock:
            if any(self._entries.get(key) != entry for key, entry in encoded_expected.items()):
                return False
            updated = dict(self._entries)
            for key, entry in encoded_replacements.items():
                if entry is None:
                    updated.pop(key, None)
                else:
                    updated[key] = entry
            if updated == self._entries:
                return False
            _assert_unique_artifact_paths(updated)
            self._entries = updated
            return True

    def replace_snapshot_if_matches_atomically(
        self,
        *,
        expected: Mapping[ArtifactKey, ArtifactManifestEntry],
        replacement: Mapping[ArtifactKey, ArtifactManifestEntry],
    ) -> bool:
        encoded_expected = _encode_target_entries(expected)
        encoded_replacement = _encode_target_entries(replacement)
        _assert_unique_artifact_paths(encoded_replacement)
        with self._lock:
            if self._entries != encoded_expected:
                return False
            self._entries = encoded_replacement
            return True

    def replace_entries_atomically(
        self,
        entries: Mapping[ArtifactKey, ArtifactManifestEntry],
    ) -> bool:
        encoded = _encode_target_entries(entries)
        _assert_unique_artifact_paths(encoded)
        with self._lock:
            if self._entries == encoded:
                return False
            self._entries = encoded
            return True


class ProjectArtifactManifestAdapter:
    """Safe project-directory adapter backed by a versioned JSON manifest."""

    def __init__(self, project_dir: Path, *, nofollow_supported: bool = True) -> None:
        self._nofollow_supported = nofollow_supported
        root_fd: int | None = None
        windows_handle: int | None = None
        try:
            initial_stat = project_dir.stat(follow_symlinks=False)
            if _is_linkish(project_dir):
                raise ArtifactManifestError(f"project directory is a symlink or junction: {project_dir}")
            if not stat.S_ISDIR(initial_stat.st_mode):
                raise ArtifactManifestError(f"project path is not a directory: {project_dir}")
            initial_identity = (initial_stat.st_dev, initial_stat.st_ino)
            opened_identity: tuple[int, int] | None = None
            if os.name == "posix":
                root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | self._nofollow_flag
                root_fd = os.open(project_dir, root_flags)
                opened_stat = os.fstat(root_fd)
                opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
            elif os.name == "nt":
                windows_handle = _open_windows_directory_handle(project_dir)
            resolved = project_dir.resolve(strict=True)
            current_stat = project_dir.stat(follow_symlinks=False)
            resolved_stat = resolved.stat(follow_symlinks=False)
        except OSError as exc:
            raise ArtifactManifestError(f"project directory is unavailable: {project_dir}") from exc
        finally:
            if root_fd is not None:
                os.close(root_fd)
            if windows_handle is not None:
                _close_windows_handle(windows_handle)
        current_identity = (current_stat.st_dev, current_stat.st_ino)
        resolved_identity = (resolved_stat.st_dev, resolved_stat.st_ino)
        if (
            _is_linkish(project_dir)
            or not stat.S_ISDIR(current_stat.st_mode)
            or not stat.S_ISDIR(resolved_stat.st_mode)
            or current_identity != initial_identity
            or resolved_identity != initial_identity
            or (opened_identity is not None and opened_identity != initial_identity)
        ):
            raise ArtifactManifestError(f"project directory changed during adapter initialization: {project_dir}")
        self._project_dir = resolved
        self._project_identity = initial_identity

    @property
    def _nofollow_flag(self) -> int:
        """`O_NOFOLLOW` 的实际取值：不支持（平台缺失或注入声明不支持）时为 0，回退到身份校验路径。"""
        return _O_NOFOLLOW if self._nofollow_supported else 0

    def inspect_artifact(self, artifact_path: str) -> ArtifactObservation:
        return self._inspect_artifact(artifact_path, include_content_digest=False)

    def inspect_artifact_content(self, artifact_path: str) -> ArtifactObservation:
        """Inspect and hash one artifact through the same confined file handle."""

        return self._inspect_artifact(artifact_path, include_content_digest=True)

    def inspect_artifact_snapshot(self, artifact_path: str) -> ArtifactObservation:
        """Read and hash one artifact from the same confined file descriptor."""

        return self._inspect_artifact(
            artifact_path,
            include_content_digest=True,
            include_content_bytes=True,
        )

    def _inspect_artifact(
        self,
        artifact_path: str,
        *,
        include_content_digest: bool,
        include_content_bytes: bool = False,
    ) -> ArtifactObservation:
        try:
            normalized = normalize_artifact_path(artifact_path)
        except ValueError as exc:
            blocker = ArtifactBlocker(code="artifact_path_invalid", path=str(artifact_path), detail=str(exc))
            return ArtifactObservation(artifact_path=str(artifact_path), present=False, blocker=blocker)
        if os.name == "posix":
            return self._inspect_artifact_posix(
                normalized,
                include_content_digest=include_content_digest,
                include_content_bytes=include_content_bytes,
            )
        return self._inspect_artifact_portable(
            normalized,
            include_content_digest=include_content_digest,
            include_content_bytes=include_content_bytes,
        )

    def _read_open_artifact(
        self,
        normalized: str,
        fd: int,
        opened_stat: os.stat_result,
        *,
        include_content_digest: bool,
        include_content_bytes: bool,
    ) -> tuple[str | None, bytes | None, ArtifactObservation | None]:
        if not include_content_digest:
            os.read(fd, 1)
            return None, None, None

        hexdigest, _size, content = digest_stream(
            lambda size: os.read(fd, size),
            collect_content=include_content_bytes,
        )
        completed_stat = os.fstat(fd)
        opened_version = (opened_stat.st_size, opened_stat.st_mtime_ns, opened_stat.st_ctime_ns)
        completed_version = (completed_stat.st_size, completed_stat.st_mtime_ns, completed_stat.st_ctime_ns)
        if completed_version != opened_version:
            return (
                None,
                None,
                self._artifact_blocked(
                    normalized,
                    "artifact_unreadable",
                    f"artifact changed while its content digest was being read: {normalized}",
                ),
            )
        return hexdigest, content, None

    def _inspect_artifact_posix(
        self,
        normalized: str,
        *,
        include_content_digest: bool = False,
        include_content_bytes: bool = False,
    ) -> ArtifactObservation:
        parts = PurePosixPath(normalized).parts
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | self._nofollow_flag
        file_flags = os.O_RDONLY | self._nofollow_flag | getattr(os, "O_NONBLOCK", 0)
        with contextlib.ExitStack() as stack:
            if not self._nofollow_flag and _is_linkish(self._project_dir):
                return self._artifact_blocked(
                    normalized,
                    "artifact_symlink",
                    f"project directory is a symlink or junction: {self._project_dir}",
                )
            try:
                root_fd = os.open(self._project_dir, directory_flags)
            except OSError as exc:
                return self._artifact_blocked(
                    normalized,
                    "artifact_unreadable",
                    f"project directory is unreadable: {exc}",
                )
            stack.callback(os.close, root_fd)
            try:
                self._assert_open_project_root_identity(root_fd)
            except ArtifactManifestError as exc:
                return self._artifact_blocked(normalized, "artifact_unreadable", str(exc))
            directory_fd = root_fd
            cursor = self._project_dir
            for part in parts[:-1]:
                cursor /= part
                expected_parent_identity: tuple[int, int] | None = None
                if not self._nofollow_flag and _is_linkish(cursor):
                    return self._artifact_blocked(
                        normalized,
                        "artifact_symlink",
                        f"artifact path contains a symlink or junction: {normalized}",
                    )
                if not self._nofollow_flag:
                    try:
                        parent_stat = cursor.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        return ArtifactObservation(artifact_path=normalized, present=False)
                    except OSError as exc:
                        return self._artifact_blocked(
                            normalized,
                            "artifact_unreadable",
                            f"artifact parent cannot be inspected safely: {normalized}: {exc}",
                        )
                    expected_parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
                try:
                    next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                except FileNotFoundError:
                    return ArtifactObservation(artifact_path=normalized, present=False)
                except OSError as exc:
                    return self._artifact_blocked(
                        normalized,
                        "artifact_symlink" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "artifact_unreadable",
                        f"artifact parent cannot be opened safely: {normalized}: {exc}",
                    )
                stack.callback(os.close, next_fd)
                if expected_parent_identity is not None:
                    try:
                        opened_parent_stat = os.fstat(next_fd)
                        current_parent_stat = cursor.stat(follow_symlinks=False)
                    except OSError as exc:
                        return self._artifact_blocked(
                            normalized,
                            "artifact_unreadable",
                            f"artifact parent changed while it was being opened: {normalized}: {exc}",
                        )
                    if (
                        _is_linkish(cursor)
                        or not stat.S_ISDIR(opened_parent_stat.st_mode)
                        or (opened_parent_stat.st_dev, opened_parent_stat.st_ino) != expected_parent_identity
                        or (current_parent_stat.st_dev, current_parent_stat.st_ino) != expected_parent_identity
                    ):
                        return self._artifact_blocked(
                            normalized,
                            "artifact_symlink",
                            f"artifact parent changed while it was being opened: {normalized}",
                        )
                directory_fd = next_fd
            final_path = cursor / parts[-1]
            expected_file_identity: tuple[int, int] | None = None
            if not self._nofollow_flag and _is_linkish(final_path):
                return self._artifact_blocked(
                    normalized,
                    "artifact_symlink",
                    f"artifact path contains a symlink or junction: {normalized}",
                )
            if not self._nofollow_flag:
                try:
                    file_stat = final_path.stat(follow_symlinks=False)
                except FileNotFoundError:
                    return ArtifactObservation(artifact_path=normalized, present=False)
                except OSError as exc:
                    return self._artifact_blocked(
                        normalized,
                        "artifact_unreadable",
                        f"artifact cannot be inspected safely: {normalized}: {exc}",
                    )
                expected_file_identity = (file_stat.st_dev, file_stat.st_ino)
            try:
                fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
            except FileNotFoundError:
                return ArtifactObservation(artifact_path=normalized, present=False)
            except OSError as exc:
                return self._artifact_blocked(
                    normalized,
                    "artifact_symlink" if exc.errno == errno.ELOOP else "artifact_unreadable",
                    f"artifact cannot be opened safely: {normalized}: {exc}",
                )
            stack.callback(os.close, fd)
            try:
                opened_file_stat = os.fstat(fd)
                if not stat.S_ISREG(opened_file_stat.st_mode):
                    return self._artifact_blocked(
                        normalized,
                        "artifact_not_regular_file",
                        f"artifact path is not a regular file: {normalized}",
                    )
                content_digest, content_bytes, content_blocker = self._read_open_artifact(
                    normalized,
                    fd,
                    opened_file_stat,
                    include_content_digest=include_content_digest,
                    include_content_bytes=include_content_bytes,
                )
                if content_blocker is not None:
                    return content_blocker
                current_file_stat = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
                opened_identity = (opened_file_stat.st_dev, opened_file_stat.st_ino)
                if (
                    stat.S_ISLNK(current_file_stat.st_mode)
                    or (current_file_stat.st_dev, current_file_stat.st_ino) != opened_identity
                    or (expected_file_identity is not None and opened_identity != expected_file_identity)
                ):
                    return self._artifact_blocked(
                        normalized,
                        "artifact_symlink",
                        f"artifact changed while it was being opened: {normalized}",
                    )
            except OSError as exc:
                return self._artifact_blocked(
                    normalized,
                    "artifact_unreadable",
                    f"artifact is unreadable: {normalized}: {exc}",
                )
        return ArtifactObservation(
            artifact_path=normalized,
            present=True,
            content_digest=content_digest,
            content_bytes=content_bytes,
        )

    def _inspect_artifact_portable(
        self,
        normalized: str,
        *,
        include_content_digest: bool = False,
        include_content_bytes: bool = False,
    ) -> ArtifactObservation:
        if _is_linkish(self._project_dir):
            return self._artifact_blocked(
                normalized,
                "artifact_symlink",
                f"project directory is a symlink or junction: {self._project_dir}",
            )
        try:
            with self._guard_portable_project_root():
                return self._inspect_artifact_portable_guarded(
                    normalized,
                    include_content_digest=include_content_digest,
                    include_content_bytes=include_content_bytes,
                )
        except ArtifactManifestError as exc:
            return self._artifact_blocked(normalized, "artifact_unreadable", str(exc))

    def _inspect_artifact_portable_guarded(
        self,
        normalized: str,
        *,
        include_content_digest: bool = False,
        include_content_bytes: bool = False,
    ) -> ArtifactObservation:
        path = self._project_dir.joinpath(*PurePosixPath(normalized).parts)
        cursor = self._project_dir
        checked_components: list[tuple[Path, tuple[int, int], int]] = []
        for part in PurePosixPath(normalized).parts:
            cursor = cursor / part
            if _is_linkish(cursor):
                blocker = ArtifactBlocker(
                    code="artifact_symlink",
                    path=normalized,
                    detail=f"artifact path contains a symlink or junction: {normalized}",
                )
                return ArtifactObservation(artifact_path=normalized, present=False, blocker=blocker)
            try:
                component_stat = cursor.stat(follow_symlinks=False)
            except FileNotFoundError:
                return ArtifactObservation(artifact_path=normalized, present=False)
            except OSError as exc:
                return self._artifact_blocked(
                    normalized,
                    "artifact_unreadable",
                    f"artifact path cannot be inspected safely: {normalized}: {exc}",
                )
            checked_components.append((cursor, (component_stat.st_dev, component_stat.st_ino), component_stat.st_mode))
        if not stat.S_ISREG(checked_components[-1][2]):
            blocker = ArtifactBlocker(
                code="artifact_not_regular_file",
                path=normalized,
                detail=f"artifact path is not a regular file: {normalized}",
            )
            return ArtifactObservation(artifact_path=normalized, present=False, blocker=blocker)
        flags = os.O_RDONLY | self._nofollow_flag
        try:
            fd = os.open(path, flags)
            try:
                opened_stat = os.fstat(fd)
                if not stat.S_ISREG(opened_stat.st_mode):
                    return self._artifact_blocked(
                        normalized,
                        "artifact_not_regular_file",
                        f"artifact path is not a regular file: {normalized}",
                    )
                content_digest, content_bytes, content_blocker = self._read_open_artifact(
                    normalized,
                    fd,
                    opened_stat,
                    include_content_digest=include_content_digest,
                    include_content_bytes=include_content_bytes,
                )
                if content_blocker is not None:
                    return content_blocker
                if (opened_stat.st_dev, opened_stat.st_ino) != checked_components[-1][1]:
                    return self._artifact_blocked(
                        normalized,
                        "artifact_symlink",
                        f"artifact path changed while it was being inspected: {normalized}",
                    )
                for checked_path, expected_identity, _ in checked_components:
                    if _is_linkish(checked_path):
                        return self._artifact_blocked(
                            normalized,
                            "artifact_symlink",
                            f"artifact path contains a symlink or junction: {normalized}",
                        )
                    current_stat = checked_path.stat(follow_symlinks=False)
                    if (current_stat.st_dev, current_stat.st_ino) != expected_identity:
                        return self._artifact_blocked(
                            normalized,
                            "artifact_symlink",
                            f"artifact path changed while it was being inspected: {normalized}",
                        )
            finally:
                os.close(fd)
        except OSError as exc:
            blocker = ArtifactBlocker(
                code="artifact_unreadable",
                path=normalized,
                detail=f"artifact is unreadable: {normalized}: {exc}",
            )
            return ArtifactObservation(artifact_path=normalized, present=False, blocker=blocker)
        return ArtifactObservation(
            artifact_path=normalized,
            present=True,
            content_digest=content_digest,
            content_bytes=content_bytes,
        )

    @staticmethod
    def _artifact_blocked(normalized: str, code: str, detail: str) -> ArtifactObservation:
        return ArtifactObservation(
            artifact_path=normalized,
            present=False,
            blocker=ArtifactBlocker(code=code, path=normalized, detail=detail),
        )

    def get_entry(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        return self._load_readonly().get(key.encode())

    def snapshot_entries(self) -> Mapping[ArtifactKey, ArtifactManifestEntry]:
        entries = self._load_readonly()
        return {ArtifactKey.decode(encoded): entry for encoded, entry in entries.items()}

    def _load_readonly(self) -> dict[str, ArtifactManifestEntry]:
        """Read one consistent snapshot without creating runtime state.

        Once a writer has created the durable lock file, readers serialize with
        that lock exactly as before.  A project that has never needed a manifest
        write has no lock file; in that state two identical guarded reads provide
        a stable snapshot without turning a status query into a filesystem write.
        A legitimate writer creates the lock before replacing the manifest, so a
        lock appearing during either read sends the reader back through the
        serialized path.
        """

        lock_path = self._project_dir / LOCK_FILENAME
        if self._runtime_file_identity(lock_path, "manifest lock") is not None:
            with self._locked() as root_fd:
                entries, _ = self._load_unlocked(root_fd)
                return entries

        with self._guard_portable_project_root():
            entries, original_bytes = self._load_unlocked(None)
            if self._runtime_file_identity(lock_path, "manifest lock") is not None:
                with self._locked() as root_fd:
                    locked_entries, _ = self._load_unlocked(root_fd)
                    return locked_entries
            repeated_entries, repeated_bytes = self._load_unlocked(None)
            if self._runtime_file_identity(lock_path, "manifest lock") is not None:
                with self._locked() as root_fd:
                    locked_entries, _ = self._load_unlocked(root_fd)
                    return locked_entries
        if original_bytes != repeated_bytes or entries != repeated_entries:
            raise ArtifactManifestError("artifact manifest changed during an unlocked read")
        return entries

    def put_entry(self, key: ArtifactKey, entry: ArtifactManifestEntry) -> bool:
        with self._locked() as root_fd:
            entries, original_bytes = self._load_unlocked(root_fd)
            encoded = key.encode()
            if entries.get(encoded) == entry and original_bytes is not None:
                return False
            entries[encoded] = entry
            new_bytes = _serialize_manifest(entries)
            if original_bytes == new_bytes:
                return False
            self._atomic_replace(new_bytes, root_fd)
            return True

    def replace_unreadable_entries_atomically(
        self,
        entries: Mapping[ArtifactKey, ArtifactManifestEntry],
    ) -> bool:
        """Recover an unreadable Manifest with one guarded whole-state replace.

        This seam is intentionally narrower than normal activation: callers may
        use it only after an explicit recovery policy decided that no existing
        claim is provable.  The Manifest lock spans revalidation and replacement;
        if another writer repaired it first, only the identical target is
        accepted and any other readable state makes the caller retry.
        """

        encoded = _encode_target_entries(entries)
        for entry in encoded.values():
            observation = self.inspect_artifact(entry.artifact_path)
            if observation.blocker is not None:
                raise ArtifactRegistrationError(observation.blocker.detail)
            if not observation.present:
                raise ArtifactRegistrationError(f"artifact is not present: {entry.artifact_path}")
        new_bytes = _serialize_manifest(encoded)
        with self._locked() as root_fd:
            try:
                current, _original_bytes = self._load_unlocked(root_fd)
            except ArtifactManifestError:
                self._atomic_replace(new_bytes, root_fd)
                return True
            if current == encoded:
                return False
            raise ArtifactManifestError("artifact manifest became readable during recovery; retry the operation")

    def repair_path_conflicted_entries_atomically(
        self,
        repair: Callable[
            [Mapping[ArtifactKey, ArtifactManifestEntry]],
            Mapping[ArtifactKey, ArtifactManifestEntry],
        ],
    ) -> bool:
        """Repair only duplicate path ownership while holding the Manifest lock.

        The recovery view retains every other schema check. General reads stay
        strict, and the transformed whole snapshot must satisfy normal write
        invariants before one atomic replacement.
        """

        with self._locked() as root_fd:
            current, _original_bytes = self._load_unlocked(root_fd, validate_path_ownership=False)
            try:
                _assert_unique_artifact_paths(current)
            except ArtifactManifestError:
                pass
            else:
                raise ArtifactManifestError("artifact manifest became readable during recovery; retry the operation")
            decoded = {ArtifactKey.decode(encoded): entry for encoded, entry in current.items()}
            replacement = _encode_target_entries(repair(decoded))
            new_bytes = _serialize_manifest(replacement)
            self._atomic_replace(new_bytes, root_fd)
            return True

    def delete_entry(self, key: ArtifactKey) -> bool:
        with self._locked() as root_fd:
            entries, original_bytes = self._load_unlocked(root_fd)
            if entries.pop(key.encode(), None) is None:
                return False
            if not entries:
                try:
                    if root_fd is None:
                        (self._project_dir / MANIFEST_FILENAME).unlink()
                    else:
                        os.unlink(MANIFEST_FILENAME, dir_fd=root_fd)
                except FileNotFoundError:
                    return True
                except OSError as exc:
                    raise ArtifactManifestError(f"cannot remove empty artifact manifest: {exc}") from exc
                return True
            new_bytes = _serialize_manifest(entries)
            if original_bytes == new_bytes:
                return False
            self._atomic_replace(new_bytes, root_fd)
            return True

    def replace_entry_if_matches(
        self,
        key: ArtifactKey,
        *,
        expected: ArtifactManifestEntry,
        replacement: ArtifactManifestEntry | None,
    ) -> bool:
        """Restore one entry only while the caller's registered claim still wins."""

        return self.replace_entries_if_matches_atomically(
            expected={key: expected},
            replacements={key: replacement},
        )

    def replace_entries_if_matches_atomically(
        self,
        *,
        expected: Mapping[ArtifactKey, ArtifactManifestEntry | None],
        replacements: Mapping[ArtifactKey, ArtifactManifestEntry | None],
    ) -> bool:
        """Compare-and-swap claims while preserving unrelated Manifest entries."""

        encoded_expected = _encode_optional_entries(expected)
        encoded_replacements = _encode_optional_entries(replacements)
        with self._locked() as root_fd:
            entries, original_bytes = self._load_unlocked(root_fd)
            if any(entries.get(key) != entry for key, entry in encoded_expected.items()):
                return False
            for key, entry in encoded_replacements.items():
                if entry is None:
                    entries.pop(key, None)
                else:
                    entries[key] = entry
            if not entries:
                try:
                    if root_fd is None:
                        (self._project_dir / MANIFEST_FILENAME).unlink()
                    else:
                        os.unlink(MANIFEST_FILENAME, dir_fd=root_fd)
                except FileNotFoundError:
                    # Deletion is idempotent; another writer may already have removed the empty sidecar.
                    pass
                except OSError as exc:
                    raise ArtifactManifestError(f"cannot remove empty artifact manifest: {exc}") from exc
                return original_bytes is not None
            new_bytes = _serialize_manifest(entries)
            if original_bytes == new_bytes:
                return False
            self._atomic_replace(new_bytes, root_fd)
            return True

    def replace_snapshot_if_matches_atomically(
        self,
        *,
        expected: Mapping[ArtifactKey, ArtifactManifestEntry],
        replacement: Mapping[ArtifactKey, ArtifactManifestEntry],
    ) -> bool:
        """Compare and replace the complete Manifest state under one lock."""

        encoded_expected = _encode_target_entries(expected)
        encoded_replacement = _encode_target_entries(replacement)
        new_bytes = _serialize_manifest(encoded_replacement)
        with self._locked() as root_fd:
            current, original_bytes = self._load_unlocked(root_fd)
            if current != encoded_expected:
                return False
            if current == encoded_replacement:
                return True
            if not encoded_replacement:
                try:
                    if root_fd is None:
                        (self._project_dir / MANIFEST_FILENAME).unlink()
                    else:
                        os.unlink(MANIFEST_FILENAME, dir_fd=root_fd)
                except FileNotFoundError:
                    # Deletion is idempotent; another writer may already have removed the empty sidecar.
                    pass
                except OSError as exc:
                    raise ArtifactManifestError(f"cannot remove empty artifact manifest: {exc}") from exc
                return True
            if original_bytes == new_bytes:
                return True
            self._atomic_replace(new_bytes, root_fd)
            return True

    def replace_entries_atomically(
        self,
        entries: Mapping[ArtifactKey, ArtifactManifestEntry],
    ) -> bool:
        """Replace the entire manifest through one lock and one atomic rename.

        Activation plans are complete target states.  Persisting them entry by
        entry would expose a partly activated project after interruption, so the
        storage boundary accepts and serializes the full set at once.
        """

        encoded = _encode_target_entries(entries)
        for entry in encoded.values():
            observation = self.inspect_artifact(entry.artifact_path)
            if observation.blocker is not None:
                raise ArtifactRegistrationError(observation.blocker.detail)
            if not observation.present:
                raise ArtifactRegistrationError(f"artifact is not present: {entry.artifact_path}")
        new_bytes = _serialize_manifest(encoded)
        with self._locked() as root_fd:
            _current, original_bytes = self._load_unlocked(root_fd)
            if original_bytes == new_bytes:
                return False
            self._atomic_replace(new_bytes, root_fd)
            return True

    @contextmanager
    def _locked(self) -> Iterator[int | None]:
        lock_path = self._project_dir / LOCK_FILENAME
        with contextlib.ExitStack() as root_stack:
            root_fd: int | None = None
            checked_lock_identity: tuple[int, int] | None = None
            try:
                if os.name == "posix":
                    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | self._nofollow_flag
                    if not self._nofollow_flag and _is_linkish(self._project_dir):
                        raise ArtifactManifestError(f"project directory is a symlink or junction: {self._project_dir}")
                    try:
                        root_fd = os.open(self._project_dir, root_flags)
                    except OSError as exc:
                        raise ArtifactManifestError(
                            f"project directory cannot be opened safely: {self._project_dir}: {exc}"
                        ) from exc
                    self._assert_open_project_root_identity(root_fd)
                else:
                    root_stack.enter_context(self._guard_portable_project_root())
                if root_fd is None or not self._nofollow_flag:
                    checked_lock_identity = self._runtime_file_identity(lock_path, "manifest lock")
                flags = os.O_WRONLY | self._nofollow_flag | getattr(os, "O_NONBLOCK", 0)
                try:
                    if root_fd is not None:
                        try:
                            fd = os.open(LOCK_FILENAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd)
                        except FileExistsError:
                            fd = os.open(LOCK_FILENAME, flags, dir_fd=root_fd)
                    else:
                        fd = os.open(lock_path, flags | os.O_CREAT, 0o600)
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise ArtifactManifestError(f"manifest lock is a symlink: {lock_path}") from exc
                    raise ArtifactManifestError(f"cannot open manifest lock: {lock_path}: {exc}") from exc
                try:
                    if root_fd is None or not self._nofollow_flag:
                        self._assert_open_runtime_file_identity(
                            lock_path,
                            fd,
                            checked_lock_identity,
                            "manifest lock",
                        )
                    elif not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise ArtifactManifestError(f"manifest lock is not a regular file: {lock_path}")
                    handle = os.fdopen(fd, "wb")
                except BaseException:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                    raise
                deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
                try:
                    while True:
                        try:
                            portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                            break
                        except (portalocker.AlreadyLocked, portalocker.LockException) as exc:
                            if time.monotonic() >= deadline:
                                raise ArtifactManifestError(f"timed out acquiring manifest lock: {lock_path}") from exc
                            time.sleep(0.05)
                    try:
                        yield root_fd
                    finally:
                        portalocker.unlock(handle)
                finally:
                    handle.close()
            finally:
                if root_fd is not None:
                    os.close(root_fd)

    @contextmanager
    def _guard_portable_project_root(self) -> Iterator[None]:
        windows_handle = _open_windows_directory_handle(self._project_dir) if os.name == "nt" else None
        try:
            self._assert_portable_project_root_identity()
            yield
            self._assert_portable_project_root_identity()
        finally:
            if windows_handle is not None:
                _close_windows_handle(windows_handle)

    def _assert_portable_project_root_identity(self) -> None:
        if _is_linkish(self._project_dir):
            raise ArtifactManifestError(f"project directory is a symlink or junction: {self._project_dir}")
        try:
            root_stat = self._project_dir.stat(follow_symlinks=False)
        except OSError as exc:
            raise ArtifactManifestError(f"project directory is unavailable: {self._project_dir}: {exc}") from exc
        if not stat.S_ISDIR(root_stat.st_mode) or (root_stat.st_dev, root_stat.st_ino) != self._project_identity:
            raise ArtifactManifestError(f"project directory changed after adapter initialization: {self._project_dir}")

    def _assert_open_project_root_identity(self, root_fd: int) -> None:
        try:
            root_stat = os.fstat(root_fd)
        except OSError as exc:
            raise ArtifactManifestError(f"opened project directory is unavailable: {self._project_dir}: {exc}") from exc
        if not stat.S_ISDIR(root_stat.st_mode) or (root_stat.st_dev, root_stat.st_ino) != self._project_identity:
            raise ArtifactManifestError(f"project directory changed after adapter initialization: {self._project_dir}")

    @staticmethod
    def _runtime_file_identity(path: Path, label: str) -> tuple[int, int] | None:
        if _is_linkish(path):
            raise ArtifactManifestError(f"{label} is a symlink or junction: {path}")
        try:
            file_stat = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ArtifactManifestError(f"{label} is unavailable: {path}: {exc}") from exc
        if not stat.S_ISREG(file_stat.st_mode):
            raise ArtifactManifestError(f"{label} is not a regular file: {path}")
        return file_stat.st_dev, file_stat.st_ino

    def _assert_open_runtime_file_identity(
        self,
        path: Path,
        fd: int,
        expected_identity: tuple[int, int] | None,
        label: str,
    ) -> None:
        try:
            opened_stat = os.fstat(fd)
        except OSError as exc:
            raise ArtifactManifestError(f"opened {label} is unavailable: {path}: {exc}") from exc
        current_identity = self._runtime_file_identity(path, label)
        opened_identity = (opened_stat.st_dev, opened_stat.st_ino)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or current_identity is None
            or current_identity != opened_identity
            or (expected_identity is not None and current_identity != expected_identity)
        ):
            raise ArtifactManifestError(f"{label} changed while it was being opened: {path}")

    def _load_unlocked(
        self,
        root_fd: int | None,
        *,
        validate_path_ownership: bool = True,
    ) -> tuple[dict[str, ArtifactManifestEntry], bytes | None]:
        path = self._project_dir / MANIFEST_FILENAME
        checked_manifest_identity: tuple[int, int] | None = None
        if root_fd is None or not self._nofollow_flag:
            checked_manifest_identity = self._runtime_file_identity(path, "artifact manifest")
            if checked_manifest_identity is None:
                return {}, None
        flags = os.O_RDONLY | self._nofollow_flag | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(MANIFEST_FILENAME, flags, dir_fd=root_fd) if root_fd is not None else os.open(path, flags)
        except FileNotFoundError:
            return {}, None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ArtifactManifestError(f"artifact manifest is a symlink: {path}") from exc
            raise ArtifactManifestError(f"cannot open artifact manifest: {path}: {exc}") from exc
        try:
            if root_fd is None or not self._nofollow_flag:
                self._assert_open_runtime_file_identity(
                    path,
                    fd,
                    checked_manifest_identity,
                    "artifact manifest",
                )
            elif not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ArtifactManifestError(f"artifact manifest is not a regular file: {path}")
            handle = os.fdopen(fd, "rb")
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        try:
            with handle:
                raw = handle.read()
        except OSError as exc:
            raise ArtifactManifestError(f"cannot read artifact manifest: {path}: {exc}") from exc
        return _parse_manifest(raw, validate_path_ownership=validate_path_ownership), raw

    def _atomic_replace(self, content: bytes, root_fd: int | None) -> None:
        if root_fd is None:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f"{MANIFEST_FILENAME}.",
                suffix=".tmp",
                dir=self._project_dir,
            )
        else:
            fd, tmp_name = _create_temporary_file(root_fd, nofollow_flag=self._nofollow_flag)
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                _unlink_temporary_file(tmp_name, root_fd)
            raise
        try:
            with handle:
                if os.name == "posix":
                    os.fchmod(handle.fileno(), 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if root_fd is None:
                os.replace(tmp_name, self._project_dir / MANIFEST_FILENAME)
            else:
                os.replace(tmp_name, MANIFEST_FILENAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        except OSError as exc:
            with contextlib.suppress(OSError):
                _unlink_temporary_file(tmp_name, root_fd)
            raise ArtifactManifestError(f"cannot replace artifact manifest: {exc}") from exc
        except BaseException:
            with contextlib.suppress(OSError):
                _unlink_temporary_file(tmp_name, root_fd)
            raise


@dataclass(frozen=True, slots=True, init=False)
class ArtifactBasis:
    """Canonical, immutable evidence describing an artifact's direct inputs."""

    kind: str
    kind_version: int
    _normalized: bytes
    digest: str

    def __init__(self, kind: str, *, kind_version: int, inputs: Mapping[str, object]) -> None:
        if not kind:
            raise ValueError("basis kind must be a non-empty string")
        if type(kind_version) is not int or kind_version < 1:
            raise ValueError("basis kind_version must be a positive integer")
        normalized_inputs = _normalize_json(inputs)
        payload = {
            "inputs": normalized_inputs,
            "kind": kind,
            "kind_version": kind_version,
        }
        normalized = canonical_json_bytes(payload, allow_nan=False)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "kind_version", kind_version)
        object.__setattr__(self, "_normalized", normalized)
        object.__setattr__(self, "digest", prefixed(hashlib.sha256(normalized).hexdigest()))

    @classmethod
    def build(cls, kind: str, *, kind_version: int, inputs: Mapping[str, object]) -> Self:
        return cls(kind, kind_version=kind_version, inputs=inputs)

    def normalized_bytes(self) -> bytes:
        return self._normalized

    def to_evidence_dict(self) -> dict[str, object]:
        """Return the complete canonical basis together with its verified digest."""

        payload = json.loads(self._normalized)
        if not isinstance(payload, dict):  # pragma: no cover - constructor invariant
            raise RuntimeError("canonical artifact basis is not an object")
        return {**payload, "digest": self.digest}

    @classmethod
    def from_evidence_dict(cls, value: object) -> Self:
        """Parse complete portable evidence and verify its canonical digest."""

        if not isinstance(value, Mapping) or set(value) != {"kind", "kind_version", "inputs", "digest"}:
            raise ValueError("artifact basis evidence has an invalid schema")
        kind = value["kind"]
        kind_version = value["kind_version"]
        inputs = value["inputs"]
        digest = value["digest"]
        if not isinstance(kind, str) or not isinstance(inputs, Mapping):
            raise ValueError("artifact basis evidence has invalid canonical inputs")
        basis = cls(kind, kind_version=cast(int, kind_version), inputs=cast(Mapping[str, object], inputs))
        if not isinstance(digest, str) or basis.digest != digest:
            raise ValueError("artifact basis evidence digest does not match its canonical inputs")
        return basis


@dataclass(frozen=True, slots=True)
class ArtifactBasisDescriptor:
    """Strict, portable identity for a canonical basis used as source evidence."""

    kind: str
    kind_version: int
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise ValueError("artifact basis descriptor kind must be a non-empty string")
        if type(self.kind_version) is not int or self.kind_version < 1:
            raise ValueError("artifact basis descriptor kind_version must be a positive integer")
        if not isinstance(self.digest, str) or PREFIXED_DIGEST_RE.fullmatch(self.digest) is None:
            raise ValueError("artifact basis descriptor digest must be a canonical sha256-v1 digest")

    @classmethod
    def from_basis(cls, basis: ArtifactBasis) -> Self:
        if not isinstance(basis, ArtifactBasis):
            raise TypeError("basis must be an ArtifactBasis")
        return cls(kind=basis.kind, kind_version=basis.kind_version, digest=basis.digest)

    @classmethod
    def from_dict(cls, value: object) -> Self:
        if not isinstance(value, Mapping):
            raise ValueError("artifact basis descriptor must be an object")
        if set(value) != {"kind", "kind_version", "digest"}:
            raise ValueError("artifact basis descriptor has an invalid schema")
        return cls(
            kind=cast(str, value["kind"]),
            kind_version=cast(int, value["kind_version"]),
            digest=cast(str, value["digest"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "kind_version": self.kind_version,
            "digest": self.digest,
        }


def compose_video_artifact_basis(
    *,
    visual: ArtifactBasis | ArtifactBasisDescriptor,
    speech: ArtifactBasis | ArtifactBasisDescriptor | None = None,
    duration: ArtifactBasis | ArtifactBasisDescriptor | None = None,
) -> ArtifactBasis:
    """Compose independently owned video inputs into one manifest basis.

    The resulting basis is registered under the existing episode-video key. A
    change in any present component consequently produces one stale comparison,
    rather than parallel visual/speech/duration artifact states.
    """

    visual_descriptor = _coerce_artifact_basis_descriptor("visual", visual)
    speech_descriptor = _coerce_optional_artifact_basis_descriptor("speech", speech)
    duration_descriptor = _coerce_optional_artifact_basis_descriptor("duration", duration)
    return ArtifactBasis.build(
        "artifact-components/video",
        kind_version=1,
        inputs={
            "components": {
                "visual": visual_descriptor.to_dict(),
                "speech": speech_descriptor.to_dict() if speech_descriptor is not None else None,
                "duration": duration_descriptor.to_dict() if duration_descriptor is not None else None,
            }
        },
    )


def _coerce_artifact_basis_descriptor(
    field: str,
    value: ArtifactBasis | ArtifactBasisDescriptor,
) -> ArtifactBasisDescriptor:
    if isinstance(value, ArtifactBasis):
        return ArtifactBasisDescriptor.from_basis(value)
    if isinstance(value, ArtifactBasisDescriptor):
        return value
    raise TypeError(f"{field} must be an ArtifactBasis or ArtifactBasisDescriptor")


def _coerce_optional_artifact_basis_descriptor(
    field: str,
    value: ArtifactBasis | ArtifactBasisDescriptor | None,
) -> ArtifactBasisDescriptor | None:
    if value is None:
        return None
    return _coerce_artifact_basis_descriptor(field, value)


def encode_artifact_key_parts(kind: str, components: Sequence[object]) -> str:
    """Encode raw key parts into the manifest key wire form.

    Shared with :meth:`ArtifactKey.encode` so exactly one encoding exists, including
    for producers that cannot go through :class:`ArtifactKey` — a migration rewriting
    a key whose kind this build no longer defines has no typed key to encode.
    """

    payload = json.dumps([kind, *components], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return _KEY_PREFIX + token


class ArtifactKeyParts(NamedTuple):
    """A manifest key's raw parts, before any :class:`ArtifactKind` is required to exist."""

    kind: str
    components: tuple[object, ...]


def decode_artifact_key_parts(value: str) -> ArtifactKeyParts | None:
    """Decode a manifest key back into its raw kind and components.

    Returns ``None`` for anything that is not this encoding.  Unlike
    :meth:`ArtifactKey.decode` the kind is not required to still be a supported
    :class:`ArtifactKind`, which is what lets a migration rewrite a renamed kind.
    """

    if not value.startswith(_KEY_PREFIX):
        return None
    token = value.removeprefix(_KEY_PREFIX)
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], str):
        return None
    return ArtifactKeyParts(payload[0], tuple(cast(list[object], payload)[1:]))


@dataclass(frozen=True, slots=True)
class ArtifactKey:
    """Typed artifact identity with a canonical reversible wire representation."""

    kind: ArtifactKind
    components: tuple[str | int, ...]

    def __post_init__(self) -> None:
        valid = False
        if self.kind is ArtifactKind.ASSET_SHEET and len(self.components) == 2:
            asset_type, asset_id = self.components
            if isinstance(asset_type, str) and asset_type in ASSET_TYPES and isinstance(asset_id, str):
                canonical_asset_id = asset_name_comparison_key(asset_id)
                if canonical_asset_id:
                    object.__setattr__(self, "components", (asset_type, canonical_asset_id))
                    valid = True
        elif self.kind in {ArtifactKind.EPISODE_SCRIPT_PLAN, ArtifactKind.EPISODE_SCRIPT} and len(self.components) == 1:
            episode = self.components[0]
            valid = type(episode) is int and episode > 0
        elif (
            self.kind
            in {
                ArtifactKind.EPISODE_GRID,
                ArtifactKind.EPISODE_STORYBOARD,
                ArtifactKind.EPISODE_VIDEO,
                ArtifactKind.EPISODE_AUDIO,
            }
            and len(self.components) == 2
        ):
            episode, resource_id = self.components
            valid = type(episode) is int and episode > 0 and isinstance(resource_id, str) and bool(resource_id)
        elif (
            self.kind in {ArtifactKind.EPISODE_SUBTITLE, ArtifactKind.EPISODE_PRESENTATION}
            and len(self.components) == 3
        ):
            episode, resource_id, variant = self.components
            valid = (
                type(episode) is int
                and episode > 0
                and isinstance(resource_id, str)
                and bool(resource_id)
                and variant in {"post_production", "use_tts"}
            )
        if not valid:
            raise ValueError(f"artifact key components do not match {self.kind!r}: {self.components!r}")

    @classmethod
    def asset_sheet(cls, asset_type: str, asset_id: str) -> Self:
        if asset_type not in ASSET_TYPES:
            raise ValueError(f"unsupported asset type: {asset_type!r}")
        return cls(ArtifactKind.ASSET_SHEET, (asset_type, _non_empty("asset_id", asset_id)))

    @classmethod
    def episode_script_plan(cls, episode: int) -> Self:
        return cls(ArtifactKind.EPISODE_SCRIPT_PLAN, (_episode_number(episode),))

    @classmethod
    def episode_script(cls, episode: int) -> Self:
        return cls(ArtifactKind.EPISODE_SCRIPT, (_episode_number(episode),))

    @classmethod
    def episode_grid(cls, episode: int, group_id: str) -> Self:
        return cls(ArtifactKind.EPISODE_GRID, (_episode_number(episode), _non_empty("group_id", group_id)))

    @classmethod
    def episode_storyboard(cls, episode: int, resource_id: str) -> Self:
        return cls(
            ArtifactKind.EPISODE_STORYBOARD,
            (_episode_number(episode), _non_empty("resource_id", resource_id)),
        )

    @classmethod
    def episode_video(cls, episode: int, resource_id: str) -> Self:
        return cls(
            ArtifactKind.EPISODE_VIDEO,
            (_episode_number(episode), _non_empty("resource_id", resource_id)),
        )

    @classmethod
    def episode_audio(cls, episode: int, resource_id: str) -> Self:
        """Identify one storyboard item or reference-video unit's narration audio."""

        return cls(
            ArtifactKind.EPISODE_AUDIO,
            (_episode_number(episode), _non_empty("resource_id", resource_id)),
        )

    @classmethod
    def episode_subtitle(cls, episode: int, resource_id: str, variant: str) -> Self:
        """Identify one rendition variant's mechanical subtitle artifact."""

        return cls(
            ArtifactKind.EPISODE_SUBTITLE,
            (
                _episode_number(episode),
                _non_empty("resource_id", resource_id),
                _rendition_variant(variant),
            ),
        )

    @classmethod
    def episode_presentation(cls, episode: int, resource_id: str, variant: str) -> Self:
        """Identify one independently current final-presentation variant."""

        return cls(
            ArtifactKind.EPISODE_PRESENTATION,
            (
                _episode_number(episode),
                _non_empty("resource_id", resource_id),
                _rendition_variant(variant),
            ),
        )

    @classmethod
    def episode_resource_artifacts(cls, episode: int, resource_id: str) -> tuple[Self, ...]:
        """Enumerate every formal artifact identity owned by one script item."""

        return (
            cls.episode_storyboard(episode, resource_id),
            cls.episode_video(episode, resource_id),
            cls.episode_audio(episode, resource_id),
            cls.episode_subtitle(episode, resource_id, "post_production"),
            cls.episode_subtitle(episode, resource_id, "use_tts"),
            cls.episode_presentation(episode, resource_id, "post_production"),
            cls.episode_presentation(episode, resource_id, "use_tts"),
        )

    @property
    def episode_number(self) -> int | None:
        """Return the owning episode for any episode-scoped artifact key."""

        if self.kind is ArtifactKind.ASSET_SHEET:
            return None
        episode = self.components[0]
        return episode if type(episode) is int else None

    def encode(self) -> str:
        return encode_artifact_key_parts(self.kind.value, self.components)

    @classmethod
    def decode(cls, value: str) -> Self:
        if not value.startswith(_KEY_PREFIX):
            raise ValueError("artifact key has an unsupported encoding")
        parts = decode_artifact_key_parts(value)
        if parts is None:
            raise ValueError("artifact key is malformed")
        key = cls._from_parts(parts.kind, list(parts.components))
        if key.encode() != value:
            raise ValueError("artifact key is not canonical")
        return key

    @classmethod
    def _from_parts(cls, kind_value: str, parts: list[object]) -> Self:
        try:
            kind = ArtifactKind(kind_value)
        except ValueError as exc:
            raise ValueError(f"unsupported artifact kind: {kind_value!r}") from exc
        try:
            return cls(kind, cast(tuple[str | int, ...], tuple(parts)))
        except ValueError as exc:
            raise ValueError("artifact key payload does not match its kind") from exc


def _episode_number(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"episode must be a positive integer, got {value!r}")
    return value


def _non_empty(field: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _rendition_variant(value: object) -> str:
    if value not in {"post_production", "use_tts"}:
        raise ValueError(f"variant must be 'post_production' or 'use_tts', got {value!r}")
    return cast(str, value)


def _normalize_json(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("artifact basis does not permit non-finite numbers")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("artifact basis object keys must be strings")
            normalized[raw_key] = _normalize_json(raw_value)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    raise ValueError(f"artifact basis contains a non-JSON value: {type(value).__name__}")


def normalize_artifact_path(value: object) -> str:
    """Return the canonical project-relative POSIX form of a recorded artifact path.

    This is the single rule for what a registered artifact path may look like:
    project-relative, POSIX-separated, free of traversal, drive letters and
    runtime-owned names. Anything else raises ``ValueError`` — callers that
    only need a verdict catch it rather than re-deriving the rule.
    """

    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError(f"artifact path must be a non-empty project-relative POSIX path: {value!r}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"artifact path must be valid UTF-8: {value!r}") from exc
    raw_parts = value.split("/")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.drive
        or windows_path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
        or any(part.rstrip(" .") != part for part in raw_parts)
        or any(":" in part for part in raw_parts)
    ):
        raise ValueError(f"artifact path must be a canonical project-relative POSIX path: {value!r}")
    normalized = path.as_posix()
    if normalized in {".", ""}:
        raise ValueError(f"artifact path must name a file: {value!r}")
    windows_alias = normalized.rstrip(" .").casefold()
    if normalized in _RESERVED_ARTIFACT_PATHS or (
        len(path.parts) == 1 and windows_alias in _WINDOWS_RESERVED_ARTIFACT_PATHS
    ):
        raise ValueError(f"runtime-owned path cannot be registered as an artifact: {value!r}")
    return normalized


def _encode_target_entries(
    entries: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> dict[str, ArtifactManifestEntry]:
    encoded: dict[str, ArtifactManifestEntry] = {}
    for key, entry in entries.items():
        if not isinstance(key, ArtifactKey):
            raise TypeError("manifest target keys must be ArtifactKey values")
        if not isinstance(entry, ArtifactManifestEntry):
            raise TypeError("manifest target entries must be ArtifactManifestEntry values")
        normalized_path = normalize_artifact_path(entry.artifact_path)
        if normalized_path != entry.artifact_path:
            raise ValueError("manifest target artifact path must be canonical")
        if PREFIXED_DIGEST_RE.fullmatch(entry.basis_digest) is None:
            raise ValueError("manifest target basis digest must be a canonical sha256-v1 digest")
        encoded_key = key.encode()
        if encoded_key in encoded:
            raise ValueError(f"duplicate manifest target key: {encoded_key}")
        encoded[encoded_key] = entry
    return encoded


def _encode_optional_entries(
    entries: Mapping[ArtifactKey, ArtifactManifestEntry | None],
) -> dict[str, ArtifactManifestEntry | None]:
    present: dict[ArtifactKey, ArtifactManifestEntry] = {}
    for key, entry in entries.items():
        if entry is not None:
            present[key] = entry
    encoded_present = _encode_target_entries(present)
    encoded: dict[str, ArtifactManifestEntry | None] = {}
    for key, entry in entries.items():
        if not isinstance(key, ArtifactKey):
            raise TypeError("manifest compare-and-swap keys must be ArtifactKey values")
        encoded[key.encode()] = encoded_present.get(key.encode()) if entry is not None else None
    return encoded


def _entries_match(
    adapter: ArtifactManifestAdapter,
    expected: Mapping[ArtifactKey, ArtifactManifestEntry | None],
) -> bool:
    return all(adapter.get_entry(key) == entry for key, entry in expected.items())


def _serialize_manifest(entries: Mapping[str, ArtifactManifestEntry]) -> bytes:
    _assert_unique_artifact_paths(entries)
    payload = {
        "entries": {
            key: {
                "artifact_path": entry.artifact_path,
                "basis_digest": entry.basis_digest,
            }
            for key, entry in sorted(entries.items())
        },
        "hash_algorithm": HASH_ALGORITHM,
        "schema_version": MANIFEST_SCHEMA_VERSION,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _assert_unique_artifact_paths(entries: Mapping[str, ArtifactManifestEntry]) -> None:
    """Reject a target state in which two identities own one formal file."""

    owners: dict[str, str] = {}
    for key, entry in entries.items():
        filesystem_identity = unicodedata.normalize("NFC", entry.artifact_path).casefold()
        owner = owners.get(filesystem_identity)
        if owner is not None and owner != key:
            raise ArtifactManifestError(
                f"formal artifact path is claimed by multiple keys: {entry.artifact_path} ({owner}, {key})"
            )
        owners[filesystem_identity] = key


def encode_artifact_manifest_payload(
    snapshot: ArtifactManifestArchiveSnapshot,
) -> dict[str, object]:
    """Encode complete claims and their formal-byte evidence for an archive."""

    encoded = _encode_target_entries(snapshot.entries)
    content_digests: dict[str, str] = {}
    for key, digest in snapshot.content_digests.items():
        if not isinstance(key, ArtifactKey):
            raise TypeError("archive artifact content digest keys must be ArtifactKey values")
        if not isinstance(digest, str) or CONTENT_DIGEST_RE.fullmatch(digest) is None:
            raise ValueError("archive artifact content digest must be a lowercase SHA-256 digest")
        content_digests[key.encode()] = digest
    if set(content_digests) != set(encoded):
        raise ValueError("archive artifact content digests must cover the complete Manifest snapshot")
    return {
        "entries": {
            key: {
                "artifact_path": entry.artifact_path,
                "basis_digest": entry.basis_digest,
                "content_digest": content_digests[key],
            }
            for key, entry in sorted(encoded.items())
        },
        "hash_algorithm": HASH_ALGORITHM,
        "schema_version": ARCHIVE_MANIFEST_SCHEMA_VERSION,
    }


def decode_artifact_manifest_payload(payload: object) -> ArtifactManifestArchiveSnapshot:
    """Strictly decode claims and formal-byte evidence from an archive envelope."""

    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except RecursionError as exc:
        raise ArtifactManifestError("archive artifact manifest payload exceeds the JSON nesting limit") from exc
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ArtifactManifestError(f"artifact manifest payload is not JSON: {exc}") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArtifactManifestError(f"archive artifact manifest is not valid UTF-8 JSON: {exc}") from exc
    except RecursionError as exc:
        raise ArtifactManifestError("archive artifact manifest exceeds the JSON nesting limit") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"entries", "hash_algorithm", "schema_version"}:
        raise ArtifactManifestError("archive artifact manifest has an invalid top-level schema")
    if type(decoded["schema_version"]) is not int or decoded["schema_version"] != ARCHIVE_MANIFEST_SCHEMA_VERSION:
        raise ArtifactManifestError(
            f"unsupported archive artifact manifest schema_version: {decoded['schema_version']!r}"
        )
    if decoded["hash_algorithm"] != HASH_ALGORITHM:
        raise ArtifactManifestError(
            f"unsupported archive artifact manifest hash_algorithm: {decoded['hash_algorithm']!r}"
        )
    raw_entries = decoded["entries"]
    if not isinstance(raw_entries, dict):
        raise ArtifactManifestError("archive artifact manifest entries must be an object")

    entries: dict[ArtifactKey, ArtifactManifestEntry] = {}
    content_digests: dict[ArtifactKey, str] = {}
    encoded_entries: dict[str, ArtifactManifestEntry] = {}
    for encoded_key, raw_entry in raw_entries.items():
        if not isinstance(encoded_key, str):
            raise ArtifactManifestError("archive artifact manifest entry keys must be strings")
        try:
            key = ArtifactKey.decode(encoded_key)
        except ValueError as exc:
            raise ArtifactManifestError(f"archive artifact manifest contains an invalid key: {encoded_key!r}") from exc
        if not isinstance(raw_entry, dict) or set(raw_entry) != {
            "artifact_path",
            "basis_digest",
            "content_digest",
        }:
            raise ArtifactManifestError(f"archive artifact manifest entry has an invalid schema: {encoded_key}")
        artifact_path = raw_entry["artifact_path"]
        basis_digest = raw_entry["basis_digest"]
        content_digest = raw_entry["content_digest"]
        try:
            normalized_path = normalize_artifact_path(artifact_path)
        except (TypeError, ValueError) as exc:
            raise ArtifactManifestError(f"archive artifact manifest entry has an invalid path: {encoded_key}") from exc
        if normalized_path != artifact_path:
            raise ArtifactManifestError(f"archive artifact manifest entry path is not canonical: {encoded_key}")
        if not isinstance(basis_digest, str) or PREFIXED_DIGEST_RE.fullmatch(basis_digest) is None:
            raise ArtifactManifestError(f"archive artifact manifest entry has an invalid basis digest: {encoded_key}")
        if not isinstance(content_digest, str) or CONTENT_DIGEST_RE.fullmatch(content_digest) is None:
            raise ArtifactManifestError(f"archive artifact manifest entry has an invalid content digest: {encoded_key}")
        entry = ArtifactManifestEntry(artifact_path=normalized_path, basis_digest=basis_digest)
        entries[key] = entry
        content_digests[key] = content_digest
        encoded_entries[encoded_key] = entry
    _assert_unique_artifact_paths(encoded_entries)
    return ArtifactManifestArchiveSnapshot(entries=entries, content_digests=content_digests)


def _parse_manifest(raw: bytes, *, validate_path_ownership: bool = True) -> dict[str, ArtifactManifestEntry]:
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ArtifactManifestError(f"artifact manifest is not valid UTF-8 JSON: {exc}") from exc
    except RecursionError as exc:
        raise ArtifactManifestError("artifact manifest exceeds the JSON nesting limit") from exc
    if not isinstance(payload, dict) or set(payload) != {"entries", "hash_algorithm", "schema_version"}:
        raise ArtifactManifestError("artifact manifest has an invalid top-level schema")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ArtifactManifestError(f"unsupported artifact manifest schema_version: {payload['schema_version']!r}")
    if payload["hash_algorithm"] != HASH_ALGORITHM:
        raise ArtifactManifestError(f"unsupported artifact manifest hash_algorithm: {payload['hash_algorithm']!r}")
    raw_entries = payload["entries"]
    if not isinstance(raw_entries, dict):
        raise ArtifactManifestError("artifact manifest entries must be an object")
    entries: dict[str, ArtifactManifestEntry] = {}
    for encoded_key, raw_entry in raw_entries.items():
        if not isinstance(encoded_key, str):
            raise ArtifactManifestError("artifact manifest entry keys must be strings")
        try:
            ArtifactKey.decode(encoded_key)
        except ValueError as exc:
            raise ArtifactManifestError(f"artifact manifest contains an invalid key: {encoded_key!r}") from exc
        if not isinstance(raw_entry, dict) or set(raw_entry) != {"artifact_path", "basis_digest"}:
            raise ArtifactManifestError(f"artifact manifest entry has an invalid schema: {encoded_key}")
        artifact_path = raw_entry["artifact_path"]
        basis_digest = raw_entry["basis_digest"]
        try:
            normalized_path = normalize_artifact_path(artifact_path)
        except (TypeError, ValueError) as exc:
            raise ArtifactManifestError(f"artifact manifest entry has an invalid path: {encoded_key}") from exc
        if normalized_path != artifact_path:
            raise ArtifactManifestError(f"artifact manifest entry path is not canonical: {encoded_key}")
        if not isinstance(basis_digest, str) or PREFIXED_DIGEST_RE.fullmatch(basis_digest) is None:
            raise ArtifactManifestError(f"artifact manifest entry has an invalid basis digest: {encoded_key}")
        entries[encoded_key] = ArtifactManifestEntry(
            artifact_path=normalized_path,
            basis_digest=basis_digest,
        )
    if validate_path_ownership:
        _assert_unique_artifact_paths(entries)
    return entries


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise ArtifactManifestError(f"artifact manifest contains a duplicate field: {key!r}")
        payload[key] = value
    return payload


def _is_linkish(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _open_windows_directory_handle(path: Path) -> int:
    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0,
        0x00000001 | 0x00000002,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        error_code = ctypes.get_last_error()
        raise ArtifactManifestError(f"project directory cannot be held safely: {path}: winerror {error_code}")
    return cast(int, handle)


def _close_windows_handle(handle: int) -> None:
    kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _create_temporary_file(root_fd: int, *, nofollow_flag: int) -> tuple[int, str]:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow_flag
    for _ in range(100):
        tmp_name = f"{MANIFEST_FILENAME}.{secrets.token_hex(8)}.tmp"
        try:
            return os.open(tmp_name, flags, 0o600, dir_fd=root_fd), tmp_name
        except FileExistsError:
            continue
        except OSError as exc:
            raise ArtifactManifestError(f"cannot create temporary artifact manifest: {exc}") from exc
    raise ArtifactManifestError("cannot allocate a unique temporary artifact manifest")


def _unlink_temporary_file(tmp_name: str, root_fd: int | None) -> None:
    if root_fd is None:
        os.unlink(tmp_name)
    else:
        os.unlink(tmp_name, dir_fd=root_fd)
