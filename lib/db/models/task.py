"""Task queue ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, UserOwnedMixin


class Task(UserOwnedMixin, Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.batch_id"), index=True)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    task_type: Mapped[str] = mapped_column(String, nullable=False)
    media_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    # Written only for image_edit tasks (other task_type values already distinguish resource kinds):
    # included in deduplication keys so same-named assets of different types (for example, a character
    # and prop both named 玉佩) do not incorrectly deduplicate active tasks.
    resource_type: Mapped[str | None] = mapped_column(String)
    script_file: Mapped[str | None] = mapped_column(String)
    payload_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String, nullable=False, server_default="webui")
    dependency_task_id: Mapped[str | None] = mapped_column(String)
    dependency_group: Mapped[str | None] = mapped_column(String)
    dependency_index: Mapped[int | None] = mapped_column(Integer)
    cancelled_by: Mapped[str | None] = mapped_column(String)
    provider_id: Mapped[str | None] = mapped_column(String)
    provider_job_id: Mapped[str | None] = mapped_column(String)
    # Protocol identifier used to submit the provider job (protocol dimension), custom providers only;
    # always NULL for built-ins. Persist together with provider_job_id for troubleshooting submission
    # protocol attribution, without the request hostname. Resume protocol comparison does not read
    # this column: execution_checkpoint_json endpoint_guard is authoritative for that gate.
    provider_endpoint: Mapped[str | None] = mapped_column(String)
    # Actual request hostname at job submission (connection dimension), shared by both provider kinds
    # and persisted with provider_job_id. Configuration may change the hostname; resume replays polling
    # against this original hostname to avoid looking up an old job on a new host.
    submitted_base_url: Mapped[str | None] = mapped_column(String)
    # Strict execution facts frozen before initial reference-video submission. A separate column avoids
    # mixing with mutable enqueue payloads and permits unambiguous checkpoint/job dispatch on restart.
    # Consumed only internally by workers; not part of the tasks API contract.
    execution_checkpoint_json: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_tasks_status_queued_at", "status", "queued_at"),
        Index("idx_tasks_project_updated_at", "project_name", "updated_at"),
        Index("idx_tasks_dependency_task_id", "dependency_task_id"),
        Index("idx_tasks_status_provider_queued", "status", "provider_id", "queued_at"),
        Index(
            "idx_tasks_dedupe_active",
            "project_name",
            "user_id",
            "task_type",
            "resource_id",
            text("COALESCE(script_file, '')"),
            text("COALESCE(resource_type, '')"),
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )


class GenerationBatch(UserOwnedMixin, Base):
    __tablename__ = "batches"

    batch_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    requested_json: Mapped[str] = mapped_column(Text, nullable=False)
    blocked_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BatchTask(Base):
    __tablename__ = "batch_tasks"

    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.batch_id", ondelete="CASCADE"), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id"), primary_key=True)
    unit_id: Mapped[str] = mapped_column(String, primary_key=True)
    deduped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("batch_id", "unit_id", name="uq_batch_tasks_batch_unit"),)


class WorkerLease(Base):
    __tablename__ = "worker_lease"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    lease_until: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
