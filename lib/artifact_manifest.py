"""Project-local artifact identity, provenance, and manifest storage."""

from __future__ import annotations

import base64
import binascii
import contextlib
import errno
import hashlib
import json
import math
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, Self, cast

import portalocker

from lib.asset_types import ASSET_TYPES

_KEY_PREFIX = "artifact-key-v1:"
MANIFEST_FILENAME = ".arcreel_artifacts.json"
LOCK_FILENAME = ".artifact_manifest.lock"
MANIFEST_SCHEMA_VERSION = 1
HASH_ALGORITHM = "sha256-v1"
LOCK_TIMEOUT_SECONDS = 10.0
_DIGEST_RE = re.compile(r"sha256-v1:[0-9a-f]{64}\Z")
_RESERVED_ARTIFACT_PATHS = frozenset({MANIFEST_FILENAME, LOCK_FILENAME})


class ArtifactKind(StrEnum):
    """Kinds supported by the project artifact manifest schema."""

    ASSET_SHEET = "asset-sheet"
    EPISODE_STEP1 = "episode-step1"
    EPISODE_SCRIPT = "episode-script"
    EPISODE_GRID = "episode-grid"
    EPISODE_STORYBOARD = "episode-storyboard"
    EPISODE_VIDEO = "episode-video"
    EPISODE_AUDIO = "episode-audio"


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
class ArtifactObservation:
    artifact_path: str
    present: bool
    blocker: ArtifactBlocker | None = None


class ArtifactManifestAdapter(Protocol):
    """Storage seam used by the artifact manifest domain module."""

    def inspect_artifact(self, artifact_path: str) -> ArtifactObservation:
        raise NotImplementedError

    def get_entry(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        raise NotImplementedError

    def put_entry(self, key: ArtifactKey, entry: ArtifactManifestEntry) -> bool:
        raise NotImplementedError


class ArtifactManifest:
    """Register and compare canonical artifact bases through one storage seam."""

    def __init__(self, adapter: ArtifactManifestAdapter) -> None:
        self._adapter = adapter

    def register(self, key: ArtifactKey, *, artifact_path: str, basis: ArtifactBasis) -> bool:
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

    def compare(self, key: ArtifactKey, *, artifact_path: str, basis: ArtifactBasis) -> ArtifactComparison:
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
        status = (
            ArtifactStatus.CURRENT
            if entry.artifact_path == observation.artifact_path and entry.basis_digest == basis.digest
            else ArtifactStatus.STALE
        )
        return ArtifactComparison(status=status, artifact_path=observation.artifact_path)


class InMemoryArtifactManifestAdapter:
    """Thread-safe in-memory adapter for isolated domain tests and ephemeral callers."""

    def __init__(self, *, artifacts: set[str] | None = None) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, ArtifactManifestEntry] = {}
        self._artifacts = {_normalize_relative_path(path) for path in artifacts or set()}
        self._blockers: dict[str, ArtifactBlocker] = {}

    def inspect_artifact(self, artifact_path: str) -> ArtifactObservation:
        try:
            normalized = _normalize_relative_path(artifact_path)
        except ValueError as exc:
            blocker = ArtifactBlocker(code="artifact_path_invalid", path=str(artifact_path), detail=str(exc))
            return ArtifactObservation(artifact_path=str(artifact_path), present=False, blocker=blocker)
        with self._lock:
            blocker = self._blockers.get(normalized)
            return ArtifactObservation(
                artifact_path=normalized,
                present=normalized in self._artifacts and blocker is None,
                blocker=blocker,
            )

    def get_entry(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        with self._lock:
            return self._entries.get(key.encode())

    def put_entry(self, key: ArtifactKey, entry: ArtifactManifestEntry) -> bool:
        with self._lock:
            encoded = key.encode()
            if self._entries.get(encoded) == entry:
                return False
            self._entries[encoded] = entry
            return True

    def remove_artifact(self, artifact_path: str) -> None:
        normalized = _normalize_relative_path(artifact_path)
        with self._lock:
            self._artifacts.discard(normalized)
            self._blockers.pop(normalized, None)

    def block_artifact(self, artifact_path: str, *, code: str, detail: str) -> None:
        normalized = _normalize_relative_path(artifact_path)
        with self._lock:
            self._artifacts.discard(normalized)
            self._blockers[normalized] = ArtifactBlocker(code=code, path=normalized, detail=detail)


class ProjectArtifactManifestAdapter:
    """Safe project-directory adapter backed by a versioned JSON manifest."""

    def __init__(self, project_dir: Path) -> None:
        if _is_linkish(project_dir):
            raise ArtifactManifestError(f"project directory is a symlink or junction: {project_dir}")
        try:
            resolved = project_dir.resolve(strict=True)
        except OSError as exc:
            raise ArtifactManifestError(f"project directory is unavailable: {project_dir}") from exc
        if not resolved.is_dir():
            raise ArtifactManifestError(f"project path is not a directory: {resolved}")
        self._project_dir = resolved

    def inspect_artifact(self, artifact_path: str) -> ArtifactObservation:
        try:
            normalized = _normalize_relative_path(artifact_path)
        except ValueError as exc:
            blocker = ArtifactBlocker(code="artifact_path_invalid", path=str(artifact_path), detail=str(exc))
            return ArtifactObservation(artifact_path=str(artifact_path), present=False, blocker=blocker)
        if os.name == "posix":
            return self._inspect_artifact_posix(normalized)
        return self._inspect_artifact_portable(normalized)

    def _inspect_artifact_posix(self, normalized: str) -> ArtifactObservation:
        parts = PurePosixPath(normalized).parts
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            root_fd = os.open(self._project_dir, directory_flags)
        except OSError as exc:
            return self._artifact_blocked(normalized, "artifact_unreadable", f"project directory is unreadable: {exc}")
        with contextlib.ExitStack() as stack:
            stack.callback(os.close, root_fd)
            directory_fd = root_fd
            for part in parts[:-1]:
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
                directory_fd = next_fd
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
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    return self._artifact_blocked(
                        normalized,
                        "artifact_not_regular_file",
                        f"artifact path is not a regular file: {normalized}",
                    )
                os.read(fd, 1)
            except OSError as exc:
                return self._artifact_blocked(
                    normalized,
                    "artifact_unreadable",
                    f"artifact is unreadable: {normalized}: {exc}",
                )
        return ArtifactObservation(artifact_path=normalized, present=True)

    def _inspect_artifact_portable(self, normalized: str) -> ArtifactObservation:
        if _is_linkish(self._project_dir):
            return self._artifact_blocked(
                normalized,
                "artifact_symlink",
                f"project directory is a symlink or junction: {self._project_dir}",
            )
        path = self._project_dir.joinpath(*PurePosixPath(normalized).parts)
        cursor = self._project_dir
        for part in PurePosixPath(normalized).parts:
            cursor = cursor / part
            if _is_linkish(cursor):
                blocker = ArtifactBlocker(
                    code="artifact_symlink",
                    path=normalized,
                    detail=f"artifact path contains a symlink or junction: {normalized}",
                )
                return ArtifactObservation(artifact_path=normalized, present=False, blocker=blocker)
            if not cursor.exists():
                return ArtifactObservation(artifact_path=normalized, present=False)
        if not path.is_file():
            blocker = ArtifactBlocker(
                code="artifact_not_regular_file",
                path=normalized,
                detail=f"artifact path is not a regular file: {normalized}",
            )
            return ArtifactObservation(artifact_path=normalized, present=False, blocker=blocker)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
            try:
                os.read(fd, 1)
            finally:
                os.close(fd)
        except OSError as exc:
            blocker = ArtifactBlocker(
                code="artifact_unreadable",
                path=normalized,
                detail=f"artifact is unreadable: {normalized}: {exc}",
            )
            return ArtifactObservation(artifact_path=normalized, present=False, blocker=blocker)
        return ArtifactObservation(artifact_path=normalized, present=True)

    @staticmethod
    def _artifact_blocked(normalized: str, code: str, detail: str) -> ArtifactObservation:
        return ArtifactObservation(
            artifact_path=normalized,
            present=False,
            blocker=ArtifactBlocker(code=code, path=normalized, detail=detail),
        )

    def get_entry(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        with self._locked() as root_fd:
            entries, _ = self._load_unlocked(root_fd)
            return entries.get(key.encode())

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

    @contextmanager
    def _locked(self) -> Iterator[int | None]:
        root_fd: int | None = None
        if os.name == "posix":
            root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                root_fd = os.open(self._project_dir, root_flags)
            except OSError as exc:
                raise ArtifactManifestError(
                    f"project directory cannot be opened safely: {self._project_dir}: {exc}"
                ) from exc
        elif _is_linkish(self._project_dir):
            raise ArtifactManifestError(f"project directory is a symlink or junction: {self._project_dir}")
        lock_path = self._project_dir / LOCK_FILENAME
        try:
            if root_fd is None and _is_linkish(lock_path):
                raise ArtifactManifestError(f"manifest lock is a symlink or junction: {lock_path}")
            flags = os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            try:
                fd = (
                    os.open(LOCK_FILENAME, flags, 0o600, dir_fd=root_fd)
                    if root_fd is not None
                    else os.open(lock_path, flags, 0o600)
                )
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ArtifactManifestError(f"manifest lock is a symlink: {lock_path}") from exc
                raise ArtifactManifestError(f"cannot open manifest lock: {lock_path}: {exc}") from exc
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
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

    def _load_unlocked(self, root_fd: int | None) -> tuple[dict[str, ArtifactManifestEntry], bytes | None]:
        path = self._project_dir / MANIFEST_FILENAME
        if root_fd is None and _is_linkish(path):
            raise ArtifactManifestError(f"artifact manifest is a symlink or junction: {path}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(MANIFEST_FILENAME, flags, dir_fd=root_fd) if root_fd is not None else os.open(path, flags)
        except FileNotFoundError:
            return {}, None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ArtifactManifestError(f"artifact manifest is a symlink: {path}") from exc
            raise ArtifactManifestError(f"cannot open artifact manifest: {path}: {exc}") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
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
        return _parse_manifest(raw), raw

    def _atomic_replace(self, content: bytes, root_fd: int | None) -> None:
        if root_fd is None:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f"{MANIFEST_FILENAME}.",
                suffix=".tmp",
                dir=self._project_dir,
            )
        else:
            fd, tmp_name = _create_temporary_file(root_fd)
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
        normalized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "kind_version", kind_version)
        object.__setattr__(self, "_normalized", normalized)
        object.__setattr__(self, "digest", f"sha256-v1:{hashlib.sha256(normalized).hexdigest()}")

    @classmethod
    def build(cls, kind: str, *, kind_version: int, inputs: Mapping[str, object]) -> Self:
        return cls(kind, kind_version=kind_version, inputs=inputs)

    def normalized_bytes(self) -> bytes:
        return self._normalized


@dataclass(frozen=True, slots=True)
class ArtifactKey:
    """Typed artifact identity with a canonical reversible wire representation."""

    kind: ArtifactKind
    components: tuple[str | int, ...]

    def __post_init__(self) -> None:
        valid = False
        if self.kind is ArtifactKind.ASSET_SHEET and len(self.components) == 2:
            asset_type, asset_id = self.components
            valid = (
                isinstance(asset_type, str)
                and asset_type in ASSET_TYPES
                and isinstance(asset_id, str)
                and bool(asset_id)
            )
        elif self.kind in {ArtifactKind.EPISODE_STEP1, ArtifactKind.EPISODE_SCRIPT} and len(self.components) == 1:
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
        if not valid:
            raise ValueError(f"artifact key components do not match {self.kind!r}: {self.components!r}")

    @classmethod
    def asset_sheet(cls, asset_type: str, asset_id: str) -> Self:
        if asset_type not in ASSET_TYPES:
            raise ValueError(f"unsupported asset type: {asset_type!r}")
        return cls(ArtifactKind.ASSET_SHEET, (asset_type, _non_empty("asset_id", asset_id)))

    @classmethod
    def episode_step1(cls, episode: int) -> Self:
        return cls(ArtifactKind.EPISODE_STEP1, (_episode_number(episode),))

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
    def episode_audio(cls, episode: int, segment_id: str) -> Self:
        return cls(
            ArtifactKind.EPISODE_AUDIO,
            (_episode_number(episode), _non_empty("segment_id", segment_id)),
        )

    def encode(self) -> str:
        payload = json.dumps(
            [self.kind.value, *self.components],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        return _KEY_PREFIX + token

    @classmethod
    def decode(cls, value: str) -> Self:
        if not value.startswith(_KEY_PREFIX):
            raise ValueError("artifact key has an unsupported encoding")
        token = value.removeprefix(_KEY_PREFIX)
        try:
            raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
            payload = json.loads(raw.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("artifact key is malformed") from exc
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], str):
            raise ValueError("artifact key payload is malformed")
        key = cls._from_parts(payload[0], payload[1:])
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


def _normalize_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError(f"artifact path must be a non-empty project-relative POSIX path: {value!r}")
    raw_parts = value.split("/")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.drive
        or windows_path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ValueError(f"artifact path must be a canonical project-relative POSIX path: {value!r}")
    normalized = path.as_posix()
    if normalized in {".", ""}:
        raise ValueError(f"artifact path must name a file: {value!r}")
    if normalized in _RESERVED_ARTIFACT_PATHS:
        raise ValueError(f"runtime-owned path cannot be registered as an artifact: {value!r}")
    return normalized


def _serialize_manifest(entries: Mapping[str, ArtifactManifestEntry]) -> bytes:
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


def _parse_manifest(raw: bytes) -> dict[str, ArtifactManifestEntry]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactManifestError(f"artifact manifest is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"entries", "hash_algorithm", "schema_version"}:
        raise ArtifactManifestError("artifact manifest has an invalid top-level schema")
    if payload["schema_version"] != MANIFEST_SCHEMA_VERSION:
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
            normalized_path = _normalize_relative_path(artifact_path)
        except (TypeError, ValueError) as exc:
            raise ArtifactManifestError(f"artifact manifest entry has an invalid path: {encoded_key}") from exc
        if normalized_path != artifact_path:
            raise ArtifactManifestError(f"artifact manifest entry path is not canonical: {encoded_key}")
        if not isinstance(basis_digest, str) or _DIGEST_RE.fullmatch(basis_digest) is None:
            raise ArtifactManifestError(f"artifact manifest entry has an invalid basis digest: {encoded_key}")
        entries[encoded_key] = ArtifactManifestEntry(
            artifact_path=normalized_path,
            basis_digest=basis_digest,
        )
    return entries


def _is_linkish(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _create_temporary_file(root_fd: int) -> tuple[int, str]:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
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
