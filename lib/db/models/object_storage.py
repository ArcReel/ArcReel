"""ORM models for object-storage connections, relay copies and formal material objects."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin

#: ``slot`` value of the current connection; a unique index permits only one current connection per instance.
CURRENT_CONNECTION_SLOT = 1


class ObjectStorageConnection(TimestampMixin, Base):
    """Connection configuration for user-owned object storage, separate from media-generation provider credentials.

    Initially TOS is the only ``kind``. Each instance has at most one current connection:
    ``slot`` is 1 and unique, including concurrent first saves. Empty ``slot`` marks retired
    connections retained only when migration merges historical duplicates to preserve
    still-referenced locators; never read them as current connections.
    ``access_key_id`` / ``secret_access_key`` are stored in plaintext and masked by
    ``mask_secret`` in read APIs, as with provider credentials. Never reuse deleted
    connection IDs: stored content records its owning connection by ID, so reuse would
    point old locators at a different bucket.
    """

    __tablename__ = "object_storage_connection"
    __table_args__ = (
        Index("uq_object_storage_connection_slot", "slot", unique=True),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    access_key_id: Mapped[str] = mapped_column(Text, nullable=False)
    secret_access_key: Mapped[str] = mapped_column(Text, nullable=False)
    slot: Mapped[int | None] = mapped_column(Integer, nullable=True, default=CURRENT_CONNECTION_SLOT)


class RelayObject(TimestampMixin, Base):
    """Relay copy written to user object storage for provider image retrieval, managed separately from formal material objects.

    Create the record before writing the object so its locator remains recoverable on
    partial-write failure. ``connection_id`` / ``endpoint`` / ``bucket`` describe the
    connection at write time. If it later changes to another bucket, do not delete,
    avoiding removal of an identically keyed object in another bucket. Give signed
    download URLs only to providers, never store them; cleanup uses only this locator.

    ``state``: ``retained`` awaits cleanup; ``deleted`` is removed. Record deletion failures and next retry times.
    """

    __tablename__ = "object_storage_relay_object"
    __table_args__ = (
        Index("ix_object_storage_relay_object_state_next", "state", "next_attempt_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Library-ingestion job using this copy (provider_asset_link.id); its state determines retention.
    link_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="retained")
    # Expiry of the download URL issued to the provider; empty when none was issued.
    url_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delete_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FormalObject(TimestampMixin, Base):
    """Formal material object in user-owned object storage, targeted by persistent locators for remote content versions.

    Keys use content digests (under ``arcreel-materials/``, separate from relay copies).
    Content versions with identical bytes on one connection share an object, with ownership
    recorded by :class:`FormalObjectRef`. Create the record before upload so its locator
    remains recoverable after partial-upload failure or process exit.

    ``state``: ``uploading`` is registered but unconfirmed remotely; ``stored`` is confirmed;
    ``orphaned`` has no references and awaits deletion (record failures and next retry times);
    ``deleted`` is removed. ``created_attempt`` identifies the latest formal commit responsible
    for upload, allowing startup recovery to locate interrupted commits.
    """

    __tablename__ = "object_storage_formal_object"
    __table_args__ = (
        Index("uq_object_storage_formal_object_key", "connection_id", "bucket", "object_key", unique=True),
        Index("ix_object_storage_formal_object_state_next", "state", "next_attempt_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="uploading")
    created_attempt: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delete_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FormalObjectRef(TimestampMixin, Base):
    """Reference to a formal material object: a holder's content version uses it, or an in-progress formal commit occupies it.

    ``project_name`` identifies the holder: a project name (reassigned to its tombstone
    name after whole-project deletion), or a colon-containing key for non-project consumers.
    ``state``: ``pending`` is an in-progress formal commit (``attempt_id`` identifies it);
    settlement changes it to ``active`` or deletes it according to version history.
    ``active`` means the holder uses ``content_version_id`` (a version-history entry in
    projects); ``trashed`` means a project-trash deletion record can still restore it.
    Never delete an object while any reference remains (``docs/adr/0118``).
    """

    __tablename__ = "object_storage_formal_object_ref"
    __table_args__ = (
        Index("ix_object_storage_formal_object_ref_object", "object_id"),
        Index("ix_object_storage_formal_object_ref_version", "project_name", "content_version_id"),
        Index("ix_object_storage_formal_object_ref_attempt", "attempt_id"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
