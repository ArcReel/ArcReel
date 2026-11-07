"""Material storage entry: saving/reading content versions in local material storage (``docs/adr/0109``).

Materials identify project-resource version histories (``MaterialRef``); each record is a content
version with three independent facts:

- ``content_version_id``: stable project-local identity, unchanged by selection/restoration/resource
  renaming; never derived from hashes. Equal-byte records, including cross-project copies, are distinct versions.
- ``content_digest``: raw sha256 hexdigest, verified during reads.
- ``file``: project-relative snapshot path, the local durable locator for records without ``storage``.
- ``storage``: remote durable locator (:class:`ObjectLocator`: connection, bucket, object key),
  per ``docs/adr/0116``. When present, ``file`` is only a cleanable work file fetched remotely on demand.

Formal resource files such as ``characters/Alice.png`` are selected-version local work copies,
not durable locators. Save new versions through :class:`NewContentStorage` (local by default;
formal commits can select remote via ``lib.material.formal_objects``). Reads before selection
use :meth:`LocalMaterialStorage.open`; version managers first validate managed snapshot buckets.
Remote-version fetch/verification/use protection lives in ``lib.material.work_files``.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from lib.infra.api_errors import ConflictError
from lib.infra.content_digest import sha256_file

#: Version-record content-identity key.
CONTENT_VERSION_ID = "content_version_id"
#: Version-record content-digest key: raw snapshot sha256 hexdigest, matching ``lib.infra.content_digest.sha256_file``.
CONTENT_DIGEST = "content_digest"
#: Remote-locator record key, values from :meth:`ObjectLocator.to_record`; records without it use local locators.
STORAGE = "storage"
_OBJECT_STORAGE_KIND = "object_storage"


class VersionSnapshotCorruptedError(ConflictError):
    """A version snapshot no longer matches the content digest recorded when the version was created."""

    def __init__(self, resource_type: str, resource_id: str, version: object) -> None:
        super().__init__(
            "version_snapshot_corrupted", resource_type=resource_type, resource_id=resource_id, version=version
        )


@dataclass(frozen=True, slots=True)
class MaterialRef:
    """Material identity: one project resource’s version history; asset entries are merely creative objects consuming it."""

    resource_type: str
    resource_id: str


@dataclass(frozen=True, slots=True)
class LocalLocator:
    """Local durable locator: a project-relative version snapshot."""

    path: str


@dataclass(frozen=True, slots=True)
class ObjectLocator:
    """Object-storage durable locator: connection ID, bucket, and key at write time.

    Read only through that recorded connection/bucket; never reinterpret after bucket changes,
    and never include signed URLs in locators.
    """

    connection_id: int
    bucket: str
    key: str

    def to_record(self) -> dict[str, Any]:
        return {
            "kind": _OBJECT_STORAGE_KIND,
            "connection_id": self.connection_id,
            "bucket": self.bucket,
            "key": self.key,
        }

    @classmethod
    def from_record(cls, value: object) -> ObjectLocator | None:
        """Recorded ``storage`` value; absent/malformed values return ``None`` and use local locators."""
        if not isinstance(value, Mapping) or value.get("kind") != _OBJECT_STORAGE_KIND:
            return None
        connection_id, bucket, key = value.get("connection_id"), value.get("bucket"), value.get("key")
        if not isinstance(connection_id, int) or isinstance(connection_id, bool):
            return None
        if not isinstance(bucket, str) or not isinstance(key, str) or not bucket or not key:
            return None
        return cls(connection_id, bucket, key)


@dataclass(frozen=True, slots=True)
class ContentVersion:
    """One material content version.

    ``id`` / ``digest`` are ``None`` only on records predating content identities; backfill through
    ``VersionManager.ensure_content_version`` when referencing them. Nonempty ``stored`` means
    remote durable storage; ``locator`` then locates only local work files.
    """

    material: MaterialRef
    version: int
    id: str | None
    digest: str | None
    locator: LocalLocator
    stored: ObjectLocator | None = None

    @property
    def persistent_locator(self) -> LocalLocator | ObjectLocator:
        return self.stored or self.locator


@dataclass(frozen=True, slots=True)
class PersistedContent:
    """Content-save result: digest of persisted content and, for remote saves, its durable locator."""

    digest: str
    stored: ObjectLocator | None = None


class NewContentStorage(Protocol):
    """New-version storage strategy; version managers call :meth:`persist` before writing each new record.

    Persist durably before returning, confirming remote saves. On errors, version managers roll
    back the commit and remove partial ``slot`` snapshots. ``slot`` identifies this record’s
    snapshot: durable locator for local saves, working file for remote saves.
    """

    def persist(self, project_path: Path, resource_type: str, source: Path, slot: LocalLocator) -> PersistedContent: ...


def new_content_version_id() -> str:
    """New-version identity, randomly generated independently of content."""

    return uuid.uuid4().hex


class LocalMaterialStorage:
    """Project-directory local storage; synchronous disk operations belong in async callers’ worker threads.

    Remote-version :meth:`open` reads only local work files; callers first fetch missing files
    through ``lib.material.work_files.ensure_content_version_file``.
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = Path(project_path)

    def persist(self, source: Path, locator: LocalLocator) -> str:
        """Save ``source`` bytes to ``locator`` and return the persisted-content digest.

        Hash the saved snapshot, not ``source``, recording bytes actually retained at the durable
        locator. Failures may leave partial snapshots for caller rollback to delete.
        """

        target = self.project_path / locator.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return sha256_file(target)

    def open(self, content_version: ContentVersion) -> Path:
        """Return a locally readable content-version file, rejecting mismatches with recorded digests.

        Do not verify records lacking digests (early unselected versions).

        Raises:
            FileNotFoundError: No file at the durable locator.
            VersionSnapshotCorruptedError: Content mismatches its recorded digest.
        """

        path = self.project_path / content_version.locator.path
        if not path.is_file():
            raise FileNotFoundError(f"版本文件不存在: {path}")
        verify_content(content_version, path)
        return path


def verify_content(content_version: ContentVersion, path: Path) -> None:
    """Verify ``path`` against recorded version digests; absent digests pass."""

    expected = content_version.digest
    if expected is not None and sha256_file(path) != expected:
        material = content_version.material
        raise VersionSnapshotCorruptedError(material.resource_type, material.resource_id, content_version.version)


_NEW_CONTENT_STORAGE: ContextVar[NewContentStorage | None] = ContextVar("new_content_storage", default=None)


def active_new_content_storage() -> NewContentStorage | None:
    """New-version storage strategy for current formal commits; ``None`` means local."""
    return _NEW_CONTENT_STORAGE.get()


@contextmanager
def use_new_content_storage(storage: NewContentStorage | None) -> Generator[None]:
    """Save new content versions inside this context through ``storage``.

    Context variables copy through ``asyncio.to_thread`` and remain effective for worker-thread
    version writes. Only formal commit entry ``lib.material.formal_objects.formal_commit`` uses
    this; business callers must not call directly.
    """
    token = _NEW_CONTENT_STORAGE.set(storage)
    try:
        yield
    finally:
        _NEW_CONTENT_STORAGE.reset(token)


__all__ = [
    "CONTENT_DIGEST",
    "CONTENT_VERSION_ID",
    "STORAGE",
    "ContentVersion",
    "LocalLocator",
    "LocalMaterialStorage",
    "MaterialRef",
    "NewContentStorage",
    "ObjectLocator",
    "PersistedContent",
    "VersionSnapshotCorruptedError",
    "active_new_content_storage",
    "new_content_version_id",
    "use_new_content_storage",
    "verify_content",
]
