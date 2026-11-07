"""ORM models for provider asset-library control-plane accounts, inference-credential bindings and provider asset links."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin


class AssetLibraryAccount(TimestampMixin, Base):
    """A provider asset-library scope: site, region and project, with control-plane credentials for that scope.

    Control-plane credentials (Access Key ID / Secret Access Key) and inference API keys
    are not interchangeable. Store plaintext and mask in read APIs. Send control-plane
    requests only to official hosts mapped in code from ``site`` and ``region``, never
    infer them from inference-credential base_url. Never reuse deleted account IDs,
    preventing later provider asset links from pointing at a different scope.
    """

    __tablename__ = "asset_library_account"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    site: Mapped[str] = mapped_column(String(16), nullable=False)  # "volcengine" | "byteplus"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    project_name: Mapped[str] = mapped_column(String(128), nullable=False)
    access_key_id: Mapped[str] = mapped_column(Text, nullable=False)
    secret_access_key: Mapped[str] = mapped_column(Text, nullable=False)


class AssetLibraryBinding(TimestampMixin, Base):
    """Explicitly bind an asset-library scope to an inference credential permitted to use it.

    For ``credential_type`` = ``builtin``, ``credential_id`` references ``provider_credential.id``;
    for ``custom``, it references ``custom_provider.id``. Targets belong to different tables,
    so no foreign key is created. Each inference credential binds to at most one scope.
    ``confirmed_fingerprint`` hashes credential contents, endpoint and library scope at
    confirmation time. Recompute and compare on reads: differences require reconfirmation;
    an unchanged credential-record ID never inherits previous eligibility by itself.
    """

    __tablename__ = "asset_library_binding"
    __table_args__ = (
        UniqueConstraint("credential_type", "credential_id", name="uq_asset_library_binding_credential"),
        Index("ix_asset_library_binding_account_id", "account_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("asset_library_account.id", ondelete="CASCADE"), nullable=False
    )
    credential_type: Mapped[str] = mapped_column(String(16), nullable=False)
    credential_id: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderAssetLink(TimestampMixin, Base):
    """Link between a content version and a provider asset, together with the ingestion job establishing it.

    The link belongs to ``content_version_id`` in ``project_name``. Content-version identity
    is unique only within a project, and archive imports preserve version records, so keys
    must include the project. ``account_identity`` / ``site`` / ``region`` /
    ``provider_project_name`` freeze the invocation scope at submission; never reinterpret
    it after account edits or deletion. An account record can be replaced in place with
    another cloud account's Access Key, so scope follows frozen cloud identity rather than
    ``account_id``. ``account_id`` only records the submitting account record used for later
    status queries; account IDs are never reused, so there is no foreign key. Archive-imported
    links bypass instance account records and use 0. Before obtaining a provider asset ID,
    the record already exists and represents the ingestion job.

    Each content version has at most one ``selected`` record per invocation scope: its
    selected link or ingestion job. Unlinking clears ``selected`` and records ``unlinked_at``;
    subsequent status queries update facts without restoring selection.
    """

    __tablename__ = "provider_asset_link"
    __table_args__ = (
        Index(
            "uq_provider_asset_link_selected",
            "project_name",
            "content_version_id",
            "account_identity",
            "site",
            "region",
            "provider_project_name",
            unique=True,
            sqlite_where=text("selected = 1"),
            postgresql_where=text("selected"),
        ),
        Index("ix_provider_asset_link_content_version", "project_name", "content_version_id"),
        Index("ix_provider_asset_link_next_check_at", "next_check_at"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # Resource and version number at initiation, for display/diagnostics; stale after renaming, so locate links only by content-version identity.
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Persistent locator of the content version at submission (project-relative path); ingestion reads verify content_digest.
    locator_path: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)  # "upload" / "import"
    account_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Cloud-account identity digest at submission/import (lib.asset_library.bindings.account_identity), containing no secrets.
    account_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    site: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_project_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # pending / submitting / processing / active / failed / uncertain; see lib.asset_library.links
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_asset_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_group_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Asset name at ingestion submission, used to recover its ID from the asset group after uncertain request outcomes; imported links record the selected asset name.
    submit_name: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Reason the latest status query itself failed (network, authentication, etc.); does not change ingestion state.
    check_error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    unlinked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
