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
    # 仅 image_edit 任务写入（其余任务类型 task_type 本身已按资源种类区分，无需此列）：
    # 纳入去重键，避免不同资产类型同名（如角色和道具都叫「玉佩」）时活动任务互相误判去重。
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
    # 提交该供应商任务时所用的协议标识（协议维度），只有自定义供应商有这个维度，内置供应商恒 NULL。
    # 与 provider_job_id 同一次写入落地，记录这笔供应商任务按哪套协议提交，供排障时归因。不存请求
    # 域名。续跑的协议比对不读这一列——那道闸的权威是 execution_checkpoint_json 里的 endpoint_guard。
    provider_endpoint: Mapped[str | None] = mapped_column(String)
    # 提交该供应商任务时实际请求的域名（连接维度），两类供应商通用，与 provider_job_id 同一次
    # 写入落地。域名随用户配置变化，续跑据此回放原域名轮询，避免按新域名轮旧任务查无。
    submitted_base_url: Mapped[str | None] = mapped_column(String)
    # 参考生视频首次提交前冻结的严格执行事实。独立列避免与可变 enqueue payload 混合，且让
    # checkpoint/job 组合在重启时可无歧义分流；只由 worker 内部消费，不属于 tasks API 契约。
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
            "task_type",
            "resource_id",
            text("COALESCE(script_file, '')"),
            text("COALESCE(resource_type, '')"),
            unique=True,
            sqlite_where=text("status IN ('queued', 'running', 'cancelling')"),
            postgresql_where=text("status IN ('queued', 'running', 'cancelling')"),
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
    unit_id: Mapped[str] = mapped_column(String, nullable=False)
    deduped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("batch_id", "unit_id", name="uq_batch_tasks_batch_unit"),)


class WorkerLease(Base):
    __tablename__ = "worker_lease"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    lease_until: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
